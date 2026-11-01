from __future__ import annotations

from pathlib import Path

from ..domain import Resource, UserError
from ..runtime import TaskContext
from ..archives import is_link


class FolderAdapter:
    capabilities = frozenset({"observe", "backup", "verify", "restore", "open"})

    def observe(self, resource: Resource, context: TaskContext) -> dict:
        root = Path(str(resource.options.get("path", ""))).expanduser()
        if not root.is_dir() or is_link(root):
            raise UserError("请选择存在的本地文件夹。")
        context.checkpoint()
        return {"path": str(root.resolve()), "type": "本地文件夹", "backup_note": "内容将压缩并加密保存到统一备份目录。"}


class ScriptAdapter:
    capabilities = FolderAdapter.capabilities

    def observe(self, resource: Resource, context: TaskContext) -> dict:
        script = Path(str(resource.options.get("script_path", ""))).expanduser()
        if not script.is_file() or is_link(script):
            raise UserError("备份脚本不存在或不是普通文件。")
        context.checkpoint()
        return {"path": str(script.resolve()), "type": "脚本备份", "backup_note": "脚本需将待备份文件写入环境变量 SELFMANGER_BACKUP_OUTPUT_DIR 指定的临时目录。"}
