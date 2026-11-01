"""Current six-module workbench contracts; retired interfaces live in history/."""
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from tests import test_plan_modules as fixture
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.projects import ProjectDialog, ProjectPromptDialog
from selfManger.ui.task_dialog import UnifiedTaskDialog
from selfManger.ui.today_widgets import ROW_DATA
from selfManger.workbench import work_root


class WorkbenchUITests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window

    def seed(self):
        window = self.make_window()
        workspace = ProjectWorkspace(self.root / 'workspace')
        workspace.initialize(TaskContext())
        created = workspace.create('工作台项目', '核验现有操作', False, TaskContext())
        window.store.set_setting('project_workspace', str(workspace.root))
        window.project_page.refresh()
        self.settle(window)
        return window, workspace, created

    def test_visible_navigation_and_default_page_match_current_seven_modules(self):
        window = self.make_window()
        visible = [window.navigation.item(i).text() for i in range(window.navigation.count())
                   if not window.navigation.item(i).isHidden()]
        self.assertEqual(visible, ['今日工作台', '计划与待办', '项目管理', '工具与环境', '知识库', '数据安全', '设置'])
        self.assertIs(window.stack.currentWidget(), window.today_page)
        self.assertFalse(window.today_page.open_daily_button.isVisible())
        button = window.today_page.open_daily_button
        self.assertFalse(button.isWindow())
        self.assertIs(button.parentWidget(), window.today_page)
        for width in (1000, 1680, 1000):
            window.resize(width, 1000)
            self.app.processEvents()
            self.assertFalse(button.isVisible())
            self.assertNotIn(button, QApplication.topLevelWidgets())
        for index in (window.PLAN, window.PROJECT, window.TOOLS, window.LIBRARY, window.SAFETY, window.SETTINGS):
            window.navigation.setCurrentRow(index)
            self.settle(window)
            self.assertEqual(window.stack.currentIndex(), index)

    def test_tools_page_registers_and_opens_shortcuts_without_shell_execution(self):
        from selfManger.ui.developer_tools import DeveloperToolsPage
        from PySide6.QtGui import QDesktopServices

        window = self.make_window()
        self.assertIsInstance(window.developer_tools_page, DeveloperToolsPage)
        executable = self.root / 'fake-tool.exe'
        executable.write_bytes(b'fixture')
        registered = window.service.save_developer_tool({
            'name': '本地工具', 'kind': 'application', 'target': str(executable), 'summary': '测试'
        })
        window.navigation.setCurrentRow(window.TOOLS)
        self.settle(window)
        page = window.developer_tools_page
        self.assertEqual(page.diagnostics_grid.count(), 8)
        self.assertTrue(all(page.diagnostics_grid.itemAt(index).widget().wordWrap()
                            for index in range(page.diagnostics_grid.count())))
        self.assertEqual(page.items.rowCount(), 1)
        self.assertEqual(page.selected_id(), registered['id'])
        with patch.object(QDesktopServices, 'openUrl', return_value=True) as open_url:
            page.open_tool()
        open_url.assert_called_once()
        window.service.remove_developer_tool(registered['id'])
        page.refresh()
        self.assertEqual(page.items.rowCount(), 0)

    def test_workbench_create_complete_and_refresh_sqlite_action_without_daily_markdown(self):
        window = self.make_window()
        today, planning = window.today_page, window.planning_page
        selected = date.today() - timedelta(days=1)
        today.set_day(selected)
        self.settle(window)
        def save(dialog):
            dialog.title.setText('工作台行动')
            dialog.body.setPlainText('原始说明')
            dialog.save.click()
            self.settle(window)
            self.assertEqual(dialog.result(), 1)
            return dialog.result()
        with patch.object(PlanDocumentDialog, 'exec', save):
            today.add_current_entry()
        self.settle(window)
        task = window.store.planned_tasks()[0]
        self.assertFalse((work_root(window.store) / '每日').exists())
        self.assertIsNone(planning.service.documents.path('action', task['id']))
        item = next(today.tasks.item(i) for i in range(today.tasks.count())
                    if (today.tasks.item(i).data(ROW_DATA) or {}).get('planned_task_id') == task['id'])
        item.setCheckState(Qt.CheckState.Checked)
        self.settle(window)
        self.assertEqual(window.store.planned_task(task['id'])['status'], 'done')
        self.assertEqual(window.store.planned_task(task['id'])['status'], 'done')
        self.assertEqual(window.store.planned_task(task['id'])['notes'], '原始说明')
        today.manual_refresh()
        self.settle(window)
        self.assertEqual(window.store.planned_task(task['id'])['notes'], '原始说明')
        self.assertFalse((work_root(window.store) / '每日').exists())
        today.open_daily()
        self.assertEqual((planning.view, planning.day), ('today', selected))

    def test_project_creation_clears_search_selects_new_project_and_retains_previous(self):
        window, workspace, old = self.seed()
        page = window.project_page
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        page.search.setText('不存在')
        def fill(dialog):
            dialog.name.setText('新项目')
            dialog.folder_name.setText('new-project')
            dialog.goal.setPlainText('新目标')
            dialog.entry.setChecked(False)
            dialog.initialization_selection = dialog.initialization_panel.selection()
            return 1
        with patch.object(ProjectDialog, 'exec', fill), patch.object(ProjectPromptDialog, 'exec', return_value=0):
            page.create_project()
            self.settle(window)
        self.assertEqual(page.selected()['name'], '新项目')
        self.assertEqual(page.search.text(), '')
        self.assertEqual(len(workspace.registry()['projects']), 2)
        self.assertTrue(Path(old['directory']).is_dir())

    def test_archived_project_retains_directory_and_disables_task_mutations(self):
        window, workspace, created = self.seed()
        page = window.project_page
        identity = created['project_id']
        payload = Path(created['directory']) / 'payload.txt'
        payload.write_text('保留资料', encoding='utf-8')
        workspace.set_archived(identity, True, TaskContext())
        page.set_state_filter('archived')
        page.refresh()
        window.navigation.setCurrentRow(window.PROJECT)
        self.settle(window)
        self.assertEqual(page.selected()['state'], 'archived')
        self.assertEqual(page.selected()['directory'], created['directory'])
        self.assertEqual(payload.read_text(encoding='utf-8'), '保留资料')
        self.assertFalse(page.task_add_buttons['ai'].isEnabled())
        with patch.object(UnifiedTaskDialog, 'exec') as opened:
            page.create_shared_task('ai')
            opened.assert_not_called()
        workspace.set_archived(identity, False, TaskContext())
        page.set_state_filter('active')
        page.refresh()
        self.settle(window)
        self.assertEqual(page.selected()['id'], identity)

    def test_missing_task_file_does_not_hide_status_or_handoff(self):
        window, workspace, created = self.seed()
        path = Path(created['directory']) / 'agent/TASKS.md'
        path.unlink()  # Synthetic fixture only.
        window.navigation.setCurrentRow(window.PROJECT)
        window.project_page.refresh()
        self.settle(window)
        page = window.project_page
        self.assertIn(1, page.document_errors)
        self.assertTrue(page.status_text)
        self.assertFalse(path.exists())
        self.assertNotIn('读取中', page.project_name.text())

    def test_invalid_workspace_keeps_personal_plan_accessible(self):
        window = self.make_window()
        window.planning_page.service.save_action({'title': '仍可使用'})
        window.store.set_setting('project_workspace', str(self.root / 'missing-workspace'))
        with patch.object(QMessageBox, 'warning'):
            window.project_page.refresh()
            self.settle(window)
        window.navigation.setCurrentRow(window.PLAN)
        window.planning_page.select_view('inbox')
        self.settle(window)
        self.assertEqual(window.planning_page.service.actions()[0]['title'], '仍可使用')

    def test_future_browsing_and_manual_refresh_preserve_selected_day_without_creating_files(self):
        window = self.make_window()
        page = window.today_page
        future = date.today() + timedelta(days=20)
        before = set(self.root.rglob('*.md'))
        page.set_day(future)
        page.manual_refresh()
        self.settle(window)
        self.assertEqual(page.day, future)
        self.assertEqual(set(self.root.rglob('*.md')), before)

    def test_empty_workspace_disables_project_actions_without_hiding_data_location(self):
        window = self.make_window()
        window.store.set_setting('project_workspace', '')
        window.project_page.refresh()
        self.settle(window)
        page = window.project_page
        self.assertEqual(page.table.rowCount(), 0)
        self.assertIn(str(window.store.root), page.message.text())
        self.assertEqual(page.new_button.text(), '选择工作区')
        self.assertTrue(page.new_button.isEnabled())
        with patch.object(QFileDialog, 'getExistingDirectory', return_value='') as chooser:
            page.new_button.click()
            chooser.assert_called_once()
        for button in (page.edit_project_info_button, page.task_add_buttons['ai']):
            self.assertFalse(button.isEnabled())

    def test_empty_workspace_can_select_existing_project_workspace(self):
        window = self.make_window()
        workspace = ProjectWorkspace(self.root / 'workspace')
        workspace.initialize(TaskContext())
        window.store.set_setting('project_workspace', '')
        page = window.project_page
        page.refresh()
        self.settle(window)

        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(workspace.root)) as chooser:
            page.new_button.click()
            self.settle(window)

        chooser.assert_called_once()
        self.assertEqual(Path(window.service.project_workspace_root()), workspace.root)
        self.assertEqual(page.new_button.text(), '新建项目')
        self.assertTrue(page.new_button.isEnabled())
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(workspace.registry()['projects'], [])


if __name__ == '__main__':
    unittest.main()
