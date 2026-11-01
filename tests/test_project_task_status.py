"""Inline manual project task status updates keep one shared record and document."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton
from tests.support import evidence_root, start_visual_timing, visual

from tests import test_action_center as domain_fixture
from tests import test_project_plan_filter as ui_fixture
from selfManger.domain import UserError
from selfManger.project_task_documents import import_legacy_project_task_documents
from selfManger.runtime import TaskContext
from selfManger.ui.theme import setup_theme, apply_theme
from selfManger.ui.plan_module_widgets import ProjectTaskStatusDialog
from tests.personal_markdown_fixtures import attach_legacy_document


class ProjectStatusTests(unittest.TestCase):
    setUp = domain_fixture.ActionCenterTests.setUp

    def test_manual_status_reopens_and_rejects_ai_stale_and_deleted(self):
        row = self.store.add_planned_task({"title": "项目文档手动任务"}, "project-doc:manual")
        for status in ("active", "done", "planned"):
            result = self.center.set_project_task_status(row["id"], status, expected=row)
            self.assertEqual(result["status"], status)
            row = result
        stale = dict(row)
        self.center.set_project_task_status(row["id"], "active")
        with self.assertRaises(UserError):
            self.center.set_project_task_status(row["id"], "done", expected=stale)
        ai = self.store.add_planned_task({"title": "只在项目管理维护的 AI", "source": "ai"})
        with self.assertRaises(UserError):
            self.center.set_project_task_status(ai["id"], "done")
        self.center.delete_record("action", row["id"])
        with self.assertRaises(UserError):
            self.center.set_project_task_status(row["id"], "done")

    def test_personal_action_status_is_sqlite_only_after_legacy_migration(self):
        row = self.center.save_action({"title": "个人项目行动"})
        path = attach_legacy_document(self.store, "action", row["id"], "daily", row)
        self.center.documents.migrate_legacy_documents()
        text = path.read_text(encoding="utf-8") + "\n外部新增内容"
        path.write_text(text, encoding="utf-8")
        self.center.set_project_task_status(row["id"], "done", expected=row)
        self.assertTrue(self.center.actions()[0]["done"])
        self.center.set_project_task_status(row["id"], "planned")
        self.assertEqual(self.store.planned_task(row["id"])["status"], "planned")
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_recurring_status_only_changes_one_day(self):
        day = date.today()
        row = self.center.save_action({"title": "项目每日检查", "repeat": "daily", "due_date": day.isoformat()})
        self.center.set_project_task_status(row["id"], "done", expected=row, day=day.isoformat())
        self.assertTrue(self.center.actions("today", day=day)[0]["done"])
        self.assertFalse(self.center.actions("today", day=day+timedelta(days=1))[0]["done"])
        self.assertEqual(self.store.planned_task(row["id"])["status"], "planned")
        self.center.set_project_task_status(row["id"], "planned", day=day.isoformat())
        self.assertFalse(self.center.actions("today", day=day)[0]["done"])
        with self.assertRaises(UserError):
            self.center.set_project_task_status(row["id"], "done", day=(day+timedelta(days=1)).isoformat())


class ProjectStatusUITests(unittest.TestCase):
    setUpClass = classmethod(ui_fixture.ProjectPlanFilterTests.setUpClass.__func__)
    setUp = ui_fixture.ProjectPlanFilterTests.setUp
    make_window = ui_fixture.ProjectPlanFilterTests.make_window
    until_idle = ui_fixture.ProjectPlanFilterTests.until_idle
    settle = ui_fixture.ProjectPlanFilterTests.settle
    close_window = ui_fixture.ProjectPlanFilterTests.close_window
    open_page = ui_fixture.ProjectPlanFilterTests.open_page
    workspace = ui_fixture.ProjectPlanFilterTests.workspace

    def seed(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        import_legacy_project_task_documents(window.store, workspace, projects[0], TaskContext())
        task = window.store.add_planned_task({"title": "明确目标与验收条件", "source": "manual", "project_id": projects[0],
            "project_root": str(workspace.root)}, "project-doc:status")
        page.select_view("project-plans")
        self.settle(window)
        page.project_filter.setCurrentIndex(page.project_filter.findData(projects[0]))
        return window, page, workspace, projects[0], task

    def card(self, page):
        return page.project_records_list.itemWidget(page.project_records_list.item(0))

    def badge(self, page):
        return self.card(page).findChild(QLabel, "ProjectTaskStatusBadge")

    def test_visible_status_updates_stats_project_document_and_reopens(self):
        window, page, workspace, project, task = self.seed()
        for status in ("active", "done", "planned"):
            self.assertIsNone(self.card(page).findChild(QComboBox))
            before = window.store.planned_task(task["id"])
            def edit(dialog):
                dialog.combo.setCurrentIndex(dialog.combo.findData(status))
                self.assertEqual(window.store.planned_task(task["id"]), before)
                dialog.save.click()
                self.assertTrue(dialog.busy)
                dialog.reject()
                self.assertEqual(dialog.result(), 0)
                self.settle(window)
                self.assertEqual(dialog.result(), 1)
                return dialog.result()
            with patch.object(ProjectTaskStatusDialog, "exec", edit):
                self.card(page).findChild(QPushButton, "EditProjectTaskStatus").click()
            self.settle(window)
            self.assertEqual(window.store.planned_task(task["id"])["status"], status)
            self.assertEqual(self.badge(page).property("status"), status)
            self.assertEqual(page.project_filter.currentData(), project)
            self.assertEqual(page.module_summary.values[2][1].text(), "1" if status == "done" else "0")
            import_legacy_project_task_documents(window.store, workspace, project, TaskContext())
            self.assertEqual(window.store.planned_task(task["id"])["status"], status)
            page.refresh()
            self.settle(window)
        self.assertEqual(len(window.store.planned_tasks()), 1)

    def test_failed_status_save_restores_visible_value_and_keeps_filter(self):
        window, page, workspace, project, task = self.seed()
        row = page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole)
        dialog = ProjectTaskStatusDialog(page, row)
        dialog.show()
        with patch.object(page.service, "set_project_task_status", side_effect=UserError("模拟状态保存失败")):
            dialog.combo.setCurrentIndex(dialog.combo.findData("done"))
            dialog.save.click()
            self.settle(window)
        self.assertEqual(self.badge(page).property("status"), "planned")
        self.assertTrue(dialog.isEnabled())
        self.assertTrue(dialog.error.isVisible())
        self.assertEqual(dialog.combo.currentData(), "done")
        self.assertEqual(page.project_filter.currentData(), project)
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")
        dialog.reject()
        dialog.deleteLater()

    def test_status_is_readonly_until_edit_and_cancel_never_writes(self):
        window, page, workspace, project, task = self.seed()
        self.assertIsNone(self.card(page).findChild(QComboBox))
        self.assertEqual(self.badge(page).property("status"), "planned")
        def cancel(dialog):
            dialog.combo.setCurrentIndex(dialog.combo.findData("done"))
            dialog.reject()
            return dialog.result()
        with patch.object(ProjectTaskStatusDialog, "exec", cancel), patch.object(page.service, "set_project_task_status") as save:
            self.card(page).findChild(QPushButton, "EditProjectTaskStatus").click()
            self.settle(window)
            save.assert_not_called()
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")
        self.assertEqual(self.badge(page).property("status"), "planned")

    @visual
    def test_status_dropdown_visuals(self):
        timer = start_visual_timing(self)
        window, page, workspace, project, task = self.seed()
        page.service.save_action({"title": "计划和项目模块测试及功能完善", "project_id": project, "project_root": str(workspace.root), "status": "active"})
        page.service.save_action({"title": "创建私有 Git 仓库并推送阶段成果", "project_id": project, "project_root": str(workspace.root), "status": "done"})
        page.refresh()
        self.settle(window)
        setup_theme(self.app)
        output = evidence_root("test_project_task_status")
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for width in (1400, 1024):
                window.resize(width, 800)
                timer.run("processEvents", self.app.processEvents)
                self.assertTrue(timer.capture(page, output / f"{theme}-{width}.png"))
            row = page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole)
            with timer.measure("construction"):
                dialog = ProjectTaskStatusDialog(page, row)
            dialog.show()
            timer.run("processEvents", self.app.processEvents)
            self.assertTrue(timer.capture(dialog, output / f"{theme}-edit.png"))
            dialog.reject()
            dialog.deleteLater()
