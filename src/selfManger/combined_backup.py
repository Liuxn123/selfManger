"""Capture the manager database and configured Markdown in one checked archive."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from . import __version__
from .archives import SKIP_DIRS as ARCHIVE_SKIP_DIRS, safe_name
from .domain import UserError
from .project_workspaces import ProjectWorkspace, linked
from .snapshots import snapshot_file
from .storage import now
from .workbench import work_root


SKIP_DIRS = ARCHIVE_SKIP_DIRS | {".obsidian", ".local-secrets", ".codex", ".agents", ".ssh", ".aws", ".azure", ".worktrees"}
MAX_FILES = 20000
MAX_BYTES = 20 * 1024**3


def _hash(path, context):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            context.checkpoint()
            digest.update(block)
    return digest.hexdigest()


def _sources(store):
    personal = work_root(store).absolute()
    if linked(personal) or any(linked(parent) for parent in personal.parents):
        raise UserError("个人 Markdown 目录不能经过联接，请选择真实目录。")
    if not personal.is_dir():
        raise UserError("个人 Markdown 目录不存在，请检查设置：" + str(personal))
    sources = [("personal", personal)]
    workspace_root = str(store.setting("project_workspace", "") or "").strip()
    if not workspace_root:
        raise UserError("尚未设置项目工作区，无法确认登记项目范围。")
    workspace = ProjectWorkspace(Path(workspace_root))
    registry_bytes = workspace.registry_path.read_bytes()
    registry = workspace.registry()
    if workspace.registry_path.read_bytes() != registry_bytes:
        raise UserError("项目登记在读取时改变，请重试。")
    for project in registry["projects"]:
        path = workspace.path(project["path"])
        if not path.is_dir():
            raise UserError("登记项目目录不存在，无法完成整体备份：" + str(path))
        sources.append(("projects/" + project["id"], path))
    return sources, workspace.registry_path, registry_bytes, workspace.root


def _scan(sources, context):
    result, folded, total = {}, set(), 0
    def failure(exc):
        raise UserError("无法遍历 Markdown 目录：" + str(exc)) from exc
    for prefix, root in sources:
        if linked(root) or any(linked(parent) for parent in root.parents):
            raise UserError("备份目录不能经过联接：" + str(root))
        for current, directories, files in os.walk(root, followlinks=False, onerror=failure):
            context.checkpoint()
            here = Path(current)
            for name in list(directories):
                item = here / name
                if name in SKIP_DIRS:
                    directories.remove(name)
                elif linked(item):
                    raise UserError("Markdown 范围含目录联接，请先处理后重试：" + str(item))
            for name in files:
                item = here / name
                if item.suffix.lower() not in {".md", ".markdown"}:
                    continue
                if linked(item) or not item.is_file():
                    raise UserError("Markdown 文件是联接或非普通文件：" + str(item))
                relative = safe_name(prefix + "/" + item.relative_to(root).as_posix())
                if relative.casefold() in folded:
                    raise UserError("备份路径大小写冲突：" + relative)
                folded.add(relative.casefold())
                size = item.stat().st_size
                total += size
                if len(result) >= MAX_FILES or total > MAX_BYTES:
                    raise UserError("Markdown 超过整体备份上限（2 万文件 / 20 GiB）。")
                result[relative] = (item, size, _hash(item, context))
    return result


def capture(store, staging: Path, context):
    """Freeze SQLite writes, snapshot it, and reject changed Markdown or registry."""
    guard = sqlite3.connect(store.path, timeout=15)
    try:
        guard.execute("BEGIN IMMEDIATE")
        for folder in (".markdown-recovery",):
            recovery = store.root / folder
            if linked(recovery):
                raise UserError("恢复记录目录是联接，请检查后重试：" + str(recovery))
            if recovery.exists() and any(recovery.iterdir()):
                raise UserError("存在待处理的文件恢复记录，请先恢复或核对后再备份：" + str(recovery))
        sources, registry_path, registry_bytes, workspace_root = _sources(store)
        before = _scan(sources, context)
        with snapshot_file(store.path, context) as (snapshot, _):
            shutil.copy2(snapshot, staging / "manager.sqlite3")
        records = []
        for relative, (path, size, digest) in before.items():
            context.checkpoint()
            target = staging / "markdown" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if target.stat().st_size != size or _hash(target, context) != digest:
                raise UserError("Markdown 在复制时发生变化，请暂停编辑后重试：" + str(path))
            records.append({"path": "markdown/" + relative, "sha256": digest, "size": size})
        after = _scan(sources, context)
        if ({key: value[1:] for key, value in before.items()} !=
                {key: value[1:] for key, value in after.items()} or registry_path.read_bytes() != registry_bytes):
            raise UserError("备份期间 Markdown 或项目登记发生变化，请暂停编辑后重试。")
        (staging / "projects.json").write_bytes(registry_bytes)
        for name in ("manager.sqlite3", "projects.json"):
            path = staging / name
            records.append({"path": name, "sha256": _hash(path, context), "size": path.stat().st_size})
        schema = guard.execute("PRAGMA user_version").fetchone()[0]
        manifest = {"version": 1, "created_at": now(), "application_version": __version__,
                    "database_schema": schema, "files": sorted(records, key=lambda row: row["path"]),
                    "source_personal_root": str(sources[0][1].resolve()),
                    "source_workspace_root": str(workspace_root),
                    "source_portable_root": str(store.portable_root) if store.portable_root else None,
                    "excluded": "非 Markdown 项目附件、知识库文件、凭据及指定系统/依赖目录；不跟随联接",
                    "consistency": "SQLite 写入锁与前后内容检查；外部编辑器需暂停"}
        (staging / "overall-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "恢复说明.txt").write_text(
            "本备份包含数据库（含设置、知识索引和应用自有工作笔记）、个人计划 Markdown、登记项目 Markdown 与登记快照。\n"
            "不包含非 Markdown 附件、知识库文件、系统凭据或指定系统/依赖目录。\n"
            "先恢复到新的空目录并核对清单，退出管家后再由用户决定如何迁移；不要覆盖运行中的数据库。\n"
            "数据库内保留原工作区、计划目录等路径；运行恢复副本前应在隔离环境中重新配置，避免连接原资料。\n"
            "请在安全位置单独保管口令；丢失口令将无法解密。\n", encoding="utf-8")
        return manifest
    except sqlite3.OperationalError as exc:
        raise UserError("无法锁定或读取数据库，请等待当前写入结束后重试。") from exc
    finally:
        guard.rollback()
        guard.close()


def verify_manifest(archive, outer):
    """Validate the dataset manifest against the already verified encrypted archive."""
    try:
        info = archive.getinfo("files/overall-manifest.json")
        if info.file_size > 10_000_000:
            raise ValueError("manifest too large")
        manifest = json.loads(archive.read(info))
        records = manifest["files"]
        if manifest.get("version") != 1 or not isinstance(records, list):
            raise ValueError("unsupported manifest")
        listed = {row["path"]: (row["sha256"], row["size"]) for row in records}
        if len(listed) != len(records) or not {"manager.sqlite3", "projects.json"} <= listed.keys():
            raise ValueError("duplicate or missing required file")
        outer_paths = {row["path"] for row in outer["files"]}
        if not {"overall-manifest.json", "恢复说明.txt"} <= outer_paths:
            raise ValueError("missing manifest or restore instructions")
        archived = {row["path"]: (row["sha256"], row["size"]) for row in outer["files"]
                    if row["path"] not in {"overall-manifest.json", "恢复说明.txt"}}
        if listed != archived:
            raise ValueError("dataset differs from manifest")
        if not isinstance(manifest.get("database_schema"), int) or not isinstance(manifest.get("application_version"), str):
            raise ValueError("missing version")
        for key in ("source_personal_root", "source_workspace_root"):
            if key in manifest and (not isinstance(manifest[key], str) or not Path(manifest[key]).is_absolute()):
                raise ValueError("invalid source root")
        portable_root = manifest.get("source_portable_root")
        if portable_root is not None and (not isinstance(portable_root, str) or not Path(portable_root).is_absolute()):
            raise ValueError("invalid portable root")
        with archive.open("files/manager.sqlite3") as database:
            header = database.read(100)
        if len(header) < 100 or header[:16] != b"SQLite format 3\x00" or int.from_bytes(header[60:64], "big") != manifest["database_schema"]:
            raise ValueError("database schema differs from manifest")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UserError("整体备份清单缺失、损坏或与文件不一致，拒绝恢复。") from exc
    return manifest
