from __future__ import annotations

import hashlib
import json
import os
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from tests.support import TestDirectory
from pathlib import Path

from selfManger import archives
from selfManger.domain import Cancelled, Resource, UserError
from selfManger.runtime import TaskContext


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TestDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "知识库"
        self.project.mkdir()
        (self.project / "附件").mkdir()
        (self.project / "空目录").mkdir()
        (self.project / "笔记.md").write_text("你好，世界\n[[附件/图片.bin]]", encoding="utf-8")
        (self.project / "附件/图片.bin").write_bytes(os.urandom(350_000))
        (self.project / ".env").write_text("TEST_PRIVATE_VALUE=local-only-fixture", encoding="utf-8")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules/cache.txt").write_text("excluded")
        self.resource = Resource("知识库", "vault", {"path": str(self.project), "manage_git": True})
        self.password = "test-password-备份"
        self.context = TaskContext()

    def backup(self) -> Path:
        report = archives.create_archive(self.resource, self.root / "backups", self.password, self.context)
        return Path(report["archive"])

    def test_archive_listing_reports_scan_errors_instead_of_returning_partial_results(self):
        backup_root = self.root / "backups"
        backup_root.mkdir()
        with patch("selfManger.archives.os.walk", side_effect=PermissionError("locked backup folder")):
            with self.assertRaises(PermissionError):
                archives.list_archives(backup_root, self.resource.id)

    def test_archive_listing_treats_missing_backup_root_as_empty(self):
        self.assertEqual([], archives.list_archives(self.root / "missing-backups", self.resource.id))

    def require_git_temp_access(self, *, bundle_probe: bool = True, clone_probe: bool = False) -> None:
        if os.name != "nt":
            return
        skip_reason = None
        command_error = ""
        try:
            with tempfile.TemporaryDirectory(prefix="selfManger-git-probe-") as temporary:
                if bundle_probe:
                    refs = subprocess.run(["git", "-C", str(self.project), "show-ref"],
                                          capture_output=True, text=True)
                    if refs.returncode == 0:
                        bundle = Path(temporary) / "probe.bundle"
                        result = subprocess.run(["git", "-C", str(self.project), "bundle", "create",
                                                 str(bundle), "--all"], capture_output=True, text=True)
                        command_error = result.stderr.strip()
                        if result.returncode and any(message in command_error.casefold()
                                                     for message in ("permission denied", "access is denied", "winerror 5", "win32 error 5")):
                            skip_reason = command_error
                        elif result.returncode:
                            self.fail("Local Git bundle preflight failed outside the known ACL condition: " + command_error)
                        elif clone_probe:
                            clone_target = self.root / "git-clone-probe"
                            clone_result = subprocess.run(
                                ["git", "clone", str(bundle), str(clone_target)],
                                capture_output=True, text=True)
                            command_error = clone_result.stderr.strip()
                            if clone_result.returncode and any(message in command_error.casefold()
                                                               for message in ("permission denied", "access is denied",
                                                                               "winerror 5", "win32 error 5",
                                                                               "couldn't create signal pipe")):
                                skip_reason = command_error
                            elif clone_result.returncode:
                                self.fail("Local Git clone preflight failed outside the known ACL condition: " + command_error)
        except PermissionError as exc:
            if getattr(exc, "winerror", None) == 5 or exc.errno == 13:
                skip_reason = str(exc)
            else:
                raise
        if skip_reason:
            self.skipTest("Windows 受限测试环境拒绝访问 selfManger-git-* 临时工作区（WinError 5）；"
                          "Git bundle/clone 用例跳过，请在本机正常权限下重跑。详情：" + skip_reason)

    def test_encrypted_round_trip_preserves_unicode_attachments_empty_directories_and_secrets(self):
        source = self.backup()
        self.assertNotIn(b"local-only-fixture", source.read_bytes())
        self.assertFalse(list(source.parent.glob("*.zip")))
        self.assertTrue(archives.verify_archive(source, self.password, self.context)["valid"])
        target = self.root / "恢复"
        plan = archives.plan_restore(self.resource, source, target, self.password, self.context)
        report = archives.apply_restore(plan, self.password, self.context)
        self.assertTrue(report["applied"])
        for name in ["笔记.md", "附件/图片.bin", ".env"]:
            self.assertEqual((self.project / name).read_bytes(), (target / name).read_bytes())
        self.assertTrue((target / "空目录").is_dir())
        self.assertFalse((target / "node_modules").exists())

    def test_wrong_password_and_tampering_leave_target_untouched(self):
        source = self.backup()
        target = self.root / "restore"
        with self.assertRaises(UserError):
            archives.plan_restore(self.resource, source, target, "wrong", self.context)
        self.assertFalse(target.exists())
        data = bytearray(source.read_bytes())
        data[len(archives.MAGIC) + 40] ^= 1
        source.write_bytes(data)
        with self.assertRaises(UserError):
            archives.plan_restore(self.resource, source, target, self.password, self.context)
        self.assertFalse(target.exists())

    def test_restore_cleanup_failure_reports_residual_staging_path(self):
        target = self.root / "restore"
        original = UserError("synthetic restore failure")
        with patch("selfManger.archives.open_archive", side_effect=original), \
                patch("selfManger.archives.shutil.rmtree", side_effect=PermissionError("locked stage")):
            with self.assertRaises(UserError) as raised:
                archives.apply_restore(
                    archives.RestorePlan("vault", "token", str(self.root / "backup.amb"), str(target), {}),
                    self.password, self.context,
                )
        staging = next(self.root.glob(".selfManger-restore-*"))
        self.assertIn(str(staging), str(raised.exception))
        self.assertIn("PermissionError", str(raised.exception))
        self.assertIs(raised.exception.__cause__, original)
        self.assertFalse(target.exists())

    def test_plan_is_bound_to_exact_archive_and_resource(self):
        source = self.backup()
        target = self.root / "restore"
        plan = archives.plan_restore(self.resource, source, target, self.password, self.context)
        (self.project / "new.txt").write_text("change")
        replacement = self.backup()
        source.write_bytes(replacement.read_bytes())
        with self.assertRaises(UserError):
            archives.apply_restore(plan, self.password, self.context)
        self.assertFalse(target.exists())
        other = Resource("其他资料", "project", {"path": str(self.project)})
        with self.assertRaises(UserError):
            archives.plan_restore(other, source, target, self.password, self.context)

    def test_existing_data_and_backup_inside_source_are_rejected(self):
        with self.assertRaises(UserError):
            archives.create_archive(self.resource, self.project / "backups", self.password, self.context)
        source = self.backup()
        target = self.root / "existing"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        with self.assertRaises(UserError):
            archives.plan_restore(self.resource, source, target, self.password, self.context)
        self.assertEqual("keep", (target / "keep.txt").read_text())

    def test_cancelled_backup_does_not_leave_partial_files(self):
        context = TaskContext()
        context.cancel.set()
        with self.assertRaises(Cancelled):
            archives.create_archive(self.resource, self.root / "backups", self.password, context)
        self.assertFalse(list((self.root / "backups").glob("*.amb")))
        self.assertFalse(list((self.root / "backups").glob("*.partial")))

    def test_archive_paths_reject_traversal_windows_devices_and_links(self):
        for name in ["../escape", "/absolute", "C:/secret", "files\\escape", "x/../y", "CON.txt", "x/NUL", "trail."]:
            with self.subTest(name=name), self.assertRaises(UserError):
                archives.safe_name(name)

    def test_git_bundle_preserves_local_commits_and_untracked_files(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)
        git("init")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "笔记.md")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "local commit")
        self.require_git_temp_access(clone_probe=True)
        (self.project / "untracked.txt").write_text("untracked")
        source = self.backup()
        target = self.root / "restore"
        plan = archives.plan_restore(self.resource, source, target, self.password, self.context)
        archives.apply_restore(plan, self.password, self.context)
        self.assertEqual("untracked", (target / "untracked.txt").read_text())
        bundle = target / ".selfManger-history.bundle"
        clone = self.root / "repo-clone"
        subprocess.run(["git", "clone", str(bundle), str(clone)], check=True, capture_output=True)
        self.assertEqual((self.project / "笔记.md").read_text(encoding="utf-8"), (clone / "笔记.md").read_text(encoding="utf-8"))

    def test_new_git_repository_without_commits_is_backed_up(self):
        subprocess.run(["git", "init", str(self.project)], check=True, capture_output=True)
        self.require_git_temp_access(bundle_probe=False)
        self.assertTrue(self.backup().is_file())


if __name__ == "__main__":
    unittest.main()
