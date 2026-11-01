"""Prepare an isolated overall-backup restore for a new application data directory."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .archives import is_link, path_token, safe_name
from .domain import UserError
from .portable import PREFIX, decode_paths
from .project_workspaces import ProjectWorkspace
from .storage import Store
from .knowledge import knowledge_root_id

BLOCKER_APPLICATION_ID = 1397573458
BLOCKER_SCHEMA_VERSION = 2147483647
PATH_KEYS = frozenset({"path", "root", "root_path", "daily", "projects", "workbench_root", "project_workspace",
                       "project_root", "backup_root", "backup_repo", "home", "vault_path", "knowledge_root",
                       "obsidian_vault_root"})


def _plain_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(is_link(part) for part in (path, *path.parents)):
        raise UserError("恢复或启用目录不能包含符号链接或目录联接。")
    return path


def _is_within(value: Path, root: Path) -> bool:
    try:
        value.relative_to(root)
        return True
    except ValueError:
        if os.name == "nt":
            return os.path.normcase(str(value)).startswith(os.path.normcase(str(root)) + os.sep)
        return False


def _safe_join(root: Path, relative: str) -> Path:
    current = root
    for part in Path(safe_name(relative)).parts:
        current = current / part
        if is_link(current):
            raise UserError("恢复路径包含符号链接或目录联接，拒绝继续。")
    if not _is_within(current, root):
        raise UserError("恢复路径超出所选目录，拒绝继续。")
    return current


def _decode(value, portable_root: Path | None):
    if isinstance(value, str) and value.startswith(PREFIX) and portable_root is None:
        raise UserError("此整体备份使用便携路径，但清单缺少原便携目录；为避免错连路径，不能启用数据库。")
    return decode_paths(value, portable_root)


def _mapped_path(value: str, mappings: list[tuple[Path, Path]], portable_root: Path | None) -> str:
    decoded = _decode(value, portable_root)
    if not isinstance(decoded, str):
        return value
    path = Path(decoded).expanduser()
    if not path.is_absolute():
        return decoded
    for old, new in mappings:
        if path == old or _is_within(path, old):
            try:
                suffix = path.relative_to(old)
            except ValueError:
                # Windows drive and case normalization fallback.
                suffix = Path(os.path.relpath(str(path), str(old)))
            return str(new / suffix)
    return decoded


def _remap_known(value, mappings: list[tuple[Path, Path]], portable_root: Path | None):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(item, str) and key in PATH_KEYS:
                result[key] = _mapped_path(item, mappings, portable_root)
            else:
                result[key] = _remap_known(item, mappings, portable_root)
        return result
    if isinstance(value, list):
        return [_remap_known(item, mappings, portable_root) for item in value]
    return value


def _verify_extracted(root: Path, manifest: dict, context) -> dict[str, dict]:
    rows = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(rows, list):
        raise UserError("恢复清单版本无效，不能启用数据库。")
    indexed = {}
    for row in rows:
        context.checkpoint()
        try:
            relative = safe_name(row["path"])
            digest, size = row["sha256"], row["size"]
        except (KeyError, TypeError, ValueError) as exc:
            raise UserError("恢复清单含无效文件记录，不能启用数据库。") from exc
        if relative in indexed or not isinstance(digest, str) or len(digest) != 64 or not isinstance(size, int) or size < 0:
            raise UserError("恢复清单含重复或无效文件记录，不能启用数据库。")
        source_relative = "manager.sqlite3.recovered" if relative == "manager.sqlite3" else relative
        source = _safe_join(root, source_relative)
        if not source.is_file() or is_link(source) or source.stat().st_size != size or path_token(source) != digest:
            raise UserError("恢复文件与清单不一致，不能启用数据库：" + relative)
        indexed[relative] = row
    if not {"manager.sqlite3", "projects.json"} <= indexed.keys():
        raise UserError("整体备份缺少数据库或项目登记表，不能启用数据库。")
    return indexed


def _copy_project_workspace(root: Path, destination: Path, rows: dict[str, dict], context) -> int:
    registry_source = root / "projects.json"
    try:
        registry = json.loads(registry_source.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UserError("整体备份中的项目登记表不可读，不能启用数据库。") from exc
    destination.mkdir(parents=True)
    workspace = ProjectWorkspace(destination)
    workspace.registry_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(registry_source, workspace.registry_path)
    try:
        projects = workspace.registry()["projects"]
    except UserError:
        raise
    copied = 0
    for project in projects:
        context.checkpoint()
        identity = project["id"]
        source_prefix = f"markdown/projects/{identity}/"
        project_target = workspace.path(project["path"])
        project_target.mkdir(parents=True, exist_ok=True)
        for relative in rows:
            if not relative.startswith(source_prefix):
                continue
            tail = relative[len(source_prefix):]
            source = _safe_join(root, relative)
            target = project_target.joinpath(*Path(safe_name(tail)).parts)
            if not _is_within(target, project_target) or is_link(source):
                raise UserError("项目恢复路径越界或包含联接，不能启用数据库。")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    return copied


def _copy_personal_markdown(root: Path, destination: Path, rows: dict[str, dict], context) -> int:
    destination.mkdir(parents=True)
    prefix = "markdown/personal/"
    copied = 0
    for relative in rows:
        context.checkpoint()
        if not relative.startswith(prefix):
            continue
        source = _safe_join(root, relative)
        target = destination.joinpath(*Path(safe_name(relative[len(prefix):])).parts)
        if not _is_within(target, destination) or is_link(source):
            raise UserError("个人计划恢复路径越界或包含联接，不能启用数据库。")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _path_hash(path: str) -> str:
    return knowledge_root_id(path)


def restored_vault_root(restored_root: Path) -> str | None:
    """Read the source Vault path from an extracted backup without changing it."""
    restored_root = _plain_path(Path(restored_root))
    try:
        manifest = json.loads(_safe_join(restored_root, "overall-manifest.json").read_text(encoding="utf-8"))
        portable_text = manifest.get("source_portable_root")
        if portable_text is not None and (not isinstance(portable_text, str) or not Path(portable_text).is_absolute()):
            raise UserError("整体备份的便携目录信息无效。")
        portable_root = Path(portable_text) if portable_text else None
        database = _safe_join(restored_root, "manager.sqlite3.recovered")
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT value FROM settings WHERE key='obsidian_vault_root'").fetchone()
        if not row:
            return None
        value = _decode(json.loads(row[0]), portable_root)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise UserError("无法从恢复目录读取知识库路径；原恢复副本未修改。") from exc
    return value if isinstance(value, str) and Path(value).is_absolute() else None


def _transform_database(database: Path, mappings: list[tuple[Path, Path]], portable_root: Path | None,
                        personal_old: Path, workspace_old: Path, personal_new: Path,
                        workspace_new: Path, destination_root: Path, context) -> dict:
    stats = {"settings": 0, "resources": 0, "task_paths": 0, "personal_documents": 0,
             "personal_plan_origins": 0, "knowledge_roots": 0, "unmapped_personal_documents": 0,
             "unresolved_knowledge_references": 0}
    extra_maps = [pair for pair in mappings if pair[0] not in {personal_old, workspace_old}]
    managed_maps = sorted(mappings, key=lambda pair: len(str(pair[0])), reverse=True)
    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > Store.SCHEMA_VERSION:
            raise UserError("恢复数据库来自更新版本，当前程序不能启用。")
        if db.execute("PRAGMA application_id").fetchone()[0] == BLOCKER_APPLICATION_ID:
            raise UserError("恢复数据库仍是隔离阻挡文件，原数据库缺失。")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise UserError("恢复数据库完整性检查未通过，不能启用。")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"settings", "resources", "planned_tasks", "personal_plans", "personal_markdown_documents"} <= tables:
            raise UserError("恢复数据库缺少当前应用所需数据表，不能启用。")
        settings = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}
        vault_key = "obsidian_vault_root"
        old_knowledge_root = None
        if vault_key in settings:
            try:
                value = json.loads(settings[vault_key])
                old_knowledge_root = _mapped_path(value, extra_maps, portable_root) if isinstance(value, str) else None
            except (TypeError, ValueError):
                raise UserError("知识库路径设置无效，不能启用数据库。")
        new_knowledge_root = old_knowledge_root
        if old_knowledge_root:
            original_value = json.loads(settings[vault_key])
            decoded = _decode(original_value, portable_root)
            new_knowledge_root = _mapped_path(decoded, extra_maps, None)
        old_knowledge_path = _decode(json.loads(settings[vault_key]), portable_root) if old_knowledge_root else None
        new_knowledge_id = _path_hash(new_knowledge_root) if new_knowledge_root and new_knowledge_root != old_knowledge_path else None
        old_knowledge_id = _path_hash(old_knowledge_path) if old_knowledge_path else None

        with db:
            # Rebase only known settings and path-bearing import markers; user notes/logs are untouched.
            for key, raw in list(settings.items()):
                context.checkpoint()
                if key == "workbench_root":
                    value = str(personal_new)
                elif key == "project_workspace":
                    value = str(workspace_new)
                elif key == "backup_root":
                    value = str(destination_root / "backups")
                elif key == vault_key:
                    value = new_knowledge_root
                else:
                    try:
                        value = _remap_known(json.loads(raw), managed_maps, portable_root)
                    except (TypeError, ValueError):
                        raise UserError("数据库设置格式无效，不能安全重映射：" + key)
                encoded = json.dumps(value, ensure_ascii=False)
                if encoded != raw:
                    db.execute("UPDATE settings SET value=? WHERE key=?", (encoded, key))
                    stats["settings"] += 1

            old_marker = "personal_plans_import:" + str(personal_old)
            new_marker = "personal_plans_import:" + str(personal_new)
            if db.execute("SELECT 1 FROM settings WHERE key=?", (old_marker,)).fetchone():
                if db.execute("SELECT 1 FROM settings WHERE key=?", (new_marker,)).fetchone():
                    raise UserError("恢复数据库中个人计划导入标记冲突，原件保持不变。")
                db.execute("UPDATE settings SET key=? WHERE key=?", (new_marker, old_marker))
                stats["settings"] += 1

            for row in db.execute("SELECT id,document FROM resources").fetchall():
                context.checkpoint()
                try:
                    document = json.loads(row["document"])
                    options = document.get("options", {})
                    if document.get("kind") == "project":
                        options = _remap_known(options, managed_maps, portable_root)
                    else:
                        options = _remap_known(options, extra_maps, portable_root)
                    updated = json.dumps({**document, "options": options}, ensure_ascii=False)
                except (TypeError, ValueError, AttributeError) as exc:
                    raise UserError("恢复数据库中的资源登记不可读，不能启用数据库。") from exc
                if updated != row["document"]:
                    db.execute("UPDATE resources SET document=? WHERE id=?", (updated, row["id"]))
                    stats["resources"] += 1

            for row in db.execute("SELECT id,project_root,source_ref FROM planned_tasks").fetchall():
                project_root = _mapped_path(row["project_root"], [pair for pair in managed_maps if pair[0] == workspace_old], portable_root) if row["project_root"] else None
                source_ref = row["source_ref"]
                for old, new in managed_maps:
                    for marker in ("legacy:daily:", "legacy:project:"):
                        prefix = marker + str(old)
                        if source_ref and (source_ref == prefix or source_ref.startswith(prefix + os.sep) or source_ref.startswith(prefix + ":")):
                            source_ref = marker + str(new) + source_ref[len(prefix):]
                if project_root != row["project_root"] or source_ref != row["source_ref"]:
                    db.execute("UPDATE planned_tasks SET project_root=?,source_ref=? WHERE id=?",
                               (project_root, source_ref, row["id"]))
                    stats["task_paths"] += 1

            for row in db.execute("SELECT id,project_root,origin_path FROM personal_plans").fetchall():
                project_root = _mapped_path(row["project_root"], [pair for pair in managed_maps if pair[0] == workspace_old], portable_root) if row["project_root"] else None
                origin_path = _mapped_path(row["origin_path"], [pair for pair in managed_maps if pair[0] == personal_old], portable_root) if row["origin_path"] else None
                if project_root != row["project_root"] or origin_path != row["origin_path"]:
                    db.execute("UPDATE personal_plans SET project_root=?,origin_path=? WHERE id=?",
                               (project_root, origin_path, row["id"]))
                    stats["personal_plan_origins"] += 1

            for row in db.execute("SELECT entity_type,entity_id,root_path,relative_path FROM personal_markdown_documents").fetchall():
                root_path = _mapped_path(row["root_path"], [pair for pair in managed_maps if pair[0] == personal_old], portable_root)
                if root_path != row["root_path"]:
                    db.execute("UPDATE personal_markdown_documents SET root_path=? WHERE entity_type=? AND entity_id=?",
                               (root_path, row["entity_type"], row["entity_id"]))
                    stats["personal_documents"] += 1
                try:
                    path = Path(root_path) / safe_name(row["relative_path"])
                    if not path.is_file():
                        stats["unmapped_personal_documents"] += 1
                except (UserError, TypeError):
                    stats["unmapped_personal_documents"] += 1

            if new_knowledge_id and old_knowledge_id and new_knowledge_id != old_knowledge_id:
                for table in ("knowledge_files", "knowledge_metadata", "knowledge_references", "knowledge_bundles", "knowledge_legacy_imports"):
                    if table not in tables:
                        continue
                    if db.execute(f"SELECT 1 FROM {table} WHERE root_id=? LIMIT 1", (new_knowledge_id,)).fetchone():
                        raise UserError("新知识库路径已有索引记录，无法无损合并旧索引。")
                    changed = db.execute(f"UPDATE {table} SET root_id=? WHERE root_id=?", (new_knowledge_id, old_knowledge_id))
                    stats["knowledge_roots"] += changed.rowcount

            if "knowledge_references" in tables:
                for row in db.execute("SELECT rowid,target_path FROM knowledge_references").fetchall():
                    mapped = _mapped_path(row["target_path"], extra_maps, portable_root)
                    if mapped != row["target_path"]:
                        db.execute("UPDATE knowledge_references SET target_path=? WHERE rowid=?", (mapped, row["rowid"]))
                    if not Path(mapped).is_file():
                        stats["unresolved_knowledge_references"] += 1

            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
                raise UserError("路径重映射后数据库完整性检查失败，未发布启用目录。")
            db.execute(f"PRAGMA user_version={version}")
            db.execute("PRAGMA application_id=0")
    return stats


def prepare_overall_restore(restored_root: Path, destination_root: Path, context, *, path_mappings: dict[str, str] | None = None) -> dict:
    """Copy an isolated restore into a new data directory and rebase managed paths.

    The quarantined extraction remains untouched. Destination must be a new path;
    no user data is overwritten. External vaults are only rebased when explicitly
    supplied in ``path_mappings`` and are never copied by this operation.
    """
    restored_root = _plain_path(Path(restored_root))
    destination_root = _plain_path(Path(destination_root))
    if not restored_root.is_dir() or restored_root == destination_root or _is_within(destination_root, restored_root) or _is_within(restored_root, destination_root):
        raise UserError("恢复源与启用目标必须是互不包含的目录。")
    if destination_root.exists():
        raise UserError("启用目标必须是新目录；不会覆盖已有资料。")
    manifest_path = _safe_join(restored_root, "overall-manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UserError("恢复目录缺少有效的整体备份清单。") from exc
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("source_personal_root"), str)
            or not Path(manifest["source_personal_root"]).is_absolute()
            or not isinstance(manifest.get("source_workspace_root"), str)
            or not Path(manifest["source_workspace_root"]).is_absolute()):
        raise UserError("此整体备份缺少安全路径重映射所需的来源目录信息；原恢复副本保持不变。")
    portable_text = manifest.get("source_portable_root")
    if portable_text is not None and not isinstance(portable_text, str):
        raise UserError("整体备份的便携目录信息无效。")
    portable_root = Path(portable_text) if portable_text else None
    rows = _verify_extracted(restored_root, manifest, context)
    blocker = _safe_join(restored_root, "manager.sqlite3")
    with closing(sqlite3.connect(blocker)) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != BLOCKER_APPLICATION_ID or db.execute("PRAGMA user_version").fetchone()[0] != BLOCKER_SCHEMA_VERSION:
            raise UserError("恢复目录的数据库阻挡文件状态无效。")
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
            raise UserError("恢复目录的数据库阻挡文件不为空，拒绝继续。")
    recovered = _safe_join(restored_root, "manager.sqlite3.recovered")
    with closing(sqlite3.connect(recovered)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != manifest.get("database_schema"):
            raise UserError("隔离数据库版本与恢复清单不一致。")

    personal_old = Path(manifest["source_personal_root"]).expanduser().absolute()
    workspace_old = Path(manifest["source_workspace_root"]).expanduser().absolute()
    personal_new = destination_root / "personal"
    workspace_new = destination_root / "workspace"
    mappings = [(personal_old, personal_new), (workspace_old, workspace_new)]
    for old_text, new_text in (path_mappings or {}).items():
        old, new = _plain_path(Path(old_text)), _plain_path(Path(new_text))
        if old == new or not new.is_dir():
            raise UserError("附加路径映射必须指向已存在的新目录。")
        mappings.append((old, new))
    mappings.sort(key=lambda pair: len(str(pair[0])), reverse=True)

    destination_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".selfManger-ready-", dir=destination_root.parent))
    try:
        data_root = stage / "data"
        data_root.mkdir()
        personal_stage = stage / "personal"
        personal_files = _copy_personal_markdown(restored_root, personal_stage, rows, context)
        workspace_stage = stage / "workspace"
        project_files = _copy_project_workspace(restored_root, workspace_stage, rows, context)
        shutil.copy2(recovered, data_root / "manager.sqlite3")
        # Historical schema 3/5 did not reliably indicate that later tables and
        # columns had been created. Migrate only the staged copy before rebasing.
        Store(data_root)
        stats = _transform_database(data_root / "manager.sqlite3", mappings, portable_root,
                                    personal_old, workspace_old, personal_new,
                                    destination_root / "workspace", destination_root, context)
        report = {"prepared": True, "data_directory": str(destination_root / "data"),
                  "project_workspace": str(destination_root / "workspace"),
                  "personal_markdown": str(personal_new), "restored_personal_markdown_files": personal_files,
                  "restored_project_markdown_files": project_files,
                  "database_schema": Store.SCHEMA_VERSION, "path_updates": stats,
                  "notes": ["原隔离恢复目录及 manager.sqlite3.recovered 保持不变。",
                            "未包含的知识库文件、凭据与项目非 Markdown 附件仍需另行恢复。",
                            "未显式映射的知识库和外部引用路径保留原值；缺失关联只报告，不删除。"]}
        (stage / "恢复后启动说明.txt").write_text(
            "此目录由隔离整体备份生成，源恢复目录和 manager.sqlite3.recovered 均已保留。\n"
            "启动 selfManger 时将数据目录指定为本目录下的 data，例如：selfManger.exe --data-dir \""
            + str(destination_root / "data") + "\"。\n"
            "首次使用前核对项目工作区和个人计划目录；知识库文件、外部引用和系统凭据不包含在整体备份内。\n",
            encoding="utf-8")
        (stage / "恢复映射报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with closing(sqlite3.connect(data_root / "manager.sqlite3")) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise UserError("启用数据库校验失败，未发布目标目录。")
        context.checkpoint()
        if destination_root.exists():
            raise UserError("启用目标在准备期间被创建；未覆盖现有目录。")
        stage.replace(destination_root)
        return report
    except Exception as original_exc:
        if stage.exists() and stage.parent.resolve() == destination_root.parent.resolve() and stage.name.startswith(".selfManger-ready-"):
            try:
                shutil.rmtree(stage)
            except OSError as cleanup_exc:
                raise UserError(
                    f"准备启用目录失败（{type(original_exc).__name__}），部分临时副本无法清理 "
                    f"（{type(cleanup_exc).__name__}）：{stage}"
                ) from original_exc
        raise
