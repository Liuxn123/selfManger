"""One-shot migration retains records and rejects conflicts without runtime fallbacks."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from tests.support import TestDirectory
from unittest.mock import patch

from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store

spec = importlib.util.spec_from_file_location("name_migration", Path(__file__).resolve().parents[1] / "scripts/migrate_legacy_data.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class FakeKeyring:
    def __init__(self):
        self.values = {(migration.OLD_SERVICE, "common:backup"): "fixture-secret-only"}

    def get_credential(self, service, identity):
        return SimpleNamespace(username="common:backup") if (service, "common:backup") in self.values else None

    def get_password(self, service, identity):
        return self.values.get((service, identity))

    def set_password(self, service, identity, value):
        self.values[service, identity] = value

    def delete_password(self, service, identity):
        del self.values[service, identity]


class NameMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / "old"
        self.new = self.root / "selfManger"
        self.snapshot = self.root / "snapshot"
        self.store = Store(self.old)
        self.store.set_setting("workbench_root", str(self.old / "notes"))
        self.store.add_planned_task({"title": "preserve user task", "kind": "task"})
        self.workspace = ProjectWorkspace(self.root / "workspace")
        self.workspace.initialize(TaskContext())
        item = self.workspace.create("fixture", "goal", False, TaskContext(), "fixture")
        self.project = Path(item["directory"])
        (self.project / ".selfManger").rename(self.project / ".xiaonian")
        self.rules = self.project / "AGENTS.md"
        self.rules.write_text("# user rules\nSee .xiaonian/config.json\n", encoding="utf-8")
        self.tasks = self.project / "agent/TASKS-AI.md"
        self.tasks.write_text("user notes\n<!-- agent-manager:tasks:start -->\nkeep row\n<!-- agent-manager:tasks:end -->\n", encoding="utf-8")
        self.keyring = FakeKeyring()

    def test_migrates_once_with_verified_snapshot_and_no_legacy_credentials(self):
        result = migration.migrate(self.old, self.new, self.snapshot, workspace=self.workspace.root, keyring=self.keyring, apply=True)
        self.assertFalse(self.old.exists())
        self.assertTrue((self.new / "manager.sqlite3").is_file())
        self.assertEqual(result["counts"]["planned_tasks"], 1)
        self.assertEqual(result["credential_count"], 1)
        self.assertNotIn((migration.OLD_SERVICE, "common:backup"), self.keyring.values)
        self.assertEqual(self.keyring.values[migration.NEW_SERVICE, "common:backup"], "fixture-secret-only")
        self.assertTrue((self.snapshot / "application-data/manager.sqlite3").is_file())
        self.assertTrue((self.project / ".selfManger/config.json").is_file())
        self.assertFalse((self.project / ".xiaonian").exists())
        self.assertTrue(self.rules.read_text(encoding="utf-8").startswith("# user rules"))
        self.assertIn("selfManger:tasks:start", self.tasks.read_text(encoding="utf-8"))
        moved = Store(self.new)
        self.assertEqual(moved.setting("workbench_root"), str(self.new / "notes"))
        self.assertEqual(moved.planned_tasks()[0]["title"], "preserve user task")
        with self.assertRaises(RuntimeError):
            migration.migrate(self.old, self.new, self.root / "second-snapshot", apply=True)

    def test_conflict_stops_before_moving_data_or_writing_snapshot(self):
        (self.project / ".selfManger").mkdir()
        with self.assertRaises(RuntimeError):
            migration.migrate(self.old, self.new, self.snapshot, workspace=self.workspace.root, apply=True)
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.new.exists())
        self.assertFalse(self.snapshot.exists())

    def test_note_directory_migration_preserves_original_bytes(self):
        old = self.root / "notes-old"
        old.mkdir()
        (old / "user.md").write_bytes(b"user text\r\nkeep original bytes\r\n")
        new = self.root / "notes-new"
        backup = self.root / "notes-snapshot"
        result = migration.migrate_directory(old, new, backup, apply=True)
        self.assertFalse(old.exists())
        self.assertEqual((new / "user.md").read_bytes(), (backup / "user.md").read_bytes())
        self.assertTrue(result["snapshot_verified"])

    def test_note_reference_migration_preserves_import_identity_and_index(self):
        import hashlib
        from selfManger.knowledge import KnowledgeIndex
        from selfManger.personal_documents import PersonalMarkdownDocuments
        from selfManger.workbench import render_document
        vault = self.root / "vault"
        old, new = vault / "Agent工作台", vault / "selfManger"
        new.mkdir(parents=True)
        index = KnowledgeIndex(self.store, vault)
        self.store.set_setting("legacy_task_import_v1", {"daily": str(old), "files": ["today.md"]})
        self.store.set_setting("legacy_daily_task_import_v2", {"root": str(old), "file_count": 1})
        self.store.set_setting("personal_plans_import:" + str(old), True)
        self.store.set_setting("workbench_root", str(old))
        task = self.store.add_planned_task({"title": "keep once", "kind": "task"}, "legacy:daily:" + str(old) + ":date:task:hash")
        plan_id = "c" * 32
        relative = "计划/安排/" + plan_id + ".md"
        document_metadata = {"quick_document": True, "entity_type": "plan", "entity_id": plan_id,
                             "module": "longterm", "title": "保留原件", "status": "planned"}
        document_text = render_document(document_metadata, "旧正文")
        document_path = new / relative
        document_path.parent.mkdir(parents=True)
        document_path.write_text(document_text, encoding="utf-8", newline="\n")
        timestamp = "2026-10-29T00:00:00+00:00"
        reference_key = 'external:"C:\\\\fixture\\\\external.bin"'
        bundle = {"version": 1, "id": "fixture-bundle", "title": "旧合集", "folder": "Agent工作台",
                  "directory": "Agent工作台/group", "members": ["Agent工作台/note.md"], "excluded": [],
                  "entry": "Agent工作台/note.md", "active": True}
        with self.store.connect() as db:
            db.execute("INSERT INTO knowledge_files VALUES (?,?,?,?,?,?,?,?)", (index.root_id, "Agent工作台/note.md", "note", "Agent工作台", "note", 1, 1, 0))
            db.execute("INSERT INTO knowledge_metadata(root_id,relative_path,tags,favorite,projects,agents,updated_at,details) VALUES (?,?,?,?,?,?,?,?)",
                       (index.root_id, "Agent工作台/note.md", '["keep"]', 0, "[]", "[]", timestamp,
                        json.dumps({"archive_original": "Agent工作台/old.md", "related": ["Agent工作台/related.md"]}, ensure_ascii=False)))
            db.execute("INSERT INTO knowledge_references VALUES (?,?,?,?,?)",
                       (index.root_id, reference_key, str(self.root / "external.bin"), "external.bin", "Agent工作台"))
            db.execute("INSERT INTO knowledge_bundles VALUES (?,?,?)", (index.root_id, "fixture-bundle", json.dumps(bundle, ensure_ascii=False)))
            db.execute("INSERT INTO knowledge_legacy_imports VALUES (?,?,?,?,?)",
                       (index.root_id, "kref:Agent工作台/ref.kref", timestamp, 1, 0))
            db.execute("""INSERT INTO personal_plans
                (id,title,notes,status,origin_path,created_at,updated_at) VALUES (?,?,?,'planned',?,?,?)""",
                (plan_id, "保留原件", "旧正文", str(old / relative), timestamp, timestamp))
            db.execute("""INSERT INTO personal_markdown_documents
                (entity_type,entity_id,module,root_path,relative_path,content_hash,updated_at)
                VALUES ('plan',?,'longterm',?,?,?,?)""",
                (plan_id, str(old), relative, hashlib.sha256(document_text.encode("utf-8")).hexdigest(), timestamp))
        result = migration.migrate_workbench_references(self.store.path, old, new, self.root / "refs.sqlite", vault, apply=True)
        self.assertEqual(result["task_references"], 1)
        self.assertEqual(result["knowledge_references"], 2)
        self.assertEqual(result["knowledge_reference_folders"], 1)
        self.assertEqual(result["knowledge_bundles"], 1)
        self.assertEqual(result["knowledge_import_markers"], 1)
        self.assertEqual(result["personal_document_roots"], 1)
        self.assertEqual(result["personal_plan_origins"], 1)
        self.assertEqual(result["daily_import_marker"], 1)
        self.assertEqual(result["personal_plan_import_marker"], 1)
        self.assertEqual(result["workbench_root_setting"], 1)
        self.assertEqual(self.store.setting("legacy_task_import_v1")["daily"], str(new))
        self.assertEqual(self.store.setting("legacy_daily_task_import_v2")["root"], str(new))
        self.assertTrue(self.store.setting("personal_plans_import:" + str(new)))
        self.assertEqual(self.store.setting("workbench_root"), str(new))
        self.assertEqual(len(self.store.planned_tasks()), 2)
        self.assertTrue(any(ref.startswith("legacy:daily:" + str(new)) for ref in self.store.planned_task_source_refs()))
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT relative_path,folder FROM knowledge_files").fetchone()), ("selfManger/note.md", "selfManger"))
            details = json.loads(db.execute("SELECT details FROM knowledge_metadata WHERE relative_path='selfManger/note.md'").fetchone()[0])
            self.assertEqual(details["archive_original"], "selfManger/old.md")
            self.assertEqual(details["related"], ["selfManger/related.md"])
            self.assertEqual(db.execute("SELECT folder FROM knowledge_references WHERE relative_path=?", (reference_key,)).fetchone()[0], "selfManger")
            bundle_record = json.loads(db.execute("SELECT record FROM knowledge_bundles WHERE bundle_id='fixture-bundle'").fetchone()[0])
            self.assertEqual((bundle_record["folder"], bundle_record["directory"], bundle_record["members"]),
                             ("selfManger", "selfManger/group", ["selfManger/note.md"]))
            self.assertEqual(db.execute("SELECT source_kind FROM knowledge_legacy_imports WHERE root_id=? AND source_kind LIKE 'kref:%'", (index.root_id,)).fetchone()[0],
                             "kref:selfManger/ref.kref")
            self.assertEqual(db.execute("SELECT origin_path FROM personal_plans WHERE id=?", (plan_id,)).fetchone()[0], str(new / relative))
            self.assertEqual(db.execute("SELECT root_path FROM personal_markdown_documents WHERE entity_id=?", (plan_id,)).fetchone()[0], str(new))
        relocated = Path(PersonalMarkdownDocuments(self.store).path("plan", plan_id))
        self.assertEqual(relocated, new / relative)
        self.assertEqual(relocated.read_text(encoding="utf-8"), document_text)
        edited_text = render_document({**document_metadata, "title": "搬迁后外部修改"}, "搬迁后正文")
        relocated.write_text(edited_text, encoding="utf-8", newline="\n")
        imported = PersonalMarkdownDocuments(self.store).migrate_legacy_documents()
        self.assertEqual((imported["updated"], imported["errors"]), (1, []))
        from selfManger.action_center import ActionCenter
        migrated_plan = next(row for row in ActionCenter(self.store).plans() if row["id"] == plan_id)
        self.assertEqual((migrated_plan["title"], migrated_plan["notes"]), ("搬迁后外部修改", "搬迁后正文"))

    def test_note_reference_migration_stops_on_existing_destination_import_marker(self):
        from selfManger.knowledge import KnowledgeIndex
        vault = self.root / "vault-conflict"
        old, new = vault / "Agent工作台", vault / "selfManger"
        new.mkdir(parents=True)
        KnowledgeIndex(self.store, vault)
        self.store.set_setting("personal_plans_import:" + str(old), True)
        self.store.set_setting("personal_plans_import:" + str(new), True)
        snapshot = self.root / "conflict-snapshot.sqlite"
        with self.assertRaisesRegex(RuntimeError, "Destination already has a personal-plan import marker"):
            migration.migrate_workbench_references(self.store.path, old, new, snapshot, vault, apply=True)
        self.assertFalse(snapshot.exists())
        self.assertTrue(self.store.setting("personal_plans_import:" + str(old)))
        self.assertTrue(self.store.setting("personal_plans_import:" + str(new)))

    def test_failure_rolls_back_files_database_and_data_directory(self):
        original = self.rules.read_bytes()
        rename = Path.rename
        def fail_config(path, target):
            if path.name == ".xiaonian":
                raise OSError("fixture rename failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_config), self.assertRaises(OSError):
            migration.migrate(self.old, self.new, self.snapshot, workspace=self.workspace.root, keyring=self.keyring, apply=True)
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.new.exists())
        self.assertEqual(self.rules.read_bytes(), original)
        self.assertIn((migration.OLD_SERVICE, "common:backup"), self.keyring.values)
        self.assertEqual(Store(self.old).setting("workbench_root"), str(self.old / "notes"))


if __name__ == "__main__":
    unittest.main()
