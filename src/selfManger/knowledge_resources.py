"""Portable resource descriptions and semantic presentation for the knowledge index."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import yaml

from .domain import UserError
from .naming import readable_name


def topic_name(value: str) -> str:
    return re.sub(r"^\d+[-_]", "", value)


def resource_type(entry: dict) -> str:
    if entry["kind"] == "bundle":
        return entry.get("package_type", "资料合集")
    if entry.get("missing"):
        return "位置失效"
    if entry["kind"] == "link":
        return "GitHub 项目" if "github.com" in entry.get("source_url", "").lower() else "网页收藏"
    if entry["kind"] == "external" or entry.get("relative", "").startswith("external:"):
        return "外部引用"
    if entry.get("suffix") in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}:
        return "图片素材"
    if entry.get("suffix") in {".py", ".js", ".ts", ".java", ".ps1", ".sh", ".cpp", ".yaml", ".yml"}:
        return "代码片段"
    if "Prompts" in entry.get("folder", ""):
        return "Prompt"
    return "知识笔记" if entry["kind"] == "note" else "参考资料"


def content_summary(text: str) -> str:
    """A literal excerpt, never an invented summary."""
    _, text = note_header(text)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "```", "!", "|", "<", "---")):
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = line.replace("[[", "").replace("]]", "").strip("*>- `")
        if len(line) >= 6:
            return line[:150]
    return ""


def note_header(text: str) -> tuple[dict, str]:
    """Read imported headers without swallowing a body when the closing --- is absent."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    end = 1
    for end, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            try:
                value = yaml.safe_load("".join(lines[1:end]))
                return value if isinstance(value, dict) else {}, "".join(lines[end + 1:])
            except yaml.YAMLError:
                return {}, text
        if line.lstrip().startswith(("#", "**", "```")):
            break
    else:
        return {}, text
    try:
        value = yaml.safe_load("".join(lines[1:end]))
        return value if isinstance(value, dict) else {}, "".join(lines[end:])
    except yaml.YAMLError:
        return {}, text


