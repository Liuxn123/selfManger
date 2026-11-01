from __future__ import annotations

import unittest
from tests.support import TestDirectory
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from selfManger.domain import UserError
from selfManger.obsidian import project_uri, index_uri
from selfManger.project_workspaces import ProjectWorkspace, field
from selfManger.runtime import TaskContext


class ObsidianTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TestDirectory()
        self.workspace = ProjectWorkspace(Path(self.temporary.name) / "workspace")
        self.context = TaskContext()
        self.workspace.initialize(self.context)
        (self.workspace.root / "myself/.obsidian").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_uri_preserves_chinese_spaces_reserved_characters_and_lexical_entry(self):
        project = self.workspace.create("中文 # 项目", "联动验证", True, self.context)
        note = self.workspace.create_note(project["project_id"], "说明 # [草稿] &", self.context)
        report = project_uri(self.workspace, project["project_id"], note["relative"])
        params = parse_qs(urlparse(report["uri"]).query)
        entry = self.workspace.project(project["project_id"])["vault_entry"]
        expected = self.workspace.root / entry / note["relative"]
        self.assertEqual(params["path"], [str(expected)])
        self.assertEqual(params["paneType"], ["tab"])
        self.assertNotEqual(str(expected), str(expected.resolve()))
        self.assertEqual(expected.resolve(), Path(note["path"]).resolve())
        self.assertNotIn("#", report["uri"])
        self.assertEqual(field(Path(note["path"]).read_text(encoding="utf-8"), "status"), "draft")

    def test_note_duplicate_traversal_and_archive_are_rejected(self):
        project = self.workspace.create("笔记项目", "验证边界", False, self.context)
        identity = project["project_id"]
        note = self.workspace.create_note(identity, "说明", self.context)
        Path(note["path"]).write_text("用户正文", encoding="utf-8")
        with self.assertRaises(UserError):
            self.workspace.create_note(identity, "说明", self.context)
        self.assertEqual(Path(note["path"]).read_text(encoding="utf-8"), "用户正文")
        for name in ("../outside", "..", "CON", "name:stream"):
            with self.assertRaises(UserError):
                self.workspace.create_note(identity, name, self.context)
        with self.assertRaises(UserError):
            self.workspace.note_document(identity, "笔记/../agent/STATUS.md")
        self.workspace.set_archived(identity, True, self.context)
        with self.assertRaises(UserError):
            self.workspace.create_note(identity, "不能写归档", self.context)

    def test_external_note_edit_is_read_from_original_and_survives_archive(self):
        project = self.workspace.create("联动项目", "同一份文件", True, self.context)
        identity = project["project_id"]
        note = self.workspace.create_note(identity, "实验说明", self.context)
        entry = self.workspace.root / self.workspace.project(identity)["vault_entry"]
        (entry / note["relative"]).write_text("# Obsidian 编辑\n外部新内容", encoding="utf-8")
        self.assertIn("外部新内容", self.workspace.note_document(identity, note["relative"])["text"])
        self.workspace.set_archived(identity, True, self.context)
        self.assertFalse(entry.exists())
        self.assertIn("外部新内容", Path(note["path"]).read_text(encoding="utf-8"))
        with self.assertRaises(UserError):
            project_uri(self.workspace, identity, note["relative"])
        self.workspace.set_archived(identity, False, self.context)
        self.assertIn("外部新内容", Path(note["path"]).read_text(encoding="utf-8"))

    def test_wrong_entry_and_missing_vault_do_not_open_another_project(self):
        project = self.workspace.create("原项目", "入口验证", True, self.context)
        other = self.workspace.create("其他项目", "隔离", False, self.context)
        # Retain the document entry and replace its visible path with a wrong root link.
        entry = self.workspace.root / self.workspace.project(project["project_id"])["vault_entry"]
        entry.rename(entry.with_name("." + entry.name + ".saved"))
        self.workspace._link(self.workspace.project(project["project_id"])["vault_entry"], Path(other["directory"]))
        with self.assertRaisesRegex(UserError, "入口缺失或不是普通目录"):
            project_uri(self.workspace, project["project_id"], "README.md")
        (self.workspace.root / "myself/.obsidian").rmdir()
        with self.assertRaisesRegex(UserError, "知识库打开"):
            index_uri(self.workspace)

    def test_no_entry_policy_is_preserved_and_standalone_vault_can_open(self):
        project = self.workspace.create("无入口", "保持例外", False, self.context)
        before = self.workspace.registry_path.read_bytes()
        with self.assertRaisesRegex(UserError, "没有 Obsidian 入口"):
            project_uri(self.workspace, project["project_id"], "agent/STATUS.md")
        (Path(project["directory"]) / ".obsidian").mkdir()
        report = project_uri(self.workspace, project["project_id"], "agent/STATUS.md")
        self.assertEqual(report["path"], str(Path(project["directory"]) / "agent/STATUS.md"))
        self.assertEqual(self.workspace.registry_path.read_bytes(), before)

    def test_manual_index_text_survives_external_status_refresh(self):
        project = self.workspace.create("状态联动", "索引刷新", False, self.context)
        index = self.workspace.root / "myself/03-项目/项目总览.md"
        index.write_text(index.read_text(encoding="utf-8") + "\n人工导航保留\n", encoding="utf-8")
        status = Path(project["directory"]) / "agent/STATUS.md"
        status.write_text(status.read_text(encoding="utf-8").replace("status: active", "status: paused"), encoding="utf-8")
        self.workspace.refresh_indexes(self.context)
        self.assertIn("| paused |", index.read_text(encoding="utf-8"))
        self.assertIn("人工导航保留", index.read_text(encoding="utf-8"))
        self.assertEqual(parse_qs(urlparse(index_uri(self.workspace, True)["uri"]).query)["path"], [str(index.with_name("归档项目索引.md"))])


if __name__ == "__main__":
    unittest.main()
