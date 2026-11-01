"""Category-local modal behavior, readable rows, and isolated visual acceptance."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QMessageBox

from tests.support import evidence_root, start_visual_timing, visual
from tests import test_plan_modules as fixture
from selfManger.domain import UserError
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.theme import setup_theme, apply_theme


class PlanSubpageTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window
    open_page = fixture.PlanModuleTests.open_page
    save_modal = fixture.PlanModuleTests.save_modal

    def test_cancelled_dialog_keeps_classification_date_filters_and_quick_draft(self):
        window, page = self.open_page()
        page.quick_create.body.setPlainText("# 独立保留的快速新建草稿\n\n不能被分类弹窗覆盖")
        draft = page.quick_create.snapshot()
        page.select_view("today")
        page.set_day(date.today() - timedelta(days=2))
        page.search.setText("保留筛选")
        self.settle(window)
        def enter(dialog):
            self.assertEqual(page.view, "today")
            dialog.title.setText("取消的新计划")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                dialog.reject()
                self.assertEqual(dialog.title.text(), "取消的新计划")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                dialog.reject()
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter):
            page.add_button.click()
        self.assertEqual(page.quick_create.snapshot(), draft)
        self.assertEqual((page.view, page.day, page.search.text()), ("today", date.today()-timedelta(days=2), "保留筛选"))
        self.assertEqual(window.store.planned_tasks(), [])
        page.quick_create.mark_clean()

    def test_failed_modal_save_keeps_content_and_can_retry(self):
        window, page = self.open_page()
        page.select_view("plans")
        self.settle(window)
        def enter(dialog):
            dialog.title.setText("失败后重试")
            dialog.body.setPlainText("# 原文草稿\n\n保存失败不能丢失")
            with patch.object(page.service, "save_plan", side_effect=UserError("隔离失败")):
                dialog.save.click()
                self.settle(window)
            self.assertEqual(dialog.result(), 0)
            self.assertTrue(dialog.isEnabled())
            self.assertIn("保存失败不能丢失", dialog.body.toPlainText())
            self.assertTrue(dialog.error.text())
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter):
            page.add_button.click()
        self.assertEqual(page.view, "plans")
        self.assertEqual(len(page.service.plans()), 1)

    def test_week_month_dialogs_edit_existing_period_without_duplicate(self):
        window, page = self.open_page()
        page.select_view("review")
        self.settle(window)
        self.save_modal(page, page.add_button.click, "周总结")
        identity = page.service.reviews()[0]["id"]
        self.save_modal(page, page.add_button.click, "继续编辑周总结")
        self.assertEqual(page.service.reviews()[0]["id"], identity)
        self.save_modal(page, page.add_month_review_button.click, "月总结")
        self.assertEqual(len(page.service.reviews()), 2)
        page.review_overview.select_mode("monthly")
        self.assertEqual(page.review_overview.mode, "monthly")
        self.assertEqual(page.view, "review")

    def test_plan_body_is_saved_in_sqlite_without_creating_a_markdown_file(self):
        window, page = self.open_page()
        page.select_view("plans")
        self.settle(window)
        def enter(dialog):
            dialog.title.setText("SQLite 计划正文")
            dialog.body.setPlainText("# SQLite 计划正文\n\n正文可保留并恢复")
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            records = page.service.plans()
            self.assertEqual(len(records), 1)
            identity = records[0]["id"]
            self.assertIsNone(page.service.documents.path("plan", identity))
            self.assertEqual(page.service.plans()[0]["notes"], "正文可保留并恢复")
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter):
            page.add_button.click()

    def test_visible_row_checkbox_updates_shared_action_and_repeat_occurrence(self):
        window, page = self.open_page()
        today = date.today()
        task = page.service.save_action({"title": "从新事项行打卡", "due_date": today.isoformat(), "repeat": "daily"})
        page.select_view("recurring")
        self.settle(window)
        entry = next(page.list.item(index) for index in range(page.list.count()) if
                     (page.list.item(index).data(Qt.ItemDataRole.UserRole) or {}).get("id") == task["id"])
        check = page.list.itemWidget(entry).findChild(QCheckBox)
        self.assertTrue(check.isVisible())
        check.click()
        self.settle(window)
        self.assertTrue(page.service.actions("today")[0]["done"])
        self.assertFalse(page.service.actions("today", day=today+timedelta(days=1))[0]["done"])
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")

    @visual
    def test_populated_subpages_and_modals_at_two_sizes_and_themes(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        timer.run("settle", self.settle, window)
        setup_theme(self.app)
        workspace = ProjectWorkspace(self.root / "project-workspace")
        context = TaskContext()
        workspace.initialize(context)
        project = workspace.create("示例项目", "隔离界面验收", False, context)
        window.store.set_setting("project_workspace", str(workspace.root))
        today = date.today()
        longterm = page.service.save_plan({"title": "完成个人学习计划"})
        project_plan = page.service.save_plan({"title": "完成项目第一阶段", "due_date": (today+timedelta(days=14)).isoformat(),
            "project_id": project["project_id"], "project_root": str(workspace.root)})
        for index in range(11):
            task = page.service.save_action({"title": f"梳理阶段行动 {index+1}", "due_date": (today-timedelta(days=index % 4)).isoformat(),
                "goal_id": longterm["id"] if index % 2 else project_plan["id"],
                "project_id": project["project_id"] if index % 2 == 0 else "",
                "project_root": str(workspace.root) if index % 2 == 0 else ""})
            if index % 3:
                page.service.complete(task["id"], True)
        page.service.save_action({"title": "每日阅读与记录", "repeat": "daily", "due_date": (today-timedelta(days=5)).isoformat()})
        page.service.save_action({"title": "每周整理计划", "repeat": "weekly", "due_date": today.isoformat()})
        page.select_view("review")
        self.settle(window)
        start, end, key = page.review_targets()[0]
        page.service.save_review({"period_type": "weekly", "period_key": key, "start_date": start.isoformat(),
            "end_date": end.isoformat(), "title": "本周进展与下一步", "body": "## 收获\n## 下一步"})
        evidence = evidence_root("test_plan_subpages")
        for mode in ("light", "dark"):
            apply_theme(self.app, mode)
            for width, height in ((1680, 980), (1024, 760)):
                window.resize(width, height)
                for view in ("today", "plans", "recurring", "review", "project-plans"):
                    page.select_view(view)
                    timer.run("settle", self.settle, window)
                    if view in ("plans", "recurring"):
                        self.assertFalse(page.add_button.isVisible())
                        for entry_button in (page.longterm_create_button, page.recurring_create_button):
                            self.assertTrue(entry_button.isVisible())
                            self.assertTrue(page.rect().contains(entry_button.mapTo(page, entry_button.rect().bottomRight())))
                    else:
                        self.assertTrue(page.add_button.isVisible())
                        self.assertTrue(page.rect().contains(page.add_button.mapTo(page, page.add_button.rect().bottomRight())))
                    timer.capture(page, evidence / f"{view}-{mode}-{width}.png")
            window.resize(1680, 980)
            for kind, view in (("daily", "today"), ("longterm", "plans"), ("recurring", "recurring"),
                               ("weekly-review", "review"), ("monthly-review", "review"), ("project", "project-plans")):
                page.select_view(view)
                timer.run("settle", self.settle, window)
                def capture(dialog):
                    self.assertEqual(page.view, view)
                    dialog.show()
                    timer.run("processEvents", self.app.processEvents)
                    self.assertTrue(dialog.save.isVisible())
                    self.assertTrue(dialog.rect().contains(dialog.save.geometry().bottomRight()))
                    timer.capture(dialog, evidence / f"dialog-{kind}-{mode}.png")
                    dialog.reject()
                    return 0
                with patch.object(PlanDocumentDialog, "exec", capture):
                    page.open_document_dialog(kind)
