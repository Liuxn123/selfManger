"""Warm scans retain correctness without rereading unchanged text or rewriting rows."""
import os
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from selfManger.domain import UserError
from selfManger.knowledge import KnowledgeIndex
from selfManger.storage import Store


class KnowledgeIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ.get("SELFMANGER_TEST_ROOT", tempfile.gettempdir())) / ("incremental-" + uuid4().hex[:8])
        self.vault = self.root / "vault"
        self.vault.mkdir(parents=True)
        self.store = Store(self.root / "data")
        self.index = KnowledgeIndex(self.store, self.vault)
        self.note = self.vault / "笔记.md"
        self.note.write_text("# 原文\n原始搜索词", encoding="utf-8")

    def test_unchanged_scan_reads_no_bodies_and_performs_no_row_writes(self):
        first = self.index.scan()
        statements = []
        connect = self.store.connect
        @contextmanager
        def observed():
            with connect() as db:
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(self.store, "connect", observed), patch.object(Path, "read_bytes", side_effect=AssertionError("unchanged body read")):
            second = self.index.scan()
        self.assertEqual(first, second)
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for sql in statements), statements)

    def test_scan_rebuilds_file_index_without_losing_metadata_or_duplicating_rows(self):
        self.index.scan()
        self.index.update_metadata("笔记.md", tags=["persisted"], favorite=True)
        with self.store.connect() as db:
            db.execute("DELETE FROM knowledge_files WHERE root_id=?", (self.index.root_id,))
        rebuilt_index = KnowledgeIndex(self.store, self.vault)
        first = rebuilt_index.scan()
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["items"][0]["tags"], ["persisted"])
        self.assertTrue(first["items"][0]["favorite"])
        self.assertIn("原始搜索词", first["items"][0]["search_text"])
        second = rebuilt_index.scan()
        self.assertEqual(len(second["items"]), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_files WHERE root_id=?", (self.index.root_id,)).fetchone()[0], 1)

    def test_edit_add_delete_and_reappearance_update_results_and_search(self):
        self.index.scan()
        self.note.write_text("# 修改后的标题\n新搜索内容", encoding="utf-8")
        second = self.vault / "新增.md"
        second.write_text("# 新增\n新资料", encoding="utf-8")
        rows = self.index.scan()["items"]
        self.assertEqual(len(rows), 2)
        self.assertIn("新搜索内容", next(row for row in rows if row["relative"] == "笔记.md")["search_text"])
        self.note.unlink()
        missing = next(row for row in self.index.scan()["items"] if row["relative"] == "笔记.md")
        self.assertTrue(missing["missing"])
        self.assertEqual(missing["search_text"], "")
        self.note.write_text("# 恢复\n恢复搜索词", encoding="utf-8")
        restored = next(row for row in self.index.scan()["items"] if row["relative"] == "笔记.md")
        self.assertFalse(restored["missing"])
        self.assertIn("恢复搜索词", restored["search_text"])

    def test_metadata_changes_use_database_after_one_time_import(self):
        legacy_dir = self.vault / "99-系统" / "知识库索引" / "资源"
        legacy_dir.mkdir(parents=True)
        original_sidecar = legacy_dir / "legacy.json"
        original_sidecar.write_text(json.dumps({"version": 1, "relative": "笔记.md", "title": "Imported once"}, ensure_ascii=False), encoding="utf-8")
        self.index.scan()
        self.index.update_metadata("笔记.md", tags=["新标签"], favorite=True)
        result = self.index.scan()["items"][0]
        self.assertEqual(result["tags"], ["新标签"])
        self.assertTrue(result["favorite"])
        self.index.update_resource("笔记.md", title="新名称", notes="新说明", deleted=True)
        result = self.index.scan()["items"][0]
        self.assertEqual(result["name"], "新名称")
        self.assertTrue(result["deleted"])
        self.assertTrue(original_sidecar.is_file())
        late = self.vault / "后加.md"
        late.write_text("# 后加", encoding="utf-8")
        (legacy_dir / "late.json").write_text(json.dumps({"relative": "后加.md", "title": "不应导入"}, ensure_ascii=False), encoding="utf-8")
        late_row = next(row for row in self.index.scan()["items"] if row["relative"] == "后加.md")
        self.assertEqual(late_row["name"], "后加")

    def test_late_synced_legacy_sources_are_imported_until_first_batch(self):
        self.index.scan()
        legacy_dir = self.vault / "99-系统" / "知识库索引" / "资源"
        legacy_dir.mkdir(parents=True)
        sidecar = legacy_dir / "late.json"
        sidecar.write_text(json.dumps({"version": 1, "relative": "笔记.md", "title": "Synced later"}, ensure_ascii=False), encoding="utf-8")
        target = self.root / "synced.txt"
        target.write_text("synced target", encoding="utf-8")
        (self.vault / "synced.kref").write_text(json.dumps({"path": str(target)}), encoding="utf-8")
        bundle_dir = self.vault / "99-系统" / "知识库索引" / "资源组"
        bundle_dir.mkdir(parents=True)
        old_bundle = bundle_dir / "synced.json"
        old_bundle.write_text(json.dumps({"version": 1, "id": "synced-bundle", "title": "Synced bundle",
            "folder": ".", "members": ["missing.md"], "directory": "", "entry": "", "active": True}, ensure_ascii=False), encoding="utf-8")
        first = self.index.scan()
        self.assertEqual(next(row for row in first["items"] if row["relative"] == "笔记.md")["name"], "Synced later")
        self.assertIn("Synced bundle", {row["name"] for row in first["items"]})
        self.assertTrue(any(row["path"] == str(target) for row in first["items"]))
        sidecar.write_text(json.dumps({"version": 1, "relative": "笔记.md", "title": "changed later"}, ensure_ascii=False), encoding="utf-8")
        old_bundle.write_text(json.dumps({"version": 1, "id": "synced-bundle", "title": "changed bundle",
            "folder": ".", "members": ["missing.md"], "directory": "", "entry": "", "active": True}, ensure_ascii=False), encoding="utf-8")
        second = self.index.scan()
        self.assertEqual(next(row for row in second["items"] if row["relative"] == "笔记.md")["name"], "Synced later")
        self.assertIn("Synced bundle", {row["name"] for row in second["items"]})
        self.assertTrue(sidecar.is_file())
        self.assertTrue(old_bundle.is_file())

    def test_text_budget_can_grow_without_reusing_truncated_cache(self):
        self.index.MAX_TEXT_TOTAL = 5
        short = self.index.scan()["items"][0]["search_text"]
        self.index.MAX_TEXT_TOTAL = 1000
        full = self.index.scan()["items"][0]["search_text"]
        self.assertGreater(len(full), len(short))
        self.assertIn("原始搜索词", full)
        self.assertLessEqual(sum(len(value[1]) for value in self.index._text_cache.values()), 1000)

    def test_external_reference_detects_changed_and_missing_target(self):
        target = self.root / "external.md"
        target.write_text("# 外部\n原内容", encoding="utf-8")
        (self.vault / "引用.kref").write_text(json.dumps({"path": str(target)}), encoding="utf-8")
        self.index.scan()
        self.assertTrue((self.vault / "引用.kref").is_file())
        other = self.root / "changed-target.md"
        other.write_text("# 不应重新导入", encoding="utf-8")
        (self.vault / "引用.kref").write_text(json.dumps({"path": str(other)}), encoding="utf-8")
        target.write_text("# 外部已改\n更新后的引用内容", encoding="utf-8")
        row = next(row for row in self.index.scan()["items"] if row["kind"] == "external")
        self.assertIn("更新后的引用内容", row["search_text"])
        self.assertEqual(Path(row["path"]), target)
        target.unlink()
        row = next(row for row in self.index.scan()["items"] if row["kind"] == "external")
        self.assertTrue(row["missing"])
        self.assertEqual(row["search_text"], "")
        late = self.root / "late-target.txt"
        late.write_text("late", encoding="utf-8")
        (self.vault / "late.kref").write_text(json.dumps({"path": str(late)}), encoding="utf-8")
        self.assertFalse(any(item["path"] == str(late) for item in self.index.scan()["items"]))

    def test_folder_rename_relocates_sqlite_metadata_references_and_bundles(self):
        folder = self.vault / "folder"
        folder.mkdir()
        note = folder / "note.md"
        note.write_text("# Note", encoding="utf-8")
        external = self.root / "external.txt"
        external.write_text("external", encoding="utf-8")
        self.index.scan()
        self.index.update_resource("folder/note.md", notes="metadata", archive_original="folder/old.md",
                                   related=["folder/other.md"])
        self.index.add_file(external, folder="folder", copy=False)
        self.index.create_bundle("Folder bundle", members=["folder/note.md"], folder="folder")
        self.index.rename_folder("folder", "renamed")
        report = self.index.scan()
        self.assertEqual(report["errors"], [], report)
        grouped = next(item for item in report["items"] if item["kind"] == "bundle")
        row = next(item for item in grouped["members"] if item["relative"] == "renamed/note.md")
        self.assertEqual(row["notes"], "metadata")
        self.assertEqual(self.index.read_record("renamed/note.md")["archive_original"], "renamed/old.md")
        self.assertEqual(self.index.read_record("renamed/note.md")["related"], ["renamed/other.md"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT folder FROM knowledge_references").fetchone()[0], "renamed")
        bundle = next(item for item in self.index.read_bundles([]) if item["title"] == "Folder bundle")
        self.assertEqual(bundle["folder"], "renamed")
        self.assertEqual(bundle["members"], ["renamed/note.md"])

    def test_external_reference_archive_and_restore_only_move_sqlite_registration(self):
        folder = self.vault / "docs"
        folder.mkdir()
        target = self.root / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        self.index.add_file(target, folder="docs", copy=False)
        entry = next(item for item in self.index.scan()["items"] if item["kind"] == "external")
        self.index.archive_resource(entry)
        archived = next(item for item in self.index.scan()["items"] if item["kind"] == "external")
        self.assertTrue(archived["archived"])
        self.assertEqual(archived["folder"], "98-归档/docs")
        self.assertTrue(target.is_file())
        self.index.archive_resource(archived)
        restored = next(item for item in self.index.scan()["items"] if item["kind"] == "external")
        self.assertFalse(restored["archived"])
        self.assertEqual(restored["folder"], "docs")
        self.assertTrue(target.is_file())

    def test_root_is_revalidated_when_reusing_index(self):
        self.index.scan()
        original = Path.is_symlink
        def swapped(path):
            return path == self.index.root or original(path)
        with patch.object(Path, "is_symlink", swapped):
            with self.assertRaises(UserError):
                self.index.scan()

    def test_new_index_does_not_reuse_another_roots_content(self):
        self.index.scan()
        other = self.root / "other-vault"
        other.mkdir()
        (other / "笔记.md").write_text("# 独立资料\n另一个库", encoding="utf-8")
        result = KnowledgeIndex(self.store, other).scan()
        self.assertIn("另一个库", result["items"][0]["search_text"])
        self.assertNotIn("原始搜索词", result["items"][0]["search_text"])


from tests import test_knowledge as browser


class KnowledgeIncrementalUITests(unittest.TestCase):
    setUpClass = classmethod(browser.KnowledgeBrowserTests.setUpClass.__func__)
    setUp = browser.KnowledgeBrowserTests.setUp

    def test_refresh_retains_warm_index_and_root_switch_replaces_it(self):
        index = self.page.index
        self.page.refresh()
        self.assertIs(self.page.index, index)
        other = self.vault.parent / "another-vault"
        other.mkdir()
        (other / "独立.md").write_text("# 独立库\n全新内容", encoding="utf-8")
        self.window.store.set_setting("obsidian_vault_root", str(other))
        self.page.refresh()
        self.assertIsNot(self.page.index, index)
        self.assertEqual([row["relative"] for row in self.page.items], ["独立.md"])
