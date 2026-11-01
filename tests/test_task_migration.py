"""Legacy Markdown task migration into the SQLite planning store."""
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.support import TestDirectory
from selfManger.domain import UserError
from selfManger.storage import Store
from selfManger.task_migration import migrate_legacy_tasks
from selfManger.workbench import LegacyDailyReader


class LegacyDailyMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = TestDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.notes = self.root / "notes"
        self.store = Store(self.root / "data")

    def test_daily_markdown_is_imported_once_and_later_edits_are_not_resynced(self):
        day = date(2026, 10, 5)
        relative = Path("每日") / (day.isoformat() + ".md")
        path = self.notes / relative
        path.parent.mkdir(parents=True)
        original = (LegacyDailyReader(self.notes, day).template()
                    .replace("## 日程\n", "## 日程\n- 09:00–10:00 项目评审\n")
                    .replace("## 今日任务\n", "## 今日任务\n- [ ] [高] 整理验收记录 @14:00\n"))
        path.write_text(original, encoding="utf-8")
        original_bytes = path.read_bytes()

        imported = migrate_legacy_tasks(self.store, self.notes)
        self.assertEqual(imported["daily"], 2)
        rows = self.store.planned_tasks(due_date=day.isoformat())
        self.assertEqual({row["title"] for row in rows}, {"项目评审", "整理验收记录"})
        by_title = {row["title"]: row for row in rows}
        self.assertEqual(by_title["项目评审"]["scheduled_time"], "09:00–10:00")
        self.assertEqual(by_title["整理验收记录"]["scheduled_time"], "14:00")
        self.assertEqual(len({row["source_ref"] for row in rows}), 2)
        self.assertEqual(path.read_bytes(), original_bytes)

        changed = original.replace("## 工作记录", "## 今日任务\n- 外部新增但不再自动导入\n\n## 工作记录")
        path.write_text(changed, encoding="utf-8")
        later = self.notes / "每日" / "2026-10-06.md"
        later.write_text(LegacyDailyReader(self.notes, date(2026, 10, 6)).template().replace(
            "## 今日任务\n", "## 今日任务\n- 新日期文件也不再自动导入\n"), encoding="utf-8")

        second = migrate_legacy_tasks(self.store, self.notes)
        self.assertEqual(second["daily"], 0)
        self.assertEqual(len(self.store.planned_tasks()), 2)
        self.assertEqual(path.read_text(encoding="utf-8"), changed)
        self.assertEqual({row["title"] for row in rows}, {"项目评审", "整理验收记录"})
        self.assertNotIn("外部新增但不再自动导入", {row["title"] for row in self.store.planned_tasks()})
        self.assertNotIn("新日期文件也不再自动导入", {row["title"] for row in self.store.planned_tasks()})

    def test_invalid_date_filename_aborts_import_without_marking_migration_complete(self):
        directory = self.notes / "每日"
        directory.mkdir(parents=True)
        source = directory / "2026-99-99.md"
        source.write_text("## 今日任务\n- 保留但不静默跳过\n", encoding="utf-8")
        original = source.read_bytes()

        with self.assertRaisesRegex(UserError, "每日文件名日期无效"):
            migrate_legacy_tasks(self.store, self.notes)

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.store.planned_tasks(), [])
        self.assertIsNone(self.store.setting("legacy_daily_task_import_v2"))

    def test_symlinked_daily_file_aborts_import_without_following_or_marking(self):
        directory = self.notes / "每日"
        directory.mkdir(parents=True)
        source = directory / "2026-10-05.md"
        source.write_text("## 今日任务\n- 链接目标内容不应被读取\n", encoding="utf-8")

        with patch.object(type(source), "is_symlink", autospec=True,
                          side_effect=lambda path: path.name == source.name):
            with self.assertRaisesRegex(UserError, "每日目录包含链接文件"):
                migrate_legacy_tasks(self.store, self.notes)

        self.assertEqual(self.store.planned_tasks(), [])
        self.assertIsNone(self.store.setting("legacy_daily_task_import_v2"))


if __name__ == "__main__":
    unittest.main()
