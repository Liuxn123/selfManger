from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterator
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .domain import Resource, RestorePlan, UserError
from .runtime import TaskContext, run_process
from .storage import now

MAGIC = b"AGENT-MANAGER-1\n"
CHUNK = 1024 * 1024
MAX_SIZE = 50 * 1024**3
MAX_FILES = 200_000
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", ".pytest_cache"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _resource_folder_name(resource: Resource) -> str:
    readable = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", resource.name).strip(" ._")
    readable = readable[:60].rstrip(" .")
    if not readable or readable.split(".")[0].upper() in WINDOWS_RESERVED:
        readable = "资料"
    return f"{readable}_{resource.id[:8]}"


def _version_folder_name(identity: str, created_at: str) -> str:
    try:
        stamp = datetime.fromisoformat(created_at).strftime("%Y%m%d-%H%M%S")
    except (TypeError, ValueError):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{identity[:8]}"


def key_for(password: str, salt: bytes) -> bytes:
    if not password:
        raise UserError("备份口令不能为空。")
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))


class EncryptWriter(io.RawIOBase):
    """Let zipfile stream directly into AES-GCM; no plaintext ZIP on disk."""
    def __init__(self, handle, encryptor) -> None:
        self.handle = handle
        self.encryptor = encryptor
        self.position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.position

    def write(self, data: bytes) -> int:
        self.handle.write(self.encryptor.update(data))
        self.position += len(data)
        return len(data)

    def flush(self) -> None:
        self.handle.flush()


def path_token(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise UserError("备份包含不安全的文件路径。")
    if any(":" in part or part.endswith((".", " ")) or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))} for part in path.parts):
        raise UserError("备份包含无法跨平台恢复的文件名。")
    return str(path)


def create_archive(resource: Resource, destination: Path, password: str, context: TaskContext) -> dict:
    if resource.kind not in {"project", "folder", "script", "vault"}:
        raise UserError("该资源类型的备份功能已移除；已有备份文件保持原样。")
    return _create_single_archive(resource, destination, password, context)


