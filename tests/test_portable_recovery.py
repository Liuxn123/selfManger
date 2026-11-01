import importlib.util
import json
from pathlib import Path
import unittest
from tests.support import TestDirectory

from selfManger.domain import Resource
from selfManger.storage import Store

spec = importlib.util.spec_from_file_location("portable_recovery", Path(__file__).resolve().parents[1] / "scripts/recover_portable_data.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class PortableRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        portable = self.root / "portable"
        portable.mkdir()
        (portable / "portable.json").write_text('{"mode":"portable"}')
        self.old = Store(portable / "data")
        self.new = Store(self.root / "current")
        self.old.save_resource(Resource("old", "folder", {"path": str(portable / "notes")}))
        self.new.add_planned_task({"title": "keep newer task", "kind": "task"})
        self.old.set_setting("project_workspace", "old-choice")
        self.new.set_setting("project_workspace", "new-choice")

    def test_additive_recovery_preserves_newer_tasks_settings_and_decodes_portable_paths(self):
        report = recovery.recover(self.old.path, self.new.path, self.root / "snapshot", apply=True)
        self.assertEqual(report["added"]["resources"], 1)
        self.assertEqual(len(self.new.planned_tasks()), 1)
        self.assertEqual(self.new.setting("project_workspace"), "new-choice")
        self.assertEqual(self.new.resources()[0].options["path"], str(self.old.root.parent / "notes"))
        self.assertTrue((self.root / "snapshot/current-before.sqlite").is_file())
        self.assertEqual(len(self.old.resources()), 1)

    def test_second_recovery_adds_no_duplicates(self):
        recovery.recover(self.old.path, self.new.path, self.root / "first", apply=True)
        report = recovery.recover(self.old.path, self.new.path, self.root / "second", apply=True)
        self.assertFalse(any(report["added"].values()))
        self.assertEqual(len(self.new.resources()), 1)

    def test_existing_snapshot_is_never_overwritten(self):
        snapshot = self.root / "existing"
        snapshot.mkdir()
        with self.assertRaises(RuntimeError):
            recovery.recover(self.old.path, self.new.path, snapshot, apply=True)
        self.assertFalse(self.new.resources())
