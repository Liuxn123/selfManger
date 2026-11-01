"""Document-only Vault entries open the original task files without importing data."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from tests import test_obsidian as service_fixture
from tests import test_ui as ui_fixture
from tests.support import visual
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from selfManger.domain import UserError
from selfManger.obsidian import project_uri
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.theme import setup_theme


def document_project(workspace):
    project = workspace.create("文档入口", "只接文档和任务", False, TaskContext())
    registry = workspace.registry()
    item = next(row for row in registry["projects"] if row["id"] == project["project_id"])
    item.update(vault_entry="myself/03-项目/" + Path(item["path"]).name,
                link_policy="junction", vault_scope="documents", vault_subdirs=["agent"])
    workspace.registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    entry = workspace.root / item["vault_entry"]
    entry.mkdir()
    (entry / "README.md").write_text("# 文档入口\n导航说明", encoding="utf-8")
    workspace._link(item["vault_entry"] + "/agent", Path(project["directory"]) / "agent")
    return project, item, entry


class DocumentEntryTests(unittest.TestCase):
    setUp = service_fixture.ObsidianTests.setUp
    tearDown = service_fixture.ObsidianTests.tearDown

    def test_tasks_status_and_milestones_use_lexical_subjunction_without_writes(self):
        project, item, entry = document_project(self.workspace)
        before = self.workspace.registry_path.read_bytes()
        for name in ("TASKS.md", "TASKS-AI.md", "TASKS-MANUAL.md", "STATUS.md", "MILESTONES.md"):
            source = Path(project["directory"]) / "agent" / name
            if not source.exists():
                source.write_text("# 独立任务原文", encoding="utf-8")
            content = source.read_bytes()
            report = project_uri(self.workspace, item["id"], "agent/" + name)
            params = parse_qs(urlparse(report["uri"]).query)
            self.assertEqual(params["path"], [str(entry / "agent" / name)])
            self.assertEqual((entry / "agent" / name).resolve(), source.resolve())
            self.assertEqual(content, source.read_bytes())
        self.assertEqual(before, self.workspace.registry_path.read_bytes())
        self.assertEqual(project_uri(self.workspace, item["id"], "README.md")["path"], str(entry / "README.md"))

    def test_wrong_subjunction_missing_file_and_unregistered_directory_are_rejected(self):
        project, item, entry = document_project(self.workspace)
        with self.assertRaises(UserError):
            project_uri(self.workspace, item["id"], "agent/../README.md")
        with self.assertRaisesRegex(UserError, "尚未接入"):
            project_uri(self.workspace, item["id"], "AGENTS.md")
        source = Path(project["directory"]) / "agent/STATUS.md"
        missing = source.with_name("STATUS.saved")
        source.rename(missing)
        with self.assertRaisesRegex(UserError, "已移动或不存在"):
            project_uri(self.workspace, item["id"], "agent/STATUS.md")
        missing.rename(source)
        other = self.workspace.create("其他文档", "不打开其他项目", False, self.context)
        registry = self.workspace.registry()
        row = next(p for p in registry["projects"] if p["id"] == item["id"])
        row["vault_entry"] += "-wrong"
        self.workspace.registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
        wrong = self.workspace.root / row["vault_entry"]
        wrong.mkdir()
        self.workspace._link(row["vault_entry"] + "/agent", Path(other["directory"]) / "agent")
        with self.assertRaisesRegex(UserError, "指向其他目录"):
            project_uri(self.workspace, item["id"], "agent/STATUS.md")


class DocumentEntryUITests(unittest.TestCase):
    setUp = ui_fixture.UITests.setUp

    def tearDown(self):
        from tests.support import wait_for_idle
        # Project refresh can queue a delayed detail load after the URL opens.
        wait_for_idle(self.app, self.window)
        ui_fixture.UITests.tearDown(self)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        setup_theme(cls.app)

    @visual
    def test_new_project_dialog_defaults_to_document_entry_and_label_fits(self):
        from selfManger.ui.projects import ProjectDialog
        dialog = ProjectDialog(self.window)
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.entry.isChecked())
        self.assertIn("笔记、文档和项目任务", dialog.entry.text())
        self.assertIn("源码", dialog.entry.toolTip())
        self.assertGreater(dialog.entry.width(), dialog.entry.fontMetrics().horizontalAdvance(dialog.entry.text()) + 25)
        import os
        output = os.environ.get("SELFMANGER_QA_OUTPUT")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            self.assertTrue(dialog.grab().save(str(Path(output) / "new-project-document-entry.png")))

    def test_task_tab_top_button_opens_index_through_document_entry(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        (workspace.root / "myself/.obsidian").mkdir()
        project, item, entry = document_project(workspace)
        self.store.set_setting("project_workspace", str(workspace.root))
        page = self.window.project_page
        self.window.navigation.setCurrentRow(self.window.PROJECT)
        page.refresh()
        import time
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (not page.selected() or self.window.jobs):
            QTest.qWait(30)
            time.sleep(0.005)
        self.assertEqual(page.selected()["id"], item["id"])
        page.documents.setCurrentWidget(page.task_page)
        self.app.processEvents()
        self.assertTrue(page.obsidian_button.isEnabled())
        original = (Path(project["directory"]) / "agent/TASKS.md").read_bytes()
        with patch("selfManger.ui.projects.QDesktopServices.openUrl", return_value=True) as opened:
            page.obsidian_button.click()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not opened.called:
                QTest.qWait(30)
                time.sleep(0.005)
            self.assertEqual(opened.call_count, 1)
            params = parse_qs(urlparse(opened.call_args[0][0].toString()).query)
            self.assertEqual(params["path"], [str(entry / "agent/TASKS.md")])
        self.assertEqual(original, (Path(project["directory"]) / "agent/TASKS.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
