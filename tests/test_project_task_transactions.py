"""One-time project task migration and SQLite mutation behavior."""
from pathlib import Path
import threading
import unittest

from tests.support import TestDirectory
from selfManger.domain import UserError
from selfManger.project_task_documents import (
    END, _block, _parse_rows, _render, preview_legacy_project_task_import,
    import_legacy_project_task_documents,
)
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store


class ProjectTaskTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = TestDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "data")
        self.workspace = ProjectWorkspace(self.root / "workspace")
        self.workspace.initialize(TaskContext())
        created = self.workspace.create("迁移验证", "虚构项目", False, TaskContext())
        self.identity = created["project_id"]
        self.project = self.workspace.project(self.identity)
        self.path = self.workspace.path(self.project["path"] + "/agent/TASKS-AI.md")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("# 历史说明\n\n" + _render("ai", []) + "\n保留尾注\n", encoding="utf-8")

    def write_rows(self, rows):
        original = self.path.read_text(encoding="utf-8")
        before, _body, after = _block(original)
        updated = before + _render("ai", rows) + after
        self.workspace.write(self.path, updated, original)

    def test_one_time_import_preserves_existing_database_values_and_original_file(self):
        existing = self.store.add_planned_task({
            "title": "数据库标题", "source": "ai", "project_id": self.identity,
            "project_root": str(self.workspace.root),
        })
        self.write_rows([
            {**existing, "title": "旧文档冲突值"},
            {"id": "new", "title": "仅旧文档中的任务", "kind": "task", "status": "planned", "notes": "迁移备注"},
        ])
        source_before = self.path.read_bytes()
        preview = preview_legacy_project_task_import(self.store, self.workspace, self.identity)
        self.assertEqual((preview["added"], preview["changed"]), (1, 1))
        result = import_legacy_project_task_documents(self.store, self.workspace, self.identity, preview_token=preview["token"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.store.planned_task(existing["id"])["title"], "数据库标题")
        self.assertIn("仅旧文档中的任务", {row["title"] for row in self.store.planned_tasks()})
        self.assertEqual(self.path.read_bytes(), source_before)

        self.write_rows([{ "id": "new", "title": "迁移后编辑", "kind": "task", "status": "planned" }])
        repeated = import_legacy_project_task_documents(self.store, self.workspace, self.identity)
        self.assertTrue(repeated["already_imported"])
        self.assertEqual(self.path.read_text(encoding="utf-8").count("迁移后编辑"), 1)
        self.assertEqual(len(self.store.planned_tasks()), 2)

    def test_preview_token_rejects_source_change_and_preserves_database(self):
        self.write_rows([{"id": "new", "title": "待迁移", "kind": "task", "status": "planned"}])
        preview = preview_legacy_project_task_import(self.store, self.workspace, self.identity)
        changed = self.path.read_text(encoding="utf-8").replace("待迁移", "预览后修改")
        self.path.write_text(changed, encoding="utf-8")
        with self.assertRaisesRegex(UserError, "预览后"):
            import_legacy_project_task_documents(self.store, self.workspace, self.identity, preview_token=preview["token"])
        self.assertEqual(self.store.planned_tasks(), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), changed)

    def test_malformed_legacy_rows_are_rejected_without_writing_source(self):
        original = self.path.read_text(encoding="utf-8")
        malformed = original.replace(END, "| new | 坏行 | planned | - | - | task | - | - | - | extra |\n" + END)
        self.path.write_text(malformed, encoding="utf-8")
        with self.assertRaisesRegex(UserError, "列数"):
            preview_legacy_project_task_import(self.store, self.workspace, self.identity)
        self.assertEqual(self.path.read_text(encoding="utf-8"), malformed)

    def test_database_edits_and_stale_markdown_do_not_block_or_overwrite(self):
        task = self.store.add_planned_task({
            "title": "数据库任务", "source": "ai", "project_id": self.identity,
            "project_root": str(self.workspace.root),
        })
        changed = self.path.read_text(encoding="utf-8") + "\n用户历史说明\n"
        self.path.write_text(changed, encoding="utf-8")
        updated = self.store.update_planned_task(task["id"], {**task, "title": "数据库编辑"})
        self.assertEqual(updated["title"], "数据库编辑")
        self.assertEqual(self.path.read_text(encoding="utf-8"), changed)
        self.assertEqual(self.store.planned_task(task["id"])["title"], "数据库编辑")

    def test_concurrent_stale_database_editors_only_one_succeeds(self):
        task = self.store.add_planned_task({"title": "原任务", "source": "manual"})
        gate, results = threading.Barrier(2), []

        def edit(title):
            gate.wait(timeout=5)
            try:
                self.store.update_planned_task(task["id"], {**task, "title": title, "_expected": task})
                results.append("saved")
            except UserError:
                results.append("conflict")

        threads = [threading.Thread(target=edit, args=(title,)) for title in ("编辑甲", "编辑乙")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["saved", "conflict"])

    def test_legacy_row_parser_keeps_escaped_network_paths(self):
        note = r"\\server\share\folder | C:\one\two" + "\n第二行"
        body = _block(_render("ai", [{"id": "new", "title": "路径", "notes": note}]))[1]
        rows = _parse_rows(body, "ai", self.identity, self.workspace.root)
        self.assertEqual(rows[0]["notes"], note)

    def test_legacy_project_task_parser_normalizes_textual_none_goal(self):
        row = {"id": "new", "title": "无计划任务", "goal_id": "None"}
        body = _block(_render("ai", [row]))[1]
        rows = _parse_rows(body, "ai", self.identity, self.workspace.root)
        self.assertIsNone(rows[0]["goal_id"])


if __name__ == "__main__":
    unittest.main()
