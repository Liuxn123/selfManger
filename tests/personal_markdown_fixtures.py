"""Create legacy personal Markdown fixtures without using production writers."""
import hashlib
from pathlib import Path

from selfManger.naming import document_stem
from selfManger.storage import now
from selfManger.workbench import MarkdownFiles, render_document, work_root


def attach_legacy_document(store, entity_type, identity, module, record):
    root = work_root(store).expanduser().resolve()
    folder = {
        "daily": "每日计划", "longterm": "长期计划", "recurring": "周期计划",
        "weekly-review": "周复盘", "monthly-review": "月复盘", "project": "项目计划",
    }[module]
    relative = "计划/" + folder + "/" + document_stem(record["title"], identity) + ".md"
    metadata = {"quick_document": True, "entity_type": entity_type, "entity_id": identity,
                "module": module, "title": record["title"]}
    for key in ("due_date", "project_id", "project_root", "goal_id", "status", "priority"):
        if key in record:
            metadata[key] = record.get(key) or ""
    if entity_type == "action":
        metadata.update(frequency=record.get("repeat", ""), repeat_end=record.get("repeat_end", ""),
                        repeat_days=record.get("repeat_days", []),
                        repeat_month_day=record.get("repeat_month_day"),
                        completed_dates=record.get("completed_dates", []))
    if entity_type == "review":
        for key in ("period_key", "period_type", "start_date", "end_date"):
            metadata[key] = record.get(key, "")
    text = render_document(metadata, record.get("notes", record.get("body", "")))
    path = MarkdownFiles(root).write(relative, text, None)["path"]
    with store.connect() as db:
        db.execute("""INSERT INTO personal_markdown_documents
            (entity_type,entity_id,module,root_path,relative_path,content_hash,updated_at)
            VALUES (?,?,?,?,?,?,?)""",
            (entity_type, identity, module, str(root), relative,
             hashlib.sha256(text.encode("utf-8")).hexdigest(), now()))
    return Path(path)


def legacy_action_record(center, identity, record):
    """Build a historical Markdown fixture payload; production no longer renders these records."""
    rule = center.rules().get(identity, {})
    with center.store.connect() as db:
        completed = [row["day"] for row in db.execute(
            "SELECT day FROM action_occurrences WHERE task_id=? ORDER BY day", (identity,))]
    return {**record, "repeat": rule.get("frequency", ""), "repeat_end": rule.get("end_date", ""),
            "repeat_days": rule.get("weekdays", []), "repeat_month_day": rule.get("month_day"),
            "completed_dates": completed}
