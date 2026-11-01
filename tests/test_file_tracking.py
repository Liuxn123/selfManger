"""Partial file-tracking scans must not be reported as deletions."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from selfManger.file_tracking import FileTrackingService, MAX_HISTORY, TRACKING_KEY, snapshot
from selfManger.ui.file_tracking import FileTrackingPage
from tests.support import begin_ui_tracking, register_ui_cleanup


class FileTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        register_ui_cleanup(self, self.app)
        self.directory = Path(tempfile.mkdtemp(prefix="selfmanger-file-tracking-"))
        self.path = self.directory / "locked.md"
        self.path.write_text("synthetic", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_unreadable_file_marks_snapshot_incomplete(self):
        original_is_file = Path.is_file
        original_stat = Path.stat

        def is_file(path):
            if path == self.path:
                return True
            return original_is_file(path)

        def stat(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError("synthetic locked file")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "is_file", is_file), patch.object(Path, "stat", stat):
            files, _directories, truncated = snapshot(str(self.directory), Mock(is_set=lambda: False))

        self.assertEqual(files, {})
        self.assertTrue(truncated)

    def test_root_walk_error_marks_snapshot_incomplete(self):
        with patch("selfManger.file_tracking.os.walk", side_effect=PermissionError("synthetic root failure")):
            files, directories, truncated = snapshot(str(self.directory), Mock(is_set=lambda: False))
        self.assertEqual(files, {})
        self.assertEqual(directories, [])
        self.assertTrue(truncated)

    def test_service_compares_snapshots_in_stable_action_order(self):
        service = FileTrackingService(Mock())
        previous = {"deleted.md": [1, 1], "changed.md": [2, 2]}
        current = {"added.md": [3, 3], "changed.md": [4, 2]}

        self.assertEqual(service.changes(previous, current), [
            ("新增", "added.md"), ("删除", "deleted.md"), ("修改", "changed.md")
        ])

    def test_service_loads_only_object_settings_and_caps_history(self):
        store = Mock()
        store.setting.return_value = {"roots": [], "events": [], "enabled": True}
        service = FileTrackingService(store)
        self.assertTrue(service.load()["enabled"])
        store.setting.return_value = []
        self.assertEqual(service.load(), {})

        events = [{"n": n} for n in range(MAX_HISTORY + 10)]
        service.save([], events, False)
        store.set_setting.assert_called_once_with(TRACKING_KEY, {
            "roots": [], "events": events[-MAX_HISTORY:], "enabled": False,
        })

    def test_partial_scan_is_disclosed_in_status(self):
        page = self.make_page({"id": "synthetic", "name": "synthetic", "path": str(self.directory),
                               "snapshot": {}, "file_count": 0, "initialized": True, "truncated": True})
        self.assertIn("文件/目录无法访问", page.status_label.text())
        self.assertIn("不会据此记录文件删除", page.status_label.text())

    def test_partial_scan_keeps_old_snapshot_without_false_deletions(self):
        previous = {str(self.path): [self.path.stat().st_size, self.path.stat().st_mtime_ns]}
        root = {"id": "synthetic", "name": "synthetic", "path": str(self.directory),
                "snapshot": previous.copy(), "file_count": 1, "initialized": True}
        page = self.make_page(root)

        page._apply_scan([("synthetic", str(self.directory), previous, {}, [], True)])

        self.assertEqual(page.events, [])
        self.assertEqual(root["snapshot"], previous)
        self.assertTrue(root["truncated"])

    def test_failed_background_scan_keeps_old_snapshot_and_shows_retry_hint(self):
        old_snapshot = {str(self.path): [9, 10]}
        root = {"id": "synthetic", "name": "synthetic", "path": str(self.directory),
                "snapshot": old_snapshot.copy(), "file_count": 1, "initialized": True}
        page = self.make_page(root)
        page._busy = True
        page._scan_finished(Mock(), None)
        self.assertEqual(root["snapshot"], old_snapshot)
        self.assertIn("最近一次扫描未完成", page.status_label.text())
        self.assertIn("立即扫描", page.status_label.text())

    def make_page(self, root):
        store = Mock()
        store.setting.return_value = {
            "roots": [root],
            "events": [], "enabled": False,
        }
        page = FileTrackingPage(SimpleNamespace(store=store))
        self.addCleanup(page.close)
        return page


if __name__ == "__main__":
    unittest.main()
