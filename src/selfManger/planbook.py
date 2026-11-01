"""Read-only parser for legacy longer-range planning documents."""
from __future__ import annotations

from datetime import date, timedelta

from .domain import UserError
from .workbench import MarkdownFiles, frontmatter

KINDS = {
    "plan": "长期任务",
    "goal": "长期任务",
    "recurring": "周期任务",
    "template": "计划模板",
    "review": "计划复盘",
}
FOLDERS = {
    "plan": "安排",
    "goal": "目标",
    "recurring": "周期任务",
    "template": "模板",
    "review": "复盘",
}
class PlanBook:
    """Read existing Markdown records for the one-time SQLite migration."""

    def __init__(self, root):
        self.files = MarkdownFiles(root)

    def scan(self, kind=None, context=None):
        if kind is not None and kind not in KINDS:
            raise UserError("计划类型无效。")
        kinds = [kind] if kind else list(KINDS)
        items, errors = [], []
        for entry_kind in kinds:
            directory = self.files.path("计划/" + FOLDERS[entry_kind])
            if not directory.exists():
                continue
            try:
                paths = sorted(directory.glob("*.md"), key=lambda path: path.name.casefold())
            except OSError as exc:
                errors.append(FOLDERS[entry_kind] + "：" + str(exc))
                continue
            for index, path in enumerate(paths):
                if context:
                    context.checkpoint()
                if index >= 1000:
                    errors.append(FOLDERS[entry_kind] + "超过 1000 个文件，请整理后再查看。")
                    break
                relative = "计划/" + FOLDERS[entry_kind] + "/" + path.name
                try:
                    safe = self.files.path(relative)
                    text = self.files.read(relative)
                    if text is None:
                        continue
                    metadata, body = frontmatter(text)
                    if metadata.get("type") != entry_kind:
                        errors.append(path.name + "：计划类型与目录不匹配。")
                        continue
                    title = metadata.get("title")
                    identity = metadata.get("id")
                    if not isinstance(title, str) or not title.strip() or not isinstance(identity, str):
                        errors.append(path.name + "：缺少有效标题或编号。")
                        continue
                    items.append({"id": identity, "type": entry_kind, "title": title,
                                  "metadata": metadata, "body": body, "text": text,
                                  "relative": relative, "path": str(safe)})
                except (UserError, OSError) as exc:
                    errors.append(path.name + "：" + str(exc))
        items.sort(key=lambda item: (item["metadata"].get("due") or "9999-99-99",
                                     item["title"].casefold()))
        return {"items": items, "errors": errors}

def occurs_on(item, day):
    """Project a recurring rule onto one date without writing an occurrence."""
    metadata = item["metadata"]
    if item["type"] != "recurring" or metadata.get("status", "planned") in {"done", "archived"}:
        return False
    try:
        start = date.fromisoformat(metadata.get("start", ""))
        end = date.fromisoformat(metadata["due"]) if metadata.get("due") else None
    except (TypeError, ValueError):
        return False
    if day < start or (end and day > end):
        return False
    frequency = metadata.get("frequency")
    if frequency == "daily":
        return True
    if frequency == "weekly":
        weekdays = metadata.get("weekdays")
        if not isinstance(weekdays, (list, tuple, set)) or not weekdays:
            # Keep legacy weekly records working until they are edited and saved.
            weekdays = [start.isoweekday()]
        try:
            selected = {int(value) for value in weekdays}
        except (TypeError, ValueError):
            return False
        return day.isoweekday() in selected
    if frequency == "monthly":
        month_day = metadata.get("month_day")
        if month_day is not None:
            return type(month_day) is int and month_day in range(1, 32) and day.day == month_day
        # A month-end rule stays on month-end; other rules use their original day number.
        month_end = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        start_month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return day.day == (month_end.day if start.day == start_month_end.day else start.day)
    return False
