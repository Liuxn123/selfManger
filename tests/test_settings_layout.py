"""Settings grouping, isolated persistence, and responsive theme coverage."""
import os
import json
import unittest
from tests.support import evidence_root, start_visual_timing, visual
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QFrame, QPushButton, QLineEdit, QMessageBox

from tests import test_ui_optimization as fixture
from selfManger.knowledge import KnowledgeIndex
from selfManger.ui.theme import setup_theme, apply_theme


class SettingsLayoutTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    until_idle = fixture.UIOptimizationTests.until_idle
    close_window = fixture.UIOptimizationTests.close_window

    def open_settings(self):
        window = self.make_window()
        window.navigation.setCurrentRow(window.SETTINGS)
        timer = getattr(self, "_visual_timer", None)
        if timer:
            timer.run("processEvents", self.app.processEvents)
        else:
            self.app.processEvents()
        return window

    def test_groups_preserve_path_drafts_and_show_only_related_cards(self):
        window = self.open_settings()
        self.assertEqual([window.settings_tabs.tabText(i) for i in range(4)],
                         ["通用", "资料位置", "备份与安全", "高级维护"])
        self.assertFalse(window.workbench_location.isVisible())
        window.settings_tabs.setCurrentIndex(1)
        self.assertTrue(window.workbench_location.isVisible())
        self.assertTrue(window.obsidian_vault_root.isVisible())
        self.assertFalse(window.backup_path.isVisible())
        window.obsidian_vault_root.setText(str(self.root / "draft-vault"))
        window.settings_tabs.setCurrentIndex(2)
        self.assertTrue(window.backup_path.isVisible())
        self.assertFalse(window.obsidian_vault_root.isVisible())
        window.settings_tabs.setCurrentIndex(1)
        self.assertTrue(window.obsidian_vault_root.text().endswith("draft-vault"))
        self.assertIn("尚未保存", window.settings_obsidian_status.text())
        self.assertNotIn("draft-vault", window.settings_obsidian_status.text())

    def test_backup_browse_requires_save_and_does_not_change_retention(self):
        window = self.open_settings()
        old_root = window.service.backup_root()
        old_root.mkdir(parents=True, exist_ok=True)
        original = old_root / "old.amb"
        original.write_bytes(b"existing archive")
        original_keep = window.store.setting("backup_keep", 10)
        window.backup_keep.setValue(23)
        destination = self.root / "new-backups"
        with patch("selfManger.ui.window.QFileDialog.getExistingDirectory", return_value=str(destination)):
            window.change_backup_location()
        self.assertEqual(window.service.backup_root(), old_root)
        self.assertIn("尚未保存", window.settings_backup_feedback.text())
        self.assertNotIn(str(destination), window.settings_backup_status.text())
        window.save_settings()
        self.assertEqual(window.service.backup_root(), destination.resolve())
        self.assertEqual(window.store.setting("backup_keep", 10), original_keep)
        self.assertEqual(original.read_bytes(), b"existing archive")
        self.assertFalse(destination.exists())

    def test_retention_save_does_not_apply_unsaved_backup_path(self):
        window = self.open_settings()
        old_root = window.service.backup_root()
        window.backup_path.setText(str(self.root / "unsaved-backups"))
        window.backup_keep.setValue(17)
        window.save_backup_retention()
        self.assertEqual(window.store.setting("backup_keep"), 17)
        self.assertEqual(window.service.backup_root(), old_root)
        self.assertIn("尚未保存", window.settings_backup_feedback.text())

    def test_original_backup_location_workflow_with_workspace_fixture(self):
        from tests.test_ui_management import ManagementUITests
        self.window = self.open_settings()
        self.store = self.window.store
        ManagementUITests.test_simplified_settings_change_destination_without_moving_existing_backup(self)

    def test_invalid_or_busy_saves_preserve_persisted_settings(self):
        window = self.open_settings()
        old_root = window.service.backup_root()
        window.backup_path.clear()
        with patch("selfManger.ui.window.QMessageBox.warning") as warning:
            window.save_settings()
            warning.assert_called_once()
        window.jobs["fixture"] = (None, None, None)
        try:
            window.backup_path.setText(str(self.root / "pending"))
            with patch("selfManger.ui.window.QMessageBox.information") as feedback:
                window.save_settings()
                window.save_backup_retention()
                self.assertEqual(feedback.call_count, 2)
        finally:
            window.jobs.clear()
        self.assertEqual(window.service.backup_root(), old_root)

    def test_theme_persists_and_password_remains_masked(self):
        window = self.open_settings()
        index = window.appearance_mode.findData("dark")
        with patch.object(window.service, "set_appearance", wraps=window.service.set_appearance) as save_theme:
            window.appearance_mode.setCurrentIndex(index)
            save_theme.assert_called_once_with("dark")
        self.assertEqual(window.store.setting("appearance"), "dark")
        self.assertEqual(window.common_password_view.echoMode(), QLineEdit.EchoMode.Password)
        window.settings_tabs.setCurrentIndex(2)
        self.assertFalse(window.common_password_reveal.isChecked())

    def test_vault_save_and_clear_refresh_saved_status(self):
        window = self.open_settings()
        vault = self.root / "another-vault"
        (vault / ".obsidian").mkdir(parents=True)
        window.obsidian_vault_root.setText(str(vault))
        with patch("selfManger.ui.window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            window.save_obsidian_vault_root()
        self.assertEqual(window.store.setting("obsidian_vault_root"), str(vault.absolute()))
        self.assertNotIn("尚未保存", window.settings_obsidian_status.text())
        window.obsidian_vault_root.clear()
        window.save_obsidian_vault_root()
        self.assertEqual(window.store.setting("obsidian_vault_root"), "")
        self.assertNotIn(str(vault), window.settings_obsidian_status.text())
        self.assertNotIn("尚未保存", window.settings_obsidian_status.text())

    def test_vault_save_can_rebase_existing_index_for_a_moved_vault(self):
        window = self.open_settings()
        old_root = Path(window.store.setting("obsidian_vault_root"))
        old_index = KnowledgeIndex(window.store, old_root)
        old_index.scan()
        with window.store.connect() as db:
            old_index._put_record(db, "02-知识/开发/示例.md", {"tags": ["preserved"], "favorite": True})
        moved_root = self.root / "moved-vault"
        (moved_root / ".obsidian").mkdir(parents=True)
        window.obsidian_vault_root.setText(str(moved_root))

        with patch("selfManger.ui.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes) as question:
            window.save_obsidian_vault_root()

        question.assert_called_once()
        self.assertEqual(window.store.setting("obsidian_vault_root"), str(moved_root.absolute()))
        moved_index = KnowledgeIndex(window.store, moved_root)
        record = moved_index.read_record("02-知识/开发/示例.md")
        self.assertEqual(record["tags"], ["preserved"])
        self.assertTrue(record["favorite"])

    def test_vault_save_cancel_keeps_previous_path(self):
        window = self.open_settings()
        previous = window.store.setting("obsidian_vault_root")
        old_index = KnowledgeIndex(window.store, Path(previous))
        old_index.scan()
        with window.store.connect() as db:
            old_index._put_record(db, "02-知识/开发/示例.md", {"tags": ["old"]})
        new_root = self.root / "unused-vault"
        (new_root / ".obsidian").mkdir(parents=True)
        window.obsidian_vault_root.setText(str(new_root))

        with patch("selfManger.ui.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Cancel):
            window.save_obsidian_vault_root()

        self.assertEqual(window.store.setting("obsidian_vault_root"), previous)

    def test_vault_move_conflict_keeps_both_indexes_and_previous_setting(self):
        window = self.open_settings()
        old_root = Path(window.store.setting("obsidian_vault_root"))
        old_index = KnowledgeIndex(window.store, old_root)
        old_index.scan()
        with window.store.connect() as db:
            old_index._put_record(db, "02-知识/开发/示例.md", {"tags": ["old"]})
        target_root = self.root / "indexed-vault"
        (target_root / ".obsidian").mkdir(parents=True)
        target_index = KnowledgeIndex(window.store, target_root)
        (target_root / "existing.md").write_text("# Existing\n", encoding="utf-8")
        target_index.scan()
        window.obsidian_vault_root.setText(str(target_root))

        with patch("selfManger.ui.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes), \
                patch("selfManger.ui.window.QMessageBox.warning") as warning:
            window.save_obsidian_vault_root()

        warning.assert_called_once()
        self.assertEqual(window.store.setting("obsidian_vault_root"), str(old_root))
        with window.store.connect() as db:
            self.assertEqual(json.loads(db.execute(
                "SELECT tags FROM knowledge_metadata WHERE root_id=? AND relative_path='02-知识/开发/示例.md'",
                (old_index.root_id,)).fetchone()[0]), ["old"])
        self.assertTrue(target_index.scan()["items"])

    def test_clearing_vault_path_can_rebase_to_workspace_default(self):
        window = self.open_settings()
        old_root = Path(window.store.setting("obsidian_vault_root"))
        old_index = KnowledgeIndex(window.store, old_root)
        old_index.scan()
        with window.store.connect() as db:
            old_index._put_record(db, "02-知识/开发/示例.md", {"tags": ["moved"]})
        workspace = self.root / "workspace"
        default_vault = workspace / "myself"
        (default_vault / ".obsidian").mkdir(parents=True)
        window.store.set_setting("project_workspace", str(workspace))
        window.obsidian_vault_root.clear()

        with patch("selfManger.ui.window.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes) as question:
            window.save_obsidian_vault_root()

        question.assert_called_once()
        self.assertEqual(window.store.setting("obsidian_vault_root"), "")
        moved_index = KnowledgeIndex(window.store, default_vault)
        self.assertEqual(moved_index.read_record("02-知识/开发/示例.md")["tags"], ["moved"])

    @visual
    def test_all_groups_render_at_two_sizes_and_two_themes(self):
        timer = start_visual_timing(self)
        window = self.open_settings()
        setup_theme(self.app)
        evidence = evidence_root()
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for width, height in ((1440, 960), (960, 720)):
                window.resize(width, height)
                for group in range(4):
                    window.settings_tabs.setCurrentIndex(group)
                    timer.run("processEvents", self.app.processEvents)
                    self.assertEqual(window.settings_scroll.horizontalScrollBar().maximum(), 0)
                    for widget in window.settings_page.findChildren(QPushButton):
                        if widget.isVisible():
                            self.assertGreater(widget.width(), widget.fontMetrics().horizontalAdvance(widget.text()))
                    for card in window.settings_page.findChildren(QFrame, "SettingsCard"):
                        if card.isVisible():
                            self.assertLessEqual(card.width(), window.settings_scroll.viewport().width())
                    timer.capture(window, evidence / f"settings-{group}-{theme}-{width}.png")
