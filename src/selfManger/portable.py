"""Portable paths follow the application folder, never the current directory."""
from __future__ import annotations

import sys
from pathlib import Path

PREFIX = "@portable/"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin":
            bundle = next((p for p in executable.parents if p.suffix == ".app"), None)
            if bundle:
                return bundle.parent
        return executable.parent
    return Path(__file__).resolve().parents[2]


def portable_directory() -> Path | None:
    root = application_directory()
    return root if (root / "portable.json").is_file() else None


def encode_paths(value, root: Path | None):
    if isinstance(value, dict):
        return {key: encode_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_paths(item, root) for item in value]
    if root and isinstance(value, str) and Path(value).is_absolute():
        try:
            return PREFIX + Path(value).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return value


def decode_paths(value, root: Path | None):
    if isinstance(value, dict):
        return {key: decode_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_paths(item, root) for item in value]
    if root and isinstance(value, str) and value.startswith(PREFIX):
        path = (root / value[len(PREFIX):]).resolve()
        if not path.is_relative_to(root.resolve()):
            from .domain import UserError
            raise UserError("便携路径越过了程序目录。")
        return str(path)
    return value
