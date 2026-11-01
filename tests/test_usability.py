"""Isolated end-to-end coverage for plan actions, imports, notes and self backup."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import unittest
import time
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QTextEdit
from PySide6.QtCore import QTimer

from tests import test_ui_optimization as fixture
from selfManger import archives
from selfManger.domain import UserError
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.task_dialog import UnifiedTaskDialog
from selfManger.ui.presentation import markdown_preserving_linebreaks
from selfManger.workbench import work_root
from selfManger.application import MANAGER_BACKUP_ID, OVERALL_BACKUP_ID
from selfManger.maintenance import backup_health


class UsabilityTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    close_window = fixture.UIOptimizationTests.close_window
    until_idle = fixture.UIOptimizationTests.until_idle
    def workspace(self, window):
        workspace = ProjectWorkspace(self.root / "workspace")
        context = TaskContext()
        workspace.initialize(context)
        project = workspace.create("示例项目", "验证交付", False, context)
        window.store.set_setting("project_workspace", str(workspace.root))
        return workspace, project, context

    def test_plan_actions_select_current_plan_and_share_completion(self):
        window = self.make_window()
        from selfManger.ui.action_page import ItemEditor
        page = window.planning_page
        plan = page.service.save_plan({"title": "长期计划", "notes": "第一行\n第二行"})
        page.plans = page.service.plans()
        editor = ItemEditor(page, day=date.today(), plan_id=plan["id"])
        editor.title.setText("每日行动")
        self.assertEqual(editor.plan.currentData(), plan["id"])
        task = page.service.save_action(editor.values())
        self.assertEqual(len(page.service.actions(plan_id=plan["id"])), 1)
        page.service.complete(task["id"], True)
        self.assertTrue(page.service.actions(plan_id=plan["id"])[0]["done"])
        self.assertNotEqual(page.service.plans()[0]["status"], "done")
        editor.close()

    def test_editing_action_keeps_unresolved_legacy_plan_reference(self):
        window = self.make_window()
        from selfManger.ui.action_page import ItemEditor
        page = window.planning_page
        task = window.store.add_planned_task(
            {"title": "旧计划关联行动", "status": "active", "goal_id": "old1"},
            "personal:legacy-goal-action",
        )
        page.plans = page.service.plans()

        editor = ItemEditor(page, record=task)
        self.assertEqual(editor.plan.currentData(), "old1")
        self.assertIn("未找到", editor.plan.currentText())
        editor.title.setText("编辑后仍保留旧计划关联")
        saved = page.service.save_action(editor.values(), task["id"])

        self.assertEqual(saved["title"], "编辑后仍保留旧计划关联")
        self.assertEqual(saved["goal_id"], "old1")
        editor.close()

        dialog = UnifiedTaskDialog(window, record=saved)
        self.assertEqual(dialog.goal.currentData(), "old1")
        self.assertIn("未找到", dialog.goal.currentText())
        dialog.title.setText("第二种编辑器也保留旧关联")
        saved = page.service.save_action(dialog.values(), task["id"])
        self.assertEqual(saved["goal_id"], "old1")
        dialog.close()

        page.quick_create.load_options()
        page.quick_create.load_document("daily", saved)
        self.assertEqual(page.quick_create.plan.currentData(), "old1")
        self.assertIn("未找到", page.quick_create.plan.currentText())

    def test_direct_import_copies_files_and_preserves_source(self):
        window = self.make_window()
        page = window.knowledge_page
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        source = self.root / "原始资料.txt"
        source.write_text("原始内容\n第二行", encoding="utf-8")
        with patch.object(page, "collect_resource") as collect:
            page.import_files([str(source)])
            self.until_idle(window)
            collect.assert_not_called()
        target = page.index.root / "02-知识/00-收件箱/原始资料.txt"
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(page.quick_filter, "inbox")
        self.assertTrue(page.import_button.isVisible())
        self.assertFalse(page.new_note_button.isVisible())

    def test_note_save_preserves_original_and_refuses_conflicts_and_escape(self):
        window = self.make_window()
        workspace, project, context = self.workspace(window)
        note = workspace.create_note(project["project_id"], "测试笔记", context)
        original = workspace.note_document(project["project_id"], note["relative"])["text"]
        text = original + "第一行\n第二行\n"
        workspace.save_note(project["project_id"], note["relative"], text, original, context)
        with self.assertRaises(UserError):
            workspace.save_note(project["project_id"], note["relative"], "过期覆盖", original, context)
        with self.assertRaises(UserError):
            workspace.save_note(project["project_id"], "笔记/../README.md", text, text, context)
        self.assertEqual(Path(note["path"]).read_text(encoding="utf-8"), text)

    def test_preview_retains_newlines_and_fenced_code(self):
        browser = QTextBrowser()
        text = "第一行\n第二行\n\n```python\na = 1\nb = 2\n```\n"
        rendered = markdown_preserving_linebreaks(text)
        browser.setMarkdown(rendered)
        self.assertIn("第一行\n第二行", browser.toPlainText())
        self.assertIn("```python\na = 1\nb = 2\n```", rendered)
        browser.close()

    def test_project_note_buttons_create_edit_and_preview_saved_text(self):
        window = self.make_window()
        workspace, project, context = self.workspace(window)
        page = window.project_page
        window.navigation.setCurrentRow(window.PROJECT)
        page.refresh()
        self.until_idle(window)
        page.table.selectRow(0)
        self.until_idle(window)
        page.documents.setCurrentIndex(1)
        original_exec = QDialog.exec
        edited = []
        def write_note(dialog):
            editor = dialog.findChild(QTextEdit)
            edited.append(editor is not None)
            if editor:
                editor.setPlainText(editor.toPlainText() + "第一行\n第二行\n")
                QTimer.singleShot(0, lambda: dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click())
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            timer.timeout.connect(dialog.reject)
            timer.start(6000)
            result = original_exec(dialog)
            timer.stop()
            return result
        with patch("selfManger.ui.projects.QInputDialog.getText", return_value=("界面新建笔记", True)), patch.object(QDialog, "exec", write_note), patch("selfManger.ui.window.QMessageBox.information") as info, patch("selfManger.ui.window.QMessageBox.warning") as warning:
            page.new_note_button.click()
            self.until_idle(window)
            info.assert_not_called()
            warning.assert_not_called()
        self.assertEqual(edited, [True])
        report = workspace.note_document(project["project_id"], "笔记/界面新建笔记.md")
        self.assertIn("第一行\n第二行", report["text"])
        self.assertEqual(page.notes_list.currentItem().text(), "界面新建笔记")
        self.assertIn("第一行\n第二行", page.notes_preview.toPlainText())

    def test_database_backup_is_decryptable_complete_and_excludes_other_files(self):
        window = self.make_window()
        window.store.set_setting("test_marker", "可恢复")
        (window.store.root / "unrelated-secret.txt").write_text("不要入包", encoding="utf-8")
        context = TaskContext()
        report = window.service.backup(window.service.manager_resource(), context, "test-only-password")
        self.assertTrue(Path(report["archive"]).is_relative_to(window.service.backup_root()))
        with archives.open_archive(Path(report["archive"]), "test-only-password", context) as archive:
            manifest = archives.verify_open(archive, context)
            self.assertIn("files/manager.sqlite3", archive.namelist())
            self.assertNotIn("files/unrelated-secret.txt", archive.namelist())
            restored = self.root / "restored.sqlite"
            restored.write_bytes(archive.read("files/manager.sqlite3"))
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNotNone(db.execute("SELECT value FROM settings WHERE key='test_marker'").fetchone())
        self.assertTrue(report["verified"])
        self.assertTrue(backup_health(window.store, window.service.manager_resource(), window.service.backup_root())["verified"])

    def test_builtin_database_uses_normal_list_and_cannot_be_removed_or_reconfigured(self):
        window = self.make_window()
        page = window.safety_page
        window.navigation.setCurrentRow(window.SAFETY)
        deadline = time.monotonic() + 10
        while page._worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(page._worker)
        builtin = window.service.manager_resource()
        page.show_overview(builtin)
        self.assertEqual(page.resources_list.count(), len(window.store.resources()) + 2)
        self.assertEqual(page._selected_entry()["resource"].id, MANAGER_BACKUP_ID)
        self.assertIn("内置", page.resources_list.currentItem().text())
        self.assertFalse(hasattr(page, "manager_backup_button"))
        self.assertFalse(page.remove_button.isEnabled())
        self.assertFalse(page.edit_button.isEnabled())
        self.assertTrue(page.primary_button.isEnabled())
        self.assertIn(str(window.store.path), page.source_path.text())
        with patch.object(window, "perform") as perform:
            page.primary_button.click()
            perform.assert_called_once_with(builtin, "backup", "")
        with patch.object(window.service, "remove_resource") as remove, patch.object(window.service, "save_resource") as save, patch("selfManger.ui.window.QMessageBox.question") as question:
            page._remove_selected()
            window.remove_resource(builtin)
            window.edit_resource(builtin)
            remove.assert_not_called()
            save.assert_not_called()
            question.assert_not_called()
        self.assertEqual(sum(resource.id == MANAGER_BACKUP_ID for resource in window.service.backup_resources()), 1)
        self.assertEqual(sum(resource.id == OVERALL_BACKUP_ID for resource in window.service.backup_resources()), 1)
