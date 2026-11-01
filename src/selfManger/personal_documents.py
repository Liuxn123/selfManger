"""Read-only access to legacy personal plan documents and their one-time importer."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

from .domain import UserError
from .storage import Store, now
from .workbench import MarkdownFiles, frontmatter


IMPORT_STATE_KEY = "personal_markdown_import_v1"


class PersonalMarkdownDocuments:
    """Keep legacy files readable while importing their final edits once."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _mapping(self, entity_type: str, identity: str):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM personal_markdown_documents WHERE entity_type=? AND entity_id=?",
                             (entity_type, identity)).fetchone()
        return dict(row) if row else None

    def path(self, entity_type: str, identity: str) -> str | None:
        mapping = self._mapping(entity_type, identity)
        if not mapping:
            return None
        return str(MarkdownFiles(mapping["root_path"]).path(mapping["relative_path"]))

    def migrate_legacy_documents(self, context=None) -> dict:
        """Import old external edits once; never follow future file changes."""
        return self._migrate_legacy_documents(context)

    def _migrate_legacy_documents(self, context=None) -> dict:
        with self.store.atomic():
            state = self.store.setting(IMPORT_STATE_KEY, {})
            if not isinstance(state, dict):
                state = {}
            if state.get("complete") or state.get("attempted_session") == self.store.session_id:
                return {"updated": state.get("updated", 0), "errors": state.get("errors", []),
                        "complete": bool(state.get("complete"))}
            return self._import_legacy_documents(context, state)

    def _import_legacy_documents(self, context=None, previous_state=None) -> dict:
        with self.store.connect() as db:
            mappings = [dict(row) for row in db.execute(
                "SELECT * FROM personal_markdown_documents ORDER BY updated_at")]
        changed, errors = 0, []
        for mapping in mappings:
            if context:
                context.checkpoint()
            try:
                files = MarkdownFiles(mapping["root_path"])
                text = files.read(mapping["relative_path"])
                if text is None:
                    errors.append(mapping["relative_path"] + "：Markdown 文档已找不到。")
                    continue
                digest = self._digest(text)
                if digest == mapping["content_hash"]:
                    continue
                metadata, body = frontmatter(text)
                if (metadata.get("quick_document") is not True
                        or metadata.get("entity_type") != mapping["entity_type"]
                        or metadata.get("entity_id") != mapping["entity_id"]):
                    errors.append(mapping["relative_path"] + "：文档身份信息不匹配，未导入修改。")
                    continue
                # Drop the single structural newline separating frontmatter and body.
                body = body.removeprefix("\n")
                title = str(metadata.get("title", "")).strip()
                title_limit = 160 if mapping["entity_type"] == "review" else 240
                if not title or len(title) > title_limit or "\n" in title or "\r" in title or len(body) > 20_000:
                    errors.append(mapping["relative_path"] + "：标题或正文不符合计划字段限制，未导入修改。")
                    continue
                with self.store.connect() as db:
                    entity_type, identity = mapping["entity_type"], mapping["entity_id"]
                    if entity_type == "action":
                        existing = db.execute("SELECT * FROM planned_tasks WHERE id=? AND source='manual'", (identity,)).fetchone()
                        if not existing:
                            raise UserError("对应的个人行动已不存在。")
                        current = dict(existing)
                        due_date = str(metadata.get("due_date") or "").strip() or None
                        if due_date:
                            date.fromisoformat(due_date)
                        project_id = str(metadata.get("project_id") or "").strip() or None
                        project_root = str(metadata.get("project_root") or "").strip() or None
                        goal_id = str(metadata.get("goal_id") or "").strip() or None
                        if project_id == current.get("project_id") and not project_root:
                            project_root = current.get("project_root")
                        clean = self.store._validate_planned_task({
                            **current, "title": title, "notes": body, "due_date": due_date,
                            "project_id": project_id, "project_root": project_root, "goal_id": goal_id,
                            "priority": metadata.get("priority", current["priority"]),
                            "scheduled_time": metadata.get("scheduled_time", current.get("scheduled_time", "")),
                            "status": metadata.get("status", current["status"]), "source": "manual"})
                        if clean["goal_id"] and not db.execute(
                                "SELECT 1 FROM personal_plans WHERE id=?", (clean["goal_id"],)).fetchone():
                            raise UserError("frontmatter 指向的长期计划不存在。")
                        frequency = metadata.get("frequency", "")
                        if not isinstance(frequency, str) or frequency not in {"", "daily", "weekly", "monthly"}:
                            raise UserError("frontmatter 中的周期规则无效。")
                        repeat_end = str(metadata.get("repeat_end") or "").strip() or None
                        if repeat_end:
                            date.fromisoformat(repeat_end)
                            if not frequency or not clean["due_date"] or repeat_end < clean["due_date"]:
                                raise UserError("frontmatter 中的周期截止日期无效。")
                        weekdays = metadata.get("repeat_days") or []
                        if not isinstance(weekdays, list) or any(type(day) is not int or day not in range(1, 8) for day in weekdays):
                            raise UserError("frontmatter 中的重复星期无效。")
                        if frequency == "weekly" and not weekdays:
                            raise UserError("每周计划至少保留一个重复星期。")
                        month_day = metadata.get("repeat_month_day") if frequency == "monthly" else None
                        if month_day is not None and (type(month_day) is not int or month_day not in range(1, 32)):
                            raise UserError("frontmatter 中的每月重复日期必须为 1–31 号。")
                        completed_dates = metadata.get("completed_dates", [])
                        if (not isinstance(completed_dates, list) or len(completed_dates) > 5000
                                or any(not isinstance(item, str) for item in completed_dates)):
                            raise UserError("frontmatter 中的打卡日期无效。")
                        normalized_dates = set()
                        for item in completed_dates:
                            occurrence_day = date.fromisoformat(item)
                            if occurrence_day > date.today():
                                raise UserError("打卡日期不能晚于今天。")
                            normalized_dates.add(occurrence_day.isoformat())
                        db.execute("UPDATE planned_tasks SET " + ",".join(field + "=?" for field in clean)
                                   + ",updated_at=? WHERE id=?", (*clean.values(), now(), identity))
                        if frequency:
                            db.execute("""INSERT INTO action_repeat_rules (task_id,frequency,start_date,end_date,weekdays,month_day) VALUES (?,?,?,?,?,?)
                                ON CONFLICT(task_id) DO UPDATE SET frequency=excluded.frequency,
                                start_date=excluded.start_date,end_date=excluded.end_date,weekdays=excluded.weekdays,month_day=excluded.month_day""",
                                (identity, frequency, clean["due_date"], repeat_end, json.dumps(weekdays), month_day))
                        else:
                            db.execute("DELETE FROM action_repeat_rules WHERE task_id=?", (identity,))
                        if frequency:
                            db.execute("DELETE FROM action_occurrences WHERE task_id=?", (identity,))
                            db.executemany("INSERT INTO action_occurrences(task_id,day,completed_at) VALUES (?,?,?)",
                                           ((identity, item, now()) for item in sorted(normalized_dates)))
                        else:
                            db.execute("DELETE FROM action_occurrences WHERE task_id=?", (identity,))
                    elif entity_type == "plan":
                        existing = db.execute("SELECT * FROM personal_plans WHERE id=?", (identity,)).fetchone()
                        if not existing:
                            raise UserError("对应的长期计划已不存在。")
                        current = dict(existing)
                        due_date = str(metadata.get("due_date") or "").strip() or None
                        if due_date:
                            date.fromisoformat(due_date)
                        project_id = str(metadata.get("project_id") or "").strip() or None
                        project_root = str(metadata.get("project_root") or "").strip() or None
                        if project_id == current.get("project_id") and not project_root:
                            project_root = current.get("project_root")
                        clean = self.store._validate_planned_task({
                            "title": title, "notes": body, "due_date": due_date,
                            "project_id": project_id, "project_root": project_root})
                        status = metadata.get("status", current["status"])
                        if not isinstance(status, str) or status not in {"planned", "active", "done", "archived"}:
                            raise UserError("frontmatter 中的计划状态无效。")
                        db.execute("""UPDATE personal_plans SET title=?,notes=?,due_date=?,project_id=?,
                            project_root=?,status=?,updated_at=? WHERE id=?""",
                            (clean["title"], clean["notes"], clean["due_date"], clean["project_id"],
                             clean["project_root"], status, now(), identity))
                    elif entity_type == "review":
                        start = str(metadata.get("start_date") or "").strip()
                        end = str(metadata.get("end_date") or "").strip()
                        period_type = metadata.get("period_type")
                        period_key = str(metadata.get("period_key") or "").strip()
                        if not isinstance(period_type, str) or period_type not in {"weekly", "monthly"} or not period_key:
                            raise UserError("frontmatter 中的复盘周期无效。")
                        if date.fromisoformat(start) > date.fromisoformat(end):
                            raise UserError("frontmatter 中的复盘日期范围无效。")
                        db.execute("""UPDATE personal_reviews SET title=?,body=?,period_type=?,period_key=?,
                            start_date=?,end_date=?,updated_at=? WHERE id=?""",
                            (title, body, period_type, period_key, start, end, now(), identity))
                    else:
                        errors.append(mapping["relative_path"] + "：记录类型无效。")
                        continue
                    db.execute("UPDATE personal_markdown_documents SET content_hash=?,updated_at=? "
                               "WHERE entity_type=? AND entity_id=?",
                               (digest, now(), entity_type, identity))
                changed += 1
            except (OSError, UserError, ValueError, TypeError, sqlite3.Error) as exc:
                errors.append(mapping["relative_path"] + "：" + str(exc))
        state = {"complete": not errors, "attempted_session": self.store.session_id,
                 "updated": changed + int((previous_state or {}).get("updated", 0)),
                 "errors": errors, "checked": len(mappings)}
        self.store.set_setting(IMPORT_STATE_KEY, state)
        return {"updated": state["updated"], "errors": errors, "complete": state["complete"]}
