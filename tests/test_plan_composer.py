"""Prototype controls, source editing, and genuine local draft persistence."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QDate

from tests.support import evidence_root, start_visual_timing, visual
from tests import test_plan_modules as fixture
from selfManger.ui.action_page import MarkdownQuickCreate
from selfManger.ui.theme import setup_theme


class PlanComposerTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PlanModuleTests.setUpClass.__func__)
    setUp = fixture.PlanModuleTests.setUp
    make_window = fixture.PlanModuleTests.make_window
    until_idle = fixture.PlanModuleTests.until_idle
    settle = fixture.PlanModuleTests.settle
    close_window = fixture.PlanModuleTests.close_window
    open_page = fixture.PlanModuleTests.open_page

    def test_type_tabs_review_selector_and_source_header(self):
        window, page = self.open_page()
        composer = page.quick_create
        composer.type_buttons["longterm"].click()
        self.assertEqual(composer.kind.currentData(), "longterm")
        self.assertFalse(composer.date_enabled.isChecked())
        self.assertIn("longterm_plan", composer.body.metadata.text())
        composer.type_buttons["review"].click()
        composer.review_period.setCurrentIndex(1)
        self.assertEqual(composer.kind.currentData(), "monthly-review")
        self.assertIn("monthly_review", composer.body.metadata.text())
        composer.type_buttons["daily"].click()
        composer.date.setDate(QDate(2026, 10, 19))
        self.assertIn("2026-10-19", composer.body.metadata.text())
        self.assertNotIn("type:", composer.body.toPlainText())
        composer.mark_clean()

    def test_markdown_heading_supplies_title_and_save_opens_module(self):
        window, page = self.open_page()
        composer = page.quick_create
        composer.type_buttons["longterm"].click()
        composer.body.setPlainText("# 从正文命名的长期计划\n\n## 下一步\n保留 Markdown")
        composer.save_button.click()
        self.settle(window)
        record = page.service.plans()[0]
        self.assertEqual(record["title"], "从正文命名的长期计划")
        self.assertEqual(page.view, "plans")
        self.assertIsNone(record["due_date"])
        self.assertEqual(record["notes"], "# 从正文命名的长期计划\n\n## 下一步\n保留 Markdown")
        self.assertIsNone(page.service.documents.path("plan", record["id"]))

    def test_draft_is_persisted_and_restored_without_creating_plan_records(self):
        window, page = self.open_page()
        composer = page.quick_create
        composer.body.setPlainText("# 本机草稿\n\n未完成的 Markdown 内容")
        with patch.object(window.service, "save_planning_draft", wraps=window.service.save_planning_draft) as save_draft:
            composer.persist_draft()
            self.settle(window)
        save_draft.assert_called_once()
        value = window.store.setting("planning_markdown_draft_v1")
        self.assertIn("未完成的 Markdown 内容", value["fields"][2])
        self.assertIn("自动保存", page.draft_status.text())
        self.assertEqual(page.service.plans(), [])
        self.assertEqual(window.store.planned_tasks(), [])
        restored = MarkdownQuickCreate(page)
        with patch.object(window.service, "planning_draft", wraps=window.service.planning_draft) as load_draft:
            restored.restore_draft()
        load_draft.assert_called_once_with()
        self.assertEqual(restored.body.toPlainText(), composer.body.toPlainText())
        self.assertEqual(restored.title_input.text(), "本机草稿")
        restored.draft_timer.stop()
        restored.deleteLater()
        composer.mark_clean()

    @visual
    def test_prototype_page_preview(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        timer.run("settle", self.settle, window)
        setup_theme(self.app)
        window.resize(1680, 980)
        timer.run("processEvents", self.app.processEvents)
        composer = page.quick_create
        for title in ("学习计划", "项目阶段安排"):
            record = page.service.save_plan({"title": title, "notes": "## 目标\n隔离样例"})
        record = page.service.save_action({"title": "每日计划-20261019", "due_date": "2026-10-19"})
        page.refresh()
        timer.run("settle", self.settle, window)
        composer.body.setPlainText("# 今日计划\n\n## 今天最重要的事\n- [ ] 写下今天想完成的重点…\n\n## 时间安排\n- [ ] 可添加行动，也可关联项目计划…\n\n## 日末复盘\n")
        composer.persist_draft()
        timer.run("settle", self.settle, window)
        root = evidence_root("test_plan_composer")
        self.assertTrue(composer.rail.isVisible())
        timer.capture(page, root / "prototype-page-light.png")
        timer.capture(window, root / "prototype-window-light.png")
        composer.mark_clean()
