"""Behavioral checks for navigation without UI-thread disk writes or project scans."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest
from tests.support import TestDirectory, begin_ui_tracking, cleanup_ui_test, register_ui_cleanup
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from selfManger.project_workspaces import ProjectWorkspace
from selfManger.project_details import ProjectDetailReader
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.ui.window import MainWindow
from selfManger.ui import projects


class NavigationResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        self.temporary = TestDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")
        self.workspace = ProjectWorkspace(self.root / "workspace")
        self.workspace.initialize(TaskContext())
        self.workspace.create("性能项目", "验证首次点击与刷新", False, TaskContext())
        self.project = self.workspace.registry()["projects"][0]
        self.store.set_setting("project_workspace", str(self.workspace.root))
        self.store.set_setting("workbench_root", str(self.root / "workbench"))
        credential_patch = patch("selfManger.security.SecretStore.get", return_value=None)
        credential_patch.start()
        self.addCleanup(credential_patch.stop)
        self.window = None
        register_ui_cleanup(self, self.app)

    def open_window(self):
        self.window = MainWindow(self.store)
        self.window.show()
        self.addCleanup(self.close_window)
        self.settle()
        return self.window

    def settle(self):
        deadline = time.monotonic() + 15
        quiet = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            busy = (self.window.jobs or self.window.pool.activeThreadCount()
                    or self.window.project_page.project_detail_timer.isActive()
                    or self.window.today_page.reload_timer.isActive())
            if busy:
                quiet = None
            elif quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > .2:
                return
            time.sleep(.002)
        self.fail("Isolated navigation did not settle")

    def close_window(self):
        self.settle()
        cleanup_ui_test(self, self.app)

    def test_read_job_finishes_while_another_connection_holds_write_lock(self):
        window = self.open_window()
        history = len(self.store.tasks())
        outcomes = []
        with self.store.connect() as writer:
            writer.execute("BEGIN IMMEDIATE")
            with patch.object(self.store, "start_task", side_effect=AssertionError("Read submitted a DB write")), \
                 patch.object(window, "refresh_common_password", side_effect=AssertionError("Read refreshed credentials")):
                window.submit(None, "隔离页面读取", lambda context: {"ok": True}, outcomes.append,
                              persist_result=False, record_history=False)
                # A separate draft autosave may legitimately wait for this
                # writer. Observe the read callback, not all unrelated workers.
                deadline = time.monotonic() + 3
                while not outcomes and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(outcomes, [{"ok": True}])
        self.settle()
        self.assertEqual(outcomes, [{"ok": True}])
        self.assertEqual(len(self.store.tasks()), history)

    def test_navigation_does_not_restart_migration_or_write_read_history(self):
        window = self.open_window()
        history = len(self.store.tasks())
        with patch.object(window, "import_legacy_tasks") as migrate:
            for row in (window.PROJECT, window.TODAY, window.PLAN, window.PROJECT, window.TODAY):
                window.navigation.setCurrentRow(row)
                self.settle()
        migrate.assert_not_called()
        self.assertEqual(len(self.store.tasks()), history)

    def test_first_project_discovery_parsing_and_recent_use_run_off_ui_thread(self):
        main_thread = threading.get_ident()
        calls = []
        def delayed(name, original):
            def run(*args, **kwargs):
                calls.append((name, threading.get_ident()))
                time.sleep(.08)
                return original(*args, **kwargs)
            return run
        with patch.object(projects, "project_git_repositories", delayed("git", projects.project_git_repositories)), \
             patch.object(ProjectDetailReader, "read", delayed("parse", ProjectDetailReader.read)), \
             patch.object(ProjectWorkspace, "record_project_use", delayed("recent", ProjectWorkspace.record_project_use)):
            window = self.open_window()
            window.navigation.setCurrentRow(window.PROJECT)
            self.settle()
            # Updating buttons repeatedly must not rediscover the filesystem.
            count = len(calls)
            for _ in range(5):
                window.project_page.update_selection_actions()
            self.assertEqual(len(calls), count)
            self.assertNotIn("git", {name for name, _ in calls})
            with patch.object(window.project_page, "_inspect_git_status_ready") as ready:
                window.project_page.inspect_git_status()
                self.settle()
                ready.assert_called_once()
        self.assertEqual({name for name, _ in calls}, {"git", "parse", "recent"})
        self.assertTrue(all(thread != main_thread for _, thread in calls), calls)
        self.assertEqual(window.project_page.project_name.text(), self.project["name"])

    def test_read_snapshot_reuses_registry_but_refresh_observes_external_change(self):
        original = ProjectWorkspace.read
        reads = []
        def read(path):
            if Path(path) == self.workspace.registry_path:
                reads.append(path)
            return original(path)
        with patch.object(ProjectWorkspace, "read", staticmethod(read)):
            snapshot = self.workspace.read_snapshot()
            for _ in range(3):
                snapshot.document(self.project["id"], "agent/STATUS.md")
                snapshot.milestones_document(self.project["id"])
        self.assertEqual(len(reads), 1)
        window = self.open_window()
        path = self.workspace.root / self.project["path"] / "agent/STATUS.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## 当前进展\n外部编辑的新进展\n", encoding="utf-8")
        window.project_page.refresh()
        self.settle()
        self.assertIn("外部编辑的新进展", window.project_page.status_text)


if __name__ == "__main__":
    unittest.main()
