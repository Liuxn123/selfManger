from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .domain import Resource, UserError
from .task_ordering import order_project_ai_tasks
from .project_task_transactions import project_task_mutation
from .portable import application_directory, portable_directory, encode_paths, decode_paths


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_data_directory() -> Path | None:
    """Explicit local source configuration; never silently create a replacement DB."""
    if getattr(sys, "frozen", False):
        return None
    config = application_directory() / "local-config.json"
    if not config.is_file():
        return None
    try:
        value = json.loads(config.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError()
        location = value.get("data_directory")
        if not isinstance(location, str) or not location.strip():
            raise ValueError()
        directory = (config.parent / Path(location).expanduser()).resolve()
        if value.get("require_existing_database") and not (directory / "manager.sqlite3").is_file():
            raise UserError("本机配置指定的数据库不存在，请检查 data 目录或恢复快照；未创建空数据库。")
        return directory
    except (OSError, ValueError, TypeError) as exc:
        raise UserError("local-config.json 数据位置配置无效，请核对本机配置。") from exc


def data_directory() -> Path:
    if override := os.environ.get("SELFMANGER_DATA_DIR"):
        return Path(override).expanduser().resolve()
    if configured := source_data_directory():
        return configured
    if portable := portable_directory():
        return portable / "data"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "selfManger"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/selfManger"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "selfManger"


def validate_public_config(value: Any) -> None:
    """Reject credential values; paths and OS credential identifiers are permitted."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {"password", "passphrase", "token", "api_key", "secret", "private_key", "access_token"}:
                raise UserError("配置不能保存明文凭据，请使用凭据引用或受保护文件。")
            validate_public_config(item)
    elif isinstance(value, list):
        for item in value:
            validate_public_config(item)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise UserError("配置包含不支持的值。")


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or data_directory()).resolve()
        self.portable_root = self.root.parent if (self.root.parent / "portable.json").is_file() else None
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "manager.sqlite3"
        self._local = threading.local()
        self.session_id = uuid4().hex
        self._migrate()
        from .document_transactions import recover_documents
        recover_documents(self)

    # user_version 3/5 were historically re-stamped on every start while tables and columns kept
    # being added afterwards, so 5 does not prove the schema is complete; 6 does.
    SCHEMA_VERSION = 10

    def _migrate(self) -> None:
        with self.connect() as db:
            if db.execute("PRAGMA application_id").fetchone()[0] == 1397573458:
                raise UserError("这是已隔离的恢复目录，原数据库保存在 manager.sqlite3.recovered。请在数据安全页使用“准备可用数据目录…”生成新目录后再启动；勿直接改名数据库。")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise UserError("数据库来自更新版本，请使用对应版本打开；当前程序未修改数据库。")
            if version == self.SCHEMA_VERSION:
                return
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (key TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, resource_id TEXT, title TEXT, state TEXT,
                    started_at TEXT, finished_at TEXT, log TEXT, result TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_resource ON tasks(resource_id);
                CREATE TABLE IF NOT EXISTS planned_tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('task', 'schedule')),
                    due_date TEXT,
                    project_id TEXT,
                    project_root TEXT,
                    goal_id TEXT,
                    priority TEXT NOT NULL DEFAULT '' CHECK(priority IN ('', 'high', 'medium', 'low')),
                    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'active', 'done')),
                    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('ai', 'manual')),
                    notes TEXT NOT NULL DEFAULT '',
                    completion_note TEXT NOT NULL DEFAULT '',
                    source_ref TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS planned_tasks_due ON planned_tasks(due_date, status);
                CREATE INDEX IF NOT EXISTS planned_tasks_project ON planned_tasks(project_id, status);
                CREATE INDEX IF NOT EXISTS planned_tasks_source ON planned_tasks(source, status);
                PRAGMA user_version=3;
            """)
        with self.connect() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(planned_tasks)")}
        if "source_ref" not in columns:
            with self.connect() as db:
                db.execute("ALTER TABLE planned_tasks ADD COLUMN source_ref TEXT")
        if "goal_id" not in columns:
            with self.connect() as db:
                db.execute("ALTER TABLE planned_tasks ADD COLUMN goal_id TEXT")
        if "project_root" not in columns:
            with self.connect() as db:
                db.execute("ALTER TABLE planned_tasks ADD COLUMN project_root TEXT")
        if "completion_note" not in columns:
            with self.connect() as db:
                db.execute("ALTER TABLE planned_tasks ADD COLUMN completion_note TEXT NOT NULL DEFAULT ''")
        with self.connect() as db:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS planned_tasks_source_ref ON planned_tasks(source_ref)")
            db.execute("CREATE INDEX IF NOT EXISTS planned_tasks_goal ON planned_tasks(goal_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS planned_tasks_project_root ON planned_tasks(project_root, project_id, status)")
            db.execute("PRAGMA user_version=5")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS personal_plans (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, due_date TEXT,
                    project_id TEXT, project_root TEXT, notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned', origin_path TEXT,
                    start_date TEXT, parent_id TEXT, progress INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_repeat_rules (
                    task_id TEXT PRIMARY KEY, frequency TEXT NOT NULL,
                    start_date TEXT NOT NULL, end_date TEXT, weekdays TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS action_occurrences (
                    task_id TEXT NOT NULL, day TEXT NOT NULL, completed_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, day)
                );
                CREATE TABLE IF NOT EXISTS personal_plan_templates (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS personal_reviews (
                    id TEXT PRIMARY KEY, period_key TEXT NOT NULL UNIQUE,
                    period_type TEXT NOT NULL CHECK(period_type IN ('weekly', 'monthly')),
                    title TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS personal_work_notes (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL,
                    source_ref TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS personal_markdown_documents (
                    entity_type TEXT NOT NULL CHECK(entity_type IN ('action', 'plan', 'review')),
                    entity_id TEXT NOT NULL, module TEXT NOT NULL, root_path TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(entity_type, entity_id)
                );
            """)

        with self.connect() as db:
            repeat_columns = {row[1] for row in db.execute("PRAGMA table_info(action_repeat_rules)")}
            if "month_day" not in repeat_columns:
                db.execute("ALTER TABLE action_repeat_rules ADD COLUMN month_day INTEGER")
            task_columns = {row[1] for row in db.execute("PRAGMA table_info(planned_tasks)")}
            if "scheduled_time" not in task_columns:
                db.execute("ALTER TABLE planned_tasks ADD COLUMN scheduled_time TEXT NOT NULL DEFAULT ''")
            plan_columns = {row[1] for row in db.execute("PRAGMA table_info(personal_plans)")}
            if "start_date" not in plan_columns:
                db.execute("ALTER TABLE personal_plans ADD COLUMN start_date TEXT")
            if "parent_id" not in plan_columns:
                db.execute("ALTER TABLE personal_plans ADD COLUMN parent_id TEXT")
            if "progress" not in plan_columns:
                db.execute("ALTER TABLE personal_plans ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS planning_deletions (
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                deleted_at TEXT NOT NULL, PRIMARY KEY(entity_type,entity_id))""")
            db.execute("""
                UPDATE planned_tasks
                SET goal_id=NULL
                WHERE project_id IS NOT NULL AND trim(project_id)<>''
                  AND lower(trim(goal_id))='none'
            """)
            db.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        shared = getattr(self._local, "connection", None)
        if shared is not None:
            yield shared
            return
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def atomic(self) -> Iterator[sqlite3.Connection]:
        """Serialize compare-and-update and share one transaction on this thread."""
        shared = getattr(self._local, "connection", None)
        if shared is not None:
            yield shared
            return
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            self._local.connection = db
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                del self._local.connection
        finally:
            db.close()

    def resources(self) -> list[Resource]:
        with self.connect() as db:
            return [Resource.from_dict(decode_paths(json.loads(row[0]), self.portable_root)) for row in db.execute("SELECT document FROM resources ORDER BY rowid")]

    def save_resource(self, resource: Resource) -> None:
        Resource.from_dict(resource.to_dict())
        validate_public_config(resource.options)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO resources VALUES (?, ?)", (resource.id, json.dumps(encode_paths(resource.to_dict(), self.portable_root), ensure_ascii=False)))

    def remove_resource(self, identity: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM resources WHERE id=?", (identity,))

    def setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return decode_paths(json.loads(row[0]), self.portable_root) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        validate_public_config({key: value})
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(encode_paths(value, self.portable_root), ensure_ascii=False)))

    def evidence(self, key: str) -> dict:
        key = str(encode_paths(key, self.portable_root))
        with self.connect() as db:
            row = db.execute("SELECT document FROM evidence WHERE key=?", (key,)).fetchone()
        if not row:
            return {}
        report = decode_paths(json.loads(row[0]), self.portable_root)
        return report if isinstance(report, dict) else {}

    def save_evidence(self, key: str, report: dict) -> None:
        from .security import safe_result
        key = str(encode_paths(key, self.portable_root))
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO evidence VALUES (?, ?)", (key, json.dumps(encode_paths(safe_result(report), self.portable_root), ensure_ascii=False)))

    def personal_work_notes(self) -> list[dict[str, str | None]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,body,created_at,source_ref FROM personal_work_notes ORDER BY created_at,rowid")]

    def add_personal_work_note(self, body: str, *, source_ref: str | None = None) -> dict[str, str | None]:
        identity = uuid4().hex
        timestamp = now()
        with self.atomic() as db:
            if source_ref:
                existing = db.execute(
                    "SELECT id,body,created_at,source_ref FROM personal_work_notes WHERE source_ref=?",
                    (source_ref,),
                ).fetchone()
                if existing:
                    return dict(existing)
            db.execute(
                "INSERT INTO personal_work_notes (id,body,created_at,source_ref) VALUES (?,?,?,?)",
                (identity, body, timestamp, source_ref),
            )
            row = db.execute(
                "SELECT id,body,created_at,source_ref FROM personal_work_notes WHERE id=?", (identity,)
            ).fetchone()
            return dict(row)

    def export_config(self, destination: Path) -> None:
        payload = {"schema_version": 1, "resources": [r.to_dict() for r in self.resources()]}
        validate_public_config(payload)
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_config(self, source: Path) -> int:
        if source.stat().st_size > 2_000_000:
            raise UserError("配置文件过大。")
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("resources"), list):
            raise UserError("不支持的配置格式。")
        validate_public_config(data)
        resources = [Resource.from_dict(item) for item in data["resources"]]
        if len(resources) > 1000:
            raise UserError("资源数量过多。")
        with self.connect() as db:
            count = 0
            for resource in resources:
                count += db.execute("INSERT OR IGNORE INTO resources VALUES (?, ?)", (resource.id, json.dumps(encode_paths(resource.to_dict(), self.portable_root), ensure_ascii=False))).rowcount
        return count

    def recover_interrupted(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET state='interrupted', finished_at=? WHERE state IN ('running','queued')", (now(),))

    def start_task(self, identity: str, resource_id: str, title: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO tasks VALUES (?, ?, ?, 'running', ?, NULL, '', '{}')", (identity, resource_id, title, now()))

    def append_log(self, identity: str, message: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET log=substr(log || ?, -40000) WHERE id=?", (f"{now()}  {message}\n", identity))

    def finish_task(self, identity: str, state: str, result: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?, finished_at=?, result=? WHERE id=?", (state, now(), json.dumps(result, ensure_ascii=False), identity))

    def tasks(self, limit: int = 100, resource_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if resource_id is not None:
                return [dict(row) for row in db.execute("SELECT * FROM tasks WHERE resource_id=? ORDER BY rowid DESC LIMIT ?", (resource_id, limit))]
            return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,))]

    def task_summaries(self, limit: int = 100, resource_id: str | None = None) -> list[dict[str, Any]]:
        """List rows without loading every historical log and result body."""
        columns = "id, resource_id, title, state, started_at, finished_at"
        with self.connect() as db:
            if resource_id is not None:
                return [dict(row) for row in db.execute(f"SELECT {columns} FROM tasks WHERE resource_id=? ORDER BY rowid DESC LIMIT ?", (resource_id, limit))]
            return [dict(row) for row in db.execute(f"SELECT {columns} FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,))]

    def task(self, identity: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (identity,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _validate_planned_task(values: dict[str, Any]) -> dict[str, str | None]:
        title = str(values.get("title", "")).strip()
        if not title or len(title) > 240 or "\n" in title or "\r" in title:
            raise UserError("任务名称必须为 1–240 字的一行文本。")
        kind = str(values.get("kind") or "task")
        if kind not in {"task", "schedule"}:
            raise UserError("任务类型无效。")
        due_date = values.get("due_date")
        due_date = str(due_date).strip() if due_date else None
        if due_date:
            try:
                date.fromisoformat(due_date)
            except ValueError as exc:
                raise UserError("日期请使用 YYYY-MM-DD 格式。") from exc
        project_id = str(values.get("project_id") or "").strip() or None
        if project_id and len(project_id) > 120:
            raise UserError("项目编号过长。")
        project_root = str(values.get("project_root") or "").strip() or None
        if project_root and len(project_root) > 4000:
            raise UserError("项目工作区路径过长。")
        goal_id = str(values.get("goal_id") or "").strip() or None
        if goal_id and len(goal_id) > 120:
            raise UserError("目标编号过长。")
        priority = str(values.get("priority", ""))
        if priority not in {"", "high", "medium", "low"}:
            raise UserError("优先级无效。")
        status = str(values.get("status", "planned"))
        if status not in {"planned", "active", "done"}:
            raise UserError("任务状态无效。")
        source = str(values.get("source", "manual"))
        if source not in {"ai", "manual"}:
            raise UserError("任务来源无效。")
        notes = str(values.get("notes", ""))
        if len(notes) > 20_000:
            raise UserError("任务备注不能超过 20000 字。")
        scheduled_time = str(values.get("scheduled_time", "")).strip().replace("：", ":").replace("—", "–").replace("-", "–")
        clock = r"(?:[01]\d|2[0-3]):[0-5]\d"
        if scheduled_time and not re.fullmatch(clock + r"(?:\s*–\s*" + clock + r")?", scheduled_time):
            raise UserError("时间使用 09:00；日程也可填写 09:00–10:00。")
        return {"title": title, "kind": kind, "due_date": due_date,
                "project_id": project_id, "project_root": project_root, "goal_id": goal_id,
                "priority": priority, "status": status,
                "source": source, "notes": notes, "scheduled_time": scheduled_time}

    @project_task_mutation
    def add_planned_task(self, values: dict[str, Any], source_ref: str | None = None) -> dict[str, Any] | None:
        """Create one shared, date-precise task or schedule record."""
        clean = self._validate_planned_task(values)
        identity, timestamp = uuid4().hex, now()
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO planned_tasks
                   (id, title, kind, due_date, project_id, project_root, goal_id, priority, status, source, notes, scheduled_time, source_ref, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identity, clean["title"], clean["kind"], clean["due_date"], clean["project_id"], clean["project_root"], clean["goal_id"],
                 clean["priority"], clean["status"], clean["source"], clean["notes"], clean["scheduled_time"], source_ref, timestamp, timestamp),
            )
        created = self.planned_task(identity)
        if created:
            return created
        with self.connect() as db:
            row = db.execute("""SELECT * FROM planned_tasks WHERE source_ref=? AND NOT EXISTS
                (SELECT 1 FROM planning_deletions WHERE entity_type='action' AND entity_id=planned_tasks.id)""", (source_ref,)).fetchone()
        return dict(row) if row else None

    @project_task_mutation
    def update_planned_task(self, identity: str, values: dict[str, Any], *, manual_override: bool = False) -> dict[str, Any]:
        clean = self._validate_planned_task(values)
        existing = self.planned_task(identity)
        if not existing:
            raise UserError("任务已不存在，请刷新后重试。")
        if values.get("_expected") is not None and existing != values["_expected"]:
            raise UserError("任务已被其他操作修改，请保留草稿并刷新后重试。")
        if existing["source"] == "ai" and clean["source"] != "ai":
            raise UserError("AI 规划任务不能通过编辑改为手动来源。")
        if existing["source"] == "ai" and existing["status"] != "done" and clean["status"] == "done" and not manual_override:
            raise UserError("AI 规划任务必须由 Agent 执行并验证后才能完成。")
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE planned_tasks SET title=?, kind=?, due_date=?, project_id=?, project_root=?, goal_id=?, priority=?,
                   status=?, source=?, notes=?, scheduled_time=?, updated_at=? WHERE id=?""",
                (clean["title"], clean["kind"], clean["due_date"], clean["project_id"], clean["project_root"], clean["goal_id"],
                 clean["priority"], clean["status"], clean["source"], clean["notes"], clean["scheduled_time"], now(), identity),
            )
            if not cursor.rowcount:
                raise UserError("任务已不存在，请刷新后重试。")
        return self.planned_task(identity)

    @project_task_mutation
    def set_planned_task_status(self, identity: str, status: str,
                                manual_override: bool = False, expected: dict | None = None) -> dict[str, Any]:
        if status not in {"planned", "active", "done"}:
            raise UserError("任务状态无效。")
        existing = self.planned_task(identity)
        if not existing:
            raise UserError("任务已不存在，请刷新后重试。")
        if existing["source"] == "ai" and status == "done" and not manual_override:
            raise UserError("AI 规划任务必须由 Agent 执行并验证后才能完成。")
        with self.connect() as db:
            if expected is not None:
                if not db.in_transaction:
                    db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM planned_tasks WHERE id=?", (identity,)).fetchone()
                if not current or dict(current) != expected or db.execute(
                        "SELECT 1 FROM planning_deletions WHERE entity_type='action' AND entity_id=?", (identity,)).fetchone():
                    raise UserError("任务已变化，请刷新后再修改状态。")
            cursor = db.execute("UPDATE planned_tasks SET status=?, updated_at=? WHERE id=?", (status, now(), identity))
            if not cursor.rowcount:
                raise UserError("任务已不存在，请刷新后重试。")
        return self.planned_task(identity)

    @project_task_mutation
    def complete_ai_planned_task(self, identity: str, completion_note: str) -> dict[str, Any]:
        """Persist the agent's verified completion record and complete the AI task."""
        completion_note = str(completion_note or "").strip()
        if not completion_note:
            raise UserError("AI 必须提供完成记录后才能完成任务。")
        if len(completion_note) > 6000:
            raise UserError("AI 完成记录不能超过 6000 字。")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE planned_tasks SET status='done', completion_note=?, updated_at=? "
                "WHERE id=? AND source='ai' AND status='active'",
                (completion_note, now(), identity),
            )
            if not cursor.rowcount:
                raise UserError("只有进行中的 AI 规划任务可以由 Agent 标记完成；请刷新任务后重试。")
        return self.planned_task(identity)

    @project_task_mutation
    def delete_planned_task(self, identity: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM planned_tasks WHERE id=?", (identity,))

    def planned_task(self, identity: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("""SELECT * FROM planned_tasks WHERE id=? AND NOT EXISTS
                (SELECT 1 FROM planning_deletions WHERE entity_type='action' AND entity_id=planned_tasks.id)""", (identity,)).fetchone()
        return dict(row) if row else None

    def planned_task_source_refs(self) -> set[str]:
        with self.connect() as db:
            return {row[0] for row in db.execute("SELECT source_ref FROM planned_tasks WHERE source_ref IS NOT NULL")}

    def planned_task_goal_progress(self) -> dict[str, dict[str, int]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT goal_id, COUNT(*) AS total,
                          SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done
                   FROM planned_tasks WHERE goal_id IS NOT NULL AND NOT EXISTS
                   (SELECT 1 FROM planning_deletions WHERE entity_type='action' AND entity_id=planned_tasks.id)
                   GROUP BY goal_id"""
            ).fetchall()
        return {row["goal_id"]: {"total": row["total"], "done": row["done"] or 0} for row in rows}

    def planned_tasks(self, *, due_date: str | None = None, project_id: str | None = None,
                      project_root: str | None = None,
                      goal_id: str | None = None, status: str | None = None, source: str | None = None,
                      include_undated: bool = False, limit: int = 5000) -> list[dict[str, Any]]:
        clauses, args = ["NOT EXISTS (SELECT 1 FROM planning_deletions WHERE entity_type='action' AND entity_id=planned_tasks.id)"], []
        if due_date is not None:
            clauses.append("(due_date=? OR (? AND due_date IS NULL))")
            args.extend((due_date, int(include_undated)))
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if project_root is not None:
            clauses.append("project_root=?")
            args.append(project_root)
        if goal_id is not None:
            clauses.append("goal_id=?")
            args.append(goal_id)
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        if source is not None:
            clauses.append("source=?")
            args.append(source)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        maximum = max(1, min(int(limit), 5000))
        project_order = project_id is not None and source in (None, "ai")
        query = "SELECT * FROM planned_tasks" + where + " ORDER BY due_date IS NULL, due_date, created_at DESC"
        # Project numbering must be applied before limiting, otherwise a recent
        # batch (e.g. 32, 33) can displace 01 even in a small project preview.
        if not project_order:
            query += " LIMIT ?"
            args.append(maximum)
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        tasks = [dict(row) for row in rows]
        if project_order:
            tasks = order_project_ai_tasks(tasks)
        return tasks[:maximum]
