"""Project selection includes existing shared arrangements without copying records."""
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.support import evidence_root, start_visual_timing, visual
from tests import test_plan_modules as fixture
from selfManger.action_center import ActionCenter
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.theme import setup_theme, apply_theme


class ProjectPlanFilterTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window
    open_page = fixture.PlanModuleTests.open_page
    save_modal = fixture.PlanModuleTests.save_modal

    def workspace(self, window):
        workspace = ProjectWorkspace(self.root / "projects")
        context = TaskContext()
        workspace.initialize(context)
        projects = [workspace.create(name, "隔离项目筛选验收", False, context)["project_id"]
                    for name in ("示例开发项目", "示例学习项目")]
        window.store.set_setting("project_workspace", str(workspace.root))
        return workspace, projects

    def seed(self, page, workspace, projects):
        root = str(workspace.root)
        plans = [page.service.save_plan({"title": "关联计划 " + project, "project_id": project, "project_root": root})
                 for project in projects]
        tasks = [page.window.store.add_planned_task({"title": "已有安排 " + project,
            "project_id": project, "project_root": root, "source": "manual",
            "status": "planned"}, f"project-doc:{root}:{project}:existing")
            for index, project in enumerate(projects)]
        return plans, tasks

    def test_existing_records_are_root_scoped_and_not_imported_or_copied(self):
        store = Store(self.root / "data")
        center = ActionCenter(store)
        root = str((self.root / "workspace-a").resolve())
        other = str((self.root / "workspace-b").resolve())
        original = store.add_planned_task({"title": "项目原有手动安排", "project_id": "P-1",
            "project_root": root, "source": "manual"})
        store.add_planned_task({"title": "同项目 AI 规划", "project_id": "P-1",
            "project_root": root, "source": "ai"})
        store.add_planned_task({"title": "另一个工作区的同编号", "project_id": "P-1", "project_root": other})
        store.add_planned_task({"title": "另一个项目", "project_id": "P-2", "project_root": root})
        before = store.planned_tasks()
        first = center.project_schedule(root, {"P-1"})
        second = center.project_schedule(root, {"P-1"})
        self.assertEqual([row["id"] for row in first], [original["id"]])
        self.assertEqual(first, second)
        self.assertEqual(store.planned_tasks(), before)
        self.assertEqual(center.actions("today"), [])

    def test_filter_shows_selected_plan_and_existing_project_arrangements(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        plans, tasks = self.seed(page, workspace, projects)
        foreign = page.service.save_plan({"title": "别的工作区计划", "project_id": projects[0],
                                         "project_root": str(self.root / "different-workspace")})
        page.select_view("project-plans")
        self.settle(window)
        self.assertEqual(page.project_filter.count(), 3)
        self.assertEqual(page.list.count(), 2)
        self.assertEqual(page.project_records_list.count(), 2)
        page.project_filter.setCurrentIndex(page.project_filter.findData(projects[1]))
        self.assertEqual(page.list.count(), 1)
        self.assertEqual(page.list.item(0).data(Qt.ItemDataRole.UserRole)["plan"]["id"], plans[1]["id"])
        self.assertEqual(page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole)["id"], tasks[1]["id"])
        page.refresh()
        self.settle(window)
        self.assertEqual(page.project_filter.currentData(), projects[1])
        page.search.setText("不存在的内容")
        QTest.qWait(220)
        self.settle(window)
        self.assertEqual(page.project_records_list.count(), 0)
        self.assertTrue(page.empty_card.isVisible())
        page.empty_primary()
        self.settle(window)
        self.assertEqual(page.project_filter.currentData(), projects[1])
        self.assertEqual(page.project_records_list.count(), 1)
        self.assertEqual(len(page.service.plans()), 3)
        self.assertEqual(window.store.planned_task(tasks[1]["id"])["source"], "manual")

    def test_only_manual_tasks_are_listed_searched_and_counted_without_changing_ai(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        root = str(workspace.root)
        manual = page.service.save_action({"title": "计划和项目模块测试及功能完善", "project_id": projects[0], "project_root": root})
        ai = [window.store.add_planned_task({"title": "AI 隐藏检索词 " + str(i), "source": "ai", "status": "done",
               "project_id": projects[0], "project_root": root}) for i in range(30)]
        before = window.store.planned_tasks(project_root=root)
        page.select_view("project-plans")
        self.settle(window)
        page.project_filter.setCurrentIndex(page.project_filter.findData(projects[0]))
        self.assertEqual(page.project_records_list.count(), 1)
        self.assertEqual(page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole)["id"], manual["id"])
        self.assertEqual([value.text() for _, value in page.module_summary.values], ["0", "1", "0"])
        self.assertIn("手动任务 · 1", page.project_records_title.text())
        page.search.setText("隐藏检索词")
        QTest.qWait(220)
        self.settle(window)
        self.assertEqual(page.project_records_list.count(), 0)
        self.assertEqual([value.text() for _, value in page.module_summary.values], ["0", "0", "0"])
        self.assertEqual(window.store.planned_tasks(project_root=root), before)
        self.assertEqual(len(window.store.planned_tasks(project_root=root, source="ai")), 30)
        page.search.clear()
        QTest.qWait(220)
        self.settle(window)
        self.assertEqual(page.project_records_list.count(), 1)

    def test_existing_project_tasks_are_visible_without_new_personal_plans(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        task = window.store.add_planned_task({"title": "项目管理中已有的任务", "project_id": projects[0],
            "project_root": str(workspace.root), "source": "manual"}, "legacy:project:existing")
        page.select_view("project-plans")
        self.settle(window)
        page.project_filter.setCurrentIndex(page.project_filter.findData(projects[0]))
        self.assertEqual(page.list.count(), 0)
        self.assertEqual(page.project_records_list.count(), 1)
        self.assertFalse(page.empty_card.isVisible())
        self.assertEqual(page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole)["id"], task["id"])
        self.assertEqual(page.service.plans(), [])
        def inspect(dialog):
            self.assertIn("项目管理中已有的任务", dialog.text.toPlainText())
            self.assertIn("项目任务文档", dialog.text.toPlainText())
            return 0
        with patch("selfManger.ui.library.TextReportDialog.exec", inspect):
            page.open_project_record(page.project_records_list.item(0).data(Qt.ItemDataRole.UserRole))

    def test_new_dialog_prefills_selection_and_save_keeps_project_filter(self):
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        page.select_view("project-plans")
        self.settle(window)
        page.project_filter.setCurrentIndex(page.project_filter.findData(projects[1]))
        def check(dialog):
            self.assertEqual(dialog.project.currentData(), projects[1])
        self.save_modal(page, page.add_button.click, "当前项目的新计划", setup=check)
        self.assertEqual(page.project_filter.currentData(), projects[1])
        self.assertEqual(page.service.plans()[0]["project_id"], projects[1])
        self.assertEqual(page.list.count(), 1)

    def test_repeat_project_arrangement_projects_selected_day_state(self):
        store = Store(self.root / "data")
        center = ActionCenter(store)
        root = str((self.root / "workspace").resolve())
        day = date.today()
        task = center.save_action({"title": "项目每日检查", "repeat": "daily", "due_date": day.isoformat(),
                                  "project_id": "P-1", "project_root": root})
        center.complete(task["id"], True, day.isoformat())
        self.assertTrue(center.project_schedule(root, {"P-1"}, day=day)[0]["done"])
        self.assertFalse(center.project_schedule(root, {"P-1"}, day=day+timedelta(days=1))[0]["done"])

    @visual
    def test_project_filter_visuals(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        workspace, projects = self.workspace(window)
        self.seed(page, workspace, projects)
        setup_theme(self.app)
        evidence = evidence_root("test_project_plan_filter")
        for mode in ("light", "dark"):
            apply_theme(self.app, mode)
            for width, height in ((1680, 980), (1024, 760)):
                window.resize(width, height)
                page.select_view("project-plans")
                timer.run("settle", self.settle, window)
                page.project_filter.setCurrentIndex(page.project_filter.findData(projects[0]))
                timer.run("processEvents", self.app.processEvents)
                self.assertTrue(page.project_filter.isVisible())
                self.assertTrue(page.rect().contains(page.project_filter.mapTo(page, page.project_filter.rect().bottomRight())))
                timer.capture(page, evidence / f"project-filter-{mode}-{width}.png")
