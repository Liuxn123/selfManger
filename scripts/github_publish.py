"""Publish this source-only project using an existing Git credential.

Never prints credentials and never writes them to repository files or remotes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def credential() -> str:
    supplied = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if supplied:
        return supplied
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}, cwd=ROOT)
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise RuntimeError("No existing GitHub credential; sign in with Git Credential Manager or GitHub CLI.")
    return fields["password"]


def api(token: str, route: str, data=None, method: str | None = None):
    request = urllib.request.Request("https://api.github.com" + route,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "selfManger-publisher"}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub HTTP {exc.code}") from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--name", default="selfManger")
    options = parser.parse_args()
    token = credential()
    user = api(token, "/user")["login"]
    if options.probe:
        print(json.dumps({"authenticated": True, "owner": user}))
        return
    route = f"/repos/{user}/{options.name}"
    if options.status:
        repo = api(token, route)
        runs = api(token, route + "/actions/runs?per_page=5")
        print(json.dumps({"url": repo["html_url"], "private": repo["private"], "runs": [
            {key: run.get(key) for key in ("id", "name", "status", "conclusion", "html_url", "head_sha")} for run in runs.get("workflow_runs", [])]}))
        return
    if not options.create:
        parser.error("Choose --probe, --create or --status")
    try:
        existing = api(token, route)
    except RuntimeError as exc:
        if str(exc) != "GitHub HTTP 404":
            raise
        existing = None
    if existing:
        if not existing["private"] or existing["size"]:
            raise RuntimeError("Repository already exists and is not an empty private repository; refusing to overwrite.")
        repo = existing
    else:
        repo = api(token, "/user/repos", {"name": options.name, "private": True, "auto_init": False,
            "description": "Personal cross-platform desktop manager for plans, projects and knowledge"})
    if repo.get("private") is not True:
        raise RuntimeError("Private repository verification failed; push refused.")
    print(json.dumps({"url": repo["html_url"], "private": True, "clone_url": repo["clone_url"]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Deliberately omit raw transport errors and credential helper output.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print("Publish failed: " + message, file=sys.stderr)
        raise SystemExit(1)
