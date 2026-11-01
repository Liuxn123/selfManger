"""Project task edits use shared SQLite records."""
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from tests import test_plan_modules as fixture
from tests.support import evidence_root
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.knowledge import KnowledgeIndex
from selfManger.domain import UserError
from selfManger.ui.projects import ProjectDialog
from selfManger.ui import projects
from selfManger.ui.plan_module_widgets import ProjectTaskStatusDialog
from selfManger.ui.theme import setup_theme
from selfManger.ui.task_dialog import UnifiedTaskDialog
from selfManger.project_task_documents import _render


class ProjectTaskInteractionTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window

    def seed(self):
        window = self.make_window()
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        project = workspace.create("交互验证", "仅隔离数据", False, TaskContext())["project_id"]
        item = workspace.project(project)
        for source, filename in (("ai", "TASKS-AI.md"), ("manual", "TASKS-MANUAL.md")):
            path = workspace.path(item["path"] + "/agent/" + filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render(source, []), encoding="utf-8")
        window.store.set_setting("project_workspace", str(workspace.root))
        page = window.project_page
        page.refresh()
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        return window, page, workspace, project

    def test_shared_task_dialog_loads_plan_options_from_sqlite(self):
        window, _page, _workspace, _project = self.seed()
        plan = window.planning_page.service.save_plan({"title": "SQLite 中的长期计划"})
        dialog = UnifiedTaskDialog(window)
        index = dialog.goal.findData(plan["id"])
        self.assertGreaterEqual(index, 0)
        self.assertIn("SQLite 中的长期计划", dialog.goal.itemText(index))
        dialog.close()

    def test_shared_task_dialog_discloses_unavailable_project_list(self):
        window, _page, workspace, _project = self.seed()
        window.store.set_setting("project_workspace", str(workspace.root))
        with patch.object(ProjectWorkspace, "registry", side_effect=PermissionError("locked")):
            dialog = UnifiedTaskDialog(window)
        self.assertIn("项目列表加载失败（PermissionError）", dialog.project.toolTip())
        self.assertIn("手动输入项目编号", dialog.project.toolTip())
        dialog.close()

    def select_task(self, page, identity, source="manual"):
        widget = page.task_lists[source]
        for index in range(widget.count()):
            row = widget.item(index)
            if (row.data(Qt.ItemDataRole.UserRole) or {}).get("id") == identity:
                widget.setCurrentItem(row)
                return row
        self.fail("shared task not shown")

    def test_project_related_materials_come_from_knowledge_metadata(self):
        window, page, workspace, project = self.seed()
        vault = self.root / "knowledge"
        notes = vault / "02-知识"
        notes.mkdir(parents=True)
        (notes / "关联资料.md").write_text("# 关联资料\n", encoding="utf-8")
        (notes / "无关联资料.md").write_text("# 无关联资料\n", encoding="utf-8")
        window.store.set_setting("obsidian_vault_root", str(vault))
        index = KnowledgeIndex(window.store, vault)
        index.update_metadata("02-知识/关联资料.md", projects=[project])

        with patch.object(KnowledgeIndex, "scan", side_effect=AssertionError("project refresh rescanned the Vault")):
            page.refresh()
            self.settle(window)

        self.assertEqual([entry["name"] for entry in page.project_knowledge], ["关联资料"])
        self.assertEqual(page.related_list.count(), 1)
        related = page.related_list.item(0)
        self.assertIn("关联资料", related.text())
        self.assertEqual(related.data(Qt.ItemDataRole.UserRole), ("knowledge", "02-知识/关联资料.md"))

    def test_active_actions_order_status_delete_and_restart(self):
        window, page, workspace, project = self.seed()
        center = window.planning_page.service
        scope = {"project_id": project, "project_root": str(workspace.root)}
        completed = center.save_action({**scope, "title": "已完成", "status": "done"})
        active = center.save_action({**scope, "title": "进行中", "status": "active"})
        page.refresh()
        self.settle(window)
        rows = page.linked_project_tasks({"id": project})
        self.assertEqual([row["id"] for row in rows], [active["id"], completed["id"]])
        self.assertIn("进行中", self.select_task(page, active["id"]).text())
        setup_theme(self.app)
        window.resize(1440, 1000)
        page.documents.setCurrentWidget(page.task_page)
        self.app.processEvents()
        output = evidence_root().parent
        self.assertTrue(page.task_page.grab().save(str(output / "project-tasks.png")))
        def save(dialog):
            dialog.combo.setCurrentIndex(dialog.combo.findData("done"))
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(ProjectTaskStatusDialog, "exec", save):
            page.edit_shared_task_status("manual")
        self.assertEqual(window.store.planned_task(active["id"])["status"], "done")
        self.assertIsNone(center.documents.path("action", active["id"]))
        self.select_task(page, active["id"])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.delete_shared_task("manual")
            self.settle(window)
        self.assertIsNone(window.store.planned_task(active["id"]))
        self.assertFalse(window.today_page.open_daily_button.isVisible())
        self.assertEqual(len(center.actions()), 1)
        reopened = Store(window.store.root)
        self.assertIsNone(reopened.planned_task(active["id"]))
        self.assertEqual(reopened.planned_task(completed["id"])["status"], "done")

    def test_project_task_mutations_do_not_write_legacy_documents(self):
        window, page, workspace, project = self.seed()
        task = window.store.add_planned_task({"title": "原AI任务", "source": "ai", "project_id": project,
                                              "project_root": str(workspace.root)})
        path = workspace.path(workspace.project(project)["path"] + "/agent/TASKS-AI.md")
        path.write_text(_render("ai", [task]), encoding="utf-8")
        original = path.read_bytes()
        page.refresh()
        self.settle(window)
        self.assertEqual(window.store.planned_task(task["id"])["title"], "原AI任务")
        self.select_task(page, task["id"], "ai")
        def complete(dialog):
            dialog.combo.setCurrentIndex(dialog.combo.findData("done"))
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(ProjectTaskStatusDialog, "exec", complete):
            page.edit_shared_task_status("ai")
        self.assertEqual(window.store.planned_task(task["id"])["status"], "done")
        self.assertEqual(path.read_bytes(), original)
        self.select_task(page, task["id"], "ai")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.delete_shared_task("ai")
            self.settle(window)
        page.refresh()
        self.settle(window)
        self.assertIsNone(window.store.planned_task(task["id"]))
        self.assertEqual(path.read_bytes(), original)

    def test_import_button_previews_and_requires_confirmation(self):
        window, page, workspace, project = self.seed()
        path = workspace.path(workspace.project(project)["path"] + "/agent/TASKS-AI.md")
        path.write_text(_render("ai", [{"id": "new", "title": "迁入任务", "kind": "task", "status": "planned"}]), encoding="utf-8")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            page.import_legacy_project_tasks()
            self.settle(window)
            self.assertTrue(question.called)
            self.assertIn("一次性迁移", question.call_args.args[2])
        self.assertEqual(window.store.planned_tasks(project_id=project, project_root=str(workspace.root)), [])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.import_legacy_project_tasks()
            self.settle(window)
        self.assertEqual([row["title"] for row in window.store.planned_tasks(project_id=project, project_root=str(workspace.root))], ["迁入任务"])

    def test_invalid_document_reports_location_preserves_source_and_other_details(self):
        window, page, workspace, project = self.seed()
        path = workspace.path(workspace.project(project)["path"] + "/agent/TASKS-AI.md")
        invalid = _render("ai", [{"id": "new", "title": "错误类型任务", "source": "ai", "kind": "invalid-kind"}])
        path.write_text(invalid, encoding="utf-8")
        page.refresh()
        self.settle(window)
        self.assertEqual(path.read_text(encoding="utf-8"), invalid)
        from selfManger.project_task_documents import import_legacy_project_task_documents
        with self.assertRaises(UserError) as error:
            import_legacy_project_task_documents(window.store, workspace, project)
        self.assertIn("TASKS-AI.md", str(error.exception))
        self.assertIn("错误类型任务", str(error.exception))
        self.assertIn("invalid-kind", str(error.exception))
        self.assertNotEqual(page.project_name.text(), "读取中…")
        path.write_text(invalid.replace("| invalid-kind |", "| task |"), encoding="utf-8")
        page.refresh()
        self.settle(window)
        from selfManger.project_task_documents import import_legacy_project_task_documents
        import_legacy_project_task_documents(window.store, workspace, project)
        self.assertEqual(window.store.planned_tasks(project_id=project, project_root=str(workspace.root))[0]["title"], "错误类型任务")

    def test_empty_legacy_kind_defaults_but_unknown_kind_remains_an_error(self):
        for value in (None, ""):
            self.assertEqual(Store._validate_planned_task({"title": "旧记录", "kind": value})["kind"], "task")
        with self.assertRaises(UserError):
            Store._validate_planned_task({"title": "无效记录", "kind": "invalid-kind"})

    def test_project_refresh_does_not_reimport_legacy_task_rows(self):
        window, page, workspace, project = self.seed()
        other = workspace.create("另一个项目", "隔离切换", False, TaskContext())["project_id"]
        task = window.store.add_planned_task({"title": "旧AI类型任务", "source": "ai", "project_id": project,
                                              "project_root": str(workspace.root)})
        path = workspace.path(workspace.project(project)["path"] + "/agent/TASKS-AI.md")
        path.write_text(_render("ai", [task]).replace("| task |", "| AI规划 |"), encoding="utf-8")
        with patch.object(QMessageBox, "warning") as warning:
            page.refresh()
            self.settle(window)
            for identity in (other, project, other, project):
                index = next(i for i, row in enumerate(page.rows) if row["id"] == identity)
                page.table.selectRow(index)
                self.settle(window)
            warning.assert_not_called()
        self.assertEqual(window.store.planned_task(task["id"])["source"], "ai")
        self.assertEqual(window.store.planned_task(task["id"])["kind"], "task")
        self.assertIn("旧AI类型任务", self.select_task(page, task["id"], "ai").text())
        self.assertIn("| AI规划 |", path.read_text(encoding="utf-8"))
        self.assertEqual(window.store.planned_task(task["id"])["title"], "旧AI类型任务")

    def test_repeat_occurrence_can_be_undone_without_changing_start_date(self):
        from datetime import date, timedelta
        window, page, workspace, project = self.seed()
        center = window.planning_page.service
        start = (date.today() - timedelta(days=2)).isoformat()
        task = center.save_action({"title": "每日重复", "project_id": project, "project_root": str(workspace.root),
                                   "due_date": start, "repeat": "daily"})
        center.complete(task["id"], True, date.today().isoformat())
        page.refresh()
        self.settle(window)
        row = self.select_task(page, task["id"]).data(Qt.ItemDataRole.UserRole)
        self.assertTrue(row["done"])
        self.assertEqual(row["due_date"], start)
        def undo(dialog):
            dialog.combo.setCurrentIndex(dialog.combo.findData("planned"))
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(ProjectTaskStatusDialog, "exec", undo):
            page.edit_shared_task_status("manual")
        self.assertFalse(center.actions("today")[0]["done"])
        self.assertEqual(window.store.planned_task(task["id"])["due_date"], start)

    def test_status_failure_preserves_dialog_and_cancel_delete_keeps_record(self):
        window, page, workspace, project = self.seed()
        task = window.planning_page.service.save_action({"title": "失败保留", "project_id": project,
                                                        "project_root": str(workspace.root)})
        page.refresh()
        self.settle(window)
        self.select_task(page, task["id"])
        def fail(dialog):
            dialog.combo.setCurrentIndex(dialog.combo.findData("done"))
            dialog.save.click()
            self.settle(window)
            self.assertTrue(dialog.isEnabled())
            self.assertFalse(dialog.error.isHidden())
            self.assertEqual(dialog.combo.currentData(), "done")
            dialog.reject()
            return dialog.result()
        with patch.object(ProjectTaskStatusDialog, "exec", fail), patch("selfManger.action_center.ActionCenter.set_project_task_status", side_effect=UserError("模拟保存失败")):
            page.edit_shared_task_status("manual")
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            page.delete_shared_task("manual")
        self.assertIsNotNone(window.store.planned_task(task["id"]))

    def test_task_editor_failure_keeps_draft_and_retry_updates_same_identity(self):
        window, page, workspace, project = self.seed()
        task = window.store.add_planned_task({"title": "编辑原文", "source": "ai", "project_id": project,
                                              "project_root": str(workspace.root)})
        page.refresh()
        self.settle(window)
        self.select_task(page, task["id"], "ai")
        original = window.store.update_planned_task
        calls = []
        def fail_once(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise OSError("模拟写入失败")
            return original(*args, **kwargs)
        def edit(dialog):
            dialog.title.setText("保留草稿")
            dialog.confirm()
            self.settle(window)
            self.assertEqual(dialog.title.text(), "保留草稿")
            self.assertTrue(dialog.isEnabled())
            self.assertFalse(dialog.save_error.isHidden())
            self.assertEqual(window.store.planned_task(task["id"])["title"], "编辑原文")
            dialog.confirm()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(UnifiedTaskDialog, "exec", edit), patch.object(window.store, "update_planned_task", side_effect=fail_once):
            page.edit_shared_task("ai")
        self.assertEqual(window.store.planned_task(task["id"])["title"], "保留草稿")
        self.assertEqual(len(window.store.planned_tasks()), 1)

    def test_stale_task_rows_cannot_mutate_previous_project_after_switch(self):
        window, page, workspace, project = self.seed()
        task = window.store.add_planned_task({"title": "上一项目任务", "source": "ai", "project_id": project,
                                              "project_root": str(workspace.root)})
        page.refresh()
        self.settle(window)
        self.select_task(page, task["id"], "ai")
        with patch.object(page, "selected", return_value={"id": "another-project", "state": "active"}):
            self.assertIsNone(page.selected_shared_task("ai"))
            with patch.object(UnifiedTaskDialog, "exec") as opened, patch.object(window.store, "set_planned_task_status") as saved:
                page.edit_shared_task("ai")
                page.toggle_shared_task(task["id"], True)
                opened.assert_not_called()
                saved.assert_not_called()
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")

    def test_new_project_opens_without_warning_and_four_steps_work(self):
        window, page, workspace, project = self.seed()
        with patch.object(QMessageBox, "warning") as warning:
            dialog = ProjectDialog(page)
            dialog.name.setText("测试新项目")
            dialog.folder_name.setText("test-new-project")
            dialog.goal.setPlainText("仅验证，不创建真实项目")
            for _ in range(3):
                dialog.next_step()
            self.assertEqual(dialog.initialization_panel.currentIndex(), 3)
            dialog.confirm()
            self.assertEqual(dialog.result(), 1)
            warning.assert_not_called()
            dialog.deleteLater()

    def test_unreadable_project_git_scan_does_not_look_like_no_repository(self):
        root = self.root / "git-scan"
        root.mkdir(parents=True)

        def inaccessible(path, onerror):
            onerror(PermissionError("synthetic inaccessible child"))
            return iter(())

        with patch("selfManger.ui.projects.os.walk", side_effect=inaccessible):
            self.assertIsNone(projects.project_git_repositories(root))


if __name__ == "__main__":
    unittest.main()
