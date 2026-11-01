"""The new application uses only its own entry point and data namespace."""
import importlib.util
import os
from pathlib import Path
import unittest
from tests.support import TestDirectory
from unittest.mock import patch

from selfManger import __version__
from selfManger.security import SecretStore
from selfManger.storage import Store, data_directory
from selfManger.workbench import work_root


class BrandingTests(unittest.TestCase):
    def test_only_new_package_is_importable(self):
        self.assertEqual(__version__, "1.0.1")
        self.assertIsNone(importlib.util.find_spec("agent_manager"))

    def test_data_environment_and_credentials_use_new_namespace(self):
        with TestDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.dict(os.environ, {"USERPROFILE": str(root), "LOCALAPPDATA": str(root), "AGENT_MANAGER_DATA_DIR": str(root / "legacy")}, clear=True), patch("selfManger.storage.sys.platform", "win32"), patch("selfManger.storage.portable_directory", return_value=None), patch("selfManger.storage.source_data_directory", return_value=None):
                self.assertEqual(data_directory(), root / "selfManger")
                with patch.dict(os.environ, {"SELFMANGER_DATA_DIR": str(root / "selected")}):
                    self.assertEqual(data_directory(), root / "selected")
            self.assertEqual(SecretStore.service, "selfManger")

    def test_workspace_notes_use_new_directory(self):
        with TestDirectory() as temporary:
            root = Path(temporary).resolve()
            store = Store(root / "data")
            workspace = root / "workspace"
            (workspace / "myself").mkdir(parents=True)
            store.set_setting("project_workspace", str(workspace))
            self.assertEqual(work_root(store), workspace / "myself/selfManger")
