"""Bound query growth and compare optimized projections with the prior behavior."""
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.support import TestDirectory
from tests import test_plan_modules as ui_fixture
from selfManger.action_center import ActionCenter
from selfManger.domain import UserError
from selfManger.project_task_documents import _block, _parse_rows, _render, import_legacy_project_task_documents
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store


class HotpathDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.store.set_setting("workbench_root", str(self.root / "notes"))
        self.center = ActionCenter(self.store)

    def project(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        created = workspace.create("隔离性能测试", "不读取真实资料", False, TaskContext())
        return workspace, created, {"project_id": created["project_id"], "project_root": str(workspace.root)}

    def test_benchmark_script_tracks_sqlite_only_project_task_reads(self):
        output = self.root / "benchmark"
        script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_hotpaths.py"
        result = subprocess.run(
            [sys.executable, str(script), "--output", str(output), "--tasks", "0", "--repeats", "1"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(report["task_count"], 0)
        self.assertEqual(report["project_task_read"]["selects"], [1])
        self.assertNotIn("project_sync", report)
        self.assertTrue(report["period_actions_comparison"]["equivalent"])
        self.assertEqual(report["period_actions_comparison"]["row_count"], 0)
        self.assertIn("not a historical source snapshot", report["period_actions_comparison"]["reference_label"])

    def test_600_sqlite_tasks_use_bounded_queries_without_markdown_export(self):
        workspace, project, scope = self.project()
        with self.store.atomic():
            for index in range(600):
                self.store.add_planned_task({**scope, "title": f"任务{index}", "source": "ai"})
        before = self.store.planned_tasks()
        statements = []
        connect = sqlite3.connect
        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with patch("selfManger.storage.sqlite3.connect", traced):
            loaded = self.store.planned_tasks(project_id=project["project_id"], project_root=str(workspace.root))
        self.assertLessEqual(sum(sql.lstrip().upper().startswith("SELECT") for sql in statements), 2)
        self.assertEqual(loaded, before)
        self.assertFalse(list(Path(project["directory"]).glob("agent/TASKS-*.md")))

    def test_batched_lookup_still_rejects_foreign_task_id(self):
        workspace, project, scope = self.project()
        foreign = self.store.add_planned_task({"title": "其他项目任务", "source": "ai", "project_id": "P-other",
                                               "project_root": str(self.root / "other")})
        path = Path(project["directory"]) / "agent/TASKS-AI.md"
        text = _render("ai", [foreign])
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(UserError, "不属于当前项目"):
            import_legacy_project_task_documents(self.store, workspace, project["project_id"])
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        self.assertEqual(self.store.planned_task(foreign["id"]), foreign)

    def test_table_parser_resolves_the_same_root_once(self):
        rows = [{"id": f"{index:032x}", "title": f"任务{index}", "status": "planned", "kind": "task"} for index in range(400)]
        body = _block(_render("ai", rows))[1]
        original, calls = Path.resolve, []
        def tracked(path, *args, **kwargs):
            calls.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", tracked):
            parsed = _parse_rows(body, "ai", "P-fixture", self.root)
        self.assertEqual(len(parsed), 400)
        self.assertEqual(calls, [self.root])

    def prior_period_projection(self, start, end):
        rules = self.center.rules()
        with self.store.connect() as db:
            completed = {(row["task_id"], row["day"]) for row in db.execute(
                "SELECT task_id,day FROM action_occurrences WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}
        result = []
        for row in self.center.actions("all", day=start):
            rule = rules.get(row["id"])
            if not rule:
                if row["due_date"] and start.isoformat() <= row["due_date"] <= end.isoformat():
                    result.append(row)
                continue
            for offset in range((end - start).days + 1):
                target = start + timedelta(days=offset)
                if self.center.occurs(rule, target):
                    key = target.isoformat()
                    result.append({**row, "occurrence_day": key, "done": (row["id"], key) in completed, "overdue": False})
        return sorted(result, key=lambda row: (row["occurrence_day"], row["title"]))

    def test_period_projection_matches_prior_behavior_including_ties_and_completed_rules(self):
        plan = self.center.save_plan({"title": "所属计划"})
        start, end = date(2026, 1, 1), date(2026, 3, 2)
        records = []
        for values in (
            {"title": "同名", "due_date": "2026-01-05", "priority": "low"},
            {"title": "同名", "due_date": "2026-01-05", "priority": "high", "status": "done"},
            {"title": "同名", "due_date": "2025-12-01", "repeat": "weekly", "repeat_days": [1, 3], "goal_id": plan["id"]},
            {"title": "同名", "due_date": "2025-12-01", "repeat": "weekly", "repeat_days": [1], "status": "done"},
            {"title": "月末", "due_date": "2026-01-31", "repeat": "monthly", "repeat_month_day": 31},
            {"title": "结束周期", "due_date": "2025-12-20", "repeat": "daily", "repeat_end": "2026-01-08"},
            {"title": "未来周期", "due_date": "2030-01-01", "repeat": "daily"},
            {"title": "未排期"},
            {"title": "日程", "kind": "schedule", "due_date": "2026-02-01"},
        ):
            records.append(self.center.save_action(values))
        with self.store.connect() as db:
            db.execute("INSERT INTO action_occurrences VALUES (?, ?, ?)", (records[2]["id"], "2026-01-05", "fixture"))
        expected = self.prior_period_projection(start, end)
        with patch.object(self.center, "actions", side_effect=AssertionError("Review searched outside its interval")):
            actual = self.center.period_actions(start, end)
        self.assertEqual(actual, expected)
        self.assertTrue(any(row["plan_title"] == plan["title"] for row in actual))

    def test_period_projection_reuses_dates_for_identical_recurrence_rules(self):
        start, end = date(2026, 1, 5), date(2026, 1, 18)
        with self.store.atomic():
            for index in range(12):
                self.center.save_action({"title": f"共享周期{index}", "due_date": start.isoformat(),
                                         "repeat": "weekly", "repeat_days": [start.isoweekday()]})
        with patch.object(self.center, "occurs", wraps=self.center.occurs) as occurs:
            rows = self.center.period_actions(start, end)
        self.assertEqual(len(rows), 24)
        self.assertEqual(occurs.call_count, (end - start).days + 1)


class HotpathUITests(unittest.TestCase):
    setUpClass = classmethod(ui_fixture.PlanModuleTests.setUpClass.__func__)
    setUp = ui_fixture.PlanModuleTests.setUp
    make_window = ui_fixture.PlanModuleTests.make_window
    until_idle = ui_fixture.PlanModuleTests.until_idle
    settle = ui_fixture.PlanModuleTests.settle
    close_window = ui_fixture.PlanModuleTests.close_window



    def test_workbench_location_change_updates_saved_root(self):
        window = self.make_window()
        destination = self.root / "new-notes"
        destination.mkdir()
        with patch("selfManger.ui.window.QFileDialog.getExistingDirectory", return_value=str(destination)):
            window.change_workbench_location()
        self.settle(window)
        self.assertEqual(Path(window.store.setting("workbench_root")).resolve(), destination.resolve())


if __name__ == "__main__":
    unittest.main()
