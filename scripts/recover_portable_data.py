"""Explicit additive recovery from a known portable database; no startup fallback."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from selfManger.portable import decode_paths


def table_counts(db):
    return {name: db.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not name.startswith("sqlite_")}


def recover(source, target, snapshot, *, apply=False):
    source, target, snapshot = (Path(p).resolve() for p in (source, target, snapshot))
    if not source.is_file() or not target.is_file() or source == target or snapshot.exists():
        raise RuntimeError("Use two existing distinct databases and a new snapshot directory")
    for database in (source, target):
        if snapshot.is_relative_to(database.parent):
            raise RuntimeError("Snapshots must be outside both live data directories")
    root = source.parent.parent
    if not (root / "portable.json").is_file():
        raise RuntimeError("Source must be the explicitly selected portable installation")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old, closing(sqlite3.connect(target)) as new:
        for db in (old, new):
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database integrity check failed")
        before = table_counts(new)
        added, conflicts, rows = {}, {}, {}
        for table in ("resources", "settings", "tasks", "evidence"):
            columns = [r[1] for r in old.execute("PRAGMA table_info(" + table + ")")]
            if columns != [r[1] for r in new.execute("PRAGMA table_info(" + table + ")")]:
                raise RuntimeError("Recovery table schema differs: " + table)
            records = []
            for row in old.execute("SELECT * FROM " + table):
                values = list(row)
                if table != "tasks":
                    value = decode_paths(json.loads(values[1]), root)
                    if table == "resources":
                        options = value.get("options", {})
                        old_code = root.parent / "agent-manager"
                        if options.get("path") and Path(options["path"]).resolve() == old_code:
                            options["path"] = str(Path(__file__).resolve().parents[1])
                            if value.get("name") == "Agent 管家源代码":
                                value["name"] = "selfManger 源代码"
                    values[1] = json.dumps(value, ensure_ascii=False)
                records.append(tuple(values))
            rows[table] = (columns, records)
        if not apply:
            return {"source_counts": table_counts(old), "target_counts": before}
        snapshot.mkdir(parents=True)
        for label, db in (("portable-source.sqlite", old), ("current-before.sqlite", new)):
            with closing(sqlite3.connect(snapshot / label)) as copy:
                db.backup(copy)
                if table_counts(copy) != table_counts(db) or copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Recovery snapshot verification failed")
        with new:
            new.execute("BEGIN IMMEDIATE")
            # Recheck counts under the write lock; never replace existing records.
            before = table_counts(new)
            for table, (columns, records) in rows.items():
                added[table] = conflicts[table] = 0
                key = columns[0]
                for values in records:
                    existing = new.execute('SELECT * FROM ' + table + ' WHERE "' + key + '"=?', (values[0],)).fetchone()
                    if existing is not None:
                        conflicts[table] += tuple(existing) != values
                        continue
                    new.execute("INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in values) + ")", values)
                    added[table] += 1
            after = table_counts(new)
            if any(after[t] != count + added.get(t, 0) for t, count in before.items()):
                raise RuntimeError("Recovery record counts differ")
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Recovered database integrity check failed")
        result = {"before": before, "added": added, "existing_conflicts_preserved": conflicts, "after": after,
                  "integrity": "ok", "snapshots_verified": True}
        (snapshot / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "target", "snapshot"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(recover(args.source, args.target, args.snapshot, apply=args.apply)))
