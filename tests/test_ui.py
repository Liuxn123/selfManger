from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import threading
import unittest
from tests.support import (TestDirectory, begin_ui_tracking, cleanup_ui_test,
                           wait_for_idle)
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit

from selfManger.domain import KINDS, Resource
from selfManger.storage import Store
from selfManger.ui.dialogs import ResourceDialog
from selfManger.ui.theme import setup_theme
from selfManger.ui.window import MainWindow
from selfManger.runtime import TaskContext


class UITests(unittest.TestCase):
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
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")
        self.window = MainWindow(self.store)
        self.window.show()
        wait_for_idle(self.app, self.window)

    def tearDown(self):
        deadline = time.monotonic() + 30
        while (self.window.jobs or self.window.pool.activeThreadCount()) and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(20)
        if self.window.jobs or self.window.pool.activeThreadCount():
            for worker, _, _ in self.window.jobs.values():
                worker.context.cancel.set()
            deadline = time.monotonic() + 10
            while (self.window.jobs or self.window.pool.activeThreadCount()) and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(20)
        self.assertFalse(self.window.jobs, "后台 job 未在 teardown 超时内结束")
        self.assertEqual(self.window.pool.activeThreadCount(), 0, "后台 worker 仍在运行")
        cleanup_ui_test(self, self.app)
        self.temporary.cleanup()

    def test_help_sidebar_names_match_bundled_guide_sections(self):
        from selfManger.ui.help import GuideDialog
        dialog = GuideDialog(self.window)
        for index in range(dialog.sections.count()):
            label = dialog.sections.item(index).text()
            self.assertFalse(dialog.viewer.document().find(f"{index + 1}. {label}").isNull(), label)
        dialog.close()

    def test_all_pages_navigate_and_forms_fit_at_minimum_window_size(self):
        from PySide6.QtGui import QRawFont
        self.assertTrue(QRawFont.fromFont(self.app.font()).supportsCharacter(ord("恢")))
        self.window.resize(960, 660)
        for index in range(self.window.navigation.count()):
            self.window.navigation.setCurrentRow(index)
            self.app.processEvents()
            self.assertEqual(self.window.stack.currentIndex(), index)
            self.assertTrue(self.window.stack.currentWidget().isVisible())
        for kind in ("project", "folder", "script", "vault"):
            dialog = ResourceDialog(self.window, kind)
            dialog.show()
            self.app.processEvents()
            self.assertGreater(dialog.fields["name"].height(), 25)
            self.assertGreater(dialog.fields["name"].width(), 100)
            dialog.close()

    def test_background_task_updates_persistence_on_gui_thread_and_controls_reenable(self):
        resource = Resource("Test project", "project", {"path": str(self.root)})
        self.store.save_resource(resource)
        self.window.refresh_resources()
        self.window.open_safety_resource(resource)
        wait_for_idle(self.app, self.window)
        page = self.window.safety_page
        self.assertTrue(page.primary_button.isEnabled())

        callbacks = []
        release = threading.Event()
        def observe(context):
            if not release.wait(5):
                raise AssertionError("Test did not release observation")
            return {"okay": True}
        identity = self.window.submit(resource, "observe", observe,
            lambda result: callbacks.append((result, QThread.currentThread() == self.app.thread())))
        try:
            deadline = time.monotonic() + 3
            while page.primary_button.isEnabled() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertFalse(page.primary_button.isEnabled())
        finally:
            release.set()
        deadline = time.monotonic() + 10
        while self.window.jobs and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(self.window.jobs)
        wait_for_idle(self.app, self.window)
        self.assertEqual(callbacks, [({"okay": True}, True)])
        self.assertEqual(self.store.tasks()[0]["state"], "success")
        self.assertTrue(page.primary_button.isEnabled())


    def test_management_labels_wrap_paths_are_complete_and_actions_do_not_overlap(self):
        location = str(self.root / ("带有较长名称的资料目录" * 8))
        resource = Resource("本地资料 · " + "需要完整显示的资源名称" * 4, "folder", {"path": location})
        self.store.save_resource(resource)
        self.window.refresh_resources()
        self.window.open_safety_resource(resource)
        page = self.window.safety_page
        wait_for_idle(self.app, self.window)
        for width in (960, 1280):
            self.window.resize(width, 700)
            QTest.qWait(120)
            self.assertEqual(page.restore_button.text(), "查看备份版本")
            self.assertIn(location, page.source_path.text())
            self.assertIn(location, page.source_path.toolTip())
            controls = [page.primary_button, page.restore_button, page.edit_button,
                        page.location_button, page.remove_button]
            for first in controls:
                self.assertGreaterEqual(first.width(), first.sizeHint().width())
                self.assertLessEqual(first.geometry().right(), page.hero.width())
                for second in controls:
                    if first is not second:
                        self.assertFalse(first.geometry().intersects(second.geometry()))
        self.assertTrue(page.source_path.wordWrap())

    def test_resource_activity_route_does_not_mix_resources(self):
        first = Resource("第一个项目", "project", {"path": str(self.root)})
        second = Resource("第二个项目", "project", {"path": str(self.root)})
        for resource in (first, second):
            self.store.save_resource(resource)
            self.store.start_task(resource.id, resource.id, resource.name + " · 检查")
        self.window.refresh_resources()
        self.window.show_resource_activity(first)
        wait_for_idle(self.app, self.window)
        self.assertEqual(self.window.safety_page.detail_name.text(), first.name)
        self.window.show_resource_activity(second)
        wait_for_idle(self.app, self.window)
        self.assertEqual(self.window.safety_page.detail_name.text(), second.name)



    def test_obsidian_external_note_and_atomic_status_edits_refresh_original_documents(self):
        from selfManger.project_workspaces import ProjectWorkspace
        from selfManger.runtime import TaskContext
        from urllib.parse import parse_qs, urlparse
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        project = workspace.create("界面联动", "验证同一份资料", True, TaskContext())
        (workspace.root / "myself/.obsidian").mkdir()
        note = workspace.create_note(project["project_id"], "实验笔记", TaskContext())
        self.store.set_setting("project_workspace", str(workspace.root))
        self.window.navigation.setCurrentRow(self.window.PROJECT)
        page = self.window.project_page
        page.refresh()

        def until(predicate):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                QTest.qWait(30)
                time.sleep(0.005)  # Let Python workers run between Qt's synchronous test waits.
                if predicate() and not self.window.jobs:
                    return
            self.fail("Obsidian UI refresh did not complete: " + str({"rows": page.rows, "notes": page.notes_list.count(), "preview": page.notes_preview.toPlainText(), "tasks": [(t['title'], t['state'], t['result']) for t in self.store.tasks()[:8]]}))

        until(lambda: page.notes_list.count() == 1 and "实验笔记" in page.notes_preview.toPlainText())
        page.documents.setCurrentIndex(1)
        until(lambda: page.edit_note_button.isEnabled())
        with patch("selfManger.ui.projects.QDesktopServices.openUrl", return_value=True) as opened:
            page.edit_note_button.click()
            until(lambda: opened.call_count == 1)
            params = parse_qs(urlparse(opened.call_args[0][0].toString()).query)
            entry = workspace.root / workspace.project(project["project_id"])["vault_entry"]
            self.assertEqual(params["path"], [str(entry / note["relative"])])
        marker = "fixture-external-note-edit-private"
        Path(note["path"]).write_text("# 外部编辑\n" + marker, encoding="utf-8")
        page.refresh_notes_button.click()
        until(lambda: marker in page.notes_preview.toPlainText())
        status = Path(project["directory"]) / "agent/STATUS.md"
        replacement = status.with_suffix(".tmp")
        replacement.write_text(status.read_text(encoding="utf-8").replace("status: active", "status: paused"), encoding="utf-8")
        replacement.replace(status)
        page.refresh()
        until(lambda: page.rows and page.rows[0]["state"] == "paused")
        self.assertIn("status: paused", status.read_text(encoding="utf-8"))
        self.assertFalse(any(marker in t["result"] + t["log"] for t in self.store.tasks()))
        self.window.resize(960, 660)
        self.app.processEvents()
        for control in (page.open_project_button, page.edit_note_button, page.new_note_button):
            self.assertTrue(control.isVisible())
            self.assertGreater(control.width(), 65)
        for control in (page.open_project_button, page.archive_button):
            self.assertTrue(control.isEnabled())

    def test_obsidian_archived_notes_are_visible_without_create_or_inline_edit(self):
        from selfManger.project_workspaces import ProjectWorkspace
        from selfManger.runtime import TaskContext
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        project = workspace.create("历史笔记", "保持历史", False, TaskContext())
        workspace.create_note(project["project_id"], "保留笔记", TaskContext())
        workspace.set_archived(project["project_id"], True, TaskContext())
        self.store.set_setting("project_workspace", str(workspace.root))
        self.window.navigation.setCurrentRow(self.window.PROJECT)
        page = self.window.project_page
        page.filter.setCurrentIndex(1)
        page.refresh()
        deadline = time.monotonic() + 10
        while (self.window.jobs or page.notes_list.count() != 1) and time.monotonic() < deadline:
            QTest.qWait(30)
            time.sleep(0.005)
        page.documents.setCurrentIndex(1)
        deadline = time.monotonic() + 10
        while (self.window.jobs or page.notes_list.count() != 1) and time.monotonic() < deadline:
            QTest.qWait(30)
            time.sleep(0.005)
        self.assertEqual(page.notes_list.count(), 1)
        self.assertFalse(page.new_note_button.isEnabled())
        self.assertFalse(page.inline_note_button.isEnabled())
        self.assertFalse(page.edit_project_info_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
