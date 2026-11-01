"""Discover and safely assign Skills in Agent-native directories.

Directory mappings and the SKILL.md marker follow the Skills Manager project
(MIT, copyright 2026 Tianliang Zhang); this module is a PySide application port.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .domain import Resource, UserError


@dataclass(frozen=True)
class SkillEntry:
    name: str
    path: Path
    managed: bool = False


ENGINE_DIRECTORIES = {
    "codex": ".codex/skills",
    "claude code": ".claude/skills",
    "claude": ".claude/skills",
    "codebuddy": ".codebuddy/skills",
    "workbuddy": ".workbuddy/skills",
}


def skills_root(resource: Resource) -> Path:
    override = str(resource.options.get("skills_dir_override", "")).strip()
    if override:
        return Path(override).expanduser()
    engine = str(resource.options.get("engine", resource.name)).casefold()
    relative = ENGINE_DIRECTORIES.get(engine)
    if not relative:
        raise UserError("此 Agent 尚无内置 Skills 目录映射，请先在配置中登记 skills_dir_override。")
    return Path.home() / relative


def _contains_skill(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file()


def discover_skills(resource: Resource) -> list[SkillEntry]:
    root = skills_root(resource)
    if not root.is_dir():
        return []
    entries: dict[str, SkillEntry] = {}
    try:
        candidates = root.iterdir()
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            if _contains_skill(candidate):
                entries[str(candidate.resolve()).casefold()] = SkillEntry(candidate.name, candidate)
    except OSError as exc:
        raise UserError(f"读取 Skills 目录失败：{exc}") from exc
    return sorted(entries.values(), key=lambda item: item.name.casefold())


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise UserError("Skill 含有符号链接，为避免越界复制已停止。")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def install_skill(source: Path, destination_root: Path) -> tuple[Path, str]:
    source = source.expanduser()
    if source.is_symlink() or not _contains_skill(source):
        raise UserError("所选目录不是有效 Skill（缺少 SKILL.md）。")
    source = source.resolve(strict=True)
    destination_root = destination_root.expanduser()
    destination = destination_root / source.name
    if destination.exists() or destination.is_symlink():
        raise UserError(f"目标位置已存在同名 Skill：{destination}")
    fingerprint = _tree_hash(source)
    destination_root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, destination, symlinks=False)
        if _tree_hash(destination) != fingerprint:
            shutil.rmtree(destination)
            raise UserError("复制后的 Skill 校验失败，已清理本次副本。")
    except Exception:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination, fingerprint


def remove_managed_skill(target: Path, expected_hash: str) -> None:
    if target.is_symlink() or not target.is_dir():
        raise UserError("目标已不是管家创建的普通 Skill 副本，未作改动。")
    if _tree_hash(target) != expected_hash:
        raise UserError("Skill 内容已变化。为保护用户修改，拒绝自动移除；请手动检查目标目录。")
    shutil.rmtree(target)
