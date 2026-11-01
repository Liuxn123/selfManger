"""Readable, collision-safe names for documents written into the user's Vault.

Rule: ``<语义名称>-<短编号>`` — the readable part comes first so Obsidian's file
list can be understood at a glance, and a short stable id keeps names unique
without any 32/64 character hex soup. User content is never renamed by the
application after creation; only the generated name changes.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_NAME = 60
SHORT_ID = 8

_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_LEGACY_HEX = re.compile(r"^[0-9a-f]{16,64}$")


def readable_name(text: str, limit: int = MAX_NAME) -> str:
    """Keep Chinese and normal words, drop characters Windows forbids."""
    value = _INVALID.sub("_", str(text or ""))
    value = _SPACES.sub(" ", value).strip(" ._")
    value = value.replace("--", "-")
    if len(value) > limit:
        value = value[:limit].strip(" ._-")
    if value.casefold() in _RESERVED:
        value += "_"
    return value


def short_id(identity: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(identity or ""))[:SHORT_ID].lower()


def document_stem(title: str, identity: str, limit: int = MAX_NAME) -> str:
    """``标题-8位编号``; falls back to the id when the title is unusable."""
    suffix = short_id(identity)
    name = readable_name(title, limit)
    if not name:
        return suffix or "未命名"
    return f"{name}-{suffix}" if suffix else name


def is_legacy_name(stem: str) -> bool:
    """True for the old pure-hash file names that users could not read."""
    return bool(_LEGACY_HEX.match(str(stem or "")))


def matches_identity(stem: str, identity: str) -> bool:
    """Accept both the readable name and the historical hash-only name."""
    stem = str(stem or "")
    if stem == identity:
        return True
    suffix = short_id(identity)
    if not suffix:
        return False
    return bool(re.search(r"-" + re.escape(suffix) + r"(-\d+)?$", stem))


def temporary_path(path: Path, token: str) -> Path:
    """Hidden sibling used for atomic replaces.

    A leading dot keeps Obsidian's file list clean while the readable stem makes
    a leftover self-explanatory; the short token keeps concurrent writers apart.
    """
    stem = readable_name(path.stem, 40) or "document"
    return path.with_name("." + stem + "." + str(token)[:8] + ".tmp")
