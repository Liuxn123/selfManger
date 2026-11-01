"""Interaction regressions for the plan/library workspace redesign."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QDate, Qt, QPoint, QTimer
from PySide6.QtWidgets import QWidget, QCheckBox, QComboBox, QMessageBox, QDialog, QTextEdit, QDialogButtonBox

from tests import test_ui_optimization as fixture
from selfManger.domain import UserError
from selfManger.workbench import work_root
from selfManger.ui.task_dialog import UnifiedTaskDialog
from selfManger.ui.action_page import ItemEditor
from selfManger.ui.plan_dialog import PlanDocumentDialog


class WorkspaceRedesignTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    close_window = fixture.UIOptimizationTests.close_window
    until_idle = fixture.UIOptimizationTests.until_idle

    def settle(self, window):
        deadline = time.monotonic() + 12
        page = window.planning_page
        idle_since = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not window.jobs and window.today_page.worker is None and not page.loading and not page.pending_refresh:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since > .1:
                    return
            else:
                idle_since = None
            time.sleep(.005)
        self.fail("Planning readers did not settle")

    def test_topbar_is_removed_and_sidebar_footer_keeps_actions(self):
        window = self.make_window()
        self.assertIsNone(window.findChild(QWidget, "GlobalTopbar"))
        self.assertTrue(window.sidebar_footer.isAncestorOf(window.global_date))
        self.assertTrue(window.sidebar_footer.isAncestorOf(window.notification_button))
        window.navigation.setCurrentRow(window.LIBRARY)
        window.notification_button.click()
        self.assertEqual(window.navigation.currentRow(), window.TODAY)
        self.assertEqual(window.stack.geometry().top(), 0)

    def test_plan_navigation_date_and_long_term_selection(self):
        window = self.make_window()
        page = window.planning_page
        window.navigation.setCurrentRow(window.PLAN)
        self.assertEqual(page.view, "quick-create")
        future = date.today() + timedelta(days=4)
        plan = page.service.save_plan({"title": "完成学习目标", "due_date": future.isoformat()})
        page.service.save_action({"title": "阅读资料", "goal_id": plan["id"], "due_date": future.isoformat()})
        page.refresh()
        self.settle(window)
        page.select_view("plan", plan["id"])
        self.settle(window)
        self.assertEqual(page.editor.record["title"], "完成学习目标")
        self.assertEqual(page.service.actions(plan_id=plan["id"])[0]["title"], "阅读资料")
        page.select_view("today")
        page.set_day(future)
        self.settle(window)
        self.assertEqual(page.rows[0]["due_date"], future.isoformat())
        page.select_view("today")
        page.set_day(date.today())
        self.settle(window)
        self.assertEqual(page.rows, [])

    def test_daily_checkbox_updates_single_linked_task_and_plan_progress(self):
        window = self.make_window()
        page = window.planning_page
        window.navigation.setCurrentRow(window.PLAN)
        page.select_view("today")
        goal = page.service.save_plan({"title": "长期学习"})
        task = page.service.save_action({"title": "当天行动", "goal_id": goal["id"], "kind": "task", "due_date": date.today().isoformat()})
        page.refresh()
        self.settle(window)
        entry = next(page.list.item(i) for i in range(page.list.count())
                     if (page.list.item(i).data(Qt.ItemDataRole.UserRole) or {}).get("id") == task["id"])
        entry.setCheckState(Qt.CheckState.Checked)
        self.settle(window)
        self.assertEqual(window.store.planned_task(task["id"])["status"], "done")
        self.assertEqual(len(window.store.planned_tasks(goal_id=goal["id"])), 1)
        page.select_view("plan", goal["id"])
        self.settle(window)
        self.assertEqual(page.editor.record["id"], goal["id"])
        self.assertEqual(window.store.planned_task_goal_progress()[goal["id"]], {"total": 1, "done": 1})

    def test_task_optional_fields_and_plan_reference_selectors_preserve_values(self):
        window = self.make_window()
        dialog = UnifiedTaskDialog(window, title="任务", due_date=date.today())
        self.assertFalse(dialog.advanced_fields.isVisible())
        dialog.advanced_button.setChecked(True)
        self.assertFalse(dialog.advanced_fields.isHidden())
        dialog.notes.setPlainText("备注保留")
        self.assertEqual(dialog.values()["notes"], "备注保留")
        dialog.close()
        editor = ItemEditor(window.planning_page, plan=True)
        self.assertIsInstance(editor.project, QComboBox)
        self.assertFalse(editor.advanced.isVisible())
        editor.title.setText("无需期限的计划")
        self.assertIsNone(editor.values()["due_date"])
        editor.close()

    def test_inline_note_keeps_draft_on_conflict_and_saves_in_place(self):
        window = self.make_window()
        window.resize(960, 660)
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        page = window.knowledge_page
        page.search.setText("检索标记")
        page.render_items()
        self.assertFalse(page.grid_view)
        self.assertFalse(hasattr(page, "type_chart"))
        self.assertEqual(page.columns.count(), 2)
        self.assertTrue(page.list_panel.isVisible())
        self.assertTrue(page.detail_panel.isVisible())
        page.note_button.click()
        self.app.processEvents()
        self.assertLessEqual(page.note_save_bar.mapTo(window, QPoint(0, page.note_save_bar.height())).y(), window.height())
        self.assertIsNotNone(page.editing_note)
        self.assertFalse(page.file_views.isEnabled())
        entry, original = page.editing_note
        draft = original + "\n新的一行\n另一行"
        page.note_editor.setPlainText(draft)
        with patch.object(page.index, "save_note", side_effect=UserError("外部修改")), patch.object(QMessageBox, "information"):
            page.save_note_edit()
        self.assertEqual(page.note_editor.toPlainText(), draft)
        self.assertEqual(Path(entry["path"]).read_text(encoding="utf-8"), original)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(page.confirm_note_close())
        page.save_note_edit()
        self.until_idle(window)
        self.assertIsNone(page.editing_note)
        self.assertTrue(page.file_views.isEnabled())
        self.assertEqual(Path(entry["path"]).read_text(encoding="utf-8"), draft)
        self.assertIn("新的一行\n另一行", page.preview.toPlainText())
        page.edit_note()
        page.note_editor.setPlainText(draft + "\n退出前保存")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            self.assertTrue(page.confirm_note_close())
        self.assertFalse(window.jobs)

    def test_note_creation_keeps_failed_draft_and_uses_first_line_as_title(self):
        window = self.make_window()
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        page = window.knowledge_page
        original_exec = QDialog.exec
        captured = []
        def enter(dialog):
            body = dialog.findChild(QTextEdit)
            body.setPlainText("自动标题\n第二行")
            buttons = dialog.findChild(QDialogButtonBox)
            QTimer.singleShot(0, lambda: buttons.button(QDialogButtonBox.StandardButton.Save).click())
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            def timeout():
                captured.append((body.toPlainText(), buttons.isEnabled()))
                dialog.reject()
            timer.timeout.connect(timeout)
            timer.start(2500)
            result = original_exec(dialog)
            timer.stop()
            return result
        with patch.object(QDialog, "exec", enter), patch.object(page.index, "add_note", side_effect=UserError("不能写入")), patch.object(QMessageBox, "information"):
            page.new_note()
        self.assertEqual(captured, [("自动标题\n第二行", True)])
        with patch.object(QDialog, "exec", enter):
            page.new_note()
        self.until_idle(window)
        target = page.index.root / "02-知识/00-收件箱/自动标题.md"
        self.assertIn("自动标题\n第二行", target.read_text(encoding="utf-8"))

    def test_plan_dialog_saves_before_closing_and_returns_to_category(self):
        window = self.make_window()
        page = window.planning_page
        page.select_view("plans")
        self.settle(window)
        original_exec = QDialog.exec
        def enter(dialog):
            self.assertIsInstance(dialog, PlanDocumentDialog)
            dialog.title.setText("弹窗新建的计划")
            QTimer.singleShot(0, dialog.save.click)
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            timer.timeout.connect(dialog.reject)
            timer.start(6000)
            result = original_exec(dialog)
            timer.stop()
            return result
        with patch.object(QDialog, "exec", enter), patch.object(QMessageBox, "warning") as warning:
            page.create_plan("plan")
            self.settle(window)
            warning.assert_not_called()
        self.assertEqual(page.view, "plans")
        self.assertEqual(page.list.count(), 1)
        self.assertEqual(page.service.plans()[0]["title"], "弹窗新建的计划")
