"""Behavioral coverage for local personal planning and shared occurrences."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from tests.support import TestDirectory
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QMessageBox

from selfManger.action_center import ActionCenter
from selfManger.domain import UserError
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.ui.action_page import ItemEditor
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.today_widgets import ROW_DATA
from selfManger.workbench import work_root
from tests.legacy_plan_fixtures import write_legacy_plan
from tests import test_ui_optimization as fixture


class ActionCenterTests(unittest.TestCase):
    def setUp(self):
        temporary = TestDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "data")
        self.center = ActionCenter(self.store)
        self.day = date(2026, 10, 17)

    def test_review_periods_use_iso_week_year_at_calendar_year_boundary(self):
        weekly, monthly = self.center.review_periods(date(2027, 1, 1))
        self.assertEqual(weekly, (date(2026, 12, 28), date(2027, 1, 3), "weekly:2026-W53"))
        self.assertEqual(monthly, (date(2027, 1, 1), date(2027, 1, 31), "monthly:2027-01"))

    def test_review_period_summary_counts_occurrences_and_linked_plans(self):
        start, end = date(2026, 10, 12), date(2026, 10, 18)
        rows = [
            {"occurrence_day": "2026-10-12", "done": True, "goal_id": "g1", "project_id": "p1"},
            {"occurrence_day": "2026-10-13", "done": False, "goal_id": "g1", "project_id": "p1"},
            {"occurrence_day": "2026-10-19", "done": True, "goal_id": "g2", "project_id": "p2"},
            {"occurrence_day": None, "done": False, "goal_id": "g2", "project_id": "p2"},
        ]
        plans = [
            {"id": "g1", "due_date": None},
            {"id": "g3", "due_date": "2026-10-18"},
            {"id": "g4", "due_date": "2026-10-19"},
        ]
        summary = self.center.review_period_summary(start, end, rows, plans)
        self.assertEqual((summary["done"], summary["total"]), (1, 2))
        self.assertEqual([plan["id"] for plan in summary["plans"]], ["g1", "g3"])
        self.assertEqual(summary["plan_counts"], [("g1", 1, 2)])
        self.assertEqual(summary["project_counts"], [("p1", 1, 2)])

    def test_review_period_summary_rejects_reversed_interval(self):
        with self.assertRaisesRegex(UserError, "开始日期不能晚于结束日期"):
            self.center.review_period_summary(date(2026, 10, 18), date(2026, 10, 12), [], [])

    def test_independent_inbox_optional_plan_deadline_and_project(self):
        task = self.center.save_action({"title": "随手记下来"})
        self.assertEqual([row["id"] for row in self.center.actions("inbox")], [task["id"]])
        self.assertEqual(self.center.actions("today", day=self.day), [])
        plan = self.center.save_plan({"title": "整理环境"})
        linked = self.center.save_action({"title": "列出软件清单", "goal_id": plan["id"],
                                          "project_id": "P-test", "project_root": str(self.root)})
        self.assertIsNone(plan["due_date"])
        self.assertEqual(self.center.actions(plan_id=plan["id"])[0]["id"], linked["id"])
        self.center.complete(linked["id"], True)
        self.assertEqual(self.center.plans()[0]["status"], "planned")
        self.assertEqual(self.store.planned_tasks(project_id="P-test")[0]["status"], "done")

    def test_project_linked_record_counts_include_legacy_unmapped_roots_only_for_same_project(self):
        root = str(self.root / "project")
        self.center.save_action({"title": "根目录相同行动", "project_id": "P-one", "project_root": root})
        self.center.save_action({"title": "旧行缺根行动", "project_id": "P-one", "project_root": ""})
        self.center.save_action({"title": "别的项目行动", "project_id": "P-two", "project_root": root})
        self.store.add_planned_task({"title": "项目 AI 任务", "source": "ai",
                                     "project_id": "P-one", "project_root": root})
        self.center.save_plan({"title": "根目录相同计划", "project_id": "P-one", "project_root": root})
        self.center.save_plan({"title": "旧行缺根计划", "project_id": "P-one", "project_root": None})
        self.center.save_plan({"title": "别的目录计划", "project_id": "P-one", "project_root": str(self.root / "other")})

        self.assertEqual(self.center.project_linked_record_counts("P-one", root), (3, 2))

    def test_project_task_service_pins_project_identity_and_rejects_stale_edit(self):
        root = str(self.root / "project")
        original = self.center.save_project_task({
            "title": "原项目任务", "source": "ai", "project_id": "P-one", "project_root": root,
        })
        edited = self.center.save_project_task(
            {**original, "title": "已更新", "project_id": "P-other", "project_root": str(self.root / "other")},
            original["id"], expected=original)

        self.assertEqual(edited["title"], "已更新")
        self.assertEqual(edited["project_id"], "P-one")
        self.assertEqual(edited["project_root"], root)
        with self.assertRaisesRegex(UserError, "任务已变化"):
            self.center.save_project_task({**original, "title": "过期覆盖"}, original["id"], expected=original)
        self.assertEqual(self.store.planned_task(original["id"])["title"], "已更新")

        completed = self.center.set_ai_project_task_status(original["id"], "done", expected=edited)
        self.assertEqual(completed["status"], "done")
        with self.assertRaisesRegex(UserError, "AI 项目任务已变化"):
            self.center.set_ai_project_task_status(original["id"], "planned", expected=edited)
        self.assertEqual(self.store.planned_task(original["id"])["status"], "done")

    def test_project_task_delete_checks_selected_database_version(self):
        task = self.center.save_project_task({"title": "删除版本", "project_id": "P-one",
                                              "project_root": str(self.root)})
        selected = {**task, "personal": True, "done": False, "occurrence_day": None}
        changed = self.store.update_planned_task(task["id"], {**task, "title": "并发修改"})
        with self.assertRaisesRegex(UserError, "任务已变化"):
            self.center.delete_project_task(task["id"], expected=selected)
        self.assertEqual(self.store.planned_task(task["id"])["title"], "并发修改")

        deleted = self.center.delete_project_task(task["id"], expected=changed)
        self.assertEqual(deleted["deleted"], task["id"])
        self.assertIsNone(self.store.planned_task(task["id"]))

    def test_edit_preserves_but_cannot_change_an_unresolved_legacy_plan_reference(self):
        task = self.store.add_planned_task(
            {"title": "仍有效的历史行动", "status": "active", "goal_id": "old1"},
            "personal:legacy-goal-action")

        edited = self.center.save_action({**task, "title": "已编辑的历史行动"}, task["id"])

        self.assertEqual(edited["title"], "已编辑的历史行动")
        self.assertEqual(edited["goal_id"], "old1")
        with self.assertRaisesRegex(UserError, "计划已不存在"):
            self.center.save_action({**edited, "title": "不允许替换为别的无效关联", "goal_id": "old2"}, task["id"])
        with self.assertRaisesRegex(UserError, "计划已不存在"):
            self.center.save_action({"title": "不允许新建无效关联", "goal_id": "old1"})
        self.assertEqual(self.store.planned_task(task["id"])["goal_id"], "old1")
        unlinked = self.center.save_action({**edited, "title": "明确取消旧关联", "goal_id": ""}, task["id"])
        self.assertIsNone(unlinked["goal_id"])

    def test_rhythm_counts_planned_and_checked_occasions_per_day(self):
        start = self.day
        self.center.save_action({"title": "当日未做", "due_date": start.isoformat()})
        done = self.center.save_action({"title": "当日完成", "due_date": start.isoformat()})
        self.center.complete(done["id"], True)
        rule = self.center.save_action({"title": "每天", "repeat": "daily", "due_date": start.isoformat()})
        self.center.complete(rule["id"], True, (start + timedelta(days=1)).isoformat())
        days = self.center.rhythm(start, start + timedelta(days=2), today=start)
        self.assertEqual([(day["planned"], day["done"]) for day in days], [(3, 1), (1, 1), (1, 0)])
        self.assertEqual([day["label"] for day in days], ["六", "日", "一"])
        self.assertTrue(days[0]["today"])
        self.assertFalse(days[1]["today"])
        with self.assertRaises(UserError):
            self.center.rhythm(start, start + timedelta(days=90))

    def test_time_views_keep_overdue_and_group_completed_only_on_their_day(self):
        today = self.center.save_action({"title": "今天", "due_date": self.day.isoformat()})
        old = self.center.save_action({"title": "之前未做", "due_date": (self.day-timedelta(days=1)).isoformat()})
        future = self.center.save_action({"title": "明天", "due_date": (self.day+timedelta(days=1)).isoformat()})
        self.center.complete(today["id"], True)
        self.assertEqual({row["id"] for row in self.center.actions("today", day=self.day)}, {today["id"], old["id"]})
        self.assertEqual(self.center.actions("upcoming", day=self.day)[0]["id"], future["id"])
        self.center.complete(old["id"], True)
        self.assertEqual(len(self.center.actions("today", day=self.day)), 1)

    def test_daily_completion_is_idempotent_and_can_be_undone_without_completing_series(self):
        task = self.center.save_action({"title": "每天整理", "repeat": "daily", "due_date": self.day.isoformat()})
        self.center.complete(task["id"], True, self.day.isoformat())
        self.center.complete(task["id"], True, self.day.isoformat())
        self.assertTrue(self.center.actions("today", day=self.day)[0]["done"])
        self.assertFalse(self.center.actions("today", day=self.day+timedelta(days=1))[0]["done"])
        self.assertEqual(self.store.planned_task(task["id"])["status"], "planned")
        self.center.complete(task["id"], False, self.day.isoformat())
        self.assertFalse(self.center.actions("today", day=self.day)[0]["done"])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM planned_tasks").fetchone()[0], 1)

    def test_weekly_until_and_invalid_rule_are_atomic(self):
        task = self.center.save_action({"title": "每周整理", "repeat": "weekly", "due_date": self.day.isoformat(),
                                       "repeat_end": (self.day+timedelta(days=7)).isoformat()})
        self.assertFalse(self.center.actions("today", day=self.day+timedelta(days=1)))
        self.assertEqual(len(self.center.actions("today", day=self.day+timedelta(days=7))), 1)
        self.assertFalse(self.center.actions("today", day=self.day+timedelta(days=14)))
        with self.assertRaises(UserError):
            self.center.complete(task["id"], True, (self.day+timedelta(days=1)).isoformat())
        with self.assertRaises(UserError):
            self.center.save_action({"title": "无效重复", "repeat": "daily"})
        self.assertEqual(len(self.store.planned_tasks()), 1)

    def test_project_document_templates_are_quarantined_and_original_records_remain(self):
        for title, ref in (("明确目标与验收条件", "legacy:project:root:P-1:manual:task:x"),
                           ("用户要求恢复时确认范围与真源", "project-doc:root:P-2:manual:x")):
            self.store.add_planned_task({"title": title, "due_date": self.day.isoformat()}, ref)
        self.store.add_planned_task({"title": "Agent 工作", "source": "ai"})
        self.assertEqual(self.center.actions(), [])
        self.assertEqual(self.center.actions("today", day=self.day), [])
        self.assertEqual(len(self.store.planned_tasks()), 3)

    def test_legacy_plans_rules_templates_are_copied_once_and_originals_unchanged(self):
        root = self.root / "workbench"
        write_legacy_plan(root, "plan", "a" * 32,
                          {"title": "原计划", "due": self.day.isoformat()}, "原正文")
        write_legacy_plan(root, "recurring", "b" * 32,
                          {"title": "旧重复", "start": self.day.isoformat(), "frequency": "weekly", "weekdays": [6, 7]}, "说明")
        write_legacy_plan(root, "template", "c" * 32, {"title": "旧模板"}, "模板正文")
        originals = {path: path.read_bytes() for path in root.rglob("*.md")}
        self.assertEqual(self.center.import_legacy_plans(root), 2)
        self.assertEqual(self.center.import_legacy_plans(root), 0)
        self.assertEqual(self.center.plans()[0]["title"], "原计划")
        self.assertEqual(self.center.templates()[0]["notes"].strip(), "模板正文")
        self.assertEqual(len(self.center.actions("today", day=self.day+timedelta(days=1))), 1)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

    def test_malformed_legacy_status_is_reported_without_partial_import(self):
        cases = [
            ("plan", {"status": ["done"]}),
            ("plan", {"project_id": ["P-1"]}),
            ("recurring", {"start": self.day.isoformat(), "frequency": ["daily"]}),
            ("recurring", {"start": self.day.isoformat(), "frequency": "daily", "status": "unknown"}),
            ("recurring", {"start": "not-a-date", "frequency": "daily"}),
            ("recurring", {"start": self.day.isoformat(), "due": "2026-01-01", "frequency": "daily"}),
            ("review", {"start": self.day.isoformat(), "due": self.day.isoformat(), "summary_period": []}),
        ]
        for index, (kind, invalid) in enumerate(cases):
            with self.subTest(kind=kind, invalid=invalid):
                root = self.root / f"invalid-legacy-metadata-{index}"
                metadata = {"title": "无效旧资料", **invalid}
                if kind == "recurring":
                    metadata.setdefault("start", self.day.isoformat())
                    metadata.setdefault("frequency", "daily")
                if kind == "review":
                    metadata.setdefault("start", self.day.isoformat())
                    metadata.setdefault("due", self.day.isoformat())
                write_legacy_plan(root, kind, f"{index:032x}", metadata, "原文保留")
                source = next(root.rglob("*.md"))
                original = source.read_bytes()
                with self.assertRaises(UserError):
                    self.center.import_legacy_plans(root)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(self.center.plans(), [])
                self.assertEqual(self.store.planned_tasks(), [])
                self.assertEqual(self.center.reviews(), [])
                self.assertFalse(self.store.setting("personal_plans_import:" + str(root.resolve()), False))

    def test_legacy_recurring_plan_accepts_yaml_date_scalars(self):
        root = self.root / "yaml-date-scalars"
        start = date(2026, 10, 1)
        due = date(2026, 12, 31)
        write_legacy_plan(root, "recurring", "a" * 32,
                          {"title": "YAML日期周期", "start": start, "due": due, "frequency": "daily"}, "正文")
        self.assertEqual(self.center.import_legacy_plans(root), 1)
        task = next(row for row in self.store.planned_tasks() if row["title"] == "YAML日期周期")
        rule = self.center.rules()[task["id"]]
        self.assertEqual((rule["start_date"], rule["end_date"]), (start.isoformat(), due.isoformat()))

    def test_legacy_goal_relationship_progress_and_review_fields_are_migrated(self):
        root = self.root / "legacy-fields"
        goal_id, child_id, recurring_id, review_id = ("d" * 32, "e" * 32, "f" * 32, "0" * 32)
        write_legacy_plan(root, "goal", goal_id,
                          {"title": "旧目标", "start": "2026-01-01", "due": "2026-12-31", "progress": 65}, "目标正文")
        write_legacy_plan(root, "plan", child_id,
                          {"title": "子计划", "start": "2026-02-01", "due": "2026-03-01", "parent_id": goal_id}, "子计划正文")
        write_legacy_plan(root, "recurring", recurring_id,
                          {"title": "月末计划", "start": "2026-01-31", "due": "2026-03-31",
                           "frequency": "monthly", "completed_dates": ["2026-02-28"]}, "月末正文")
        write_legacy_plan(root, "review", review_id,
                          {"title": "旧周复盘", "start": "2026-10-12", "due": "2026-10-18",
                           "summary_period": "weekly"}, "复盘正文")

        self.assertEqual(self.center.import_legacy_plans(root), 4)

        imported = {row["id"]: row for row in self.center.plans()}
        self.assertEqual((imported[goal_id]["start_date"], imported[goal_id]["progress"]),
                         ("2026-01-01", 65))
        self.assertEqual((imported[child_id]["start_date"], imported[child_id]["parent_id"]),
                         ("2026-02-01", goal_id))
        monthly = next(row for row in self.store.planned_tasks() if row["title"] == "月末计划")
        self.assertEqual(self.center.rules()[monthly["id"]]["frequency"], "monthly")
        with self.store.connect() as db:
            occurrences = {row[0] for row in db.execute(
                "SELECT day FROM action_occurrences WHERE task_id=?", (monthly["id"],))}
        self.assertEqual(occurrences, {"2026-02-28"})
        review = self.center.reviews()[0]
        self.assertEqual((review["period_type"], review["period_key"], review["start_date"], review["end_date"], review["body"]),
                         ("weekly", "weekly:2026-W42", "2026-10-12", "2026-10-18", "复盘正文"))

    def test_project_link_conflict_retains_persisted_change(self):
        plan = self.center.save_plan({"title": "个人管家"})
        task = self.center.save_action({"title": "界面", "project_id": "P", "project_root": str(self.root), "goal_id": plan["id"]})
        self.center.complete(task["id"], True)
        with self.assertRaises(UserError):
            self.center.save_action({**task, "title": "覆盖", "_expected": task}, task["id"])
        self.assertEqual(self.store.planned_task(task["id"])["status"], "done")


class ActionPageTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    close_window = fixture.UIOptimizationTests.close_window
    until_idle = fixture.UIOptimizationTests.until_idle

    def settle(self, window):
        deadline = time.monotonic()+12
        idle_since = None
        while time.monotonic() < deadline:
            timer = getattr(self, "_visual_timer", None)
            if timer:
                timer.run("processEvents", self.app.processEvents)
            else:
                self.app.processEvents()
            page = window.planning_page
            if not window.jobs and window.today_page.worker is None and not page.loading and not page.pending_refresh:
                idle_since = idle_since or time.monotonic()
                if time.monotonic()-idle_since > .15:
                    return
            else:
                idle_since = None
            time.sleep(.005)
        self.fail("Action readers did not settle")

    def test_quick_add_today_checkbox_and_workbench_share_identity(self):
        window = self.make_window()
        page = window.planning_page
        window.navigation.setCurrentRow(window.PLAN)
        self.settle(window)
        self.assertEqual(page.view, "quick-create")
        page.select_view("today")
        self.settle(window)
        self.assertFalse(page.drawer.isVisible())
        page.quick_input.setText("今天开始重构")
        page.quick_input.returnPressed.emit()
        self.settle(window)
        task = page.rows[0]
        self.assertEqual(task["title"], "今天开始重构")
        self.assertEqual(window.today_page.report["tasks"][0]["planned_task_id"], task["id"])
        entry = next(page.list.item(i) for i in range(page.list.count()) if page.list.item(i).data(Qt.ItemDataRole.UserRole))
        entry.setCheckState(Qt.CheckState.Checked)
        page.open_item(entry)
        self.assertFalse(page.drawer.isVisible())
        self.settle(window)
        self.assertEqual(window.store.planned_task(task["id"])["status"], "done")
        self.assertTrue(window.today_page.report["tasks"][0]["done"])

    def test_repeat_can_be_completed_from_workbench_and_tomorrow_is_independent(self):
        window = self.make_window()
        page = window.planning_page
        task = page.service.save_action({"title": "检查日程", "due_date": date.today().isoformat(), "repeat": "daily"})
        window.today_page.refresh()
        self.settle(window)
        entry = next(window.today_page.tasks.item(i) for i in range(window.today_page.tasks.count())
                     if (window.today_page.tasks.item(i).data(ROW_DATA) or {}).get("planned_task_id") == task["id"])
        entry.setCheckState(Qt.CheckState.Checked)
        self.settle(window)
        self.assertTrue(page.service.actions("today")[0]["done"])
        self.assertFalse(page.service.actions("today", day=date.today()+timedelta(days=1))[0]["done"])

    def test_project_linked_action_has_one_record_and_completion_updates_both_views(self):
        window = self.make_window()
        workspace = ProjectWorkspace(self.root / "workspace")
        context = TaskContext()
        workspace.initialize(context)
        created = workspace.create("个人管家", "界面", False, context)
        window.store.set_setting("project_workspace", str(workspace.root))
        project = workspace.project(created["project_id"])
        task_documents = {path: path.read_bytes() for path in workspace.root.rglob("TASKS-MANUAL.md")}
        page = window.planning_page
        task = page.service.save_action({"title": "周日完成知识库界面", "project_id": project["id"],
                                        "project_root": str(workspace.root.resolve()), "due_date": date.today().isoformat()})
        rows = window.project_page.linked_project_tasks(project)
        self.assertEqual([row["id"] for row in rows], [task["id"]])
        window.project_page.refresh()
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        window.project_page.toggle_shared_task(task["id"], True)
        self.settle(window)
        self.assertTrue(page.service.actions()[0]["done"])
        self.assertEqual(len(window.store.planned_tasks()), 1)
        self.assertEqual({path: path.read_bytes() for path in workspace.root.rglob("TASKS-MANUAL.md")},
                         task_documents)

        self.assertTrue(window.close())
        reopened_store = Store(self.root / "data")
        reopened = type(window)(reopened_store)
        reopened.show()
        self.settle(reopened)
        reopened.navigation.setCurrentRow(reopened.PLAN)
        self.settle(reopened)
        persisted = reopened_store.planned_task(task["id"])
        self.assertEqual(persisted["status"], "done")
        self.assertEqual(len(reopened.planning_page.service.actions()), 1)
        reopened.navigation.setCurrentRow(reopened.PROJECT)
        self.settle(reopened)
        reopened_project = workspace.project(created["project_id"])
        self.assertEqual([row["id"] for row in reopened.project_page.linked_project_tasks(reopened_project)],
                         [task["id"]])
        reopened.project_page.toggle_shared_task(task["id"], False)
        self.settle(reopened)
        self.assertEqual(reopened_store.planned_task(task["id"])["status"], "planned")
        reopened.navigation.setCurrentRow(reopened.PLAN)
        self.settle(reopened)
        self.assertFalse(reopened.planning_page.service.actions()[0]["done"])

    def test_drawer_responsive_bounds_and_cancel_keeps_original(self):
        window = self.make_window()
        page = window.planning_page
        task = page.service.save_action({"title": "原行动"})
        window.navigation.setCurrentRow(window.PLAN)
        self.settle(window)
        for width in (1440, 960):
            window.resize(width, 660)
            page.open_editor(record=task)
            self.app.processEvents()
            editor = page.editor
            self.assertTrue(editor.save.isVisible())
            self.assertTrue(editor.rect().contains(editor.save.geometry().bottomRight()))
            editor.title.setText("未保存草稿")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                self.assertFalse(page.close_drawer())
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                self.assertTrue(page.close_drawer())
        self.assertEqual(window.store.planned_task(task["id"])["title"], "原行动")

    def test_new_plan_template_and_failed_action_save_preserve_form(self):
        window = self.make_window()
        page = window.planning_page
        self.settle(window)
        captured = []
        original_exec = QDialog.exec
        def enter(dialog):
            editor = dialog.findChild(ItemEditor)
            editor.title.setText("失败草稿")
            editor.save.click()
            self.settle(window)
            captured.append((editor.title.text(), editor.isEnabled(), dialog.result()))
            dialog.reject()
            return QDialog.DialogCode.Rejected
        with patch.object(QDialog, "exec", enter), patch.object(page.service, "save_action", side_effect=UserError("测试失败")), patch.object(QMessageBox, "warning"):
            page.create_action()
        self.assertEqual(captured, [("失败草稿", True, 0)])
        page.select_view("plans")
        self.settle(window)
        def enter_plan(dialog):
            dialog.title.setText("无期限计划")
            dialog.save.click()
            self.settle(window)
            return dialog.result()
        with patch.object(PlanDocumentDialog, "exec", enter_plan):
            page.create_plan()
        self.settle(window)
        self.assertEqual(page.service.plans()[0]["title"], "无期限计划")
        self.assertIsNone(page.service.plans()[0]["due_date"])

    def test_navigation_never_rescans_project_task_templates(self):
        window = self.make_window()
        self.settle(window)
        for index in (window.PLAN, window.PROJECT, window.TODAY):
            window.navigation.setCurrentRow(index)
            self.settle(window)
        self.assertEqual(window.store.planned_tasks(), [])

    def test_today_sections_and_review_views(self):
        window = self.make_window()
        page = window.planning_page
        today = date.today().isoformat()
        window.navigation.setCurrentRow(window.PLAN)
        self.settle(window)
        page.select_view("today")
        self.settle(window)
        page.service.save_action({"title": "今日事项", "due_date": today})
        page.service.save_action({"title": "另一今日事项", "due_date": today})
        page.refresh()
        self.settle(window)

        headers = [page.list.item(i).text() for i in range(page.list.count())
                   if not page.list.item(i).data(Qt.ItemDataRole.UserRole)]
        self.assertIn("今日任务", headers)

        page.select_view("review")
        self.settle(window)
        self.assertTrue(page.review_overview.isVisible())
        self.assertEqual(page.review_overview.summary.values[0][1].text(), "0 / 2")
        page.review_overview.select_mode("monthly")
        self.assertEqual(page.review_overview.summary.values[0][1].text(), "0 / 2")

    def test_quick_new_opens_peer_markdown_editor(self):
        window = self.make_window()
        page = window.planning_page
        window.navigation.setCurrentRow(window.PLAN)
        self.settle(window)
        page.select_view("review")
        self.settle(window)
        page.start_quick()
        self.settle(window)
        self.assertEqual(page.view, "quick-create")
        self.assertTrue(page.quick_create.title_input.isEnabled())
        self.assertTrue(page.quick_create.body.toPlainText())
