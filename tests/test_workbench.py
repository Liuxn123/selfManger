import concurrent.futures
import json
import shutil
import threading
import unittest
from tests.support import TestDirectory
import os
from unittest.mock import patch
from datetime import date
from pathlib import Path

from selfManger.domain import Resource, UserError
from selfManger.storage import Store
from selfManger.runtime import TaskContext
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.workbench import (LegacyDailyReader, MarkdownFiles, work_root, migrate_workbench,
    project_context, append_stage, summaries, stage_template, markdown_uri)
from selfManger.planbook import PlanBook, occurs_on
from tests.legacy_plan_fixtures import write_legacy_plan


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_legacy_daily_reader_parses_without_modifying_the_source(self):
        daily = LegacyDailyReader(self.root, date(2026, 10, 5))
        original = "# 2026-10-05\n\n## 日程\n- 09:00 开发\n## 今日任务\n- 测试恢复\n- [x] 阶段总结\n## 工作记录\n人工笔记\n"
        path = self.root / daily.relative
        path.parent.mkdir(parents=True)
        path.write_text(original, encoding="utf-8")
        before = path.read_bytes()
        report = daily.load()
        self.assertEqual(report["schedule"][0]["text"], "09:00 开发")
        self.assertTrue(report["tasks"][1]["done"])
        self.assertIn("人工笔记", report["text"])
        self.assertEqual(path.read_bytes(), before)
        self.assertNotEqual(LegacyDailyReader(self.root, date(2026, 10, 6)).relative, daily.relative)

    def test_read_does_not_create_workspace_and_boundaries_reject_links_and_large_files(self):
        root = self.root / "missing"
        self.assertEqual(LegacyDailyReader(root).load()["tasks"], [])
        self.assertFalse(root.exists())
        for relative in ("../keep", "C:/keep", "resources/name:stream"):
            with self.assertRaises(UserError):
                MarkdownFiles(root).read(relative)
        oversized = self.root / "large.md"
        oversized.write_bytes(b"x" * 1_000_001)
        with self.assertRaises(UserError):
            MarkdownFiles(self.root).read("large.md")

    def test_async_first_read_after_workbench_directory_is_created_stays_in_bounds(self):
        day = date(2026, 10, 31)
        for index in range(50):
            root = self.root / "new-workbench" / str(index)
            reader_ready = threading.Event()
            directory_ready = threading.Event()

            def read_after_setup():
                reader = LegacyDailyReader(root, day)
                reader_ready.set()
                if not directory_ready.wait(timeout=5):
                    raise TimeoutError("workbench fixture was not created")
                return reader.load()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(read_after_setup)
                self.assertTrue(reader_ready.wait(timeout=5))
                self.assertFalse(root.exists())
                daily = root / "每日"
                daily.mkdir(parents=True)
                (daily / f"{day.isoformat()}.md").write_text(
                    f"# {day.isoformat()}\n\n## 今日任务\n- 首次工作台读取\n",
                    encoding="utf-8",
                )
                directory_ready.set()
                report = future.result(timeout=5)

            self.assertEqual(report["tasks"], [{"text": "首次工作台读取", "done": False}])

    def test_plan_book_reads_legacy_fields_without_writing(self):
        root = self.root / "plans"
        book = PlanBook(root)
        self.assertEqual(book.scan()["items"], [])
        self.assertFalse(root.exists())
        path = write_legacy_plan(root, "goal", "a" * 32,
                                 {"title": "年度目标", "status": "active", "start": "2026-01-01",
                                  "due": "2026-12-31", "project_id": "P-TEST-001", "progress": 70,
                                  "custom": "保留用户字段"}, "## 目标\n\n完成首期版本。\n")
        original = path.read_bytes()
        item = book.scan("goal")["items"][0]
        self.assertEqual(item["metadata"]["progress"], 70)
        self.assertEqual(item["metadata"]["project_id"], "P-TEST-001")
        self.assertEqual(item["metadata"]["custom"], "保留用户字段")
        self.assertIn("完成首期版本。", item["body"])
        self.assertEqual(path.read_bytes(), original)

    def test_recurring_plan_projects_daily_weekly_and_month_end_dates_without_writes(self):
        daily = {"type": "recurring", "metadata": {"status": "planned", "frequency": "daily",
                 "start": "2026-10-10", "due": "2026-10-12"}}
        self.assertTrue(occurs_on(daily, date(2026, 10, 11)))
        self.assertFalse(occurs_on(daily, date(2026, 10, 13)))
        weekly = {"type": "recurring", "metadata": {"status": "active", "frequency": "weekly",
                  "start": "2026-10-05"}}
        self.assertTrue(occurs_on(weekly, date(2026, 10, 12)))
        self.assertFalse(occurs_on(weekly, date(2026, 10, 13)))
        monthly = {"type": "recurring", "metadata": {"status": "planned", "frequency": "monthly",
                   "start": "2026-01-31"}}
        self.assertTrue(occurs_on(monthly, date(2026, 2, 28)))
        self.assertTrue(occurs_on(monthly, date(2026, 3, 31)))
        self.assertFalse(occurs_on(monthly, date(2026, 3, 30)))

    def test_windows_busy_read_handle_retries_without_losing_markdown(self):
        files = MarkdownFiles(self.root)
        original = "# legacy document\n"
        files.write("document.md", original, None)
        replace = os.replace
        blocked = PermissionError("Windows sharing violation")
        blocked.winerror = 32
        calls = []
        def temporarily_busy(source, target):
            calls.append(target)
            if len(calls) == 1:
                raise blocked
            return replace(source, target)
        with patch("selfManger.workbench.os.replace", side_effect=temporarily_busy), patch("selfManger.workbench.time.sleep"):
            files.write("document.md", original + "保存成功\n", original)
        self.assertEqual(len(calls), 2)
        self.assertIn("保存成功", files.read("document.md"))
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_external_edit_during_windows_rename_retry_is_never_overwritten(self):
        files = MarkdownFiles(self.root)
        original = "# source\n"
        files.write("document.md", original, None)
        blocked = PermissionError("Windows sharing violation")
        blocked.winerror = 32
        external = original + "Obsidian 新内容\n"
        with patch("selfManger.workbench.os.replace", side_effect=blocked), patch("selfManger.workbench.time.sleep", side_effect=lambda _: (self.root / "document.md").write_text(external, encoding="utf-8")):
            with self.assertRaises(UserError):
                files.write("document.md", "# 不应覆盖\n", original)
        self.assertEqual(files.read("document.md"), external)
        self.assertFalse((self.root / ".write.lock").exists())
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_project_status_is_not_a_resource_association_source(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        context = TaskContext()
        workspace.initialize(context)
        project = workspace.create("兼容项目", "验证既有规范", False, context)
        identity = project["project_id"]
        registry = workspace.registry_path.read_bytes()
        status = workspace.document(identity, "agent/STATUS.md")["text"]
        self.assertEqual(project_context(status)["phase"], "规划")
        original = status.replace("project_id:", "# 人工注释\ncustom: 保留\nresources: [prompt-id]\nproject_id:")
        original = original.replace("- 当前阶段：待明确。", "- 当前阶段：实验探索。")
        workspace.save_document(identity, "agent/STATUS.md", original, status, context)
        updated = workspace.document(identity, "agent/STATUS.md")["text"]
        self.assertIn("# 人工注释\ncustom: 保留", updated)
        self.assertNotIn("resources", project_context(updated))
        self.assertEqual(project_context(updated)["phase"], "实验探索")
        self.assertEqual(workspace.registry_path.read_bytes(), registry)
        append_stage(workspace, identity, "实验探索", stage_template("实验探索") + "\n验证结果：人工记录", context)
        handoff = workspace.document(identity, "agent/HANDOFF.md")["text"]
        self.assertEqual(len(summaries(handoff)), 1)
        self.assertIn("人工记录", summaries(handoff)[0])
        self.assertFalse(list(Path(project["directory"]).rglob("SUMMARY.md")))
        workspace.set_archived(identity, True, context)
        self.assertEqual(project_context(workspace.document(identity, "agent/STATUS.md")["text"])["phase"], "实验探索")
        with self.assertRaises(UserError):
            append_stage(workspace, identity, "实验探索", stage_template("实验探索"), context)

    def test_additive_migration_preserves_existing_registration_settings_and_portable_paths(self):
        portable = self.root / "usb-A"
        portable.mkdir()
        (portable / "portable.json").write_text("{}")
        store = Store(portable / "data")
        resource = Resource("旧 Codex", "agent", {"engine": "Codex", "record_paths": [str(portable / "records")]})
        store.save_resource(resource)
        store.set_setting("workbench_root", str(portable / "notes"))
        store.set_setting("backup_keep", 17)
        store.save_evidence("legacy", {"valid": True})
        migrate_workbench(store)
        migrate_workbench(store)
        self.assertEqual(store.resources()[0].id, resource.id)
        self.assertEqual([Path(p) for p in store.resources()[0].options["record_paths"]], [(portable / "records").resolve()])
        self.assertEqual(store.setting("backup_keep"), 17)
        self.assertTrue(store.evidence("legacy")["valid"])
        daily_path = work_root(store) / "每日" / "2026-10-05.md"
        daily_path.parent.mkdir(parents=True)
        daily_path.write_text("# fixture", encoding="utf-8")
        shutil.copytree(portable, self.root / "usb-B")
        moved = Store(self.root / "usb-B/data")
        self.assertEqual(work_root(moved), (self.root / "usb-B/notes").resolve())
        self.assertEqual(LegacyDailyReader(work_root(moved), date(2026, 10, 5)).load()["text"], "# fixture")
        self.assertNotIn("# fixture", str(moved.setting("workbench_root")))

    def test_obsidian_opens_real_file_and_does_not_create_vault(self):
        path = self.root / "每日" / (date.today().isoformat() + ".md")
        path.parent.mkdir(parents=True)
        path.write_text("# legacy daily\n", encoding="utf-8")
        with self.assertRaises(UserError):
            markdown_uri(path)
        self.assertFalse((self.root / ".obsidian").exists())
        (self.root / ".obsidian").mkdir()
        self.assertTrue(markdown_uri(path).startswith("obsidian://open?"))

    def test_legacy_phase_paragraph_does_not_turn_into_daily_stage_label(self):
        text = "---\nproject_id: P-TEST-001\nstatus: active\n---\n- 当前阶段：" + "原有详细进展说明" * 30 + "\n- 下一步：读取最近验证结果。\n"
        self.assertEqual(project_context(text, "active")["phase"], "进行中")
        self.assertEqual(project_context(text.replace("原有详细进展说明" * 30, "L0 基础了解与概念地图，学习中。后续详细说明保留。"), "active")["phase"], "L0 基础了解与概念地图，学习中")
        self.assertIn("原有详细进展说明", text)
