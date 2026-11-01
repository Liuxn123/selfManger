from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def validate_project_repository_url(value):
    """Accept only credential-safe HTTPS or SSH Git remotes."""
    value = str(value or "").strip()
    if not value or any(character.isspace() for character in value) or value.startswith("-"):
        return False
    if "://" not in value:
        return bool(re.fullmatch(r"(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+:[^\s:][^\s]*", value))
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or not parsed.hostname or not parsed.path.strip("/"):
            return False
        if parsed.password or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "https" and parsed.username:
            return False
        _ = parsed.port
        return True
    except ValueError:
        return False


def _safe_git_remote_url(value):
    """Hide user info and query strings that may contain credentials."""
    value = str(value or "").strip()
    if "://" in value:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parsed.port:
                host += f":{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            return "（地址格式无法安全显示）"
    if re.match(r"^[^\s:]+@[^\s:]+:.+$", value):
        return value.rsplit("@", 1)[-1]
    return value


def project_git_repositories(directory) -> list[Path] | None:
    """Find the project repository, including a single nested application repository."""
    try:
        root = Path(directory).resolve(strict=True)
    except OSError:
        return None
    if not root.is_dir():
        return None
    if (root / ".git").exists():
        return [root]

    ignored = {".agents", ".cache", ".codex", ".git", ".obsidian", ".venv", "build", "dist",
               "node_modules", "__pycache__", "reference", "references", "third-party", "third_party",
               "vendor", "vendors", "venv"}
    repositories = []
    scan_errors = []
    for current, directories, files in os.walk(root, onerror=scan_errors.append):
        current_path = Path(current)
        if (current_path / ".git").exists():
            repositories.append(current_path)
            directories[:] = []
            continue
        depth = len(current_path.relative_to(root).parts)
        if depth >= 4:
            directories[:] = []
            continue
        directories[:] = [name for name in directories
                          if name.casefold() not in ignored and not name.startswith(".")
                          and not (current_path / name).is_symlink()
                          and not getattr(current_path / name, "is_junction", lambda: False)()]
        if ".git" in files:
            repositories.append(current_path)
            directories[:] = []
    if scan_errors:
        return None
    return sorted(repositories, key=lambda path: path.as_posix().casefold())


