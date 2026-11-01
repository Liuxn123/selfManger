import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import time
import unittest
from tests.support import (TestDirectory, begin_ui_tracking, cleanup_ui_test,
                           wait_for_idle)
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QDialog

from selfManger import archives
from selfManger.domain import Resource
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.ui.dialogs import TransferDialog, PlanDialog
from selfManger.ui.theme import setup_theme
from selfManger.ui.window import MainWindow


class ManagementUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        setup_theme(cls.app)

    def setUp(self):
        begin_ui_tracking(self, self.app)
        credential_patch = patch("selfManger.security.SecretStore.get", return_value=None)
        credential_patch.start()
        self.addCleanup(credential_patch.stop)
        self.temporary = TestDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = Store(self.root / "data")
        self.window = MainWindow(self.store)
        self.window.show()
        wait_for_idle(self.app, self.window)

    def wait_jobs(self):
        deadline = time.monotonic() + 15
        while self.window.jobs and time.monotonic() < deadline:
            QTest.qWait(20)
            time.sleep(0.005)
        self.assertFalse(self.window.jobs)

    def tearDown(self):
        self.wait_jobs()
        cleanup_ui_test(self, self.app)
        self.temporary.cleanup()

    def test_simplified_settings_change_destination_without_moving_existing_backup(self):
        self.window.navigation.setCurrentRow(self.window.SETTINGS)
        wait_for_idle(self.app, self.window)
        self.assertFalse(self.window.settings_advanced.isVisible())
        self.assertFalse(self.window.backup_keep.isVisible())
        old_root = self.window.service.backup_root()
        old_root.mkdir(parents=True, exist_ok=True)
        (old_root / "existing.amb").write_bytes(b"fixture-existing-backup")
        destination = self.root / "new-backups"
        destination.mkdir()
        with patch("selfManger.ui.window.QFileDialog.getExistingDirectory", return_value=str(destination)):
            self.window.change_backup_location()
        self.window.save_settings()
        self.assertEqual(self.window.service.backup_root().resolve(), destination.resolve())
        self.assertEqual((old_root / "existing.amb").read_bytes(), b"fixture-existing-backup")
        self.assertIn(str(destination.resolve()), self.window.backup_location.toPlainText())
        self.window.settings_tabs.setCurrentIndex(3)
        self.window.settings_advanced_toggle.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.window.backup_keep.isVisible())
