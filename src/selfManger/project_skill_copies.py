"""Bounded, exclusive copies of selected Skills into a newly created project."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from .domain import UserError


MAX_SKILL_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_SKILL_FILES = 5000


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def skill_destination(entry):
    name = Path(entry["path"]).name
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if not name or name in {".", ".."} or name.endswith((".", " ")) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.split(".")[0].casefold() in devices:
        raise UserError("Skill 目录名不能用于项目副本：" + entry["name"])
    return ".selfManger/skills/" + name


def skill_inventory(source, context):
    """Reject links before reading, retain empty dirs, and fingerprint every file."""
    source = Path(source)
    if _linked(source) or not source.is_dir():
        raise UserError("Skill 来源必须是普通目录，不能是链接：" + str(source))
    directories, records, total = [], {}, 0

    def failed(error):
        raise UserError("无法完整读取 Skill：" + str(source)) from error

    for directory, subdirs, files in os.walk(source, followlinks=False, onerror=failed):
        context.checkpoint()
        subdirs.sort()
        files.sort()
        folder = Path(directory)
        if _linked(folder):
            raise UserError("Skill 包含链接目录，已停止复制：" + str(folder))
        directories.append(folder.relative_to(source).as_posix())
        if len(directories) + len(records) > MAX_SKILL_FILES:
            raise UserError("单个 Skill 的文件和目录数量超过 5000。")
        for name in subdirs + files:
            item = folder / name
            if name in {".local-secrets", ".aws", ".ssh", ".git"}:
                raise UserError("Skill 包含非技能资料目录，请先整理来源：" + name)
            if _linked(item):
                raise UserError("Skill 包含符号链接或目录联接，已停止复制：" + str(item))
        for name in files:
            path = folder / name
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise UserError("Skill 含非常规文件，已停止复制。")
            total += before.st_size
            if total > MAX_SKILL_BYTES or len(directories) + len(records) >= MAX_SKILL_FILES:
                raise UserError("单个 Skill 超过 100 MB 或 5000 个文件/目录。")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    context.checkpoint()
                    digest.update(chunk)
            after = path.stat()
            if _linked(path) or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise UserError("Skill 在读取期间发生变化，请重试。")
            records[path.relative_to(source).as_posix()] = {"size": before.st_size, "sha256": digest.hexdigest(), "mode": before.st_mode}
    if "SKILL.md" not in records:
        raise UserError("Skill 缺少 SKILL.md。")
    return {"directories": directories, "files": records, "bytes": total}


def prepare_skill_copies(entries, context):
    plans, names, total = [], set(), 0
    for entry in entries:
        context.log("正在检查所选 Skill：" + entry["name"])
        destination = skill_destination(entry)
        if destination.casefold() in names:
            raise UserError("所选 Skills 有同名目录，请仅选择一个版本：" + Path(entry["path"]).name)
        names.add(destination.casefold())
        inventory = skill_inventory(entry["path"], context)
        total += inventory["bytes"]
        if total > MAX_TOTAL_BYTES:
            raise UserError("所选 Skills 总大小超过 250 MB，请减少选择。")
        plans.append({"source": entry["path"], "destination": destination, "inventory": inventory})
    return plans


def copy_project_skills(plans, workspace, project, context):
    """Leave partial creation evidence on failure; never delete or overwrite paths."""
    for plan in plans:
        source = Path(plan["source"])
        context.log("正在复制 Skill 到项目：" + source.name)
        expected = plan["inventory"]
        if skill_inventory(source, context) != expected:
            raise UserError("Skill 来源在创建前发生变化，请检查后重试。")
        destination = workspace.path((project / plan["destination"]).relative_to(workspace.root).as_posix())
        destination.mkdir(parents=True, exist_ok=False)
        for relative in expected["directories"]:
            target = workspace.path((destination / relative).relative_to(workspace.root).as_posix())
            target.mkdir(parents=True, exist_ok=True)
        for relative, record in expected["files"].items():
            context.checkpoint()
            path = source / relative
            for ancestor in (path, *path.parents):
                if _linked(ancestor):
                    raise UserError("Skill 来源变成链接，已停止复制。")
                if ancestor == source:
                    break
            target = workspace.path((destination / relative).relative_to(workspace.root).as_posix())
            digest, count = hashlib.sha256(), 0
            with path.open("rb") as original, target.open("xb") as output:
                while chunk := original.read(1024 * 1024):
                    context.checkpoint()
                    count += len(chunk)
                    if count > record["size"]:
                        raise UserError("Skill 来源在复制期间变更，已停止。")
                    output.write(chunk)
                    digest.update(chunk)
            if count != record["size"] or digest.hexdigest() != record["sha256"]:
                raise UserError("Skill 副本与来源不一致，创建未完成；保留现场供检查。")
            os.chmod(target, stat.S_IMODE(record["mode"]))
        if skill_inventory(source, context) != expected:
            raise UserError("Skill 来源在复制期间变更，创建未完成；保留现场供检查。")
