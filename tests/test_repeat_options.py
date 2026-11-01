"""Selectable recurrence dates persist across all planning entry points."""
import unittest
from datetime import date

from PySide6.QtCore import QDate
from tests.support import evidence_root, start_visual_timing, visual

from tests import test_action_center as domain_fixture
from tests import test_plan_modules as ui_fixture
from selfManger.action_center import ActionCenter
from selfManger.domain import UserError
from selfManger.storage import Store
from selfManger.ui.action_page import ItemEditor
from selfManger.ui.plan_dialog import PlanDocumentDialog
from selfManger.ui.theme import setup_theme, apply_theme


class RepeatRuleTests(unittest.TestCase):
    setUp = domain_fixture.ActionCenterTests.setUp

    def test_multiple_weekdays_and_independent_completion(self):
        task = self.center.save_action(dict(title="周一周五", repeat="weekly", due_date="2026-10-01", repeat_days=[1, 5]))
        rule = self.center.rules()[task["id"]]
        self.assertTrue(self.center.occurs(rule, date(2026, 10, 2)))
        self.assertTrue(self.center.occurs(rule, date(2026, 10, 5)))
        self.assertFalse(self.center.occurs(rule, date(2026, 10, 6)))
        self.assertFalse(self.center.occurs(rule, date(2026, 9, 28)))
        self.center.complete(task["id"], True, "2026-10-02")
        self.assertFalse(self.center.actions("today", day=date(2026, 10, 5))[0]["done"])
        with self.assertRaises(UserError):
            self.center.save_action(dict(title="空星期", repeat="weekly", due_date="2026-10-01", repeat_days=[]))

    def test_month_date_boundaries_and_legacy_month_end(self):
        task = self.center.save_action(dict(title="每月十五", repeat="monthly", due_date="2026-01-01", repeat_month_day=15))
        rule = self.center.rules()[task["id"]]
        self.assertTrue(self.center.occurs(rule, date(2026, 2, 15)))
        self.assertFalse(self.center.occurs(rule, date(2026, 2, 1)))
        for selected, day, expected in ((31, date(2026, 2, 28), False), (31, date(2026, 3, 31), True),
                                        (29, date(2028, 2, 29), True), (29, date(2027, 2, 28), False)):
            self.assertEqual(self.center.occurs({**rule, "month_day": selected}, day), expected)
        legacy = self.center.save_action(dict(title="旧月末", repeat="monthly", due_date="2026-01-31"))
        self.assertTrue(self.center.occurs(self.center.rules()[legacy["id"]], date(2026, 2, 28)))
        for invalid in (0, 32, True, "15"):
            with self.assertRaises(UserError):
                self.center.save_action(dict(title="无效", repeat="monthly", due_date="2026-01-01", repeat_month_day=invalid))

    def test_existing_database_migrates_without_changing_old_rules(self):
        with self.store.connect() as db:
            db.execute("DROP TABLE action_repeat_rules")
            db.execute("CREATE TABLE action_repeat_rules(task_id TEXT PRIMARY KEY,frequency TEXT,start_date TEXT,end_date TEXT,weekdays TEXT)")
            db.execute("INSERT INTO action_repeat_rules VALUES ('old','monthly','2026-01-31',NULL,'[]')")
            # The fixture represents a legacy schema, including its version.
            db.execute("PRAGMA user_version=5")
        migrated = ActionCenter(Store(self.root / "data"))
        self.assertIsNone(migrated.rules()["old"]["month_day"])
        self.assertTrue(migrated.occurs(migrated.rules()["old"], date(2026, 2, 28)))


