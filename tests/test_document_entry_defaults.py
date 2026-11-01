"""Default project creation and lifecycle keep only declared documents in the Vault."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_project_workspaces as fixture
from selfManger.domain import UserError
from selfManger.obsidian import project_uri
from selfManger.project_workspaces import DEFAULT_VAULT_SUBDIRS, field, linked


class DocumentDefaultTests(unittest.TestCase):
    setUp = fixture.ProjectWorkspaceTests.setUp
    tearDown = fixture.ProjectWorkspaceTests.tearDown

    def make(self):
        project = self.workspace.create("默认文档项目", "只接入可编辑资料", True, self.context)
        item = self.workspace.project(project["project_id"])
        return project, item, self.workspace.root / item["vault_entry"]

    def test_new_project_only_exposes_documents_and_agent_task_records(self):
        project, item, entry = self.make()
        (self.workspace.root / "myself/.obsidian").mkdir()
        self.assertEqual(item["vault_scope"], "documents")
        self.assertEqual(item["vault_subdirs"], list(DEFAULT_VAULT_SUBDIRS))
        self.assertFalse(linked(entry))
        real = Path(project["directory"])
        for name in ("src", "qa", "data", ".venv", "dist"):
            (real / name).mkdir()
            (real / name / "payload.bin").write_bytes(b"not indexed")
            self.assertFalse((entry / name).exists())
        self.assertEqual({p.name for p in entry.iterdir()}, {*DEFAULT_VAULT_SUBDIRS, "README.md"})
        for name in DEFAULT_VAULT_SUBDIRS:
            self.assertTrue(linked(entry / name))
            self.assertEqual((entry / name).resolve(), (real / name).resolve())
        for name in ("MILESTONES.md",):
            self.assertTrue((entry / "agent" / name).is_file())
            self.assertEqual(project_uri(self.workspace, item["id"], "agent/" + name)["path"], str(entry / "agent" / name))
        self.assertTrue((entry / "agent" / "TASKS.md").is_file())
        self.assertFalse((entry / "agent" / "TASKS-AI.md").exists())
        self.assertFalse((entry / "agent" / "TASKS-MANUAL.md").exists())
        self.assertIn("任务清单", (entry / "README.md").read_text(encoding="utf-8"))

    def test_archive_resume_preserve_source_and_edited_entry_readme(self):
        project, item, entry = self.make()
        real = Path(project["directory"])
        original = "# 用户保留的说明\n"
        (entry / "README.md").write_text(original, encoding="utf-8")
        (real / "笔记/原文.md").write_text("不复制原文", encoding="utf-8")
        self.workspace.set_archived(item["id"], True, self.context)
        hidden = entry.with_name("." + entry.name + ".archived")
        self.assertFalse(entry.exists())
        self.assertTrue(hidden.is_dir())
        self.assertTrue((real / "笔记/原文.md").is_file())
        self.workspace.set_archived(item["id"], False, self.context)
        self.assertFalse(hidden.exists())
        self.assertFalse(linked(entry))
        self.assertEqual((entry / "README.md").read_text(encoding="utf-8"), original)
        self.assertEqual((entry / "笔记/原文.md").read_text(encoding="utf-8"), "不复制原文")

    def test_archive_index_failure_rolls_back_status_and_keeps_document_entry(self):
        project, item, entry = self.make()
        status = Path(project["directory"]) / "agent/STATUS.md"
        before = status.read_bytes()
        real_write = self.workspace.write
        def failed_index(path, text, expected=None):
            if path.name == "项目总览.md":
                raise UserError("injected index failure")
            return real_write(path, text, expected)
        with patch.object(self.workspace, "write", side_effect=failed_index):
            with self.assertRaises(UserError):
                self.workspace.set_archived(item["id"], True, self.context)
        self.assertEqual(status.read_bytes(), before)
        self.assertTrue(entry.is_dir())
        self.assertFalse(linked(entry))

    def test_delete_archived_document_entry_cleans_hidden_wrapper_and_keeps_other_project(self):
        project, item, entry = self.make()
        other = self.workspace.create("保留项目", "不删除其他原文", False, self.context)
        sentinel = Path(other["directory"]) / "保留.txt"
        sentinel.write_text("keep", encoding="utf-8")
        self.workspace.set_archived(item["id"], True, self.context)
        hidden = entry.with_name("." + entry.name + ".archived")
        result = self.workspace.delete_project(item["id"], item["name"], self.context)
        self.assertTrue(result["deleted"])
        self.assertFalse(hidden.exists())
        self.assertFalse(Path(project["directory"]).exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertIn(item["id"], self.workspace.registry()["issued_ids"])

    def test_incorrect_child_target_blocks_archive_without_status_change(self):
        project, item, entry = self.make()
        status = Path(project["directory"]) / "agent/STATUS.md"
        before = status.read_bytes()
        (entry / "文档").rename(entry / "文档-original")
        other = self.workspace.create("其他目标", "不触及它", False, self.context)
        self.workspace._link(item["vault_entry"] + "/文档", Path(other["directory"]) / "agent")
        with self.assertRaises(UserError):
            self.workspace.set_archived(item["id"], True, self.context)
        self.assertEqual(status.read_bytes(), before)
        self.assertTrue(entry.exists())

    def test_legacy_whole_project_entry_still_archives_and_resumes(self):
        project = self.workspace.create("旧整项目", "保留兼容", False, self.context)
        data = self.workspace.registry()
        item = data["projects"][0]
        item.update(vault_entry="myself/03-项目/" + Path(item["path"]).name, link_policy="junction")
        self.workspace.registry_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.workspace._link(item["vault_entry"], Path(project["directory"]))
        entry = self.workspace.root / item["vault_entry"]
        self.workspace.set_archived(item["id"], True, self.context)
        self.assertFalse(entry.exists())
        self.workspace.set_archived(item["id"], False, self.context)
        self.assertTrue(linked(entry))
        self.assertEqual(entry.resolve(), Path(project["directory"]).resolve())


if __name__ == "__main__":
    unittest.main()
