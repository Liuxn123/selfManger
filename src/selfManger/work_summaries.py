"""One-time import and display helpers for legacy work-summary.md notes."""
from pathlib import Path

from .domain import UserError

MAX_SUMMARY_BYTES = 2 * 1024 * 1024
MAX_NOTE_CHARS = 20_000
LEGACY_WORK_SUMMARY_SOURCE = "legacy:work-summary.md:v1"


def read_legacy_work_summary(root: Path) -> str | None:
    """Read the old note file once without changing or following it."""
    path = Path(root) / "work-summary.md"
    if path.is_symlink():
        raise UserError("旧总结文件不能是链接。")
    if not path.exists():
        return None
    if not path.is_file():
        raise UserError("旧总结路径不是普通文件，请先检查文件。")
    before = path.stat()
    if before.st_size > MAX_SUMMARY_BYTES:
        raise UserError("旧总结文件超过 2 MiB，请先检查文件。")
    with path.open("rb") as source:
        data = source.read(MAX_SUMMARY_BYTES + 1)
    after = path.stat()
    if len(data) > MAX_SUMMARY_BYTES:
        raise UserError("旧总结文件超过 2 MiB，请先检查文件。")
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise UserError("旧总结文件正在变化，请稍后重试。")
    try:
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise UserError("旧总结文件不是有效的 UTF-8 文本，请先检查文件。") from exc


def render_work_summaries(rows: list[dict[str, str | None]]) -> str:
    """Render database rows while preserving the original imported Markdown."""
    sections = []
    for row in rows:
        body = str(row.get("body") or "").rstrip()
        if row.get("source_ref") == LEGACY_WORK_SUMMARY_SOURCE:
            section = body
        else:
            section = f"## {row['created_at']}\n\n{body}"
        if section:
            sections.append(section)
    return "\n\n".join(sections)