def _run_git_read(git, root, args, environment):
    return subprocess.run(
        [git, "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=6, check=False, shell=False, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def read_project_git_status(directory, cancel, registered_repository=""):
    git = shutil.which("git")
    try:
        root = Path(directory).resolve(strict=True)
    except OSError:
        return {"ok": False, "text": "项目目录无法读取。"}
    if not git or not (root / ".git").exists():
        return {"ok": False, "text": "此项目没有可用的 Git 仓库。"}
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    try:
        status = _run_git_read(git, root, ["status", "--porcelain=v1", "--branch", "--ahead-behind", "--untracked-files=all"], environment)
        if cancel.is_set():
            return None
        remotes = _run_git_read(git, root, ["remote", "-v"], environment)
        if cancel.is_set():
            return None
        last_commit = _run_git_read(git, root, ["log", "-1", "--format=%h%x09%s%x09%cr"], environment)
    except Exception:
        return {"ok": False, "text": "Git 状态读取失败或超时。请确认项目目录可访问、Git 已正确安装；没有修改仓库。"}
    if cancel.is_set():
        return None
    if status.returncode:
        return {"ok": False, "text": "Git 无法读取此目录状态；没有修改仓库。"}

    lines = status.stdout.splitlines()
    header = next((line[3:] for line in lines if line.startswith("## ")), "")
    branch_part, separator, tracking_part = header.partition("...")
    if branch_part.startswith("No commits yet on "):
        branch = branch_part.removeprefix("No commits yet on ")
    else:
        branch = branch_part or "HEAD（游离状态）"
    tracking = tracking_part.split(maxsplit=1)[0] if separator and tracking_part else ""
    ahead_match = re.search(r"\bahead (\d+)\b", tracking_part)
    behind_match = re.search(r"\bbehind (\d+)\b", tracking_part)
    ahead = int(ahead_match.group(1)) if ahead_match else 0
    behind = int(behind_match.group(1)) if behind_match else 0

    changed_files = []
    file_rows = []
    staged = unstaged = untracked = 0
    for line in lines:
        if not line or line.startswith("## "):
            continue
        code, path = line[:2], line[3:] if len(line) > 3 else ""
        if code == "??":
            untracked += 1
            state = "未跟踪"
        else:
            if code[0] not in {".", " "}:
                staged += 1
            if code[1] not in {".", " "}:
                unstaged += 1
            state_names = {"A": "新增", "M": "已修改", "D": "已删除", "R": "已重命名", "C": "已复制", "U": "冲突"}
            state = " / ".join(dict.fromkeys(state_names.get(char, "已修改") for char in code if char not in {".", " "}))
        changed_files.append(f"{code}  {path}  ·  {state}")
        file_rows.append({"code": code, "path": path, "state": state,
                          "staged": code != "??" and code[0] not in {".", " "},
                          "unstaged": code == "??" or code[1] not in {".", " "}})

    remote_rows = []
    if remotes.returncode == 0:
        for line in remotes.stdout.splitlines():
            if "\t" not in line:
                continue
            name, address = line.split("\t", 1)
            if " " not in address:
                continue
            url, kind = address.rsplit(" ", 1)
            direction = kind.strip("()")
            remote_rows.append((name, direction, _safe_git_remote_url(url)))

    change_count = len(changed_files)
    change_summary = ("工作区干净" if not change_count else
                      f"{change_count} 个变更文件（暂存 {staged}、未暂存 {unstaged}、未跟踪 {untracked}）")
    summary_parts = [f"分支：{branch}", change_summary]
    if tracking:
        summary_parts.append(f"上游：{tracking}（本地领先 {ahead}、落后 {behind}）")
    else:
        summary_parts.append("未设置上游分支")

    details = ["本地仓库", f"目录：{root}", f"当前分支：{branch}",
               f"上游分支：{tracking or '未设置'}"]
    if tracking:
        details.append(f"本地与上游差异：领先 {ahead} 个提交，落后 {behind} 个提交")
    else:
        details.append("本地与远程差异：当前分支尚未配置上游分支")
    details.extend((f"工作区变更：{change_count} 个文件", f"已暂存：{staged} 个",
                    f"未暂存：{unstaged} 个", f"未跟踪：{untracked} 个"))
    if last_commit.returncode == 0 and last_commit.stdout.strip():
        commit_hash, _, commit_info = last_commit.stdout.strip().partition("\t")
        subject, _, relative_time = commit_info.partition("\t")
        details.extend(("", "最近提交", f"{commit_hash}  {subject}  ({relative_time})"))
    else:
        details.extend(("", "最近提交", "仓库还没有提交记录。"))

    details.extend(("", "工作区文件"))
    if changed_files:
        details.extend(changed_files[:100])
        if len(changed_files) > 100:
            details.append(f"…另有 {len(changed_files) - 100} 个文件未显示。")
    else:
        details.append("没有本地变更。")

    details.extend(("", "远程仓库（只读）"))
    if remote_rows:
        for name, direction, address in remote_rows:
            details.append(f"{name}（{'获取' if direction == 'fetch' else '推送'}）：{address}")
    else:
        details.append("Git 配置中尚未登记远程仓库。")
    registered_repository = _safe_git_remote_url(registered_repository)
    if registered_repository:
        details.extend(("", f"项目信息登记的仓库：{registered_repository}"))
    details.extend(("", "远程领先/落后数量基于本地已有的远程跟踪记录；本次没有联网 fetch，也没有修改仓库。"))
    commit = None
    if last_commit.returncode == 0 and last_commit.stdout.strip():
        commit_hash, _, commit_info = last_commit.stdout.strip().partition("\t")
        subject, _, relative_time = commit_info.partition("\t")
        commit = {"hash": commit_hash, "subject": subject, "time": relative_time}
    return {"ok": True, "summary": "  ·  ".join(summary_parts), "text": "\n".join(details),
            "directory": str(root), "branch": branch, "tracking": tracking, "ahead": ahead, "behind": behind,
            "staged": staged, "unstaged": unstaged, "untracked": untracked, "change_count": change_count,
            "files": file_rows, "commit": commit, "remotes": remote_rows,
            "registered_repository": registered_repository}


def run_project_git_action(directory, action, cancel, commit_message="", token="", registered_repository="", file_path=""):
    allowed = {"init", "stage", "stage_file", "unstage_file", "unstage_all", "commit", "fetch", "pull", "push"}
    if action not in allowed:
        return {"ok": False, "message": "不支持此 Git 操作。"}
    try:
        root = Path(directory).resolve(strict=True)
    except OSError:
        return {"ok": False, "message": "项目仓库目录无法读取。"}
    if not root.is_dir():
        return {"ok": False, "message": "项目仓库路径不是目录。"}
    if action != "init" and not (root / ".git").exists():
        return {"ok": False, "message": "所选目录尚未初始化 Git。"}
    git = shutil.which("git")
    if not git:
        return {"ok": False, "message": "没有找到 Git，请先安装 Git。"}

    commands = {
        "init": ["init"],
        "stage": ["add", "-A"],
        "stage_file": ["add", "-A", "--", file_path],
        "unstage_file": ["restore", "--staged", "--", file_path],
        "unstage_all": ["reset", "--mixed", "HEAD"],
        "commit": ["commit", "-m", commit_message.strip()],
        "fetch": ["fetch"],
        "pull": ["pull", "--ff-only"],
        "push": ["push"],
    }
    command = commands[action]
    if action in {"stage_file", "unstage_file"} and (not file_path or Path(file_path).is_absolute() or
            ".." in Path(file_path).parts):
        return {"ok": False, "message": "文件路径无效，未执行 Git 操作。"}
    if action == "commit" and not commit_message.strip():
        return {"ok": False, "message": "请填写提交说明。"}
    if action == "init" and registered_repository and not validate_project_repository_url(registered_repository):
        return {"ok": False, "message": "仓库地址仅支持无凭据的 HTTPS/SSH 地址，请在项目信息中修正。"}
    if action in {"fetch", "pull", "push"}:
        remotes = _run_git_read(git, root, ["remote"], {**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if remotes.returncode or not remotes.stdout.strip():
            return {"ok": False, "message": "尚未配置远程仓库。请先在项目资料中设置仓库地址，或在 Git 中添加 origin。"}
        remote_urls = _run_git_read(git, root, ["remote", "-v"], {**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if remote_urls.returncode:
            return {"ok": False, "message": "无法安全核对远程仓库地址，未执行联网操作。"}
        configured_urls = [line.split("\t", 1)[1].rsplit(" ", 1)[0]
                           for line in remote_urls.stdout.splitlines() if "\t" in line and " " in line]
        if not configured_urls or any(not validate_project_repository_url(url) for url in configured_urls):
            return {"ok": False, "message": "远程仓库地址不安全或格式无法识别；请改为 HTTPS/SSH 后重试。"}
        status = _run_git_read(git, root, ["status", "--porcelain=v1", "--branch"],
                               {**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        header = next((line[3:] for line in status.stdout.splitlines() if line.startswith("## ")), "")
        if "..." not in header and "origin" in remotes.stdout.splitlines():
            branch = _run_git_read(git, root, ["branch", "--show-current"],
                                   {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip()
            if branch:
                if action == "push":
                    command = ["push", "-u", "origin", branch]
                elif action == "pull":
                    command = ["pull", "--ff-only", "origin", branch]

    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    auth_scope = tempfile.TemporaryDirectory(prefix="selfManger-git-") if token and action in {"fetch", "pull", "push"} else nullcontext("")
    try:
        with auth_scope as temporary:
            if temporary:
                askpass = Path(temporary) / "git-askpass.cmd"
                askpass.write_text(
                    "@echo off\r\npowershell.exe -NoProfile -NonInteractive -Command "
                    "\"[Console]::Out.Write([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SELFMANGER_GIT_TOKEN_B64)))\"\r\n",
                    encoding="utf-8",
                )
                environment["GIT_ASKPASS"] = str(askpass)
                environment["SELFMANGER_GIT_TOKEN_B64"] = base64.b64encode(token.encode("utf-8")).decode("ascii")
            result = subprocess.run(
                [git, "-c", "core.fsmonitor=false", "-C", str(root), *command],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, check=False, shell=False, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Git 操作超时；请检查网络或仓库状态。"}
    except OSError:
        return {"ok": False, "message": "无法启动 Git 操作；请检查 Git 安装与目录权限。"}
    except Exception:
        return {"ok": False, "message": "Git 操作未完成；请检查项目目录、Git 配置和网络。"}
    if cancel.is_set():
        return None
    if result.returncode:
        from ..security import redact
        detail = redact((result.stderr or result.stdout).strip())
        return {"ok": False, "message": f"Git {action} 未完成。" + ("\n" + detail if detail else "")}

    if action == "init" and registered_repository:
        remote_check = _run_git_read(git, root, ["remote", "get-url", "origin"],
                                     {**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if remote_check.returncode:
            remote_add = subprocess.run(
                [git, "-C", str(root), "remote", "add", "origin", registered_repository.strip()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                check=False, shell=False, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if remote_add.returncode:
                from ..security import redact
                return {"ok": False, "message": "仓库已初始化，但未能添加 origin。\n" +
                        redact((remote_add.stderr or remote_add.stdout).strip())}

    labels = {"init": "已初始化 Git 仓库", "stage": "已暂存全部变更", "stage_file": "文件已暂存",
              "unstage_file": "已取消暂存", "unstage_all": "已取消全部暂存", "commit": "提交已创建",
              "fetch": "已获取远程状态", "pull": "已完成快进拉取", "push": "已推送到远程仓库"}
    status = read_project_git_status(root, cancel, registered_repository)
    return {"ok": True, "message": labels[action], "status": status}
