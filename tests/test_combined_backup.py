import json
import os
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from tests.support import TestDirectory
from selfManger import archives
from selfManger.application import ApplicationService
from selfManger.combined_backup import capture
from selfManger.domain import UserError, Resource
from selfManger.application import OVERALL_BACKUP_ID
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store


class CombinedBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.personal = self.root / "个人计划"
        self.personal.mkdir()
        (self.personal / "今日.md").write_text("今日计划", encoding="utf-8")
        self.workspace = ProjectWorkspace(self.root / "workspace")
        self.workspace.initialize(TaskContext())
        self.created = self.workspace.create("虚构项目", "隔离", False, TaskContext())
        self.project = Path(self.created["directory"])
        (self.project / "attachment.bin").write_bytes(b"not included")
        worktree_markdown = self.project / ".worktrees" / "temporary" / "agent" / "STATUS.md"
        worktree_markdown.parent.mkdir(parents=True)
        worktree_markdown.write_text("temporary checkout copy", encoding="utf-8")
        self.store.set_setting("workbench_root", str(self.personal))
        self.store.set_setting("project_workspace", str(self.workspace.root))
        self.store.add_planned_task({"title": "备份前任务"})
        self.service = ApplicationService(self.store)

    def test_encrypted_backup_restore_and_corruption_rejection(self):
        password = "test-password"
        from selfManger.knowledge import KnowledgeIndex
        knowledge = KnowledgeIndex(self.store, self.root)
        note = self.root / "knowledge-note.md"
        note.write_text("# Synthetic knowledge", encoding="utf-8")
        knowledge.update_metadata("knowledge-note.md", tags=["backup-check"], favorite=True)
        knowledge.update_resource("knowledge-note.md", title="SQLite knowledge", notes="preserved detail")
        knowledge.create_bundle("SQLite bundle", members=["knowledge-note.md"])
        external = self.root.parent / "external-knowledge.txt"
        external.write_text("external source", encoding="utf-8")
        knowledge.add_file(external, copy=False)
        with self.store.connect() as db:
            db.execute("INSERT INTO knowledge_files VALUES (?,?,?,?,?,?,?,?)",
                       (knowledge.root_id, "note.md", "note", "", "note", 1, 0, 0))
            db.execute("INSERT INTO personal_markdown_documents VALUES (?,?,?,?,?,?,?)",
                       ("action", "missing-action", "today", str(self.personal), "missing.md", "hash", "now"))
        result = self.service.backup_overall(self.root / "backups", password, TaskContext())
        source = Path(result["archive"])
        with self.assertRaises(UserError):
            archives.verify_archive(source, "wrong-password", TaskContext())
        (self.personal / "今日.md").write_text("故意破坏虚构副本", encoding="utf-8")
        with self.store.connect() as db:
            db.execute("UPDATE planned_tasks SET title='故意修改虚构副本'")
        target = self.root / "restore"
        plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(source),
                                         target=str(target), password=password)
        self.service.restore(self.service.overall_resource(), plan, TaskContext(), password)
        manifest = json.loads((target / "overall-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["database_schema"], self.store.SCHEMA_VERSION)
        self.assertEqual((target / "markdown/personal/今日.md").read_text(encoding="utf-8"), "今日计划")
        self.assertTrue((target / "markdown/projects" / self.created["project_id"] / "agent/STATUS.md").is_file())
        self.assertFalse(any(".worktrees" in path.parts for path in (target / "markdown/projects").rglob("*.md")))
        self.assertFalse(list(target.rglob("attachment.bin")))
        with self.assertRaisesRegex(UserError, "隔离"):
            Store(target)
        self.assertEqual((self.personal / "今日.md").read_text(encoding="utf-8"), "故意破坏虚构副本")
        self.assertEqual(self.store.planned_tasks()[0]["title"], "故意修改虚构副本")
        with closing(sqlite3.connect(target / "manager.sqlite3")) as blocker:
            self.assertEqual(blocker.execute("PRAGMA user_version").fetchone()[0], 2147483647)
            self.assertEqual(blocker.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)
        with closing(sqlite3.connect(target / "manager.sqlite3.recovered")) as db:
            self.assertEqual(db.execute("SELECT title FROM planned_tasks").fetchone()[0], "备份前任务")
            metadata = db.execute("SELECT tags,favorite,details FROM knowledge_metadata WHERE relative_path='knowledge-note.md'").fetchone()
            self.assertEqual(json.loads(metadata[0]), ["backup-check"])
            self.assertEqual(metadata[1], 1)
            self.assertEqual(json.loads(metadata[2])["notes"], "preserved detail")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_bundles").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge_references").fetchone()[0], 1)
        notice = (target / "恢复副本隔离说明.txt").read_text(encoding="utf-8")
        self.assertIn("知识索引有 1 条", notice)
        self.assertIn("missing.md", notice)
        self.assertTrue(self.service.rehearse(self.service.overall_resource(), source, password, TaskContext())["valid"])
        modified = self.root / "damaged.amb"
        data = bytearray(source.read_bytes())
        data[len(data) // 2] ^= 1
        modified.write_bytes(data)
        with self.assertRaises(UserError):
            archives.verify_archive(modified, password, TaskContext())
        with self.assertRaises(UserError):
            self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(source),
                                      target=str(target), password=password)

    def test_overall_capture_includes_sqlite_work_notes_and_preserves_legacy_file(self):
        from selfManger import combined_backup

        legacy = self.store.root / "work-summary.md"
        legacy.write_text("# Legacy note\n", encoding="utf-8")
        original = legacy.read_bytes()
        self.service.work_summaries()
        self.service.append_work_summary("SQLite note")
        staging = self.root / "capture-stage"
        staging.mkdir()

        @contextmanager
        def database_source(_path, _context):
            yield self.store.path, True

        with patch.object(combined_backup, "snapshot_file", database_source):
            manifest = capture(self.store, staging, TaskContext())

        with closing(sqlite3.connect(staging / "manager.sqlite3")) as db:
            notes = db.execute("SELECT body FROM personal_work_notes ORDER BY rowid").fetchall()
        self.assertEqual(notes, [("# Legacy note\n",), ("SQLite note",)])
        self.assertEqual(legacy.read_bytes(), original)
        self.assertFalse(any(row["path"] == "markdown/work-summary.md" for row in manifest["files"]))

    def test_overall_backup_imports_legacy_note_before_capture(self):
        from selfManger import combined_backup

        legacy = self.store.root / "work-summary.md"
        legacy.write_text("legacy before any note-page visit", encoding="utf-8")

        class SafeTemporaryDirectory:
            def __init__(inner_self, *_args, **_kwargs):
                inner_self.fixture = None

            def __enter__(inner_self):
                inner_self.fixture = TestDirectory()
                return Path(inner_self.fixture.name)

            def __exit__(inner_self, *_exc):
                inner_self.fixture.cleanup()

        captured = []

        def capture_notes(store, _staging, _context):
            captured.extend(row["body"] for row in store.personal_work_notes())
            return {"files": []}

        with patch("selfManger.application.tempfile.TemporaryDirectory", SafeTemporaryDirectory), \
                patch.object(combined_backup, "capture", side_effect=capture_notes), \
                patch("selfManger.application.archives.create_archive", return_value={"archive": self.root / "fake.amb"}), \
                patch.object(self.service, "verify_backup", return_value={"verified": True}):
            result = self.service.backup_overall(self.root / "backups", "fixture", TaskContext())

        self.assertEqual(captured, ["legacy before any note-page visit"])
        self.assertTrue(result["verified"])

    def test_prepares_quarantined_overall_restore_without_touching_source(self):
        from selfManger.knowledge import KnowledgeIndex

        project_task = self.store.add_planned_task(
            {"title": "虚构项目行动", "project_id": self.created["project_id"], "project_root": str(self.project)},
            "legacy:project:" + str(self.project) + ":fixture-task")
        daily_task = self.store.add_planned_task(
            {"title": "虚构每日行动", "due_date": "2026-10-30"},
            "legacy:daily:" + str(self.personal) + ":2026-10-30:fixture-task:hash")
        review_id = "restore-review-fixture"
        (self.personal / "周复盘.md").write_text("# Synthetic review\n", encoding="utf-8")
        (self.personal / "计划.md").write_text("# Old plan\n", encoding="utf-8")
        plan_id = "restore-plan-fixture"
        timestamp = "2026-10-30T00:00:00+00:00"
        with self.store.connect() as db:
            db.execute("""INSERT INTO personal_plans
                (id,title,notes,status,origin_path,created_at,updated_at)
                VALUES (?,?,?,'planned',?,?,?)""",
                (plan_id, "隔离恢复计划", "内容", str(self.personal / "计划.md"), timestamp, timestamp))
            db.execute("""INSERT INTO personal_markdown_documents
                (entity_type,entity_id,module,root_path,relative_path,content_hash,updated_at)
                VALUES ('plan',?,'longterm',?,?,?,'now')""",
                (plan_id, str(self.personal), "计划.md", "fixture-hash"))
            db.execute("""INSERT INTO personal_reviews
                (id,period_key,period_type,title,start_date,end_date,body,created_at,updated_at)
                VALUES (?,'weekly:2026-W44','weekly','虚构周复盘','2026-10-26','2026-11-01','正文','now','now')""",
                (review_id,))
            db.execute("""INSERT INTO personal_markdown_documents
                (entity_type,entity_id,module,root_path,relative_path,content_hash,updated_at)
                VALUES ('review',?,'review',?,?,?,'now')""",
                (review_id, str(self.personal), "周复盘.md", "review-hash"))
            db.execute("""INSERT INTO action_repeat_rules
                (task_id,frequency,start_date,end_date,weekdays,month_day)
                VALUES (?,'weekly','2026-10-30',NULL,'[5]',NULL)""", (daily_task["id"],))
            db.execute("INSERT INTO action_occurrences (task_id,day,completed_at) VALUES (?,'2026-10-30','now')",
                       (daily_task["id"],))
        self.store.set_setting("personal_plans_import:" + str(self.personal), True)

        old_vault = self.root / "old-vault"
        old_vault.mkdir()
        (old_vault / "note.md").write_text("# Knowledge", encoding="utf-8")
        self.store.set_setting("obsidian_vault_root", str(old_vault))
        index = KnowledgeIndex(self.store, old_vault)
        index.scan()
        index.update_metadata("note.md", tags=["restore-tag"], favorite=True)

        legacy_note = self.store.root / "work-summary.md"
        legacy_note.write_text("# Synthetic legacy work note\n", encoding="utf-8")
        original_legacy_note = legacy_note.read_bytes()
        self.assertEqual(len(self.service.work_summaries()), 1)
        self.service.append_work_summary("Synthetic SQLite work note")

        archive = Path(self.service.backup_overall(self.root / "backups", "fixture-password", TaskContext())["archive"])
        restored_root = self.root / "recovered-files"
        plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(archive),
                                         target=str(restored_root), password="fixture-password")
        self.service.restore(self.service.overall_resource(), plan, TaskContext(), "fixture-password")
        original_database_hash = archives.path_token(restored_root / "manager.sqlite3.recovered")
        new_vault = self.root / "new-vault"
        new_vault.mkdir()
        (new_vault / "note.md").write_text("# Knowledge on new computer", encoding="utf-8")
        destination = self.root / "prepared-recovery"

        report = self.service.prepare_overall_restore(
            restored_root, destination, TaskContext(), path_mappings={str(old_vault): str(new_vault)})

        self.assertTrue(report["prepared"])
        self.assertEqual(report["restored_personal_markdown_files"], 3)
        self.assertEqual((destination / "personal/计划.md").read_text(encoding="utf-8"), "# Old plan\n")
        self.assertEqual((destination / "personal/周复盘.md").read_text(encoding="utf-8"), "# Synthetic review\n")
        workspace = ProjectWorkspace(destination / "workspace")
        project = workspace.registry()["projects"][0]
        new_project_root = workspace.path(project["path"])
        self.assertTrue((new_project_root / "agent/STATUS.md").is_file())
        self.assertEqual(archives.path_token(restored_root / "manager.sqlite3.recovered"), original_database_hash)
        with self.assertRaisesRegex(UserError, "隔离"):
            Store(restored_root)

        restored = Store(destination / "data")
        self.assertEqual(restored.setting("workbench_root"), str(destination / "personal"))
        self.assertEqual(restored.setting("project_workspace"), str(destination / "workspace"))
        self.assertEqual(restored.setting("obsidian_vault_root"), str(new_vault))
        self.assertTrue(restored.setting("personal_plans_import:" + str(destination / "personal")))
        work_notes = restored.personal_work_notes()
        self.assertEqual([row["body"] for row in work_notes],
                         ["# Synthetic legacy work note\n", "Synthetic SQLite work note"])
        self.assertEqual(legacy_note.read_bytes(), original_legacy_note)
        self.assertEqual(restored.planned_tasks(project_id=self.created["project_id"],
                                                project_root=str(new_project_root))[0]["id"], project_task["id"])

        failed_destination = self.root / "failed-prepared-recovery"
        source_hash = archives.path_token(restored_root / "manager.sqlite3.recovered")
        with patch("selfManger.restore_migration._transform_database", side_effect=RuntimeError("fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.service.prepare_overall_restore(restored_root, failed_destination, TaskContext())
        self.assertFalse(failed_destination.exists())
        self.assertEqual(archives.path_token(restored_root / "manager.sqlite3.recovered"), source_hash)
        self.assertFalse(list(self.root.glob(".selfManger-ready-*")))
        from selfManger.action_center import is_personal_action
        restored_project_task = restored.planned_task(project_task["id"])
        self.assertTrue(restored_project_task["source_ref"].startswith("legacy:project:" + str(new_project_root)))
        self.assertFalse(is_personal_action(restored_project_task))
        restored_daily_task = restored.planned_task(daily_task["id"])
        self.assertTrue(restored_daily_task["source_ref"].startswith(
            "legacy:daily:" + str(destination / "personal") + ":2026-10-30:"))
        with restored.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM personal_reviews WHERE id=?", (review_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM personal_markdown_documents WHERE entity_type='review' AND entity_id=? AND root_path=?",
                                        (review_id, str(destination / "personal"))).fetchone()[0], 1)
            rule = db.execute("SELECT frequency,weekdays FROM action_repeat_rules WHERE task_id=?",
                              (daily_task["id"],)).fetchone()
            self.assertEqual(tuple(rule), ("weekly", "[5]"))
            self.assertEqual(db.execute("SELECT day FROM action_occurrences WHERE task_id=?",
                                        (daily_task["id"],)).fetchone()[0], "2026-10-30")
            self.assertEqual(db.execute("SELECT origin_path FROM personal_plans WHERE id=?", (plan_id,)).fetchone()[0],
                             str(destination / "personal/计划.md"))
            self.assertEqual(db.execute("SELECT root_path FROM personal_markdown_documents WHERE entity_id=?", (plan_id,)).fetchone()[0],
                             str(destination / "personal"))
        restored_index = KnowledgeIndex(restored, new_vault)
        restored_index.scan()
        with restored.connect() as db:
            metadata = db.execute("SELECT tags,favorite FROM knowledge_metadata WHERE root_id=? AND relative_path='note.md'",
                                  (restored_index.root_id,)).fetchone()
        self.assertEqual(json.loads(metadata[0]), ["restore-tag"])
        self.assertEqual(metadata[1], 1)

    def test_prepared_restore_rejects_changed_extracted_file_without_output(self):
        from selfManger.restore_migration import prepare_overall_restore

        archive = Path(self.service.backup_overall(self.root / "backups", "fixture-password", TaskContext())["archive"])
        restored_root = self.root / "changed-recovery"
        plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(archive),
                                         target=str(restored_root), password="fixture-password")
        self.service.restore(self.service.overall_resource(), plan, TaskContext(), "fixture-password")
        (restored_root / "markdown/personal/今日.md").write_text("altered", encoding="utf-8")
        destination = self.root / "must-not-exist"
        with self.assertRaisesRegex(UserError, "与清单不一致"):
            prepare_overall_restore(restored_root, destination, TaskContext())
        self.assertFalse(destination.exists())
        self.assertTrue((restored_root / "manager.sqlite3.recovered").is_file())

    def test_prepared_restore_cleanup_failure_reports_residual_staging_path(self):
        from selfManger import restore_migration

        archive = Path(self.service.backup_overall(self.root / "backups", "fixture-password", TaskContext())["archive"])
        restored_root = self.root / "cleanup-failure-recovery"
        plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(archive),
                                         target=str(restored_root), password="fixture-password")
        self.service.restore(self.service.overall_resource(), plan, TaskContext(), "fixture-password")
        destination = self.root / "prepared-cleanup-failure"
        original = UserError("synthetic database transform failure")
        with patch.object(restore_migration, "_transform_database", side_effect=original), \
                patch.object(restore_migration.shutil, "rmtree", side_effect=PermissionError("locked staging")):
            with self.assertRaises(UserError) as raised:
                restore_migration.prepare_overall_restore(restored_root, destination, TaskContext())
        staging = next(self.root.glob(".selfManger-ready-*"))
        self.assertIn(str(staging), str(raised.exception))
        self.assertIn("PermissionError", str(raised.exception))
        self.assertIs(raised.exception.__cause__, original)
        self.assertFalse(destination.exists())

    def test_capture_blocks_another_database_writer(self):
        destination = self.root / "stage"
        destination.mkdir()
        from selfManger import combined_backup
        real_scan = combined_backup._scan
        checked = []
        def inspect_lock(sources, context):
            if not checked:
                with closing(sqlite3.connect(self.store.path, timeout=0)) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("UPDATE planned_tasks SET title='冲突写入'")
                checked.append(True)
            return real_scan(sources, context)
        with patch.object(combined_backup, "_scan", side_effect=inspect_lock):
            capture(self.store, destination, TaskContext())
        self.assertTrue(checked)
        self.assertEqual(self.store.planned_tasks()[0]["title"], "备份前任务")

    def test_detects_external_markdown_change(self):
        destination = self.root / "stage"
        destination.mkdir()
        from selfManger import combined_backup
        real_scan = combined_backup._scan
        calls = 0
        def change(sources, context):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.personal / "今日.md").write_text("外部修改", encoding="utf-8")
            return real_scan(sources, context)
        with patch.object(combined_backup, "_scan", side_effect=change):
            with self.assertRaisesRegex(UserError, "发生变化"):
                capture(self.store, destination, TaskContext())
        with self.store.connect() as db:
            db.execute("UPDATE planned_tasks SET title='失败后可写入'")
        self.assertEqual(self.store.planned_tasks()[0]["title"], "失败后可写入")

    def test_crashed_process_releases_sqlite_write_lock(self):
        script = ("import sqlite3,sys,os; db=sqlite3.connect(sys.argv[1]); "
                  "db.execute('BEGIN IMMEDIATE'); os._exit(0)")
        result = subprocess.run([sys.executable, "-c", script, str(self.store.path)], timeout=20)
        self.assertEqual(result.returncode, 0)
        with self.store.connect() as db:
            db.execute("UPDATE planned_tasks SET title='崩溃后可写入'")
        self.assertEqual(self.store.planned_tasks()[0]["title"], "崩溃后可写入")

    def test_restoration_rejects_inconsistent_inner_manifest(self):
        stage = self.root / "stage"
        stage.mkdir()
        capture(self.store, stage, TaskContext())
        path = stage / "overall-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"] = [row for row in manifest["files"] if row["path"] != "projects.json"]
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        resource = Resource("虚构整体备份", "folder", {"path": str(stage)}, OVERALL_BACKUP_ID)
        result = archives.create_archive(resource, self.root / "backups", "test-password", TaskContext())
        with self.assertRaisesRegex(UserError, "清单"):
            self.service.verify_backup(Path(result["archive"]), "test-password", TaskContext())
        with self.assertRaisesRegex(UserError, "清单"):
            self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=result["archive"],
                                      target=str(self.root / "new-target"), password="test-password")
        self.assertFalse((self.root / "new-target").exists())

    def test_future_database_schema_rejected_before_restore(self):
        stage = self.root / "stage"
        stage.mkdir()
        capture(self.store, stage, TaskContext())
        with closing(sqlite3.connect(stage / "manager.sqlite3")) as db:
            db.execute(f"PRAGMA user_version={Store.SCHEMA_VERSION + 1}")
        path = stage / "overall-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["database_schema"] = Store.SCHEMA_VERSION + 1
        for row in manifest["files"]:
            if row["path"] == "manager.sqlite3":
                row["sha256"] = archives.path_token(stage / "manager.sqlite3")
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        resource = Resource("虚构整体备份", "folder", {"path": str(stage)}, OVERALL_BACKUP_ID)
        result = archives.create_archive(resource, self.root / "backups", "test-password", TaskContext())
        with self.assertRaisesRegex(UserError, "更新版本"):
            self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=result["archive"],
                                      target=str(self.root / "future-target"), password="test-password")
        self.assertFalse((self.root / "future-target").exists())

    def test_older_schema_archive_can_be_quarantined_without_migration(self):
        stage = self.root / "old-stage"
        stage.mkdir()
        capture(self.store, stage, TaskContext())
        with closing(sqlite3.connect(stage / "manager.sqlite3")) as db:
            db.execute("PRAGMA user_version=5")
        path = stage / "overall-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["database_schema"] = 5
        for row in manifest["files"]:
            if row["path"] == "manager.sqlite3":
                row["sha256"] = archives.path_token(stage / "manager.sqlite3")
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        resource = Resource("旧版虚构备份", "folder", {"path": str(stage)}, OVERALL_BACKUP_ID)
        result = archives.create_archive(resource, self.root / "backups", "test-password", TaskContext())
        target = self.root / "old-target"
        plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=result["archive"],
                                         target=str(target), password="test-password")
        self.service.restore(self.service.overall_resource(), plan, TaskContext(), "test-password")
        with closing(sqlite3.connect(target / "manager.sqlite3.recovered")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
        with self.assertRaisesRegex(UserError, "隔离"):
            Store(target)

    def test_prepares_legacy_schemas_by_migrating_only_the_new_copy(self):
        from selfManger.storage import Store

        project_task = self.store.add_planned_task(
            {"title": "旧版项目行动", "project_id": self.created["project_id"], "project_root": str(self.project)},
            "legacy:project:" + str(self.project) + ":legacy-task")
        for schema_version in (3, 5):
            with self.subTest(schema_version=schema_version):
                archive = Path(self.service.backup_overall(
                    self.root / "backups", "fixture-password", TaskContext())["archive"])
                restored_root = self.root / f"legacy-recovery-v{schema_version}"
                plan = self.service.plan_restore(self.service.overall_resource(), TaskContext(), source=str(archive),
                                                 target=str(restored_root), password="fixture-password")
                self.service.restore(self.service.overall_resource(), plan, TaskContext(), "fixture-password")
                recovered = restored_root / "manager.sqlite3.recovered"
                manifest_path = restored_root / "overall-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                with closing(sqlite3.connect(recovered)) as db:
                    for table in ("personal_markdown_documents", "personal_reviews", "action_occurrences",
                                  "action_repeat_rules", "personal_plan_templates", "personal_plans", "planning_deletions"):
                        db.execute(f"DROP TABLE IF EXISTS {table}")
                    db.execute(f"PRAGMA user_version={schema_version}")
                manifest["database_schema"] = schema_version
                for row in manifest["files"]:
                    if row["path"] == "manager.sqlite3":
                        row["sha256"] = archives.path_token(recovered)
                        row["size"] = recovered.stat().st_size
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                source_hash = archives.path_token(recovered)
                destination = self.root / f"legacy-prepared-v{schema_version}"

                report = self.service.prepare_overall_restore(restored_root, destination, TaskContext())

                self.assertTrue(report["prepared"])
                self.assertEqual(report["database_schema"], Store.SCHEMA_VERSION)
                self.assertEqual(archives.path_token(recovered), source_hash)
                restored = Store(destination / "data")
                self.assertIn("备份前任务", {row["title"] for row in restored.planned_tasks()})
                restored_project = restored.planned_task(project_task["id"])
                workspace = ProjectWorkspace(destination / "workspace")
                project_root = workspace.path(workspace.registry()["projects"][0]["path"])
                self.assertEqual(restored_project["project_root"], str(project_root))
                self.assertTrue(restored_project["source_ref"].startswith("legacy:project:" + str(project_root)))
                with self.assertRaisesRegex(UserError, "隔离"):
                    Store(restored_root)
                source_root = Path(__file__).resolve().parents[1]
                screenshot = destination / "prepared-startup.png"
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(source_root / "src")
                startup = subprocess.run(
                    [sys.executable, "-m", "selfManger", "--data-dir", str(destination / "data"),
                     "--smoke-test", "--screenshot", str(screenshot)],
                    cwd=source_root, env=environment, capture_output=True, text=True, timeout=60, check=False)
                self.assertEqual(startup.returncode, 0, msg=startup.stdout + startup.stderr)
                self.assertGreater(screenshot.stat().st_size, 0)
