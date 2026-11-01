"""Backup evidence and conservative schedules shared by the dashboard and UI."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import archives
from .domain import Resource
from .storage import Store

DEFAULT_BACKUP_KEEP = 10
MIN_BACKUP_KEEP = 1
MAX_BACKUP_KEEP = 100


def backup_retention_count(store: Store) -> int:
    """Return a safe retention count for persisted user settings."""
    try:
        count = int(store.setting("backup_keep", DEFAULT_BACKUP_KEEP))
    except (TypeError, ValueError, OverflowError):
        count = DEFAULT_BACKUP_KEEP
    return max(MIN_BACKUP_KEEP, min(MAX_BACKUP_KEEP, count))


def age_hours(value: str | None, current: datetime | None = None) -> float | None:
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, ((current or datetime.now(timezone.utc)) - stamp).total_seconds() / 3600)
    except (TypeError, ValueError):
        return None


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"file_size": stat.st_size, "modified_ns": stat.st_mtime_ns}


def backup_health(store: Store, resource: Resource, backup_root: Path,
                  candidates: list[dict] | None = None) -> dict:
    if resource.kind in {"project", "folder", "script", "vault"}:
        candidates = candidates if candidates is not None else archives.list_archives(backup_root, resource.id)
        if not candidates:
            return {"state": "未备份", "source": "", "created_at": None, "verified": False, "rehearsed": False}
        newest = candidates[0]
        path = Path(newest["archive"])
        if "integrity" in newest:
            verified = newest["integrity"] in {"已校验", "已演练"}
            rehearsed = newest["integrity"] == "已演练"
        else:
            evidence = store.evidence(str(path.resolve()))
            try:
                matches = all(evidence.get(key) == value for key, value in fingerprint(path).items())
            except OSError:
                return {"state": "备份已移走", "source": "", "created_at": None, "verified": False, "rehearsed": False}
            verified = bool(matches and evidence.get("verified_at"))
            rehearsed = bool(matches and evidence.get("rehearsed_at"))
        age = age_hours(newest.get("created_at"))
        return {**newest, "source": str(path), "verified": verified, "rehearsed": rehearsed,
                "state": "已过期" if age is not None and age > 48 else "已演练" if rehearsed else "已校验" if verified else "待校验"}
    return {"state": "不支持", "source": "", "created_at": None, "verified": False, "rehearsed": False}


def is_due(store: Store, resource: Resource, current: datetime | None = None) -> bool:
    if resource.kind not in {"project", "folder", "script", "vault"} or not resource.options.get("automatic_backup"):
        return False
    policy = store.evidence("schedule:" + resource.id)
    age = age_hours(policy.get("last_attempt_at"), current)
    hours = max(1, min(168, int(resource.options.get("backup_interval_hours", 24))))
    return age is None or age >= hours


def retention_candidates(root: Path, resource_id: str, keep: int, protected: str) -> list[Path]:
    """Only archives created for this resource, inside this root, are eligible."""
    keep = max(1, min(100, keep))
    result = []
    for item in archives.list_archives(root, resource_id)[keep:]:
        source = Path(item["archive"])
        try:
            resolved = source.resolve()
            resolved_root = root.resolve()
            if not resolved.is_relative_to(resolved_root) or archives.is_link(source):
                continue
            is_version = source.name == "backup.amb" and bool(re.fullmatch(r"\d{8}-\d{6}_[a-f0-9]{8}", source.parent.name))
            is_legacy = source.parent.resolve() == resolved_root and bool(re.fullmatch(r"[a-f0-9]{32}\.amb", source.name))
            if (is_version or is_legacy) and resolved != Path(protected).resolve():
                result.append(source)
        except (OSError, ValueError):
            continue
    return result
