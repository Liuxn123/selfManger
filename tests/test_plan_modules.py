"""Acceptance coverage for the six peer planning modules and shared records."""
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from tests.support import cleanup_ui_test, evidence_root, start_visual_timing, visual
from tests import test_action_center as fixture
from selfManger.domain import UserError
from selfManger.runtime import TaskContext
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.ui.theme import setup_theme, apply_theme
from selfManger.ui.plan_dialog import PlanDocumentDialog


class PlanModuleTests(unittest.TestCase):
    setUpClass = classmethod(fixture.ActionPageTests.setUpClass.__func__)
    make_window = fixture.ActionPageTests.make_window
    until_idle = fixture.ActionPageTests.until_idle
    settle = fixture.ActionPageTests.settle

    def setUp(self):
        fixture.ActionPageTests.setUp(self)
        for method in ("warning", "information"):
            mock = patch("selfManger.ui.action_page.QMessageBox." + method)
            mock.start()
            self.addCleanup(mock.stop)

    def close_window(self, window):
        cleanup_ui_test(self, self.app)

    def open_page(self):
        window = self.make_window()
        window.navigation.setCurrentRow(window.PLAN)
        self.settle(window)
        return window, window.planning_page

    def save_composer(self, page, title, body="## 目标\n保存的正文"):
        page.quick_create.title_input.setText(title)
        page.quick_create.body.setPlainText(body)
        page.quick_create.save_button.click()
        self.settle(page.window)

    def save_modal(self, page, trigger, title, *, setup=None):
        view, day, query = page.view, page.day, page.search.text()
        def enter(dialog):
            self.assertEqual((page.view, page.day, page.search.text()), (view, day, query))
            if setup:
                setup(dialog)
            dialog.title.setText(title)
            dialog.body.setPlainText("## 目标\n保存的正文")
            dialog.save.click()
            self.settle(page.window)
            self.assertEqual(dialog.result(), 1, dialog.error.text())
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter):
            trigger()
        self.settle(page.window)
        self.assertEqual((page.view, page.day, page.search.text()), (view, day, query))

    def test_six_peer_entries_and_category_creation_returns_to_origin(self):
        window, page = self.open_page()
        self.assertEqual([title for title, _, _ in page.VIEWS],
                         ["快速新建", "每日计划", "长期计划", "周期计划", "周月复盘", "项目计划"])
        self.assertEqual(page.view, "quick-create")
        for view, kind in (("today", "daily"), ("plans", "longterm"), ("recurring", "recurring")):
            page.select_view(view)
            self.settle(window)
            if view in {"plans", "recurring"}:
                self.assertTrue(page.longterm_create_button.isVisible())
                self.assertTrue(page.recurring_create_button.isVisible())
                self.assertFalse(page.add_button.isVisible())
                self.assertFalse(page.empty_create.isVisible())
                create_button = page.longterm_create_button if kind == "longterm" else page.recurring_create_button
            else:
                create_button = page.add_button
            def check(dialog):
                self.assertEqual(dialog.kind, kind)
                self.assertTrue(dialog.body.toPlainText())
                if kind == "longterm":
                    self.assertFalse(dialog.has_date.isChecked())
            self.save_modal(page, create_button.click, "新建" + kind, setup=check)
            self.assertEqual(page.view, view)
        self.assertEqual(len(page.service.plans()), 1)
        self.assertEqual(len(window.store.planned_tasks()), 2)

    def test_longterm_and_recurring_create_buttons_share_both_planning_views(self):
        window, page = self.open_page()
        for view in ("plans", "recurring"):
            page.select_view(view)
            self.settle(window)
            self.assertEqual(page.longterm_create_button.text(), "＋ 新建长期计划")
            self.assertEqual(page.recurring_create_button.text(), "＋ 新建周期计划")
            self.assertTrue(page.longterm_create_button.isVisible())
            self.assertTrue(page.recurring_create_button.isVisible())
            with patch.object(page, "open_document_dialog") as open_dialog:
                page.longterm_create_button.click()
                page.recurring_create_button.click()
            self.assertEqual([call.args[0] for call in open_dialog.call_args_list], ["longterm", "recurring"])

    def test_history_edit_and_workbench_checkbox_use_same_sqlite_identity(self):
        window, page = self.open_page()
        yesterday = date.today() - timedelta(days=1)
        page.select_view("today")
        page.set_day(yesterday)
        self.settle(window)
        self.save_modal(page, page.add_button.click, "昨日安排")
        task = window.store.planned_tasks()[0]
        self.assertIsNone(page.service.documents.path("action", task["id"]))
        self.assertEqual(task["due_date"], yesterday.isoformat())
        entry = next(page.list.item(i) for i in range(page.list.count())
                     if page.list.item(i).data(Qt.ItemDataRole.UserRole))
        page.open_item(entry)
        page.editor.notes.setPlainText("在每日页面修改")
        page.editor.save.click()
        self.settle(window)
        self.assertEqual(window.store.planned_task(task["id"])["notes"], "在每日页面修改")
        page.refresh()
        self.settle(window)
        self.assertEqual(window.store.planned_task(task["id"])["notes"], "在每日页面修改")
        page.service.complete(task["id"], True)
        page.refresh()
        self.settle(window)
        self.assertTrue(page.rows[0]["done"])

    def test_existing_plan_composer_edits_original_instead_of_creating_duplicate(self):
        window, page = self.open_page()
        page.begin_quick_create("longterm")
        self.save_composer(page, "原长期计划")
        original = page.service.plans()[0]
        page.quick_create.load_document("longterm", original)
        self.save_composer(page, "改名后的长期计划")
        plans = page.service.plans()
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["id"], original["id"])
        self.assertEqual(plans[0]["title"], "改名后的长期计划")
        self.assertIsNone(plans[0]["due_date"])

    def test_project_plan_is_shared_with_longterm_and_can_schedule_daily_action(self):
        window, page = self.open_page()
        workspace = ProjectWorkspace(self.root / "projects")
        context = TaskContext()
        workspace.initialize(context)
        project = workspace.create("虚构项目", "测试", False, context)
        window.store.set_setting("project_workspace", str(workspace.root))
        page.select_view("project-plans")
        self.settle(window)
        self.save_modal(page, page.add_button.click, "项目阶段安排", setup=lambda dialog:
            dialog.project.setCurrentIndex(dialog.project.findData(project["project_id"])))
        plan = page.service.plans()[0]
        self.assertEqual(page.view, "project-plans")
        self.assertEqual(page.list.count(), 1)
        page.select_view("plans")
        self.settle(window)
        self.assertEqual(page.list.count(), 1)
        self.assertEqual(page.list.item(0).data(Qt.ItemDataRole.UserRole)["plan"]["id"], plan["id"])
        self.save_modal(page, lambda: page.schedule_plan(plan), "执行项目阶段安排")
        task = window.store.planned_tasks()[0]
        self.assertEqual(task["goal_id"], plan["id"])
        self.assertEqual(task["project_id"], project["project_id"])
        self.assertEqual(task["due_date"], date.today().isoformat())
        self.assertEqual(len(page.service.plans()), 1)
        self.assertTrue(any(row.get("planned_task_id") == task["id"] for row in window.today_page.report["tasks"]))

    def test_review_period_occurrences_and_history_edit(self):
        window, page = self.open_page()
        start = date.today() - timedelta(days=3)
        task = page.service.save_action({"title": "每天阅读", "repeat": "daily", "due_date": start.isoformat()})
        page.service.complete(task["id"], True, start.isoformat())
        rows = page.service.period_actions(start, date.today())
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row["done"] for row in rows), 1)
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")
        with self.assertRaises(UserError):
            page.service.complete(task["id"], True, (date.today() + timedelta(days=1)).isoformat())
        page.select_view("review")
        page.set_day(date(2026, 9, 15))
        self.settle(window)
        for kind, expected in (("weekly-review", "weekly:2026-W38"), ("monthly-review", "monthly:2026-09")):
            page.begin_quick_create(kind)
            self.save_composer(page, "历史" + kind)
            self.assertEqual(page.view, "review")
            record = next(row for row in page.service.reviews() if row["period_key"] == expected)
            page.quick_create.load_document("review", record)
            self.save_composer(page, "编辑历史" + kind)
            self.assertEqual(page.view, "review")
        self.assertEqual(len(page.service.reviews()), 2)

    def test_repeat_editor_preserves_frequency_and_new_form_resets_template(self):
        window, page = self.open_page()
        page.begin_quick_create("recurring")
        page.quick_create.frequency.setCurrentIndex(page.quick_create.frequency.findData("monthly"))
        self.save_composer(page, "每月整理")
        record = window.store.planned_tasks()[0]
        page.quick_create.load_document("recurring", record)
        self.assertEqual(page.quick_create.frequency.currentData(), "monthly")
        self.save_composer(page, "每月整理改名")
        self.assertEqual(page.service.rules()[record["id"]]["frequency"], "monthly")
        page.begin_quick_create("recurring")
        self.assertNotIn("保存的正文", page.quick_create.body.toPlainText())

    @visual
    def test_render_all_modules_at_two_sizes_in_light_and_dark(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        timer.run("settle", self.settle, window)
        setup_theme(self.app)
        evidence = evidence_root("test_plan_modules")
        for mode in ("light", "dark"):
            apply_theme(self.app, mode)
            for width, height in ((1536, 960), (1024, 760)):
                window.resize(width, height)
                for _, view, _ in page.VIEWS:
                    page.select_view(view)
                    timer.run("settle", self.settle, window)
                    self.assertTrue(page.navigation.isVisible())
                    self.assertTrue(page.main.isVisible())
                    if view == "quick-create":
                        self.assertGreaterEqual(page.quick_create.kind.width(), 120)
                        self.assertGreaterEqual(page.quick_create.date.width(), 125)
                    timer.capture(window, evidence / f"{view}-{mode}-{width}.png")

    def test_unsaved_markdown_is_retained_when_replacement_is_cancelled(self):
        window, page = self.open_page()
        page.quick_create.title_input.setText("保留我的草稿")
        page.quick_create.body.setPlainText("尚未保存的正文")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(page.begin_quick_create("longterm"))
            self.assertFalse(page.confirm_close(include_quick=True))
        self.assertEqual(page.quick_create.body.toPlainText(), "尚未保存的正文")
        page.quick_create.kind.setCurrentIndex(page.quick_create.kind.findData("longterm"))
        self.assertEqual(page.quick_create.body.toPlainText(), "尚未保存的正文")
        page.quick_create.mark_clean()  # The test window may now close without a modal prompt.

    def test_weekend_and_month_end_reminders_stop_after_reviews_are_saved(self):
        window, page = self.open_page()
        page.set_day(date(2026, 5, 31))  # Sunday and the last day of May.
        self.settle(window)
        text = page.review_reminder_text()
        self.assertIn("本周最后一天", text)
        self.assertIn("本月最后一天", text)
        for period_type, target in zip(("weekly", "monthly"), page.review_targets()):
            start, end, key = target
            page.service.save_review({"title": "已复盘" + period_type, "period_type": period_type,
                                      "period_key": key, "start_date": start.isoformat(),
                                      "end_date": end.isoformat(), "body": "记录"})
        page.reviews = page.service.reviews()
        self.assertEqual(page.review_reminder_text(), "")
