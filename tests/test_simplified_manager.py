import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import unittest
from tests.support import TestDirectory, begin_ui_tracking, cleanup_ui_test
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser
from selfManger import archives
from selfManger.domain import Resource, UserError
from selfManger.runtime import TaskContext
from selfManger.snapshots import snapshot_file
from selfManger.storage import Store
from selfManger.ui.projects import ProjectGitStatusDialog as ProjectPageGitStatusDialog, ProjectLogView
from selfManger.ui.project_git_dialog import ProjectGitStatusDialog
from selfManger.ui.window import MainWindow
from selfManger.application import ApplicationService
from selfManger.work_summaries import MAX_SUMMARY_BYTES, LEGACY_WORK_SUMMARY_SOURCE


class SimplifiedManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        credential_patch = patch("selfManger.security.SecretStore.get", return_value=None)
        credential_patch.start()
        self.addCleanup(credential_patch.stop)
        save_patch = patch("selfManger.security.SecretStore.save")
        save_patch.start()
        self.addCleanup(save_patch.stop)
        self.temp = TestDirectory()
        self.root = Path(self.temp.name).resolve()
        self.context = TaskContext()

    def tearDown(self):
        cleanup_ui_test(self, self.app)
        self.temp.cleanup()



    def test_live_log_snapshot_has_fixed_boundary_and_does_not_follow_later_appends(self):
        path = self.root / "rollout.jsonl"
        path.write_bytes(b'{"first":1}\n{"partial":')
        with snapshot_file(path, self.context, append_log=True) as (copy, database):
            with path.open("ab") as handle:
                handle.write(b'2}\n{"later":3}\n')
            self.assertEqual(copy.read_bytes(), b'{"first":1}\n')
            self.assertFalse(database)

    def test_standalone_sqlite_fallback_preserves_data_when_source_cannot_create_shm(self):
        import sqlite3
        path = self.root / "standalone.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE facts (id INTEGER)")
            db.execute("INSERT INTO facts VALUES (42)")
            db.commit()
        connect = sqlite3.connect
        def fail_source(database, *args, **kwargs):
            if database == path.as_uri() + "?mode=ro":
                raise sqlite3.OperationalError("unable to open database file")
            return connect(database, *args, **kwargs)
        with patch("selfManger.snapshots.sqlite3.connect", fail_source):
            with snapshot_file(path, self.context) as (copy, database):
                self.assertTrue(database)
                with closing(connect(copy)) as db:
                    self.assertEqual(db.execute("SELECT id FROM facts").fetchall(), [(42,)])
        # An existing WAL must never be ignored by the standalone fallback.
        path.with_name(path.name + "-wal").write_bytes(b"fixture log")
        with patch("selfManger.snapshots.sqlite3.connect", fail_source), self.assertRaisesRegex(UserError, "日志文件"):
            with snapshot_file(path, self.context):
                pass




    def test_project_logs_show_latest_entry_and_keep_original_available(self):
        browser = QTextBrowser()
        widget = ProjectLogView(browser)
        text = "# HANDOFF\nTechnical rules\n## 2026-10-01 — old\nold result\n## 2026-10-05 — new\nlatest result\n"
        widget.set_text(text)
        self.assertIn("latest result", browser.toPlainText())
        self.assertNotIn("Technical rules", browser.toPlainText())
        widget.all.setChecked(True)
        self.assertIn("Technical rules", browser.toPlainText())
        widget.deleteLater()

    def test_git_status_dialog_renders_report_and_routes_controls_to_page_callback(self):
        actions = []
        report = {
            "ok": True,
            "branch": "main",
            "tracking": "origin/main",
            "ahead": 2,
            "behind": 1,
            "change_count": 2,
            "staged": 1,
            "unstaged": 1,
            "untracked": 1,
            "files": [
                {"path": "src/ready.py", "state": "M", "staged": True, "unstaged": False},
                {"path": "src/new.py", "state": "??", "staged": False, "unstaged": True},
            ],
            "commit": {"hash": "abc123", "subject": "fixture change", "time": "2026-10-31"},
            "remotes": [
                ("origin", "fetch", "https://example.invalid/repo.git"),
                ("origin", "push", "https://example.invalid/repo.git"),
            ],
            "summary": "工作区有变更",
        }
        dialog = ProjectGitStatusDialog(None, "项目", "repo", report,
                                        lambda *action: actions.append(action))
        self.assertEqual(dialog.branch_label.text(), "分支  main")
        self.assertIs(ProjectPageGitStatusDialog, ProjectGitStatusDialog)
        self.assertEqual(dialog.files_tree.topLevelItemCount(), 2)
        unstaged = dialog.files_tree.topLevelItem(1)
        self.assertEqual(unstaged.childCount(), 1)
        file_action = dialog.files_tree.itemWidget(unstaged.child(0), 2)
        file_action.click()
        self.assertEqual(actions.pop(), ("stage_file", "", "src/new.py"))
        self.assertTrue(dialog.pull_button.isEnabled())
        self.assertTrue(dialog.push_button.isEnabled())
        self.assertFalse(dialog.commit_button.isEnabled())
        dialog.commit_input.setText("fixture commit")
        dialog.commit_button.click()
        self.assertEqual(actions.pop(), ("commit", "fixture commit"))
        dialog.stage_button.click()
        self.assertEqual(actions.pop(), ("stage", ""))
        with patch("selfManger.ui.project_git_dialog.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            dialog.confirm_remote("push")
        self.assertEqual(actions, [("push", "")])
        dialog.set_busy(True)
        self.assertFalse(dialog.push_button.isEnabled())
        dialog.set_busy(False)
        dialog.close()
        dialog.deleteLater()

    def test_single_session_password_applies_to_all_resources_without_disk_credentials(self):
        store = Store(self.root / "data")
        window = MainWindow(store)
        a, b = Resource("A", "agent", {}), Resource("B", "vault", {})
        self.assertEqual(window.resolve_password(a, "fixture-password", True), "fixture-password")
        self.assertEqual(window.resolve_password(b, ""), "fixture-password")
        self.assertEqual(window.resolve_password(b, "old-backup-password"), "old-backup-password")
        self.assertNotIn("fixture-password", store.path.read_bytes().decode(errors="ignore"))
        from tests.support import wait_for_idle
        wait_for_idle(self.app, window)
        window.close()
        self.assertEqual(window.service.common_password, "")

    def test_work_summary_is_persistent_append_only_user_content(self):
        legacy_path = self.root / "data" / "work-summary.md"
        legacy_path.parent.mkdir()
        legacy_path.write_bytes(b"# Summary\r\n\r\nEntry\r\n")
        original = legacy_path.read_bytes()
        service = ApplicationService(Store(self.root / "data"))
        imported = service.work_summaries()
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["body"], "# Summary\n\nEntry\n")
        self.assertEqual(imported[0]["source_ref"], LEGACY_WORK_SUMMARY_SOURCE)
        service.append_work_summary("今天完成备份")
        second = service.append_work_summary("下一步验证恢复")
        self.assertEqual(len(second), 3)
        self.assertEqual(legacy_path.read_bytes(), original)
        reopened = ApplicationService(Store(self.root / "data"))
        self.assertEqual(len(reopened.work_summaries()), 3)
        with self.assertRaises(UserError):
            service.append_work_summary(" ")

    def test_work_summary_read_is_bounded_and_reports_invalid_content(self):
        service = ApplicationService(Store(self.root / "data"))
        self.assertEqual(service.work_summaries(), [])
        path = self.root / "data" / "work-summary.md"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"# Summary\r\n\r\nEntry\r\n")
        self.assertEqual(service.work_summaries()[0]["body"], "# Summary\n\nEntry\n")
        path.write_text("externally changed", encoding="utf-8")
        self.assertEqual(service.work_summaries()[0]["body"], "# Summary\n\nEntry\n")
        invalid_data = self.root / "invalid-data"
        invalid_data.mkdir()
        invalid_path = invalid_data / "work-summary.md"
        invalid_path.write_bytes(b"\xff")
        with self.assertRaisesRegex(UserError, "UTF-8"):
            ApplicationService(Store(invalid_data)).work_summaries()
        oversize_data = self.root / "oversize-data"
        oversize_data.mkdir()
        (oversize_data / "work-summary.md").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(UserError, "2 MiB"):
            ApplicationService(Store(oversize_data)).work_summaries()
        self.assertEqual(MAX_SUMMARY_BYTES, 2 * 1024 * 1024)

    def test_window_build_survives_invalid_work_summary_path(self):
        data = self.root / "data"
        (data / "work-summary.md").mkdir(parents=True)
        window = MainWindow(Store(data))
        self.assertIn("暂时无法读取", window.summary_history.toPlainText())
        window.close()

    def test_wrong_migration_password_never_marks_checks_passed(self):
        source = self.root / "project"
        source.mkdir()
        (source / "file.txt").write_text("fixture")
        resource = Resource("fixture", "project", {"path": str(source)})
        report = archives.create_archive(resource, self.root / "backups", "correct-password", self.context)
        window = MainWindow(Store(self.root / "data"))
        from PySide6.QtTest import QTest
        with patch.object(window, "password_input", return_value=("wrong-password", False)), patch("selfManger.ui.window.QMessageBox.warning"):
            window.import_backup(report["archive"])
            for _ in range(500):
                if not window.jobs:
                    break
                QTest.qWait(10)
        self.assertFalse(window.jobs)
        self.assertTrue(window.migration_checklist.table.item(1, 1).text().startswith("未通过"))
        self.assertFalse(window.migration_checklist.table.item(4, 1).text().startswith("已恢复"))
        window.close()


if __name__ == "__main__":
    unittest.main()
