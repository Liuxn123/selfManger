from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from tests.support import TestDirectory
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from selfManger import archives
from selfManger.application import ApplicationService
from selfManger.domain import Resource, UserError
from selfManger.maintenance import backup_health, backup_retention_count, is_due
from selfManger.security import SecretStore
from selfManger.storage import Store, now
from selfManger.runtime import TaskContext


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TestDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.usb = self.root / "usb-A/selfManger"
        self.usb.mkdir(parents=True)
        (self.usb / "portable.json").write_text('{}')
        self.store = Store(self.usb / "data")
        self.service = ApplicationService(self.store)
        self.source = self.root / "project"
        self.source.mkdir()
        (self.source / "成果.md").write_text("fixture-work", encoding="utf-8")
        self.resource = Resource("我的项目", "project", {"path": str(self.source), "automatic_backup": True})
        self.store.save_resource(self.resource)
        self.context = TaskContext()
        self.password = "fixture-archive-password"

    def test_backup_retention_setting_falls_back_and_clamps(self):
        self.store.set_setting("backup_keep", None)
        self.assertEqual(backup_retention_count(self.store), 10)
        self.store.set_setting("backup_keep", "invalid")
        self.assertEqual(backup_retention_count(self.store), 10)
        self.store.set_setting("backup_keep", 0)
        self.assertEqual(backup_retention_count(self.store), 1)
        self.store.set_setting("backup_keep", 1000)
        self.assertEqual(backup_retention_count(self.store), 100)

    def test_portable_paths_and_data_follow_a_changed_drive(self):
        local = self.usb / "projects/local"
        local.mkdir(parents=True)
        self.resource.options["path"] = str(local)
        self.store.save_resource(self.resource)
        self.store.set_setting("backup_root", str(self.usb / "backups"))
        self.store.save_evidence(str(self.usb / "backups/example.amb"), {"source": str(local)})
        other = self.root / "usb-B/selfManger"
        shutil.copytree(self.usb, other)
        moved = Store(other / "data")
        self.assertEqual(Path(moved.resources()[0].options["path"]), (other / "projects/local").resolve())
        self.assertEqual(Path(moved.setting("backup_root")), (other / "backups").resolve())
        self.assertEqual(Path(moved.evidence(str(other / "backups/example.amb"))["source"]), (other / "projects/local").resolve())

    def test_portable_paths_normalize_equivalent_directory_spellings(self):
        from selfManger.portable import encode_paths, decode_paths
        value = str(self.usb / "nested/../projects/local")
        encoded = encode_paths(value, self.usb.resolve())
        self.assertEqual(encoded, "@portable/projects/local")
        self.assertEqual(Path(decode_paths(encoded, self.usb.resolve())), (self.usb / "projects/local").resolve())

    def test_encrypted_portable_credentials_move_and_reject_wrong_master_password(self):
        vault = self.service.secrets
        vault.unlock("fixture-master-password")
        vault.save(self.resource.id + ":backup", self.password)
        raw = vault.vault.read_bytes()
        self.assertNotIn(self.password.encode(), raw)
        moved_root = self.root / "another-computer/selfManger"
        shutil.copytree(self.usb, moved_root)
        moved = SecretStore(moved_root)
        with self.assertRaises(UserError):
            moved.unlock("incorrect-master-password")
        self.assertIsNone(moved.get(self.resource.id + ":backup"))
        moved.unlock("fixture-master-password")
        self.assertEqual(moved.get(self.resource.id + ":backup"), self.password)
        moved.lock()
        self.assertIsNone(moved.get(self.resource.id + ":backup"))

    def test_backup_verification_and_actual_rehearsal_survive_restart(self):
        result = self.service.backup(self.resource, self.context, self.password)
        self.assertTrue(result["verified"])
        self.assertEqual(backup_health(self.store, self.resource, self.service.backup_root())["state"], "已校验")
        report = self.service.rehearse(self.resource, Path(result["archive"]), self.password, self.context)
        self.assertEqual(report["identical_files"], 1)
        self.assertEqual((self.source / "成果.md").read_text(), "fixture-work")
        restarted = Store(self.store.root)
        self.assertEqual(backup_health(restarted, self.resource, self.service.backup_root())["state"], "已演练")
        source = Path(result["archive"])
        with source.open("ab") as handle:
            handle.write(b"corrupted")
        self.assertFalse(backup_health(restarted, self.resource, self.service.backup_root())["verified"])

    def test_parallel_credential_saves_keep_every_resource(self):
        vault = self.service.secrets
        vault.unlock("fixture-master-password")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda key: vault.save(key, "fixture-" + key), ["a", "b", "c"]))
        vault.lock()
        vault.unlock("fixture-master-password")
        self.assertEqual([vault.get(key) for key in ["a", "b", "c"]], ["fixture-a", "fixture-b", "fixture-c"])

    def test_automatic_backup_cadence_and_retention_preserve_originals(self):
        self.service.secrets.unlock("fixture-master-password")
        self.service.secrets.save(self.resource.id + ":backup", self.password)
        first = self.service.backup(self.resource, self.context, self.password)
        second = self.service.backup(self.resource, self.context, self.password)
        # Make ordering explicit even on a fast filesystem / same-second creation.
        for value, stamp in [(first, "2020-01-01T00:00:00+00:00"), (second, "2021-01-01T00:00:00+00:00")]:
            sidecar = Path(value["archive"]).with_name("metadata.json")
            metadata = json.loads(sidecar.read_text())
            metadata["created_at"] = stamp
            sidecar.write_text(json.dumps(metadata))
        self.store.set_setting("backup_keep", 2)
        latest = self.service.automatic_backup(self.resource, self.context)
        self.assertFalse(Path(first["archive"]).exists())
        self.assertTrue(Path(second["archive"]).exists())
        self.assertTrue(Path(latest["archive"]).exists())
        self.assertTrue((self.source / "成果.md").exists())
        self.assertFalse(is_due(self.store, self.resource))
        self.assertTrue(is_due(self.store, self.resource, datetime.now(timezone.utc) + timedelta(days=2)))






if __name__ == "__main__":
    unittest.main()
