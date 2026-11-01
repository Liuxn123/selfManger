from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import time
import unittest
from tests.support import TestDirectory, begin_ui_tracking, cleanup_ui_test
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from selfManger.domain import Resource
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.ui.window import MainWindow


class PerformanceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        credential_patch = patch("selfManger.security.SecretStore.get", return_value=None)
        credential_patch.start()
        self.addCleanup(credential_patch.stop)
        self.temporary = TestDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")
        self.window = MainWindow(self.store)
        self.window.show()
        self.until(lambda: not self.window.jobs)

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
            time.sleep(.002)
        self.fail("Background operation did not complete")

    def tearDown(self):
        self.until(lambda: not self.window.jobs)
        cleanup_ui_test(self, self.app)
        self.temporary.cleanup()


    def test_idle_history_timer_is_stopped_and_document_reads_do_not_refresh_all_pages(self):
        self.assertFalse(self.window.timer.isActive())
        with patch.object(self.window, "refresh_resources") as refresh, patch.object(self.window, "refresh_dashboard") as dashboard:
            self.window.submit(None, "读取文档", lambda context: {"text": "fixture"}, persist_result=False)
            self.until(lambda: not self.window.jobs)
            refresh.assert_not_called()
            dashboard.assert_not_called()
        self.assertFalse(self.window.timer.isActive())

    def test_history_loads_only_selected_detail_and_preserves_unchanged_cells(self):
        self.store.start_task("first", "system", "日志测试")
        self.store.append_log("first", "fixture-log")
        self.store.finish_task("first", "success", {"fixture": True})
        with patch.object(self.store, "tasks", side_effect=AssertionError("Full history should not be loaded")):
            self.window.refresh_tasks()
            cell = self.window.task_table.item(0, 0)
            self.window.refresh_tasks()
        self.assertIs(self.window.task_table.item(0, 0), cell)
        self.assertIn("fixture-log", self.window.task_detail.toPlainText())
        self.assertNotIn("log", self.window.task_rows[0])

    def test_project_tabs_reuse_loaded_documents(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        workspace.create("性能验证", "验证页面切换", False, TaskContext())
        self.store.set_setting("project_workspace", str(workspace.root))
        self.window.navigation.setCurrentRow(self.window.PROJECT)
        self.until(lambda: not self.window.jobs)
        page = self.window.project_page
        version, history = page.document_version, len(self.store.tasks())
        for index in (1, 2, 3, 0):
            page.documents.setCurrentIndex(index)
        self.assertEqual(page.document_version, version)
        self.assertFalse(self.window.jobs)
        self.assertEqual(len(self.store.tasks()), history)


if __name__ == "__main__":
    unittest.main()
