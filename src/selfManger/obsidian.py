"""Open the existing project files through Obsidian's standard URI protocol."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode

from .domain import UserError
from .project_workspaces import ProjectWorkspace, MANAGEMENT_FILES, linked, lifecycle_state


def open_uri(path: Path) -> str:
    # Keep the lexical junction path: resolving it would select another vault.
    return "obsidian://open?" + urlencode({"path": str(path), "paneType": "tab"}, quote_via=quote)


def require_vault(path: Path) -> None:
    if not path.is_dir() or not (path / ".obsidian").is_dir():
        raise UserError("请先在 Obsidian 中把这个文件夹作为知识库打开一次：" + str(path))
    if linked(path) or linked(path / ".obsidian"):
        raise UserError("知识库根目录和 .obsidian 配置不能使用联接或符号链接。")


def require_document_subdir(item: dict, name: str) -> None:
    """Keep file/folder opening within the registered document scope."""
    subdirs = item.get("vault_subdirs")
    if not isinstance(subdirs, list) or name not in subdirs:
        current = "、".join(str(value) for value in subdirs) if isinstance(subdirs, list) else "未登记"
        raise UserError(f"“{name}”目录尚未接入 Obsidian（当前已接入：{current}）。"
                        f"请先为当前项目登记并建立“{name}”目录的文档联接，再重试。")


def project_uri(workspace: ProjectWorkspace, identity: str, relative: str) -> dict:
    item = workspace.project(identity)
    if relative not in MANAGEMENT_FILES and not (relative.startswith("笔记/") and len(Path(relative).parts) == 2 and relative.lower().endswith(".md")):
        raise UserError("请选择项目管理文档或项目笔记。")
    source = workspace.path(item["path"] + "/" + relative)
    if not source.is_file():
        raise UserError("文档已移动或不存在，请刷新项目。")
    if item.get("vault_entry"):
        entry = workspace.path(item["vault_entry"], allow_link=True)
        target = workspace.path(item["path"])
        if item.get("vault_scope") == "documents":
            if linked(entry) or not entry.is_dir():
                raise UserError("Obsidian 文档入口缺失或不是普通目录，请先核对入口。")
            path = entry / relative
            if relative == "README.md":
                if linked(path) or not path.is_file():
                    raise UserError("Obsidian 项目说明入口不存在，请先核对入口。")
            else:
                leaf = Path(relative).parts[0]
                require_document_subdir(item, leaf)
                child = workspace.path(item["vault_entry"] + "/" + leaf, allow_link=True)
                if (not linked(child) or not child.is_dir()
                        or child.resolve() != (target / leaf).resolve()
                        or not path.is_file() or path.resolve() != source.resolve()):
                    raise UserError("Obsidian 文档联接缺失或指向其他目录；不会打开错误的项目。")
        elif not linked(entry) or not entry.is_dir() or entry.resolve() != target.resolve():
            try:
                status_path = workspace.path(item["path"] + "/agent/STATUS.md")
                archived = lifecycle_state(workspace.read(status_path)) == "archived"
            except (OSError, UserError):
                archived = False
            if archived:
                raise UserError("已归档项目已从 Obsidian 的 03-项目目录隐藏；取消归档后会自动恢复入口。")
            raise UserError("Obsidian 项目入口缺失或指向其他目录，请先核对入口；不会打开错误的项目。")
        vault = workspace.path("myself")
        require_vault(vault)
        path = entry / relative
    else:
        # Honor projects whose registered policy intentionally excludes a vault entry.
        vault = workspace.path(item["path"])
        if not (vault / ".obsidian").is_dir():
            raise UserError("这个项目按登记策略没有 Obsidian 入口。可用“打开文件夹”；若要单独使用 Obsidian，请先把真实项目目录作为知识库打开。")
        require_vault(vault)
        path = source
    return {"uri": open_uri(path), "path": str(path), "vault": str(vault)}


def index_uri(workspace: ProjectWorkspace, archived: bool = False) -> dict:
    vault = workspace.path("myself")
    require_vault(vault)
    path = workspace.path("myself/03-项目/" + ("归档项目索引.md" if archived else "项目总览.md"))
    if not path.is_file():
        raise UserError("项目索引不存在，请先刷新 Obsidian 索引。")
    return {"uri": open_uri(path), "path": str(path), "vault": str(vault)}


def project_notes_uri(workspace: ProjectWorkspace, identity: str, vault_root: Path) -> dict:
    """Resolve a project's notes folder inside the user-selected Obsidian vault."""
    item = workspace.project(identity)
    vault = Path(vault_root).expanduser().absolute()
    require_vault(vault)
    project = workspace.path(item["path"])
    notes = workspace.path(item["path"] + "/笔记")

    # Prefer a project path that is already inside the configured vault.
    try:
        relative_project = project.resolve().relative_to(vault.resolve())
        target = vault / relative_project / "笔记"
    except ValueError:
        target = None

    # Registered Obsidian entries are junctions from the vault into the project tree.
    if target is None and item.get("vault_entry"):
        entry_parts = Path(item["vault_entry"]).parts
        if len(entry_parts) < 2 or entry_parts[0] != "myself":
            raise UserError("项目登记中的 Obsidian 入口无效。")
        entry = vault.joinpath(*entry_parts[1:])
        target = entry / "笔记"
        if item.get("vault_scope") == "documents":
            require_document_subdir(item, "笔记")
            if linked(entry) or not entry.is_dir():
                raise UserError("Obsidian 文档入口缺失或不是普通目录，请先核对入口。")
            if not linked(target) or not target.is_dir() or target.resolve() != notes.resolve():
                raise UserError("Obsidian 笔记联接缺失或指向其他目录；请核对入口后重试。")

    if target is not None and not target.exists() and item.get("vault_entry"):
        try:
            status_path = workspace.path(item["path"] + "/agent/STATUS.md")
            archived = lifecycle_state(workspace.read(status_path)) == "archived"
        except (OSError, UserError):
            archived = False
        if archived:
            raise UserError("已归档项目已从 Obsidian 的 03-项目目录隐藏；取消归档后会自动恢复入口。")

    if target is None:
        raise UserError("当前项目不在设置的 Obsidian 知识库中，也没有登记 Obsidian 入口。请在设置中选择包含此项目的知识库根目录。")
    if (target.exists() and not target.is_dir()) or target.resolve() != notes.resolve():
        raise UserError("设置的 Obsidian 知识库与当前项目入口不匹配。请检查知识库根目录和项目登记；不会打开其他目录。")
    return {"uri": open_uri(target), "path": str(target), "vault": str(vault)}
