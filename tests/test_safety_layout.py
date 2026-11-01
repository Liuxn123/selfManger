"""Selection and action routing checks for the two-column protection UI."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

from selfManger.domain import Resource
from selfManger.application import OVERALL_BACKUP_ID
from selfManger.ui.safety import SafetyPage
from tests.support import begin_ui_tracking, register_ui_cleanup


class SafetyLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        self.project = Resource("示例项目", "project", {"path": "C:/fixture/project"})
        self.agent = Resource("本地资料", "folder", {"path": "C:/fixture/records"})
        self.capabilities = {"backup", "restore", "verify", "observe", "open"}
        self.window = SimpleNamespace(store=Mock(), is_busy=Mock(return_value=False),
            perform=Mock(), edit_resource=Mock(), remove_resource=Mock(), add_resource=Mock(), import_backup=Mock(), rehearse_backup=Mock(),
            service=SimpleNamespace(registry=SimpleNamespace(get=lambda resource: SimpleNamespace(capabilities=self.capabilities)),
                                    backup_root=lambda: Path("C:/fixture/backups"),
                                    database_path=Path("C:/fixture/manager.sqlite3")))
        with patch.object(SafetyPage, "refresh"):
            self.page = SafetyPage(self.window)
        register_ui_cleanup(self, self.app)
        self.page.entries = [
            {"resource": self.project, "health": {"state": "待校验", "created_at": "2026-10-15T10:00:00", "source": "C:/fixture/a.amb"},
             "versions": [{"archive": "C:/fixture/a.amb", "created_at": "2026-10-15T10:00:00", "integrity": "待校验"},
                          {"archive": "C:/fixture/b.amb", "created_at": "2026-10-14T10:00:00", "integrity": "已校验"}]},
            {"resource": self.agent, "health": {"state": "未备份", "created_at": None}, "versions": []},
        ]
        self.page._fill_overview()

    def test_selection_and_filtering_do_not_execute_backup_actions(self):
        self.page.search.setText("本地资料")
        self.assertEqual(self.page.resources_list.count(), 1)
        self.assertEqual(self.page._selected_entry()["resource"].id, self.agent.id)
        self.page.show_restore(self.project)
        self.assertEqual(self.page.search.text(), "")
        self.assertEqual(self.page.views.currentIndex(), 1)
        self.assertEqual(self.page._selected_entry()["resource"].id, self.project.id)
        self.window.perform.assert_not_called()

    def test_refresh_failure_discloses_stale_overview_and_preserves_entries(self):
        previous = list(self.page.entries)
        self.page._worker = object()
        self.page.refresh_button.setEnabled(False)
        self.page._refresh_finished(None)
        self.assertEqual(self.page.entries, previous)
        self.assertIn("保留上次结果", self.page.registry_summary.text())
        self.assertTrue(self.page.refresh_button.isEnabled())

    def test_selected_source_and_version_survive_refresh(self):
        self.page.show_restore(self.project)
        self.page.version_list.setCurrentRow(1)
        self.page.entries.reverse()
        self.page._fill_overview()
        self.assertEqual(self.page._selected_entry()["resource"].id, self.project.id)
        self.page._continue_restore()
        self.window.perform.assert_called_once_with(self.project, "restore", "C:/fixture/b.amb")

    def test_rehearsal_uses_selected_version(self):
        self.page.show_restore(self.project)
        self.page.version_list.setCurrentRow(1)
        self.page._rehearse_selected_version()
        self.window.rehearse_backup.assert_called_once_with(self.project, "C:/fixture/b.amb")

    def test_empty_versions_cannot_trigger_restore(self):
        self.page.show_restore(self.agent)
        self.assertFalse(self.page.continue_restore.isEnabled())
        with patch.object(QMessageBox, "information"):
            self.page._continue_restore()
        self.window.perform.assert_not_called()
        self.assertEqual(self.page.summary[0].text(), "尚无备份")

    def test_primary_action_routes_to_selected_resource(self):
        self.page.show_overview(self.project)
        self.page._run_primary_action()
        self.window.perform.assert_called_once_with(self.project, "verify", "C:/fixture/a.amb")
        self.window.perform.reset_mock()
        self.page.show_overview(self.agent)
        self.page._run_primary_action()
        self.window.perform.assert_called_once_with(self.agent, "backup", "")

    def test_overall_backup_is_builtin_and_explains_scope(self):
        overall = Resource("数据库与 Markdown 整体备份", "folder", {"path": "C:/fixture/data"}, OVERALL_BACKUP_ID)
        self.page.entries.append({"resource": overall, "health": {"state": "未备份", "created_at": None}, "versions": []})
        self.page._fill_overview()
        self.page.show_overview(overall)
        self.assertFalse(self.page.remove_button.isEnabled())
        self.assertFalse(self.page.edit_button.isEnabled())
        self.assertIn("非 Markdown 附件", self.page.attention_detail.text())
        self.assertIn("知识库文件", self.page.attention_detail.text())
        self.assertIn("凭据", self.page.attention_detail.text())
        self.assertIn("暂停外部编辑", self.page.attention_detail.text())
        self.page._run_primary_action()
        self.window.perform.assert_called_once_with(overall, "backup", "")

    def test_busy_guards_preserved(self):
        self.page.show_restore(self.project)
        self.window.is_busy.return_value = True
        self.page._render_selected()
        self.assertFalse(self.page.continue_restore.isEnabled())
        self.assertFalse(self.page.primary_button.isEnabled())
        self.page._continue_restore()
        self.page._run_primary_action()
        self.window.perform.assert_not_called()

    def test_filters_and_no_match_clear_detail_actions(self):
        self.page.search.setText(self.project.name)
        self.assertEqual(self.page._selected_entry()["resource"].id, self.project.id)
        self.page.search.setText(self.agent.name)
        self.assertEqual(self.page._selected_entry()["resource"].id, self.agent.id)
        self.page.search.setText("no-match")
        self.assertIsNone(self.page._selected_entry())
        self.assertFalse(self.page.primary_button.isEnabled())
        self.assertFalse(self.page.continue_restore.isEnabled())
        self.assertEqual(self.page.source_path.text(), "")

    def test_external_resource_navigation_clears_filters_and_selects_correct_item(self):
        self.page.search.setText(self.agent.name)
        self.page.show_overview(self.project)
        self.assertEqual(self.page.search.text(), "")
        self.assertEqual(self.page._selected_entry()["resource"].id, self.project.id)
        self.assertEqual(self.page.views.currentIndex(), 0)
        self.page.show_restore()
        self.assertEqual(self.page._selected_entry()["resource"].id, self.project.id)
        self.assertEqual(self.page.views.currentIndex(), 1)


if __name__ == "__main__":
    unittest.main()
