"""Local personal plans and dated actions shared by every UI entry point."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import date, timedelta
from uuid import uuid4

from .domain import UserError
from .planbook import occurs_on
from .personal_documents import PersonalMarkdownDocuments
from .storage import Store, now
from .document_transactions import atomic_action


def is_personal_action(task: dict) -> bool:
    """Project document checklists and agent jobs do not become personal inbox items."""
    ref = task.get("source_ref") or ""
    return task.get("source") != "ai" and not ref.startswith(("legacy:project:", "project-doc:"))


class ActionCenter:
    def __init__(self, store: Store):
        self.store = store
        self.documents = PersonalMarkdownDocuments(store)

    def plans(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM personal_plans WHERE NOT EXISTS
                (SELECT 1 FROM planning_deletions WHERE entity_type='plan' AND entity_id=personal_plans.id)
                ORDER BY status='done', due_date IS NULL, due_date, created_at""")]

    def ensure_not_deleted(self, entity_type, identity):
        if identity:
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM planning_deletions WHERE entity_type=? AND entity_id=?",
                              (entity_type, identity)).fetchone():
                    raise UserError("这条计划已被删除，请关闭旧编辑器并刷新。")

    def delete_record(self, entity_type, identity, *, expected=None):
        """Delete one shared identity while retaining its database snapshot."""
        tables = {"plan": "personal_plans", "action": "planned_tasks", "review": "personal_reviews"}
        if entity_type not in tables:
            raise UserError("计划类型无效。")
        table = tables[entity_type]
        with self.store.atomic() as db:
            self.ensure_not_deleted(entity_type, identity)
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if not row or (expected is not None and dict(row) != expected):
                raise UserError("计划已变化，请刷新后再删除。")
            record = dict(row)
            linked = self.store.planned_tasks(goal_id=identity) if entity_type == "plan" else []
            snapshot = {"record": record, "linked": linked,
                        "document": self.documents._mapping(entity_type, identity)}
            if entity_type == "action":
                snapshot["rule"] = self.rules().get(identity)
                snapshot["occurrences"] = [dict(row) for row in db.execute(
                    "SELECT * FROM action_occurrences WHERE task_id=?", (identity,))]
            current = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if not current or dict(current) != record:
                raise UserError("计划在删除期间变化，请刷新后重试。")
            if entity_type == "action":
                rule = db.execute("SELECT * FROM action_repeat_rules WHERE task_id=?", (identity,)).fetchone()
                rule = {**dict(rule), "weekdays": json.loads(rule["weekdays"])} if rule else None
                occurrences = [dict(row) for row in db.execute("SELECT * FROM action_occurrences WHERE task_id=?", (identity,))]
                if rule != snapshot["rule"] or occurrences != snapshot["occurrences"]:
                    raise UserError("周期规则或打卡记录已变化，请刷新后再删除。")
            current_linked = [dict(row) for row in db.execute(
                "SELECT * FROM planned_tasks WHERE goal_id=? AND NOT EXISTS (SELECT 1 FROM planning_deletions "
                "WHERE entity_type='action' AND entity_id=planned_tasks.id)", (identity,))] if entity_type == "plan" else []
            if {row["id"]: row for row in current_linked} != {row["id"]: row for row in linked}:
                raise UserError("关联行动已变化，请刷新后再删除。")
            db.execute("INSERT INTO planning_deletions VALUES (?,?,?,?)",
                       (entity_type, identity, json.dumps(snapshot, ensure_ascii=False), now()))
            if entity_type == "plan":
                db.execute("UPDATE planned_tasks SET goal_id=NULL,updated_at=? WHERE goal_id=?", (now(), identity))
            elif entity_type == "action":
                db.execute("DELETE FROM action_repeat_rules WHERE task_id=?", (identity,))
                db.execute("DELETE FROM action_occurrences WHERE task_id=?", (identity,))
            else:
                # Free the unique period key so a deleted review can be written again.
                db.execute("UPDATE personal_reviews SET period_key=? WHERE id=?", ("deleted:" + identity, identity))
            db.execute("DELETE FROM personal_markdown_documents WHERE entity_type=? AND entity_id=?", (entity_type, identity))
        return {"deleted": identity, "entity_type": entity_type, "warning": ""}

    @atomic_action
    def delete_project_task(self, identity: str, *, expected: dict) -> dict:
        """Delete a selected project task only if its persisted fields are unchanged."""
        current = self.store.planned_task(identity)
        if (not current or not isinstance(expected, dict)
                or not current.keys() <= expected.keys()
                or any(current[key] != expected[key] for key in current)):
            raise UserError("任务已变化，请刷新后再删除。")
        return self.delete_record("action", identity, expected=current)

    @atomic_action
    def save_plan(self, values: dict, identity: str | None = None) -> dict:
        self.ensure_not_deleted("plan", identity)
        clean = self.store._validate_planned_task({**values, "kind": "task", "source": "manual", "status": "planned"})
        status = values.get("status", "planned")
        if status not in {"planned", "active", "done", "archived"}:
            raise UserError("计划状态无效。")
        identity = identity or uuid4().hex
        timestamp = now()
        with self.store.connect() as db:
            if values.get("_expected"):
                existing = db.execute("SELECT * FROM personal_plans WHERE id=?", (identity,)).fetchone()
                if not existing or dict(existing) != values["_expected"]:
                    raise UserError("计划已被其他操作修改，请保留草稿并刷新后重试。")
            db.execute("""INSERT INTO personal_plans
                (id,title,due_date,project_id,project_root,notes,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,due_date=excluded.due_date,project_id=excluded.project_id,
                project_root=excluded.project_root,notes=excluded.notes,status=excluded.status,updated_at=excluded.updated_at""",
                (identity, clean["title"], clean["due_date"], clean["project_id"], clean["project_root"],
                 clean["notes"], status, timestamp, timestamp))
            result = dict(db.execute("SELECT * FROM personal_plans WHERE id=?", (identity,)).fetchone())
        return result

    def templates(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM personal_plan_templates ORDER BY title")]

    def project_schedule(self, project_root, project_ids, *, day=None) -> list[dict]:
        """Read manual project tasks and linked actions without importing documents."""
        if not project_root or not project_ids:
            return []
        root = str(Path(project_root).expanduser().resolve())
        ids = set(project_ids)
        personal = {row["id"]: row for row in self.actions("all", day=day, project_root=root)}
        result = []
        for task in self.store.planned_tasks(project_root=root, source="manual"):
            if task.get("project_id") not in ids:
                continue
            row = personal.get(task["id"])
            result.append({**task, "personal": is_personal_action(task),
                           "done": row["done"] if row else task["status"] == "done",
                           "occurrence_day": row["occurrence_day"] if row else task.get("due_date"),
                           "repeat": row["repeat"] if row else ""})
        return result

    def reviews(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM personal_reviews WHERE NOT EXISTS
                (SELECT 1 FROM planning_deletions WHERE entity_type='review' AND entity_id=personal_reviews.id)
                ORDER BY start_date DESC, updated_at DESC""")]

    def set_project_task_status(self, identity, status, *, expected=None, day=None):
        """Change a shared task; repeating personal actions complete one occurrence."""
        record = self.store.planned_task(identity)
        if not record:
            raise UserError("任务已不存在，请刷新后重试。")
        if status not in {"planned", "active", "done"}:
            raise UserError("任务状态无效。")
        if expected and any(record[key] != expected.get(key) for key in record):
            raise UserError("任务已被其他操作修改，请刷新后再修改状态。")
        if record["source"] != "manual":
            raise UserError("只能修改现有项目任务的状态，请刷新后重试。")
        if identity in self.rules():
            if status == "active":
                raise UserError("周期行动请修改当天打卡状态。")
            return self.complete(identity, status == "done", day)
        if is_personal_action(record):
            return self.save_action({**record, "status": status, "_expected": record}, identity)
        return self.store.set_planned_task_status(identity, status, expected=record)

    @atomic_action
    def set_ai_project_task_status(self, identity: str, status: str, *, expected=None) -> dict:
        """Apply the explicit status override offered by the project editor."""
        current = self.store.planned_task(identity)
        if not current or current.get("source") != "ai":
            raise UserError("AI 项目任务已变化，请刷新后重试。")
        if expected is not None and any(current[key] != expected.get(key) for key in current):
            raise UserError("AI 项目任务已变化，请刷新后重试。")
        return self.store.set_planned_task_status(
            identity, status, manual_override=True, expected=current)

    @atomic_action
    def save_project_task(self, values: dict, identity: str | None = None, *, expected=None) -> dict:
        """Create or edit a project task through the shared task business service."""
        if not identity:
            return self.store.add_planned_task(values)
        self.ensure_not_deleted("action", identity)
        current = self.store.planned_task(identity)
        if not current or (expected is not None and any(
                current[key] != expected.get(key) for key in current)):
            raise UserError("任务已变化，请保留草稿并刷新后重试。")
        # The project identity is fixed by the project editor; older rows without
        # a root may acquire the current registered root when they are edited.
        clean_values = {
            **values,
            "project_id": current.get("project_id"),
            "project_root": current.get("project_root") or values.get("project_root"),
            "_expected": current,
        }
        return self.store.update_planned_task(identity, clean_values)

    @atomic_action
    def save_review(self, values: dict, identity: str | None = None) -> dict:
        self.ensure_not_deleted("review", identity)
        period_type = values.get("period_type")
        if period_type not in {"weekly", "monthly"}:
            raise UserError("复盘周期无效。")
        title = str(values.get("title", "")).strip()
        body = str(values.get("body", ""))
        start = str(values.get("start_date", ""))
        end = str(values.get("end_date", ""))
        period_key = str(values.get("period_key", ""))
        if not title or len(title) > 160 or "\n" in title or "\r" in title:
            raise UserError("请填写 1–160 字的一行复盘标题。")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise UserError("复盘日期无效，请刷新后重试。") from exc
        if start_day > end_day or not period_key:
            raise UserError("复盘周期信息无效，请刷新后重试。")
        identity = identity or uuid4().hex
        timestamp = now()
        with self.store.connect() as db:
            existing = db.execute("SELECT * FROM personal_reviews WHERE id=?", (identity,)).fetchone() if values.get("_expected") else None
            if values.get("_expected") and (not existing or dict(existing) != values["_expected"]):
                raise UserError("复盘已被其他操作修改，请保留草稿并刷新后重试。")
            if not values.get("_expected") and db.execute(
                    "SELECT 1 FROM personal_reviews WHERE period_key=?", (period_key,)).fetchone():
                raise UserError("这个周期已有复盘记录，请从最近文档打开后编辑。")
            db.execute("""INSERT INTO personal_reviews
                (id,period_key,period_type,title,start_date,end_date,body,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(period_key) DO UPDATE SET
                period_type=excluded.period_type,title=excluded.title,start_date=excluded.start_date,
                end_date=excluded.end_date,body=excluded.body,updated_at=excluded.updated_at""",
                (identity, period_key, period_type, title, start, end, body,
                 values.get("created_at") or timestamp, timestamp))
            result = dict(db.execute("SELECT * FROM personal_reviews WHERE period_key=?", (period_key,)).fetchone())
        return result

    def rules(self) -> dict[str, dict]:
        with self.store.connect() as db:
            return {row["task_id"]: {**dict(row), "weekdays": json.loads(row["weekdays"])}
                    for row in db.execute("SELECT * FROM action_repeat_rules")}

    @staticmethod
    def occurs(rule: dict, day: date) -> bool:
        return occurs_on({"type": "recurring", "metadata": {
            "start": rule["start_date"], "due": rule.get("end_date"),
            "frequency": rule["frequency"], "weekdays": rule.get("weekdays", []), "month_day": rule.get("month_day")}}, day)

    @atomic_action
    def save_action(self, values: dict, identity: str | None = None) -> dict:
        self.ensure_not_deleted("action", identity)
        if identity and "scheduled_time" not in values:
            existing_record = self.store.planned_task(identity)
            if existing_record:
                values = {**values, "scheduled_time": existing_record.get("scheduled_time", "")}
        clean = self.store._validate_planned_task({**values, "source": "manual"})
        frequency = values.get("repeat", "")
        if frequency not in {"", "daily", "weekly", "monthly"}:
            raise UserError("重复设置无效。")
        if frequency and not clean["due_date"]:
            raise UserError("重复行动需要一个开始日期。")
        end = values.get("repeat_end") or None
        if end:
            try:
                date.fromisoformat(end)
            except (ValueError, TypeError) as exc:
                raise UserError("重复截止日期无效。") from exc
            if not frequency or end < clean["due_date"]:
                raise UserError("重复截止日期不能早于开始日期。")
        weekdays = values.get("repeat_days", None)
        weekdays = weekdays if weekdays is not None else ([date.fromisoformat(clean["due_date"]).isoweekday()] if frequency == "weekly" else [])
        if not isinstance(weekdays, list) or any(type(day) is not int or day not in range(1, 8) for day in weekdays):
            raise UserError("重复星期无效。")
        if frequency == "weekly" and not weekdays:
            raise UserError("请至少选择一个重复星期。")
        month_day = values.get("repeat_month_day") if frequency == "monthly" else None
        if month_day is not None and (type(month_day) is not int or month_day not in range(1, 32)):
            raise UserError("每月重复日期必须为 1–31 号。")
        identity = identity or uuid4().hex
        timestamp = now()
        with self.store.connect() as db:
            existing = db.execute("SELECT * FROM planned_tasks WHERE id=?", (identity,)).fetchone()
            if values.get("_expected") and (not existing or dict(existing) != values["_expected"]):
                raise UserError("行动已被其他操作修改，请保留草稿并刷新后重试。")
            if "_expected_rule" in values:
                previous_rule = db.execute("SELECT * FROM action_repeat_rules WHERE task_id=?", (identity,)).fetchone()
                previous_rule = {**dict(previous_rule), "weekdays": json.loads(previous_rule["weekdays"])} if previous_rule else {}
                if previous_rule != values["_expected_rule"]:
                    raise UserError("重复规则已被其他操作修改，请保留草稿并刷新后重试。")
            if existing and not is_personal_action(dict(existing)):
                raise UserError("项目文档任务请在项目管理中维护。")
            if clean["goal_id"] and not db.execute("""SELECT 1 FROM personal_plans WHERE id=? AND NOT EXISTS
                (SELECT 1 FROM planning_deletions WHERE entity_type='plan' AND entity_id=personal_plans.id)""", (clean["goal_id"],)).fetchone():
                # Preserve existing legacy links until the user explicitly changes or clears them.
                unchanged_legacy_reference = existing and existing["goal_id"] == clean["goal_id"]
                if not unchanged_legacy_reference:
                    raise UserError("所选计划已不存在，请重新选择或取消关联。")
            fields = tuple(clean)
            if existing:
                db.execute("UPDATE planned_tasks SET " + ",".join(field + "=?" for field in fields)
                           + ",updated_at=? WHERE id=?", (*clean.values(), timestamp, identity))
            else:
                db.execute("INSERT INTO planned_tasks (id," + ",".join(fields) + ",source_ref,created_at,updated_at) VALUES ("
                           + ",".join("?" for _ in range(len(fields) + 4)) + ")",
                           (identity, *clean.values(), "personal:" + identity, timestamp, timestamp))
            if frequency:
                db.execute("""INSERT INTO action_repeat_rules (task_id,frequency,start_date,end_date,weekdays,month_day) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(task_id) DO UPDATE SET frequency=excluded.frequency,start_date=excluded.start_date,
                    end_date=excluded.end_date,weekdays=excluded.weekdays,month_day=excluded.month_day""",
                    (identity, frequency, clean["due_date"], end, json.dumps(weekdays), month_day))
            else:
                db.execute("DELETE FROM action_repeat_rules WHERE task_id=?", (identity,))
        result = self.store.planned_task(identity)
        return result

    @atomic_action
    def complete(self, identity: str, done: bool, day: str | None = None) -> dict:
        self.ensure_not_deleted("action", identity)
        with self.store.connect() as db:
            task = db.execute("SELECT * FROM planned_tasks WHERE id=?", (identity,)).fetchone()
            if not task or not is_personal_action(dict(task)):
                raise UserError("个人行动已不存在，请刷新后重试。")
            rule = db.execute("SELECT * FROM action_repeat_rules WHERE task_id=?", (identity,)).fetchone()
            if rule:
                try:
                    occurrence_day = date.fromisoformat(day or "")
                except ValueError as exc:
                    raise UserError("请选择要更新的重复行动日期。") from exc
                if occurrence_day > date.today():
                    raise UserError("未来日期尚不能打卡，请选择今天或过去的日期。")
                if not self.occurs({**dict(rule), "weekdays": json.loads(rule["weekdays"])}, occurrence_day):
                    raise UserError("这一天不属于该重复规则。")
                if done:
                    db.execute("INSERT OR IGNORE INTO action_occurrences VALUES (?,?,?)", (identity, day, now()))
                else:
                    db.execute("DELETE FROM action_occurrences WHERE task_id=? AND day=?", (identity, day))
            else:
                db.execute("UPDATE planned_tasks SET status=?,updated_at=? WHERE id=?",
                           ("done" if done else "planned", now(), identity))
        result = self.store.planned_task(identity)
        return result

    def actions(self, view="all", *, day: date | None = None, plan_id=None,
                project_id=None, project_root=None, query="", status="all") -> list[dict]:
        day = day or date.today()
        rules = self.rules()
        plans = {item["id"]: item for item in self.plans()}
        with self.store.connect() as db:
            completed = {(row["task_id"], row["day"]) for row in db.execute("SELECT task_id,day FROM action_occurrences")}
        result = []
        tasks = self.store.planned_tasks(project_id=project_id, project_root=project_root, goal_id=plan_id)
        for task in tasks:
            if not is_personal_action(task):
                continue
            rule = rules.get(task["id"])
            dates = [task["due_date"]]
            if rule:
                if view == "inbox":
                    continue
                if task["status"] == "done":
                    dates = [task["due_date"]] if view == "all" else []
                else:
                    dates = []
                    for offset in range(1 if view == "today" else 367):
                        target = day + timedelta(days=offset + (1 if view == "upcoming" else 0))
                        if view == "upcoming" and offset >= 30:
                            break
                        if self.occurs(rule, target):
                            dates.append(target.isoformat())
                            if view != "upcoming":
                                break
                    if not dates and view == "all":
                        last = date.fromisoformat(rule.get("end_date") or rule["start_date"])
                        for offset in range(32):
                            candidate = last - timedelta(days=offset)
                            if self.occurs(rule, candidate):
                                dates = [candidate.isoformat()]
                                break
            for target in dates:
                done = ((task["id"], target) in completed or task["status"] == "done") if rule else task["status"] == "done"
                if view == "today" and not rule and (not target or target > day.isoformat() or (done and target != day.isoformat())):
                    continue
                if view == "upcoming" and not rule and (not target or not day.isoformat() < target <= (day + timedelta(days=30)).isoformat()):
                    continue
                if view == "inbox" and target:
                    continue
                plan = plans.get(task["goal_id"])
                title_context = task["title"] + " " + task["notes"] + " " + (plan["title"] if plan else "") + " " + (task["project_id"] or "")
                if query.casefold() not in title_context.casefold() or (status == "open" and done) or (status == "done" and not done):
                    continue
                result.append(self._action_row(task, rule, target, done, day, plan))
        return sorted(result, key=self._action_sort_key)

    def project_tasks(self, project_id: str, project_root: str) -> list[dict]:
        """Combine project checklist tasks and personal actions for one project view."""
        tasks = self.store.planned_tasks(project_id=project_id, project_root=project_root)
        personal = self.actions(project_id=project_id, project_root=project_root)
        linked = [{**row, "personal": True} for row in personal]
        return sorted([task for task in tasks if not is_personal_action(task)] + linked,
                      key=lambda task: task.get("done", task["status"] == "done"))

    def project_linked_record_counts(self, project_id: str, project_root: str) -> tuple[int, int]:
        """Count task and plan rows that project deletion will leave behind."""
        with self.store.connect() as db:
            tasks = db.execute(
                "SELECT COUNT(*) FROM planned_tasks WHERE project_id=? AND (project_root=? OR project_root IS NULL OR project_root='')",
                (project_id, project_root),
            ).fetchone()[0]
            plans = db.execute(
                "SELECT COUNT(*) FROM personal_plans WHERE project_id=? AND (project_root=? OR project_root IS NULL OR project_root='')",
                (project_id, project_root),
            ).fetchone()[0]
        return tasks, plans

    @staticmethod
    def _action_sort_key(item):
        return (item["done"], item["occurrence_day"] or "9999",
                {"high": 0, "medium": 1, "low": 2}.get(item["priority"], 3), item["title"].casefold())

    @staticmethod
    def _action_row(task, rule, target, done, day, plan):
        return {**task, "occurrence_day": target, "done": done,
                "repeat": rule["frequency"] if rule else "", "plan_title": plan["title"] if plan else "",
                "overdue": bool(target and target < day.isoformat() and not done and not rule)}

    def period_actions(self, start: date, end: date) -> list[dict]:
        """Expand each scheduled occurrence in the period, including past check-ins."""
        if start > end or (end - start).days > 366:
            raise UserError("请选择一年以内的复盘区间。")
        rules = self.rules()
        plans = {item["id"]: item for item in self.plans()}
        start_key, end_key = start.isoformat(), end.isoformat()
        with self.store.connect() as db:
            completed = {(row["task_id"], row["day"]) for row in db.execute(
                "SELECT task_id,day FROM action_occurrences WHERE day BETWEEN ? AND ?",
                (start_key, end_key))}
        result, order_keys = [], {}
        occurrence_days_by_rule = {}
        for task in self.store.planned_tasks():
            if not is_personal_action(task):
                continue
            rule = rules.get(task["id"])
            plan = plans.get(task["goal_id"])
            if not rule:
                if task["due_date"] and start_key <= task["due_date"] <= end_key:
                    row = self._action_row(task, None, task["due_date"], task["status"] == "done", start, plan)
                    result.append(row)
                    order_keys[task["id"]] = self._action_sort_key(row)
                continue
            if rule["start_date"] > end_key or (rule.get("end_date") and rule["end_date"] < start_key):
                continue
            rule_key = (rule["start_date"], rule.get("end_date"), rule["frequency"],
                        json.dumps(rule.get("weekdays", []), sort_keys=True), rule.get("month_day"))
            occurrence_days = occurrence_days_by_rule.get(rule_key)
            if occurrence_days is None:
                matching_days = []
                for offset in range((end - start).days + 1):
                    target = start + timedelta(days=offset)
                    if self.occurs(rule, target):
                        matching_days.append(target)
                occurrence_days = tuple(matching_days)
                occurrence_days_by_rule[rule_key] = occurrence_days
            first = len(result)
            for target in occurrence_days:
                day = target.isoformat()
                result.append(self._action_row(task, rule, day, (task["id"], day) in completed, start, plan))
            if len(result) > first:
                anchor = (self._action_row(task, rule, task["due_date"], True, start, plan)
                          if task["status"] == "done" else result[first])
                order_keys[task["id"]] = self._action_sort_key(anchor)
        return sorted(result, key=lambda row: (row["occurrence_day"], row["title"], order_keys[row["id"]]))

    @staticmethod
    def review_periods(day: date) -> tuple[tuple[date, date, str], tuple[date, date, str]]:
        """Return the ISO week and calendar month containing ``day``."""
        week_start = day - timedelta(days=day.weekday())
        week_end = week_start + timedelta(days=6)
        month_start = day.replace(day=1)
        next_month = (date(day.year + 1, 1, 1) if day.month == 12
                      else date(day.year, day.month + 1, 1))
        month_end = next_month - timedelta(days=1)
        iso_week = week_start.isocalendar()
        return (
            (week_start, week_end, f"weekly:{iso_week.year}-W{iso_week.week:02d}"),
            (month_start, month_end, f"monthly:{month_start:%Y-%m}"),
        )

    @staticmethod
    def review_period_summary(start: date, end: date, period_rows: list[dict], plans: list[dict]) -> dict:
        """Aggregate scheduled occurrences and linked plans for a review interval."""
        if start > end:
            raise UserError("复盘开始日期不能晚于结束日期。")
        start_key, end_key = start.isoformat(), end.isoformat()
        rows = [row for row in period_rows
                if row.get("occurrence_day") and start_key <= row["occurrence_day"] <= end_key]
        plan_counts: dict[str, tuple[int, int]] = {}
        project_counts: dict[str, tuple[int, int]] = {}
        for row in rows:
            goal_id = row.get("goal_id")
            if goal_id:
                done, total = plan_counts.get(goal_id, (0, 0))
                plan_counts[goal_id] = (done + bool(row["done"]), total + 1)
            project_id = row.get("project_id")
            if project_id:
                done, total = project_counts.get(project_id, (0, 0))
                project_counts[project_id] = (done + bool(row["done"]), total + 1)
        linked_ids = {row.get("goal_id") for row in rows if row.get("goal_id")}
        linked_plans = [plan for plan in plans if plan["id"] in linked_ids or
                        (plan.get("due_date") and start_key <= plan["due_date"] <= end_key)]
        return {
            "rows": rows,
            "done": sum(bool(row["done"]) for row in rows),
            "total": len(rows),
            "plans": linked_plans,
            "plan_counts": sorted(((identity, done, total) for identity, (done, total) in plan_counts.items()),
                                  key=lambda item: -item[2])[:6],
            "project_counts": sorted(((identity, done, total) for identity, (done, total) in project_counts.items()),
                                     key=lambda item: -item[2])[:6],
        }

    def rhythm(self, start: date, end: date, *, today: date | None = None) -> list[dict]:
        """Planned and completed volume per day, read in one pass for a short range."""
        if start > end or (end - start).days > 62:
            raise UserError("节奏视图最多读取两个月。")
        today = today or date.today()
        rules = self.rules()
        tasks = [task for task in self.store.planned_tasks() if is_personal_action(task)]
        with self.store.connect() as db:
            checked = {(row["task_id"], row["day"]) for row in db.execute(
                "SELECT task_id,day FROM action_occurrences WHERE day BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()))}
        result = []
        for offset in range((end - start).days + 1):
            target = start + timedelta(days=offset)
            key = target.isoformat()
            planned = done = 0
            for task in tasks:
                rule = rules.get(task["id"])
                if rule:
                    if not self.occurs(rule, target):
                        continue
                    planned += 1
                    done += (task["id"], key) in checked
                elif task["due_date"] == key:
                    planned += 1
                    done += task["status"] == "done"
            result.append({"day": key, "planned": planned, "done": done,
                           "label": "一二三四五六日"[target.weekday()],
                           "today": target == today})
        return result

    def import_legacy_plans(self, root, project_root=None) -> int:
        """Copy old planning records once; keep all original Markdown untouched."""
        from .planbook import PlanBook
        marker = "personal_plans_import:" + str(root.resolve())
        if self.store.setting(marker, False):
            return 0
        report = PlanBook(root).scan()
        if report["errors"]:
            raise UserError("旧计划读取未完成，原资料保留：" + "；".join(report["errors"][:3]))
        count = 0
        timestamp = now()
        with self.store.connect() as db:
            for item in report["items"]:
                meta = item["metadata"]
                body = item["body"].removeprefix("\n")
                project_value = meta.get("project_id")
                if project_value is not None and not isinstance(project_value, str):
                    raise UserError("旧计划关联项目编号无效，请先检查：" + item["title"])
                project = (project_value.strip() or None) if project_value is not None else None
                if item["type"] in {"goal", "plan"}:
                    start = str(meta.get("start") or "").strip() or None
                    due = str(meta.get("due") or "").strip() or None
                    if start:
                        date.fromisoformat(start)
                    if due:
                        date.fromisoformat(due)
                    if start and due and start > due:
                        raise UserError("旧计划的开始日期晚于目标日期，请先检查：" + item["title"])
                    status = meta.get("status", "planned")
                    if not isinstance(status, str) or status not in {"planned", "active", "done", "archived"}:
                        raise UserError("旧计划状态无效，请先检查：" + item["title"])
                    parent_id = str(meta.get("parent_id") or "").strip() or None
                    if parent_id and len(parent_id) > 120:
                        raise UserError("旧计划关联编号过长，请先检查：" + item["title"])
                    progress = meta.get("progress", 0)
                    if type(progress) is not int or not 0 <= progress <= 100:
                        raise UserError("旧目标进度无效，请先检查：" + item["title"])
                    cursor = db.execute("""INSERT OR IGNORE INTO personal_plans
                        (id,title,due_date,project_id,project_root,notes,status,origin_path,start_date,parent_id,
                         progress,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item["id"], item["title"], due, project,
                         str(project_root) if project and project_root else None, body, status,
                         item["path"], start, parent_id, progress, timestamp, timestamp))
                    count += cursor.rowcount
                elif item["type"] == "template":
                    db.execute("INSERT OR IGNORE INTO personal_plan_templates VALUES (?,?,?)", (item["id"], item["title"], body))
                elif item["type"] == "recurring":
                    start = meta.get("start")
                    frequency = meta.get("frequency")
                    due_value = meta.get("due")
                    status = meta.get("status", "planned")
                    if type(start) is date:
                        start = start.isoformat()
                    if type(due_value) is date:
                        due_value = due_value.isoformat()
                    if (not isinstance(start, str) or not start
                            or not isinstance(frequency, str) or frequency not in {"daily", "weekly", "monthly"}):
                        raise UserError("旧重复规则缺少有效开始日期或频率，请先检查：" + item["title"])
                    try:
                        start_day = date.fromisoformat(start)
                        due_day = date.fromisoformat(due_value) if isinstance(due_value, str) and due_value else None
                    except ValueError as exc:
                        raise UserError("旧重复规则日期无效，请先检查：" + item["title"]) from exc
                    if due_value not in (None, "") and due_day is None:
                        raise UserError("旧重复规则截止日期无效，请先检查：" + item["title"])
                    if due_day and due_day < start_day:
                        raise UserError("旧重复规则截止日期早于开始日期，请先检查：" + item["title"])
                    if not isinstance(status, str) or status not in {"planned", "active", "done", "archived"}:
                        raise UserError("旧重复规则状态无效，请先检查：" + item["title"])
                    identity = "repeat-" + item["id"]
                    weekdays = meta.get("weekdays") or []
                    if (not isinstance(weekdays, list)
                            or any(type(value) is not int or value not in range(1, 8) for value in weekdays)
                            or (frequency == "weekly" and not weekdays)):
                        raise UserError("旧周期计划的重复星期无效，请先检查：" + item["title"])
                    month_day = meta.get("month_day")
                    if month_day is not None and (type(month_day) is not int or month_day not in range(1, 32)):
                        raise UserError("旧周期计划的每月日期无效，请先检查：" + item["title"])
                    completed_dates = meta.get("completed_dates", [])
                    if not isinstance(completed_dates, list) or len(completed_dates) > 5000:
                        raise UserError("旧周期计划的完成记录无效，请先检查：" + item["title"])
                    completed_dates = sorted({date.fromisoformat(str(value)).isoformat() for value in completed_dates})
                    db.execute("""INSERT OR IGNORE INTO planned_tasks
                        (id,title,kind,due_date,project_id,project_root,status,notes,source_ref,created_at,updated_at)
                        VALUES (?,?,'task',?,?,?,?,?,?,?,?)""",
                        (identity, item["title"], start, project, str(project_root) if project and project_root else None,
                         "done" if status in {"done", "archived"} else "planned", body, "personal:" + identity, timestamp, timestamp))
                    db.execute("INSERT OR IGNORE INTO action_repeat_rules (task_id,frequency,start_date,end_date,weekdays,month_day) VALUES (?,?,?,?,?,?)",
                               (identity, frequency, start, due_day.isoformat() if due_day else None, json.dumps(weekdays), month_day))
                    for value in completed_dates:
                        db.execute("INSERT OR IGNORE INTO action_occurrences VALUES (?,?,?)", (identity, value, timestamp))
                    count += 1
                elif item["type"] == "review":
                    period_type = meta.get("summary_period", "weekly")
                    start = str(meta.get("start") or "").strip()
                    end = str(meta.get("due") or "").strip()
                    if not isinstance(period_type, str) or period_type not in {"weekly", "monthly"} or not start or not end:
                        raise UserError("旧复盘缺少有效的周期或日期范围，请先检查：" + item["title"])
                    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
                    if start_day > end_day:
                        raise UserError("旧复盘日期范围无效，请先检查：" + item["title"])
                    if period_type == "weekly":
                        week = start_day.isocalendar()
                        period_key = f"weekly:{week.year}-W{week.week:02d}"
                    else:
                        period_key = f"monthly:{start_day:%Y-%m}"
                    if db.execute("SELECT 1 FROM personal_reviews WHERE period_key=?", (period_key,)).fetchone():
                        period_key = "legacy:" + item["id"]
                    cursor = db.execute("""INSERT OR IGNORE INTO personal_reviews
                        (id,period_key,period_type,title,start_date,end_date,body,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (item["id"], period_key, period_type, item["title"], start, end,
                         body, str(meta.get("created") or timestamp),
                         str(meta.get("updated") or timestamp)))
                    count += cursor.rowcount
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (marker, "true"))
        return count
