import sqlite3
import unittest
from tests.support import TestDirectory
from pathlib import Path

from selfManger.storage import Store

LATER_TABLES = {"personal_plans", "action_repeat_rules", "action_occurrences", "personal_plan_templates",
                "personal_reviews", "personal_work_notes", "personal_markdown_documents", "planning_deletions"}


def tables(path: Path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def user_version(path: Path) -> int:
    db = sqlite3.connect(path)
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


class StoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.root = Path(self.temp.name) / "data"

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_database_gets_full_schema_and_version(self):
        store = Store(self.root)
        self.assertTrue(LATER_TABLES <= tables(store.path))
        self.assertEqual(user_version(store.path), Store.SCHEMA_VERSION)

    def test_legacy_version_5_database_is_completed_not_trusted(self):
        # Legacy databases were stamped 5 before the later tables/columns existed.
        self.root.mkdir(parents=True)
        db = sqlite3.connect(self.root / "manager.sqlite3")
        db.executescript("""
            CREATE TABLE resources (id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE planned_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, due_date TEXT, project_id TEXT,
                priority TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
                source TEXT NOT NULL DEFAULT 'manual', notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE action_repeat_rules (task_id TEXT PRIMARY KEY, frequency TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT, weekdays TEXT NOT NULL DEFAULT '[]');
            INSERT INTO resources VALUES ('keep-me', '{}');
            PRAGMA user_version=5;
        """)
        db.close()
        store = Store(self.root)
        self.assertEqual(user_version(store.path), Store.SCHEMA_VERSION)
        self.assertTrue(LATER_TABLES <= tables(store.path))
        db = sqlite3.connect(store.path)
        try:
            planned = {row[1] for row in db.execute("PRAGMA table_info(planned_tasks)")}
            repeat = {row[1] for row in db.execute("PRAGMA table_info(action_repeat_rules)")}
            kept = db.execute("SELECT id FROM resources").fetchall()
        finally:
            db.close()
        self.assertTrue({"source_ref", "goal_id", "project_root", "completion_note", "scheduled_time"} <= planned)
        self.assertIn("month_day", repeat)
        self.assertEqual(kept, [("keep-me",)])

    def test_schema_8_clears_project_task_textual_none_goal_without_dropping_rows(self):
        store = Store(self.root)
        with store.connect() as db:
            db.executemany(
                """INSERT INTO planned_tasks
                   (id,title,kind,project_id,project_root,goal_id,status,source,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    ("ai-none", "AI", "task", "P-fixture", "fixture", "None", "done", "ai", "t", "t"),
                    ("manual-none", "manual", "task", "P-fixture", "fixture", "nOnE", "active", "manual", "t", "t"),
                    ("real-goal", "linked", "task", "P-fixture", "fixture", "plan-id", "planned", "manual", "t", "t"),
                    ("other-dangling", "preserve", "task", "P-fixture", "fixture", "missing-id", "planned", "manual", "t", "t"),
                    ("unlinked-none", "unlinked", "task", None, None, "None", "planned", "manual", "t", "t"),
                ],
            )
            db.execute("INSERT INTO personal_plans (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                       ("plan-id", "关联计划", "t", "t"))
            db.execute("PRAGMA user_version=8")

        migrated = Store(self.root)
        with migrated.connect() as db:
            rows = {row["id"]: row["goal_id"] for row in db.execute("SELECT id,goal_id FROM planned_tasks")}
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM planned_tasks").fetchone()[0]
        self.assertEqual(user_version(migrated.path), Store.SCHEMA_VERSION)
        self.assertEqual(count, 5)
        self.assertEqual(rows["ai-none"], None)
        self.assertEqual(rows["manual-none"], None)
        self.assertEqual(rows["real-goal"], "plan-id")
        self.assertEqual(rows["other-dangling"], "missing-id")
        self.assertEqual(rows["unlinked-none"], "None")
        self.assertEqual(integrity, "ok")

    def test_schema_9_upgrade_adds_work_notes_without_changing_existing_rows(self):
        store = Store(self.root)
        task = store.add_planned_task({"title": "保留的历史行动"})
        with store.connect() as db:
            db.execute("DROP TABLE personal_work_notes")
            db.execute("PRAGMA user_version=9")

        upgraded = Store(self.root)
        with upgraded.connect() as db:
            row = db.execute("SELECT title FROM planned_tasks WHERE id=?", (task["id"],)).fetchone()
            notes_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_work_notes'"
            ).fetchone()
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(user_version(upgraded.path), Store.SCHEMA_VERSION)
        self.assertEqual(row[0], "保留的历史行动")
        self.assertIsNotNone(notes_table)
        self.assertEqual(integrity, "ok")

    def test_current_version_skips_migration(self):
        Store(self.root)
        marker = sqlite3.connect(self.root / "manager.sqlite3")
        marker.execute("DROP TABLE planning_deletions")  # would be recreated if migration reran
        marker.commit()
        marker.close()
        store = Store(self.root)
        self.assertNotIn("planning_deletions", tables(store.path))


if __name__ == "__main__":
    unittest.main()
