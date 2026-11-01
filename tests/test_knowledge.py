import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import shutil
import unittest
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QMessageBox

from selfManger.domain import UserError
from selfManger.knowledge import KnowledgeIndex, rebase_knowledge_index
from selfManger.storage import Store
from selfManger.ui.knowledge import KnowledgePage
from selfManger.ui.theme import setup_theme


def isolated_directory(test):
    # Use inherited ACLs; mode 0700 temp directories can deny restricted Windows tokens.
    parent = Path(tempfile.gettempdir()).resolve()
    root = parent / ("knowledge-check-" + uuid4().hex)
    root.mkdir()
    def cleanup():
        resolved = root.resolve()
        if resolved.parent != parent or not resolved.name.startswith("knowledge-check-"):
            raise RuntimeError("Unexpected test cleanup path")
        shutil.rmtree(resolved)
    test.addCleanup(cleanup)
    return root


class KnowledgeIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = isolated_directory(self)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.index = KnowledgeIndex(Store(self.root / "data"), self.vault)

    def test_resource_editor_submission_rolls_back_on_late_failure(self):
        (self.vault / "note.md").write_text("# Note\n", encoding="utf-8")
        self.index.scan()
        self.index.update_metadata("note.md", tags=["original"], favorite=False)
        self.index.update_resource("note.md", title="Original", notes="Keep this")

        with patch.object(self.index, "record_access", side_effect=UserError("injected late failure")):
            with self.assertRaisesRegex(UserError, "injected late failure"):
                self.index.save_resource_details(
                    "note.md",
                    {"tags": ["changed"], "favorite": True, "projects": [], "agents": []},
                    {"title": "Changed", "summary": "New summary", "related": [], "notes": "Changed"},
                )

        record = self.index.read_record("note.md")
        self.assertEqual(record["tags"], ["original"])
        self.assertFalse(record["favorite"])
        self.assertEqual(record["title"], "Original")
        self.assertEqual(record["notes"], "Keep this")

    def test_rebases_index_identity_and_vault_local_paths(self):
        store = self.index.store
        (self.vault / "note.md").write_text("# Note\n", encoding="utf-8")
        self.index.scan()
        with store.connect() as db:
            self.index._put_record(db, "note.md", {
                "tags": ["kept"], "favorite": True, "projects": ["P-1"],
                "archive_original": str(self.vault / "note.md"),
                "archive_original_directory": str(self.vault),
                "archive_member_locations": {str(self.vault / "note.md"): str(self.vault / "copy.md")}})
            target = self.vault / "attachment.bin"
            key = "external:" + json.dumps(str(target), ensure_ascii=False)
            db.execute("INSERT INTO knowledge_references VALUES (?,?,?,?,?)",
                       (self.index.root_id, key, str(target), "attachment.bin", ""))
        bundle = self.index.create_bundle("Saved bundle", members=["note.md"])
        archive_folder = self.vault / "archive"
        archive_folder.mkdir()
        absolute_bundle = self.index.create_bundle("External folder", directory=str(archive_folder))
        old_root = self.vault
        new_root = self.root / "moved-vault"
        (new_root / "archive").mkdir(parents=True)
        (new_root / "note.md").write_text("# Note\n", encoding="utf-8")

        changed = rebase_knowledge_index(store, old_root, new_root)

        self.assertGreater(changed, 0)
        moved = KnowledgeIndex(store, new_root)
        record = moved.read_record("note.md")
        self.assertEqual(record["tags"], ["kept"])
        self.assertTrue(record["favorite"])
        self.assertEqual(record["projects"], ["P-1"])
        self.assertEqual(record["archive_original"], str(new_root / "note.md"))
        self.assertEqual(record["archive_original_directory"], str(new_root))
        with store.connect() as db:
            reference = db.execute("SELECT relative_path,target_path FROM knowledge_references WHERE root_id=?",
                                   (moved.root_id,)).fetchone()
            self.assertEqual(reference[0], "external:" + json.dumps(
                str(new_root / "attachment.bin"), ensure_ascii=False))
            self.assertEqual(reference[1], str(new_root / "attachment.bin"))
            self.assertFalse(db.execute("SELECT 1 FROM knowledge_metadata WHERE root_id=?",
                                        (self.index.root_id,)).fetchone())
        self.assertTrue(moved.has_bundle(bundle.partition(":")[2]))
        moved_bundles = {item["id"]: item for item in moved.read_bundles([])}
        self.assertEqual(moved_bundles[absolute_bundle.partition(":")[2]]["directory"], "archive")

    def test_rebase_rejects_existing_target_index_without_mutation(self):
        store = self.index.store
        self.index.scan()
        target = self.root / "other-vault"
        target.mkdir()
        other = KnowledgeIndex(store, target)
        (target / "existing.md").write_text("# Existing\n", encoding="utf-8")
        other.scan()
        with store.connect() as db:
            self.index._put_record(db, "source.md", {"tags": ["source"]})

        with self.assertRaisesRegex(UserError, "不能合并"):
            rebase_knowledge_index(store, self.vault, target)

        with store.connect() as db:
            self.assertEqual(json.loads(db.execute(
                "SELECT tags FROM knowledge_metadata WHERE root_id=? AND relative_path='source.md'",
                (self.index.root_id,)).fetchone()[0]), ["source"])
        self.assertTrue(other.scan()["items"])

    def test_delete_local_copy_and_reject_external_reference(self):
        source = self.root / "original.txt"
        source.write_text("original", encoding="utf-8")
        self.index.add_file(source, copy=True)
        entry = next(item for item in self.index.scan()["items"] if not item["relative"].startswith("external:"))
        copied = Path(entry["path"])
        trash = self.root / "trash-copy.txt"
        def move(path):
            Path(path).rename(trash)
            return True
        self.index.delete_local_resource(entry, move)
        self.assertFalse(copied.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "original")
        self.assertEqual(trash.read_text(encoding="utf-8"), "original")
        self.index.add_file(source, copy=False)
        external = next(item for item in self.index.scan()["items"] if item["relative"].startswith("external:"))
        with self.assertRaises(UserError):
            self.index.delete_local_resource(external, move)
        self.assertTrue(source.exists())

    def test_delete_directory_copy_and_refuse_failed_move(self):
        source = self.root / "source"
        source.mkdir()
        (source / "note.md").write_text("note", encoding="utf-8")
        self.index.import_directory(source, "copies")
        entry = next(item for item in self.index.scan()["items"] if item["kind"] == "bundle")
        copy = self.vault / "copies" / "source"
        with self.assertRaises(UserError):
            self.index.delete_local_resource(entry, lambda _path: False)
        self.assertTrue(copy.is_dir())
        def move(path):
            Path(path).rename(self.root / "trash-folder")
            return True
        self.index.delete_local_resource(entry, move)
        self.assertFalse(copy.exists())
        self.assertTrue((source / "note.md").exists())

    def test_purge_deleted_local_file_does_not_return_after_rescan(self):
        path = self.vault / "discarded.md"
        path.write_text("# Discarded", encoding="utf-8")
        self.index.scan()
        entry = next(item for item in self.index.scan()["items"] if item["relative"] == "discarded.md")
        self.index.update_resource("discarded.md", deleted=True)
        trash = self.root / "discarded-trash.md"

        def move_to_trash(source):
            Path(source).rename(trash)
            return True

        self.index.purge_deleted_resource(entry, move_to_trash)

        self.assertTrue(trash.is_file())
        self.assertNotIn("discarded.md", {item["relative"] for item in self.index.scan()["items"]})

    def test_purge_deleted_external_reference_forgets_only_registration(self):
        source = self.root / "external.md"
        source.write_text("# Keep source", encoding="utf-8")
        self.index.add_file(source, copy=False)
        entry = next(item for item in self.index.scan()["items"] if item["relative"].startswith("external:"))
        self.index.update_resource(entry["relative"], deleted=True)

        self.index.purge_deleted_resource(entry, lambda _path: self.fail("external source must not be moved"))

        self.assertTrue(source.is_file())
        self.assertNotIn(entry["relative"], {item["relative"] for item in self.index.scan()["items"]})

    def test_purge_member_bundle_removes_bundle_but_keeps_shared_members(self):
        source = self.vault / "shared.md"
        source.write_text("# Shared", encoding="utf-8")
        identity = self.index.create_bundle("Shared bundle", members=["shared.md"])
        entry = next(item for item in self.index.scan()["items"] if item["relative"] == identity)
        self.index.update_resource(identity, deleted=True)

        self.index.purge_deleted_resource(entry, lambda _path: self.fail("member bundle must not move shared files"))

        items = {item["relative"] for item in self.index.scan()["items"]}
        self.assertNotIn(identity, items)
        self.assertIn("shared.md", items)
        self.assertTrue(source.is_file())

    def test_delete_reports_trash_move_when_index_transaction_rolls_back(self):
        source = self.root / "original.txt"
        source.write_text("original", encoding="utf-8")
        self.index.add_file(source, copy=True)
        entry = next(item for item in self.index.scan()["items"]
                     if not item["relative"].startswith("external:"))
        self.index.update_metadata(entry["relative"], tags=["preserve"])
        copied = Path(entry["path"])
        trash = self.root / "trash-copy.txt"

        def move(path):
            Path(path).rename(trash)
            return True

        def fail_after_partial_index_change(relative):
            with self.index.store.connect() as db:
                db.execute("DELETE FROM knowledge_metadata WHERE root_id=? AND relative_path=?",
                           (self.index.root_id, relative))
            raise sqlite3.OperationalError("injected index failure")

        with patch.object(self.index, "purge_resource", side_effect=fail_after_partial_index_change):
            with self.assertRaisesRegex(UserError, "本地文件已移入系统回收站"):
                self.index.delete_local_resource(entry, move)

        self.assertFalse(copied.exists())
        self.assertEqual(trash.read_text(encoding="utf-8"), "original")
        self.assertEqual(self.index.read_record(entry["relative"])["tags"], ["preserve"])
        with self.index.store.connect() as db:
            self.assertTrue(db.execute(
                "SELECT 1 FROM knowledge_files WHERE root_id=? AND relative_path=?",
                (self.index.root_id, entry["relative"])).fetchone())

    def test_note_edit_preserves_encoding_and_rejects_external_changes(self):
        note = self.vault / "note.md"
        note.write_bytes(b"\xef\xbb\xbf# Original\r\n\r\nBody\r\n")
        expected = note.read_text(encoding="utf-8-sig")
        self.index.save_note("note.md", expected, "# Updated\n\nNew body\n")
        self.assertEqual(note.read_bytes(), b"\xef\xbb\xbf# Updated\r\n\r\nNew body\r\n")
        note.write_text("Changed elsewhere", encoding="utf-8")
        with self.assertRaises(UserError):
            self.index.save_note("note.md", expected, "Overwrite")
        self.assertEqual(note.read_text(), "Changed elsewhere")
        self.assertFalse(list(self.vault.glob(".*.tmp")))

    def test_read_note_is_bounded_and_rejects_external_or_non_markdown_paths(self):
        note = self.vault / "note.md"
        note.write_bytes(b"\xef\xbb\xbf# Note\r\n")
        self.index.scan()
        self.assertEqual(self.index.read_note("note.md"), "# Note\n")
        with self.assertRaisesRegex(UserError, "Markdown"):
            self.index.read_note("note.txt")
        with self.assertRaisesRegex(UserError, "外部文件"):
            self.index.read_note("external:" + json.dumps(str(note)))
        note.write_bytes(b"x" * 1_000_001)
        with self.assertRaisesRegex(UserError, "超过 1 MB"):
            self.index.read_note("note.md")

    def test_entry_text_read_is_bounded_and_validates_external_reference(self):
        source = self.root / "external.txt"
        source.write_text("0123456789", encoding="utf-8")
        self.index.add_file(source, copy=False)
        entry = next(item for item in self.index.scan()["items"] if item["relative"].startswith("external:"))
        self.assertEqual(self.index.read_entry_text(entry["relative"], 4), "0123")
        with self.assertRaisesRegex(UserError, "预览长度无效"):
            self.index.read_entry_text(entry["relative"], 1_000_001)
        with self.index.store.connect() as db:
            db.execute("UPDATE knowledge_references SET target_path=? WHERE root_id=? AND relative_path=?",
                       (str(self.root / "substituted.txt"), self.index.root_id, entry["relative"] ))
        with self.assertRaisesRegex(UserError, "引用已变化"):
            self.index.read_entry_text(entry["relative"], 4)

    def test_entry_text_read_rejects_paths_outside_knowledge_root(self):
        with self.assertRaisesRegex(UserError, "知识库根目录"):
            self.index.read_entry_text("../outside.txt", 100)

    def test_folder_order_round_trips_through_index_service(self):
        self.assertEqual(self.index.folder_order(), [])
        self.index.save_folder_order(["02-知识/写作", "02-知识/研究"])
        self.assertEqual(self.index.folder_order(), ["02-知识/写作", "02-知识/研究"])

    def test_folder_order_save_refuses_to_overwrite_external_edit(self):
        self.index.save_folder_order(["02-知识/原顺序"])
        path = self.vault / "99-系统" / "知识库索引" / "主题顺序.json"
        read_bytes = Path.read_bytes
        calls = 0

        def externally_edit_before_compare(target):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text('{"version": 1, "folders": ["外部顺序"]}', encoding="utf-8")
            return read_bytes(target)

        with patch.object(Path, "read_bytes", externally_edit_before_compare):
            with self.assertRaisesRegex(UserError, "已被其他程序修改"):
                self.index.save_folder_order(["02-知识/本地顺序"])

        self.assertEqual(self.index.folder_order(), ["外部顺序"])
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_broken_external_reference_is_unique_and_still_taggable(self):
        source = self.root / "model.bin"
        source.write_bytes(b"model")
        self.index.add_file(source, copy=False)
        entry = self.index.scan()["items"][0]
        source.unlink()
        self.index.update_metadata(entry["relative"], tags=["model"], favorite=True)
        report = self.index.scan()
        self.assertEqual(len(report["items"]), 1)
        self.assertTrue(report["items"][0]["missing"])
        self.assertEqual(report["items"][0]["tags"], ["model"])
        self.assertTrue(report["items"][0]["favorite"])
        self.assertFalse((self.vault / "model.bin.kref").exists())
        with self.index.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_references").fetchone()[0], 1)

    def test_reference_can_be_classified_without_moving_original(self):
        source = self.root / "original.txt"
        source.write_text("Original stays here", encoding="utf-8")
        self.index.create_folder("resources")
        self.index.add_file(source, copy=False)
        entry = self.index.scan()["items"][0]
        self.index.update_metadata(entry["relative"], tags=["reference"])
        self.index.move_file(entry["relative"], "resources")
        self.assertEqual(source.read_text(), "Original stays here")
        report = self.index.scan()
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(report["items"][0]["folder"], "resources")
        self.assertEqual(report["items"][0]["tags"], ["reference"])

    def test_bundle_member_move_rolls_back_when_a_later_member_fails(self):
        originals = self.vault / "02-知识" / "原件"
        originals.mkdir(parents=True)
        (originals / "one.md").write_text("one", encoding="utf-8")
        (originals / "two.md").write_text("two", encoding="utf-8")
        identity = self.index.create_bundle("两份资料", members=[
            "02-知识/原件/one.md", "02-知识/原件/two.md"], folder="02-知识")
        entry = next(item for item in self.index.scan()["items"] if item["relative"] == identity)
        destination = "02-知识/目标"
        (self.vault / destination).mkdir()
        move_file = self.index.move_file
        calls = 0

        def fail_on_second_member(relative, folder):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected member move failure")
            return move_file(relative, folder)

        with patch.object(self.index, "move_file", side_effect=fail_on_second_member):
            with self.assertRaisesRegex(OSError, "injected member move failure"):
                self.index.move_bundle(entry, destination)

        self.assertEqual((originals / "one.md").read_text(encoding="utf-8"), "one")
        self.assertEqual((originals / "two.md").read_text(encoding="utf-8"), "two")
        current = next(item for item in self.index.read_bundles([]) if item["id"] == entry["bundle_record"]["id"])
        self.assertEqual(current["folder"], "02-知识")
        self.assertEqual(current["members"], ["02-知识/原件/one.md", "02-知识/原件/two.md"])

    def test_bundle_member_move_updates_files_and_index_on_success(self):
        originals = self.vault / "02-知识" / "原件"
        originals.mkdir(parents=True)
        (originals / "one.md").write_text("one", encoding="utf-8")
        (originals / "two.md").write_text("two", encoding="utf-8")
        identity = self.index.create_bundle("两份资料", members=[
            "02-知识/原件/one.md", "02-知识/原件/two.md"], folder="02-知识")
        entry = next(item for item in self.index.scan()["items"] if item["relative"] == identity)
        destination = "02-知识/目标"
        (self.vault / destination).mkdir()

        self.index.move_bundle(entry, destination)

        self.assertEqual((self.vault / destination / "one.md").read_text(encoding="utf-8"), "one")
        self.assertEqual((self.vault / destination / "two.md").read_text(encoding="utf-8"), "two")
        current = next(item for item in self.index.read_bundles([]) if item["id"] == entry["bundle_record"]["id"])
        self.assertEqual(current["folder"], destination)
        self.assertEqual(current["members"], [destination + "/one.md", destination + "/two.md"])

    def test_bundle_directory_move_rolls_back_when_bundle_save_fails(self):
        source = self.root / "source"
        source.mkdir()
        (source / "note.md").write_text("note", encoding="utf-8")
        identity = self.index.import_directory(source, "copies")
        entry = next(item for item in self.index.scan()["items"] if item["relative"] == identity)
        (self.vault / "02-知识").mkdir()

        with patch.object(self.index, "save_bundle", side_effect=OSError("injected bundle save failure")):
            with self.assertRaisesRegex(OSError, "injected bundle save failure"):
                self.index.move_bundle(entry, "02-知识")

        self.assertTrue((self.vault / "copies" / "source" / "note.md").is_file())
        self.assertFalse((self.vault / "02-知识" / "source").exists())
        current = next(item for item in self.index.read_bundles([]) if item["id"] == entry["bundle_record"]["id"])
        self.assertEqual(current["directory"], "copies/source")
        self.assertEqual(current["folder"], "copies")

    def test_legacy_resource_sidecar_imports_once_and_is_preserved(self):
        note = self.vault / "legacy.md"
        note.write_text("# Legacy\nBody", encoding="utf-8")
        sidecar = self.vault / "99-系统" / "知识库索引" / "资源" / "legacy.json"
        sidecar.parent.mkdir(parents=True)
        original = {"version": 1, "relative": "legacy.md", "title": "Imported title", "notes": "Imported note", "tags": ["old"]}
        sidecar.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        first = next(item for item in self.index.scan()["items"] if item["relative"] == "legacy.md")
        self.assertEqual(first["name"], "Imported title")
        sidecar.write_text(json.dumps({**original, "title": "late external edit"}, ensure_ascii=False), encoding="utf-8")
        self.index.update_resource("legacy.md", notes="Database edit")
        second = next(item for item in self.index.scan()["items"] if item["relative"] == "legacy.md")
        self.assertEqual(second["name"], "Imported title")
        self.assertEqual(second["notes"], "Database edit")
        self.assertTrue(sidecar.is_file())

    def test_legacy_workbench_catalog_is_imported_once_as_knowledge_references(self):
        source_root = self.root / "workbench"
        resource_root = source_root / "资源"
        resource_root.mkdir(parents=True)
        source = resource_root / "旧提示.md"
        original = ("---\nid: prompt-1\ntype: prompt\nname: 旧提示\ntags: [迁移]\n"
                    "projects: [P-TEST-001]\nagents: [codex]\nfavorite: true\nsummary: 原用途\n---\n\n原正文\n")
        source.write_text(original, encoding="utf-8")

        report = self.index.import_legacy_catalog(source_root)

        self.assertEqual(report["imported"], 1)
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        key = "external:" + json.dumps(str(source.resolve()), ensure_ascii=False)
        with self.index.store.connect() as db:
            reference = db.execute("SELECT target_path FROM knowledge_references WHERE relative_path=?", (key,)).fetchone()
            metadata = db.execute("SELECT tags,favorite,projects,agents,details FROM knowledge_metadata WHERE relative_path=?",
                                  (key,)).fetchone()
        self.assertEqual(Path(reference[0]), source.resolve())
        self.assertEqual(json.loads(metadata[0]), ["迁移"])
        self.assertEqual(metadata[1], 1)
        self.assertEqual(json.loads(metadata[2]), ["P-TEST-001"])
        self.assertEqual(json.loads(metadata[3]), ["codex"])
        self.assertEqual(json.loads(metadata[4])["legacy_catalog_type"], "prompt")
        self.assertEqual(self.index.scan()["items"][0]["name"], "旧提示")
        self.assertEqual(self.index.project_resources("P-TEST-001")[0]["id"], key)

        source.write_text(original.replace("原用途", "外部后来修改"), encoding="utf-8")
        second = self.index.import_legacy_catalog(source_root)
        self.assertTrue(second["already_completed"])
        self.assertEqual(self.index.read_record(key)["summary"], "原用途")
        self.assertEqual(source.read_text(encoding="utf-8"), original.replace("原用途", "外部后来修改"))

    def test_legacy_workbench_catalog_partial_failure_can_be_repaired_and_retried(self):
        source_root = self.root / "workbench-retry"
        resource_root = source_root / "资源"
        resource_root.mkdir(parents=True)
        valid = resource_root / "valid.md"
        valid_bytes = b"---\nname: Valid\ntags: [one]\n---\n\nKeep valid source.\n"
        valid.write_bytes(valid_bytes)
        invalid = resource_root / "invalid.md"
        invalid_bytes = b"---\nname: Invalid\ntags: not-a-list\n---\n\nKeep invalid source.\n"
        invalid.write_bytes(invalid_bytes)

        first = self.index.import_legacy_catalog(source_root)

        self.assertEqual(first["imported"], 1)
        self.assertEqual(len(first["errors"]), 1)
        self.assertEqual(valid.read_bytes(), valid_bytes)
        self.assertEqual(invalid.read_bytes(), invalid_bytes)
        valid_key = "external:" + json.dumps(str(valid.resolve()), ensure_ascii=False)
        with self.index.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM knowledge_references WHERE root_id=?", (self.index.root_id,)
            ).fetchone()[0], 1)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind LIKE 'legacy-catalog:v1:%'",
                (self.index.root_id,),
            ).fetchone())
        self.assertEqual(self.index.read_record(valid_key)["tags"], ["one"])

        repaired_bytes = b"---\nname: Invalid\ntags: [repaired]\n---\n\nKeep invalid source.\n"
        invalid.write_bytes(repaired_bytes)
        second = self.index.import_legacy_catalog(source_root)
        third = self.index.import_legacy_catalog(source_root)

        self.assertEqual(second["imported"], 1)
        self.assertEqual(second["already_registered"], 1)
        self.assertEqual(second["errors"], [])
        self.assertTrue(third["already_completed"])
        self.assertEqual(third["imported"], 0)
        self.assertEqual(invalid.read_bytes(), repaired_bytes)
        with self.index.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM knowledge_references WHERE root_id=?", (self.index.root_id,)
            ).fetchone()[0], 2)


