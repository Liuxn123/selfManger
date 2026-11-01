from __future__ import annotations

import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from selfManger.project_git import run_project_git_action, validate_project_repository_url
from tests.support import TestDirectory


class ProjectGitRemoteSafetyTests(unittest.TestCase):
    def test_repository_url_validation_accepts_safe_https_and_ssh(self):
        for value in (
            "https://github.com/example/project.git",
            "ssh://git@github.com/example/project.git",
            "git@github.com:example/project.git",
        ):
            with self.subTest(value=value):
                self.assertTrue(validate_project_repository_url(value))

    def test_repository_url_validation_rejects_insecure_or_credential_bearing_values(self):
        for value in (
            "http://example.com/project.git",
            "git://example.com/project.git",
            "https://user@example.com/project.git",
            "https://user:secret@example.com/project.git",
            "https://example.com/project.git?token=secret",
            "ssh://git@example.com/project.git#fragment",
            "ssh://example.com:bad/project.git",
            "-c core.sshCommand=evil",
            "https://example.com/project.git extra",
        ):
            with self.subTest(value=value):
                self.assertFalse(validate_project_repository_url(value))

    def test_init_rejects_insecure_registered_remote_before_git_mutation(self):
        fixture = TestDirectory()
        try:
            temporary = fixture.name
            with patch("selfManger.project_git.shutil.which", return_value="git"), \
                 patch("selfManger.project_git.subprocess.run") as run:
                result = run_project_git_action(
                    temporary, "init", threading.Event(), registered_repository="http://example.com/repo.git"
                )
        finally:
            fixture.cleanup()
        self.assertFalse(result["ok"])
        run.assert_not_called()

    def test_fetch_rejects_http_remote_before_network_operation(self):
        fixture = TestDirectory()
        try:
            temporary = fixture.name
            (Path(temporary) / ".git").mkdir()
            remote_names = subprocess.CompletedProcess([], 0, "origin\n", "")
            remote_urls = subprocess.CompletedProcess([], 0,
                "origin\thttp://example.com/repo.git (fetch)\n"
                "origin\thttp://example.com/repo.git (push)\n", "")
            with patch("selfManger.project_git.shutil.which", return_value="git"), \
                 patch("selfManger.project_git._run_git_read", side_effect=[remote_names, remote_urls]) as read, \
                 patch("selfManger.project_git.subprocess.run") as run:
                result = run_project_git_action(temporary, "fetch", threading.Event(), token="secret")
        finally:
            fixture.cleanup()
        self.assertFalse(result["ok"])
        self.assertIn("HTTPS/SSH", result["message"])
        self.assertEqual(read.call_count, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
