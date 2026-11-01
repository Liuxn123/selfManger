"""Read old interrupted Markdown journals and serialize SQLite action writes."""
from functools import wraps
import hashlib
import json

from .domain import UserError
from .workbench import MarkdownFiles, MAX_TEXT


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _directory(store):
    return MarkdownFiles(store.root).path(".markdown-recovery")


def recover_documents(store, paths=None):
    directory = _directory(store)
    if not directory.exists():
        return
    paths = list(paths) if paths is not None else sorted(directory.glob("*.json"))
    if not paths:
        return
    # Wait for another process's in-flight transaction before judging its files.
    with store.atomic() as db:
        for path in paths:
            if not path.exists():
                continue
            if path.is_symlink() or path.parent != directory or path.stat().st_size > MAX_TEXT * 3:
                raise UserError("计划恢复记录不可安全读取，请保留现场并检查数据目录。")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                required = {"entity_type", "entity_id", "root_path", "relative_path", "original", "replacement"}
                if (not isinstance(record, dict) or set(record) != required
                        or any(not isinstance(value, str) for value in record.values())
                        or record["entity_type"] not in {"action", "plan", "review"}
                        or any(len(record[key].encode("utf-8")) > MAX_TEXT for key in ("original", "replacement"))):
                    raise ValueError("Invalid journal")
            except (ValueError, UnicodeError) as exc:
                raise UserError("计划恢复记录格式无效，已保留现场，请检查数据目录。") from exc
            mapping = db.execute("SELECT * FROM personal_markdown_documents WHERE entity_type=? AND entity_id=?",
                                 (record["entity_type"], record["entity_id"])).fetchone()
            if (mapping is None or mapping["root_path"] != record["root_path"]
                    or mapping["relative_path"] != record["relative_path"]):
                raise UserError("计划恢复记录与当前资料位置不一致，已保留记录，请检查后重试。")
            files = MarkdownFiles(mapping["root_path"])
            relative = mapping["relative_path"]
            current = files.read(relative)
            original, replacement = record["original"], record["replacement"]
            if not isinstance(original, str) or not isinstance(replacement, str):
                raise UserError("计划恢复记录格式无效，已保留现场。")
            persisted = mapping["content_hash"]
            if current is not None and _digest(current) == persisted:
                # Either committed, already restored, or superseded consistently.
                path.unlink()
            elif persisted == _digest(original) and current == replacement:
                files.write(relative, original, replacement)
                path.unlink()
            else:
                raise UserError("计划保存被中断且文档已有外部修改；原文及恢复记录均已保留，请核对后重试。")


def atomic_action(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.atomic():
            return method(self, *args, **kwargs)
    return wrapped
