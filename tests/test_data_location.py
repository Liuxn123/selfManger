import json
import os
from pathlib import Path
import unittest
from tests.support import TestDirectory
from unittest.mock import patch

from selfManger.domain import UserError
from selfManger.storage import Store, data_directory, source_data_directory


class DataLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TestDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "software"
        self.source.mkdir()
        self.database = Store(self.root / "data")
        self.config = self.source / "local-config.json"
        self.config.write_text(json.dumps({"schema_version": 1, "data_directory": "../data", "require_existing_database": True}))
        patcher = patch("selfManger.storage.application_directory", return_value=self.source)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_source_uses_relative_data_without_switching_credential_backend(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(data_directory(), self.database.root)
            self.assertIsNone(Store().portable_root)

    def test_missing_database_stops_without_creating_an_empty_replacement(self):
        self.config.write_text(json.dumps({"schema_version": 1, "data_directory": "../missing", "require_existing_database": True}))
        with self.assertRaises(UserError):
            source_data_directory()
        self.assertFalse((self.root / "missing").exists())

    def test_environment_override_precedes_local_configuration(self):
        self.config.write_text("invalid JSON")
        with patch.dict(os.environ, {"SELFMANGER_DATA_DIR": str(self.root / "selected")}):
            self.assertEqual(data_directory(), self.root / "selected")

    def test_invalid_configuration_never_silently_falls_back(self):
        self.config.write_text("invalid JSON")
        with self.assertRaises(UserError):
            source_data_directory()

    def test_frozen_packages_ignore_source_only_configuration(self):
        with patch("selfManger.storage.sys.frozen", True, create=True):
            self.assertIsNone(source_data_directory())
