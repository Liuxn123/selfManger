from __future__ import annotations

import re
from typing import Any
from datetime import datetime

from ..security import safe_result


def readable_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def readable_size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    return f"{value / 1024 ** 2:.1f} MB"

ACTION_LABELS = {"observe": "检查状态", "backup": "创建备份", "verify": "校验备份", "restore": "恢复", "open": "打开目录",
                 "versions": "备份提交列表", "start": "启动", "stop": "停止", "restart": "重启网关", "git_pull": "Git 拉取", "open_vault": "打开 Obsidian", "records": "浏览本地记录", "library": "浏览会话", "logs": "网关日志"}
FIELD_LABELS = {"home": "运行目录", "home_present": "运行目录存在", "backup_repo": "备份仓库", "snapshot_present": "存在快照",
    "observed_at": "最近检测时间", "memory_used_percent": "内存使用率（%）", "external_process_count": "原应用进程数量",
    "backup_repo_present": "备份仓库存在", "backup_tool_present": "备份工具存在", "restore_tool_present": "恢复工具存在",
    "backup_key_file": "服务器备份口令文件路径", "backup_key_available": "服务器备份口令文件存在", "backup_ready": "备份环境就绪",
    "identical_files": "恢复后完全一致的文件数", "active_sessions": "活跃会话", "archived_sessions": "归档会话", "cron_files": "定时任务文件",
    "created_at": "备份时间", "counts": "资料数量", "sessions": "会话", "messages": "消息", "skills": "技能", "facts": "事实记忆",
    "format": "备份格式", "git_status": "Git 状态", "branch": "当前分支", "runtime_state": "运行状态", "connected": "连接成功",
    "service_state": "网关服务状态", "service_scope": "服务范围", "load_average": "系统负载", "cpu_count": "CPU 核心数",
    "disk_used_percent": "磁盘使用率（%）", "backup_created_at": "服务器备份时间", "profiles": "Profile",
    "path": "目录", "type": "类型", "entries": "目录内容", "name": "名称", "link": "链接", "remotes": "Git 远端名称",
    "backup_note": "备份说明", "engine": "Agent 类型", "state": "状态", "pid": "进程编号", "assets": "资料检测",
    "note": "说明", "applied": "实际恢复已执行", "changes": "计划变化", "rescue_dir": "救援资料目录", "portability": "迁移处理",
    "valid": "文件校验通过", "mismatches": "不一致项", "file_count": "文件数量", "target": "恢复目标", "excluded": "排除项",
    "excluded_count": "排除数量", "encrypted": "已加密", "size": "大小（字节）", "resource_id": "资源标识",
    "archive": "备份文件", "verified": "执行过校验", "existing_files_overwritten": "覆盖现有文件数", "archive_sha256": "备份校验标识",
    "pushed": "产生远端推送", "status": "操作状态", "service_started": "已启动恢复后的服务", "operation": "操作",
    "started": "进程已启动", "stopped": "进程已停止", "exited": "进程已退出", "exit_code": "退出码", "error": "问题",
    "error_type": "诊断类型", "git_history": "包含 Git 历史", "versions": "备份提交", "commit": "提交", "time": "时间",
    "resource_name": "资料名称", "kind": "资料类型", "components": "包含的资料目录", "component_folders": "恢复后的文件夹",
    "restored_components": "已恢复的资料", "label": "资料", "role": "用途", "prefix": "备份内文件夹", "exists": "目录存在"}


def readable_report(report: Any, depth: int = 0) -> str:
    report = safe_result(report) if depth == 0 else report
    indent = "  " * depth
    if isinstance(report, dict):
        lines = []
        for key, value in report.items():
            if key in {"archive_sha256", "resource_id", "id", "prefix"}:
                continue
            label = FIELD_LABELS.get(key, {"project": "项目文件", "records": "本地记录"}.get(key, key))
            if key.endswith("_at"):
                value = readable_time(value)
            if isinstance(value, (dict, list)):
                lines.append(indent + label + "：")
                lines.append(readable_report(value, depth + 1))
            else:
                lines.append(indent + label + "：" + readable_report(value, 0))
        return "\n".join(lines)
    if isinstance(report, list):
        if not report:
            return indent + "无"
        return "\n".join(indent + "• " + readable_report(value, depth + 1).lstrip() for value in report[:100])
    if report is True:
        return "是"
    if report is False:
        return "否"
    if report is None:
        return "未知 / 未提供"
    return {"project": "项目文件", "records": "本地记录", "vault": "Obsidian 笔记库", "agent": "工作 Agent",
            "active": "运行中", "inactive": "已停止", "failed": "故障", "system": "系统服务", "unknown": "未知"}.get(str(report), str(report))


def markdown_preserving_linebreaks(text):
    """Make authored single newlines visible in QTextBrowser Markdown previews."""
    output = []
    fence_marker = ""
    fence_length = 0
    for raw_line in str(text or "").splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        newline = raw_line[len(line):]
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_marker:
            if (fence and fence[1][0] == fence_marker and len(fence[1]) >= fence_length
                    and not fence[2].strip()):
                fence_marker = ""
                fence_length = 0
            output.append(raw_line)
            continue
        if fence:
            fence_marker = fence[1][0]
            fence_length = len(fence[1])
            output.append(raw_line)
            continue
        if newline and line.strip():
            output.append(line.rstrip() + "  " + newline)
        else:
            output.append(raw_line)
    return "".join(output)
