"""One-time, explicit migration to selfManger; never imported by the application."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from contextlib import closing


OLD_SERVICE = "personal-agent-manager"
NEW_SERVICE = "selfManger"


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ordinary_tree(root):
    records = {}
    for directory, subdirs, files in os.walk(root, followlinks=False):
        for name in ["", *subdirs, *files]:
            path = Path(directory) / name if name else Path(directory)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("Migration source contains a link; inspect it before proceeding")
        for name in files:
            path = Path(directory) / name
            if not path.is_file():
                raise RuntimeError("Migration source contains a non-regular file")
            records[path.relative_to(root).as_posix()] = fingerprint(path)
    return records


def counts(db):
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}


def migrate_directory(source, destination, snapshot, *, apply=False):
    source, destination, snapshot = map(lambda p: Path(p).absolute(), (source, destination, snapshot))
    if source.parent != destination.parent or destination.exists() or snapshot.exists() or snapshot.resolve().is_relative_to(source.resolve()):
        raise RuntimeError("Directory migration requires a new sibling target and separate snapshot")
    records = ordinary_tree(source)
    if not apply:
        return {"files": len(records), "apply": False}
    shutil.copytree(source, snapshot)
    if ordinary_tree(snapshot) != records or ordinary_tree(source) != records:
        raise RuntimeError("Directory snapshot verification failed")
    source.rename(destination)
    if ordinary_tree(destination) != records:
        destination.rename(source)
        raise RuntimeError("Directory content changed during migration")
    return {"files": len(records), "snapshot_verified": True, "migrated": True}


def migrate_workbench_references(database, source, destination, snapshot, knowledge_root, *, apply=False):
    """Follow the explicit notes move, including import identities to prevent duplicates."""
    database, source, destination, snapshot, knowledge_root = map(lambda p: Path(p).absolute(),
        (database, source, destination, snapshot, knowledge_root))
    if source.exists() or not destination.is_dir() or snapshot.exists() or not database.is_file():
        raise RuntimeError("Move notes first; use an existing database and a new snapshot file")
    old = source.relative_to(knowledge_root).as_posix()
    new = destination.relative_to(knowledge_root).as_posix()
    root_id = hashlib.sha256(str(knowledge_root.resolve()).casefold().encode()).hexdigest()
    old_ref = "legacy:daily:" + str(source) + ":"
    new_ref = "legacy:daily:" + str(destination) + ":"
    old_plan_marker = "personal_plans_import:" + str(source)
    new_plan_marker = "personal_plans_import:" + str(destination)
    with closing(sqlite3.connect(database)) as db:
        before = counts(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        refs = [(identity, value, new_ref + value[len(old_ref):]) for identity, value in
                db.execute("SELECT id,source_ref FROM planned_tasks WHERE source_ref IS NOT NULL") if value.startswith(old_ref)]
        files = [(table, rowid, relative, new + relative[len(old):])
                 for table in ("knowledge_files", "knowledge_metadata")
                 for rowid, relative in db.execute("SELECT rowid,relative_path FROM " + table + " WHERE root_id=?", (root_id,))
                 if relative == old or relative.startswith(old + "/")]
        reference_folders = [(rowid, folder, new + folder[len(old):])
                             for rowid, folder in (db.execute("SELECT rowid,folder FROM knowledge_references WHERE root_id=?", (root_id,)) if "knowledge_references" in tables else [])
                             if folder == old or folder.startswith(old + "/")]
        bundle_records = []
        for rowid, raw in (db.execute("SELECT rowid,record FROM knowledge_bundles WHERE root_id=?", (root_id,)) if "knowledge_bundles" in tables else []):
            value = json.loads(raw)
            changed = False
            for key in ("folder", "directory", "entry"):
                item = value.get(key, "")
                if isinstance(item, str) and not Path(item).is_absolute() and (item == old or item.startswith(old + "/")):
                    value[key] = new + item[len(old):]
                    changed = True
            for key in ("members", "excluded"):
                items = value.get(key, [])
                updated = [new + item[len(old):] if isinstance(item, str) and not item.startswith("external:") and (item == old or item.startswith(old + "/")) else item for item in items]
                changed = changed or updated != items
                value[key] = updated
            if changed:
                bundle_records.append((rowid, json.dumps(value, ensure_ascii=False)))
        legacy_markers = [(rowid, marker, "kref:" + new + marker[len("kref:" + old):])
                          for rowid, marker in (db.execute("SELECT rowid,source_kind FROM knowledge_legacy_imports WHERE root_id=?", (root_id,)) if "knowledge_legacy_imports" in tables else [])
                          if marker.startswith("kref:" + old) and (marker == "kref:" + old or marker.startswith("kref:" + old + "/"))]
        personal_roots = [row[0] for row in db.execute(
            "SELECT entity_id FROM personal_markdown_documents WHERE root_path=?", (str(source),))]
        personal_origins = [(identity, value, str(destination) + value[len(str(source)):])
                            for identity, value in db.execute(
                                "SELECT id,origin_path FROM personal_plans WHERE origin_path IS NOT NULL")
                            if value == str(source) or value.startswith(str(source) + os.sep)]
        daily_import = db.execute("SELECT value FROM settings WHERE key='legacy_daily_task_import_v2'").fetchone()
        old_plan_import = db.execute("SELECT value FROM settings WHERE key=?", (old_plan_marker,)).fetchone()
        new_plan_import = db.execute("SELECT value FROM settings WHERE key=?", (new_plan_marker,)).fetchone()
        if old_plan_import and new_plan_import:
            raise RuntimeError("Destination already has a personal-plan import marker")
        daily_marker_changed = False
        if daily_import:
            try:
                daily_marker_changed = transform(json.loads(daily_import[0]), source, destination) != json.loads(daily_import[0])
            except (TypeError, ValueError):
                raise RuntimeError("Daily task import marker is invalid")
        workbench_setting = db.execute("SELECT value FROM settings WHERE key='workbench_root'").fetchone()
        workbench_changed = False
        if workbench_setting:
            try:
                workbench_value = json.loads(workbench_setting[0])
                workbench_changed = transform(workbench_value, source, destination) != workbench_value
            except (TypeError, ValueError):
                raise RuntimeError("Workbench root setting is invalid")
        result = {"task_references": len(refs), "knowledge_references": len(files),
                  "knowledge_reference_folders": len(reference_folders), "knowledge_bundles": len(bundle_records),
                  "knowledge_import_markers": len(legacy_markers),
                  "personal_document_roots": len(personal_roots), "personal_plan_origins": len(personal_origins),
                  "daily_import_marker": int(daily_marker_changed),
                  "personal_plan_import_marker": int(bool(old_plan_import)),
                  "workbench_root_setting": int(workbench_changed)}
        if not apply:
            return result
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(snapshot)) as copy:
            db.backup(copy)
            if counts(copy) != before or copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Reference snapshot verification failed")
        with db:
            row = db.execute("SELECT value FROM settings WHERE key='legacy_task_import_v1'").fetchone()
            if row:
                marker = json.loads(row[0])
                updated = transform(marker, source, destination)
                db.execute("UPDATE settings SET value=? WHERE key='legacy_task_import_v1'", (json.dumps(updated, ensure_ascii=False),))
            if daily_import and daily_marker_changed:
                marker = transform(json.loads(daily_import[0]), source, destination)
                db.execute("UPDATE settings SET value=? WHERE key='legacy_daily_task_import_v2'",
                           (json.dumps(marker, ensure_ascii=False),))
            if old_plan_import:
                db.execute("UPDATE settings SET key=? WHERE key=?", (new_plan_marker, old_plan_marker))
            if workbench_setting and workbench_changed:
                value = transform(json.loads(workbench_setting[0]), source, destination)
                db.execute("UPDATE settings SET value=? WHERE key='workbench_root'",
                           (json.dumps(value, ensure_ascii=False),))
            for identity, previous, updated in refs:
                db.execute("UPDATE planned_tasks SET source_ref=? WHERE id=?", (updated, identity))
            for identity, previous, updated in personal_origins:
                db.execute("UPDATE personal_plans SET origin_path=? WHERE id=?", (updated, identity))
            if personal_roots:
                db.execute("UPDATE personal_markdown_documents SET root_path=? WHERE root_path=?",
                           (str(destination), str(source)))
            for table, rowid, previous, updated in files:
                db.execute("UPDATE " + table + " SET relative_path=? WHERE rowid=?", (updated, rowid))
                if table == "knowledge_files":
                    folder = str(Path(updated).parent).replace("\\", "/")
                    db.execute("UPDATE knowledge_files SET folder=? WHERE rowid=?", (folder, rowid))
                elif "details" in {column[1] for column in db.execute("PRAGMA table_info(knowledge_metadata)")}:
                    raw = db.execute("SELECT details FROM knowledge_metadata WHERE rowid=?", (rowid,)).fetchone()
                    if raw:
                        details = json.loads(raw[0] or "{}")
                        def move_value(value):
                            return new + value[len(old):] if isinstance(value, str) and not value.startswith(("external:", "bundle:")) and (value == old or value.startswith(old + "/")) else value
                        for key in ("archive_original", "archive_original_directory"):
                            if key in details:
                                details[key] = move_value(details[key])
                        if isinstance(details.get("related"), list):
                            details["related"] = [move_value(value) for value in details["related"]]
                        if isinstance(details.get("archive_member_locations"), dict):
                            details["archive_member_locations"] = {move_value(key): move_value(value) for key, value in details["archive_member_locations"].items()}
                        db.execute("UPDATE knowledge_metadata SET details=? WHERE rowid=?", (json.dumps(details, ensure_ascii=False), rowid))
            for rowid, _, updated in reference_folders:
                db.execute("UPDATE knowledge_references SET folder=? WHERE rowid=?", (updated, rowid))
            for rowid, updated in bundle_records:
                db.execute("UPDATE knowledge_bundles SET record=? WHERE rowid=?", (updated, rowid))
            for rowid, _, updated in legacy_markers:
                db.execute("UPDATE knowledge_legacy_imports SET source_kind=? WHERE rowid=?", (updated, rowid))
            if counts(db) != before or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Reference migration verification failed")
        return {**result, "snapshot_verified": True}


def transform(value, old_root, new_root):
    if isinstance(value, dict):
        return {key: transform(item, old_root, new_root) for key, item in value.items()}
    if isinstance(value, list):
        return [transform(item, old_root, new_root) for item in value]
    if isinstance(value, str):
        # Only application-owned path prefixes; do not replace user prose/logs.
        for old, new in ((str(old_root), str(new_root)),
                         (str(Path(__file__).resolve().parents[2] / "agent-manager"), str(Path(__file__).resolve().parents[1]))):
            if value == old or value.startswith(old + os.sep):
                return new + value[len(old):]
        if value.startswith(".xiaonian/"):
            return ".selfManger/" + value[len(".xiaonian/"):]
    return value


def project_plan(workspace):
    if workspace is None:
        return [], []
    workspace = Path(workspace).resolve()
    registry = json.loads((workspace / "agent/projects.json").read_text(encoding="utf-8-sig"))
    renames, edits = [], []
    for item in registry["projects"]:
        project = workspace / item["path"]
        if not project.resolve().is_relative_to(workspace):
            raise RuntimeError("Registered project escapes workspace")
        old = project / ".xiaonian"
        new = project / ".selfManger"
        if old.exists():
            if new.exists():
                raise RuntimeError("Project already has both old and new configuration directories")
            ordinary_tree(old)
            renames.append((old, new))
        for relative in ("AGENTS.md", "agent/README.md", "agent/PROMPTS.md", "agent/TASKS-AI.md", "agent/TASKS-MANUAL.md", ".xiaonian/config.json", ".xiaonian/RESOURCES.md"):
            path = project / relative
            if not path.is_file():
                continue
            if path.is_symlink() or path.stat().st_size > 2_000_000:
                raise RuntimeError("Generated management file requires manual inspection")
            raw = path.read_bytes()
            before = raw.decode("utf-8-sig")
            after = before.replace("<!-- agent-manager:tasks:", "<!-- selfManger:tasks:")
            after = after.replace(".xiaonian/", ".selfManger/").replace("小年助手项目配置", "selfManger项目配置")
            if after != before:
                edits.append((path, raw, after.encode("utf-8")))
    return renames, edits


def migrate(source, destination, snapshot, *, workspace=None, keyring=None, apply=False):
    source, destination, snapshot = map(lambda p: Path(p).absolute(), (source, destination, snapshot))
    if source == destination or destination.exists() or snapshot.exists():
        raise RuntimeError("Use distinct existing source and new destination/snapshot paths")
    if source.parent != destination.parent or snapshot.resolve().is_relative_to(source.resolve()):
        raise RuntimeError("Data rename must stay in the same parent; snapshot must be outside source")
    original = ordinary_tree(source)
    db_path = source / "manager.sqlite3"
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Source database integrity check failed")
        before_counts = counts(db)
        resource_ids = [row[0] for row in db.execute("SELECT id FROM resources")]
    renames, edits = project_plan(workspace)
    result = {"counts": before_counts, "project_directories": len(renames), "management_files": len(edits), "credential_count": 0}
    if not apply:
        return result
    snapshot.mkdir(parents=True)
    shutil.copytree(source, snapshot / "application-data")
    if ordinary_tree(snapshot / "application-data") != original or ordinary_tree(source) != original:
        raise RuntimeError("Snapshot differs from source; no migration performed")
    for i, (old, new) in enumerate(renames):
        shutil.copytree(old, snapshot / "project-configs" / str(i))
        if ordinary_tree(old) != ordinary_tree(snapshot / "project-configs" / str(i)):
            raise RuntimeError("Project snapshot verification failed")
    for i, (path, raw, after) in enumerate(edits):
        (snapshot / "management-files").mkdir(exist_ok=True)
        (snapshot / "management-files" / str(i)).write_bytes(raw)
    credentials = {}
    if keyring is not None:
        identities = {"common:backup", "git:https-token", *(identity + ":backup" for identity in resource_ids)}
        current = keyring.get_credential(OLD_SERVICE, None)
        if current:
            identities.add(current.username)
        for identity in identities:
            value = keyring.get_password(OLD_SERVICE, identity)
            if value is None:
                continue
            existing = keyring.get_password(NEW_SERVICE, identity)
            if existing is not None and existing != value:
                raise RuntimeError("Destination credential conflicts; no overwrite")
            credentials[identity] = value
        for identity, value in credentials.items():
            keyring.set_password(NEW_SERVICE, identity, value)
            if keyring.get_password(NEW_SERVICE, identity) != value:
                raise RuntimeError("New credential storage verification failed")
    changed = []
    moved = []
    source.rename(destination)
    try:
        with closing(sqlite3.connect(destination / "manager.sqlite3")) as db, db:
            for table, column in (("settings", "value"), ("resources", "document")):
                for rowid, text in db.execute(f'SELECT rowid, "{column}" FROM "{table}"').fetchall():
                    value = json.loads(text)
                    updated = transform(value, source, destination)
                    if updated != value:
                        db.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (json.dumps(updated, ensure_ascii=False), rowid))
            if counts(db) != before_counts or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Migrated database verification failed")
        for path, raw, after in edits:
            if path.read_bytes() != raw:
                raise RuntimeError("Management file changed during migration")
            path.write_bytes(after)
            changed.append((path, raw, after))
        for old, new in renames:
            old.rename(new)
            moved.append((old, new))
        for identity in credentials:
            keyring.delete_password(OLD_SERVICE, identity)
        result["credential_count"] = len(credentials)
        (snapshot / "result.json").write_text(json.dumps({**result, "source": str(source), "destination": str(destination),
            "renamed_project_configs": [[str(a),str(b)] for a,b in moved], "edited_files": [str(p) for p,_,_ in changed],
            "snapshot_verified": True, "database_integrity": "ok"}, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    except Exception:
        for old, new in reversed(moved):
            new.rename(old)
        for path, raw, after in reversed(changed):
            if path.read_bytes() == after:
                path.write_bytes(raw)
        # Restore the database from the verified snapshot before restoring its name.
        shutil.copy2(snapshot / "application-data/manager.sqlite3", destination / "manager.sqlite3")
        destination.rename(source)
        for identity, value in credentials.items():
            keyring.set_password(OLD_SERVICE, identity, value)
        raise
    finally:
        credentials.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--system-credentials", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--directory-only", action="store_true")
    parser.add_argument("--references-database", type=Path)
    parser.add_argument("--knowledge-root", type=Path)
    options = parser.parse_args()
    if options.references_database:
        if not options.knowledge_root:
            parser.error("--references-database requires --knowledge-root")
        print(json.dumps(migrate_workbench_references(options.references_database, options.source, options.destination,
            options.snapshot, options.knowledge_root, apply=options.apply), ensure_ascii=False))
        return
    keyring = __import__("keyring") if options.system_credentials else None
    result = (migrate_directory(options.source, options.destination, options.snapshot, apply=options.apply)
              if options.directory_only else migrate(options.source, options.destination, options.snapshot, workspace=options.workspace, keyring=keyring, apply=options.apply))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
