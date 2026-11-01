from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from tests.support import TestDirectory
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("desktop_build", Path(__file__).resolve().parents[1] / "scripts/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildTests(unittest.TestCase):
    def test_build_collects_assets_from_current_checkout(self):
        root = Path("candidate")
        with patch.object(build.sys, "platform", "win32"):
            command = build.pyinstaller_command(root, root / "dist", root / "build")
        data_argument = command[command.index("--add-data") + 1]
        self.assertEqual(data_argument, f"{root / 'src/selfManger/assets'}{os.pathsep}selfManger/assets")
        self.assertNotIn("--collect-data", command)
        self.assertIn(str(root / "src/selfManger/assets/ui/selfManger.ico"), command)

    def test_windows_build_cannot_collect_dlls_from_unrelated_tools_on_path(self):
        with TestDirectory() as temporary:
            root = Path(temporary)
            windows = root / "Windows"
            (windows / "System32").mkdir(parents=True)
            tools = root / "unrelated-tools"
            tools.mkdir()
            (tools / "icuuc.dll").write_bytes(b"incompatible fixture library")
            environment = {"SystemRoot": str(windows), "PATH": str(tools), "KEEP": "value"}
            with patch.object(build.sys, "platform", "win32"), patch.dict(os.environ, environment, clear=True):
                result = build.build_environment()
            paths = result["PATH"].split(os.pathsep)
            self.assertNotIn(str(tools), paths)
            self.assertIn(str(windows / "System32"), paths)
            self.assertIn(str(Path(sys.executable).parent), paths)
            self.assertEqual(result["KEEP"], "value")

    def test_other_platforms_preserve_build_environment(self):
        with patch.object(build.sys, "platform", "linux"), patch.dict(os.environ, {"PATH": "/test/tools"}, clear=True):
            self.assertEqual(build.build_environment(), {"PATH": "/test/tools"})
