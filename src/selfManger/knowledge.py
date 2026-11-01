"""Filesystem backed knowledge library with a rebuildable SQLite metadata index."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlsplit

from .domain import UserError
from .naming import temporary_path
from .knowledge_resources import PortableResources
from .knowledge_bundles import ResourceBundles
from .obsidian import require_vault


def knowledge_root(store):
    """Use the configured Obsidian vault, or infer myself from the workspace setting."""
    configured = str(store.setting("obsidian_vault_root", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    workspace = str(store.setting("project_workspace", "") or "").strip()
    if workspace:
        candidate = Path(workspace).expanduser() / "myself"
        if candidate.is_dir():
            return candidate
    return None


def knowledge_root_id(root):
    path = Path(root).expanduser().resolve(strict=False)
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()


def knowledge_root_has_index(store, root):
    identity = knowledge_root_id(root)
    with store.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return any(table in tables and db.execute(
            f"SELECT 1 FROM {table} WHERE root_id=? LIMIT 1", (identity,)).fetchone()
                   for table in ("knowledge_files", "knowledge_metadata", "knowledge_references",
                                 "knowledge_bundles", "knowledge_legacy_imports"))


def configure_knowledge_root(store, value, migrate_index=None):
    """Validate and save the selected vault root, optionally rebasing its index.

    A ``None`` migration choice returns ``requires_choice`` without changing
    settings. ``False`` switches roots while preserving the old index; ``True``
    rebases the old index and setting in one transaction.
    """
    raw = str(value or "").strip().strip('"')
    saved_path = ""
    if raw:
        path = Path(raw).expanduser()
        require_vault(path)
        saved_path = str(path.absolute())

    old_root = knowledge_root(store)
    new_root = Path(saved_path) if saved_path else None
    if not new_root:
        workspace = str(store.setting("project_workspace", "") or "").strip()
        candidate = Path(workspace).expanduser() / "myself" if workspace else None
        if candidate and candidate.is_dir():
            new_root = candidate

    needs_choice = bool(old_root and new_root
                        and knowledge_root_id(old_root) != knowledge_root_id(new_root)
                        and knowledge_root_has_index(store, old_root))
    if needs_choice and migrate_index is None:
        return {"saved_path": saved_path, "requires_choice": True, "migrated": False}

    migrated = bool(needs_choice and migrate_index)
    with store.atomic():
        if migrated:
            rebase_knowledge_index(store, old_root, new_root)
        store.set_setting("obsidian_vault_root", saved_path)
    return {"saved_path": saved_path, "requires_choice": False, "migrated": migrated}


def rebase_knowledge_index(store, old_root, new_root):
    """Move one vault's SQLite index identity without merging another vault's rows."""
    old_path = Path(old_root).expanduser().resolve(strict=False)
    new_path = Path(new_root).expanduser().resolve(strict=False)
    old_id, new_id = knowledge_root_id(old_path), knowledge_root_id(new_path)
    if old_id == new_id:
        return 0

    def remap_absolute(value):
        if not isinstance(value, str) or not value:
            return value
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return value
        try:
            relative = candidate.absolute().relative_to(old_path)
        except ValueError:
            return value
        return str(new_path / relative)

    with store.atomic() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        index_tables = [table for table in ("knowledge_files", "knowledge_metadata", "knowledge_references",
                                            "knowledge_bundles", "knowledge_legacy_imports") if table in tables]
        if any(db.execute(f"SELECT 1 FROM {table} WHERE root_id=? LIMIT 1", (new_id,)).fetchone()
               for table in index_tables):
            raise UserError("目标知识库路径已有索引，不能合并；原索引和设置均未改变。")

        if "knowledge_metadata" in index_tables:
            columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_metadata)")}
            if "details" in columns:
                for row in db.execute("SELECT rowid,details FROM knowledge_metadata WHERE root_id=?", (old_id,)).fetchall():
                    try:
                        details = json.loads(row["details"] or "{}")
                    except (TypeError, ValueError) as exc:
                        raise UserError("知识库元数据不可读，未迁移索引。") from exc
                    if not isinstance(details, dict):
                        raise UserError("知识库元数据格式无效，未迁移索引。")
                    updated = dict(details)
                    if isinstance(updated.get("related"), list):
                        updated["related"] = [remap_absolute(value) for value in updated["related"]]
                    for key in ("archive_original", "archive_original_directory"):
                        if key in updated:
                            updated[key] = remap_absolute(updated[key])
                    locations = updated.get("archive_member_locations")
                    if isinstance(locations, dict):
                        updated["archive_member_locations"] = {
                            remap_absolute(key): remap_absolute(value) for key, value in locations.items()}
                    if updated != details:
                        db.execute("UPDATE knowledge_metadata SET details=? WHERE rowid=?",
                                   (json.dumps(updated, ensure_ascii=False), row["rowid"]))

        if "knowledge_references" in index_tables:
            for row in db.execute("SELECT rowid,relative_path,target_path FROM knowledge_references WHERE root_id=?",
                                  (old_id,)).fetchall():
                target = remap_absolute(row["target_path"])
                relative = row["relative_path"]
                if not isinstance(relative, str):
                    raise UserError("知识库外部引用索引格式无效，未迁移索引。")
                if relative.startswith("external:"):
                    try:
                        encoded_path = json.loads(relative[len("external:"):])
                    except (TypeError, ValueError):
                        raise UserError("知识库外部引用索引不可读，未迁移索引。")
                    mapped_key = remap_absolute(encoded_path)
                    if mapped_key != encoded_path:
                        relative = "external:" + json.dumps(mapped_key, ensure_ascii=False)
                db.execute("UPDATE knowledge_references SET relative_path=?,target_path=? WHERE rowid=?",
                           (relative, target, row["rowid"]))

        if "knowledge_bundles" in index_tables:
            for row in db.execute("SELECT bundle_id,record FROM knowledge_bundles WHERE root_id=?", (old_id,)).fetchall():
                try:
                    record = json.loads(row["record"])
                except (TypeError, ValueError) as exc:
                    raise UserError("知识库资源组索引不可读，未迁移索引。") from exc
                if not isinstance(record, dict):
                    raise UserError("知识库资源组索引格式无效，未迁移索引。")
                for key in ("folder", "directory", "entry"):
                    if key in record:
                        record[key] = remap_absolute(record[key])
                for key in ("members", "excluded"):
                    if isinstance(record.get(key), list):
                        record[key] = [remap_absolute(value) for value in record[key]]
                db.execute("UPDATE knowledge_bundles SET record=? WHERE root_id=? AND bundle_id=?",
                           (json.dumps(record, ensure_ascii=False), old_id, row["bundle_id"]))

        updated = 0
        for table in index_tables:
            updated += db.execute(f"UPDATE {table} SET root_id=? WHERE root_id=?", (new_id, old_id)).rowcount
        return updated


class KnowledgeIndex(ResourceBundles, PortableResources):
    EXCLUDED = {".git", ".obsidian", ".local-secrets", ".trash"}
    MAX_FILES = 20000
    TEXT_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv", ".html", ".htm", ".xml", ".toml", ".ini", ".log"}
    MAX_TEXT_FILE = 100_000
    MAX_TEXT_TOTAL = 5_000_000

    def __init__(self, store, root):
        self.store = store
        self._text_cache = {}
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink() or getattr(self.root, "is_junction", lambda: False)():
            raise UserError("知识库根目录不能是目录联接或符号链接。")
        self.root = self.root.parent.resolve() / self.root.name
        self.root_id = knowledge_root_id(self.root)
        with self.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_metadata (
                root_id TEXT NOT NULL, relative_path TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
                favorite INTEGER NOT NULL DEFAULT 0, projects TEXT NOT NULL DEFAULT '[]',
                agents TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(root_id, relative_path))""")
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_files (
                root_id TEXT NOT NULL, relative_path TEXT NOT NULL, name TEXT NOT NULL, folder TEXT NOT NULL,
                kind TEXT NOT NULL, size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, missing INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(root_id, relative_path))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_metadata)")}
            if "details" not in columns:
                db.execute("ALTER TABLE knowledge_metadata ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_references (
                root_id TEXT NOT NULL, relative_path TEXT NOT NULL, target_path TEXT NOT NULL,
                display_name TEXT NOT NULL, folder TEXT NOT NULL,
                PRIMARY KEY(root_id, relative_path))""")
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_bundles (
                root_id TEXT NOT NULL, bundle_id TEXT NOT NULL, record TEXT NOT NULL,
                PRIMARY KEY(root_id, bundle_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS knowledge_legacy_imports (
                root_id TEXT NOT NULL, source_kind TEXT NOT NULL, completed_at TEXT NOT NULL,
                imported_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
                PRIMARY KEY(root_id, source_kind))""")

    def _relative(self, path):
        path = Path(path)
        for parent in (path, *path.parents):
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise UserError("知识库操作不能经过目录联接或符号链接。")
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.root.resolve()):
            raise UserError("路径必须位于知识库根目录内。")
        return resolved.relative_to(self.root.resolve()).as_posix()

    def project_resources(self, project_id: str) -> list[dict]:
        """Read project-linked labels from the SQLite index without rescanning the Vault."""
        if not project_id:
            return []
        results = []
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT relative_path FROM knowledge_metadata WHERE root_id=? ORDER BY relative_path",
                (self.root_id,),
            ).fetchall()
            for row in rows:
                relative = row[0]
                record = self._read_record_db(db, relative)
                if project_id not in record.get("projects", []):
                    continue
                if relative.startswith("external:"):
                    try:
                        display_path = Path(json.loads(relative[len("external:"):]))
                    except (TypeError, ValueError):
                        display_path = Path("资料")
                elif relative.startswith("bundle:"):
                    display_path = Path(relative.partition(":")[2])
                else:
                    display_path = Path(relative)
                results.append({
                    "id": relative,
                    "relative": relative,
                    "name": record.get("title") or display_path.stem or display_path.name,
                })
        return results

    def import_legacy_catalog(self, source_root, context=None) -> dict:
        """Register old workbench resource Markdown as SQLite-owned external references once."""
        from .workbench import frontmatter

        source_root = Path(source_root).expanduser().absolute()
        if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
               for path in (source_root, *source_root.parents)):
            raise UserError("旧资源目录不能经过符号链接或目录联接。")
        source_root = source_root.parent.resolve() / source_root.name
        resource_root = source_root / "资源"
        if resource_root.is_symlink() or getattr(resource_root, "is_junction", lambda: False)():
            raise UserError("旧资源目录不能是符号链接或目录联接。")
        if not resource_root.is_dir():
            return {"imported": 0, "already_registered": 0, "errors": [], "available": False}
        source_key = "legacy-catalog:v1:" + hashlib.sha256(str(source_root.resolve()).casefold().encode("utf-8")).hexdigest()
        with self.store.connect() as db:
            completed = db.execute(
                "SELECT imported_count,error_count FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?",
                (self.root_id, source_key),
            ).fetchone()
        if completed:
            return {"imported": 0, "already_registered": completed[0], "errors": [],
                    "available": True, "already_completed": True}
        files = sorted(resource_root.glob("*.md"), key=lambda path: path.name.casefold())
        if len(files) > 1000:
            raise UserError("旧资源库超过 1000 篇 Markdown；请先分批整理，不会自动部分导入。")
        report = {"imported": 0, "already_registered": 0, "errors": [], "available": True}
        for path in files:
            if context:
                context.checkpoint()
            relative_source = path.relative_to(source_root).as_posix()
            file_key = "legacy-catalog-file:v1:" + hashlib.sha256(
                (str(source_root.resolve()).casefold() + "\0" + relative_source).encode("utf-8")
            ).hexdigest()
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?",
                              (self.root_id, file_key)).fetchone():
                    report["already_registered"] += 1
                    continue
            try:
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise UserError("文件是链接，已跳过。")
                data = path.read_bytes()
                if len(data) > 1_000_000:
                    raise UserError("文件超过 1 MB，已跳过。")
                text = data.decode("utf-8-sig")
                legacy, _ = frontmatter(text)
                name = legacy.get("name", path.stem)
                if not isinstance(name, str) or not name.strip() or len(name) > 120:
                    raise UserError("资源名称无效，已跳过。")
                for key in ("tags", "agents", "projects"):
                    if key in legacy and (not isinstance(legacy[key], list)
                                          or any(not isinstance(value, str) for value in legacy[key])):
                        raise UserError(key + " 必须是文本列表，已跳过。")
                if "favorite" in legacy and not isinstance(legacy["favorite"], bool):
                    raise UserError("收藏标记无效，已跳过。")
                source = path.resolve(strict=True)
                if not source.is_file() or not source.is_relative_to(source_root.resolve()):
                    raise UserError("文件路径不在旧工作台目录内，已跳过。")
                identity = "external:" + json.dumps(str(source), ensure_ascii=False)
                projects = legacy.get("projects", [])
                agents = legacy.get("agents", [])
                tags = legacy.get("tags", [])
                summary = legacy.get("summary", "")
                notes = legacy.get("notes", "")
                if not isinstance(summary, str) or not isinstance(notes, str):
                    raise UserError("用途或个人记录字段无效，已跳过。")
                details = {"title": name, "summary": summary, "notes": notes,
                           "legacy_catalog_type": legacy.get("type", "other"),
                           "legacy_catalog_id": legacy.get("id", "file:" + relative_source),
                           "legacy_catalog_path": relative_source}
                for key, value in legacy.items():
                    if key not in {"name", "type", "id", "tags", "agents", "projects", "favorite", "summary", "notes"}:
                        details["legacy_catalog_" + str(key)] = value
                details = json.loads(json.dumps(details, ensure_ascii=False, default=str))
                record = {"relative": identity, "tags": tags, "favorite": legacy.get("favorite", False),
                          "projects": projects, "agents": agents, **details}
                self._validate_resource_record(record, identity)
                if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > 256_000:
                    raise UserError("资源元数据超过 256 KB，已跳过。")
                with self.store.connect() as db:
                    inserted = db.execute("INSERT OR IGNORE INTO knowledge_references VALUES (?,?,?,?,?)",
                                          (self.root_id, identity, str(source), name, "02-知识")).rowcount
                    imported = bool(inserted)
                    if imported:
                        self._put_record(db, identity, record)
                    from .storage import now
                    db.execute("INSERT OR IGNORE INTO knowledge_legacy_imports VALUES (?,?,?,?,?)",
                               (self.root_id, file_key, now(), int(imported), 0))
                report["imported" if imported else "already_registered"] += 1
            except (OSError, UnicodeError, ValueError, UserError) as exc:
                report["errors"].append(path.name + "：" + str(exc))
        if files and not report["errors"]:
            from .storage import now
            with self.store.connect() as db:
                db.execute("INSERT OR IGNORE INTO knowledge_legacy_imports VALUES (?,?,?,?,?)",
                           (self.root_id, source_key, now(), report["imported"] + report["already_registered"], 0))
        return report

    def delete_local_resource(self, entry, move_to_trash):
        """Move a selected library copy to the system trash; never follow external references."""
        relative = entry["relative"]
        if relative.startswith("external:"):
            raise UserError("这是库外引用，不能删除库外原文件；请先在原位置处理。")
        bundle = entry.get("bundle_record") if entry.get("kind") == "bundle" else None
        if bundle:
            directory = bundle.get("directory", "")
            if not directory:
                raise UserError("这个资料合集没有独立目录，请先逐项处理，避免删除共享文件。")
            path = Path(directory)
            if not path.is_absolute():
                path = self.root / path
        else:
            path = self.root / relative
        local = self._relative(path)
        if not local or local == ".":
            raise UserError("不能删除知识库根目录。")
        moved_to_trash = path.exists()
        if moved_to_trash and not move_to_trash(str(path)):
            raise UserError("无法移入系统回收站，文件和索引未删除：" + str(path))
        try:
            with self.store.atomic() as db:
                if bundle:
                    db.execute("DELETE FROM knowledge_bundles WHERE root_id=? AND bundle_id=?",
                               (self.root_id, bundle["id"]))
                self.purge_resource(relative)
                for table in ("knowledge_files", "knowledge_metadata"):
                    rows = db.execute(f"SELECT relative_path FROM {table} WHERE root_id=?", (self.root_id,)).fetchall()
                    keys = [row[0] for row in rows
                            if row[0] == local or (bundle and row[0].startswith(local + "/"))]
                    db.executemany(f"DELETE FROM {table} WHERE root_id=? AND relative_path=?",
                                   [(self.root_id, key) for key in keys])
                db.execute("DELETE FROM knowledge_references WHERE root_id=? AND (folder=? OR substr(folder,1,?)=?)",
                           (self.root_id, local, len(local + "/"), local + "/"))
        except Exception as exc:
            if moved_to_trash:
                raise UserError(
                    f"本地文件已移入系统回收站（可从那里还原），但索引清理失败，记录仍保留：{exc}"
                ) from exc
            raise

    def purge_deleted_resource(self, entry, move_to_trash):
        """Permanently remove a recycle-bin registration without deleting external files."""
        relative = entry.get("relative")
        if not isinstance(relative, str) or not relative:
            raise UserError("资料路径无效，请刷新知识库后重试。")
        if relative.startswith("external:"):
            with self.store.atomic() as db:
                db.execute("DELETE FROM knowledge_references WHERE root_id=? AND relative_path=?",
                           (self.root_id, relative))
                db.execute("DELETE FROM knowledge_files WHERE root_id=? AND relative_path=?",
                           (self.root_id, relative))
                db.execute("DELETE FROM knowledge_metadata WHERE root_id=? AND relative_path=?",
                           (self.root_id, relative))
            return

        bundle = entry.get("bundle_record") if entry.get("kind") == "bundle" else None
        directory = bundle.get("directory", "") if bundle else ""
        local_directory = bool(bundle and directory)
        if local_directory and Path(directory).is_absolute():
            try:
                self._relative(Path(directory))
            except UserError:
                local_directory = False
        if bundle and not local_directory:
            # A member-based or external-directory bundle does not own its source files.
            with self.store.atomic() as db:
                db.execute("DELETE FROM knowledge_bundles WHERE root_id=? AND bundle_id=?",
                           (self.root_id, bundle["id"]))
                db.execute("DELETE FROM knowledge_metadata WHERE root_id=? AND relative_path=?",
                           (self.root_id, relative))
            return

        self.delete_local_resource(entry, move_to_trash)

    def scan(self, context=None):
        self._relative(self.root)
        if not self.root.is_dir():
            return {"items": [], "folders": [], "errors": ["知识库目录不存在，请在设置中选择 myself。"]}
        items, folders, errors, seen = [], {""}, [], set()
        count, text_budget = 0, self.MAX_TEXT_TOTAL
        next_text_cache = {}
        organized = (self.root / "02-知识").is_dir()
        stack = [(self.root, 0)]
        with self.store.connect() as db:
            self.reconcile_paths(db)
            self._import_legacy_records(db, errors)
            indexed = {row[0]: tuple(row) for row in db.execute(
                "SELECT relative_path,name,folder,kind,size,modified_ns,missing FROM knowledge_files WHERE root_id=?",
                (self.root_id,))}
            metadata_rows = {row[0]: tuple(row[1:]) for row in db.execute(
                "SELECT relative_path,tags,favorite,projects,agents FROM knowledge_metadata WHERE root_id=?",
                (self.root_id,))}
            kref_migration_done = db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind='kref-files:v1'", (self.root_id,)).fetchone() is not None
            kref_imported = kref_errors = kref_files = 0
            while stack:
                folder, depth = stack.pop()
                if context:
                    context.checkpoint()
                try:
                    children = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
                except OSError as exc:
                    errors.append(f"{folder.name}：{exc}")
                    continue
                for path in children:
                    if path.name.startswith(".") or path.name in self.EXCLUDED:
                        continue
                    if organized:
                        if folder == self.root and path.name != "02-知识":
                            continue
                        if path.name == "AGENTS.md":
                            continue
                    if path.name == "知识库索引" and folder.name == "99-系统":
                        continue
                    try:
                        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                            continue
                        relative = self._relative(path)
                        local_folder = Path(relative).parent.as_posix()
                        if path.is_dir():
                            folders.add(relative)
                            if depth < 12:
                                stack.append((path, depth + 1))
                            continue
                        if not path.is_file():
                            continue
                        count += 1
                        if count > self.MAX_FILES:
                            errors.append(f"索引达到 {self.MAX_FILES} 个文件上限；请通过分类目录缩小范围。")
                            stack.clear()
                            break
                        suffix = path.suffix.casefold()
                        if suffix == ".kref":
                            if kref_migration_done:
                                continue
                            kref_files += 1
                            marker = "kref:" + relative
                            if not db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?", (self.root_id, marker)).fetchone():
                                if path.stat().st_size > 64_000:
                                    raise UserError("外部引用记录超过 64 KB。")
                                record = json.loads(path.read_text(encoding="utf-8"))
                                target = Path(str(record.get("path", ""))) if isinstance(record, dict) else Path()
                                if not isinstance(record, dict) or not target.is_absolute():
                                    raise UserError("外部引用记录格式无效。")
                                key = "external:" + json.dumps(str(target), ensure_ascii=False)
                                db.execute("INSERT OR IGNORE INTO knowledge_references VALUES (?,?,?,?,?)", (self.root_id, key, str(target), str(record.get("name") or target.name), local_folder))
                                from .storage import now
                                db.execute("INSERT INTO knowledge_legacy_imports VALUES (?,?,?,?,?)", (self.root_id, marker, now(), 1, 0))
                                kref_imported += 1
                            continue
                        target = None
                        stat = path.stat()
                        kind = "link" if suffix == ".url" else "note" if suffix == ".md" else "file"
                        searchable_path = path
                        search_text = ""
                        if stat and searchable_path.suffix.casefold() in self.TEXT_EXTENSIONS and stat.st_size <= self.MAX_TEXT_FILE and text_budget:
                            key = str(searchable_path)
                            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
                            cached = self._text_cache.get(key)
                            needed = min(stat.st_size, text_budget)
                            if cached and cached[0] == signature and len(cached[1]) >= needed:
                                raw = cached[1][:text_budget]
                            else:
                                raw = searchable_path.read_bytes()[:text_budget]
                            next_text_cache[key] = (signature, raw)
                            text_budget -= len(raw)
                            search_text = raw.decode("utf-8-sig", errors="replace")
                        seen.add(relative)
                        index_row = (relative, path.stem if kind in {"link", "note"} else path.name,
                                     local_folder, kind, stat.st_size, stat.st_mtime_ns, 0)
                        if indexed.get(relative) != index_row:
                            db.execute("INSERT OR REPLACE INTO knowledge_files VALUES (?,?,?,?,?,?,?,?)", (self.root_id, *index_row))
                        row = metadata_rows.get(relative)
                        metadata = {"tags": json.loads(row[0]), "favorite": bool(row[1]), "projects": json.loads(row[2]), "agents": json.loads(row[3])} if row else {"tags": [], "favorite": False, "projects": [], "agents": []}
                        target_path = str(path)
                        items.append({"path": target_path, "relative": relative, "name": path.stem if kind in {"link", "note"} else path.name,
                                      "filename": path.name, "folder": local_folder, "kind": kind,
                                      "size": stat.st_size if stat else 0, "modified_ns": stat.st_mtime_ns if stat else 0,
                                      "suffix": suffix,
                                      "missing": False, "search_text": search_text, **metadata})
                    except (OSError, UserError, ValueError) as exc:
                        errors.append(f"{path.name}：{exc}")
                        if path.suffix.casefold() == ".kref" and not kref_migration_done:
                            kref_errors += 1
                            from .storage import now
                            db.execute("INSERT OR IGNORE INTO knowledge_legacy_imports VALUES (?,?,?,?,?)",
                                       (self.root_id, "kref:" + path.relative_to(self.root).as_posix(), now(), 0, 1))
            if not kref_migration_done and kref_files:
                from .storage import now
                db.execute("INSERT INTO knowledge_legacy_imports VALUES (?,?,?,?,?)", (self.root_id, "kref-files:v1", now(), kref_imported, kref_errors))
            # Imported external references are database records; .kref originals remain untouched.
            for reference in db.execute("SELECT relative_path,target_path,display_name,folder FROM knowledge_references WHERE root_id=?", (self.root_id,)).fetchall():
                key, target_text, display_name, folder = reference
                if organized and folder != "02-知识" and not folder.startswith("02-知识/"):
                    continue
                if key in seen:
                    continue
                target = Path(target_text)
                try:
                    stat = target.stat() if target.is_file() else None
                except OSError:
                    stat = None
                search_text = self.read_search_text(target, stat.st_size if stat else 0, text_budget)
                text_budget -= len(search_text.encode("utf-8"))
                index_row = (key, display_name, folder, "external", stat.st_size if stat else 0, stat.st_mtime_ns if stat else 0, int(stat is None))
                if indexed.get(key) != index_row:
                    db.execute("INSERT OR REPLACE INTO knowledge_files VALUES (?,?,?,?,?,?,?,?)", (self.root_id, *index_row))
                meta = metadata_rows.get(key)
                items.append({"path": target_text, "relative": key, "name": display_name, "filename": display_name,
                    "folder": folder, "kind": "external", "size": stat.st_size if stat else 0,
                    "modified_ns": stat.st_mtime_ns if stat else 0, "suffix": target.suffix.casefold(),
                    "tags": json.loads(meta[0]) if meta else [], "favorite": bool(meta[1]) if meta else False,
                    "projects": json.loads(meta[2]) if meta else [], "agents": json.loads(meta[3]) if meta else [],
                    "missing": stat is None, "search_text": search_text})
                seen.add(key)
            # Retain previously indexed external keys so entries from old index versions stay visible.
            # Retain previously indexed paths so a moved or unavailable file is reported as broken.
            for row in db.execute("SELECT relative_path,name,folder,kind,size,modified_ns FROM knowledge_files WHERE root_id=?", (self.root_id,)).fetchall():
                if row[0] in seen:
                    continue
                if not row[0].startswith("external:") and any(part.startswith(".") for part in Path(row[0]).parts):
                    continue
                if organized:
                    if row[0].startswith("external:"):
                        if row[2] != "02-知识" and not row[2].startswith("02-知识/"):
                            continue
                    elif not row[0].startswith("02-知识/"):
                        continue
                meta = metadata_rows.get(row[0])
                missing_path = Path(json.loads(row[0][len("external:"):])) if row[0].startswith("external:") else self.root / row[0]
                items.append({"path": str(missing_path), "relative": row[0], "name": row[1], "filename": Path(row[0]).name,
                    "folder": row[2], "kind": row[3], "size": row[4], "modified_ns": row[5], "suffix": Path(row[0]).suffix.casefold(),
                    "tags": json.loads(meta[0]) if meta else [], "favorite": bool(meta[1]) if meta else False,
                    "projects": json.loads(meta[2]) if meta else [], "agents": json.loads(meta[3]) if meta else [], "missing": True, "search_text": ""})
                seen.add(row[0])
                db.execute("UPDATE knowledge_files SET missing=1 WHERE root_id=? AND relative_path=? AND missing<>1", (self.root_id, row[0]))
            rows = db.execute("""SELECT m.relative_path,m.tags,m.favorite,m.projects,m.agents
                FROM knowledge_metadata m JOIN knowledge_files f
                  ON f.root_id=m.root_id AND f.relative_path=m.relative_path
                WHERE m.root_id=? AND m.relative_path LIKE 'external:%'
                  AND (f.folder='02-知识' OR f.folder LIKE '02-知识/%')""", (self.root_id,)).fetchall()
            for row in rows:
                key = row[0]
                if key in seen:
                    continue
                path = Path(json.loads(key[len("external:"):]))
                try:
                    stat = path.stat() if path.is_file() else None
                except OSError:
                    stat = None
                items.append({"path": str(path), "relative": key, "name": path.name, "filename": path.name,
                    "folder": "外部引用", "kind": "file", "size": stat.st_size if stat else 0,
                    "modified_ns": stat.st_mtime_ns if stat else 0, "suffix": path.suffix.casefold(),
                    "tags": json.loads(row[1]), "favorite": bool(row[2]), "projects": json.loads(row[3]),
                    "agents": json.loads(row[4]), "missing": stat is None,
                    "search_text": self.read_search_text(path, stat.st_size if stat else 0, text_budget)})
                text_budget -= len(items[-1]["search_text"].encode("utf-8"))
        self.enrich_resources(items, errors)
        items = self.build_bundles(items, context, errors)
        content_count = len({entry["relative"] for resource in items for entry in (resource["members"] if resource["kind"] == "bundle" else [resource])})
        for entry in items:
            if entry["kind"] == "bundle":
                directory = entry["bundle_record"].get("directory", "")
                if directory and not Path(directory).is_absolute() and directory != entry["folder"]:
                    folders = {folder for folder in folders if folder != directory and not folder.startswith(directory + "/")}
        items.sort(key=lambda item: (not item["favorite"], item["name"].casefold()))
        self._text_cache = next_text_cache
        return {"items": items, "folders": sorted(folders, key=lambda value: (value.count("/"), value.casefold())), "errors": errors, "content_count": content_count}

    def read_search_text(self, path, size, budget):
        if not budget or size > self.MAX_TEXT_FILE or path.suffix.casefold() not in self.TEXT_EXTENSIONS:
            return ""
        try:
            return path.read_bytes()[:budget].decode("utf-8-sig", errors="replace")
        except OSError:
            return ""

    def resolve_entry_path(self, relative):
        """Resolve an indexed file for display, enforcing the knowledge root boundary."""
        if not isinstance(relative, str) or not relative:
            raise UserError("资料路径无效，请刷新知识库后重试。")
        if relative.startswith("external:"):
            try:
                raw_path = json.loads(relative[len("external:"):])
            except (TypeError, ValueError) as exc:
                raise UserError("外部资料路径无效，请刷新知识库后重试。") from exc
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                raise UserError("外部资料路径无效，请刷新知识库后重试。")
            with self.store.connect() as db:
                row = db.execute("SELECT target_path FROM knowledge_references WHERE root_id=? AND relative_path=?",
                                 (self.root_id, relative)).fetchone()
            if not row or Path(row[0]).resolve(strict=False) != Path(raw_path).resolve(strict=False):
                raise UserError("外部资料引用已变化，请刷新知识库后重试。")
            path = Path(row[0])
        elif relative.startswith("bundle:"):
            raise UserError("资料路径无效，请刷新知识库后重试。")
        else:
            if Path(relative).is_absolute():
                raise UserError("资料路径必须使用知识库索引中的相对路径。")
            path = self.root / relative
            if self._relative(path) != relative:
                raise UserError("资料路径必须使用知识库索引中的相对路径。")
            with self.store.connect() as db:
                row = db.execute("SELECT 1 FROM knowledge_files WHERE root_id=? AND relative_path=?",
                                 (self.root_id, relative)).fetchone()
            if not row:
                raise UserError("资料尚未登记，请刷新知识库后重试。")
        if not path.is_file():
            raise UserError("资料文件当前不可访问，请刷新知识库后重试。")
        return path

    def read_entry_text(self, relative, limit, *, errors="replace"):
        """Read at most ``limit`` bytes from a registered entry for a UI preview."""
        if type(limit) is not int or not 1 <= limit <= 1_000_000:
            raise UserError("资料预览长度无效。")
        if not isinstance(errors, str) or errors not in {"strict", "replace", "ignore"}:
            raise UserError("资料文本解码方式无效。")
        path = self.resolve_entry_path(relative)
        with path.open("rb") as source:
            data = source.read(limit)
        return data.decode("utf-8-sig", errors=errors)

    def read_note(self, relative):
        """Read a bounded local Markdown note for editing; external files stay read-only."""
        if not isinstance(relative, str) or relative.startswith("external:"):
            raise UserError("外部文件请使用原编辑器打开。")
        if Path(relative).suffix.casefold() != ".md":
            raise UserError("仅支持编辑 Markdown 笔记。")
        path = self.resolve_entry_path(relative)
        with path.open("rb") as source:
            data = source.read(1_000_001)
        if len(data) > 1_000_000:
            raise UserError("笔记内容已超过 1 MB，请使用外部编辑器打开。")
        return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")

    def update_metadata(self, relative, tags=None, favorite=None, projects=None, agents=None):
        if relative.startswith("bundle:"):
            group = getattr(self, "bundle_entries", {}).get(relative)
            if group and not self.has_bundle(group["bundle_record"]["id"]):
                self.change_bundle(group)
            record = self.read_record(relative)
            changes = {key: value for key, value in (("tags", tags), ("favorite", favorite), ("projects", projects), ("agents", agents)) if value is not None}
            for key in ("tags", "projects", "agents"):
                if key in changes:
                    changes[key] = list(dict.fromkeys(str(value).strip() for value in changes[key] if str(value).strip()))
                    if len(changes[key]) > 100 or any(len(value) > 100 for value in changes[key]):
                        raise UserError("标签与关联最多 100 项，每项最多 100 字。")
            self.write_record(relative, {**record, **changes})
            return
        if relative.startswith("external:"):
            path = Path(json.loads(relative[len("external:"):]))
        else:
            path = self.root / relative
            self._relative(path)
        with self.store.connect() as db:
            row = db.execute("SELECT tags,favorite,projects,agents FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, relative)).fetchone()
            indexed = db.execute("SELECT 1 FROM knowledge_files WHERE root_id=? AND relative_path=?", (self.root_id, relative)).fetchone()
            if not path.is_file() and not row and not indexed:
                raise UserError("此资料尚未登记，请先刷新知识库。")
            old = [json.loads(row[0]), bool(row[1]), json.loads(row[2]), json.loads(row[3])] if row else [[], False, [], []]
            record = self.read_record(relative)
            old = [record.get(key, value) for key, value in zip(("tags", "favorite", "projects", "agents"), old)]
            values = [tags if tags is not None else old[0], favorite if favorite is not None else old[1],
                      projects if projects is not None else old[2], agents if agents is not None else old[3]]
            values[0] = list(dict.fromkeys(str(v).strip() for v in values[0] if str(v).strip()))
            if any(len(v) > 100 for v in values[0]) or len(values[0]) > 100:
                raise UserError("标签最多 100 个，每个不超过 100 字。")
            from .storage import now
            self._put_record(db, relative, {**record, **dict(zip(("tags", "favorite", "projects", "agents"), values))})

    def save_resource_details(self, relative, metadata, details):
        """Save a resource editor submission and its access timestamp atomically."""
        with self.store.atomic():
            self.update_metadata(relative, **metadata)
            self.update_resource(relative, **details)
            return self.record_access(relative)

    def save_note(self, relative, expected_text, body):
        """Save a bounded local Markdown note, rejecting concurrent edits."""
        if relative.startswith("external:"):
            raise UserError("外部文件请使用原编辑器修改。")
        path = self.root / relative
        self._relative(path)
        if path.suffix.casefold() != ".md" or not path.is_file() or path.stat().st_size > 1_000_000:
            raise UserError("仅支持编辑不超过 1 MB 的知识库 Markdown 笔记。")
        with path.open("rb") as incoming:
            original = incoming.read(1_000_001)
        if len(original) > 1_000_000:
            raise UserError("笔记内容已超过 1 MB，请使用外部编辑器打开。")
        if original.decode("utf-8-sig").replace("\r\n", "\n") != expected_text:
            raise UserError("笔记已被其他程序修改，请刷新后重新编辑。")
        if "\r\n" in original.decode("utf-8-sig"):
            body = body.replace("\r\n", "\n").replace("\n", "\r\n")
        output = body.encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf"):
            output = b"\xef\xbb\xbf" + output
        if len(output) > 1_000_000:
            raise UserError("笔记正文不能超过 1 MB。")
        temporary = temporary_path(path, uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(output)
            with path.open("rb") as current:
                unchanged = current.read(1_000_001) == original
            if not unchanged:
                raise UserError("笔记保存前发生了外部修改，请刷新后重试。")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create_folder(self, relative):
        target = self.root / relative
        self._relative(target)
        name = target.name
        if not name or name.startswith(".") or any(c in name for c in '\\:*?"<>|') or name in self.EXCLUDED:
            raise UserError("请填写有效的文件夹名称。")
        if target.exists():
            raise UserError("同名文件夹已存在。")
        target.mkdir(parents=True)

    def folder_order(self):
        """Read the user's saved topic order from the Vault-local index folder."""
        path = self.root / "99-系统" / "知识库索引" / "主题顺序.json"
        self._relative(path)
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return []
        folders = value.get("folders", []) if isinstance(value, dict) else []
        return [folder for folder in folders if isinstance(folder, str)] if isinstance(folders, list) else []

    def save_folder_order(self, folders):
        """Atomically save topic order while refusing to overwrite an external edit."""
        path = self.root / "99-系统" / "知识库索引" / "主题顺序.json"
        temporary = temporary_path(path, uuid4().hex)
        self._relative(path)
        self._relative(temporary)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            original = path.read_bytes() if path.exists() else None
            encoded = (json.dumps({"version": 1, "folders": list(folders)}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            with temporary.open("xb") as stream:
                stream.write(encoded)
            if (path.read_bytes() if path.exists() else None) != original:
                raise UserError("主题顺序已被其他程序修改，请刷新后重试。")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def add_file(self, source, folder="", copy=False, context=None):
        source = Path(source).expanduser()
        if any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)() for parent in (source, *source.parents)):
            raise UserError("请选择普通文件，不能使用符号链接。")
        source = source.resolve(strict=True)
        if not source.is_file():
            raise UserError("请选择文件。")
        destination_folder = self.root / folder
        self._relative(destination_folder)
        if not destination_folder.is_dir():
            raise UserError("请先选择现有知识库文件夹。")
        target = destination_folder / source.name
        self._relative(target)
        if target.exists():
            raise UserError("目标目录存在同名文件，请先改名后再添加。")
        if copy:
            try:
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    while True:
                        if context:
                            context.checkpoint()
                        chunk = incoming.read(1024 * 1024)
                        if not chunk:
                            break
                        outgoing.write(chunk)
                shutil.copystat(source, target)
            except FileExistsError as exc:
                raise UserError("目标目录存在同名文件，请先改名后再添加。") from exc
            except Exception as exc:
                try:
                    target.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    raise UserError(
                        f"文件复制失败且不完整副本未能清理（{type(cleanup_exc).__name__}）：{target}"
                    ) from exc
                raise
            return target
        relative = "external:" + json.dumps(str(source), ensure_ascii=False)
        with self.store.connect() as db:
            inserted = db.execute("INSERT OR IGNORE INTO knowledge_references VALUES (?,?,?,?,?)", (self.root_id, relative, str(source), source.name, folder)).rowcount
            if not inserted:
                raise UserError("此文件已登记为外部引用。")
        return source

    def import_directory(self, source, folder, title="", summary="", context=None):
        """Copy a directory first, then register a bundle pointing to the local copy."""
        source = self.safe_bundle_folder(source).resolve(strict=True)
        if not source.is_dir():
            raise UserError("请选择普通目录。")
        destination = self.root / folder
        self._relative(destination)
        target = destination / source.name
        self._relative(target)
        if target.resolve(strict=False).is_relative_to(source):
            raise UserError("保存位置不能位于原目录内，请选择其他专题。")
        if target.exists():
            raise UserError("保存位置存在同名目录，请先改名或选择其他专题。")
        destination.mkdir(parents=True, exist_ok=True)
        staging = temporary_path(target, uuid4().hex)
        self._relative(staging)
        staging.mkdir()
        published = False
        try:
            def walk_error(error):
                raise error
            for current, directories, files in os.walk(source, followlinks=False, onerror=walk_error):
                if context:
                    context.checkpoint()
                current = Path(current)
                output = staging / current.relative_to(source)
                for name in directories + files:
                    entry = current / name
                    if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                        raise UserError("目录包含符号链接或目录联接，无法完整复制：" + name)
                    if name in {".local-secrets", ".ssh", ".aws", ".azure", "auth.json", "credentials.json", "credentials", "id_rsa", "id_ed25519"} or entry.suffix.lower() in {".pem", ".key", ".pfx"}:
                        raise UserError("目录包含凭据文件，未复制：" + name)
                for name in directories:
                    (output / name).mkdir()
                for name in files:
                    entry = current / name
                    if not entry.is_file():
                        raise UserError("目录包含非普通文件，无法复制：" + name)
                    copied = output / name
                    with entry.open("rb") as incoming, copied.open("xb") as outgoing:
                        while True:
                            if context:
                                context.checkpoint()
                            chunk = incoming.read(1024 * 1024)
                            if not chunk:
                                break
                            outgoing.write(chunk)
                    shutil.copystat(entry, copied)
            # Windows rename refuses an occupied destination, including a concurrent import.
            staging.rename(target)
            published = True
            return self.create_bundle(title or source.name, directory=str(target), folder=folder, summary=summary)
        except Exception as exc:
            cleanup = target if published else staging
            self._relative(cleanup)
            if cleanup.is_dir():
                try:
                    shutil.rmtree(cleanup)
                except OSError as cleanup_exc:
                    raise UserError(
                        f"目录导入失败且部分副本未能清理（{type(cleanup_exc).__name__}）：{cleanup}"
                    ) from exc
            raise

    def rename_folder(self, relative, new_name):
        source = self.root / relative
        self._relative(source)
        new_name = new_name.strip()
        if not new_name or any(c in new_name for c in '\\/:*?"<>|') or new_name.startswith("."):
            raise UserError("请填写有效的文件夹名称。")
        target = source.parent / new_name
        self._relative(target)
        if not source.is_dir() or target.exists():
            raise UserError("文件夹不存在或目标名称已被占用。")
        source.rename(target)
        new_relative = self._relative(target)
        with self.store.connect() as db:
            for table in ("knowledge_metadata", "knowledge_files"):
                prefix = relative + "/"
                rows = db.execute(f"SELECT relative_path FROM {table} WHERE root_id=? AND (relative_path=? OR substr(relative_path,1,?)=?)",
                                  (self.root_id, relative, len(prefix), prefix)).fetchall()
                for row in rows:
                    old = row[0]
                    changed = new_relative + old[len(relative):]
                    record = self._read_record_db(db, old) if table == "knowledge_metadata" else {}
                    if table == "knowledge_metadata":
                        db.execute("UPDATE OR IGNORE knowledge_metadata SET relative_path=? WHERE root_id=? AND relative_path=?", (changed, self.root_id, old))
                        if record:
                            relocated = self.relocate_resource_details(record, relative, new_relative)
                            self._put_record(db, changed, {**relocated, "relative": changed})
                    else:
                        db.execute("UPDATE OR IGNORE knowledge_files SET relative_path=?, folder=? WHERE root_id=? AND relative_path=?",
                                   (changed, Path(changed).parent.as_posix(), self.root_id, old))
            prefix = relative + "/"
            db.execute("UPDATE knowledge_references SET folder=? || substr(folder,?) WHERE root_id=? AND (folder=? OR substr(folder,1,?)=?)",
                       (new_relative, len(relative) + 1, self.root_id, relative, len(prefix), prefix))
            self.relocate_bundle_references(relative, new_relative, db=db)

    def move_file(self, relative, destination_folder):
        if relative.startswith("external:"):
            folder = self.root / destination_folder
            self._relative(folder)
            if not folder.is_dir():
                raise UserError("请选择现有知识库文件夹。")
            with self.store.connect() as db:
                result = db.execute("UPDATE knowledge_references SET folder=? WHERE root_id=? AND relative_path=?", (destination_folder, self.root_id, relative))
                if result.rowcount != 1:
                    raise UserError("外部引用尚未登记，请刷新知识库。")
            return relative
        source = self.root / relative
        self._relative(source)
        folder = self.root / destination_folder
        self._relative(folder)
        if not source.is_file() or source.is_symlink() or getattr(source, "is_junction", lambda: False)() or not folder.is_dir():
            raise UserError("所选资料或目标目录当前不可用。")
        if source.parent.resolve() == folder.resolve():
            return relative
        target = folder / source.name
        new_relative = self._relative(target)
        if target.exists():
            raise UserError("目标目录存在同名文件，请先改名。")
        moved = False
        record = self.read_record(relative)
        try:
            with self.store.connect() as db:
                source.rename(target)
                moved = True
                db.execute("UPDATE knowledge_metadata SET relative_path=? WHERE root_id=? AND relative_path=?", (new_relative, self.root_id, relative))
                db.execute("UPDATE knowledge_files SET relative_path=?,folder=? WHERE root_id=? AND relative_path=?",
                           (new_relative, Path(new_relative).parent.as_posix(), self.root_id, relative))
                if record:
                    relocated = self.relocate_resource_details(record, relative, new_relative)
                    self._put_record(db, new_relative, {**relocated, "relative": new_relative})
                self.relocate_bundle_references(relative, new_relative, db=db)
        except Exception:
            if moved and target.exists() and not source.exists():
                target.rename(source)
            raise
        return new_relative

    def move_bundle(self, entry, destination_folder):
        """Move a bundle and its backing files as one recoverable operation."""
        record = dict(entry.get("bundle_record") or {})
        identity = record.get("id")
        if not identity:
            raise UserError("资源合集登记无效，请刷新知识库后重试。")
        destination = self.root / destination_folder
        self._relative(destination)
        if not destination.is_dir():
            raise UserError("请选择现有知识库文件夹。")

        directory = record.get("directory", "")
        if directory and not Path(directory).is_absolute():
            if directory == ".":
                raise UserError("不能将知识库根目录作为单条资料移动。")
            original_directory = Path(directory)
            original_parent = original_directory.parent.as_posix()
            moved_directory = (Path(destination_folder) / original_directory.name).as_posix()
            moved = False
            try:
                if original_parent != destination_folder:
                    self.move_folder(directory, destination_folder)
                    moved = True
                current = next((item for item in self.read_bundles([]) if item.get("id") == identity), record)
                current.update(folder=destination_folder, directory=moved_directory)
                self.save_bundle(current)
            except Exception as exc:
                if moved:
                    try:
                        self.move_folder(moved_directory, original_parent)
                    except Exception as rollback_exc:
                        raise UserError(
                            f"资源目录移动失败且回滚未完成（{type(rollback_exc).__name__}）：{moved_directory}"
                        ) from exc
                raise
            return

        members = [member for member in entry.get("members", []) if not member.get("missing")]
        moved_members = []
        try:
            for member in members:
                relative = member["relative"]
                old_folder = member.get("folder", "")
                new_relative = self.move_file(relative, destination_folder)
                if relative.startswith("external:"):
                    if old_folder != destination_folder:
                        moved_members.append((relative, relative, old_folder))
                elif new_relative != relative:
                    moved_members.append((relative, new_relative, Path(relative).parent.as_posix()))
            current = next((item for item in self.read_bundles([]) if item.get("id") == identity), record)
            current["folder"] = destination_folder
            self.save_bundle(current)
        except Exception as exc:
            rollback_errors = []
            for _old, current, original_folder in reversed(moved_members):
                try:
                    if current.startswith("external:"):
                        self.move_file(current, original_folder)
                    else:
                        self.move_file(current, original_folder)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            try:
                current_record = next((item for item in self.read_bundles([]) if item.get("id") == identity), record)
                current_record["folder"] = record.get("folder", "")
                self.save_bundle(current_record)
            except Exception as rollback_exc:
                rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                raise UserError("资源合集移动失败，部分成员回滚未完成：" + ", ".join(rollback_errors)) from exc
            raise

    def archive_resource(self, entry):
        """Move local originals to 98-归档 and keep enough metadata to restore them."""
        relative = entry["relative"]
        record = self.read_record(relative)
        has_restore_location = any(record.get(key) for key in ("archive_original", "archive_original_directory", "archive_member_locations"))
        # Older versions only toggled a flag. Move those legacy archived items now;
        # the next click can then restore them using the recorded source location.
        archiving = not bool(entry.get("archived")) or not has_restore_location
        archive_root = self.root / "98-归档"
        self._relative(archive_root)
        archive_root.mkdir(parents=True, exist_ok=True)

        if entry["kind"] == "bundle":
            bundle = entry["bundle_record"]
            directory = bundle.get("directory", "")
            if directory and not Path(directory).is_absolute():
                if directory == ".":
                    raise UserError("不能把知识库根目录作为单个资源归档。")
                if archiving:
                    old_directory = directory
                    target_folder = (Path("98-归档") / directory).parent.as_posix()
                    (self.root / target_folder).mkdir(parents=True, exist_ok=True)
                    self.move_folder(directory, target_folder)
                    self.update_resource(relative, archived=True, archive_original_directory=old_directory)
                else:
                    original = record.get("archive_original_directory", "")
                    if not original:
                        raise UserError("找不到此资料合集的原始目录，无法恢复。")
                    (self.root / Path(original).parent).mkdir(parents=True, exist_ok=True)
                    self.move_folder(directory, Path(original).parent.as_posix())
                    self.update_resource(relative, archived=False, archive_original_directory="")
                return
            locations = record.get("archive_member_locations", {})
            if archiving:
                moved_locations = {}
                for member in entry.get("members", []):
                    old = member["relative"]
                    external_member = old.startswith("external:")
                    original = member["folder"] if external_member else old
                    destination = (Path("98-归档") / Path(original)).as_posix() if external_member else (Path("98-归档") / Path(original).parent).as_posix()
                    (self.root / destination).mkdir(parents=True, exist_ok=True)
                    new = self.move_file(old, destination)
                    self.update_resource(new if external_member else new, archived=True, archive_original=original)
                    moved_locations[new] = original
                self.update_resource(relative, archived=True, archive_member_locations=moved_locations)
            else:
                for current, original in locations.items():
                    destination = original if current.startswith("external:") else Path(original).parent.as_posix()
                    (self.root / destination).mkdir(parents=True, exist_ok=True)
                    new = self.move_file(current, destination)
                    self.update_resource(new, archived=False, archive_original="")
                self.update_resource(relative, archived=False, archive_member_locations={})
            return

        external = relative.startswith("external:")
        physical = (entry["folder"] if external else relative)
        if archiving:
            destination = (Path("98-归档") / Path(physical)).as_posix() if external else (Path("98-归档") / Path(physical).parent).as_posix()
            (self.root / destination).mkdir(parents=True, exist_ok=True)
            moved = self.move_file(relative if external else physical, destination)
            self.update_resource(relative if external else moved, archived=True, archive_original=physical)
            old_parent = self.root / Path(physical).parent
            topic_root = self.root / "02-知识"
            if old_parent.is_relative_to(topic_root):
                while old_parent != topic_root and old_parent != old_parent.parent:
                    try:
                        old_parent.rmdir()
                    except OSError:
                        break
                    old_parent = old_parent.parent
                    if old_parent.parent == topic_root:
                        break
        else:
            original = record.get("archive_original", "")
            if not original:
                raise UserError("找不到此资源的原始位置，无法恢复。")
            destination = original if external else Path(original).parent.as_posix()
            (self.root / destination).mkdir(parents=True, exist_ok=True)
            moved = self.move_file(relative if external else physical, destination)
            self.update_resource(relative if external else moved, archived=False, archive_original="")

    def move_folder(self, relative, destination_folder):
        source = self.root / relative
        destination = self.root / destination_folder
        self._relative(source)
        self._relative(destination)
        if not source.is_dir() or source.is_symlink() or getattr(source, "is_junction", lambda: False)() or not destination.is_dir():
            raise UserError("所选文件夹或目标目录当前不可用。")
        target = destination / source.name
        new_relative = self._relative(target)
        if target.exists():
            raise UserError("归档目录已存在同名文件夹。")
        source.rename(target)
        try:
            with self.store.connect() as db:
                for table in ("knowledge_metadata", "knowledge_files"):
                    prefix = relative + "/"
                    rows = db.execute(f"SELECT relative_path FROM {table} WHERE root_id=? AND (relative_path=? OR substr(relative_path,1,?)=?)",
                                      (self.root_id, relative, len(prefix), prefix)).fetchall()
                    for row in rows:
                        old = row[0]
                        changed = new_relative + old[len(relative):]
                        record = self._read_record_db(db, old) if table == "knowledge_metadata" else {}
                        if table == "knowledge_metadata":
                            db.execute("UPDATE OR IGNORE knowledge_metadata SET relative_path=? WHERE root_id=? AND relative_path=?", (changed, self.root_id, old))
                            if record:
                                relocated = self.relocate_resource_details(record, relative, new_relative)
                                self._put_record(db, changed, {**relocated, "relative": changed})
                        else:
                            db.execute("UPDATE OR IGNORE knowledge_files SET relative_path=?, folder=? WHERE root_id=? AND relative_path=?",
                                       (changed, Path(changed).parent.as_posix(), self.root_id, old))
                prefix = relative + "/"
                db.execute("UPDATE knowledge_references SET folder=? || substr(folder,?) WHERE root_id=? AND (folder=? OR substr(folder,1,?)=?)",
                           (new_relative, len(relative) + 1, self.root_id, relative, len(prefix), prefix))
                self.relocate_bundle_references(relative, new_relative, db=db)
        except Exception:
            if target.exists() and not source.exists():
                target.rename(source)
            raise

    def delete_empty_folder(self, relative):
        target = self.root / relative
        self._relative(target)
        if not relative or not target.is_dir():
            raise UserError("请选择一个非根目录文件夹。")
        if any(target.iterdir()):
            raise UserError("文件夹非空；知识库只允许从界面删除空文件夹。")
        target.rmdir()

    def add_url(self, name, url, folder="00-收件箱"):
        name, url = name.strip(), url.strip()
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not name or len(name) > 180 or any(c in name for c in '\\/:*?"<>|') or not valid_url or "\n" in url or "\r" in url:
            raise UserError("请填写名称和有效的 http / https 网址。")
        destination = self.root / folder
        self._relative(destination)
        if not destination.is_dir():
            destination = self.root
        path = destination / (name + ".url")
        self._relative(path)
        if path.exists():
            raise UserError("目标目录存在同名网址记录，请更换名称。")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write("[InternetShortcut]\nURL=" + url + "\n")
        return path

    def add_note(self, name, body, folder="00-收件箱"):
        name = name.strip()
        if not name or len(name) > 180 or any(c in name for c in '\\/:*?"<>|'):
            raise UserError("请填写有效的笔记名称。")
        destination = self.root / folder
        self._relative(destination)
        if not destination.is_dir():
            destination = self.root
        path = destination / (name + ".md")
        self._relative(path)
        if path.exists():
            raise UserError("目标目录存在同名笔记，请更换名称。")
        content = "# " + name + "\n\n" + body
        if len(content.encode("utf-8")) > 1_000_000:
            raise UserError("笔记正文不能超过 1 MB，请使用外部编辑器创建大文件。")
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
        return path
