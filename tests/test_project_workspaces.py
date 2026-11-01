from __future__ import annotations

import json
import unittest
from tests.support import TestDirectory
from pathlib import Path
from unittest.mock import Mock, patch

from selfManger.domain import UserError
from selfManger.project_workspaces import (ProjectWorkspace, MANAGEMENT_FILES, OPTIONAL_MANAGEMENT_FILES,
                                           field, optional_field, replace_frontmatter_value, replace_state)
from selfManger.runtime import TaskContext


class ProjectWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TestDirectory()
        self.root = Path(self.temporary.name) / "workspace"
        self.workspace = ProjectWorkspace(self.root)
        self.context = TaskContext()
        self.workspace.initialize(self.context)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, name="验证项目", entry=False):
        return self.workspace.create(name, "完成可核验交付", entry, self.context)

    def test_malformed_registry_json_shapes_raise_user_error(self):
        self.create()
        valid = json.loads(self.workspace.registry_path.read_text(encoding="utf-8"))
        malformed_project = valid["projects"][0]
        field_cases = []
        for field in ("id", "name", "path", "folder_name", "vault_entry", "link_policy"):
            changed = dict(malformed_project)
            changed[field] = 17
            field_cases.append({"schema_version": 2, "projects": [changed],
                                "issued_ids": valid["issued_ids"]})
        cases = (
            [],
            {"schema_version": 2, "projects": [None], "issued_ids": []},
            {"schema_version": 2, "projects": [], "issued_ids": [None]},
        ) + tuple(field_cases)
        for payload in cases:
            with self.subTest(payload=payload):
                self.workspace.registry_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(UserError):
                    self.workspace.registry()
        self.workspace.registry_path.write_text(json.dumps(valid), encoding="utf-8")

    def test_create_uses_one_status_task_log_source_and_never_reinitializes_existing(self):
        project = self.create()
        directory = Path(project["directory"])
        self.assertTrue(all((directory / name).is_file() for name in MANAGEMENT_FILES if name not in OPTIONAL_MANAGEMENT_FILES))
        self.assertEqual(field((directory / "agent/STATUS.md").read_text(encoding="utf-8"), "status"), "active")
        self.assertFalse((directory / ".git").exists())
        self.assertFalse((directory / "src").exists())
        with self.assertRaises(UserError):
            self.create()
        with self.assertRaises(UserError):
            self.workspace.initialize(self.context)

    def test_project_detail_files_collects_read_errors_and_optional_milestones(self):
        project = self.create()
        identity = project["project_id"]
        task_path = Path(project["directory"]) / "agent/TASKS.md"
        task_path.unlink()
        (Path(project["directory"]) / "agent/MILESTONES.md").unlink()

        report = self.workspace.project_detail_files(
            identity, ("agent/STATUS.md", "agent/TASKS.md", "agent/HANDOFF.md"), self.context
        )

        self.assertEqual(len(report["documents"]), 3)
        self.assertIn("项目", report["documents"][0]["text"])
        self.assertEqual(report["documents"][1]["text"], "")
        self.assertEqual(report["documents"][1]["path"], str(task_path))
        self.assertIn("agent/TASKS.md", report["errors"][0])
        self.assertEqual(report["milestones"]["text"], "")
        self.assertFalse(report["milestones"]["exists"])
        self.assertEqual(report["project_documents"]["documents"], [])
        self.assertEqual(report["notes"], [])

    def test_project_detail_files_reports_partial_directory_read_failures(self):
        project = self.create()
        with patch.object(ProjectWorkspace, "project_documents", side_effect=PermissionError("documents locked")), \
             patch.object(ProjectWorkspace, "notes", side_effect=PermissionError("notes locked")):
            report = self.workspace.project_detail_files(
                project["project_id"], ("agent/STATUS.md",), self.context
            )

        self.assertEqual(report["project_documents"]["documents"], [])
        self.assertIn("documents locked", report["project_documents_error"])
        self.assertTrue(report["project_documents"]["directory"].endswith("文档"))
        self.assertEqual(report["notes"], [])
        self.assertEqual(report["notes_error"], "notes locked")

    def test_archive_and_resume_preserve_business_data_number_and_log_history(self):
        project = self.create()
        registered = self.workspace.project(project["project_id"])
        registry_before = self.workspace.registry_path.read_bytes()
        self.assertEqual(registered["path"], Path(project["directory"]).relative_to(self.root).as_posix())
        self.assertNotIn("state", registered)
        directory = Path(project["directory"])
        (directory / "payload.bin").write_bytes(bytes(range(256)))
        (directory / "空目录").mkdir()
        self.workspace.append_log(project["project_id"], "第一次真实记录", self.context)
        result = self.workspace.set_archived(project["project_id"], True, self.context)
        archived = Path(result["path"])
        self.assertTrue(directory.exists())
        self.assertFalse(result["moved"])
        self.assertEqual((archived / "payload.bin").read_bytes(), bytes(range(256)))
        self.assertTrue((archived / "空目录").is_dir())
        self.assertEqual(field((archived / "agent/STATUS.md").read_text(encoding="utf-8"), "status"), "archived")
        self.assertEqual(self.workspace.registry_path.read_bytes(), registry_before)
        self.assertEqual(self.workspace.list_projects(self.context)["projects"][0]["state"], "archived")
        with self.assertRaises(UserError):
            self.workspace.append_log(project["project_id"], "不允许写入归档", self.context)
        resumed = self.workspace.set_archived(project["project_id"], False, self.context)
        self.assertEqual(resumed["project_id"], project["project_id"])
        self.assertEqual(resumed["path"], project["directory"])
        self.assertEqual(resumed["state"], "active")
        self.assertEqual(self.workspace.registry_path.read_bytes(), registry_before)
        log = (directory / "agent/HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("第一次真实记录", log)

    def test_archive_resume_restores_prior_state_and_handles_legacy_or_invalid_metadata(self):
        for state in ("paused", "completed"):
            project = self.create(name=f"恢复{state}")
            status_path = Path(project["directory"]) / "agent/STATUS.md"
            status_path.write_text(replace_state(status_path.read_text(encoding="utf-8"), state), encoding="utf-8")

            self.workspace.set_archived(project["project_id"], True, self.context)
            archived = status_path.read_text(encoding="utf-8")
            self.assertEqual(field(archived, "archived_from"), state)

            self.workspace.set_archived(project["project_id"], False, self.context)
            restored = status_path.read_text(encoding="utf-8")
            self.assertEqual(field(restored, "status"), state)
            self.assertIsNone(optional_field(restored, "archived_from"))

        legacy = self.create(name="旧归档")
        legacy_status = Path(legacy["directory"]) / "agent/STATUS.md"
        legacy_status.write_text(replace_state(legacy_status.read_text(encoding="utf-8"), "archived"), encoding="utf-8")
        self.workspace.set_archived(legacy["project_id"], False, self.context)
        self.assertEqual(field(legacy_status.read_text(encoding="utf-8"), "status"), "active")

        invalid = self.create(name="无效归档元数据")
        invalid_status = Path(invalid["directory"]) / "agent/STATUS.md"
        malformed = replace_frontmatter_value(
            replace_state(invalid_status.read_text(encoding="utf-8"), "archived"),
            "archived_from", "unknown",
        )
        invalid_status.write_text(malformed, encoding="utf-8")
        with self.assertRaisesRegex(UserError, "归档前状态无效"):
            self.workspace.set_archived(invalid["project_id"], False, self.context)
        self.assertEqual(invalid_status.read_text(encoding="utf-8"), malformed)

    def test_failed_upload_cleanup_error_names_partial_project_copy(self):
        project = self.create()
        source = self.root / "outside.txt"
        source.write_bytes(b"x" * (1024 * 1024 * 2))
        target = Path(project["directory"]) / "文档" / source.name
        context = Mock()
        context.checkpoint.side_effect = [None, RuntimeError("cancelled")]
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == target:
                raise PermissionError("file is locked")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            with self.assertRaisesRegex(UserError, "未能清理可能不完整的副本") as raised:
                self.workspace.upload_project_document(project["project_id"], source, context)
        self.assertIn(str(target), str(raised.exception))
        self.assertIn("file is locked", str(raised.exception))
        self.assertEqual(target.stat().st_size, 1024 * 1024)


    def test_reservation_survives_partial_creation_failure_and_number_is_not_reused(self):
        with patch.object(self.workspace, "_link", side_effect=UserError("injected link failure")):
            with self.assertRaises(UserError):
                self.create("失败项目", True)
        reserved = self.workspace.registry()["issued_ids"]
        self.assertEqual(len(reserved), 1)
        self.assertTrue((self.root / "projects" / (reserved[0] + "-失败项目")).exists())
        with self.assertRaises(UserError):
            self.create("不能盲目重跑")

    def test_indexes_preserve_manual_content_and_edit_rejects_stale_original(self):
        index = self.root / "myself/03-项目/项目总览.md"
        index.write_text(index.read_text(encoding="utf-8") + "\n人工说明必须保留\n", encoding="utf-8")
        project = self.create()
        self.assertIn("人工说明必须保留", index.read_text(encoding="utf-8"))
        original = self.workspace.document(project["project_id"], "agent/STATUS.md")["text"]
        path = Path(project["directory"]) / "agent/STATUS.md"
        path.write_text(original + "\n其他助手的修改\n", encoding="utf-8")
        with self.assertRaises(UserError):
            self.workspace.save_document(project["project_id"], "agent/STATUS.md", original, original, self.context)
        self.assertIn("其他助手的修改", path.read_text(encoding="utf-8"))

    def test_existing_manage_lock_and_path_traversal_are_rejected(self):
        lock = self.root / "agent/.manage.lock"
        lock.write_text("another tool")
        with self.assertRaises(UserError):
            self.create()
        self.assertTrue(lock.exists())
        for path in ("../outside", "projects/name:stream", "/outside"):
            with self.assertRaises(UserError):
                self.workspace.path(path)

    def test_obsidian_link_follows_archive_and_resume_without_copying_project(self):
        project = self.create(entry=True)
        item = self.workspace.project(project["project_id"])
        entry = self.root / item["vault_entry"]
        self.assertFalse(entry.is_symlink())
        self.assertEqual((entry / "agent").resolve(), (Path(project["directory"]) / "agent").resolve())
        result = self.workspace.set_archived(project["project_id"], True, self.context)
        self.assertFalse(entry.exists())
        self.assertEqual(Path(result["path"]), Path(project["directory"]))
        result = self.workspace.set_archived(project["project_id"], False, self.context)
        self.assertEqual((entry / "agent").resolve(), (Path(result["path"]) / "agent").resolve())




if __name__ == "__main__":
    unittest.main()
