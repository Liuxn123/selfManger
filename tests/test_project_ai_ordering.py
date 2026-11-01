"""Numbered project AI ordering without changing agenda order or task records."""
import json
import os
import subprocess
import sys
import time
import unittest
from tests.support import evidence_root
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox

from tests import test_ui_optimization as fixture
from selfManger.project_task_documents import START, END, HEADER, import_legacy_project_task_documents
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.task_ordering import order_project_ai_tasks, project_checklist_snapshot
from selfManger.ui.project_widgets import overview_snapshot
from selfManger.ui.theme import setup_theme
from selfManger.ui.window import MainWindow


class ProjectAIOrderingTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp

    def raw_tasks(self, store):
        with store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM planned_tasks ORDER BY due_date IS NULL, due_date, created_at DESC")]

    def test_numeric_prefixes_ties_and_unnumbered_slots_are_stable(self):
        titles = ["10、学习", "手动任务", "无编号 AI", " 02. 学习", "1 学习", "01、另一课", "33学习"]
        rows = [{"title": title, "source": "manual" if i == 1 else "ai", "id": str(i)}
                for i, title in enumerate(titles)]
        original = [dict(row) for row in rows]
        result = order_project_ai_tasks(rows)
        self.assertEqual([row["title"] for row in result],
                         ["01、另一课", "手动任务", "无编号 AI", "1 学习", " 02. 学习", "10、学习", "33学习"])
        self.assertEqual(rows, original)
        self.assertEqual(order_project_ai_tasks(list(reversed([rows[0], rows[3]]))),
                         order_project_ai_tasks([rows[0], rows[3]]))

    def test_only_numbered_ai_in_same_project_move_and_limit_follows_order(self):
        store = Store(self.root / "data")
        for title, source, project, root, due in (
                ("10、课", "ai", "A", "root-A", "2026-01-01"),
                ("01、手动", "manual", "A", "root-A", "2026-01-02"),
                ("无编号AI", "ai", "A", "root-A", "2026-01-03"),
                ("02、课", "ai", "A", "root-A", None),
                ("01、另一工作区", "ai", "A", "root-B", "2026-01-01"),
                ("01、另一项目", "ai", "B", "root-A", "2026-01-01"),
                ("01、个人行动", "manual", None, None, "2026-01-04")):
            store.add_planned_task({"title": title, "source": source, "project_id": project,
                                    "project_root": root, "due_date": due})
        before = self.raw_tasks(store)
        scoped = store.planned_tasks(project_id="A", project_root="root-A")
        self.assertEqual([row["title"] for row in scoped], ["02、课", "01、手动", "无编号AI", "10、课"])
        self.assertEqual(store.planned_tasks(project_id="A", project_root="root-A", limit=1), scoped[:1])
        for row in before:
            self.assertEqual(store.planned_task(row["id"]), row)
        self.assertEqual(store.planned_tasks(), before)
        self.assertEqual(store.planned_tasks(limit=2), before[:2])
        all_roots = store.planned_tasks(project_id="A")
        self.assertEqual([r["project_root"] for r in all_roots],
                         [r["project_root"] for r in before if r["project_id"] == "A"])
        self.assertEqual([r["title"] for r in all_roots if r["project_root"] == "root-B"], ["01、另一工作区"])
        self.assertEqual(store.planned_tasks(project_id="B")[0]["title"], "01、另一项目")
        # Manual-only project reads retain the original date order.
        self.assertEqual(store.planned_tasks(project_id="A", source="manual"),
                         [row for row in before if row["project_id"] == "A" and row["source"] == "manual"])

    def test_project_checklist_read_model_merges_status_and_running_state(self):
        snapshot = {"checklist": {"tasks": [{"text": "文档任务"}], "total": 1, "done": 0,
                                  "in_progress": 0, "pending": 1, "overdue": 0, "progress": 0}}
        linked = [
            {"id": "one", "title": "03、后续", "source": "ai", "status": "planned", "due_date": "2026-11-02"},
            {"id": "two", "title": "手动行动", "source": "manual", "status": "done", "done": True,
             "due_date": None, "personal": True},
            {"id": "three", "title": "02、当前", "source": "ai", "status": "active", "due_date": None},
        ]

        merged = project_checklist_snapshot(snapshot, linked, running_ids={"three"})

        checklist = merged["checklist"]
        self.assertEqual([row["text"] for row in checklist["tasks"]], ["02、当前", "03、后续", "手动行动"])
        self.assertEqual([row["text"] for row in checklist["ai_tasks"]], ["02、当前", "03、后续"])
        self.assertTrue(checklist["tasks"][0]["running"])
        self.assertEqual((checklist["total"], checklist["done"], checklist["in_progress"], checklist["pending"], checklist["progress"]),
                         (3, 1, 1, 1, 33))
        self.assertEqual(snapshot["checklist"]["tasks"], [{"text": "文档任务"}])

    def settle(self, window):
        deadline = time.monotonic() + 20
        quiet = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            busy = (window.jobs or window.pool.activeThreadCount()
                    or window.project_page.project_detail_timer.isActive()
                    or window.today_page.reload_timer.isActive())
            if busy:
                quiet = None
            elif quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > .2:
                return
            time.sleep(.005)
        self.fail("isolated project did not settle")

    def test_33_one_time_import_refresh_process_restart_and_numeric_order(self):
        store = Store(self.root / "data")
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        created = workspace.create("33项学习任务", "仅验证编号排序", False, TaskContext())
        identity = created["project_id"]
        root = str(workspace.root.resolve())
        project = workspace.project(identity)
        ai_path = workspace.path(project["path"] + "/agent/TASKS-AI.md")
        store.set_setting("project_workspace", root)
        store.set_setting("workbench_root", str(self.root / "workbench"))
        titles = [f"{n:02d}、学习第{n:02d}课" for n in range(1, 34)]
        def row(n):
            return f"| new | {titles[n-1]} | planned | - | - | task | - | 学习验收{n} | - |"
        ai_path.write_text("# 保留说明\n\n" + START + "\n" + HEADER + "\n"
                           + "\n".join(row(n) for n in range(1, 34)) + "\n" + END + "\n保留尾注\n", encoding="utf-8")
        with patch("selfManger.storage.now", return_value="2026-10-01T00:00:00+00:00"):
            self.assertEqual(import_legacy_project_task_documents(store, workspace, identity)["imported"], 33)
        imported_source = ai_path.read_bytes()
        ai_path.write_text(ai_path.read_text(encoding="utf-8").replace("学习第33课", "迁入后修改"), encoding="utf-8")
        with patch("selfManger.storage.now", return_value="2026-10-02T00:00:00+00:00"):
            self.assertTrue(import_legacy_project_task_documents(store, workspace, identity)["already_imported"])
        raw = self.raw_tasks(store)
        self.assertEqual([r["title"] for r in raw], titles)
        self.assertEqual([r["title"] for r in store.planned_tasks(project_id=identity, project_root=root, limit=5)], titles[:5])
        original = {r["id"]: (r["title"], r["created_at"], r["due_date"], r["source_ref"]) for r in raw}
        stages = {}
        setup_theme(self.app)
        window = MainWindow(store)
        window.resize(1440, 960)
        window.show()
        self.settle(window)
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        page = window.project_page
        def verify(stage):
            self.settle(window)
            self.assertEqual(page.selected()["id"], identity)
            listing = page.task_lists["ai"]
            listed = [listing.item(i).data(Qt.ItemDataRole.UserRole)["title"] for i in range(listing.count())]
            overview = [label.text() for label in page.overview.ai_tasks_card.findChildren(QLabel, "ProjectTaskText")]
            self.assertEqual(listed, titles)
            self.assertEqual(overview, titles)
            self.assertEqual([r["title"] for r in window.store.planned_tasks(project_id=identity, project_root=root)], titles)
            stages[stage] = {"overview": overview, "list": listed}
        verify("import")
        # The UI reads the imported SQLite rows without reopening the legacy source.
        page.refresh()
        verify("refresh")
        evidence = evidence_root()
        window.grab().save(str(evidence / "overview-33.png"))
        window.close()
        window.pool.waitForDone(5000)
        self.app.processEvents()
        # A fresh Python interpreter reads the same persisted database.
        code = ("import json,sys; from pathlib import Path; from selfManger.storage import Store; "
                "print(json.dumps([r['title'] for r in Store(Path(sys.argv[1])).planned_tasks(project_id=sys.argv[2],project_root=sys.argv[3])]))")
        result = subprocess.run([sys.executable, "-c", code, str(store.root), identity, root],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), titles)
        reopened = Store(store.root)
        window = MainWindow(reopened)
        window.show()
        self.settle(window)
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        page = window.project_page
        verify("restart")
        self.assertEqual(import_legacy_project_task_documents(reopened, workspace, identity)["imported"], 0)
        self.assertEqual(original, {r["id"]: (r["title"], r["created_at"], r["due_date"], r["source_ref"])
                                    for r in self.raw_tasks(reopened)})
        self.assertEqual(reopened.planned_tasks(), self.raw_tasks(reopened))
        self.assertNotEqual(ai_path.read_bytes(), imported_source)
        self.assertIn("迁入后修改", ai_path.read_text(encoding="utf-8"))
        # The project overview merges its SQLite read model; Markdown is only imported explicitly.
        snapshot = overview_snapshot("", "", [])
        merged = project_checklist_snapshot(snapshot, page.linked_project_tasks(page.selected()))["checklist"]["ai_tasks"]
        self.assertEqual([r["text"] for r in merged], titles)
        (evidence / "33-task-orders.json").write_text(json.dumps(stages, ensure_ascii=False, indent=2), encoding="utf-8")
