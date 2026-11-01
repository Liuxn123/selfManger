"""Project task behavior after SQLite becomes the only mutable source."""
from pathlib import Path
import unittest

from tests.support import TestDirectory
from selfManger.domain import UserError
from selfManger.project_task_documents import (
    _block, _render, preview_legacy_project_task_import, import_legacy_project_task_documents,
)
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store


class ProjectTaskSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = TestDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "data")
        self.workspace = ProjectWorkspace(self.root / "workspace")
        self.workspace.initialize(TaskContext())
        created = self.workspace.create("SQLite 项目任务", "虚构数据", False, TaskContext())
        self.identity = created["project_id"]
        self.project = self.workspace.project(self.identity)
        self.relative = self.project["path"] + "/agent/TASKS-AI.md"
        self.path = self.workspace.path(self.relative)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_render("ai", []), encoding="utf-8")
        manual = self.workspace.path(self.project["path"] + "/agent/TASKS-MANUAL.md")
        manual.write_text(_render("manual", []), encoding="utf-8")

    def test_create_edit_complete_and_delete_do_not_write_legacy_documents(self):
        before = {path: path.read_bytes() for path in self.workspace.root.rglob("TASKS-*.md")}
        task = self.store.add_planned_task({
            "title": "数据库中新建", "source": "ai", "project_id": self.identity,
            "project_root": str(self.workspace.root),
        })
        self.assertEqual(self.store.planned_task(task["id"])["title"], "数据库中新建")
        edited = self.store.update_planned_task(task["id"], {**task, "title": "数据库中编辑"})
        self.assertEqual(edited["title"], "数据库中编辑")
        done = self.store.set_planned_task_status(task["id"], "done", manual_override=True)
        self.assertEqual(done["status"], "done")
        self.store.delete_planned_task(task["id"])
        self.assertIsNone(self.store.planned_task(task["id"]))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_one_time_import_keeps_files_and_existing_database_values(self):
        task = self.store.add_planned_task({
            "title": "数据库权威标题", "source": "ai", "project_id": self.identity,
            "project_root": str(self.workspace.root),
        })
        original = self.path.read_text(encoding="utf-8")
        before, _body, after = _block(original)
        old_row = {**task, "title": "文档中的旧标题"}
        new_row = {"id": "new", "title": "只在旧文档中的任务", "kind": "task",
                   "status": "planned", "source": "ai", "notes": "旧备注"}
        imported_text = before + _render("ai", [old_row, new_row]) + after
        self.workspace.write(self.path, imported_text, original)
        originals = {path: path.read_bytes() for path in self.workspace.root.rglob("TASKS-*.md")}

        preview = preview_legacy_project_task_import(self.store, self.workspace, self.identity)
        self.assertEqual((preview["added"], preview["changed"]), (1, 1))
        report = import_legacy_project_task_documents(self.store, self.workspace, self.identity,
                                             preview_token=preview["token"])
        self.assertEqual(report["imported"], 1)
        self.assertEqual(self.store.planned_task(task["id"])["title"], "数据库权威标题")
        self.assertIn("只在旧文档中的任务", {row["title"] for row in self.store.planned_tasks()})
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)

        later_before, _later_body, later_after = _block(imported_text)
        later_row = {"id": "new", "title": "后来追加", "kind": "task",
                     "status": "planned", "source": "ai"}
        later = later_before + _render("ai", [old_row, new_row, later_row]) + later_after
        self.workspace.write(self.path, later, imported_text)
        repeated = import_legacy_project_task_documents(self.store, self.workspace, self.identity)
        self.assertTrue(repeated["already_imported"])
        self.assertEqual(len(self.store.planned_tasks()), 2)
        self.assertEqual(self.path.read_text(encoding="utf-8"), later)

    def test_preview_token_rejects_changed_legacy_source_without_writing_it(self):
        original = self.path.read_text(encoding="utf-8")
        before, _body, after = _block(original)
        new_row = {"id": "new", "title": "待迁移任务", "kind": "task", "status": "planned", "source": "ai"}
        first = before + _render("ai", [new_row]) + after
        self.workspace.write(self.path, first, original)
        preview = preview_legacy_project_task_import(self.store, self.workspace, self.identity)
        changed = first.replace("待迁移任务", "用户后来编辑")
        self.workspace.write(self.path, changed, first)
        with self.assertRaisesRegex(UserError, "预览后"):
            import_legacy_project_task_documents(self.store, self.workspace, self.identity,
                                        preview_token=preview["token"])
        self.assertEqual(self.store.planned_tasks(), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), changed)

    def test_database_mutations_ignore_later_markdown_edits_and_preserve_them(self):
        task = self.store.add_planned_task({
            "title": "已有任务", "source": "manual", "project_id": self.identity,
            "project_root": str(self.workspace.root),
        })
        changed = self.path.read_text(encoding="utf-8") + "\n用户历史说明\n"
        self.path.write_text(changed, encoding="utf-8")
        self.store.set_planned_task_status(task["id"], "active")
        self.assertEqual(self.store.planned_task(task["id"])["status"], "active")
        self.assertEqual(self.path.read_text(encoding="utf-8"), changed)


if __name__ == "__main__":
    unittest.main()
