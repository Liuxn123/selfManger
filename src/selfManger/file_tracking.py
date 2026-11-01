"""Data access and metadata-only snapshots for local file tracking."""
from __future__ import annotations

import os
from pathlib import Path

from .storage import Store


TRACKING_KEY = "local_file_tracking"
MAX_FILES_PER_ROOT = 30000
MAX_HISTORY = 300


def snapshot(root: str, cancel) -> tuple[dict[str, list[int]], list[str], bool]:
    """Read file metadata only. Never open or hash user file contents."""
    files: dict[str, list[int]] = {}
    directories: list[str] = []
    truncated = False
    base = Path(root)
    if not base.is_dir():
        return files, directories, truncated
    try:
        def inaccessible(_error: OSError) -> None:
            nonlocal truncated
            truncated = True

        for current, dirs, names in os.walk(base, followlinks=False, onerror=inaccessible):
            if cancel.is_set():
                break
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            directories.append(current)
            for name in names:
                path = Path(current) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    info = path.stat()
                    files[str(path)] = [int(info.st_size), int(info.st_mtime_ns)]
                except (OSError, PermissionError):
                    truncated = True
                    continue
                if len(files) >= MAX_FILES_PER_ROOT:
                    truncated = True
                    return files, directories, truncated
    except (OSError, PermissionError):
        return files, directories, True
    return files, directories, truncated


class FileTrackingService:
    """Persist tracking settings and compare metadata snapshots."""

    def __init__(self, store: Store):
        self.store = store

    def load(self) -> dict:
        value = self.store.setting(TRACKING_KEY, {}) or {}
        return value if isinstance(value, dict) else {}

    def save(self, roots: list[dict], events: list[dict], enabled: bool) -> None:
        self.store.set_setting(TRACKING_KEY, {
            "roots": roots, "events": events[-MAX_HISTORY:], "enabled": enabled,
        })

    @staticmethod
    def snapshot(root: str, cancel):
        return snapshot(root, cancel)

    @staticmethod
    def changes(previous: dict[str, list[int]], current: dict[str, list[int]]) -> list[tuple[str, str]]:
        """Return sorted path changes; callers must reject partial snapshots first."""
        old_paths, new_paths = set(previous), set(current)
        result = [("新增", path) for path in sorted(new_paths - old_paths)]
        result.extend(("删除", path) for path in sorted(old_paths - new_paths))
        result.extend(("修改", path) for path in sorted(old_paths & new_paths)
                      if previous[path] != current[path])
        return result
