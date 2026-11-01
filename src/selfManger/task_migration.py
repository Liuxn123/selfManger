"""One-way, idempotent import of legacy Markdown tasks into the shared SQLite store."""
from __future__ import annotations

import hashlib
import copy
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from .domain import UserError
from .storage import Store
from .workbench import LegacyDailyReader, task_details


def legacy_ref(scope: str, kind: str, text: str, occurrence: int = 0) -> str:
    digest = hashlib.sha256(" ".join(text.split()).casefold().encode("utf-8")).hexdigest()[:20]
    return f"legacy:{scope}:{kind}:{digest}:{occurrence}"


def _daily_names(root: Path) -> list[str]:
    directory = root / "每日"
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise UserError("每日目录无效，未导入旧任务。")
    names = []
    for path in directory.iterdir():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name):
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise UserError("每日目录包含链接文件，未导入旧任务：" + path.name)
        names.append(path.name)
    names.sort()
    if len(names) > 5000:
        raise UserError("每日目录超过 5000 个文件，未开始迁移；请先分批整理。")
    return names


def _daily_entries(root: Path, names: set[str] | None = None):
    directory = root / "每日"
    if not directory.exists():
        return
    if directory.is_symlink() or not directory.is_dir():
        raise UserError("每日目录无效，未导入旧任务。")
    candidates = [directory / name for name in _daily_names(root) if names is None or name in names]
    total_bytes = 0
    for path in candidates:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError as exc:
            raise UserError("每日文件名日期无效，未完成旧任务导入：" + path.name) from exc
        total_bytes += path.stat().st_size
        if total_bytes > 50_000_000:
            raise UserError("每日任务资料超过 50 MB，未完成导入。")
        report = LegacyDailyReader(root, day).load()
        for kind, rows in (("task", report["tasks"]), ("schedule", report["schedule"])):
            occurrences = defaultdict(int)
            for row in rows:
                identity = legacy_ref("daily:" + str(root.resolve()) + ":" + day.isoformat(), kind, row["text"], occurrences[row["text"]])
                occurrences[row["text"]] += 1
                title, priority, scheduled_time, status = row["text"], "", "", "done" if row["done"] else "planned"
                if kind == "task":
                    details = task_details(title)
                    title, priority, scheduled_time = details["title"], {"高": "high", "中": "medium", "低": "low"}.get(details["priority"], ""), details["time"]
                else:
                    time_match = re.match(r"^((?:[01]\d|2[0-3]):[0-5]\d(?:\s*[–-]\s*(?:[01]\d|2[0-3]):[0-5]\d)?)\s+", title)
                    if time_match:
                        scheduled_time = time_match[1].replace("-", "–")
                        title = title[time_match.end():]
                yield {"title": title, "kind": kind, "due_date": day.isoformat(),
                       "priority": priority, "scheduled_time": scheduled_time,
                       "status": status, "source": "manual"}, identity


def migrate_legacy_tasks(store: Store, daily_root: Path, context=None):
    """Import the existing personal daily Markdown corpus once into SQLite."""
    key = "legacy_task_import_v1"
    daily_key_v2 = "legacy_daily_task_import_v2"
    marker = store.setting(key, {})
    if not isinstance(marker, dict):
        marker = {}
    previous_marker = copy.deepcopy(marker)
    counts = {"daily": 0}
    imported_refs = store.planned_task_source_refs()

    def import_entries(entries, category):
        for values, source_ref in entries:
            if context:
                context.checkpoint()
            if source_ref in imported_refs:
                continue
            if store.add_planned_task(values, source_ref):
                counts[category] += 1
                imported_refs.add(source_ref)

    daily_key = str(daily_root.resolve())
    daily_state = store.setting(daily_key_v2, {})
    if not isinstance(daily_state, dict) or daily_state.get("root") != daily_key:
        daily_files = set(_daily_names(daily_root))
        import_entries(_daily_entries(daily_root, daily_files), "daily")
        marker["daily"] = daily_key
        marker["daily_files"] = sorted(daily_files)
        store.set_setting(daily_key_v2, {"root": daily_key, "file_count": len(daily_files)})
    if marker != previous_marker:
        store.set_setting(key, marker)
    return counts
