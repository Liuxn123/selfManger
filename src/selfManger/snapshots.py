"""Read-only SQLite snapshots; ordinary files still require stable writers."""
from __future__ import annotations

import sqlite3
import json
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path

from .domain import UserError
from .runtime import TaskContext


def is_sqlite(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\0"
    except OSError as exc:
        raise UserError(f"无法读取需要备份的文件：{path}（{type(exc).__name__}）。请检查权限或退出原应用后重试。") from exc


def is_database_companion(path: Path) -> bool:
    for suffix in ("-wal", "-shm", "-journal"):
        if path.name.endswith(suffix):
            return is_sqlite(path.with_name(path.name[:-len(suffix)]))
    return False


@contextmanager
def snapshot_file(path: Path, context: TaskContext, *, append_log: bool = False):
    if append_log:
        # Codex transcripts are append-only JSONL. Capture a fixed byte boundary,
        # dropping the incomplete final event; later appends belong to the next backup.
        before = path.stat()
        with tempfile.TemporaryDirectory(prefix="selfManger-log-") as temporary:
            target = Path(temporary) / "transcript.jsonl"
            remaining, complete, position = before.st_size, 0, 0
            with path.open("rb") as reader, target.open("xb") as writer:
                target.chmod(0o600)
                while remaining:
                    context.checkpoint()
                    chunk = reader.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise UserError(f"会话文件被截断，请退出原应用后重试：{path}")
                    writer.write(chunk)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        complete = position + newline + 1
                    position += len(chunk)
                    remaining -= len(chunk)
                if complete < before.st_size and before.st_size - complete <= 16 * 1024 * 1024:
                    # Older/exported transcripts may end with a complete JSON event
                    # without a newline. Preserve it; drop only malformed partials.
                    reader.seek(complete)
                    tail = reader.read(before.st_size - complete)
                    try:
                        json.loads(tail)
                        complete = before.st_size
                    except (ValueError, UnicodeDecodeError):
                        pass
                writer.truncate(complete)
            after = path.stat()
            if after.st_ino != before.st_ino or after.st_size < before.st_size or (after.st_size == before.st_size and after.st_mtime_ns != before.st_mtime_ns):
                raise UserError(f"会话文件被改写，请退出原应用后重试：{path}")
            yield target, False
        return
    if not is_sqlite(path):
        yield path, False
        return
    with tempfile.TemporaryDirectory(prefix="selfManger-sqlite-") as temporary:
        target = Path(temporary) / "snapshot.sqlite"
        deadline = time.monotonic() + 60
        def progress(status, remaining, total):
            context.checkpoint()
            if time.monotonic() > deadline:
                raise UserError("数据库持续忙碌，请暂停写入后重试。")
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise UserError("数据库快照完整性检查失败。")
            context.log("数据库已生成一致性快照。")
            yield target, True
        except sqlite3.Error as exc:
            # Some dormant WAL-mode databases have no WAL/SHM, but a read-only
            # connection tries to create SHM in the source directory. Copy only a
            # demonstrably stable standalone database, then read the isolated copy.
            companions = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm", "-journal")]
            if any(item.exists() for item in companions):
                raise UserError(f"无法创建数据库快照：{path}（{exc}）。存在日志文件，请退出原应用后重试。") from exc
            before = path.stat()
            static = Path(temporary) / "standalone.sqlite"
            with path.open("rb") as reader, static.open("xb") as writer:
                static.chmod(0o600)
                remaining = before.st_size
                while remaining:
                    context.checkpoint()
                    chunk = reader.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise UserError(f"数据库在读取时被截断：{path}")
                    writer.write(chunk)
                    remaining -= len(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or any(item.exists() for item in companions):
                raise UserError(f"数据库在读取时改变，请退出原应用后重试：{path}")
            try:
                with closing(sqlite3.connect(static.as_uri() + "?immutable=1", uri=True)) as source, closing(sqlite3.connect(target)) as destination:
                    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise UserError(f"数据库副本完整性检查失败：{path}")
                    source.backup(destination, pages=256, progress=progress)
                context.log("静止数据库在隔离副本中通过校验。")
                yield target, True
            except sqlite3.Error as fallback:
                raise UserError(f"无法创建数据库快照：{path}（{fallback}）。请退出原应用后重试。") from fallback
