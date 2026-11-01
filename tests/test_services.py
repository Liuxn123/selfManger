from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch
from tests.support import TestDirectory
from pathlib import Path

from selfManger import archives
from selfManger.application import ApplicationService, MANAGER_BACKUP_ID
from selfManger.action_center import ActionCenter
from selfManger.domain import Resource, UserError
from selfManger.runtime import ResourceLocks, TaskContext, run_process
from selfManger.security import redact, safe_result
from selfManger.storage import Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TestDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")

    def test_developer_tools_validate_targets_and_persist_only_in_sqlite(self):
        service = ApplicationService(self.store)
        executable = self.root / "editor.exe"
        executable.write_bytes(b"fixture")
        item = service.save_developer_tool({
            "name": "编辑器", "kind": "application", "target": str(executable), "summary": "本地开发"
        })
        self.assertEqual(service.developer_tools(), [item])
        self.assertEqual(item["target"], str(executable.resolve()))
        updated = service.save_developer_tool({
            "name": "编辑器", "kind": "website", "target": "https://example.com", "summary": "网页入口"
        }, item["id"])
        self.assertEqual(updated["id"], item["id"])
        self.assertEqual(updated["created_at"], item["created_at"])
        self.assertEqual(ApplicationService(self.store).developer_tool(item["id"]), updated)
        for target in ("file:///etc/passwd", "https://user:password@example.com", "http://[broken",
                       "https://example.com:99999"):
            with self.subTest(target=target), self.assertRaises(UserError):
                service.save_developer_tool({"name": "bad", "kind": "website", "target": target})
        for target in ("https://example.com/?api_key=secret", "https://example.com/?Token=secret",
                       "https://example.com/#access_token=secret", "https://example.com/?client-secret=x"):
            with self.subTest(target=target), self.assertRaises(UserError):
                service.save_developer_tool({"name": "bad", "kind": "website", "target": target})
        with self.assertRaises(UserError):
            service.save_developer_tool({"name": "missing", "kind": "application", "target": str(self.root / "missing.exe")})
        service.remove_developer_tool(item["id"])
        self.assertEqual(service.developer_tools(), [])

    def test_resource_lifecycle_uses_only_registered_adapter_types(self):
        service = ApplicationService(self.store)
        project = Resource("项目", "project", {"path": str(self.root)})
        self.assertTrue(service.supports_resource_kind("project"))
        self.assertFalse(service.supports_resource_kind("agent"))
        self.assertIs(service.save_resource(project), project)
        self.assertEqual(self.store.resources(), [project])
        legacy = Resource("旧 Agent", "agent", {})
        with self.assertRaises(UserError):
            service.save_resource(legacy)
        self.assertEqual(self.store.resources(), [project])
        with self.assertRaises(UserError):
            service.remove_resource(MANAGER_BACKUP_ID)
        service.remove_resource(project.id)
        self.assertEqual(self.store.resources(), [])

    def test_backup_settings_are_normalized_at_the_service_boundary(self):
        service = ApplicationService(self.store)
        destination = self.root / "backups" / ".." / "archives"
        self.assertEqual(service.set_backup_root(destination), destination.resolve())
        self.assertEqual(service.backup_root(), destination.resolve())
        self.assertEqual(service.set_backup_retention(0), 1)
        self.assertEqual(service.set_backup_retention(1000), 100)
        self.assertEqual(self.store.setting("backup_keep"), 100)

    def test_backup_versions_own_archive_listing_and_evidence_status(self):
        service = ApplicationService(self.store)
        resource = Resource("测试资料", "folder", {"path": str(self.root / "source")})
        backup_root = self.root / "backups"
        backup_root.mkdir()
        paths = [backup_root / "older.amb", backup_root / "newer.amb"]
        timestamps = ["2026-10-31T10:00:00+00:00", "2026-11-01T10:00:00+00:00"]
        for path, timestamp in zip(paths, timestamps):
            path.write_bytes(path.name.encode("ascii"))
            path.with_suffix(".json").write_text(json.dumps({
                "resource_id": resource.id, "created_at": timestamp, "file_count": 1,
                "size": path.stat().st_size,
            }), encoding="utf-8")
            service.store.save_evidence(str(path.resolve()), {
                "file_size": path.stat().st_size, "modified_ns": path.stat().st_mtime_ns,
                "verified_at": timestamp,
                **({"rehearsed_at": timestamp} if path == paths[1] else {}),
            })

        with patch.object(archives, "list_archives", wraps=archives.list_archives) as listing:
            inventory = service.backup_inventory(resource, backup_root)
        self.assertEqual(listing.call_count, 1)
        versions = inventory["versions"]

        self.assertEqual([item["archive"] for item in versions], [str(paths[1]), str(paths[0])])
        self.assertEqual([item["integrity"] for item in versions], ["已演练", "已校验"])
        self.assertTrue(inventory["health"]["verified"])
        self.assertTrue(inventory["health"]["rehearsed"])
        paths[1].write_bytes(b"changed")
        refreshed = service.backup_inventory(resource, backup_root)
        self.assertEqual(refreshed["versions"][0]["integrity"], "待校验")
        self.assertFalse(refreshed["health"]["verified"])

    def test_workspace_settings_are_normalized_at_the_service_boundary(self):
        service = ApplicationService(self.store)
        project_root = self.root / "projects" / ".." / "workspace"
        workbench_root = self.root / "notes" / ".." / "daily"
        self.assertEqual(service.project_workspace_root(), "")
        self.assertEqual(service.set_project_workspace_root(project_root), project_root.resolve())
        self.assertEqual(service.set_workbench_root(workbench_root), workbench_root.resolve())
        self.assertEqual(service.project_workspace_root(), str(project_root.resolve()))
        self.assertEqual(self.store.setting("project_workspace"), str(project_root.resolve()))
        self.assertEqual(self.store.setting("workbench_root"), str(workbench_root.resolve()))

    def test_appearance_setting_accepts_only_supported_modes(self):
        service = ApplicationService(self.store)
        self.assertEqual(service.set_appearance("dark"), "dark")
        self.assertEqual(self.store.setting("appearance"), "dark")
        for invalid in ("system", ["dark"], {"mode": "dark"}):
            with self.subTest(invalid=invalid), self.assertRaises(UserError):
                service.set_appearance(invalid)
        self.assertEqual(self.store.setting("appearance"), "dark")

    def test_planning_draft_persistence_rejects_non_object_values(self):
        service = ApplicationService(self.store)
        draft = {"fields": ["task", "标题"]}
        service.save_planning_draft(draft)
        self.assertEqual(service.planning_draft(), draft)
        with self.assertRaises(UserError):
            service.save_planning_draft(["invalid"])
        self.assertEqual(service.planning_draft(), draft)

    def test_planned_task_read_uses_current_sqlite_record_and_hides_deleted_rows(self):
        service = ApplicationService(self.store)
        task = self.store.add_planned_task({"title": "服务读取"})

        self.assertEqual(service.planned_task(task["id"]), task)
        ActionCenter(self.store).delete_record("action", task["id"], expected=task)
        self.assertIsNone(service.planned_task(task["id"]))

    def test_operation_history_writes_go_through_application_service(self):
        service = ApplicationService(self.store)
        service.start_task("operation-1", "system", "隔离操作")
        service.append_log("operation-1", "started")
        service.finish_task("operation-1", "success", {"ok": True})
        record = self.store.task("operation-1")
        self.assertEqual(record["state"], "success")
        self.assertIn("started", record["log"])
        self.assertEqual(json.loads(record["result"]), {"ok": True})

    def test_project_usage_evidence_is_recorded_by_the_service(self):
        service = ApplicationService(self.store)
        service.record_project_used("project-identity")
        self.assertTrue(self.store.evidence("project-used:project-identity")["at"])

    def test_ui_query_facade_preserves_registered_rows_and_operation_summaries(self):
        service = ApplicationService(self.store)
        resource = Resource("旧 Agent 登记", "agent", {"legacy": True})
        self.store.save_resource(resource)
        service.start_task("query-task", resource.id, "查询记录")
        service.finish_task("query-task", "success", {"ok": True})
        self.store.save_evidence("query-evidence", {"state": "checked"})

        self.assertEqual(service.resources(), [resource])
        self.assertEqual(service.setting("missing-query-setting", "fallback"), "fallback")
        self.assertEqual(service.task_summaries(limit=5, resource_id=resource.id)[0]["id"], "query-task")
        self.assertEqual(service.task("query-task")["state"], "success")
        self.assertEqual(service.evidence("query-evidence"), {"state": "checked"})
        self.assertEqual(service.backup_health(resource, self.root / "backups")["state"], "不支持")
        self.assertFalse(service.backup_due(resource))
        self.assertEqual(service.database_path, self.store.path)

    def test_config_round_trip_merges_without_overwriting_or_exporting_tasks(self):
        resource = Resource("project", "project", {"path": str(self.root)})
        self.store.save_resource(resource)
        self.store.start_task("task1", resource.id, "test")
        exported = self.root / "config.json"
        ApplicationService(self.store).export_config(exported)
        self.assertNotIn("tasks", json.loads(exported.read_text()))
        changed = Resource("renamed", "project", resource.options, resource.id)
        self.store.save_resource(changed)
        self.assertEqual(ApplicationService(self.store).import_config(exported), 0)
        self.assertEqual(self.store.resources()[0].name, "renamed")
        self.store.recover_interrupted()
        self.assertEqual(self.store.tasks()[0]["state"], "interrupted")

    def test_plaintext_credentials_in_config_are_rejected_before_any_import(self):
        resource = Resource("project", "project", {"password": "test-value"})
        with self.assertRaises(UserError):
            self.store.save_resource(resource)
        exported = self.root / "bad.json"
        exported.write_text(json.dumps({"schema_version": 1, "resources": [Resource("okay", "project", {}).to_dict(), resource.to_dict()]}))
        with self.assertRaises(UserError):
            self.store.import_config(exported)
        self.assertFalse(self.store.resources())

    def test_parent_and_child_paths_are_locked_across_distinct_resources(self):
        locks = ResourceLocks()
        with locks.acquire(["resource:a", "path:" + str(self.root)]):
            with self.assertRaises(UserError):
                with locks.acquire(["resource:b", "path:" + str(self.root / "child")]):
                    pass
        with locks.acquire(["path:" + str(self.root)]):
            pass

    def test_process_output_is_bounded_and_credentials_not_in_error(self):
        with self.assertRaises(UserError) as caught:
            run_process([sys.executable, "-c", "import sys;sys.stderr.write('token=fixture-secret');sys.exit(1)"], TaskContext())
        self.assertNotIn("fixture-secret", str(caught.exception))
        with self.assertRaises(UserError):
            run_process([sys.executable, "-c", "import sys;sys.stdout.write('x'*10000000)"], TaskContext(), timeout=10)



    def test_redaction_preserves_status_but_removes_credential_values(self):
        data = safe_result({"password": "fixture", "secrets_present": True, "result": "token=fixture-value"})
        self.assertEqual(data["password"], "[已隐藏]")
        self.assertTrue(data["secrets_present"])
        self.assertNotIn("fixture-value", data["result"])


if __name__ == "__main__":
    unittest.main()
