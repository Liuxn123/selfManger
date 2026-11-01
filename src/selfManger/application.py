from __future__ import annotations

import shutil
import os
import sys
import tempfile
import hashlib
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from . import archives
from .adapters.folder import FolderAdapter, ScriptAdapter
from .adapters.projects import ProjectAdapter, VaultAdapter
from .domain import AdapterRegistry, Resource, RestorePlan, UserError
from .runtime import ResourceLocks, TaskContext, run_process
from .security import SecretStore
from .storage import Store
from .storage import now
from .maintenance import backup_health, backup_retention_count, fingerprint, retention_candidates

MANAGER_BACKUP_ID = "selfMangerDatabase"
OVERALL_BACKUP_ID = "selfMangerOverall"

def build_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("project", ProjectAdapter())
    registry.register("folder", FolderAdapter())
    registry.register("script", ScriptAdapter())
    registry.register("vault", VaultAdapter())
    return registry


class ApplicationService:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.registry = build_registry()
        self.locks = ResourceLocks()
        self.secrets = SecretStore(store.portable_root)
        try:
            self.common_password = self.secrets.get("common:backup") or ""
        except UserError:
            self.common_password = ""

    def backup_root(self) -> Path:
        default = (self.store.portable_root / "backups") if self.store.portable_root else self.store.root / "backups"
        return Path(self.store.setting("backup_root", str(default))).expanduser()

    def set_backup_root(self, value: str | Path) -> Path:
        """Persist a normalized destination for future backups."""
        root = Path(value).expanduser().resolve()
        self.store.set_setting("backup_root", str(root))
        return root

    def set_backup_retention(self, count: int) -> int:
        """Persist a retention count within the supported policy bounds."""
        from .maintenance import MIN_BACKUP_KEEP, MAX_BACKUP_KEEP
        normalized = max(MIN_BACKUP_KEEP, min(MAX_BACKUP_KEEP, int(count)))
        self.store.set_setting("backup_keep", normalized)
        return normalized

    def set_project_workspace_root(self, value: str | Path) -> Path:
        root = Path(value).expanduser().resolve()
        self.store.set_setting("project_workspace", str(root))
        return root

    def project_workspace_root(self) -> str:
        """Return the configured project workspace, or an empty string if unset."""
        return str(self.store.setting("project_workspace", "") or "")

    def developer_tools(self) -> list[dict]:
        """Return locally registered app and website shortcuts from SQLite settings."""
        rows = self.store.setting("developer_tools", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise UserError("工具登记数据格式无效；原数据库未修改。")
        return sorted((dict(row) for row in rows), key=lambda row: str(row.get("name", "")).casefold())

    def developer_tool(self, identity: str) -> dict | None:
        return next((row for row in self.developer_tools() if row.get("id") == identity), None)

    def save_developer_tool(self, values: dict, identity: str | None = None) -> dict:
        if not isinstance(values, dict):
            raise UserError("工具信息无效。")
        name = str(values.get("name", "")).strip()
        kind = str(values.get("kind", "")).strip()
        target = str(values.get("target", "")).strip()
        summary = str(values.get("summary", "")).strip()
        if not name or len(name) > 120:
            raise UserError("请填写工具名称（最多120字）。")
        if len(summary) > 500:
            raise UserError("用途说明最多500字。")
        if not target or len(target) > 2048 or any(ord(char) < 32 for char in target):
            raise UserError("请填写有效的启动目标。")
        if kind == "application":
            path = Path(target).expanduser()
            if path.suffix.casefold() not in {".exe", ".com", ".lnk"}:
                raise UserError("本地工具只支持 .exe、.com 或 .lnk 启动目标。")
            if not path.is_file():
                raise UserError("启动程序不存在或不是文件，请重新选择。")
            target = str(path.resolve())
        elif kind == "website":
            try:
                parsed = urlsplit(target)
                # Accessing hostname validates malformed bracketed IPv6 authorities.
                hostname = parsed.hostname
                parsed.port  # Validate the port before persisting the address.
            except ValueError as exc:
                raise UserError("网站地址格式无效。") from exc
            if parsed.scheme.casefold() not in {"http", "https"} or not hostname or parsed.username or parsed.password:
                raise UserError("网站目标必须是没有内嵌账号信息的 HTTP 或 HTTPS 地址。")
            sensitive_keys = {"token", "access_token", "access-token", "api_key", "api-key", "apikey",
                              "key", "secret", "client_secret", "client-secret", "auth", "authorization",
                              "password", "passwd"}
            fragment_query = parsed.fragment.partition("?")[2] or parsed.fragment
            parameters = [*parse_qsl(parsed.query, keep_blank_values=True),
                          *parse_qsl(fragment_query, keep_blank_values=True)]
            if any(key.casefold() in sensitive_keys for key, _ in parameters):
                raise UserError("网址查询参数不能包含口令或令牌；请先移除敏感参数。")
        else:
            raise UserError("工具类型无效。")

        rows = self.developer_tools()
        if identity and not any(row.get("id") == identity for row in rows):
            raise UserError("工具已不存在，请刷新后重试。")
        if any(row.get("id") != identity and str(row.get("name", "")).casefold() == name.casefold()
               for row in rows):
            raise UserError("已有同名工具，请使用不同名称。")
        previous = next((row for row in rows if row.get("id") == identity), None)
        record = {"id": identity or uuid4().hex, "name": name, "kind": kind, "target": target,
                  "summary": summary, "created_at": (previous or {}).get("created_at", now()), "updated_at": now()}
        rows = [row for row in rows if row.get("id") != record["id"]]
        rows.append(record)
        self.store.set_setting("developer_tools", rows)
        return record

    def remove_developer_tool(self, identity: str) -> None:
        rows = self.developer_tools()
        remaining = [row for row in rows if row.get("id") != identity]
        if len(remaining) == len(rows):
            raise UserError("工具已不存在，请刷新后重试。")
        self.store.set_setting("developer_tools", remaining)

    def setting(self, key: str, default=None):
        return self.store.setting(key, default)

    def resources(self) -> list[Resource]:
        return self.store.resources()

    def task_summaries(self, limit: int = 100, resource_id: str | None = None) -> list[dict]:
        return self.store.task_summaries(limit=limit, resource_id=resource_id)

    def _import_legacy_work_summary(self) -> None:
        from .work_summaries import LEGACY_WORK_SUMMARY_SOURCE, read_legacy_work_summary

        if any(row["source_ref"] == LEGACY_WORK_SUMMARY_SOURCE for row in self.store.personal_work_notes()):
            return
        legacy = read_legacy_work_summary(self.store.root)
        if legacy is not None:
            self.store.add_personal_work_note(legacy, source_ref=LEGACY_WORK_SUMMARY_SOURCE)

    def work_summaries(self) -> list[dict[str, str | None]]:
        """Read user-authored work notes from SQLite, importing the legacy file once."""
        self._import_legacy_work_summary()
        return self.store.personal_work_notes()

    def append_work_summary(self, text: str) -> list[dict[str, str | None]]:
        from .work_summaries import MAX_NOTE_CHARS, MAX_SUMMARY_BYTES

        self._import_legacy_work_summary()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_NOTE_CHARS:
            raise UserError("请填写工作总结，最多 20000 字。")
        rows = self.store.personal_work_notes()
        byte_count = sum(len(str(row["body"]).encode("utf-8")) for row in rows)
        try:
            byte_count += len(text.encode("utf-8"))
        except UnicodeError as exc:
            raise UserError("工作总结不是有效的 UTF-8 文本。") from exc
        if byte_count > MAX_SUMMARY_BYTES:
            raise UserError("总结已超过 2 MiB，请先归档文件再继续。")
        self.store.add_personal_work_note(text.strip())
        return self.store.personal_work_notes()

    def task(self, identity: str) -> dict | None:
        return self.store.task(identity)

    def evidence(self, identity: str) -> dict:
        return self.store.evidence(identity)

    @property
    def database_path(self) -> Path:
        return self.store.path

    def backup_health(self, resource: Resource, backup_root: Path) -> dict:
        return backup_health(self.store, resource, backup_root)

    def backup_versions(self, resource: Resource, backup_root: Path | None = None) -> list[dict]:
        """List a resource's backups with verification evidence checked against the current file."""
        root = backup_root or self.backup_root()
        versions = archives.list_archives(root, resource.id)
        for version in versions:
            path = Path(version["archive"])
            evidence = {}
            try:
                evidence = self.evidence(str(path.resolve()))
                unchanged = all(evidence.get(key) == value for key, value in fingerprint(path).items())
            except OSError:
                unchanged = False
            version["integrity"] = (
                "已演练" if unchanged and evidence.get("rehearsed_at")
                else "已校验" if unchanged and evidence.get("verified_at")
                else "待校验"
            )
        return versions

    def backup_inventory(self, resource: Resource, backup_root: Path | None = None) -> dict:
        """Read backup health and versions from one archive listing."""
        root = backup_root or self.backup_root()
        versions = self.backup_versions(resource, root)
        health = backup_health(self.store, resource, root, candidates=versions)
        return {"health": health, "versions": versions}

    def backup_due(self, resource: Resource) -> bool:
        from .maintenance import is_due
        return is_due(self.store, resource)

    def set_workbench_root(self, value: str | Path) -> Path:
        root = Path(value).expanduser().resolve()
        self.store.set_setting("workbench_root", str(root))
        return root

    def set_appearance(self, mode: str) -> str:
        if not isinstance(mode, str) or mode not in {"light", "dark"}:
            raise UserError("界面主题设置无效。")
        self.store.set_setting("appearance", mode)
        return mode

    def planning_draft(self):
        return self.store.setting("planning_markdown_draft_v1")

    def planned_task(self, identity: str) -> dict | None:
        """Read one current personal/project task through the application boundary."""
        return self.store.planned_task(identity)

    def save_planning_draft(self, value) -> None:
        if value is not None and not isinstance(value, dict):
            raise UserError("计划草稿格式无效。")
        self.store.set_setting("planning_markdown_draft_v1", value)

    def start_task(self, identity: str, resource_id: str, title: str) -> None:
        self.store.start_task(identity, resource_id, title)

    def append_log(self, identity: str, message: str) -> None:
        self.store.append_log(identity, message)

    def finish_task(self, identity: str, state: str, result: dict) -> None:
        self.store.finish_task(identity, state, result)

    def export_config(self, destination: Path) -> None:
        self.store.export_config(destination)

    def import_config(self, source: Path) -> int:
        return self.store.import_config(source)

    def record_project_used(self, identity: str) -> None:
        self.store.save_evidence("project-used:" + identity, {"at": now()})

    def manager_resource(self) -> Resource:
        return Resource("管家自身数据库", "folder", {"path": str(self.store.root)}, MANAGER_BACKUP_ID)

    def overall_resource(self) -> Resource:
        return Resource("数据库与 Markdown 整体备份", "folder", {"path": str(self.store.root)}, OVERALL_BACKUP_ID)

    def backup_resources(self) -> list[Resource]:
        # Derived from the active Store, so it cannot be removed or point at an old data directory.
        return [self.manager_resource(), self.overall_resource(), *[resource for resource in self.store.resources()
                                          if resource.id not in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}
                                          and resource.kind in {"project", "folder", "script", "vault"}]]

    def supports_resource_kind(self, kind: str) -> bool:
        return kind in self.registry.describe()

    def save_resource(self, resource: Resource) -> Resource:
        self.registry.for_kind(resource.kind)
        self.store.save_resource(resource)
        return resource

    def remove_resource(self, identity: str) -> None:
        if identity in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}:
            raise UserError("内置备份资源不能移除。")
        self.store.remove_resource(identity)

    def backup_overall(self, destination: Path, password: str, context: TaskContext) -> dict:
        from .combined_backup import capture
        self._import_legacy_work_summary()
        with tempfile.TemporaryDirectory(prefix="selfManger-overall-") as temporary:
            staging = Path(temporary)
            manifest = capture(self.store, staging, context)
            resource = Resource("数据库与 Markdown 整体备份", "folder", {"path": str(staging)}, OVERALL_BACKUP_ID)
            result = archives.create_archive(resource, destination, password, context)
            self.verify_backup(Path(result["archive"]), password, context)
        return {**result, "verified": True, "markdown_files": len(manifest["files"]) - 2}

    def backup_manager(self, destination: Path, password: str, context: TaskContext) -> dict:
        """Export a consistent database, without runtime locks or credential files."""
        from .snapshots import snapshot_file
        self._import_legacy_work_summary()
        destination = destination.expanduser().resolve()
        # Only the staged snapshot is archived; the common data/backups destination
        # is safe here because the live data directory is never traversed.
        with tempfile.TemporaryDirectory(prefix="selfManger-backup-") as temporary:
            source = Path(temporary)
            with snapshot_file(self.store.path, context) as (snapshot, _database):
                shutil.copy2(snapshot, source / "manager.sqlite3")
            (source / "恢复说明.txt").write_text(
                "这是 selfManger 管家数据库一致性快照，包含设置、资料登记、统一任务、工作笔记、知识索引和操作记录。\n"
                "知识文件、项目笔记和计划 Markdown 原件保存在各自目录，需另外登记备份。系统凭据不包含在内。\n"
                "恢复时先用数据安全页的‘从备份文件恢复’解密到新的空目录，退出管家，\n"
                "保留当前数据库副本后，再将 manager.sqlite3 放到管家使用的数据目录。不要覆盖运行中的数据库。\n",
                encoding="utf-8")
            resource = Resource("管家自身数据库", "folder", {"path": str(source)}, MANAGER_BACKUP_ID)
            result = archives.create_archive(resource, destination, password, context)
            self.verify_backup(Path(result["archive"]), password, context)
        return {**result, "verified": True}

    def lock_keys(self, resource: Resource, target: str = "") -> list[str]:
        keys = ["resource:" + resource.id]
        for name in ("path", "home", "backup_repo"):
            if resource.options.get(name):
                keys.append("path:" + os.path.normcase(str(Path(resource.options[name]).expanduser().resolve())))
        if target:
            keys.append("path:" + os.path.normcase(str(Path(target).expanduser().resolve())))
        return keys

    def observe(self, resource: Resource, context: TaskContext) -> dict:
        try:
            report = self.registry.get(resource).observe(resource, context)
        except UserError as exc:
            observed_at = now()
            failed = {"connected": False, "error": str(exc), "observed_at": observed_at}
            self.store.save_evidence("observe:" + resource.id, failed)
            raise
        observed = {**report, "observed_at": now()}
        self.store.save_evidence("observe:" + resource.id, observed)
        return report

    def action(self, resource: Resource, action: str, context: TaskContext) -> dict:
        adapter = self.registry.get(resource)
        if action not in adapter.capabilities or not hasattr(adapter, action):
            raise UserError("此资源尚未支持该操作。")
        with self.locks.acquire(self.lock_keys(resource)):
            report = getattr(adapter, action)(resource, context)
            if action == "verify" and report.get("valid"):
                self.store.save_evidence("native:" + resource.id, {"created_at": report.get("created_at"), "verified_at": now()})
            return report

    def backup(self, resource: Resource, context: TaskContext, password: str = "") -> dict:
        adapter = self.registry.get(resource)
        with self.locks.acquire(self.lock_keys(resource)):
            if resource.id == MANAGER_BACKUP_ID:
                return self.backup_manager(self.backup_root(), password, context)
            if resource.id == OVERALL_BACKUP_ID:
                return self.backup_overall(self.backup_root(), password, context)
            if resource.kind == "script":
                script = Path(str(resource.options.get("script_path", ""))).expanduser()
                if not script.is_file() or archives.is_link(script):
                    raise UserError("备份脚本不存在或不是普通文件，请检查脚本设置。")
                arguments = resource.options.get("script_arguments", [])
                if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
                    raise UserError("脚本参数配置无效，请重新编辑脚本备份资料。")
                staging_root = self.store.root / "backup-staging"
                staging_root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="script-", dir=staging_root) as temporary:
                    output = Path(temporary) / "output"
                    output.mkdir()
                    command = self._script_command(script) + arguments
                    env = {**os.environ, "SELFMANGER_BACKUP_OUTPUT_DIR": str(output)}
                    run_process(command, context, cwd=script.parent, timeout=3600, env=env)
                    if not any(output.iterdir()):
                        raise UserError("备份脚本未在 SELFMANGER_BACKUP_OUTPUT_DIR 指定目录生成资料。")
                    staged = Resource(resource.name, "script", {**resource.options, "path": str(output)}, resource.id)
                    result = archives.create_archive(staged, self.backup_root(), password, context)
                    self.verify_backup(Path(result["archive"]), password, context)
                    return {**result, "verified": True}
            result = archives.create_archive(resource, self.backup_root(), password, context)
            self.verify_backup(Path(result["archive"]), password, context)
            return {**result, "verified": True}

    @staticmethod
    def _script_command(script: Path) -> list[str]:
        suffix = script.suffix.casefold()
        if suffix == ".py":
            return [sys.executable, str(script)]
        if suffix == ".ps1":
            return ["powershell", "-NoProfile", "-NonInteractive", "-File", str(script)]
        if suffix == ".sh":
            return ["bash", str(script)]
        if suffix in {".bat", ".cmd"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)]
        return [str(script)]

    def verify_backup(self, source: Path, password: str, context: TaskContext) -> dict:
        before = fingerprint(source)
        report = archives.verify_archive(source, password, context)
        if report["resource_id"] == OVERALL_BACKUP_ID:
            from .combined_backup import verify_manifest
            with archives.open_archive(source, password, context, report["archive_sha256"]) as archive:
                verify_manifest(archive, archives.verify_open(archive, context))
        if fingerprint(source) != before:
            raise UserError("备份文件在校验期间变化，请重试。")
        previous = self.store.evidence(str(source.resolve()))
        if previous.get("archive_sha256") != report["archive_sha256"]:
            previous = {}
        identity = {key: report[key] for key in ("resource_id", "kind", "engine", "created_at")}
        self.store.save_evidence(str(source.resolve()), {**previous, **before, **identity, "verified_at": now(), "archive_sha256": report["archive_sha256"]})
        return report

    def delete_backup_version(self, resource: Resource, source: Path) -> None:
        with self.locks.acquire(self.lock_keys(resource)):
            archives.delete_archive_version(self.backup_root(), resource.id, source)

    def rehearse(self, resource: Resource, source: Path, password: str, context: TaskContext) -> dict:
        report = self.verify_backup(source, password, context)
        directory = self.store.root / "rehearsals"
        directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="trial-", dir=directory) as temporary:
            target = Path(temporary) / "restored"
            plan = self.plan_restore(resource, context, source=str(source), target=str(target), password=password)
            if plan.token != report["archive_sha256"]:
                raise UserError("备份已改变，请重试。")
            self.restore(resource, plan, context, password)
            count = 0
            with archives.open_archive(source, password, context, plan.token) as archive:
                manifest = archives.verify_open(archive, context)
                for record in manifest["files"]:
                    context.checkpoint()
                    relative = ("manager.sqlite3.recovered" if plan.resource_id == OVERALL_BACKUP_ID and record["path"] == "manager.sqlite3" else
                                ".selfManger-history.bundle" if record["path"] == "@git-bundle" else record["path"])
                    path = target / relative
                    if archives.path_token(path) != record["sha256"]:
                        raise UserError("演练恢复后的文件不一致。")
                    count += 1
        evidence = self.store.evidence(str(source.resolve()))
        self.store.save_evidence(str(source.resolve()), {**evidence, "rehearsed_at": now()})
        return {"valid": True, "identical_files": count, "note": "已在临时目录实际恢复并逐文件校验；临时资料已清理，原目录未覆盖。"}

    def automatic_backup(self, resource: Resource, context: TaskContext) -> dict:
        if resource.kind not in {"project", "folder", "script", "vault"} or not resource.options.get("automatic_backup"):
            raise UserError("此资源没有启用自动备份。")
        from .maintenance import is_due
        if not is_due(self.store, resource):
            return {"skipped": True, "note": "尚未到备份时间。"}
        password = ""
        if resource.kind in {"project", "folder", "script", "vault"}:
            password = self.common_password or self.secrets.get(resource.id + ":backup") or ""
            if not password:
                return {"skipped": True, "note": "请在设置中输入统一备份密码；本次打开期间有效。"}
        self.store.save_evidence("schedule:" + resource.id, {"last_attempt_at": now()})
        result = self.backup(resource, context, password)
        if resource.kind in {"project", "folder", "script", "vault"}:
            keep = backup_retention_count(self.store)
            for old in retention_candidates(self.backup_root(), resource.id, keep, result["archive"]):
                context.checkpoint()
                try:
                    old_report = archives.verify_archive(old, password, context)
                    if old_report.get("resource_id") != resource.id:
                        context.log("旧版本归属不匹配，已保留。")
                        continue
                except UserError:
                    context.log("旧版本无法校验或口令已更换，已保留。")
                    continue
                if old.name == "backup.amb":
                    (old.parent / "metadata.json").unlink(missing_ok=True)
                    old.unlink()
                    try:
                        old.parent.rmdir()
                    except OSError:
                        context.log("旧版本目录含有未识别文件，已保留目录。")
                else:
                    old.unlink()
                    old.with_suffix(".json").unlink(missing_ok=True)
        return result

    def plan_restore(self, resource: Resource, context: TaskContext, *, source: str = "", target: str = "", password: str = "", folders: dict[str, str] | None = None) -> RestorePlan:
        if resource.kind not in {"project", "folder", "script", "vault"}:
            raise UserError("此旧资源类型的恢复功能已移除；原始备份文件仍然保留。")
        with self.locks.acquire(self.lock_keys(resource, target)):
            plan = archives.plan_restore(resource, Path(source), Path(target), password, context, folders)
            if plan.resource_id == OVERALL_BACKUP_ID:
                from .combined_backup import verify_manifest
                with archives.open_archive(Path(source), password, context, plan.token) as archive:
                    manifest = verify_manifest(archive, archives.verify_open(archive, context))
                    if manifest["database_schema"] > Store.SCHEMA_VERSION:
                        raise UserError("备份数据库来自更新版本，请使用对应版本恢复。")
            return plan

    def restore(self, resource: Resource, plan: RestorePlan, context: TaskContext, password: str = "") -> dict:
        if resource.kind not in {"project", "folder", "script", "vault"}:
            raise UserError("此旧资源类型的恢复功能已移除；原始备份文件仍然保留。")
        with self.locks.acquire(self.lock_keys(resource, plan.target)):
            if plan.resource_id == OVERALL_BACKUP_ID:
                from .combined_backup import verify_manifest
                with archives.open_archive(Path(plan.source), password, context, plan.token) as archive:
                    manifest = verify_manifest(archive, archives.verify_open(archive, context))
                    if manifest["database_schema"] > Store.SCHEMA_VERSION:
                        raise UserError("备份数据库来自更新版本，请使用对应版本恢复。")
            return archives.apply_restore(plan, password, context, quarantine_database=plan.resource_id == OVERALL_BACKUP_ID)

    def prepare_overall_restore(self, restored_root: Path, destination_root: Path, context: TaskContext,
                                *, path_mappings: dict[str, str] | None = None) -> dict:
        from .restore_migration import prepare_overall_restore
        return prepare_overall_restore(restored_root, destination_root, context, path_mappings=path_mappings)

    def diagnostics(self) -> dict:
        return {"platform": sys.platform, "python": sys.version.split()[0], "data_directory": str(self.store.root),
                "tools": {name: shutil.which(name) or "未找到；可在资源设置中指定路径" for name in ("git", "ssh", "gpg", "python", "python3")},
                "adapters": self.registry.describe(), "note": "Linux/macOS 系统凭据存储需要已解锁的钥匙串。"}

    def running_owned_ids(self) -> list[str]:
        return []
