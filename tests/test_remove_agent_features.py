import unittest
from pathlib import Path
from tests.support import TestDirectory
from selfManger.application import ApplicationService
from selfManger.domain import Resource, UserError
from selfManger.agent_skills import discover_skills
from selfManger.storage import Store
from selfManger.runtime import TaskContext


class RemovedAgentFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")

    def test_legacy_records_are_preserved_but_not_exposed_as_backup_resources(self):
        items = [Resource("旧 Agent", "agent", {}), Resource("旧 Hermes", "hermes_local", {}),
                 Resource("旧服务器", "hermes_server", {})]
        for resource in items:
            self.store.save_resource(resource)
        service = ApplicationService(self.store)
        self.assertEqual({item.id for item in self.store.resources()}, {item.id for item in items})
        self.assertFalse({item.id for item in service.backup_resources()} & {item.id for item in items})
        for resource in items:
            with self.assertRaises(UserError):
                service.registry.get(resource)
            with self.assertRaisesRegex(UserError, "已移除"):
                service.plan_restore(resource, TaskContext(), source="unused", target=str(self.root / "unused"))

    def test_project_skill_discovery_still_accepts_existing_agent_registration(self):
        skill = self.root / "skills" / "example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Test\n", encoding="utf-8")
        agent = Resource("本地配置", "agent", {"skills_dir_override": str(skill.parent)})
        self.assertEqual([entry.name for entry in discover_skills(agent)], ["example"])

    def test_generic_script_backup_still_runs_and_restores(self):
        script = self.root / "backup_script.py"
        script.write_text("import os\nfrom pathlib import Path\nPath(os.environ['SELFMANGER_BACKUP_OUTPUT_DIR'], 'file.txt').write_text('fixture')\n", encoding="utf-8")
        resource = Resource("脚本", "script", {"script_path": str(script), "script_arguments": []})
        service = ApplicationService(self.store)
        result = service.backup(resource, TaskContext(), "test-password")
        target = self.root / "restored"
        plan = service.plan_restore(resource, TaskContext(), source=result["archive"], target=str(target), password="test-password")
        service.restore(resource, plan, TaskContext(), "test-password")
        self.assertEqual((target / "file.txt").read_text(encoding="utf-8"), "fixture")
