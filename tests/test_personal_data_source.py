"""Personal plans use SQLite; legacy Markdown is imported once and kept intact."""
import unittest
from datetime import date, timedelta

from selfManger.action_center import ActionCenter
from selfManger.storage import Store
from selfManger.workbench import frontmatter, render_document
from tests.personal_markdown_fixtures import attach_legacy_document, legacy_action_record
from tests.support import TestDirectory


class PersonalDataSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.path / "data"
        self.store = Store(self.root)
        self.center = ActionCenter(self.store)

    def edit_document(self, path, **changes):
        metadata, body = frontmatter(path.read_text(encoding="utf-8"))
        metadata.update(changes)
        path.write_text(render_document(metadata, body + "\n迁移后保留的正文"), encoding="utf-8")

    def test_one_time_import_preserves_fields_relations_and_recurrence(self):
        plan = self.center.save_plan({"title": "原计划", "notes": "原计划正文"})
        today = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        action = self.center.save_action({"title": "原行动", "notes": "原行动正文", "due_date": today,
                                          "goal_id": plan["id"], "repeat": "weekly", "repeat_days": [1, 3]})
        self.center.complete(action["id"], True, today)
        review = self.center.save_review({"title": "原复盘", "body": "原复盘正文", "period_type": "weekly",
                                          "period_key": "weekly:fixture", "start_date": "2026-10-19",
                                          "end_date": "2026-10-25"})
        plan_file = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        action_record = legacy_action_record(self.center, action["id"], self.store.planned_task(action["id"]))
        action_file = attach_legacy_document(self.store, "action", action["id"], "recurring", action_record)
        review_file = attach_legacy_document(self.store, "review", review["id"], "weekly-review", review)
        self.edit_document(plan_file, title="迁入计划", status="active")
        self.edit_document(action_file, title="迁入行动", priority="high")
        self.edit_document(review_file, title="迁入复盘")
        originals = [path.read_bytes() for path in (plan_file, action_file, review_file)]

        report = self.center.documents.migrate_legacy_documents()

        self.assertEqual(report, {"updated": 3, "errors": [], "complete": True})
        migrated_plan = self.center.plans()[0]
        migrated_action = self.store.planned_task(action["id"])
        migrated_review = self.center.reviews()[0]
        self.assertEqual((migrated_plan["title"], migrated_plan["status"], migrated_plan["notes"]),
                         ("迁入计划", "active", "原计划正文\n迁移后保留的正文"))
        self.assertEqual((migrated_action["title"], migrated_action["priority"], migrated_action["goal_id"]),
                         ("迁入行动", "high", plan["id"]))
        self.assertEqual((migrated_review["title"], migrated_review["body"]),
                         ("迁入复盘", "原复盘正文\n迁移后保留的正文"))
        self.assertEqual(self.center.rules()[action["id"]]["weekdays"], [1, 3])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM action_occurrences WHERE task_id=?", (action["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM personal_markdown_documents").fetchone()[0], 3)
        self.assertEqual([path.read_bytes() for path in (plan_file, action_file, review_file)], originals)

    def test_future_markdown_edits_are_not_imported_after_migration(self):
        plan = self.center.save_plan({"title": "计划", "notes": "应用正文"})
        path = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        self.edit_document(path, title="旧文档的最后编辑")
        self.assertEqual(self.center.documents.migrate_legacy_documents()["updated"], 1)
        saved = self.center.plans()[0]
        self.center.save_plan({**saved, "title": "SQLite 后续编辑", "_expected": saved}, saved["id"])
        self.edit_document(path, title="迁移完成后的文件编辑")

        report = self.center.documents.migrate_legacy_documents()

        self.assertEqual(report["updated"], 1)
        self.assertEqual(self.center.plans()[0]["title"], "SQLite 后续编辑")
        self.assertIn("迁移完成后的文件编辑", path.read_text(encoding="utf-8"))

    def test_invalid_import_state_shape_does_not_break_one_time_import(self):
        plan = self.center.save_plan({"title": "原计划", "notes": "原正文"})
        path = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        self.edit_document(path, title="修订计划")
        self.store.set_setting("personal_markdown_import_v1", None)

        result = self.center.documents.migrate_legacy_documents()

        self.assertTrue(result["complete"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.center.plans()[0]["title"], "修订计划")

    def test_invalid_legacy_document_is_retained_and_retryable_after_restart(self):
        plan = self.center.save_plan({"title": "安全原值", "notes": "原正文"})
        path = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        path.write_text("---\ntitle: [invalid\n---\n破损正文", encoding="utf-8")

        failed = self.center.documents.migrate_legacy_documents()

        self.assertFalse(failed["complete"])
        self.assertTrue(failed["errors"])
        self.assertEqual(self.center.plans()[0]["title"], "安全原值")
        self.assertIn("破损正文", path.read_text(encoding="utf-8"))
        state = self.store.setting("personal_markdown_import_v1")
        self.assertFalse(state["complete"])

        path.write_text(render_document({"quick_document": True, "entity_type": "plan", "entity_id": plan["id"],
                                         "module": "longterm", "title": "修复后迁入"}, "修复后的正文"), encoding="utf-8")
        reopened = Store(self.root)
        report = ActionCenter(reopened).documents.migrate_legacy_documents()
        self.assertTrue(report["complete"])
        self.assertEqual(ActionCenter(reopened).plans()[0]["title"], "修复后迁入")

    def test_malformed_legacy_enums_return_field_errors_without_mutating_source(self):
        cases = ("plan-status", "action-frequency", "review-period")
        for case in cases:
            with self.subTest(case=case):
                store = Store(self.root / case)
                center = ActionCenter(store)
                if case == "plan-status":
                    record = center.save_plan({"title": "原计划", "notes": "原文"})
                    path = attach_legacy_document(store, "plan", record["id"], "longterm", record)
                    expected_error = "frontmatter 中的计划状态无效"
                    self.edit_document(path, title="畸形变更", status=["done"])
                elif case == "action-frequency":
                    record = center.save_action({"title": "原行动", "due_date": date.today().isoformat(), "repeat": "daily"})
                    path = attach_legacy_document(store, "action", record["id"], "recurring",
                                                  legacy_action_record(center, record["id"], store.planned_task(record["id"])))
                    expected_error = "frontmatter 中的周期规则无效"
                    self.edit_document(path, title="畸形变更", frequency=["daily"])
                else:
                    record = center.save_review({"title": "原复盘", "body": "原文", "period_type": "weekly",
                                                 "period_key": "weekly:fixture", "start_date": "2026-10-19",
                                                 "end_date": "2026-10-25"})
                    path = attach_legacy_document(store, "review", record["id"], "weekly-review", record)
                    expected_error = "frontmatter 中的复盘周期无效"
                    self.edit_document(path, title="畸形变更", period_type=["weekly"])
                changed_source = path.read_bytes()

                report = center.documents.migrate_legacy_documents()

                self.assertFalse(report["complete"])
                self.assertIn(expected_error, "\n".join(report["errors"]))
                self.assertEqual(path.read_bytes(), changed_source)
                self.assertEqual(report["updated"], 0)
                state = store.setting("personal_markdown_import_v1")
                self.assertFalse(state["complete"])
                if case == "plan-status":
                    self.assertEqual(center.plans()[0]["title"], "原计划")
                elif case == "action-frequency":
                    self.assertEqual(store.planned_task(record["id"])["title"], "原行动")
                else:
                    self.assertEqual(center.reviews()[0]["title"], "原复盘")

    def test_legacy_mapping_cannot_read_markdown_outside_its_root(self):
        plan = self.center.save_plan({"title": "SQLite 原值", "notes": "保留内容"})
        path = attach_legacy_document(self.store, "plan", plan["id"], "longterm", plan)
        outside = path.parents[3] / "outside.md"
        outside.write_text(render_document({"quick_document": True, "entity_type": "plan",
                                           "entity_id": plan["id"], "title": "越界内容"}, "外部正文"),
                           encoding="utf-8")
        original = outside.read_bytes()
        with self.store.connect() as db:
            db.execute("UPDATE personal_markdown_documents SET relative_path=? WHERE entity_type='plan' AND entity_id=?",
                       ("../outside.md", plan["id"]))

        report = self.center.documents.migrate_legacy_documents()

        self.assertFalse(report["complete"])
        self.assertTrue(report["errors"])
        self.assertEqual(self.center.plans()[0]["title"], "SQLite 原值")
        self.assertEqual(outside.read_bytes(), original)

    def test_new_records_and_deletions_do_not_create_or_change_markdown(self):
        plan = self.center.save_plan({"title": "SQLite 计划", "notes": "正文"})
        action = self.center.save_action({"title": "SQLite 行动", "goal_id": plan["id"]})
        review = self.center.save_review({"title": "SQLite 复盘", "body": "复盘正文", "period_type": "monthly",
                                          "period_key": "monthly:fixture", "start_date": "2026-10-01",
                                          "end_date": "2026-10-31"})
        self.assertEqual([self.center.documents.path(kind, identity) for kind, identity in
                          (("plan", plan["id"]), ("action", action["id"]), ("review", review["id"]))], [None] * 3)
        self.center.delete_record("plan", plan["id"])
        self.assertIsNone(self.store.planned_task(action["id"])["goal_id"])
        self.assertEqual(self.center.reviews()[0]["body"], "复盘正文")
        self.assertEqual(list((self.root / "工作台").rglob("*.md")), [])

    def test_scheduled_time_survives_action_edits(self):
        action = self.center.save_action({"title": "旧日程", "due_date": date.today().isoformat(),
                                          "kind": "schedule", "scheduled_time": "09:00–10:00"})
        self.center.save_action({**action, "title": "改过的日程", "_expected": action}, action["id"])
        self.assertEqual(self.store.planned_task(action["id"])["scheduled_time"], "09:00–10:00")


if __name__ == "__main__":
    unittest.main()
