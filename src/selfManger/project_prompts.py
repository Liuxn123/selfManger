"""Concise onboarding instructions shared by project files and copy dialogs."""
from __future__ import annotations


TASK_FORMAT = """## 任务与项目协作资料

- 项目业务任务由 selfManger 项目“任务”页保存到 SQLite；请在那里创建或编辑应用任务。不要创建或维护 TASKS-AI.md / TASKS-MANUAL.md 作为应用任务副本。
- `agent/TASKS.md` 仅记录 Agent 的开发执行事项与交接，不是应用任务数据库；需要用户决定的新增业务任务先在回复中提出。
- 已有 TASKS-AI.md / TASKS-MANUAL.md 是历史资料。不得覆盖或持续同步；应用提供一次性预览迁入，迁入后数据库记录为准。
- 里程碑与可验证验收条件写入 MILESTONES.md；当前阶段写入 STATUS.md，实质进展追加 HANDOFF.md。
"""

AGENT_WORKFLOW = """## 项目协作约定

1. 先读 AGENTS.md → agent/README.md → STATUS.md → TASKS.md → HANDOFF.md 最近两条或末尾 120 行，再按需读业务资料。遵守适用上层规则，不扩大本轮授权。
2. STATUS 是现状真源，TASKS 指向唯一任务清单，HANDOFF 追加证据；README 只导航。复用已有编号，创建前查重，保留用户内容和已完成状态。
3. 规划时实际补充 MILESTONES.md：使用“| 状态 | 里程碑 | 目标日期 |”记录阶段成果和验收，新阶段填待开始，未知日期填待定。Agent 开发行动记录在 TASKS.md；应用业务任务通过 selfManger 项目任务页管理，不生成 Markdown 副本。
4. 开始说明目标与验收，执行中汇报重要变化；有实质进展更新 STATUS、任务清单并追加 HANDOFF。收尾列实际改动、验证、未完成与下一步，不把计划当作已执行结果。
5. 只读/只写文档的要求优先：不自动安装、启动或改业务代码。仅处理本项目及授权依赖，不读凭据，不自动提交、推送或发布；并发按规则 CLAIM/RELEASE，归档项目先恢复后推进。
""" + "\n" + TASK_FORMAT


def _steps(initial: bool) -> str:
    if initial:
        return ("核对已有文件与 STATUS 目标，补充范围、验收、阶段里程碑和最小行动清单；"
                "更新 STATUS 与 HANDOFF。保留项目编号和现有文档，未知事项标待补。")
    return ("核对当前状态与最近交接，确认未完成事项和验证缺口；按本轮需求继续已授权工作，"
            "不重做已验收内容，保留原任务编号并补充证据。")


def project_prompt(name: str, identity: str, directory: str, *, initial: bool) -> str:
    purpose = "新项目初始化" if initial else "已有项目接手"
    return (f"请处理项目「{name}」（{identity}）的{purpose}。\n项目目录：{directory}\n\n"
            + AGENT_WORKFLOW + "\n## 本轮工作\n\n" + _steps(initial)
            + "\n\n本轮具体需求：以用户消息为准。若只要求了解和文档，写概览、运行说明与学习路线，"
              "将安装和首次运行列为待验证阶段，本轮不执行。\n")


def prompt_document() -> str:
    return ("# {{NAME}} AI 提示词\n\n项目：{{ID}}\n目录：{{PATH}}\n\n"
            "按用途复制下方短提示词交给 AI，并附上本轮需求；共同约定见本页，现状读取 STATUS。\n\n"
            "## 新项目初始化\n\n请先读项目 AGENTS.md 与本页协作约定。" + _steps(True)
            + "\n\n## 已有项目接手\n\n请先读项目 AGENTS.md 与本页协作约定。" + _steps(False)
            + "\n\n" + AGENT_WORKFLOW)