class RepeatOptionsTests(unittest.TestCase):
    setUpClass = classmethod(ui_fixture.PlanModuleTests.setUpClass.__func__)
    setUp = ui_fixture.PlanModuleTests.setUp
    make_window = ui_fixture.PlanModuleTests.make_window
    until_idle = ui_fixture.PlanModuleTests.until_idle
    settle = ui_fixture.PlanModuleTests.settle
    close_window = ui_fixture.PlanModuleTests.close_window
    open_page = ui_fixture.PlanModuleTests.open_page
    save_modal = ui_fixture.PlanModuleTests.save_modal
    save_composer = ui_fixture.PlanModuleTests.save_composer

    def test_modal_weekdays_and_edit_existing_rule(self):
        window, page = self.open_page()
        page.select_view("recurring")
        self.settle(window)
        def select(dialog):
            dialog.frequency.setCurrentIndex(dialog.frequency.findData("weekly"))
            dialog.repeat_options.restore([[], 15])
            dialog.title.setText("空选校验")
            dialog.save.click()
            self.assertIn("至少选择", dialog.error.text())
            self.assertFalse(dialog.busy)
            self.assertEqual(window.store.planned_tasks(), [])
            dialog.repeat_options.restore([[2, 4, 7], 15])
        self.save_modal(page, page.add_button.click, "每周执行", setup=select)
        task = window.store.planned_tasks()[0]
        rule = page.service.rules()[task["id"]]
        self.assertEqual(rule["weekdays"], [2, 4, 7])
        editor = ItemEditor(page, record=task)
        self.assertEqual(editor.values()["repeat_days"], [2, 4, 7])
        editor.repeat.setCurrentIndex(editor.repeat.findData("monthly"))
        editor.repeat_options.month_day.setValue(20)
        page.service.save_action(editor.values(), task["id"])
        page.refresh()
        self.settle(window)
        page.quick_create.load_document("recurring", window.store.planned_task(task["id"]))
        self.assertEqual(page.quick_create.repeat_options.values()["repeat_month_day"], 20)
        self.assertEqual(len(window.store.planned_tasks()), 1)
        editor.deleteLater()

    def test_quick_month_rule_uses_sqlite_and_draft(self):
        window, page = self.open_page()
        page.begin_quick_create("recurring")
        composer = page.quick_create
        composer.date.setDate(QDate(2026, 1, 1))
        composer.frequency.setCurrentIndex(composer.frequency.findData("monthly"))
        composer.repeat_options.month_day.setValue(15)
        self.save_composer(page, "每月十五")
        task = window.store.planned_tasks()[0]
        self.assertEqual(page.service.rules()[task["id"]]["month_day"], 15)
        self.assertIsNone(page.service.documents.path("action", task["id"]))
        page.refresh()
        self.settle(window)
        self.assertEqual(page.service.rules()[task["id"]]["month_day"], 15)
        composer.load_document("recurring", window.store.planned_task(task["id"]))
        composer.repeat_options.month_day.setValue(25)
        composer.persist_draft()
        self.settle(window)
        draft = window.store.setting("planning_markdown_draft_v1")
        self.assertEqual(draft["fields"][8][1], 25)
        composer.repeat_options.month_day.setValue(1)
        composer.restore_draft()
        self.assertEqual(composer.repeat_options.month_day.value(), 25)
        # Old eight-field drafts continue to restore.
        draft["fields"] = draft["fields"][:8]
        window.store.set_setting("planning_markdown_draft_v1", draft)
        composer.restore_draft()
        self.assertEqual(composer.frequency.currentData(), "monthly")

    @visual
    def test_weekly_monthly_visuals(self):
        timer = start_visual_timing(self)
        window, page = self.open_page()
        setup_theme(self.app)
        output = evidence_root("test_repeat_options")
        for theme in ("light", "dark"):
            apply_theme(self.app, theme)
            for frequency in ("weekly", "monthly"):
                with timer.measure("construction"):
                    dialog = PlanDocumentDialog(page, "recurring", "# 周期计划\n\n## 执行说明\n\n## 打卡记录\n")
                dialog.frequency.setCurrentIndex(dialog.frequency.findData(frequency))
                dialog.repeat_options.restore([[1, 3, 5], 15])
                dialog.show()
                for width in (760, 560):
                    dialog.resize(width, 740)
                    timer.run("processEvents", self.app.processEvents)
                    timer.run("processEvents", self.app.processEvents)
                    self.assertTrue(dialog.repeat_options.isVisible())
                    self.assertLessEqual(dialog.width(), width)
                    self.assertTrue(timer.capture(dialog, output / f"{theme}-{frequency}-{width}.png"))
                dialog.accept()
                dialog.deleteLater()