class LocalWindow(QObject):
    task_completed = Signal(str, str)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.submissions = 0
        self.submitted_titles = []

    def submit(self, _resource, title, operation, callback, **_options):
        self.submissions += 1
        self.submitted_titles.append(title)
        callback(operation(None))
        return "local-check"


class KnowledgeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        setup_theme(cls.app)

    def setUp(self):
        from tests.support import begin_ui_tracking, register_ui_cleanup
        begin_ui_tracking(self, self.app)
        root = isolated_directory(self)
        self.vault = root / "vault"
        (self.vault / "docs" / "nested").mkdir(parents=True)
        (self.vault / "docs" / "code.py").write_text("print('example')", encoding="utf-8")
        (self.vault / "docs" / "nested" / "note.md").write_text("# Example\n\nDistinctive nested content", encoding="utf-8")
        (self.vault / "link.url").write_text("[InternetShortcut]\nURL=https://example.com", encoding="utf-8")
        picture = QImage(80, 40, QImage.Format.Format_RGB32)
        picture.fill(0x2675F5)
        picture.save(str(self.vault / "picture.png"))
        store = Store(root / "data")
        store.set_setting("obsidian_vault_root", str(self.vault))
        self.window = LocalWindow(store)
        self.page = KnowledgePage(self.window)
        self.assertEqual(self.window.submissions, 0)
        self.page.resize(1380, 860)
        self.page.show()
        self.page.refresh()
        self.app.processEvents()
        register_ui_cleanup(self, self.app)

    def select(self, relative):
        row = next(i for i, entry in enumerate(self.page.shown) if entry["relative"] == relative)
        self.page.file_list.setCurrentCell(row, 0)
        self.page.file_list.selectRow(row)
        self.page.cards.setCurrentRow(row)
        self.app.processEvents()

    def test_legacy_catalog_import_ui_confirms_submits_and_refreshes(self):
        workbench = self.vault.parent / "legacy-workbench"
        resources = workbench / "资源"
        resources.mkdir(parents=True)
        source = resources / "legacy.md"
        original = b"---\nname: Legacy note\ntags: [kept]\n---\n\nOriginal body.\n"
        source.write_bytes(original)
        before = self.window.submissions

        with patch("selfManger.ui.knowledge.work_root", return_value=workbench), \
                patch("selfManger.ui.knowledge.QMessageBox.question",
                      return_value=QMessageBox.StandardButton.Yes), \
                patch("selfManger.ui.knowledge.QMessageBox.information") as information:
            self.page.import_legacy_catalog()

        self.assertEqual(self.window.submissions, before + 2)
        self.assertEqual(self.window.submitted_titles[-2:], ["迁入旧工作台资源", "扫描知识库索引"])
        self.assertEqual(source.read_bytes(), original)
        entry = next(item for item in self.page.items if item["name"] == "Legacy note")
        self.assertTrue(entry["relative"].startswith("external:"))
        self.assertEqual(entry["tags"], ["kept"])
        self.assertEqual(information.call_args.args[1], "旧资源迁入完成")

    def test_folder_browsing_search_and_clearing_filters(self):
        self.assertEqual(self.page.file_list.columnCount(), 4)
        # Filters live in a collapsible panel since the plan/knowledge redesign.
        self.page.filter_toggle.setChecked(True)
        self.app.processEvents()
        self.assertLessEqual(self.page.type_filter.geometry().right(), self.page.sort_choice.geometry().left())
        self.assertLess(self.page.folder_list.columnWidth(1), self.page.folder_list.columnWidth(0))
        self.page.folder_list.setCurrentItem(self.page.tree_items["docs"])
        self.assertEqual({entry["relative"] for entry in self.page.shown}, {"docs/nested/note.md", "docs/code.py"})
        self.page.search.setText("Distinctive")
        self.page.render_items()
        self.assertEqual([entry["relative"] for entry in self.page.shown], ["docs/nested/note.md"])
        self.page.clear_filters()
        self.assertEqual(len(self.page.shown), 4)
        self.assertFalse(self.page.directory_mode)

    def test_favorite_and_view_switch_keep_selected_resource_without_rescan(self):
        self.select("docs/code.py")
        submissions = self.window.submissions
        self.page.toggle_favorite()
        self.assertEqual(self.window.submissions, submissions)
        self.assertEqual(self.page.selected()["relative"], "docs/code.py")
        self.assertTrue(self.page.selected()["favorite"])
        self.page.toggle_view()
        self.assertEqual(self.page.selected()["relative"], "docs/code.py")
        self.page.toggle_view()
        self.assertEqual(self.page.selected()["relative"], "docs/code.py")

    def test_image_preview_has_decoded_image_and_note_actions_are_specific(self):
        self.select("picture.png")
        resource = self.page.preview.document().resource(QTextDocument.ResourceType.ImageResource, QUrl("knowledge:image"))
        self.assertIsInstance(resource, QImage)
        self.assertFalse(resource.isNull())
        self.assertFalse(self.page.note_button.isEnabled())
        self.select("docs/nested/note.md")
        self.assertTrue(self.page.note_button.isEnabled())
        self.assertIn("Distinctive nested content", self.page.preview.toPlainText())

    def test_rows_align_with_headers_and_delete_in_place(self):
        self.select("docs/code.py")
        for column in (1, 2, 3):
            self.assertEqual(self.page.file_list.item(0, column).textAlignment(), int(Qt.AlignmentFlag.AlignCenter))
        entry = self.page.selected()
        def move(path):
            Path(path).rename(self.vault.parent / "trash-code.py")
            return True
        with patch("selfManger.ui.knowledge.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                patch("selfManger.ui.knowledge.QFile.moveToTrash", side_effect=move):
            self.page.set_deleted([entry], True)
        self.assertFalse((self.vault / "docs" / "code.py").exists())
        self.assertNotIn("docs/code.py", {item["relative"] for item in self.page.shown})

    def test_recycle_bin_entry_and_topic_count_follow_deletion(self):
        keys = [self.page.quick_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.page.quick_list.count())]
        self.assertIn("deleted", keys)
        self.assertNotIn("archived", keys)
        self.select("docs/code.py")
        self.assertEqual(self.page.tree_items["docs"].text(1), "2")

        self.page.index.update_resource("docs/code.py", deleted=True)
        self.page.refresh()
        self.assertEqual(self.page.tree_items["docs"].text(1), "1")
        self.assertNotIn("docs/code.py", {item["relative"] for item in self.page.shown})

        row = keys.index("deleted")
        self.page.quick_list.setCurrentRow(row)
        self.app.processEvents()
        self.assertIn("docs/code.py", {item["relative"] for item in self.page.shown})
        self.page.set_deleted([self.page.selected()], False)
        self.assertEqual(self.page.tree_items["docs"].text(1), "2")

    def test_purge_button_empties_recycle_bin_and_drops_records(self):
        self.assertTrue(self.page.purge_button.isHidden())
        self.select("docs/code.py")
        self.page.index.update_resource("docs/code.py", deleted=True)
        self.page.refresh()
        self.page.quick_filter = "deleted"
        self.page.render_items()
        self.app.processEvents()
        self.assertFalse(self.page.purge_button.isHidden())

        with patch("selfManger.ui.knowledge.QFile.moveToTrash", return_value=True) as trash, \
                patch("selfManger.ui.knowledge.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.page.purge_deleted()
        trash.assert_called_once()
        self.assertEqual(Path(trash.call_args[0][0]).name, "code.py")
        self.assertEqual(self.page.index.read_record("docs/code.py"), {})
        self.assertNotIn("docs/code.py", {item["relative"] for item in self.page.shown})
        self.assertTrue(self.page.purge_button.isHidden())

    def test_purge_keeps_failed_index_cleanup_visible_after_file_reaches_system_trash(self):
        self.page.index.update_resource("docs/code.py", deleted=True)
        self.page.refresh()
        self.page.quick_filter = "deleted"
        self.page.render_items()
        recycle = self.vault.parent / "recycle-code.py"

        def move_to_trash(path):
            Path(path).rename(recycle)
            return True

        with patch("selfManger.ui.knowledge.QFile.moveToTrash", side_effect=move_to_trash), \
                patch.object(self.page.index, "purge_resource", side_effect=OSError("database unavailable")), \
                patch("selfManger.ui.knowledge.QMessageBox.question",
                      return_value=QMessageBox.StandardButton.Yes), \
                patch("selfManger.ui.knowledge.QMessageBox.information") as information:
            self.page.purge_deleted()

        self.assertTrue(recycle.exists())
        self.assertTrue(self.page.index.read_record("docs/code.py"))
        self.assertIn("docs/code.py", {item["relative"] for item in self.page.shown})
        message = information.call_args.args[2]
        self.assertIn("本地文件已移入系统回收站", message)
        self.assertIn("记录仍保留", message)

    def test_row_context_menu_offers_delete_and_restore(self):
        self.select("docs/code.py")
        menu = self.page.build_resource_menu(self.page.selected_entries())
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertIn("删除", labels)
        self.assertNotIn("恢复到知识库", labels)

        self.page.index.update_resource("docs/code.py", deleted=True)
        self.page.refresh()
        self.page.quick_filter = "deleted"
        self.page.render_items()
        self.app.processEvents()
        self.assertTrue(self.page.file_list.rowCount())
        menu = self.page.build_resource_menu(self.page.selected_entries())
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertIn("恢复到知识库", labels)
        self.assertNotIn("删除", labels)

    def test_pagination_and_search_cover_files_beyond_current_page(self):
        for number in range(205):
            (self.vault / f"file{number:03}.txt").write_text("text", encoding="utf-8")
        self.page.preferred_identity = ""
        self.page.refresh()
        self.assertEqual(self.page.file_list.rowCount(), 200)
        self.assertTrue(self.page.next_button.isEnabled())
        self.page.change_page(1)
        self.assertEqual(self.page.file_list.rowCount(), 9)
        self.assertTrue(self.page.previous_button.isEnabled())
        self.page.search.setText("file204.txt")
        self.page.render_items()
        self.assertEqual(self.page.file_list.rowCount(), 1)
        self.assertEqual(self.page.selected()["relative"], "file204.txt")

    def test_creating_under_project_and_agent_scope_keeps_new_note_visible(self):
        self.page.set_scope(project="P-example", agent="agent-example")
        self.assertFalse(self.page.shown)
        path = self.page.index.add_note("associated", "new note", "")
        self.page.associate_created(path)
        self.page.refresh()
        self.assertEqual([entry["relative"] for entry in self.page.shown], ["associated.md"])
        self.assertEqual(self.page.selected()["projects"], ["P-example"])
        self.assertEqual(self.page.selected()["agents"], ["agent-example"])


if __name__ == "__main__":
    unittest.main()
