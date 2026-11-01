"""Read-time ordering for numbered AI tasks within a project."""
from __future__ import annotations

import re
from collections import defaultdict


_NUMBER_PREFIX = re.compile(r"^\s*([0-9]+)(?=\D|$)")


def order_project_ai_tasks(tasks: list[dict], *, title_key: str = "title") -> list[dict]:
    """Reorder only numbered AI slots, preserving all other rows and project boundaries.

    Callers must already be in project context. Global agenda queries keep their
    date order. Equal numbers use title and identity for repeatable tie breaking.
    """
    result = list(tasks)
    groups = defaultdict(list)
    for index, task in enumerate(tasks):
        match = _NUMBER_PREFIX.match(task.get(title_key) or "")
        if task.get("source") == "ai" and match:
            group = (task.get("project_root"), task.get("project_id"))
            groups[group].append((index, int(match[1]), task))
    for entries in groups.values():
        ordered = sorted(entries, key=lambda entry: (
            entry[1], entry[2].get(title_key) or "",
            entry[2].get("id") or entry[2].get("task_id") or ""))
        for (index, _number, _task), (_old_index, _new_number, task) in zip(entries, ordered):
            result[index] = task
    return result


def project_checklist_snapshot(snapshot: dict, linked_tasks: list[dict], running_ids=()) -> dict:
    """Merge SQLite project actions into the document-derived overview checklist."""
    checklist = dict(snapshot["checklist"])
    running = set(running_ids)
    rows = [{"text": task["title"], "source": task["source"],
             "done": task.get("done", task["status"] == "done"),
             "status": "done" if task.get("done") else task["status"],
             "personal": task.get("personal", False), "task_id": task["id"],
             "due_date": task["due_date"], "completion_note": task.get("completion_note", ""),
             "running": task["id"] in running}
            for task in linked_tasks]
    tasks = sorted(order_project_ai_tasks(rows, title_key="text"), key=lambda task: bool(task["done"]))
    ai_tasks = [task for task in tasks if task.get("source") == "ai"]
    manual_tasks = [task for task in tasks if task.get("source") != "ai"]
    done = sum(bool(task["done"]) for task in tasks)
    active = sum(task.get("status") in {"active", "in_progress", "进行中", "处理中"} for task in tasks)
    overdue = sum(task.get("status") in {"overdue", "late", "逾期", "已逾期"} for task in tasks)
    checklist.update({"tasks": tasks, "ai_tasks": ai_tasks, "manual_tasks": manual_tasks,
                      "total": len(tasks), "done": done, "in_progress": active,
                      "pending": max(0, len(tasks) - done - active - overdue), "overdue": overdue,
                      "progress": round(done / len(tasks) * 100) if tasks else 0})
    return {**snapshot, "checklist": checklist}
