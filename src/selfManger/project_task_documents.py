"""One-time import of legacy project task Markdown into SQLite."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .domain import UserError
from .project_workspaces import ProjectWorkspace, lifecycle_state
from .project_task_transactions import project_task_transaction
from .storage import Store


SOURCES = {
    "ai": ("agent/TASKS-AI.md", "AI 规划任务"),
    "manual": ("agent/TASKS-MANUAL.md", "手动编写任务"),
}
START = "<!-- selfManger:tasks:start -->"
END = "<!-- selfManger:tasks:end -->"
HEADER = "| ID | 任务 | 状态 | 日期 | 优先级 | 类型 | 目标编号 | 备注 | 完成记录 |\n|---|---|---|---|---|---|---|---|---|"
FIELDS = ("id", "title", "status", "due_date", "priority", "kind", "goal_id", "notes", "completion_note")
HEADINGS = ("ID", "任务", "状态", "日期", "优先级", "类型", "目标编号", "备注", "完成记录")


def _split_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    cells, current = [], []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _cell(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>") or "-"


def _plain(value: str) -> str:
    return value.replace("<br>", "\n") if value != "-" else ""


def _optional_goal_id(value: str) -> str | None:
    """Treat the legacy textual null sentinel as an absent plan reference."""
    identity = _plain(value).strip()
    return None if not identity or identity.casefold() == "none" else identity


def _block(text: str) -> tuple[str, str, str]:
    if text.count(START) > 1 or text.count(END) > 1:
        raise UserError("任务文档包含重复的自动管理区块标记，已停止迁移以保护原文。")
    start, end = text.find(START), text.find(END)
    if start < 0 and end < 0:
        return text, "", ""
    if start < 0 or end < start:
        raise UserError("任务文档的自动管理区块标记不完整，已停止迁移以保护原文。")
    return text[:start], text[start + len(START):end], text[end + len(END):]


def _parse_rows(body: str, source: str, identity: str, root: Path) -> list[dict]:
    rows, columns = [], None
    root_key = str(root.resolve())
    for number, line in enumerate(body.splitlines(), 1):
        if not line.strip():
            continue
        if not line.strip().startswith("|"):
            raise UserError(f"任务管理区第 {number} 行不是表格，请将说明移到管理区外；原文未覆盖。")
        cells = _split_row(line)
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if any(cell.casefold() == "id" for cell in cells):
            normalized = ["ID" if cell.casefold() == "id" else cell for cell in cells]
            if (columns is not None or len(set(normalized)) != len(normalized)
                    or set(normalized) not in (set(HEADINGS), set(HEADINGS[:-1]))):
                raise UserError("任务管理区表头无效或重复，请保留规定的八列或九列；原文未覆盖。")
            columns = {field: normalized.index(heading) for field, heading in zip(FIELDS, HEADINGS) if heading in normalized}
            continue
        if columns is None or len(cells) != len(columns):
            raise UserError(f"任务管理区第 {number} 行列数与表头不同，请检查竖线是否使用反斜线转义；原文未覆盖。")
        raw = {field: cells[index] for field, index in columns.items()}
        task_id = _plain(raw["id"])
        if task_id.casefold() in {"", "-", "new"}:
            task_id = ""
        kind = _plain(raw["kind"])
        if source == "ai" and "".join(kind.split()).casefold() in {"ai规划", "ai规划任务"}:
            kind = "task"
        kind = {"任务": "task", "普通任务": "task", "行动": "task", "action": "task", "日程": "schedule"}.get(kind, kind or "task")
        rows.append({
            "id": task_id, "title": _plain(raw["title"]), "kind": kind,
            "due_date": _plain(raw["due_date"]) or None, "project_id": identity,
            "project_root": root_key, "goal_id": _optional_goal_id(raw["goal_id"]),
            "priority": _plain(raw["priority"]), "status": _plain(raw["status"]) or "planned",
            "source": source, "notes": _plain(raw["notes"]),
            "completion_note": _plain(raw.get("completion_note", "")),
        })
    return rows


def _render(source: str, tasks: list[dict]) -> str:
    lines = [START, "", HEADER]
    for task in sorted(tasks, key=lambda item: item.get("status") == "done"):
        lines.append("| " + " | ".join(_cell(task.get(field)) for field in FIELDS) + " |")
    lines.append(END)
    return "\n".join(lines)


def _migration_key(workspace: ProjectWorkspace, identity: str) -> str:
    scope = json.dumps([str(workspace.root), identity], ensure_ascii=False)
    return "project-task-import-v1:" + hashlib.sha256(scope.encode("utf-8")).hexdigest()


def project_task_documents_imported(store: Store, workspace: ProjectWorkspace, identity: str) -> bool:
    return bool(store.setting(_migration_key(workspace, identity), False))


def legacy_project_task_documents_exist(workspace: ProjectWorkspace, identity: str) -> bool:
    project = workspace.project(identity)
    return any(workspace.path(project["path"] + "/" + relative).is_file()
               for relative, _title in SOURCES.values())


def _read_documents(store, workspace, identity, context=None):
    project = workspace.project(identity)
    documents, parsed, seen = {}, [], set()
    for source, (relative, _title) in SOURCES.items():
        if context:
            context.checkpoint()
        path = workspace.path(project["path"] + "/" + relative)
        text = workspace.read(path) if path.is_file() else None
        documents[source] = text
        try:
            rows = _parse_rows(_block(text)[1] if text is not None else "", source, identity, workspace.root)
        except UserError as exc:
            raise UserError(f"{relative}：{exc}") from exc
        for row in rows:
            if not row["title"]:
                raise UserError(f"{relative} 中存在空任务名称，请补充后再迁移。")
            task_id = row["id"]
            if task_id:
                if not re.fullmatch(r"[a-fA-F0-9]{32}", task_id) or task_id in seen:
                    raise UserError(f"{relative} 中任务 ID 无效或重复：{task_id}")
                seen.add(task_id)
        parsed.append((source, relative, rows))
    return project, documents, parsed


def _task_state(store, parsed):
    ids = {row["id"] for _source, _relative, rows in parsed for row in rows if row["id"]}
    goals = {row["goal_id"] for _source, _relative, rows in parsed for row in rows if row.get("goal_id")}
    existing, deleted_actions, deleted_goals = {}, set(), set()
    with store.connect() as db:
        for values, query in ((ids, "SELECT * FROM planned_tasks WHERE id IN ({})"),
                              (ids | goals, "SELECT entity_type,entity_id FROM planning_deletions WHERE entity_id IN ({})")):
            ordered = sorted(values)
            for offset in range(0, len(ordered), 400):
                chunk = ordered[offset:offset + 400]
                if not chunk:
                    continue
                rows = db.execute(query.format(",".join("?" for _ in chunk)), chunk)
                for row in rows:
                    if query.startswith("SELECT *"):
                        existing[row["id"]] = dict(row)
                    elif row["entity_type"] == "action":
                        deleted_actions.add(row["entity_id"])
                    elif row["entity_type"] == "plan":
                        deleted_goals.add(row["entity_id"])
    return existing, deleted_actions, deleted_goals


def _preview_token(store, workspace, identity, documents):
    with store.connect() as db:
        tasks = [dict(row) for row in db.execute(
            "SELECT * FROM planned_tasks WHERE project_root=? AND project_id=? ORDER BY id",
            (str(workspace.root), identity))]
        deletions = [tuple(row) for row in db.execute(
            "SELECT entity_type,entity_id,deleted_at FROM planning_deletions ORDER BY entity_type,entity_id")]
    payload = json.dumps([documents, tasks, deletions], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preview_legacy_project_task_import(store: Store, workspace: ProjectWorkspace, identity: str, context=None) -> dict:
    """Preview a one-time import; existing SQLite rows remain authoritative."""
    if project_task_documents_imported(store, workspace, identity):
        return {"added": 0, "changed": 0, "ignored": 0, "token": "", "already_imported": True}
    project, documents, parsed = _read_documents(store, workspace, identity, context)
    if lifecycle_state(workspace.read(workspace.path(project["path"] + "/agent/STATUS.md"))) == "archived":
        raise UserError("归档项目为只读，请先重新启用。")
    existing, deleted, deleted_goals = _task_state(store, parsed)
    added = changed = ignored = 0
    for source, relative, rows in parsed:
        for row in rows:
            values = {key: value for key, value in row.items() if key != "id"}
            old = existing.get(row["id"]) if row["id"] else None
            if row["id"] in deleted:
                ignored += 1
                continue
            if old and (old.get("project_id") != identity or old.get("project_root") != str(workspace.root)):
                raise UserError(f"{relative} · {row['title']}：任务不属于当前项目文档。")
            if values.get("goal_id") in deleted_goals:
                values["goal_id"] = None
            store._validate_planned_task(values)
            if old is None:
                added += 1
            elif any(old.get(key) != value for key, value in values.items() if key != "completion_note"):
                changed += 1
    return {"added": added, "changed": changed, "ignored": ignored,
            "token": _preview_token(store, workspace, identity, documents)}


def import_legacy_project_task_documents(store: Store, workspace: ProjectWorkspace, identity: str, context=None, *, preview_token=None) -> dict:
    """Import legacy project task tables once without writing to the source files."""
    if project_task_documents_imported(store, workspace, identity):
        return {"project_id": identity, "imported": 0, "already_imported": True}
    project = workspace.project(identity)
    if lifecycle_state(workspace.read(workspace.path(project["path"] + "/agent/STATUS.md"))) == "archived":
        raise UserError("归档项目为只读，请先重新启用。")
    imported = 0
    with project_task_transaction(store, workspace, identity):
        if project_task_documents_imported(store, workspace, identity):
            return {"project_id": identity, "imported": 0, "already_imported": True}
        project, documents, parsed = _read_documents(store, workspace, identity, context)
        if preview_token is not None and preview_token != _preview_token(store, workspace, identity, documents):
            raise UserError("预览后任务或文档发生变化，未导入，请重新预览。")
        existing, deleted, deleted_goals = _task_state(store, parsed)
        for source, relative, rows in parsed:
            for row in rows:
                task_id = row.pop("id")
                if task_id in deleted:
                    continue
                old = existing.get(task_id) if task_id else None
                if old:
                    if old.get("project_id") != identity or old.get("project_root") != str(workspace.root):
                        raise UserError(f"{relative} · {row['title']}：任务不属于当前项目文档。")
                    try:
                        store._validate_planned_task({key: value for key, value in row.items() if key != "id"})
                    except UserError as exc:
                        raise UserError(f"{relative} · {row['title']}：{exc}") from exc
                    continue
                if row.get("goal_id") in deleted_goals:
                    row["goal_id"] = None
                if source == "ai" and row["status"] == "done":
                    raise UserError(f"{relative} · {row['title']}：新 AI 任务不能直接迁入为完成。")
                try:
                    store._validate_planned_task(row)
                except UserError as exc:
                    raise UserError(f"{relative} · {row['title']}：{exc}（类型：{row.get('kind')!r}）") from exc
                store.add_planned_task(row)
                imported += 1
        for source, (relative, _title) in SOURCES.items():
            path = workspace.path(project["path"] + "/" + relative)
            current = workspace.read(path) if path.is_file() else None
            if current != documents[source]:
                raise UserError("任务文档在迁入期间发生变化；数据库未更新，请重新预览。")
        store.set_setting(_migration_key(workspace, identity), {"completed": True, "root": str(workspace.root)})
    return {"project_id": identity, "imported": imported, "already_imported": False}
