"""Regression checks for data summaries and avoiding redundant UI work."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from tests.support import TestDirectory, begin_ui_tracking, cleanup_ui_test, register_ui_cleanup
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from selfManger.storage import Store
from selfManger.domain import UserError
from selfManger.ui.charts import DistributionChart
from selfManger.ui.components import nav_icon, _nav_icon
from selfManger.ui.window import MainWindow


class UIOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        begin_ui_tracking(self, self.app)
        credential_patch = patch("selfManger.security.SecretStore.get", return_value=None)
        credential_patch.start()
        self.addCleanup(credential_patch.stop)
        evidence = os.environ.get("SELFMANGER_TEST_ROOT")
        if evidence:
            self.root = Path(evidence) / ("ui-test-" + uuid4().hex[:10])
            self.root.mkdir()
        else:
            temporary = TestDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
        register_ui_cleanup(self, self.app)

    def until_idle(self, window):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not window.jobs and window.today_page.worker is None:
                return
            time.sleep(.005)
        self.fail("Isolated UI did not settle")

    def make_window(self):
        store = Store(self.root / "data")
        vault = self.root / "vault"
        (vault / "02-知识/开发").mkdir(parents=True)
        (vault / ".obsidian").mkdir()
        (vault / "02-知识/开发/示例.md").write_text("# 示例\n检索标记", encoding="utf-8")
        (vault / "02-知识/参考.url").write_text("[InternetShortcut]\nURL=https://example.com", encoding="utf-8")
        store.set_setting("obsidian_vault_root", str(vault))
        timer = getattr(self, "_visual_timer", None)
        window = timer.run("construction", MainWindow, store) if timer else MainWindow(store)
        self.addCleanup(lambda: self.close_window(window))
        window.show()
        if timer:
            timer.run("settle", self.until_idle, window)
        else:
            self.until_idle(window)
        return window

    def close_window(self, window):
        self.until_idle(window)
        cleanup_ui_test(self, self.app)

    def test_icon_cache_reuses_raster_and_returns_independent_icons(self):
        _nav_icon.cache_clear()
        first = nav_icon(3, "#123456")
        second = nav_icon(3, "#123456")
        self.assertEqual(_nav_icon.cache_info().misses, 1)
        self.assertEqual(_nav_icon.cache_info().hits, 1)
        before = second.cacheKey()
        first.addPixmap(nav_icon(4).pixmap(24, 24))
        self.assertEqual(nav_icon(3, "#123456").cacheKey(), before)

    def test_chart_empty_and_counts_do_not_mutate_input(self):
        chart = DistributionChart("任务", "尚无任务")
        self.addCleanup(chart.close)
        chart.set_data((("完成", 0, "success"), ("待办", 0, "accent")))
        self.assertEqual(chart.accessibleDescription(), "尚无任务")
        source = [["完成", 2, "success"], ["待办", 3, "accent"]]
        chart.set_data(source)
        source[0][1] = 99
        self.assertEqual(sum(row[1] for row in chart.segments), 5)
        self.assertIn("完成 2", chart.accessibleDescription())
        chart.resize(260, 142)
        self.assertFalse(chart.grab().isNull())

    def test_seven_pages_without_obsolete_dashboard_and_live_task_counts(self):
        window = self.make_window()
        self.assertEqual(window.stack.count(), 7)
        self.assertEqual([page.kinds for page in window.resource_pages], [["project", "vault"]])
        self.assertEqual(len(window.findChildren(type(window.resource_pages[0]))), 1)
        self.assertIs(window.stack.widget(window.SETTINGS), window.settings_page)
        self.assertFalse(hasattr(window, "health_table"))
        page = window.today_page
        report = dict(page.report)
        report["tasks"] = [{"text": "已完成", "planned_task_id": "fixture-done", "done": True},
                           {"text": "待完成", "planned_task_id": "fixture-pending", "done": False}]
        page.render_daily(report)
        self.assertIn("1/2", page.focus.fraction.text().replace(" ", ""))
        page.projects = [{"state": "completed"}, {"state": "paused"}]
        page.render_projects({"errors": [], "watches": []})
        self.assertFalse(hasattr(page, "project_chart"))

    def test_cards_skip_shell_icons_and_table_still_looks_up_icons(self):
        window = self.make_window()
        page = window.knowledge_page
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        self.assertFalse(hasattr(page, "type_chart"))
        page.toggle_view()
        with patch.object(page.file_icon_provider, "icon", wraps=page.file_icon_provider.icon) as icons:
            page.render_items()
            icons.assert_not_called()
            page.search.setText("检索标记")
            page.render_items()
            self.assertEqual(len(page.shown), 1)
            identity = page.selected()["relative"]
            page.toggle_view()
            self.assertEqual(page.selected()["relative"], identity)
            self.assertGreater(icons.call_count, 0)
            self.assertFalse(page.file_list.item(0, 0).icon().isNull())

    def test_task_checkbox_refreshes_chart_from_persisted_task(self):
        window = self.make_window()
        page = window.today_page
        task = window.store.add_planned_task({"title": "核对当日资料", "kind": "task",
                                             "due_date": page.day.isoformat()})
        page.manual_refresh()
        self.until_idle(window)
        self.assertIn("0/1", page.focus.fraction.text().replace(" ", ""))
        with patch.object(page, "refresh", wraps=page.refresh) as refresh:
            page.tasks.item(0).setCheckState(Qt.CheckState.Checked)
            self.until_idle(window)
            page.enable_tasks()
            self.assertEqual(refresh.call_count, 1)
        self.assertEqual(window.store.planned_task(task["id"])["status"], "done")
        self.assertIn("1/1", page.focus.fraction.text().replace(" ", ""))

    def test_failed_task_save_restores_checkbox_and_summary(self):
        window = self.make_window()
        page = window.today_page
        task = window.store.add_planned_task({"title": "保存失败后重试", "kind": "task",
                                             "due_date": page.day.isoformat()})
        page.manual_refresh()
        self.until_idle(window)
        with patch("selfManger.ui.workbench.ActionCenter.complete", side_effect=UserError("隔离写入失败")), patch.object(QMessageBox, "warning") as warning:
            page.tasks.item(0).setCheckState(Qt.CheckState.Checked)
            self.until_idle(window)
            page.enable_tasks()
            self.until_idle(window)
            warning.assert_called_once()
        self.assertEqual(window.store.planned_task(task["id"])["status"], "planned")
        self.assertEqual(page.tasks.item(0).checkState(), Qt.CheckState.Unchecked)
        self.assertIn("0/1", page.focus.fraction.text().replace(" ", ""))

    def test_bundle_member_search_and_archived_filter_survive_early_filtering(self):
        window = self.make_window()
        page = window.knowledge_page
        window.navigation.setCurrentRow(window.LIBRARY)
        self.until_idle(window)
        note = next(entry for entry in page.items if entry.get("suffix") == ".md")
        member = dict(note, search_text="独特内部标记")
        bundle = dict(note, kind="bundle", name="组合资料", relative="bundle:test",
                      search_text="", members=[member], bundle_record={}, entry_relative=note["relative"])
        page.items.append(bundle)
        page.search.setText("独特内部标记")
        page.render_items()
        self.assertEqual([entry["relative"] for entry in page.shown], ["bundle:test"])
        self.assertEqual(bundle["matched_members"], [note["relative"]])
        bundle["archived"] = True
        page.render_items()
        self.assertEqual(page.shown, [])
        page.quick_filter = "archived"
        page.render_items()
        self.assertEqual([entry["relative"] for entry in page.shown], ["bundle:test"])


if __name__ == "__main__":
    unittest.main()
