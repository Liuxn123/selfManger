"""Generated guidance remains usable while project UI becomes simpler."""
import os
import unittest
from tests.support import evidence_root, start_visual_timing, visual
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tests import test_ui_optimization as fixture
from selfManger.project_prompts import TASK_FORMAT, project_prompt
from selfManger.project_task_documents import _render, import_legacy_project_task_documents
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.ui.projects import ProjectDialog, ProjectPromptDialog
from selfManger.ui.project_widgets import overview_snapshot
from selfManger.ui.theme import setup_theme, apply_theme


class ProjectSimplificationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    until_idle = fixture.UIOptimizationTests.until_idle
    close_window = fixture.UIOptimizationTests.close_window

    def test_generated_prompt_sample_imports_once_with_ai_source(self):
        window = self.make_window()
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        item = workspace.create("精简样例", "验证流程", False, TaskContext())
        item["id"] = item["project_id"]
        item["name"] = "精简样例"
        directory = Path(item["directory"])
        agents = (directory / "AGENTS.md").read_text(encoding="utf-8")
        prompts = (directory / "agent/PROMPTS.md").read_text(encoding="utf-8")
        self.assertEqual(prompts.count("## 任务与项目协作资料"), 1)
        self.assertIn("不生成 Markdown 副本", prompts)
        self.assertIn(TASK_FORMAT, agents)
        ai = directory / "agent/TASKS-AI.md"
        manual = directory / "agent/TASKS-MANUAL.md"
        manual.write_text("# Historical manual task notes\n", encoding="utf-8")
        before = manual.read_bytes()
        ai.write_text("# AI 规划\n" + _render("ai", [{"id": "new", "title": "导入示例", "kind": "task", "status": "planned"}]), encoding="utf-8")
        self.assertEqual(import_legacy_project_task_documents(window.store, workspace, item["id"])["imported"], 1)
        self.assertTrue(import_legacy_project_task_documents(window.store, workspace, item["id"])["already_imported"])
        self.assertEqual(window.store.planned_tasks()[0]["source"], "ai")
        self.assertEqual(before, manual.read_bytes())
        for initial in (True, False):
            dialog = ProjectPromptDialog(window, item, initial=initial)
            text = project_prompt(item["name"], item["id"], item["directory"], initial=initial)
            self.assertLess(len(text), 2200)
            self.assertEqual(dialog.preview.toPlainText(), text)
            dialog.copy_button.click()
            self.assertEqual(QApplication.clipboard().text(), text)
            dialog.close()

    def test_dynamic_card_spans_right_column_and_info_card_is_absent(self):
        window = self.make_window()
        overview = window.project_page.overview
        index = overview.grid.indexOf(overview.activity_card)
        self.assertEqual(overview.grid.getItemPosition(index), (0, 2, 3, 1))
        self.assertFalse(hasattr(overview, "info_card"))
        snapshot = overview_snapshot("# 状态", "## 2026-10-22 — 验证\n真实动态正文", [])
        overview.set_data(snapshot, {"directory": str(self.root), "state": "active"}, {}, [], [])
        self.assertIn("真实动态正文", " ".join(label.text() for label in overview.findChildren(QLabel)))

    def test_wizard_filter_keeps_selected_resources_and_custom_rules(self):
        window = self.make_window()
        dialog = ProjectDialog(None)
        panel = dialog.initialization_panel
        panel.rules.setPlainText("## 我的规范\n保留这一段")
        panel._add_resource(panel.knowledge_list, {"id": "fixture", "name": "测试资料", "path": str(self.root / "fixture.md")})
        item = panel.knowledge_list.item(panel.knowledge_list.count() - 1)
        item.setCheckState(Qt.CheckState.Checked)
        panel.search.setText("无匹配")
        self.assertTrue(item.isHidden())
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        panel.setCurrentIndex(1)
        self.assertIn("保留这一段", panel.rules.toPlainText())
        dialog.reject()

    @visual
    def test_wizard_prompt_footer_and_overview_previews(self):
        timer = start_visual_timing(self)
        window = self.make_window()
        setup_theme(self.app)
        output = evidence_root()
        with timer.measure("construction"):
            dialog = ProjectDialog(None)
        dialog.name.setText("示例项目")
        dialog.goal.setPlainText("交付可验证结果")
        with timer.measure("construction"):
            prompt = ProjectPromptDialog(window, {"name": "示例项目", "id": "P-TEST", "directory": str(self.root)})
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for step in range(4):
                dialog.initialization_panel.setCurrentIndex(step)
                dialog.show()
                timer.run("processEvents", self.app.processEvents)
                timer.capture(dialog, output / f"wizard-{theme}-{step}.png")
            dialog.hide()
            prompt.show()
            timer.run("processEvents", self.app.processEvents)
            timer.capture(prompt, output / f"prompt-{theme}.png")
            prompt.hide()
            window.resize(1024, 760)
            timer.run("processEvents", self.app.processEvents)
            timer.capture(window, output / f"footer-{theme}.png")
            overview = window.project_page.overview
            snapshot = overview_snapshot("# 状态", "## 2026-10-22 — 验证\n项目动态占满右侧，保留真实记录", [])
            overview.set_data(snapshot, {"directory": str(self.root), "state": "active"}, {}, [], [])
            overview.setParent(None)
            overview.resize(1000, 660)
            overview.show()
            self.app.processEvents()
            timer.capture(overview, output / f"overview-{theme}.png")
            overview.hide()
        prompt.close()
        dialog.close()