def _create_single_archive(resource: Resource, destination: Path, password: str, context: TaskContext) -> dict:
    source = Path(resource.options.get("path", "")).expanduser()
    if not source.is_dir() or is_link(source):
        raise UserError("请选择真实存在的项目目录，不能使用符号链接作为根目录。")
    source = source.resolve()
    destination = destination.expanduser().resolve()
    if destination == source or destination.is_relative_to(source):
        raise UserError("备份目录必须位于被备份目录之外。")
    destination.mkdir(parents=True, exist_ok=True)
    identity = uuid4().hex
    resource_root = destination / _resource_folder_name(resource)
    resource_root.mkdir(parents=True, exist_ok=True)
    staging = resource_root / f".{identity}.partial"
    version_dir = resource_root / _version_folder_name(identity, now())
    staging.mkdir()
    final = staging / "backup.amb"
    temporary = staging / ".backup.partial"
    records: list[dict] = []
    excluded: list[str] = []
    directories: list[str] = []
    total = 0
    extra_excludes = set(resource.options.get("exclude_dirs", []))
    salt, nonce = os.urandom(16), os.urandom(12)
    encryptor = Cipher(algorithms.AES(key_for(password, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    context.log("正在创建加密备份，明文 ZIP 不会写入磁盘。")
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(MAGIC + salt + nonce)
            with zipfile.ZipFile(EncryptWriter(handle, encryptor), "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for root, dirs, files in os.walk(source, followlinks=False):
                    context.checkpoint()
                    current = Path(root)
                    retained = []
                    for name in dirs:
                        path = current / name
                        relative = path.relative_to(source).as_posix()
                        if name in SKIP_DIRS | extra_excludes or is_link(path):
                            excluded.append(relative + "/")
                        else:
                            retained.append(name)
                            directories.append(safe_name(relative))
                    dirs[:] = retained
                    for name in sorted(files):
                        context.checkpoint()
                        path = current / name
                        relative = path.relative_to(source).as_posix()
                        if is_link(path):
                            excluded.append(relative)
                            continue
                        if not path.is_file():
                            raise UserError("目录包含特殊文件，无法创建一致备份。")
                        safe_name(relative)
                        before = path.stat()
                        total += before.st_size
                        if total > MAX_SIZE or len(records) >= MAX_FILES:
                            raise UserError("备份超出首版容量限制（50 GiB / 20 万文件）。")
                        digest = hashlib.sha256()
                        with path.open("rb") as reader, archive.open("files/" + relative, "w", force_zip64=True) as writer:
                            for chunk in iter(lambda: reader.read(CHUNK), b""):
                                context.checkpoint()
                                digest.update(chunk)
                                writer.write(chunk)
                        after = path.stat()
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise UserError("备份期间文件发生变化，请关闭写入程序后重试。")
                        records.append({"path": relative, "sha256": digest.hexdigest(), "size": before.st_size, "mode": stat.S_IMODE(before.st_mode)})
                # Bundle preserves local commits without copying a live .git directory.
                if resource.kind == "vault" and resource.options.get("manage_git") and (source / ".git").exists():
                    with tempfile.TemporaryDirectory(prefix="selfManger-git-") as temporary_git:
                        bundle = Path(temporary_git) / "history.bundle"
                        try:
                            heads = subprocess.run(["git", "-C", str(source), "show-ref"], capture_output=True, timeout=30,
                                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                        except (OSError, subprocess.TimeoutExpired) as exc:
                            raise UserError("无法检查 Git 历史，请检查 Git 安装。") from exc
                        if heads.returncode not in {0, 1}:
                            raise UserError("Git 历史检查失败，备份未完成。")
                        if heads.returncode == 0:
                            if any(record["path"].casefold() == ".selfManger-history.bundle" for record in records):
                                raise UserError("项目含有保留文件 .selfManger-history.bundle，请先移动该历史资料包。")
                            run_process(["git", "-C", str(source), "bundle", "create", str(bundle), "--all"], context)
                            archive.write(bundle, "git/history.bundle")
                            records.append({"path": "@git-bundle", "sha256": path_token(bundle), "size": bundle.stat().st_size, "mode": 0o600})
                manifest = {"schema_version": 1, "resource_id": resource.id, "kind": resource.kind, "resource_name": resource.name,
                            "created_at": now(), "files": records, "directories": directories, "excluded": excluded}
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            handle.write(encryptor.finalize())
            handle.write(encryptor.tag)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(final)
        metadata = {"id": identity, "resource_id": resource.id, "kind": resource.kind, "created_at": manifest["created_at"],
                    "file_count": len(records), "size": final.stat().st_size, "excluded_count": len(excluded), "encrypted": True}
        (staging / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        version_dir = resource_root / _version_folder_name(identity, manifest["created_at"])
        staging.replace(version_dir)
        final = version_dir / "backup.amb"
        context.log(f"加密备份已完成：{len(records)} 个文件，排除 {len(excluded)} 项。")
        return {**metadata, "archive": str(final), "excluded": excluded[:100], "verified": False}
    finally:
        temporary.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)


@contextmanager
def open_archive(source: Path, password: str, context: TaskContext, expected_sha256: str = "") -> Iterator[zipfile.ZipFile]:
    if not source.is_file() or is_link(source):
        raise UserError("备份文件不存在或是链接。")
    minimum = len(MAGIC) + 16 + 12 + 16
    if source.stat().st_size < minimum or source.stat().st_size > MAX_SIZE + 1024**3:
        raise UserError("备份文件大小不正确。")
    with source.open("rb") as handle, tempfile.TemporaryFile() as decrypted:
        magic = handle.read(len(MAGIC))
        if magic != MAGIC:
            raise UserError("不支持的加密备份格式。")
        salt, nonce = handle.read(16), handle.read(12)
        content_digest = hashlib.sha256(magic + salt + nonce)
        handle.seek(-16, os.SEEK_END)
        tag = handle.read(16)
        decryptor = Cipher(algorithms.AES(key_for(password, salt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        handle.seek(len(MAGIC) + 28)
        remaining = source.stat().st_size - minimum
        while remaining:
            context.checkpoint()
            chunk = handle.read(min(CHUNK, remaining))
            if not chunk:
                raise UserError("加密备份被截断。")
            remaining -= len(chunk)
            content_digest.update(chunk)
            decrypted.write(decryptor.update(chunk))
        content_digest.update(tag)
        try:
            decrypted.write(decryptor.finalize())
        except InvalidTag as exc:
            raise UserError("口令错误或备份被篡改，目标目录未修改。") from exc
        source_sha256 = content_digest.hexdigest()
        if expected_sha256 and expected_sha256 != source_sha256:
            raise UserError("备份在预览后发生变化，请重新预览。")
        decrypted.seek(0)
        try:
            with zipfile.ZipFile(decrypted) as archive:
                archive.source_sha256 = source_sha256
                yield archive
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UserError("备份内部格式损坏，目标目录未修改。") from exc


def verify_open(archive: zipfile.ZipFile, context: TaskContext) -> dict:
    infos = archive.infolist()
    names = [entry.filename for entry in infos]
    if len(names) > MAX_FILES + 1 or len(names) != len(set(names)):
        raise UserError("备份文件数量异常或有重复条目。")
    if sum(entry.file_size for entry in infos) > MAX_SIZE:
        raise UserError("备份展开大小超出限制。")
    for entry in infos:
        safe_name(entry.filename)
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise UserError("备份包含符号链接，已拒绝恢复。")
    if archive.getinfo("manifest.json").file_size > 32_000_000:
        raise UserError("备份清单过大。")
    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise UserError("备份清单版本或内容不正确。")
    Resource.from_dict({"kind": manifest.get("kind"), "id": manifest.get("resource_id"), "name": manifest.get("resource_name", "导入的资料"), "options": {}})
    components = manifest.get("components", [])
    if not isinstance(components, list) or len(components) > 21:
        raise UserError("备份中的资料目录清单不正确。")
    prefixes = set()
    for component in components:
        if not isinstance(component, dict) or component.get("role") not in {"project", "records"}:
            raise UserError("备份中的资料类型不正确。")
        prefix = safe_name(component.get("prefix", ""))
        if "/" in prefix or component.get("id") != prefix or prefix.casefold() in prefixes:
            raise UserError("备份中的资料目录重名或不安全。")
        prefixes.add(prefix.casefold())
    expected = {"manifest.json"}
    folded: set[str] = set()
    for record in manifest["files"]:
        relative = record["path"]
        if components and relative.split("/")[0].casefold() not in prefixes:
            raise UserError("文件不属于声明的资料目录。")
        name = "git/history.bundle" if relative == "@git-bundle" else "files/" + safe_name(relative)
        if name in expected or name.casefold() in folded:
            raise UserError("备份路径存在重复或跨平台大小写冲突。")
        expected.add(name)
        folded.add(name.casefold())
        if archive.getinfo(name).file_size != record["size"]:
            raise UserError("文件大小与清单不符。")
        digest = hashlib.sha256()
        with archive.open(name) as reader:
            for chunk in iter(lambda: reader.read(CHUNK), b""):
                context.checkpoint()
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            raise UserError("文件哈希与清单不符。")
    if set(names) != expected:
        raise UserError("备份含有未声明文件。")
    for directory in manifest.get("directories", []):
        safe_name(directory)
        if components and directory.split("/")[0].casefold() not in prefixes:
            raise UserError("文件夹不属于声明的资料目录。")
    return manifest


def verify_archive(source: Path, password: str, context: TaskContext) -> dict:
    with open_archive(source, password, context) as archive:
        manifest = verify_open(archive, context)
        source_sha256 = archive.source_sha256
    return {"valid": True, "file_count": len(manifest["files"]), "resource_id": manifest["resource_id"],
            "kind": manifest["kind"], "resource_name": manifest.get("resource_name", "导入的资料"),
            "engine": manifest.get("engine", ""), "components": manifest.get("components", []),
            "created_at": manifest["created_at"], "excluded": manifest.get("excluded", [])[:100], "archive_sha256": source_sha256}


def plan_restore(resource: Resource, source: Path, target: Path, password: str, context: TaskContext, folders: dict[str, str] | None = None) -> RestorePlan:
    target = target.expanduser().absolute()
    target = check_target(target, source)
    report = verify_archive(source, password, context)
    if report["kind"] not in {"project", "folder", "script", "vault"}:
        raise UserError("此旧资源类型的恢复功能已移除；原始备份文件仍然保留。")
    if report["resource_id"] not in {resource.id, resource.options.get("source_resource_id")}:
        raise UserError("此备份属于其他资源，请在对应资源下恢复。")
    layout = validate_folders(report.get("components", []), folders)
    return RestorePlan(report["resource_id"], report["archive_sha256"], str(source.resolve()), str(target),
                       {**report, "target": str(target), "existing_files_overwritten": 0,
                        "component_folders": layout,
                        "note": "资料先完整校验，再恢复到新目录。项目与记录分开放置；恢复完成后可在管家继续浏览本地记录。原应用可能需要重新登录并登记项目路径。"})


def validate_folders(components: list[dict], folders: dict[str, str] | None = None) -> dict[str, str]:
    result = {}
    if folders is not None and (not isinstance(folders, dict) or set(folders) != {item["id"] for item in components}):
        raise UserError("恢复目录与备份资料范围不一致，请重新预览。")
    for item in components:
        name = safe_name(folders[item["id"]] if folders is not None else item["prefix"])
        path = PurePosixPath(name.casefold())
        if any(path == previous or path.is_relative_to(previous) or previous.is_relative_to(path) for previous in result.values()):
            raise UserError("项目与记录的恢复文件夹不能重名或相互包含。")
        result[item["id"]] = path
    return {item["id"]: safe_name(folders[item["id"]] if folders is not None else item["prefix"]) for item in components}


def canonical_system_path(target: Path) -> Path:
    target = target.expanduser().absolute()
    # macOS exposes immutable OS aliases /var, /tmp, /etc. Normalize only these
    # exact well-known mappings; user-controlled symlinks remain forbidden.
    if sys.platform == "darwin":
        for name in ("var", "tmp", "etc"):
            alias, actual = Path("/" + name), Path("/private/" + name)
            if target.is_relative_to(alias) and alias.is_symlink() and alias.resolve() == actual:
                target = actual / target.relative_to(alias)
                break
    return target


def check_target(target: Path, source: Path) -> Path:
    target = canonical_system_path(target)
    if is_link(target) or any(is_link(parent) for parent in target.parents):
        raise UserError("恢复目标不能包含符号链接或目录联接。")
    resolved = target.resolve()
    if resolved == Path(resolved.anchor) or source.resolve().is_relative_to(resolved):
        raise UserError("恢复目标不能是磁盘根目录或包含备份文件。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise UserError("首版只恢复到新目录或空目录，请选择其他目标。")
    return target


def apply_restore(plan: RestorePlan, password: str, context: TaskContext, *, quarantine_database: bool = False) -> dict:
    source, target = Path(plan.source), Path(plan.target)
    check_target(target, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".selfManger-restore-", dir=target.parent))
    restore_error = None
    try:
        with open_archive(source, password, context, expected_sha256=plan.token) as archive:
            manifest = verify_open(archive, context)
            if manifest["resource_id"] != plan.resource_id:
                raise UserError("备份资源身份发生变化。")
            if manifest["kind"] not in {"project", "folder", "script", "vault"}:
                raise UserError("此旧资源类型的恢复功能已移除；原始备份文件仍然保留。")
            quarantine_database = quarantine_database or manifest["resource_id"] == "selfMangerOverall"
            folders = validate_folders(manifest.get("components", []), plan.summary.get("component_folders"))
            def restored_path(relative: str) -> str:
                parts = relative.split("/")
                return "/".join([folders.get(parts[0], parts[0]), *parts[1:]])
            required = sum(record["size"] for record in manifest["files"])
            if shutil.disk_usage(staging).free < required + 64 * 1024**2:
                raise UserError("目标磁盘空间不足。")
            for directory in manifest.get("directories", []):
                (staging / safe_name(restored_path(directory))).mkdir(parents=True, exist_ok=True)
            for record in manifest["files"]:
                context.checkpoint()
                relative = record["path"]
                name = "git/history.bundle" if relative == "@git-bundle" else "files/" + safe_name(relative)
                destination = staging / (".selfManger-history.bundle" if relative == "@git-bundle" else safe_name(restored_path(relative)))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as reader, destination.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, CHUNK)
                if destination.stat().st_size != record["size"] or path_token(destination) != record["sha256"]:
                    raise UserError("恢复后的文件与备份清单不一致，未发布恢复目录：" + relative)
                # Restore ordinary permissions only; never set setuid/setgid bits.
                os.chmod(destination, int(record.get("mode", 0o600)) & 0o777)
        if quarantine_database:
            database = staging / "manager.sqlite3"
            recovered = staging / "manager.sqlite3.recovered"
            notice = staging / "恢复副本隔离说明.txt"
            if not database.is_file() or recovered.exists() or notice.exists():
                raise UserError("恢复内容缺少数据库或隔离文件名冲突，未发布恢复目录。")
            database.replace(recovered)
            with closing(sqlite3.connect(database)) as blocker:
                blocker.execute("PRAGMA application_id=1397573458")
                blocker.execute("PRAGMA user_version=2147483647")
            missing_personal = []
            knowledge_count = 0
            with closing(sqlite3.connect(recovered.resolve().as_uri() + "?mode=ro", uri=True)) as restored_db:
                tables = {row[0] for row in restored_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "knowledge_files" in tables:
                    columns = {row[1] for row in restored_db.execute("PRAGMA table_info(knowledge_files)")}
                    query = "SELECT COUNT(*) FROM knowledge_files" + (" WHERE missing=0" if "missing" in columns else "")
                    knowledge_count = restored_db.execute(query).fetchone()[0]
                if "personal_markdown_documents" in tables:
                    columns = {row[1] for row in restored_db.execute("PRAGMA table_info(personal_markdown_documents)")}
                    for (relative,) in restored_db.execute("SELECT relative_path FROM personal_markdown_documents") if "relative_path" in columns else ():
                        try:
                            candidate = staging / "markdown" / "personal" / safe_name(relative)
                            if not candidate.is_file():
                                missing_personal.append(relative)
                        except (UserError, TypeError):
                            missing_personal.append("[无效路径]")
            notice.write_text(
                "真实数据库保存在 manager.sqlite3.recovered，不是默认启动文件。\n"
                "manager.sqlite3 是阻挡启动的空数据库，不含原资料路径。\n"
                "恢复的数据库仍保存原资料路径；请在数据安全页使用“准备可用数据目录…”生成新目录后再启动，勿直接改名数据库。\n"
                f"知识索引有 {knowledge_count} 条非缺失记录；知识库文件不在备份内，需另行恢复及核对。\n"
                f"按个人目录相对路径核对，有 {len(missing_personal)} 条关联文档在备份中未发现：\n"
                + "\n".join(missing_personal[:100]) + "\n"
                "不同原目录可能使用相同相对路径；本检查不能证明全部关联有效。项目非 Markdown 附件未备份。\n", encoding="utf-8")
        context.checkpoint()
        check_target(target, source)
        context.log("校验通过，正在切换恢复目录；此步骤不强制取消。")
        if target.exists():
            target.rmdir()
        staging.replace(target)
        return {"applied": True, "target": str(target), "file_count": len(manifest["files"]),
                "database_quarantined": quarantine_database,
                "excluded": manifest.get("excluded", [])[:100], "git_history": any(r["path"] == "@git-bundle" or r["path"].endswith("/.selfManger-history.bundle") for r in manifest["files"]),
                "restored_components": [{"label": item["label"], "path": str(target / folders[item["id"]]), "role": item["role"]} for item in manifest.get("components", [])]}
    except BaseException as exc:
        restore_error = exc
        raise
    finally:
        # Only the internally created and resolved staging directory is removed.
        if staging.exists() and staging.parent.resolve() == target.parent.resolve() and staging.name.startswith(".selfManger-restore-"):
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_exc:
                if restore_error is None:
                    raise UserError(
                        f"恢复目录已发布，但恢复暂存目录无法清理（{type(cleanup_exc).__name__}）：{staging}"
                    ) from cleanup_exc
                raise UserError(
                    f"恢复失败（{type(restore_error).__name__}），部分恢复副本未能清理（{type(cleanup_exc).__name__}）：{staging}"
                ) from restore_error


def list_archives(root: Path, resource_id: str) -> list[dict]:
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError(f"备份位置不是目录：{root}")
    result = []
    root = root.resolve()
    def raise_walk_error(error: OSError) -> None:
        raise error

    for directory, child_dirs, filenames in os.walk(root, onerror=raise_walk_error, followlinks=False):
        current = Path(directory)
        relative_parent = current.relative_to(root)
        if any(part.startswith(".") for part in relative_parent.parts):
            child_dirs[:] = []
            continue
        child_dirs[:] = [name for name in child_dirs
                         if not name.startswith(".") and not is_link(current / name)]
        for name in filenames:
            source = current / name
            if source.suffix.casefold() != ".amb":
                continue
            if is_link(source):
                continue
            try:
                if not source.resolve().is_relative_to(root):
                    continue
            except (OSError, ValueError):
                continue
            sidecar = source.parent / "metadata.json" if source.name == "backup.amb" else source.with_suffix(".json")
            try:
                if is_link(sidecar) or not sidecar.resolve().is_relative_to(root):
                    continue
                if sidecar.stat().st_size > 20_000:
                    continue
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(metadata, dict) and metadata.get("resource_id") == resource_id:
                    result.append({**metadata, "archive": str(source), "verified": False})
            except (OSError, ValueError):
                continue
    return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)


def delete_archive_version(root: Path, resource_id: str, archive_path: Path) -> None:
    """Delete one listed version and its sidecar, without removing unknown files."""
    root = root.expanduser().resolve()
    source = archive_path.expanduser()
    try:
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(root) or is_link(source) or not source.is_file():
            raise UserError("备份版本路径无效，未删除文件。")
        current = source
        while current != root:
            if is_link(current):
                raise UserError("备份目录包含链接，已停止删除。")
            current = current.parent
            if not current.is_relative_to(root) and current != root:
                raise UserError("备份版本不在统一备份位置内，未删除文件。")
        listed = next((item for item in list_archives(root, resource_id)
                       if Path(item["archive"]).resolve() == resolved), None)
        if not listed:
            raise UserError("备份版本已不存在，或不属于当前资料，未删除文件。")
        sidecar = source.parent / "metadata.json" if source.name == "backup.amb" else source.with_suffix(".json")
        if is_link(sidecar) or not sidecar.is_file() or not sidecar.resolve().is_relative_to(root):
            raise UserError("备份版本元数据无效，未删除文件。")
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("resource_id") != resource_id or metadata.get("id") != listed.get("id"):
            raise UserError("备份版本归属校验失败，未删除文件。")
        source.unlink()
        sidecar.unlink()
        if source.name == "backup.amb":
            try:
                source.parent.rmdir()
            except OSError:
                pass
    except UserError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UserError("删除备份版本失败，请检查文件权限和目录状态。") from exc
