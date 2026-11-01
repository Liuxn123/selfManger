"""Project lifecycle compatible with the user's projects.json v2 convention."""
from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import shutil
import socket
import stat
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

from .domain import Cancelled, UserError
from .naming import temporary_path
from .runtime import TaskContext
from .project_prompts import AGENT_WORKFLOW, prompt_document
from .project_initialization import validate_selection, initialization_files
from .project_skill_copies import prepare_skill_copies, copy_project_skills

STATES = {"candidate": "待开展", "active": "进行中", "paused": "暂停", "completed": "已完成", "archived": "已归档"}
MANAGEMENT_FILES = ("README.md", "AGENTS.md", "agent/README.md", "agent/STATUS.md", "agent/TASKS.md",
                    "agent/TASKS-AI.md", "agent/TASKS-MANUAL.md", "agent/HANDOFF.md", "agent/MILESTONES.md", "agent/PROMPTS.md")
OPTIONAL_MANAGEMENT_FILES = {"agent/TASKS-AI.md", "agent/TASKS-MANUAL.md", "agent/MILESTONES.md", "agent/PROMPTS.md"}
DEFAULT_VAULT_SUBDIRS = ("agent", "笔记", "文档")


def today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def linked(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def optional_field(text: str, name: str) -> str | None:
    # Accept older STATUS files whose closing delimiter is immediately joined
    # to the following Markdown heading (for example `---# ...`).
    front = re.match(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|$|#)", text, re.S)
    values = re.findall(r"^" + re.escape(name) + r":[ \t]*(.*)$", front.group(1), re.M) if front else []
    if not front or len(values) > 1:
        raise UserError(f"管理文档缺少或重复字段：{name}")
    return values[0].strip().strip('\"\'') if values else None


def field(text: str, name: str) -> str:
    value = optional_field(text, name)
    if value is None:
        raise UserError(f"管理文档缺少或重复字段：{name}")
    return value


def replace_state(text: str, state: str) -> str:
    field(text, "status")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|$|#)", text, re.S)
    if not match:
        raise UserError("STATUS 缺少有效 frontmatter。")
    front, body = match.group(1), text[match.end():]
    front = re.sub(r"(?m)^status:.*$", "status: " + state, front)
    if re.search(r"(?m)^updated:", front):
        front = re.sub(r"(?m)^updated:.*$", "updated: " + today(), front)
    else:
        front = front.rstrip("\r\n") + "\nupdated: " + today()
    body = re.sub(r"(?m)^- 生命周期：.*$", "- 生命周期：" + state, body)
    if body and not body.startswith(("\n", "\r")):
        body = "\n" + body
    return "---\n" + front.rstrip("\r\n") + "\n---" + body


def lifecycle_state(text: str) -> str:
    """Read the project lifecycle from STATUS rather than inferring it from its folder."""
    return field(text, "status")


def replace_frontmatter_value(text: str, name: str, value: str | None) -> str:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?=\r?\n|$|#)", text, re.S)
    if not match:
        raise UserError("STATUS 缺少有效 frontmatter。")
    front = match.group(1)
    pattern = r"(?m)^" + re.escape(name) + r":[^\r\n]*\r?\n?"
    front = re.sub(pattern, "", front)
    if value is not None:
        front = front.rstrip("\r\n") + f"\n{name}: {value}\n"
    body = text[match.end():]
    if body and not body.startswith(("\n", "\r")):
        body = "\n" + body
    return "---\n" + front.rstrip("\r\n") + "\n---" + body


class ProjectWorkspace:
    def __init__(self, root: Path):
        selected = Path(os.path.abspath(root.expanduser()))
        if linked(selected):
            raise UserError("请选择实际工作区根目录，不能使用目录联接入口。")
        self.root = selected.resolve()
        self.path("")
        self.registry_path = self.path("agent/projects.json")

    def path(self, relative: str, allow_link: bool = False) -> Path:
        part = Path(relative)
        if part.anchor or ".." in part.parts or re.search(r'[<>:"|?*\x00-\x1f]', relative):
            raise UserError("工作区路径越界。")
        result = self.root / part
        for parent in reversed(self.root.parents):
            if linked(parent):
                raise UserError("工作区父目录不能是联接或符号链接。")
        chain = [self.root]
        for component in part.parts:
            chain.append(chain[-1] / component)
        for candidate in chain:
            if linked(candidate) and not (allow_link and candidate == result):
                raise UserError("管理路径不能穿过联接或符号链接：" + str(candidate))
        return result

    @staticmethod
    def read(path: Path) -> str:
        if path.stat().st_size > 2_000_000:
            raise UserError("管理文件过大，请先检查内容。")
        return path.read_text(encoding="utf-8-sig")

    def registry(self) -> dict:
        if hasattr(self, "_read_registry"):
            return copy.deepcopy(self._read_registry)
        try:
            data = json.loads(self.read(self.registry_path))
        except (OSError, ValueError) as exc:
            raise UserError("未找到有效的 agent/projects.json；请选择工作区或初始化新的空工作区。") from exc
        if (not isinstance(data, dict) or data.get("schema_version") != 2
                or not isinstance(data.get("projects"), list) or not isinstance(data.get("issued_ids"), list)):
            raise UserError("需要 projects.json v2 登记表。")
        ids, paths, entries = set(), set(), set()
        issued = data["issued_ids"]
        if (any(not isinstance(i, str) for i in issued) or len(set(issued)) != len(issued)
                or any(not re.fullmatch(r"P-\d{4}-\d{3}", i) or i.endswith("000") for i in issued)):
            raise UserError("已发编号清单有重复或无效编号。")
        for item in data["projects"]:
            if not isinstance(item, dict):
                raise UserError("项目登记条目必须是对象。")
            identity, name, relative = item.get("id", ""), item.get("name", ""), item.get("path", "")
            if (not isinstance(identity, str) or not isinstance(name, str) or not isinstance(relative, str)
                    or not name or any(c in name for c in "\r\n|")
                    or not re.fullmatch(r"projects/[^/\\]+", relative)):
                raise UserError("项目名称或登记路径无效。")
            description = item.get("description", "")
            if not isinstance(description, str) or len(description) > 4000:
                raise UserError("项目介绍无效或超过 4000 个字符。")
            if item.get("legacy") is True:
                if identity != "L-" + name:
                    raise UserError("旧项目登记不一致。")
            else:
                folder_name = item.get("folder_name")
                if folder_name is not None and (not isinstance(folder_name, str)
                                                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", folder_name)):
                    raise UserError("项目英文目录名不符合命名规范。")
                expected_folder = identity + "-" + (folder_name or name)
                if item.get("legacy") is not False or identity not in issued or Path(relative).name != expected_folder:
                    raise UserError("项目编号、名称与目录不一致。")
            entry = item.get("vault_entry")
            link_policy = item.get("link_policy")
            if not isinstance(link_policy, str) or link_policy not in {"junction", "symlink", "external", "plain-text"}:
                raise UserError("项目入口策略无效。")
            if entry is not None and (not isinstance(entry, str)
                                      or entry and not re.fullmatch(r"myself/03-项目/[^/\\]+", entry)):
                raise UserError("Obsidian 入口无效。")
            if identity in ids or relative in paths or entry and entry in entries:
                raise UserError("项目登记重复。")
            ids.add(identity); paths.add(relative)
            if entry:
                entries.add(entry)
                self.path(entry, allow_link=True)
            if item.get("vault_scope"):
                if item["vault_scope"] != "documents" or not entry:
                    raise UserError("项目文档入口范围无效。")
                self._document_subdirs(item)
            self.path(relative)
        return data

    def read_snapshot(self) -> ProjectWorkspace:
        """Validate the registry once for a single background read operation.

        File access still goes through path(), including its link/boundary checks.
        Never retain this snapshot between refreshes or use it for mutations.
        """
        snapshot = ProjectWorkspace(self.root)
        snapshot._read_registry = self.registry()
        return snapshot

    @contextmanager
    def lock(self, action: str):
        lock = self.path("agent/.manage.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = lock.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise UserError("工作区已有管理锁。请等待其他工具完成；残留锁需要核对进程后处理。") from exc
        with stream:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "action": action, "root": str(self.root), "started_at": datetime.now(timezone.utc).isoformat()}, stream)
            stream.flush(); os.fsync(stream.fileno())
        try:
            yield
        finally:
            lock.unlink()

    def write(self, path: Path, text: str, expected: str | None = None):
        relative = path.relative_to(self.root).as_posix()
        self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if expected is None:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            return
        if self.read(path) != expected:
            raise UserError("管理文件已被其他程序修改，停止覆盖：" + str(path))
        temporary = temporary_path(path, uuid4().hex)
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        if self.read(path) != expected:
            temporary.unlink()
            raise UserError("管理文件在写入前发生变化，停止覆盖。")
        os.replace(temporary, path)

    def save_registry(self, data: dict, expected: str):
        data["updated"] = today()
        self.write(self.registry_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", expected)

    def initialize(self, context: TaskContext) -> dict:
        if self.root.exists() and any(self.root.iterdir()):
            raise UserError("初始化工作区需要一个新目录或空目录，不会重建已有工作区。")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock("Initialize"):
            for area in ("projects", "myself/03-项目"):
                self.path(area).mkdir(parents=True, exist_ok=True)
            self.write(self.registry_path, json.dumps({"schema_version": 2, "updated": today(), "projects": [], "issued_ids": []}, indent=2) + "\n")
            self.write(self.path("AGENTS.md"), "# 工作区规则\n\n编号不复用；身份与路径以 agent/projects.json 为准。\n进度以项目 STATUS 为准，任务只维护 TASKS 指定的真源，日志追加到 HANDOFF。\n项目之间默认隔离，不读凭据；创建与移动先检查冲突。\n同步副本不能同时分配编号；同一时刻由一台电脑管理本工作区。\n")
            self.update_indexes(self.registry())
        context.log("已初始化工作区管理结构；未创建 Git 仓库或定时任务。")
        return {"workspace": str(self.root), "initialized": True}

    def list_projects(self, context: TaskContext) -> dict:
        rows = []
        for project in self.registry()["projects"]:
            context.checkpoint()
            path = self.path(project["path"])
            try:
                status = self.read(self.path(project["path"] + "/agent/STATUS.md"))
                state = field(status, "status")
                if state not in STATES or field(status, "project_id") != project["id"]:
                    raise UserError("状态与项目编号不一致。")
                error = ""
            except (OSError, UserError) as exc:
                state, error = "unknown", str(exc)
            rows.append({**project, "directory": str(path), "state": state, "error": error})
        return {"projects": rows, "workspace": str(self.root)}

    def sync_archived_vault_entries(self, context: TaskContext) -> dict:
        """Hide validated links for archived projects and repair their index rows.

        The caller holds the workspace resource lock. Each project's index/link
        changes are rolled back together if that project's update fails.
        """
        hidden = 0
        errors = []
        for item in self.registry()["projects"]:
            context.checkpoint()
            if not item.get("vault_entry") or item.get("link_policy") not in {"junction", "symlink"}:
                continue
            identity = item["id"]
            project_path = self.path(item["path"])
            try:
                status_path = self.path(item["path"] + "/agent/STATUS.md")
                status = self.read(status_path)
                if lifecycle_state(status) != "archived" or field(status, "project_id") != identity:
                    continue

                entry_before = self._vault_entry_link_state(item, project_path)
                index_outputs = self.lifecycle_index_outputs(identity, "archived")
                context.checkpoint()
                written_indexes: list[tuple[Path, str, str]] = []
                try:
                    for index_path, updated, original in index_outputs:
                        if updated != original:
                            self.write(index_path, updated, original)
                            written_indexes.append((index_path, updated, original))
                    self._set_vault_entry_link(item, project_path, False)
                except Exception as exc:
                    rollback_errors = []
                    try:
                        self._set_vault_entry_link(item, project_path, entry_before)
                    except (OSError, UserError) as rollback_exc:
                        rollback_errors.append("Obsidian 入口回退失败：" + str(rollback_exc))
                    for index_path, updated, original in reversed(written_indexes):
                        try:
                            self.write(index_path, original, updated)
                        except (OSError, UserError) as rollback_exc:
                            rollback_errors.append("索引回退失败：" + str(rollback_exc))
                    if rollback_errors:
                        raise UserError("归档入口同步失败，自动回退需要检查：\n" + "\n".join(rollback_errors)) from exc
                    raise
                hidden += int(entry_before)
            except Cancelled:
                raise
            except (OSError, UserError) as exc:
                errors.append({"project_id": identity, "message": str(exc)})
        return {"hidden_count": hidden, "errors": errors}

    def set_archived(self, identity: str, archived: bool, context: TaskContext) -> dict:
        """Archive a project and hide only its validated Obsidian directory link."""
        with self.lock("ArchiveStatus" if archived else "ResumeStatus"):
            item = self.project(identity)
            project_path = self.path(item["path"])
            status_path = self.path(item["path"] + "/agent/STATUS.md")
            try:
                before = self.read(status_path)
            except FileNotFoundError as exc:
                raise UserError(
                    "项目状态文件不存在，未执行归档或重新启用。请恢复项目工作区或修正登记路径：\n" + str(status_path)
                ) from exc
            except PermissionError as exc:
                raise UserError(
                    "没有权限读取项目状态文件，未执行归档或重新启用：\n" + str(status_path)
                ) from exc
            state = lifecycle_state(before)
            if field(before, "project_id") != identity or state not in STATES:
                raise UserError("项目状态或编号需要检查，未执行归档操作。")
            if archived:
                if state == "archived":
                    raise UserError("项目已经归档。")
                after = replace_state(before, "archived")
                after = replace_frontmatter_value(after, "archived_from", state)
            else:
                if state != "archived":
                    raise UserError("项目当前不是已归档状态。")
                prior = optional_field(before, "archived_from")
                if prior is None:
                    # Older archived records did not persist their former state.
                    prior = "active"
                if prior not in STATES or prior == "archived":
                    raise UserError("项目归档前状态无效，未执行恢复。")
                after = replace_frontmatter_value(before, "archived_from", None)
                after = replace_state(after, prior)
            entry_before = self._vault_entry_link_state(item, project_path)
            index_outputs = self.lifecycle_index_outputs(identity, "archived" if archived else prior)
            context.checkpoint()
            status_written = False
            written_indexes: list[tuple[Path, str, str]] = []
            try:
                self.write(status_path, after, before)
                status_written = True
                for index_path, updated, original in index_outputs:
                    self.write(index_path, updated, original)
                    written_indexes.append((index_path, updated, original))
                self._set_vault_entry_link(item, project_path, not archived)
            except Exception as exc:
                rollback_errors = []
                try:
                    self._set_vault_entry_link(item, project_path, entry_before)
                except (OSError, UserError) as rollback_exc:
                    rollback_errors.append("Obsidian 入口回退失败：" + str(rollback_exc))
                for index_path, updated, original in reversed(written_indexes):
                    try:
                        self.write(index_path, original, updated)
                    except (OSError, UserError) as rollback_exc:
                        rollback_errors.append("索引回退失败：" + str(rollback_exc))
                if status_written:
                    try:
                        self.write(status_path, before, after)
                    except (OSError, UserError) as rollback_exc:
                        rollback_errors.append("项目状态回退失败：" + str(rollback_exc))
                if rollback_errors:
                    raise UserError("状态切换失败，自动回退需要检查：\n" + "\n".join(rollback_errors)) from exc
                if isinstance(exc, UserError):
                    raise
                raise UserError("状态切换失败，项目状态与索引已尝试恢复：" + str(exc)) from exc
        return {"project_id": identity, "state": "archived" if archived else prior,
                "path": str(project_path), "moved": False,
                "vault_entry_hidden": bool(archived and item.get("vault_entry"))}

    def delete_project(self, identity: str, confirmation: str, context: TaskContext) -> dict:
        """Permanently delete one archived project after its exact name is confirmed."""
        with self.lock("DeleteProject"):
            observed = self.read(self.registry_path)
            data = self.registry()
            if self.read(self.registry_path) != observed:
                raise UserError("项目登记在读取时发生变化，请刷新后重试。")
            item = next((project for project in data["projects"] if project["id"] == identity), None)
            if item is None:
                raise UserError("项目登记已变化，请刷新列表。")
            if confirmation != item["name"]:
                raise UserError("输入的项目名称不匹配，未删除项目。")

            directory = self.path(item["path"])
            status_path = self.path(item["path"] + "/agent/STATUS.md")
            try:
                status = self.read(status_path)
            except FileNotFoundError as exc:
                raise UserError("项目状态文件不存在，未删除项目：\n" + str(status_path)) from exc
            except PermissionError as exc:
                raise UserError("没有权限读取项目状态文件，未删除项目：\n" + str(status_path)) from exc
            if field(status, "project_id") != identity or lifecycle_state(status) not in STATES:
                raise UserError("项目状态或编号需要检查，未删除项目。")
            if lifecycle_state(status) != "archived":
                raise UserError("请先归档项目，再执行删除。")
            if not directory.is_dir():
                raise UserError("项目目录不存在，未删除登记：\n" + str(directory))
            if linked(directory):
                raise UserError("项目目录本身是联接或符号链接；为避免触及外部资料，已停止删除。")

            entry_path = None
            if item.get("vault_entry"):
                entry_path = self.path(item["vault_entry"], allow_link=True)
                if item.get("vault_scope") == "documents":
                    self._vault_entry_link_state(item, directory)
                    visible, hidden = self._document_entry_paths(item)
                    entry_path = visible if visible.exists() else hidden if hidden.exists() else None
                elif linked(entry_path):
                    self._vault_entry_link_state(item, directory)
                elif entry_path.exists():
                    raise UserError("登记的 Obsidian 入口不是目录联接，未删除项目：\n" + str(entry_path))
                else:
                    # Archived projects intentionally have no Vault link.
                    entry_path = None

            walk_errors: list[OSError] = []
            for current, subdirs, files in os.walk(directory, topdown=True, followlinks=False,
                                                   onerror=walk_errors.append):
                context.checkpoint()
                for name in subdirs + files:
                    nested = Path(current) / name
                    if linked(nested):
                        raise UserError("项目目录内含联接或符号链接，为避免删除外部资料已停止：\n" + str(nested))
            if walk_errors:
                raise UserError("无法完整检查项目目录，为避免部分删除已停止：\n" + str(walk_errors[0].filename))

            remaining_data = {
                **data,
                "projects": [project for project in data["projects"] if project["id"] != identity],
            }
            # Deletion only needs to remove this project's generated index rows.
            # Rebuilding the full indexes would make an unrelated malformed
            # STATUS.md block deletion for every otherwise valid archived project.
            index_outputs = self.delete_index_outputs(remaining_data, identity)
            context.checkpoint()
            token = uuid4().hex
            staged_directory = self.path("projects/.delete-" + token)
            if staged_directory.exists():
                raise UserError("删除暂存路径发生冲突，请稍后重试。")
            staged_entry = None
            if entry_path is not None:
                staged_entry = self.path(item["vault_entry"] + ".delete-" + token)
                if staged_entry.exists() or linked(staged_entry):
                    raise UserError("Obsidian 入口暂存路径发生冲突，请稍后重试。")

            moved_directory = False
            moved_entry = False
            registry_saved = False
            written_indexes: list[tuple[Path, str, str]] = []
            try:
                os.replace(directory, staged_directory)
                moved_directory = True
                if entry_path is not None and staged_entry is not None:
                    os.replace(entry_path, staged_entry)
                    moved_entry = True

                data = remaining_data
                self.save_registry(data, observed)
                registry_saved = True
                for index_path, after, before in index_outputs:
                    self.write(index_path, after, before)
                    written_indexes.append((index_path, after, before))
            except Exception as exc:
                rollback_errors = []
                if registry_saved:
                    try:
                        current_registry = self.read(self.registry_path)
                        self.write(self.registry_path, observed, current_registry)
                    except (OSError, UserError) as rollback_exc:
                        rollback_errors.append("登记表回退失败：" + str(rollback_exc))
                if moved_entry and staged_entry is not None and entry_path is not None:
                    try:
                        os.replace(staged_entry, entry_path)
                    except OSError as rollback_exc:
                        rollback_errors.append("Obsidian 入口回退失败：" + str(rollback_exc))
                if moved_directory:
                    try:
                        os.replace(staged_directory, directory)
                    except OSError as rollback_exc:
                        rollback_errors.append("项目目录回退失败：" + str(rollback_exc))
                for index_path, after, before in reversed(written_indexes):
                    try:
                        self.write(index_path, before, after)
                    except (OSError, UserError) as rollback_exc:
                        rollback_errors.append("索引回退失败：" + str(rollback_exc))
                if rollback_errors:
                    raise UserError("删除未完成，且自动回退需要检查：\n" + "\n".join(rollback_errors)
                                    + "\n暂存目录：" + str(staged_directory)) from exc
                if isinstance(exc, UserError):
                    raise
                raise UserError("删除未完成，项目目录、登记和索引已尝试恢复：" + str(exc)) from exc

            try:
                shutil.rmtree(staged_directory)
            except OSError as exc:
                raise UserError("项目已从登记和索引移除，但完整资料未能全部删除；请手动检查暂存目录：\n"
                                + str(staged_directory)) from exc
            if staged_entry is not None:
                try:
                    if item.get("vault_scope") == "documents":
                        self._remove_document_entry(staged_entry, item)
                    else:
                        os.rmdir(staged_entry)
                except OSError as exc:
                    raise UserError("项目目录已删除并从登记移除，但 Obsidian 入口未能清理；请手动检查：\n"
                                    + str(staged_entry)) from exc

        context.log("已删除已归档项目目录和登记入口；永久保留已发项目编号。")
        return {"project_id": identity, "name": item["name"], "deleted": True,
                "issued_id_retained": True, "directory": str(directory),
                "vault_entry": str(entry_path) if entry_path is not None else ""}

    def record_project_use(self, identity: str) -> str:
        """Persist the most recent project selection in the workspace registry."""
        with self.lock("RecordProjectUse"):
            observed = self.read(self.registry_path)
            data = self.registry()
            item = next((project for project in data["projects"] if project["id"] == identity), None)
            if item is None:
                return ""
            used_at = datetime.now().astimezone().isoformat(timespec="seconds")
            item["last_used_at"] = used_at
            self.save_registry(data, observed)
            return used_at

    def document(self, identity: str, relative: str) -> dict:
        if relative not in MANAGEMENT_FILES:
            raise UserError("只允许打开项目管理文档。")
        item = self.project(identity)
        text = self.read(self.path(item["path"] + "/" + relative))
        return {"text": text, "path": str(self.path(item["path"] + "/" + relative))}

    def project_detail_files(self, identity: str, relatives: tuple[str, ...], context: TaskContext) -> dict:
        """Read the file-backed project detail data while preserving partial failures."""
        item = self.project(identity)
        documents, errors = [], []
        for relative in relatives:
            context.checkpoint()
            try:
                documents.append(self.document(identity, relative))
            except (UserError, OSError) as exc:
                documents.append({"text": "", "path": str(self.path(item["path"] + "/" + relative)),
                                  "error": str(exc)})
                errors.append(relative + "：" + str(exc))
        try:
            project_documents = self.project_documents(identity, context)
            project_documents_error = ""
        except (UserError, OSError) as exc:
            project_documents = {"documents": [], "directory": str(self.path(item["path"] + "/文档"))}
            project_documents_error = str(exc)
        try:
            notes = self.notes(identity, context)["notes"]
            notes_error = ""
        except (UserError, OSError) as exc:
            notes, notes_error = [], str(exc)
        try:
            milestones = self.milestones_document(identity)
        except (UserError, OSError) as exc:
            milestones = {"text": "", "path": str(self.path(item["path"] + "/agent/MILESTONES.md")),
                          "exists": False, "error": str(exc)}
        return {"documents": documents, "errors": errors, "milestones": milestones,
                "project_documents": project_documents, "project_documents_error": project_documents_error,
                "notes": notes, "notes_error": notes_error}

    def project_documents(self, identity: str, context: TaskContext) -> dict:
        """List user-owned project material from the project's 文档 directory."""
        item = self.project(identity)
        folder = self.path(item["path"] + "/文档")
        rows = []
        if folder.exists():
            for path in sorted(folder.iterdir(), key=lambda entry: entry.name.casefold()):
                context.checkpoint()
                self.path(path.relative_to(self.root).as_posix())
                if path.is_file():
                    info = path.stat()
                    rows.append({"name": path.name, "relative": "文档/" + path.name,
                                 "size": info.st_size, "modified": datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")})
        return {"documents": rows, "directory": str(folder)}

    def project_document(self, identity: str, relative: str) -> dict:
        if not relative.startswith("文档/") or len(Path(relative).parts) != 2:
            raise UserError("请选择项目文档目录中的文件。")
        item = self.project(identity)
        path = self.path(item["path"] + "/" + relative)
        if not path.is_file():
            raise UserError("项目文档不存在或不是普通文件。")
        info = path.stat()
        text_extensions = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".xml", ".html", ".css", ".js", ".py"}
        if path.suffix.lower() not in text_extensions:
            return {"text": "此文件类型不支持内置预览。可使用“打开文件”查看，或在 Obsidian 中编辑 Markdown 文件。",
                    "path": str(path), "previewable": False, "size": info.st_size}
        if info.st_size > 2_000_000:
            return {"text": "文件超过 2 MB，已跳过内置预览。可使用“打开文件”查看。", "path": str(path),
                    "previewable": False, "size": info.st_size}
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            return {"text": "文件不是 UTF-8 文本，已跳过内置预览。可使用“打开文件”查看。", "path": str(path),
                    "previewable": False, "size": info.st_size}
        return {"text": text, "path": str(path), "previewable": True, "markdown": path.suffix.lower() == ".md", "size": info.st_size}

    def ensure_project_documents_directory(self, identity: str, context: TaskContext) -> dict:
        with self.lock("OpenProjectDocuments"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("已归档项目为只读，请先重新启用。")
            folder = self.path(item["path"] + "/文档")
            context.checkpoint()
            folder.mkdir(exist_ok=True)
        return {"project_id": identity, "path": str(folder)}

    def upload_project_document(self, identity: str, source: str | Path, context: TaskContext) -> dict:
        source_path = Path(source).expanduser()
        if linked(source_path):
            raise UserError("请选择普通文件，不能上传目录联接或符号链接。")
        try:
            source_path = source_path.resolve(strict=True)
        except OSError as exc:
            raise UserError("所选文件已不存在或无法读取。") from exc
        if not source_path.is_file():
            raise UserError("请选择一个文件。")
        if source_path.stat().st_size > 500 * 1024 * 1024:
            raise UserError("单个项目文档不能超过 500 MB。")
        with self.lock("UploadProjectDocument"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("已归档项目为只读，请先重新启用。")
            folder = self.path(item["path"] + "/文档")
            folder.mkdir(exist_ok=True)
            target = self.path(item["path"] + "/文档/" + source_path.name)
            if target.resolve() == source_path:
                raise UserError("所选文件已经位于当前项目文档目录中。")
            created = False
            try:
                with source_path.open("rb") as incoming, target.open("xb") as copied:
                    created = True
                    while chunk := incoming.read(1024 * 1024):
                        context.checkpoint()
                        copied.write(chunk)
                shutil.copystat(source_path, target)
            except FileExistsError as exc:
                raise UserError("项目文档区已存在同名文件，没有覆盖原文件；请先为新文件改名。") from exc
            except Exception as exc:
                if created:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError as cleanup_exc:
                        raise UserError(
                            "项目文件复制失败，且未能清理可能不完整的副本："
                            + str(target) + "（" + str(cleanup_exc) + "）"
                        ) from exc
                raise
        context.log("已将项目资料副本保存到文档目录；原文件未移动或修改。")
        return {"project_id": identity, "name": target.name, "relative": "文档/" + target.name,
                "path": str(target), "directory": str(folder)}

    def milestones_document(self, identity: str) -> dict:
        """Read the user-maintained milestone document when it exists."""
        item = self.project(identity)
        path = self.path(item["path"] + "/agent/MILESTONES.md")
        if not path.exists():
            return {"text": "", "path": str(path), "exists": False}
        return {"text": self.read(path), "path": str(path), "exists": True}

    def set_milestone_status(self, identity: str, expected_text: str, line_number: int,
                             status_column: int, expected_status: str, completed: bool,
                             context: TaskContext) -> dict:
        """Toggle one milestone status without replacing concurrent Obsidian edits."""
        if not isinstance(line_number, int) or line_number < 1 or not isinstance(status_column, int) or status_column < 0:
            raise UserError("里程碑行位置无效，请刷新后重试。")
        if not isinstance(expected_text, str) or not isinstance(expected_status, str):
            raise UserError("里程碑内容无效，请刷新后重试。")
        new_status = "已完成" if completed else "待开始"
        context.checkpoint()
        with self.lock("SetMilestoneStatus"):
            item = self.project(identity)
            status_path = self.path(item["path"] + "/agent/STATUS.md")
            project_state = field(self.read(status_path), "status")
            if project_state == "archived":
                raise UserError("已归档项目为只读，请先重新启用。")
            if project_state not in STATES:
                raise UserError("项目状态需要检查，未更新里程碑；请先刷新项目状态。")
            path = self.path(item["path"] + "/agent/MILESTONES.md")
            if self.read(path) != expected_text:
                raise UserError("里程碑文档已被其他程序修改，请刷新后重试；文档未覆盖。")
            raw = path.read_bytes()
            has_bom = raw.startswith(b"\xef\xbb\xbf")
            source = raw.decode("utf-8-sig")
            normalized = source.replace("\r\n", "\n").replace("\r", "\n")
            if normalized != expected_text:
                raise UserError("里程碑文档在读取期间发生变化，请刷新后重试；文档未覆盖。")
            lines = source.splitlines(keepends=True)
            if line_number > len(lines):
                raise UserError("里程碑行已变化，请刷新后重试。")
            parts = lines[line_number - 1].split("|")
            cells = parts[1:-1] if len(parts) >= 3 else []
            if status_column >= len(cells):
                raise UserError("此里程碑没有可编辑的状态列。")
            current = cells[status_column].strip()
            if current != expected_status:
                raise UserError("里程碑状态已变化，请刷新后重试。")
            cell = cells[status_column]
            left = len(cell) - len(cell.lstrip())
            right = len(cell) - len(cell.rstrip())
            cells[status_column] = cell[:left] + new_status + (cell[len(cell) - right:] if right else "")
            parts[status_column + 1] = cells[status_column]
            lines[line_number - 1] = "|".join(parts)
            updated = ("\ufeff" if has_bom else "") + "".join(lines)
            self.write(path, updated, expected_text)
        context.log("已更新里程碑完成状态；仅修改对应表格单元格。")
        return {"project_id": identity, "line_number": line_number, "status": new_status}

    def create_milestones_document(self, identity: str, context: TaskContext) -> dict:
        """Create an empty milestone source without inventing milestone records."""
        with self.lock("CreateMilestonesDocument"):
            item = self.project(identity)
            if field(self.read(self.path(item["path"] + "/agent/STATUS.md")), "status") == "archived":
                raise UserError("已归档项目为只读，请先重新启用。")
            path = self.path(item["path"] + "/agent/MILESTONES.md")
            context.checkpoint()
            if not path.exists():
                self.write(path, default_template("MILESTONES.md").replace("{{NAME}}", item["name"]).replace("{{ID}}", identity).replace("{{DATE}}", today()))
        return {"project_id": identity, "path": str(path)}

    def notes(self, identity: str, context: TaskContext) -> dict:
        item = self.project(identity)
        folder = self.path(item["path"] + "/笔记")
        rows = []
        if folder.exists():
            for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
                context.checkpoint()
                if path.suffix.lower() == ".md":
                    self.path(path.relative_to(self.root).as_posix())
                    if path.is_file():
                        rows.append({"name": path.stem, "relative": "笔记/" + path.name})
        return {"notes": rows}

    def ensure_notes_directory(self, identity: str, context: TaskContext) -> dict:
        """Create the standard project note folder when needed and return its path."""
        with self.lock("OpenProjectNotesDirectory"):
            item = self.project(identity)
            folder = self.path(item["path"] + "/笔记")
            context.checkpoint()
            folder.mkdir(exist_ok=True)
        return {"project_id": identity, "path": str(folder)}

    def note_document(self, identity: str, relative: str) -> dict:
        if not relative.startswith("笔记/") or len(Path(relative).parts) != 2 or not relative.lower().endswith(".md"):
            raise UserError("请选择项目笔记目录中的 Markdown 文件。")
        item = self.project(identity)
        path = self.path(item["path"] + "/" + relative)
        return {"text": self.read(path), "path": str(path)}

    def create_note(self, identity: str, title: str, context: TaskContext) -> dict:
        title = title.strip()
        if not title or len(title) > 80 or title in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', title) or title.endswith((".", " ")) or title.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1,10)), *("LPT" + str(i) for i in range(1,10))}:
            raise UserError("请填写有效笔记名称，不包含路径或文件名保留字符。")
        with self.lock("NewNote"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("归档项目先重新启用，再新建笔记。")
            folder = self.path(item["path"] + "/笔记")
            path = self.path(item["path"] + "/笔记/" + title + ".md")
            if folder.exists() and any(p.name.casefold() == path.name.casefold() for p in folder.iterdir()):
                raise UserError("已有同名笔记，请在 Obsidian 中打开已有文件。")
            handoff = self.path(item["path"] + "/agent/HANDOFF.md")
            before = self.read(handoff)
            context.checkpoint()
            folder.mkdir(exist_ok=True)
            text = f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\ncreated: {today()}\nupdated: {today()}\nstatus: draft\ntype: project-note\nproject_id: {identity}\ntags: [project, note]\n---\n\n# {title}\n\n[项目入口](../README.md) · [当前状态](../agent/STATUS.md) · [任务](../agent/TASKS.md) · [日志与交接](../agent/HANDOFF.md)\n\n"
            self.write(path, text)
            try:
                # No note content is duplicated into the handoff or manager database.
                self.write(handoff, before.rstrip() + f"\n\n## {today()} — 创建项目笔记\n- 文件：[笔记](../笔记/{quote(path.name, safe='')})。正文在原文件维护。\n", before)
            except (OSError, UserError) as exc:
                raise UserError("笔记已建立，但交接追加未完成。请保留原文件并刷新，不重复创建：" + str(path)) from exc
        return {"project_id": identity, "relative": "笔记/" + path.name, "path": str(path)}

    def save_note(self, identity: str, relative: str, text: str, original: str, context: TaskContext) -> dict:
        if len(text.encode("utf-8")) > 1_000_000:
            raise UserError("笔记超过 1 MB，请使用外部编辑器。")
        with self.lock("EditNote"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("请先重新启用归档项目。")
            report = self.note_document(identity, relative)
            context.checkpoint()
            self.write(Path(report["path"]), text, original)
        return {"project_id": identity, "relative": relative, "saved": True}

    def refresh_indexes(self, context: TaskContext) -> dict:
        with self.lock("Index"):
            self.update_indexes(self.registry())
        context.log("已按登记表和 STATUS 刷新索引，保留人工说明。")
        return {"updated": True}

    def project(self, identity: str) -> dict:
        item = next((p for p in self.registry()["projects"] if p["id"] == identity), None)
        if item is None:
            raise UserError("项目登记已变化，请刷新列表。")
        return item

    def set_description(self, identity: str, description: str, context: TaskContext) -> dict:
        """Save the user's project introduction in projects.json, separate from STATUS."""
        if not isinstance(description, str) or len(description) > 4000:
            raise UserError("项目介绍不能超过 4000 个字符。")
        context.checkpoint()
        with self.lock("EditProjectDescription"):
            observed = self.read(self.registry_path)
            data = self.registry()
            item = next((project for project in data["projects"] if project["id"] == identity), None)
            if item is None:
                raise UserError("项目登记已变化，请刷新列表。")
            item["description"] = description.strip()
            self.save_registry(data, observed)
        context.log("已更新项目介绍；项目状态文档未修改。")
        return {"project_id": identity, "description": item["description"]}

    def set_project_info(self, identity: str, info: dict, context: TaskContext, description: str | None = None) -> dict:
        """Save overview fields and, when supplied, the introduction in one registry update."""
        if not isinstance(info, dict):
            raise UserError("项目信息格式无效。")
        if description is not None and (not isinstance(description, str) or len(description) > 4000):
            raise UserError("项目介绍不能超过 4000 个字符。")
        allowed = {"owner", "members", "start_date", "end_date", "priority", "repository", "tags", "cover_image"}
        if set(info) - allowed:
            raise UserError("项目信息包含不支持的字段。")
        context.checkpoint()
        with self.lock("EditProjectInfo"):
            observed = self.read(self.registry_path)
            data = self.registry()
            item = next((project for project in data["projects"] if project["id"] == identity), None)
            if item is None:
                raise UserError("项目登记已变化，请刷新列表。")
            if field(self.read(self.path(item["path"] + "/agent/STATUS.md")), "status") == "archived":
                raise UserError("已归档项目为只读，请先重新启用。")
            members = info.get("members", [])
            if not isinstance(members, list) or len(members) > 20 or any(not isinstance(name, str) or len(name) > 100 for name in members):
                raise UserError("项目成员信息无效。")
            previous_info = item.get("project_info", {})
            if not isinstance(previous_info, dict):
                raise UserError("已有项目信息格式无效，请先检查登记表。")
            tags = info.get("tags", previous_info.get("tags", []))
            if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 100 for tag in tags):
                raise UserError("项目标签信息无效。")
            for key in ("owner", "start_date", "end_date", "priority", "repository", "cover_image"):
                if not isinstance(info.get(key, ""), str) or len(info.get(key, "")) > 500:
                    raise UserError("项目信息字段无效或过长。")
            cover_image = info.get("cover_image", previous_info.get("cover_image", ""))
            if cover_image and (Path(cover_image).suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}):
                raise UserError("项目封面请使用 PNG、JPG、WEBP 或 BMP 图片。")
            if info.get("priority", "") not in {"", "high", "medium", "low"}:
                raise UserError("项目优先级无效。")
            parsed_dates = []
            for key in ("start_date", "end_date"):
                value = info.get(key, "")
                if value:
                    try:
                        parsed_dates.append(datetime.strptime(value, "%Y-%m-%d").date())
                    except ValueError as exc:
                        raise UserError("项目日期请使用 YYYY-MM-DD 格式。") from exc
                else:
                    parsed_dates.append(None)
            if parsed_dates[0] and parsed_dates[1] and parsed_dates[0] > parsed_dates[1]:
                raise UserError("项目开始日期不能晚于结束日期。")
            item["project_info"] = {**previous_info, **info}
            if description is not None:
                item["description"] = description.strip()
            self.save_registry(data, observed)
        context.log("已更新项目介绍和概览信息；项目 Markdown 文档未修改。")
        return {"project_id": identity, "project_info": item["project_info"], "description": item.get("description", "")}

    def _link(self, entry: str, target: Path):
        path = self.path(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or linked(path):
            raise UserError("Obsidian 入口已经存在，停止创建。")
        if os.name == "nt":
            # Arguments travel as data; no interpolated shell command or cmd deletion.
            script = "New-Item -ItemType Junction -Path $env:AM_PROJECT_LINK -Target $env:AM_PROJECT_TARGET -ErrorAction Stop | Out-Null"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, "AM_PROJECT_LINK": str(path), "AM_PROJECT_TARGET": str(target)}, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise UserError("未能建立 Obsidian 联接；请检查工作区和入口权限，保留创建现场。")
        else:
            path.symlink_to(target, target_is_directory=True)

    @staticmethod
    def _document_subdirs(item: dict) -> tuple[str, ...]:
        subdirs = item.get("vault_subdirs")
        if (not isinstance(subdirs, list) or not subdirs
                or any(not isinstance(name, str) or not name or name.startswith(".")
                       or name.endswith((".", " ")) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                       for name in subdirs)
                or len({name.casefold() for name in subdirs}) != len(subdirs)):
            raise UserError("项目文档子目录登记无效。")
        return tuple(subdirs)

    def _document_entry_paths(self, item: dict) -> tuple[Path, Path]:
        visible = self.path(item["vault_entry"])
        hidden_relative = Path(item["vault_entry"]).with_name("." + visible.name + ".archived")
        return visible, self.path(hidden_relative.as_posix())

    def _validate_document_entry(self, path: Path, item: dict, project_directory: Path) -> None:
        if linked(path) or not path.is_dir():
            raise UserError("Obsidian 文档入口不是普通目录：\n" + str(path))
        subdirs = self._document_subdirs(item)
        for name in subdirs:
            source = self.path(item["path"] + "/" + name)
            child = path / name
            if (not source.is_dir() or not linked(child) or not child.is_dir()
                    or child.resolve() != (project_directory / name).resolve()):
                raise UserError("Obsidian 文档子联接缺失或指向错误：\n" + str(child))
        for child in path.iterdir():
            if child.name in subdirs:
                continue
            if linked(child) or not child.is_file():
                raise UserError("文档入口含未登记目录或联接，已停止操作：\n" + str(child))
        if not (path / "README.md").is_file():
            raise UserError("Obsidian 文档入口缺少项目说明。")

    def _document_entry_readme(self, item: dict) -> str:
        name = item["name"]
        lines = ["---", "title: " + json.dumps(name + " 项目入口", ensure_ascii=False),
                 "created: " + today(), "type: project-navigation", "project_id: " + item["id"],
                 "tags: [project, navigation]", "---", "", "# " + name, "",
                 "- [当前状态](agent/STATUS.md)", "- [协作任务清单](agent/TASKS.md)",
                 "- [里程碑](agent/MILESTONES.md)", "",
                 "展开左侧的笔记、文档和 agent 目录即可编辑项目原文件，保存直接更新同一份资料。", "",
                 "[打开完整项目文件夹](" + (self.root / item["path"]).as_uri() + "/)", ""]
        return "\n".join(lines)

    def _create_document_entry(self, item: dict, project_directory: Path) -> None:
        visible, hidden = self._document_entry_paths(item)
        if visible.exists() or hidden.exists():
            raise UserError("Obsidian 文档入口已经存在，停止创建。")
        stage_relative = Path(item["vault_entry"]).with_name("." + visible.name + ".create-" + uuid4().hex)
        stage = self.path(stage_relative.as_posix())
        stage.mkdir()
        # Failed initialization stays hidden for inspection, never as a whole-project link.
        for name in self._document_subdirs(item):
            source = self.path(item["path"] + "/" + name)
            source.mkdir(exist_ok=True)
            self._link((stage_relative / name).as_posix(), source)
        self.write(stage / "README.md", self._document_entry_readme(item))
        self._validate_document_entry(stage, item, project_directory)
        os.replace(stage, visible)

    def _remove_document_entry(self, path: Path, item: dict) -> None:
        # This is a prevalidated, staged Vault entry, never the source project.
        if linked(path) or path.parent != self.path(item["vault_entry"]).parent:
            raise UserError("文档入口清理路径需要检查。")
        children = list(path.iterdir())
        subdirs = self._document_subdirs(item)
        for child in children:
            if child.name in subdirs:
                if not linked(child):
                    raise UserError("文档子联接已变化，保留入口供检查。")
            elif linked(child) or not child.is_file():
                raise UserError("文档入口内容已变化，保留入口供检查。")
        for child in children:
            if child.name in subdirs and os.name == "nt":
                child.rmdir()  # Detach only the junction; never recurse into its target.
            else:
                child.unlink()
        path.rmdir()

    def _vault_entry_link_state(self, item: dict, project_directory: Path) -> bool:
        """Validate the registered Vault entry and report whether its link exists."""
        relative = item.get("vault_entry")
        if not relative:
            return False
        if item.get("vault_scope") == "documents":
            visible, hidden = self._document_entry_paths(item)
            if visible.exists() and hidden.exists():
                raise UserError("可见与归档文档入口同时存在，已停止操作。")
            for entry in (visible, hidden):
                if entry.exists():
                    self._validate_document_entry(entry, item, project_directory)
            return visible.exists()
        path = self.path(relative, allow_link=True)
        if not linked(path):
            if path.exists():
                raise UserError("登记的 Obsidian 入口不是目录联接；为保护现有内容已停止：\n" + str(path))
            return False
        if not path.is_dir():
            raise UserError("登记的 Obsidian 入口不是有效目录联接：\n" + str(path))
        try:
            actual = path.resolve(strict=True)
            expected = project_directory.resolve(strict=True)
        except OSError as exc:
            raise UserError("无法核对 Obsidian 入口指向：\n" + str(path)) from exc
        if actual != expected:
            raise UserError("Obsidian 入口未指向当前项目目录；为保护其他资料已停止：\n" + str(path))
        return True

    def _set_vault_entry_link(self, item: dict, project_directory: Path, present: bool) -> None:
        """Create or remove only the registered junction/symlink, never its target."""
        relative = item.get("vault_entry")
        if not relative:
            return
        if item.get("vault_scope") == "documents":
            visible, hidden = self._document_entry_paths(item)
            exists = self._vault_entry_link_state(item, project_directory)
            if present and not exists:
                if hidden.exists():
                    os.replace(hidden, visible)
                else:
                    self._create_document_entry(item, project_directory)
            elif not present and exists:
                os.replace(visible, hidden)
            return
        path = self.path(relative, allow_link=True)
        exists = self._vault_entry_link_state(item, project_directory)
        if present and not exists:
            self._link(relative, project_directory)
        elif not present and exists:
            if os.name == "nt":
                path.rmdir()  # Removes the junction entry without touching its target.
            else:
                path.unlink()

    def create(self, name: str, goal: str, use_entry: bool, context: TaskContext, folder_name: str | None = None,
               initialization: dict | None = None) -> dict:
        if not name.strip() or len(name) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")) or name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1,10)), *("LPT" + str(i) for i in range(1,10))}:
            raise UserError("请使用有效项目名称，不包含路径或文件名保留字符。")
        standardized_folder_name = folder_name is not None
        if standardized_folder_name:
            folder_name = re.sub(r"[^a-z0-9]+", "-", folder_name.strip().lower()).strip("-")
            if not folder_name or len(folder_name) > 60:
                raise UserError("请填写 1–60 个字符的英文目录名，只使用英文字母和数字。")
        if not goal.strip() or len(goal) > 2000:
            raise UserError("请填写项目目标（不超过 2000 个字符）。")
        selection = validate_selection(initialization, check_sources=True)
        skill_plans = prepare_skill_copies(selection["skills"], context)
        with self.lock("New"):
            observed = self.read(self.registry_path)
            data = self.registry()
            self.index_outputs(data)  # Validate managed sections before any creation.
            if any(p["name"].casefold() == name.casefold() or (standardized_folder_name and p.get("folder_name", p["name"]).casefold() == folder_name.casefold()) for p in data["projects"]):
                raise UserError("已有同名项目或英文目录名，请修改后再创建。")
            known = {p["path"] for p in data["projects"]}
            for area in ("projects",):
                for child in self.path(area).iterdir():
                    if child.is_dir() and child.relative_to(self.root).as_posix() not in known:
                        raise UserError("发现未登记项目目录，请先核对登记表，不自动重复初始化。")
            year = today()[:4]
            identifiers = list(data["issued_ids"])
            for area in ("projects", "myself/03-项目"):
                identifiers.extend(p.name for p in self.path(area).iterdir())
            numbers = [int(m.group(1)) for value in identifiers if (m := re.match("P-" + year + r"-(\d{3})(?:-|$)", value))]
            number = max(numbers, default=0) + 1
            if number > 999:
                raise UserError("本年度项目编号已满。")
            identity = f"P-{year}-{number:03d}"
            folder = identity + "-" + (folder_name if standardized_folder_name else name)
            relative, entry = "projects/" + folder, "myself/03-项目/" + folder if use_entry else None
            for candidate in (relative, "myself/03-项目/" + folder):
                if self.path(candidate).exists():
                    raise UserError("项目或入口目标已存在。")
            target = self.path(relative)
            initialization_rules, extra_files = initialization_files(
                selection, target, {"NAME": name, "ID": identity, "GOAL": goal, "DATE": today()})
            data["issued_ids"].append(identity)
            self.save_registry(data, observed)  # Reserve forever, even if following creation fails.
            observed = self.read(self.registry_path)
            target = self.path(relative)
            target.mkdir()
            for filename in ("README.md", "STATUS.md", "TASKS.md", "HANDOFF.md", "MILESTONES.md", "AGENTS.md", "PROMPTS.md"):
                existing = self.path("agent/templates/" + filename)
                text = self.read(existing) if existing.is_file() else default_template(filename)
                if filename == "AGENTS.md" and not any(heading in text for heading in (
                        "## AI 任务接续与动态汇报", "## 项目协作约定")):
                    text = text.rstrip() + "\n\n" + AGENT_WORKFLOW
                if filename == "README.md" and "PROMPTS.md" not in text:
                    text = text.rstrip() + "\n\nAI 首次初始化或接手时使用 [AI 提示词](PROMPTS.md)；每次实质任务按项目规则汇报并更新状态、任务与交接。\n"
                for key, value in {"NAME": name, "DATE": today(), "ID": identity, "STATE": "active", "GOAL": goal, "PATH": str(target), "VAULT": str(self.root / entry) if entry else "无（只保留索引）", "WORKSPACE": str(self.root)}.items():
                    text = text.replace("{{" + key + "}}", value)
                if not entry and filename in {"AGENTS.md", "HANDOFF.md", "MILESTONES.md"}:
                    text += "\n本次入口策略：仅索引；未建立 Obsidian 联接。\n"
                if filename == "AGENTS.md":
                    text += initialization_rules
                if filename == "HANDOFF.md":
                    text += ("\n- 初始化模板：" + selection["template"]["name"]
                             + f"；Skills {len(selection['skills'])} 项复制到项目，知识资源 {len(selection['knowledge'])} 项保留引用。"
                             + "选择见 `.selfManger/config.json`；Agent 加载效果尚未验证。\n")
                self.write(target / (filename if filename == "AGENTS.md" else "agent/" + filename), text)
            for relative_file, content in extra_files.items():
                context.checkpoint()
                self.write(target / relative_file, content)
            copy_project_skills(skill_plans, self, target, context)
            self.write(target / "README.md", f"# {name}\n\n编号：{identity}\n\n[接续入口](agent/README.md) · [状态](agent/STATUS.md) · [任务](agent/TASKS.md) · [日志与交接](agent/HANDOFF.md)\n")
            item = {"id": identity, "name": name, "path": relative, "vault_entry": entry, "legacy": False, "link_policy": ("junction" if os.name == "nt" else "symlink") if entry else "plain-text"}
            if entry:
                item.update(vault_scope="documents", vault_subdirs=list(DEFAULT_VAULT_SUBDIRS))
                self._create_document_entry(item, target)
            if standardized_folder_name:
                item["folder_name"] = folder_name
            data["projects"].append(item)
            self.save_registry(data, observed)
            self.update_indexes(data)
        context.log("已创建必要管理文件并更新索引；业务任务和备份尚未验证。")
        return {"project_id": identity, "directory": str(target), "state": "active"}

    def index_outputs(self, data: dict) -> list[tuple[Path, str, str | None]]:
        rows = {"projects": [], "archive": []}
        for item in data["projects"]:
            status = self.read(self.path(item["path"] + "/agent/STATUS.md"))
            state = field(status, "status")
            if state not in STATES or field(status, "project_id") != item["id"]:
                raise UserError("项目状态字段不合规，停止更新索引。")
            entry = "[[" + item["vault_entry"][len("myself/"):] + "/README]]" if item.get("vault_entry") else "无"
            if state == "archived" and item.get("vault_entry"):
                entry = "已从 Vault 隐藏"
            context_link = "纯文本路径；不建 Vault 链接" if item["link_policy"] == "plain-text" else "[Agent 接续](" + (self.root / item["path"] / "agent/README.md").as_uri() + ")"
            rows["archive" if state == "archived" else "projects"].append(f"| {item['name']} | {item['id']} | {state} | {entry} | `{self.root / item['path']}` | {context_link} |")
        outputs = []
        for area, title in (("projects", "项目总览"), ("archive", "归档项目索引")):
            path = self.path("myself/03-项目/" + title + ".md")
            block = "<!-- agent-index:start -->\n<!-- 由 Manage-Projects.ps1 -Action Index 生成；身份取 projects.json，状态取 STATUS.md -->\n| 项目 | 管理编号 | 生命周期 | Vault 入口 | 完整资料真源 | 接续 |\n|---|---|---|---|---|---|\n" + "\n".join(rows[area]) + "\n<!-- agent-index:end -->"
            before = self.read(path) if path.exists() else None
            if before is not None:
                pattern = r"<!-- agent-index:start -->.*?<!-- agent-index:end -->"
                if len(re.findall(pattern, before, re.S)) != 1:
                    raise UserError("索引管理区块缺失或重复，不覆盖人工内容。")
                after = re.sub(pattern, lambda _: block, before, flags=re.S)
            else:
                after = f"---\ntitle: {title}\ncreated: {today()}\nstatus: active\ntype: project-index\ntags: [project, index]\n---\n\n# {title}\n\n{block}\n"
            outputs.append((path, after, before))
        architecture = self.path("myself/99-系统/搭建计划.md")
        if architecture.exists():
            before = self.read(architecture)
            pattern = r"<!-- agent-architecture:start -->.*?<!-- agent-architecture:end -->"
            if len(re.findall(pattern, before, re.S)) != 1:
                raise UserError("搭建计划缺少唯一自动路径区块，不覆盖人工内容。")
            block = "<!-- agent-architecture:start -->\n<!-- 由登记表生成当前路径映射，不在此手工编辑 -->\n| 编号 | 完整资料路径（工作区相对路径） | Vault 入口 | 策略 |\n|---|---|---|---|\n" + "\n".join(f"| {p['id']} | `{p['path']}` | `{p.get('vault_entry') or '无'}` | {p['link_policy']} |" for p in data["projects"]) + "\n<!-- agent-architecture:end -->"
            outputs.append((architecture, re.sub(pattern, lambda _: block, before, flags=re.S), before))
        return outputs

    def lifecycle_index_outputs(self, identity: str, state: str) -> list[tuple[Path, str, str]]:
        """Move one known project row between indexes without reading other STATUS files."""
        if state not in STATES:
            raise UserError("项目生命周期状态无效，未更新索引。")
        pattern = r"<!-- agent-index:start -->.*?<!-- agent-index:end -->"
        titles = ("项目总览", "归档项目索引")
        blocks: dict[str, tuple[Path, str, str, list[str]]] = {}
        locations: list[str] = []
        row_cells: list[str] = []
        for title in titles:
            path = self.path("myself/03-项目/" + title + ".md")
            if not path.is_file():
                raise UserError("管理索引文件缺失，未切换项目状态：\n" + str(path))
            before = self.read(path)
            matches = list(re.finditer(pattern, before, re.S))
            if len(matches) != 1:
                raise UserError("索引管理区块缺失或重复，未切换项目状态：\n" + str(path))
            match = matches[0]
            lines = match.group().splitlines()
            blocks[title] = (path, before, match.group(), lines)
            for line in lines:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 3 and cells[1] == identity:
                    locations.append(title)
                    row_cells = cells
        if len(locations) != 1 or len(row_cells) < 6:
            raise UserError(
                "项目索引中应有且仅有一条完整项目记录，实际找到 " + str(len(locations)) + " 条；未切换项目状态。"
            )

        destination = "归档项目索引" if state == "archived" else "项目总览"
        row_cells[2] = state
        item = self.project(identity)
        if state == "archived":
            row_cells[3] = "已从 Vault 隐藏" if item.get("vault_entry") else "无"
        else:
            row_cells[3] = ("[[" + item["vault_entry"][len("myself/"):] + "/README]]"
                            if item.get("vault_entry") else "无")
        updated_row = "| " + " | ".join(row_cells) + " |"
        source = locations[0]
        output = []
        for title in titles:
            path, before, old_block, lines = blocks[title]
            if title == source:
                lines = [line for line in lines if not (
                    len([cell.strip() for cell in line.strip().strip("|").split("|")]) >= 3
                    and [cell.strip() for cell in line.strip().strip("|").split("|")][1] == identity
                )]
                if source == destination:
                    lines.insert(-1, updated_row)
            if title == destination and source != destination:
                lines.insert(-1, updated_row)
            new_block = "\n".join(lines)
            after = before.replace(old_block, new_block, 1)
            output.append((path, after, before))
        return output

    def delete_index_outputs(self, after_data: dict, identity: str) -> list[tuple[Path, str, str]]:
        """Remove only the deleted project's rows, preserving other index entries."""
        outputs: list[tuple[Path, str, str]] = []
        removed_rows = 0
        pattern = r"<!-- agent-index:start -->.*?<!-- agent-index:end -->"
        for title in ("项目总览", "归档项目索引"):
            path = self.path("myself/03-项目/" + title + ".md")
            if not path.is_file():
                raise UserError("管理索引文件缺失，未删除项目：\n" + str(path))
            before = self.read(path)
            matches = list(re.finditer(pattern, before, re.S))
            if len(matches) != 1:
                raise UserError("索引管理区块缺失或重复，不删除项目：\n" + str(path))
            match = matches[0]
            lines = match.group().splitlines()
            kept = []
            for line in lines:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 3 and cells[1] == identity:
                    removed_rows += 1
                    continue
                kept.append(line)
            block = "\n".join(kept)
            after = before[:match.start()] + block + before[match.end():]
            outputs.append((path, after, before))
        if removed_rows != 1:
            raise UserError(
                "项目索引中应有且仅有一条目标项目记录，实际找到 " + str(removed_rows) + " 条；未删除项目。"
            )

        architecture = self.path("myself/99-系统/搭建计划.md")
        if architecture.exists():
            before = self.read(architecture)
            architecture_pattern = r"<!-- agent-architecture:start -->.*?<!-- agent-architecture:end -->"
            matches = list(re.finditer(architecture_pattern, before, re.S))
            if len(matches) != 1:
                raise UserError("搭建计划缺少唯一自动路径区块，不删除项目：\n" + str(architecture))
            block = (
                "<!-- agent-architecture:start -->\n"
                "<!-- 由登记表生成当前路径映射，不在此手工编辑 -->\n"
                "| 编号 | 完整资料路径（工作区相对路径） | Vault 入口 | 策略 |\n"
                "|---|---|---|---|\n"
                + "\n".join(
                    f"| {project['id']} | `{project['path']}` | `{project.get('vault_entry') or '无'}` | {project['link_policy']} |"
                    for project in after_data["projects"]
                )
                + "\n<!-- agent-architecture:end -->"
            )
            match = matches[0]
            after = before[:match.start()] + block + before[match.end():]
            outputs.append((architecture, after, before))
        return outputs

    def update_indexes(self, data: dict):
        for path, after, before in self.index_outputs(data):
            self.write(path, after, before)

    def append_log(self, identity: str, note: str, context: TaskContext) -> dict:
        if not note.strip() or len(note) > 20_000:
            raise UserError("请填写日志内容（不超过 20000 个字符）。")
        with self.lock("Log"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("归档项目先重新启用；阅读历史不会修改归档。")
            path = self.path(item["path"] + "/agent/HANDOFF.md")
            before = self.read(path)
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            self.write(path, before.rstrip() + f"\n\n## {stamp} — 用户日志\n\n{note.strip()}\n", before)
        return {"project_id": identity, "saved": True, "path": str(path)}

    def save_document(self, identity: str, relative: str, text: str, original: str, context: TaskContext) -> dict:
        if relative not in {"agent/STATUS.md", "agent/TASKS.md"} or len(text) > 100_000:
            raise UserError("只允许编辑当前状态和任务入口，内容不超过 100000 字符。")
        with self.lock("Edit"):
            item = self.project(identity)
            if lifecycle_state(self.read(self.path(item["path"] + "/agent/STATUS.md"))) == "archived":
                raise UserError("请先重新启用归档项目。")
            if field(text, "project_id") != identity:
                raise UserError("请保留文档中的 project_id。")
            if relative == "agent/STATUS.md" and field(text, "status") not in set(STATES) - {"archived"}:
                raise UserError("归档请使用归档按钮；其他状态为 candidate/active/paused/completed。")
            self.index_outputs(self.registry())
            self.write(self.path(item["path"] + "/" + relative), text, original)
            self.update_indexes(self.registry())
        return {"project_id": identity, "saved": True}

    def manifest(self, source: Path, context: TaskContext) -> dict:
        records = {}
        for directory, subdirs, files in os.walk(source, followlinks=False):
            context.checkpoint()
            for name in subdirs + files:
                path = Path(directory) / name
                if linked(path):
                    raise UserError("项目含联接/符号链接，请先核对移动后的引用：" + str(path.relative_to(source)))
            records[Path(directory).relative_to(source).as_posix() + "/"] = "directory"
            for name in files:
                path = Path(directory) / name
                if not path.is_file():
                    raise UserError("项目含非常规文件，不能自动归档。")
                digest = hashlib.sha256()
                before = path.stat()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        context.checkpoint(); digest.update(chunk)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise UserError("项目仍在写入，请停止相关任务后重试。")
                records[path.relative_to(source).as_posix()] = digest.hexdigest()
        return records

    def plan_move(self, identity: str, resume: bool, context: TaskContext) -> dict:
        """Physical project relocation is intentionally not part of lifecycle actions."""
        raise UserError("项目归档只切换 STATUS 状态，不支持移动目录。")

    def move(self, plan: dict, reason: str, context: TaskContext) -> dict:
        """Keep legacy callers from moving a project directory."""
        raise UserError("项目归档只切换 STATUS 状态，不支持移动目录。")
def default_template(filename: str) -> str:
    if filename == "PROMPTS.md":
        return prompt_document()
    if filename == "AGENTS.md":
        return "# {{ID}} — {{NAME}} 项目规则\n\n- 当前完整目录：`{{PATH}}`。\n- Obsidian 入口：`{{VAULT}}`。\n- 先读 agent/README → STATUS → TASKS → HANDOFF 最近两条。\n- 项目之间隔离；保留原文与用户修改，不写凭据，不自动提交发布。\n- 状态以 STATUS 为准，任务以 TASKS 为准，日志追加 HANDOFF。\n"
    kinds = {"README.md": "project-agent-index", "STATUS.md": "project-status", "TASKS.md": "project-tasks", "HANDOFF.md": "project-handoff", "MILESTONES.md": "project-milestones"}
    front = "---\ntitle: \"{{NAME}} " + filename + "\"\ncreated: {{DATE}}\nupdated: {{DATE}}\nstatus: " + ("{{STATE}}" if filename == "STATUS.md" else "active") + "\ntype: " + kinds[filename] + "\nproject_id: {{ID}}\ntags: [project, agent]\n---\n\n"
    contents = {
        "README.md": "# {{NAME}} 接续入口\n\n先读 [项目规则](../AGENTS.md) → [当前状态](STATUS.md) → [协作任务](TASKS.md) → [日志与交接](HANDOFF.md)。\n只维护一份状态和协作任务真源；业务目录按需建立。应用内项目任务由 selfManger 的 SQLite 管理，旧 TASKS-AI/TASKS-MANUAL 仅作历史资料，不再同步。\n\n阶段成果与验收写入 [MILESTONES.md](MILESTONES.md)；用户要求初始化或规划时，AI 应编写里程碑草案，保留用户已有内容和完成状态。\n",
        "STATUS.md": "# {{NAME}} 当前状态\n\n## 目标与验收\n- 目标：{{GOAL}}\n- 验收条件：待补。\n\n## 当前进度\n- 生命周期：{{STATE}}\n- 当前阶段：待明确。\n- 下一步：明确验收条件并选择第一项任务。\n- 阻塞：待补。\n- 任务入口：[TASKS.md](TASKS.md)。\n\n## 备份与恢复\n- 备份与恢复验证：未验证。\n",
        "TASKS.md": "# {{NAME}} 协作任务清单\n\n记录 Agent 的开发任务、检查结论和交接事项，按状态维护并保留历史。它不再作为 selfManger 应用的项目任务数据来源。\n\n应用内项目任务保存在 SQLite，由 selfManger 的项目“任务”页创建、编辑、关联和删除；修改本文件不会改变应用任务，应用也不会反向写入本文件。历史 TASKS-AI.md/TASKS-MANUAL.md 保留为原始资料，可在应用中执行一次性迁入。\n\n阶段成果与可验证验收条件写入 [项目里程碑](MILESTONES.md)。\n",
        "HANDOFF.md": "# {{NAME}} 日志与交接\n\n按时间追加，保留历史。记录实际改动、验证、未完成和下一步。\n\n## {{DATE}} — 初始化\n- 目标：{{GOAL}}\n- 已做：建立管理结构。业务、备份与运行环境未验证。\n- 下一步：明确验收条件。\n",
        "MILESTONES.md": "# {{NAME}} 项目里程碑\n\n记录阶段成果与可验证的验收条件。应用任务在 selfManger 项目页管理；Agent 协作事项记录在 TASKS.md。用户要求初始化或制定计划时，AI 应在此编写草案；已有内容和用户完成状态须保留，不擅自标记完成。新规划状态填待开始，未知日期填待定，待确认说明写在表外。应用从此文档读取里程碑。\n\n| 状态 | 里程碑 | 目标日期 |\n|---|---|---|\n",
    }
    return front + contents[filename]
