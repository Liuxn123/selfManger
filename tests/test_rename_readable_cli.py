"""Isolated CLI checks for the legacy readable-name migration tool."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rename_readable.py"


class RenameReadableCliTests(unittest.TestCase):
    def test_vault_argument_is_required(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=30
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--vault", result.stderr)

    def test_preview_then_apply_preserves_document_bytes(self):
        test_root = os.environ.get("SELFMANGER_TEST_ROOT")
        with tempfile.TemporaryDirectory(prefix="rename-readable-cli-", dir=test_root) as temporary:
            root = Path(temporary)
            vault = root / "vault"
            folder = vault / "selfManger" / "计划"
            folder.mkdir(parents=True)
            source = folder / ("b" * 64 + ".md")
            original = (
                "---\n"
                "id: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                "title: 示例迁移计划\n"
                "---\n\n"
                "合成测试正文\n"
            ).encode("utf-8")
            source.write_bytes(original)

            preview_report = root / "preview"
            preview = subprocess.run(
                [sys.executable, str(SCRIPT), "--vault", str(vault),
                 "--report", str(preview_report)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertTrue(source.is_file())
            manifest = json.loads((preview_report / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["changes"]), 1)
            self.assertEqual(Path(manifest["changes"][0]["from"]), source)

            apply_report = root / "apply"
            applied = subprocess.run(
                [sys.executable, str(SCRIPT), "--vault", str(vault), "--apply",
                 "--report", str(apply_report)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertFalse(source.exists())
            self.assertEqual(len(list(folder.glob("示例迁移计划-aaaaaaaa.md"))), 1)
            target = folder / "示例迁移计划-aaaaaaaa.md"
            self.assertEqual(target.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
