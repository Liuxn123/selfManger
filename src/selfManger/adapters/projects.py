from __future__ import annotations

from pathlib import Path

from ..domain import Resource, UserError
from ..runtime import TaskContext, run_process
from ..archives import is_link


def required_directory(options: dict, key: str) -> Path:
    value = str(options.get(key, "")).strip()
    if not value:
        raise UserError("请先配置资源目录。")
    path = Path(value).expanduser()
    if not path.is_dir() or is_link(path):
        raise UserError("配置的目录不存在或是链接，请检查资源设置。")
    return path.resolve()


class ProjectAdapter:
    capabilities = frozenset({"observe", "backup", "verify", "restore", "open"})

    def observe(self, resource: Resource, context: TaskContext) -> dict:
        root = required_directory(resource.options, "path")
        context.checkpoint()
        result = {"path": str(root), "type": "Obsidian Vault" if resource.kind == "vault" else "项目", "entries": []}
        for path in sorted(root.iterdir(), key=lambda path: (not path.is_dir(), path.name.casefold())):
            context.checkpoint()
            if len(result["entries"]) >= 100:
                break
            result["entries"].append({"name": path.name, "type": "目录" if path.is_dir() else "文件", "link": path.is_symlink()})
        result["backup_note"] = "加密保存工作文件与附件；不检查项目 Git，不打包项目 Git 历史。关闭写入程序后备份。"
        return result


class VaultAdapter(ProjectAdapter):
    capabilities = ProjectAdapter.capabilities | {"git_pull", "open_vault"}

    def observe(self, resource: Resource, context: TaskContext) -> dict:
        result = super().observe(resource, context)
        root = required_directory(resource.options, "path")
        if resource.options.get("manage_git") and (root / ".git").exists():
            result["git_status"] = run_process(["git", "-C", str(root), "status", "--short", "--branch"], context, timeout=30).strip()
            result["remotes"] = run_process(["git", "-C", str(root), "remote"], context, timeout=30).splitlines()
        result["backup_note"] = "知识库独立管理；仅明确启用的知识库检查并备份 Git 历史。"
        return result

    def git_pull(self, resource: Resource, context: TaskContext) -> dict:
        if not resource.options.get("manage_git"):
            raise UserError("这个知识库未启用独立 Git 管理。项目 Git 不由管家检查或拉取。")
        root = required_directory(resource.options, "path")
        if not (root / ".git").exists():
            raise UserError("此项目不是 Git 仓库。")
        if run_process(["git", "-C", str(root), "status", "--porcelain"], context, timeout=30).strip():
            raise UserError("项目有未提交或未跟踪文件，请先保存，避免拉取时混合修改。")
        output = run_process(["git", "-C", str(root), "pull", "--ff-only"], context, timeout=180, cancellable=False)
        return {"pulled": True, "result": output.strip()}
