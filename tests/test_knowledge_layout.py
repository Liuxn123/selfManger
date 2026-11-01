"""Layout and routing regressions using a new isolated library for each case."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from tests import test_ui_optimization as fixture
from selfManger.ui.knowledge import QUICK_CAPTURE_FOLDER


class KnowledgeLayoutTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    close_window = fixture.UIOptimizationTests.close_window
    until_idle = fixture.UIOptimizationTests.until_idle

    def make_page(self, width=1440, height=960):
        window = self.make_window()
        window.resize(width, height)
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        self.app.processEvents()
        return window.knowledge_page

    def empty(self, page):
        page.loaded({"items": [], "folders": ["", "02-知识", "02-知识/Skills"], "errors": []})
        self.app.processEvents()

    def test_empty_library_has_collect_and_topic_actions_without_dead_panels(self):
        page = self.make_page()
        self.empty(page)
        self.assertTrue(page.empty_panel.isVisible())
        for widget in (page.detail_panel, page.paging_bar, page.filter_hint, page.status):
            self.assertFalse(widget.isVisible())
        self.assertTrue(page.list_panel.isVisible())
        with patch.object(page, "collect_resource") as collect, patch.object(page, "new_folder") as topic:
            page.empty_action.click()
            collect.assert_called_once_with("file", default_folder=QUICK_CAPTURE_FOLDER)
            page.empty_secondary.click()
            topic.assert_called_once()

    def test_empty_topic_and_inbox_keep_correct_destinations(self):
        page = self.make_page()
        self.empty(page)
        page.folder_list.setCurrentItem(page.tree_items["02-知识/Skills"])
        with patch.object(page, "collect_resource") as collect:
            page.empty_action.click()
            collect.assert_called_once_with("file", default_folder="02-知识/Skills")
        page.empty_secondary.click()
        self.assertEqual(page.quick_filter, "inbox")
        with patch.object(page, "collect_resource") as collect:
            page.empty_action.click()
            collect.assert_called_once_with("file", default_folder=QUICK_CAPTURE_FOLDER)
        page.empty_secondary.click()
        self.assertEqual(page.quick_filter, "all")

    def test_narrow_preview_and_return_preserve_selection_and_filters(self):
        page = self.make_page(960, 660)
        page.search.setText("检索标记")
        page.render_items()
        identity = page.selected()["relative"]
        self.assertTrue(page.detail_panel.isVisible())
        self.assertTrue(page.list_panel.isVisible())
        self.assertIn("检索标记", page.preview.toPlainText())
        page.detail_close.click()
        self.assertTrue(page.list_panel.isVisible())
        self.assertFalse(page.detail_panel.isVisible())
        self.assertEqual(page.selected()["relative"], identity)
        self.assertEqual(page.search.text(), "检索标记")

    def test_wide_preview_closes_and_reopens_without_rescan(self):
        page = self.make_page()
        self.assertTrue(page.detail_panel.isVisible())
        identity = page.selected()["relative"]
        with patch.object(page, "refresh") as refresh:
            page.detail_button.click()
            self.assertFalse(page.detail_panel.isVisible())
            page.detail_button.click()
            self.assertTrue(page.detail_panel.isVisible())
            refresh.assert_not_called()
        self.assertEqual(page.selected()["relative"], identity)

    def test_search_no_match_hides_statistics_and_preview_and_can_clear(self):
        page = self.make_page()
        self.assertFalse(hasattr(page, "type_chart"))
        page.search.setText("missing-query-4938")
        page.render_items()
        self.assertTrue(page.empty_panel.isVisible())
        self.assertFalse(page.detail_panel.isVisible())
        self.assertFalse(hasattr(page, "type_chart"))
        self.assertTrue(page.empty_action.isVisible())
        page.empty_action.click()
        self.assertEqual(page.search.text(), "")
        self.assertTrue(page.shown)
        self.assertTrue(page.detail_panel.isVisible())

    def test_unconfigured_library_guides_to_settings(self):
        page = self.make_page()
        page.window.store.set_setting("obsidian_vault_root", "")
        page.index = None
        page.items = []
        page.render_items()
        self.assertEqual(page.empty_title.text(), "连接你的知识空间")
        self.assertFalse(hasattr(page, "type_chart"))
        page.empty_action.click()
        self.assertEqual(page.window.navigation.currentRow(), page.window.SETTINGS)


if __name__ == "__main__":
    unittest.main()