class PortableResources:
    """SQLite owns mutable resource metadata; legacy sidecars are import-only."""

    RECORD_SUFFIX = ".json"

    def record_path(self, relative):
        """``资源/<资料名>-<12位摘要>.json`` — readable first, digest for uniqueness."""
        directory = self.root / "99-系统" / "知识库索引" / "资源"
        self._relative(directory)
        return directory / (self._record_stem(relative) + self.RECORD_SUFFIX)

    def legacy_record_path(self, relative):
        """Hash-only names written before 2026-10-17; kept readable-compatible."""
        directory = self.root / "99-系统" / "知识库索引" / "资源"
        self._relative(directory)
        return directory / (hashlib.sha256(relative.encode("utf-8")).hexdigest() + self.RECORD_SUFFIX)

    def _record_stem(self, relative):
        digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
        text = str(relative)
        if text.startswith("bundle:"):
            name = text[len("bundle:"):]
        elif text.startswith("external:"):
            name = Path(text[len("external:"):]).name
        else:
            name = Path(text).stem or Path(text).name
        readable = readable_name(name, 60)
        return (readable + "-" + digest) if readable else digest

    def _existing_record(self, relative):
        path = self.record_path(relative)
        return path if path.exists() else path.with_name(hashlib.sha256(relative.encode("utf-8")).hexdigest() + self.RECORD_SUFFIX)

    def read_record(self, relative):
        with self.store.connect() as db:
            errors = []
            done = db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind='resource-sidecars:v1'", (self.root_id,)).fetchone()
            if not done:
                self._import_legacy_record(db, self.record_path(relative), errors)
                self._import_legacy_record(db, self.legacy_record_path(relative), errors)
            if errors:
                raise UserError(errors[0])
            return self._read_record_db(db, relative)

    def _read_record_db(self, db, relative):
        row = db.execute("SELECT tags,favorite,projects,agents,details FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, relative)).fetchone()
        if not row:
            return {}
        value = {"relative": relative, "tags": json.loads(row[0]), "favorite": bool(row[1]),
                 "projects": json.loads(row[2]), "agents": json.loads(row[3]), **json.loads(row[4] or "{}")}
        self._validate_resource_record(value, relative)
        return value

    def _validate_resource_record(self, value, relative):
        if not isinstance(value, dict) or value.get("relative") != relative:
            raise UserError("资源描述记录与资料路径不一致。")
        if not isinstance(relative, str) or not relative or len(relative) > 2000:
            raise UserError("资源身份无效。")
        if relative.startswith("external:"):
            try:
                external_path = json.loads(relative[len("external:"):])
            except ValueError as exc:
                raise UserError("外部引用身份无效。") from exc
            if not isinstance(external_path, str) or len(external_path) > 32767 or not Path(external_path).is_absolute():
                raise UserError("外部引用身份无效。")
        elif relative.startswith("bundle:"):
            if not re.fullmatch(r"bundle:[a-zA-Z0-9_-]{1,80}", relative):
                raise UserError("资源合集身份无效。")
        else:
            try:
                if self._relative(self.root / relative) != relative:
                    raise UserError("资源路径身份未规范化。")
            except (OSError, ValueError) as exc:
                raise UserError("资源路径身份无效。") from exc
        for key in ("tags", "projects", "agents", "collections", "related"):
            if key in value and (not isinstance(value[key], list) or len(value[key]) > 100 or any(not isinstance(item, str) or len(item) > 500 for item in value[key])):
                raise UserError("资源标签或关联记录格式无效。")
        for key in ("title", "summary", "notes"):
            if key in value and not isinstance(value[key], str):
                raise UserError("资源描述格式无效。")
        if "favorite" in value and not isinstance(value["favorite"], bool):
            raise UserError("资源收藏状态无效。")
        if "accessed_ns" in value and (not isinstance(value["accessed_ns"], int) or value["accessed_ns"] < 0):
            raise UserError("资源使用时间无效。")
        for key in ("archived", "deleted"):
            if key in value and not isinstance(value[key], bool):
                raise UserError("资源归档状态无效。")
        for key in ("archive_original", "archive_original_directory"):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > 2000):
                raise UserError("资源归档位置无效。")
        if "archive_member_locations" in value:
            locations = value["archive_member_locations"]
            if not isinstance(locations, dict) or len(locations) > 20_000 or any(not isinstance(k, str) or len(k) > 2000 or not isinstance(v, str) or len(v) > 2000 for k, v in locations.items()):
                raise UserError("资源合集归档位置无效。")
        return value

    def _put_record(self, db, relative, values):
        native = {"tags", "favorite", "projects", "agents"}
        row = db.execute("SELECT tags,favorite,projects,agents,details FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, relative)).fetchone()
        current = {"tags": json.loads(row[0]), "favorite": bool(row[1]), "projects": json.loads(row[2]), "agents": json.loads(row[3]), **json.loads(row[4] or "{}")} if row else {}
        current.update({key: value for key, value in values.items() if key not in {"relative", "version"}})
        from .storage import now
        details = {key: value for key, value in current.items() if key not in native}
        db.execute("""INSERT INTO knowledge_metadata(root_id,relative_path,tags,favorite,projects,agents,updated_at,details)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(root_id,relative_path) DO UPDATE SET
            tags=excluded.tags,favorite=excluded.favorite,projects=excluded.projects,agents=excluded.agents,
            updated_at=excluded.updated_at,details=excluded.details""",
            (self.root_id, relative, json.dumps(current.get("tags", []), ensure_ascii=False), int(bool(current.get("favorite", False))),
             json.dumps(current.get("projects", []), ensure_ascii=False), json.dumps(current.get("agents", []), ensure_ascii=False),
             now(), json.dumps(details, ensure_ascii=False)))

    @staticmethod
    def relocate_resource_details(values, old, new):
        def translated(value):
            return new + value[len(old):] if isinstance(value, str) and not value.startswith(("external:", "bundle:")) and (value == old or value.startswith(old + "/")) else value
        result = dict(values)
        for key in ("related",):
            if isinstance(result.get(key), list):
                result[key] = [translated(value) for value in result[key]]
        for key in ("archive_original", "archive_original_directory"):
            if key in result:
                result[key] = translated(result[key])
        locations = result.get("archive_member_locations")
        if isinstance(locations, dict):
            result["archive_member_locations"] = {translated(key): translated(value) for key, value in locations.items()}
        return result

    def _import_legacy_records(self, db, errors):
        marker = "resource-sidecars:v1"
        if db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?", (self.root_id, marker)).fetchone():
            return
        directory = self.root / "99-系统" / "知识库索引" / "资源"
        imported, failures = 0, 0
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        if not paths:
            return
        for path in paths:
            before = len(errors)
            self._import_legacy_record(db, path, errors)
            if len(errors) > before:
                failures += 1
            else:
                outcome = db.execute("SELECT imported_count,error_count FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?", (self.root_id, "record:" + path.name)).fetchone()
                if outcome:
                    imported += outcome[0]
                    failures += outcome[1]
        from .storage import now
        db.execute("INSERT INTO knowledge_legacy_imports VALUES (?,?,?,?,?)", (self.root_id, marker, now(), imported, failures))

    def _import_legacy_record(self, db, path, errors):
        if db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind='resource-sidecars:v1'", (self.root_id,)).fetchone():
            return
        if not path.is_file():
            return
        marker = "record:" + path.name
        if db.execute("SELECT 1 FROM knowledge_legacy_imports WHERE root_id=? AND source_kind=?", (self.root_id, marker)).fetchone():
            return
        try:
            self._relative(path)
            if path.stat().st_size > 256_000:
                raise UserError("资源描述记录超过 256 KB。")
            value = json.loads(path.read_text(encoding="utf-8"))
            relative = value.get("relative") if isinstance(value, dict) else None
            if not isinstance(relative, str):
                raise UserError("资源描述记录缺少资料路径。")
            current_path = self.record_path(relative)
            if path != current_path and current_path.is_file():
                return
            self._validate_resource_record(value, relative)
            self._put_record(db, relative, value)
            from .storage import now
            db.execute("INSERT INTO knowledge_legacy_imports VALUES (?,?,?,?,?)", (self.root_id, marker, now(), 1, 0))
        except (OSError, ValueError, UserError) as exc:
            errors.append(path.name + "：" + str(exc))
            from .storage import now
            db.execute("INSERT OR IGNORE INTO knowledge_legacy_imports VALUES (?,?,?,?,?)", (self.root_id, marker, now(), 0, 1))

    def write_record(self, relative, values):
        self._validate_resource_record({"relative": relative, **values}, relative)
        if len(json.dumps(values, ensure_ascii=False).encode("utf-8")) > 256_000:
            raise UserError("资源描述内容过长。")
        with self.store.connect() as db:
            self._put_record(db, relative, values)

    def purge_resource(self, relative):
        """Drop indexed metadata; preserve legacy sidecars as migration evidence."""
        with self.store.connect() as db:
            db.execute("DELETE FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, relative))

    def enrich_resources(self, items, errors):
        for entry in items:
            try:
                text = entry.get("search_text", "")
                frontmatter, _ = note_header(text)
                if frontmatter:
                    if isinstance(frontmatter, dict):
                        if isinstance(frontmatter.get("title"), str):
                            entry["title"] = frontmatter["title"]
                        original_tags = frontmatter.get("tags", [])
                        if isinstance(original_tags, list) and not entry["tags"]:
                            entry["tags"] = [tag for tag in original_tags[:100] if isinstance(tag, str)]
                record = self.read_record(entry["relative"])
                for key in ("tags", "favorite", "projects", "agents", "title", "summary", "notes", "collections", "related", "accessed_ns", "archived", "deleted", "archive_original", "archive_original_directory", "archive_member_locations"):
                    if key in record:
                        entry[key] = record[key]
                entry["name"] = entry.get("title") or entry["name"]
                entry["summary"] = entry.get("summary") or content_summary(entry.get("search_text", ""))
                entry["notes"] = entry.get("notes", "")
                entry["collections"] = entry.get("collections", [])
                entry["related"] = entry.get("related", [])
                entry["accessed_ns"] = entry.get("accessed_ns", 0)
                if entry["kind"] == "link" and not entry.get("missing"):
                    with Path(entry["path"]).open(encoding="utf-8-sig", errors="replace") as source:
                        text = source.read(12_000)
                    entry["source_url"] = next((line[4:].strip() for line in text.splitlines() if line.startswith("URL=")), "")
                entry["resource_type"] = resource_type(entry)
            except (OSError, ValueError, UserError, yaml.YAMLError) as exc:
                errors.append(entry["name"] + "：" + str(exc))
        return items

    def update_resource(self, relative, **changes):
        allowed = {"title", "summary", "notes", "collections", "related", "accessed_ns", "archived", "deleted",
                   "archive_original", "archive_original_directory", "archive_member_locations"}
        if changes.keys() - allowed:
            raise UserError("资源描述包含未知字段。")
        if not relative.startswith(("external:", "bundle:")):
            self._relative(self.root / relative)
        for key in ("title", "summary", "notes"):
            if key in changes:
                if not isinstance(changes[key], str) or len(changes[key]) > (50_000 if key == "notes" else 500):
                    raise UserError("资源标题、用途或笔记超过允许长度。")
        if "collections" in changes:
            changes["collections"] = list(dict.fromkeys(str(v).strip() for v in changes["collections"] if str(v).strip()))
            if len(changes["collections"]) > 50 or any(len(v) > 100 for v in changes["collections"]):
                raise UserError("收藏集最多 50 个，名称最多 100 字。")
        if "related" in changes and (not isinstance(changes["related"], list) or len(changes["related"]) > 100 or any(not isinstance(v, str) or len(v) > 500 for v in changes["related"])):
            raise UserError("关联资料记录无效。")
        record = self.read_record(relative)
        group = getattr(self, "bundle_entries", {}).get(relative)
        if group and not self.has_bundle(group["bundle_record"]["id"]):
            self.change_bundle(group)
        record.update(changes)
        self.write_record(relative, record)
        return record

    def record_access(self, relative):
        timestamp = time.time_ns()
        self.update_resource(relative, accessed_ns=timestamp)
        return timestamp

    def reconcile_paths(self, db):
        """Retain existing SQLite tags after the approved physical reorganization."""
        manifest = self.root / "99-系统" / "知识库索引" / "目录迁移.json"
        if not manifest.is_file():
            return
        self._relative(manifest)
        if manifest.stat().st_size > 2_000_000:
            raise UserError("目录迁移清单过大。")
        mappings = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        self._import_legacy_records(db, [])
        for old, new in mappings.items():
            self._relative(self.root / old)
            self._relative(self.root / new)
            new_path = self.root / new
            if (self.root / old).exists() or (not new_path.is_file() and not self._existing_record(new).is_file()):
                continue
            row = db.execute("SELECT tags,favorite,projects,agents FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, old)).fetchone()
            if row:
                existing = db.execute("SELECT 1 FROM knowledge_metadata WHERE root_id=? AND relative_path=?", (self.root_id, new)).fetchone()
                if not existing:
                    db.execute("UPDATE knowledge_metadata SET relative_path=? WHERE root_id=? AND relative_path=?", (new, self.root_id, old))
            db.execute("DELETE FROM knowledge_files WHERE root_id=? AND relative_path=?", (self.root_id, old))
            if Path(old).suffix.casefold() == ".kref":
                db.execute("UPDATE knowledge_references SET folder=? WHERE root_id=? AND folder=?", (Path(new).parent.as_posix(), self.root_id, Path(old).parent.as_posix()))
            self.relocate_bundle_references(old, new, db=db)
