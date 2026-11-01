"""Generated Vault names must stay readable: ``名称-短编号`` instead of hash soup."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from selfManger.knowledge import KnowledgeIndex
from selfManger.naming import document_stem, is_legacy_name, matches_identity, readable_name, temporary_path
from selfManger.planbook import PlanBook
from tests.legacy_plan_fixtures import write_legacy_plan
from selfManger.storage import Store

HASH_ONLY = re.compile(r"^[0-9a-f]{16,64}$")


def isolated_directory(test):
    parent = Path(tempfile.gettempdir()).resolve()
    root = parent / ("naming-check-" + uuid4().hex)
    root.mkdir()

    def cleanup():
        resolved = root.resolve()
        if resolved.parent != parent or not resolved.name.startswith("naming-check-"):
            raise RuntimeError("Unexpected test cleanup path")
        shutil.rmtree(resolved)

    test.addCleanup(cleanup)
    return root


class NamingRuleTests(unittest.TestCase):
    def test_readable_name_keeps_chinese_and_drops_illegal_characters(self):
        self.assertEqual(readable_name('周报 / 2026:第4周*'), "周报 _ 2026_第4周")
        self.assertEqual(readable_name("   多   空格  "), "多 空格")
        self.assertEqual(readable_name("con"), "con_")
        self.assertTrue(len(readable_name("很长的标题" * 40)) <= 60)

    def test_document_stem_is_title_plus_short_id(self):
        identity = "a5054891617e4c13a1ee6bfa3eaf1fb2"
        self.assertEqual(document_stem("个人云盘", identity), "个人云盘-a5054891")
        self.assertEqual(document_stem("", identity), "a5054891")
        self.assertTrue(matches_identity("个人云盘-a5054891", identity))
        self.assertTrue(matches_identity(identity, identity))
        self.assertFalse(matches_identity("别的名称-ffffffff", identity))

    def test_temporary_name_is_hidden_and_readable(self):
        path = Path("E:/vault/计划/安排/个人云盘-a5054891.md")
        temporary = temporary_path(path, "0123456789abcdef")
        self.assertTrue(temporary.name.startswith("."))
        self.assertIn("个人云盘-a5054891", temporary.name)
        self.assertTrue(temporary.name.endswith(".tmp"))

    def test_legacy_detection(self):
        self.assertTrue(is_legacy_name("a5054891617e4c13a1ee6bfa3eaf1fb2"))
        self.assertFalse(is_legacy_name("个人云盘-a5054891"))


class PlanNamingTests(unittest.TestCase):
    def setUp(self):
        self.root = isolated_directory(self) / "work"

    def test_legacy_plan_file_is_readable_without_modification(self):
        book = PlanBook(self.root)
        legacy_id = uuid4().hex
        filename = document_stem("个人云盘或者syncPan", legacy_id) + ".md"
        legacy = write_legacy_plan(self.root, "plan", legacy_id,
                                   {"title": "个人云盘或者syncPan", "status": "planned",
                                    "start": "", "due": "2028-01-01", "project_id": "",
                                    "parent_id": "", "frequency": "", "progress": 0,
                                    "created": "2026-10-01", "updated": "2026-10-01"},
                                   "旧正文\n", filename=filename)
        original = legacy.read_bytes()
        items = book.scan("plan")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], legacy_id)
        self.assertRegex(legacy.stem, r"^个人云盘或者syncPan-[0-9a-f]{8}$")
        self.assertEqual(legacy.read_bytes(), original)

    def test_legacy_knowledge_json_is_not_part_of_readable_rename_tool(self):
        base = isolated_directory(self)
        vault = base / "vault"
        records = vault / "99-系统/知识库索引/资源"
        bundles = vault / "99-系统/知识库索引/资源组"
        records.mkdir(parents=True)
        bundles.mkdir(parents=True)
        sidecar = records / ("a" * 64 + ".json")
        bundle = bundles / ("topic-" + "b" * 32 + ".json")
        sidecar.write_text(json.dumps({"version": 1, "relative": "note.md", "title": "note"}), encoding="utf-8")
        bundle.write_text(json.dumps({"version": 1, "id": "topic-" + "b" * 32, "title": "Old bundle"}), encoding="utf-8")
        report = base / "report"
        script = Path(__file__).resolve().parents[1] / "scripts/rename_readable.py"
        result = subprocess.run([sys.executable, str(script), "--vault", str(vault), "--report", str(report)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((report / "manifest.json").read_text(encoding="utf-8"))["changes"], [])
        self.assertTrue(sidecar.is_file())
        self.assertTrue(bundle.is_file())


class KnowledgeSidecarNamingTests(unittest.TestCase):
    def setUp(self):
        base = isolated_directory(self)
        self.vault = base / "vault"
        self.vault.mkdir()
        self.index = KnowledgeIndex(Store(base / "data"), self.vault)
        self.record_dir = self.vault / "99-系统/知识库索引/资源"
        self.note = self.vault / "02-知识/示例笔记.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text("# 示例笔记\n\n正文\n", encoding="utf-8")

    def test_sidecar_name_is_readable(self):
        self.index.scan()
        self.index.update_resource("02-知识/示例笔记.md", title="示例笔记")
        self.assertFalse(self.record_dir.exists())
        self.assertEqual(self.index.read_record("02-知识/示例笔记.md")["title"], "示例笔记")

    def test_legacy_hash_sidecar_is_read_and_migrated_on_write(self):
        relative = "02-知识/示例笔记.md"
        legacy = self.record_dir / (hashlib.sha256(relative.encode("utf-8")).hexdigest() + ".json")
        self.record_dir.mkdir(parents=True)
        legacy.write_text(json.dumps({"version": 1, "relative": relative, "tags": ["旧标签"]}, ensure_ascii=False),
                          encoding="utf-8")
        self.assertEqual(self.index.read_record(relative)["tags"], ["旧标签"])
        self.index.update_resource(relative, title="新标题")
        self.assertTrue(legacy.is_file())
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["tags"], ["旧标签"])
        self.assertEqual(self.index.read_record(relative)["title"], "新标题")
        self.assertEqual(self.index.read_record(relative)["tags"], ["旧标签"])


class BundleNamingTests(unittest.TestCase):
    def setUp(self):
        base = isolated_directory(self)
        self.vault = base / "vault"
        self.vault.mkdir()
        self.index = KnowledgeIndex(Store(base / "data"), self.vault)
        self.folder = self.vault / "02-知识/Java"
        self.folder.mkdir(parents=True)
        (self.folder / "a.md").write_text("# A\n", encoding="utf-8")

    def test_bundle_file_is_readable_and_id_stays_findable(self):
        reference = self.index.create_bundle("Java 学习资料", directory=str(self.folder))
        identity = reference.split(":", 1)[1]
        self.assertEqual(self.index.read_bundles([])[0]["id"], identity)
        self.assertFalse((self.vault / "99-系统/知识库索引/资源组").exists())

    def test_legacy_bundle_file_remains_readable(self):
        identity = "topic-37867f8ce537fc5d5c3ef370a0a7d488"
        directory = self.vault / "99-系统/知识库索引/资源组"
        directory.mkdir(parents=True)
        (directory / (identity + ".json")).write_text(json.dumps({
            "version": 1, "id": identity, "title": "旧资源组", "folder": "02-知识", "members": [],
            "entry": "", "active": True, "summary": "", "excluded": []}, ensure_ascii=False), encoding="utf-8")
        bundles = self.index.read_bundles([])
        self.assertEqual([value["id"] for value in bundles], [identity])
        self.assertTrue((directory / (identity + ".json")).is_file())

    def test_rename_to_new_title_moves_file_and_drops_the_old_name(self):
        reference = self.index.create_bundle("Java 学习资料", directory=str(self.folder))
        identity = reference.split(":", 1)[1]
        entry = next(value for value in self.index.read_bundles([]) if value["id"] == identity)
        self.index.save_bundle({**entry, "title": "Java 后端资料"})
        directory = self.vault / "99-系统/知识库索引/资源组"
        self.assertFalse(directory.exists())
        self.assertEqual(next(value for value in self.index.read_bundles([]) if value["id"] == identity)["title"], "Java 后端资料")


if __name__ == "__main__":
    unittest.main()
