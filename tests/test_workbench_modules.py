"""Workbench routing and template round trips against isolated records."""
import unittest
from tests.support import evidence_root, start_visual_timing, visual
from datetime import date, timedelta
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from tests import test_plan_modules as fixture
from selfManger.ui.action_page import QUICK_TEMPLATES
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.today_widgets import ROW_DATA
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.theme import setup_theme, apply_theme
from selfManger.workbench import work_root
import os


class WorkbenchModuleTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window

    def test_workbench_open_daily_keeps_selected_day(self):
        window = self.make_window()
        today, page = window.today_page, window.planning_page
        selected = date.today() - timedelta(days=9)
        today.set_day(selected)
        self.settle(window)
        today.open_daily()
        self.assertEqual((page.view, page.day), ("today", selected))

    def test_add_from_workbench_saves_sqlite_and_shared_checkbox(self):
        window = self.make_window()
        today, page = window.today_page, window.planning_page
        selected = date.today() - timedelta(days=2)
        today.set_day(selected)
        self.settle(window)
        def enter(dialog):
            self.assertEqual(dialog.kind, "daily")
            self.assertEqual(dialog.body.toPlainText(), QUICK_TEMPLATES["daily"][0])
            dialog.title.setText("工作台安排")
            dialog.save.click()
            self.settle(window)
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter):
            today.add_current_entry()
        self.settle(window)
        record = window.store.planned_tasks()[0]
        self.assertFalse((work_root(window.store) / "每日").exists())
        self.assertIsNone(page.service.documents.path("action", record["id"]))
        self.assertEqual(record["due_date"], selected.isoformat())
        item = today.tasks.item(0)
        self.assertEqual(item.data(ROW_DATA)["planned_task_id"], record["id"])
        item.setCheckState(Qt.CheckState.Checked)
        self.settle(window)
        self.assertEqual(window.store.planned_task(record["id"])["status"], "done")
        self.assertEqual(window.store.planned_task(record["id"])["status"], "done")
        self.assertFalse((work_root(window.store) / "每日").exists())

    def test_monthly_reminder_opens_exact_period_and_mode(self):
        window = self.make_window()
        item = QListWidgetItem("月复盘")
        item.setData(ROW_DATA, {"kind": "review", "day": "2026-09-30", "period_key": "monthly:2026-09"})
        window.today_page.open_attention(item)
        self.settle(window)
        page = window.planning_page
        self.assertEqual(page.view, "review")
        self.assertEqual(page.review_overview.mode, "monthly")
        self.assertEqual(page.review_targets()[1][2], "monthly:2026-09")

    def test_due_plan_opens_even_before_plan_page_loaded(self):
        window = self.make_window()
        page = window.planning_page
        record = page.service.save_plan({"title": "刚创建的到期计划", "due_date": date.today().isoformat()})
        page.plans = []
        with patch.object(page, "open_editor") as opened:
            page.open_plan_editor(record["id"])
        self.assertEqual(opened.call_args.kwargs["record"]["id"], record["id"])

    def test_templates_do_not_contain_actionable_example_tasks(self):
        for kind, (body, title) in QUICK_TEMPLATES.items():
            with self.subTest(kind=kind):
                self.assertTrue(body.startswith("# "))
                self.assertNotIn("- [ ]", body)
                self.assertIn(body.splitlines()[0][2:], title)

    def test_all_six_templates_save_and_read_back_from_sqlite(self):
        window = self.make_window()
        page = window.planning_page
        workspace = ProjectWorkspace(self.root / "projects")
        workspace.initialize(TaskContext())
        project = workspace.create("模板验证", "隔离", False, TaskContext())
        window.store.set_setting("project_workspace", str(workspace.root))
        page.refresh()
        self.settle(window)
        for kind, (body, _) in QUICK_TEMPLATES.items():
            with self.subTest(kind=kind):
                def enter(dialog):
                    dialog.title.setText("模板验证-" + kind)
                    if kind == "project":
                        dialog.project.setCurrentIndex(dialog.project.findData(project["project_id"]))
                    self.assertEqual(dialog.body.toPlainText(), body)
                    dialog.save.click()
                    self.settle(window)
                    self.assertEqual(dialog.result(), 1, dialog.error.text())
                    return dialog.result()
                with patch.object(PlanDocumentDialog, "exec", enter):
                    page.open_document_dialog(kind)
                if kind.endswith("review"):
                    entity, records = "review", page.service.reviews()
                elif kind in {"project", "longterm"}:
                    entity, records = "plan", page.service.plans()
                else:
                    entity, records = "action", window.store.planned_tasks()
                record = next(row for row in records if row["title"] == "模板验证-" + kind)
                self.assertIsNone(page.service.documents.path(entity, record["id"]))
                self.assertEqual(record.get("body", record.get("notes", "")).strip(), body.strip())

    def test_workbench_focus_attention_and_rhythm_follow_shared_records(self):
        window = self.make_window()
        page = window.today_page
        service = window.planning_page.service
        today = date.today()
        service.save_action({"title": "补交逾期行动", "due_date": (today - timedelta(days=2)).isoformat()})
        service.save_action({"title": "今天要做的行动", "due_date": today.isoformat()})
        service.save_plan({"title": "到期计划", "due_date": today.isoformat()})
        page.manual_refresh()
        self.settle(window)
        self.assertEqual(page.focus.fraction.text(), "0 / 2")
        self.assertEqual(page.focus.next_title.text(), "补交逾期行动")
        self.assertEqual(page.group_lists["overdue"].count(), 1)
        self.assertEqual(page.group_lists["today"].count(), 1)
        self.assertTrue(page.group_blocks["done"].isHidden())
        titles = [page.attention.item(index).data(ROW_DATA)["title"]
                  for index in range(page.attention.count())]
        self.assertIn("逾期与遗留行动", titles)
        self.assertIn("到期或逾期的计划", titles)
        self.assertEqual(page.focus.fraction.text(), "0 / 2")
        self.assertEqual(len(page.rhythm), 7)
        self.assertTrue(any(day["today"] for day in page.rhythm))
        # The overdue action can belong to the preceding calendar week.
        days = {entry["day"] for entry in page.rhythm}
        expected = sum(value.isoformat() in days for value in (today, today - timedelta(days=2)))
        self.assertEqual(sum(day["planned"] for day in page.rhythm), expected)
        item = next(page.attention.item(index) for index in range(page.attention.count())
                    if page.attention.item(index).data(ROW_DATA)["title"] == "逾期与遗留行动")
        page.open_attention(item)
        self.settle(window)
        self.assertEqual((window.navigation.currentRow(), window.planning_page.view), (window.PLAN, "today"))
        self.assertEqual(window.planning_page.day, today)

    def test_workbench_cards_stack_without_dropping_content(self):
        window = self.make_window()
        page = window.today_page
        window.resize(960, 700)
        self.app.processEvents()
        page.arrange_columns()
        self.app.processEvents()
        stacked = (page.task_card, page.attention_card, page.week_card, page.project_card)
        self.assertEqual([page.columns_layout.getItemPosition(page.columns_layout.indexOf(widget))[:2]
                          for widget in stacked], [(0, 0), (1, 0), (2, 0), (3, 0)])
        self.assertTrue(page.open_daily_button.isHidden())
        window.resize(1280, 820)
        self.app.processEvents()
        page.arrange_columns()
        self.app.processEvents()
        self.assertEqual([page.columns_layout.getItemPosition(page.columns_layout.indexOf(widget))[:2]
                          for widget in (page.left_column, page.right_column)],
                         [(0, 0), (0, 7)])
        self.assertIs(page.left_layout.itemAt(0).widget(), page.task_card)
        self.assertIs(page.left_layout.itemAt(1).widget(), page.week_card)
        self.assertIs(page.right_layout.itemAt(0).widget(), page.attention_card)
        self.assertIs(page.right_layout.itemAt(1).widget(), page.project_card)
        self.assertTrue(page.open_daily_button.isHidden())
        self.assertFalse(page.open_daily_button.isWindow())
        self.assertIs(page.open_daily_button.parentWidget(), page)
        for card in stacked:
            self.assertGreaterEqual(page.body.minimumHeight(), card.sizeHint().height() - 1)

    @visual
    def test_workbench_theme_and_size_previews(self):
        timer = start_visual_timing(self)
        window = self.make_window()
        page = window.planning_page
        page.service.save_action({"title": "整理今日计划", "due_date": date.today().isoformat()})
        page.service.save_plan({"title": "完成阶段验收", "due_date": date.today().isoformat()})
        window.today_page.refresh()
        timer.run("settle", self.settle, window)
        setup_theme(self.app)
        evidence = evidence_root()
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for width, height in ((1440, 960), (960, 720)):
                window.resize(width, height)
                timer.run("processEvents", self.app.processEvents)
                window.today_page.arrange_columns()
                timer.run("processEvents", self.app.processEvents)
                timer.capture(window, evidence / f"workbench-{theme}-{width}.png")
