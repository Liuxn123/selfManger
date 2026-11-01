"""Failure, retry, concurrency and crash regressions from the project audit."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from selfManger.action_center import ActionCenter
from selfManger.domain import Cancelled, UserError
from selfManger.project_task_documents import END, import_legacy_project_task_documents
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext, run_process
from selfManger.storage import Store
from selfManger.ui.tasks import Worker
from tests.support import TestDirectory
from tests.personal_markdown_fixtures import attach_legacy_document, legacy_action_record
from tests import test_ui_optimization as ui_fixture


class AuditDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.store.set_setting("workbench_root", str(self.root / "documents"))
        self.center = ActionCenter(self.store)

    def action_document(self):
        task = self.center.save_action({"title": "original"})
        attach_legacy_document(self.store, "action", task["id"], "daily", task)
        return task, Path(self.center.documents.path("action", task["id"]))

    def write_legacy_journal(self, mapping, original, replacement):
        directory = self.store.root / ".markdown-recovery"
        directory.mkdir(parents=True, exist_ok=True)
        journal = {key: mapping[key] for key in ("entity_type", "entity_id", "root_path", "relative_path")}
        journal.update(original=original, replacement=replacement)
        path = directory / (uuid4().hex + ".json")
        path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        return path

    def test_non_object_evidence_fails_closed_to_empty_mapping(self):
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO evidence(key,document) VALUES (?,?)",
                       ("fixture-invalid-evidence", '["not", "a", "report"]'))

        self.assertEqual(self.store.evidence("fixture-invalid-evidence"), {})

    def test_action_status_uses_sqlite_and_leaves_legacy_file_unchanged(self):
        task, path = self.action_document()
        original = path.read_bytes()
        self.center.complete(task["id"], True)
        self.assertEqual(self.store.planned_task(task["id"])["status"], "done")
        self.assertEqual(path.read_bytes(), original)
        self.center.documents.migrate_legacy_documents()
        text = path.read_text(encoding="utf-8") + "\n后来在 Obsidian 修改"
        path.write_text(text, encoding="utf-8")
        self.center.documents.migrate_legacy_documents()
        self.assertEqual(self.store.planned_task(task["id"])["status"], "done")
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_database_commit_failure_keeps_legacy_file_and_record(self):
        task, path = self.action_document()
        original = path.read_bytes()
        connect = sqlite3.connect
        failures = []
        class FailCommit(sqlite3.Connection):
            def commit(self):
                if self.in_transaction and not failures:
                    failures.append(True)
                    raise OSError("fixture commit failure")
                super().commit()
        with patch("selfManger.storage.sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=FailCommit)):
            with self.assertRaises(OSError):
                self.center.complete(task["id"], True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.planned_task(task["id"])["status"], "planned")

    def test_plan_review_and_repeating_checkin_do_not_write_legacy_files(self):
        plan = self.center.save_plan({"title": "plan"})
        plan_path = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        review = self.center.save_review({"title": "review", "body": "original", "period_type": "weekly",
                                          "period_key": "2026-W43", "start_date": "2026-10-19", "end_date": "2026-10-25"})
        review_path = attach_legacy_document(self.store, "review", review["id"], "weekly-review", review)
        from datetime import date
        day = date.today().isoformat()
        repeat = self.center.save_action({"title": "repeat", "repeat": "daily", "due_date": day})
        repeat_path = attach_legacy_document(self.store, "action", repeat["id"], "recurring",
                                             legacy_action_record(self.center, repeat["id"], repeat))
        originals = [path.read_bytes() for path in (plan_path, review_path, repeat_path)]
        with patch("selfManger.workbench.MarkdownFiles.write", side_effect=OSError("fixture")):
            self.center.save_plan({**plan, "title": "changed", "_expected": plan}, plan["id"])
            self.center.save_review({**review, "body": "changed", "_expected": review}, review["id"])
            self.center.complete(repeat["id"], True, day)
        self.assertEqual(self.center.plans()[0]["title"], "changed")
        self.assertEqual(self.center.reviews()[0]["body"], "changed")
        self.assertEqual([path.read_bytes() for path in (plan_path, review_path, repeat_path)], originals)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM action_occurrences").fetchone()[0], 1)

    def test_concurrent_expected_version_rejects_one_writer(self):
        plan = self.center.save_plan({"title": "original"})
        barrier = threading.Barrier(2)
        results = []
        def edit(title):
            barrier.wait(timeout=5)
            try:
                self.center.save_plan({**plan, "title": title, "_expected": plan}, plan["id"])
                results.append("saved")
            except UserError:
                results.append("conflict")
        threads = [threading.Thread(target=edit, args=(name,)) for name in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_future_database_version_is_rejected_without_writing(self):
        with self.store.connect() as db:
            db.execute("PRAGMA user_version=99")
        before = self.store.path.read_bytes()
        with self.assertRaises(UserError):
            Store(self.store.root)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_project_task_import_is_one_time_and_preserves_source(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        project = workspace.create("audit", "fixture", False, TaskContext())
        path = Path(project["directory"]) / "agent/TASKS-MANUAL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = "| new | same title | planned | - | - | task | - | - | - |\n"
        from selfManger.project_task_documents import START, HEADER
        path.write_text(START + "\n" + HEADER + "\n" + row * 2 + END + "\n", encoding="utf-8")
        source_before = path.read_bytes()
        self.assertEqual(import_legacy_project_task_documents(self.store, workspace, project["project_id"])["imported"], 2)
        ids = {task["id"] for task in self.store.planned_tasks(project_id=project["project_id"])}
        self.assertEqual(len(ids), 2)
        self.assertEqual(path.read_bytes(), source_before)
        path.write_text(path.read_text(encoding="utf-8").replace("same title", "edited after migration"), encoding="utf-8")
        self.assertTrue(import_legacy_project_task_documents(self.store, workspace, project["project_id"])["already_imported"])
        self.assertEqual({task["id"] for task in self.store.planned_tasks(project_id=project["project_id"])}, ids)

    def test_legacy_pending_document_journal_is_recovered_on_open(self):
        task, path = self.action_document()
        original = path.read_bytes()
        mapping = self.center.documents._mapping("action", task["id"])
        self.write_legacy_journal(mapping, path.read_text(encoding="utf-8"), "replacement")
        path.write_text("replacement", encoding="utf-8")
        reopened = Store(self.store.root)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(reopened.planned_task(task["id"])["status"], "planned")

    def test_recovery_does_not_overwrite_external_edit(self):
        task, path = self.action_document()
        mapping = self.center.documents._mapping("action", task["id"])
        self.write_legacy_journal(mapping, path.read_text(encoding="utf-8"), "replacement")
        path.write_text("external editor", encoding="utf-8")
        with self.assertRaises(UserError):
            Store(self.store.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "external editor")
        self.assertTrue(list((self.store.root / ".markdown-recovery").glob("*.json")))

class WorkerTerminalTests(unittest.TestCase):
    def test_transient_and_persistent_history_failure_never_emit_success(self):
        for persistent in (False, True):
            calls, signals = [], []
            def finish(*args):
                calls.append(args)
                if persistent or len(calls) == 1:
                    raise OSError("fixture history failure")
            worker = Worker("fixture", SimpleNamespace(append_log=lambda *a: None, finish_task=finish), lambda ctx: {"ok": True})
            worker.signals.finished.connect(lambda identity, outcome, state: signals.append((outcome, state)))
            worker.run()
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0][1], "failed")
            self.assertIn("error", signals[0][0])


class CancelProcessTests(unittest.TestCase):
    def test_cancel_terminates_owned_process_promptly(self):
        context = TaskContext()
        timer = threading.Timer(.3, context.cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                run_process([sys.executable, "-c", "import time; time.sleep(60)"], context, timeout=10, kill_tree=True)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 5)

    def test_cancel_stops_owned_descendant_and_output_is_bounded(self):
        import psutil
        with TestDirectory() as directory:
            marker = Path(directory) / "child.pid"
            code = ("import subprocess,sys,time; from pathlib import Path; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                    f"Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)")
            context = TaskContext()
            def cancel_after_child():
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                context.cancel.set()
            monitor = threading.Thread(target=cancel_after_child)
            monitor.start()
            try:
                with self.assertRaises(Cancelled):
                    run_process([sys.executable, "-c", code], context, timeout=10, kill_tree=True)
            finally:
                monitor.join(6)
            self.assertTrue(marker.exists())
            pid = int(marker.read_text())
            try:
                # POSIX can retain an already terminated orphan until init reaps it.
                self.assertEqual(psutil.Process(pid).status(), psutil.STATUS_ZOMBIE)
            except psutil.NoSuchProcess:
                pass
        with self.assertRaises(UserError):
            run_process([sys.executable, "-c", "import sys; sys.stdout.write('x'*9000000)"],
                        TaskContext(), timeout=10, kill_tree=True)



class KnowledgeResetTests(unittest.TestCase):
    setUpClass = classmethod(ui_fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = ui_fixture.UIOptimizationTests.setUp
    make_window = ui_fixture.UIOptimizationTests.make_window
    until_idle = ui_fixture.UIOptimizationTests.until_idle
    close_window = ui_fixture.UIOptimizationTests.close_window

    def test_clear_vault_discards_old_rows_and_rejects_stale_open(self):
        window = self.make_window()
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        page = window.knowledge_page
        note = next(entry for entry in page.items if entry["kind"] == "note")
        window.store.set_setting("obsidian_vault_root", "")
        window.store.set_setting("project_workspace", "")
        page.refresh()
        self.assertIsNone(page.index)
        self.assertEqual(page.items, [])
        self.assertEqual(page.file_list.rowCount(), 0)
        self.assertFalse(page.open_button.isEnabled())
        with patch("selfManger.ui.knowledge.QDesktopServices.openUrl") as opened:
            page.open_entry(note)
            opened.assert_not_called()

    def test_old_scan_callback_cannot_repopulate_cleared_vault(self):
        window = self.make_window()
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        page = window.knowledge_page
        old_items = list(page.items)
        callbacks = []
        def delay(*args, **kwargs):
            callbacks.append(args[3])
            return "delayed-scan"
        with patch.object(window, "submit", side_effect=delay):
            page.refresh()
        self.assertTrue(page.loading)
        window.store.set_setting("obsidian_vault_root", "")
        window.store.set_setting("project_workspace", "")
        page.refresh()
        callbacks[0]({"items": old_items, "folders": [""]})
        self.assertFalse(page.loading)
        self.assertIsNone(page.index)
        self.assertEqual(page.items, [])
