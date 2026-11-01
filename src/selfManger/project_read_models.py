"""Pure project Markdown parsers and read models; no Qt dependencies."""
from __future__ import annotations

import re
from datetime import date

from .project_workspaces import STATES
from .workbench import frontmatter


def outside_fences(text):
    """Keep line positions while ignoring fenced examples in counts and section parsing."""
    marker, length = "", 0
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if marker:
            if fence and fence[1][0] == marker and len(fence[1]) >= length and not fence[2].strip():
                marker = ""
            yield "\n" if line.endswith("\n") else ""
        elif fence:
            marker, length = fence[1][0], len(fence[1])
            yield "\n" if line.endswith("\n") else ""
        else:
            yield line


def sections(text, level=2):
    """Return original level-two sections, including Markdown and nested headings."""
    _, body = frontmatter(text)
    original = body.splitlines(keepends=True)
    starts = [(i, m[1].strip()) for i, line in enumerate(outside_fences(body))
              if (m := re.match(r"^" + "#" * level + r" (.+?)\s*#*\s*$", line))]
    return [(name, "".join(original[start + 1:starts[i + 1][0] if i + 1 < len(starts) else len(original)]).strip())
            for i, (start, name) in enumerate(starts)]


def overview_snapshot(status, handoff, summaries):
    metadata, _ = frontmatter(status)
    blocks = sections(status)
    def find(words, default):
        return "\n\n".join(body for name, body in blocks if any(word in name for word in words)) or default
    risks = []
    for line in outside_fences(status):
        if re.match(r"^\s*[-*+]\s+(?:\*\*)?(?:阻塞(?:/待决定)?|风险|待确认|待决策)[：:]", line):
            risks.append(line.strip())
    entries = [(title, body) for title, body in sections(handoff) if re.search(r"\d{4}-\d{2}-\d{2}", title)]
    checklist = {"tasks": [], "ai_tasks": [], "manual_tasks": [], "total": 0,
                 "done": 0, "in_progress": 0, "pending": 0, "overdue": 0, "progress": 0}
    latest_summary = summaries[-1] if summaries else ""
    brief = [(title, body) for title, body in sections(latest_summary, 3)
             if title in {"完成内容", "验证结果", "下一阶段计划"} and body.strip()]
    summary = "\n\n".join("**" + title + "**\n\n" + body for title, body in brief)
    if not summary:
        summary = re.sub(r"(?m)^## .+\n|^\*\*阶段总结：.+\n|^阶段：.+\n", "", latest_summary).strip()
    progress = find(("进度", "当前状态", "进展"), "尚未填写进展，完整记录可查看状态原文。")
    progress = re.sub(r"(?m)(生命周期[：:]\s*)([a-z]+)", lambda m: m[1] + STATES.get(m[2], m[2]), progress)
    recent = []
    for title, body in reversed(entries[-3:]):
        first = next((line.strip().lstrip("- ") for line in body.splitlines()
                      if line.strip() and not re.match(r"^\s*#{1,6}\s", line)), "")
        display_title = re.sub(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:[+-]\d{2}:\d{2}|Z)?", r"\1 \2", title)
        recent.append("- **" + display_title + "**\n\n  " + first[:100])
    return {
        "goals": find(("目标", "验收"), "尚未填写目标与验收。可编辑当前状态补充。"),
        "progress": progress,
        "risks": find(("风险", "阻塞", "待确认", "待决策"), "\n".join(risks) or "状态文件中尚未单独登记风险或待确认项。"),
        "next": "应用任务由 SQLite 管理；请在任务页查看和编辑。",
        "summary": summary or "还没有阶段总结。点击“写阶段总结”，记录本阶段的完成内容、验证和下一阶段计划。",
        "recent": "\n\n".join(recent) or "还没有按日期追加的日志，点击“写日志”记录最近工作。",
        "updated": str(metadata.get("updated") or "未注明"), "logs": len(entries), "checklist": checklist,
    }


def milestone_rows(text):
    """Extract milestone rows from the user's Markdown table."""
    rows, headers = [], None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip().startswith("|"):
            headers = None
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        normalized = [re.sub(r"[*`_ ]", "", value) for value in cells]
        name_index = next((i for i, value in enumerate(normalized) if value in {"里程碑", "名称", "标题"}), None)
        date_index = next((i for i, value in enumerate(normalized) if value in {"目标日期", "日期", "截止日期"}), None)
        status_index = next((i for i, value in enumerate(normalized) if value == "状态"), None)
        if name_index is not None:
            headers = (status_index, name_index, date_index)
        elif headers and len(cells) > headers[1] and cells[headers[1]]:
            status, name, due = headers
            rows.append({"name": cells[name], "status": cells[status] if status is not None and status < len(cells) else "",
                         "date": cells[due] if due is not None and due < len(cells) else "",
                         "line_number": line_number, "status_column": status,
                         "status_editable": status is not None and status < len(cells)})
    return rows


def milestone_snapshot(rows, today=None):
    """Summarize milestone completion for project progress displays."""
    today = today or date.today()
    done_states = {"已完成", "完成", "completed", "complete", "done"}
    active_states = {"进行中", "处理中", "in_progress", "active"}
    overdue_states = {"逾期", "已逾期", "overdue", "late"}
    done = active = overdue = 0
    for row in rows:
        status = str(row.get("status", "")).strip().casefold()
        if status in done_states:
            done += 1
        elif status in active_states:
            active += 1
        elif status in overdue_states:
            overdue += 1
        else:
            try:
                due = date.fromisoformat(str(row.get("date", "")).strip())
            except ValueError:
                due = None
            overdue += int(due is not None and due < today)
    total = len(rows)
    pending = max(0, total - done - active - overdue)
    return {"total": total, "done": done, "in_progress": active, "pending": pending,
            "overdue": overdue, "progress": round(done * 100 / total) if total else 0}
