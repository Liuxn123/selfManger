"""Shared deletion, Markdown recovery copies, conflicts and visible delete controls."""
import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QPushButton, QMessageBox
from PySide6.QtCore import Qt

from tests.support import evidence_root, start_visual_timing, visual
from tests import test_action_center as domain_fixture
from tests import test_plan_modules as ui_fixture
from tests import test_project_plan_filter as project_fixture
from selfManger.domain import UserError
from selfManger.project_task_documents import import_legacy_project_task_documents
from selfManger.runtime import TaskContext
from selfManger.ui.theme import setup_theme, apply_theme
from selfManger.ui.plan_dialog import PlanDocumentDialog
from tests.personal_markdown_fixtures import attach_legacy_document, legacy_action_record


class PlanDeletionTests(unittest.TestCase):
    setUp = domain_fixture.ActionCenterTests.setUp

    def attach(self, kind, row, module):
        if kind == "action":
            row = legacy_action_record(self.center, row["id"], row)
        return attach_legacy_document(self.store, kind, row["id"], module, row)

    def test_parent_delete_updates_sqlite_and_preserves_legacy_markdown(self):
        plan = self.center.save_plan({"title": "可删除的项目目标", "project_id": "P-demo"})
        action = self.center.save_action({"title": "已经安排的行动", "goal_id": plan["id"], "due_date": "2026-10-01"})
        parent_path = self.attach("plan", plan, "project")
        child_path = self.attach("action", action, "daily")
        original = parent_path.read_text(encoding="utf-8")
        self.center.delete_record("plan", plan["id"], expected=plan)
        self.assertEqual(self.center.plans(), [])
        self.assertIsNone(self.store.planned_task(action["id"])["goal_id"])
        self.assertEqual(parent_path.read_text(encoding="utf-8"), original)
        self.assertTrue(child_path.is_file())
        with self.assertRaises(UserError):
            self.center.save_plan({"title": "旧窗口不能复活"}, plan["id"])
        with self.assertRaises(UserError):
            self.center.save_action({"title": "不能关联已删除目标", "goal_id": plan["id"]})

    def test_recurring_deletion_cleans_rules_and_occurrences_and_snapshot_survives(self):
        task = self.center.save_action({"title": "重复打卡", "repeat": "daily", "due_date": "2026-10-01"})
        self.center.complete(task["id"], True, "2026-10-01")
        path = self.attach("action", task, "recurring")
        self.center.delete_record("action", task["id"])
        self.assertTrue(path.is_file())
        self.assertEqual(self.center.actions(), [])
        self.assertIsNone(self.store.planned_task(task["id"]))
        self.assertEqual(self.store.planned_tasks(), [])
        self.assertEqual(self.center.rules(), {})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM action_occurrences").fetchone()[0], 0)
            snapshot = json.loads(db.execute("SELECT snapshot FROM planning_deletions").fetchone()[0])
        self.assertEqual(snapshot["rule"]["frequency"], "daily")
        self.assertEqual(snapshot["occurrences"][0]["day"], "2026-10-01")
        with self.assertRaises(UserError):
            self.center.complete(task["id"], True)

    def test_both_review_types_can_be_deleted_and_recreated(self):
        for period in ("weekly", "monthly"):
            values = dict(title="复盘", body="总结", period_type=period, period_key=period + ":test",
                          start_date="2026-10-01", end_date="2026-10-07")
            review = self.center.save_review(values)
            self.attach("review", review, period + "-review")
            self.center.delete_record("review", review["id"], expected=review)
            self.assertFalse(any(row["id"] == review["id"] for row in self.center.reviews()))
            replacement = self.center.save_review(values)
            self.assertNotEqual(replacement["id"], review["id"])

    def test_external_edit_is_migrated_before_delete_and_file_stays_archived(self):
        plan = self.center.save_plan({"title": "原计划"})
        path = self.attach("plan", plan, "longterm")
        path.write_text(path.read_text(encoding="utf-8") + "\n外部新增", encoding="utf-8")
        report = self.center.documents.migrate_legacy_documents()
        self.assertEqual(report["updated"], 1)
        self.assertIn("外部新增", self.center.plans()[0]["notes"])
        original = path.read_bytes()
        with self.assertRaises(UserError):
            self.center.delete_record("plan", plan["id"], expected=plan)
        self.assertEqual(len(self.center.plans()), 1)
        self.center.delete_record("plan", plan["id"])
        self.assertEqual(path.read_bytes(), original)

    def test_database_failure_restores_parent_and_linked_markdown(self):
        plan = self.center.save_plan({"title": "事务失败父计划"})
        task = self.center.save_action({"title": "独立保留的行动", "goal_id": plan["id"]})
        parent = self.attach("plan", plan, "longterm")
        child = self.attach("action", task, "daily")
        originals = [path.read_bytes() for path in (parent, child)]
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_deletion BEFORE INSERT ON planning_deletions BEGIN SELECT RAISE(ABORT,'模拟事务失败'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.center.delete_record("plan", plan["id"])
        self.assertEqual([path.read_bytes() for path in (parent, child)], originals)
        self.assertEqual(len(self.center.plans()), 1)
        self.assertEqual(self.store.planned_task(task["id"])["goal_id"], plan["id"])


class PlanDeletionUITests(unittest.TestCase):
    setUpClass = classmethod(ui_fixture.PlanModuleTests.setUpClass.__func__)
    setUp = ui_fixture.PlanModuleTests.setUp
    make_window = ui_fixture.PlanModuleTests.make_window
    until_idle = ui_fixture.PlanModuleTests.until_idle
    settle = ui_fixture.PlanModuleTests.settle
    close_window = ui_fixture.PlanModuleTests.close_window
    open_page = ui_fixture.PlanModuleTests.open_page
    workspace = project_fixture.ProjectPlanFilterTests.workspace

    def test_visible_delete_cancel_and_confirm_keep_page_and_refresh(self):
        window, page = self.open_page()
        plan = page.service.save_plan({"title": "删除入口验收"})
        page.select_view("plans")
        self.settle(window)
        item = page.list.item(0)
        card = page.list.itemWidget(item)
        delete = next(button for button in card.findChildren(QPushButton) if button.text() == "删除")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            delete.click()
        self.assertEqual(len(page.service.plans()), 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            delete.click()
            self.settle(window)
        self.assertEqual(page.view, "plans")
        self.assertEqual(page.service.plans(), [])
        self.assertEqual(page.list.count(), 0)

    def test_existing_project_task_deletes_and_stale_markdown_does_not_revive(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        plan = page.service.save_plan(dict(title="项目阶段目标", project_id=projects[0], project_root=str(workspace.root)))
        task = window.store.add_planned_task(dict(title="已有项目安排", source="ai", project_id=projects[0],
                                                 project_root=str(workspace.root), goal_id=plan["id"]))
        page.select_view("project-plans")
        self.settle(window)
        self.assertFalse(page.service.project_schedule(workspace.root, projects))  # AI tasks stay in project management.
        source = workspace.path(workspace.project(projects[0])["path"] + "/agent/TASKS-AI.md")
        from selfManger.project_task_documents import _render
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(_render("ai", [task]), encoding="utf-8")
        import_legacy_project_task_documents(window.store, workspace, projects[0], TaskContext())
        original = source.read_text(encoding="utf-8")
        self.assertIn(plan["id"], original)
        page.service.delete_record("plan", plan["id"])
        self.assertIsNone(window.store.planned_task(task["id"])["goal_id"])
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        import_legacy_project_task_documents(window.store, workspace, projects[0], TaskContext())
        self.assertIsNone(window.store.planned_task(task["id"])["goal_id"])
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        task = window.store.planned_task(task["id"])
        result = page.service.delete_record("action", task["id"], expected=task)
        self.assertFalse(result["warning"])
        self.assertFalse(page.service.project_schedule(workspace.root, projects))
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        import_legacy_project_task_documents(window.store, workspace, projects[0], TaskContext())
        self.assertIsNone(window.store.planned_task(task["id"]))
        self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_daily_and_recurring_visible_delete_buttons_and_quick_editor(self):
        window, page = self.open_page()
        for view, frequency in (("today", ""), ("recurring", "daily")):
            task = page.service.save_action(dict(title="可删除的" + view, due_date=date.today().isoformat(), repeat=frequency))
            page.select_view(view)
            self.settle(window)
            item = next(page.list.item(i) for i in range(page.list.count()) if page.list.item(i).data(Qt.ItemDataRole.UserRole))
            card = page.list.itemWidget(item)
            delete = next(button for button in card.findChildren(QPushButton) if button.text() == "删除")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                delete.click()
                self.settle(window)
            self.assertIsNone(window.store.planned_task(task["id"]))
        plan = page.service.save_plan(dict(title="快速编辑的已存计划"))
        page.refresh()
        self.settle(window)
        page.quick_create.load_document("longterm", plan)
        self.settle(window)
        self.assertTrue(page.quick_create.delete_button.isVisible())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page.quick_create.delete_button.click()
            self.settle(window)
        self.assertIsNone(page.quick_edit_record)
        self.assertFalse(page.quick_create.delete_button.isVisible())

    def test_review_history_and_edit_dialog_offer_deletion(self):
        window, page = self.open_page()
        page.select_view("review")
        self.settle(window)
        start, end, key = page.review_targets()[0]
        review = page.service.save_review(dict(title="删除周复盘", period_type="weekly", period_key=key,
                                               start_date=start.isoformat(), end_date=end.isoformat(), body="记录"))
        page.refresh()
        self.settle(window)
        delete = next(button for button in page.review_overview.findChildren(QPushButton) if button.text() == "删除")
        self.assertTrue(delete.isVisible())
        dialog = PlanDocumentDialog(page, "weekly-review", "", record=review)
        dialog.show()
        delete_dialog = next(button for button in dialog.findChildren(QPushButton) if button.text() == "删除复盘")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            delete_dialog.click()
            self.settle(window)
        self.assertEqual(dialog.result(), 1)
        self.assertEqual(page.service.reviews(), [])
        dialog.deleteLater()

    @visual
    def test_delete_card_visuals(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        page.service.save_plan({"title": "个人云盘或者 syncPan", "due_date": "2028-10-01"})
        page.select_view("plans")
        timer.run("settle", self.settle, window)
        setup_theme(self.app)
        output = evidence_root("test_plan_deletion")
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for width in (1400, 1024):
                window.resize(width, 800)
                timer.run("processEvents", self.app.processEvents)
                self.assertTrue(timer.capture(window, output / f"{theme}-{width}.png"))
