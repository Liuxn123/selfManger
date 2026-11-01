import unittest

from selfManger.ui.project_widgets import sections, overview_snapshot
from selfManger.task_ordering import project_checklist_snapshot


class ProjectOverviewTests(unittest.TestCase):
    def test_sections_preserve_nested_markdown_and_ignore_fenced_heading_examples(self):
        text = "---\nupdated: 2026-10-05\n---\n# 状态\n## 目标与验收\n- 原目标\n```markdown\n## 不是新章节\n```\n### 补充\n正文\n## 当前进度\n- 保留原进度\n"
        result = sections(text)
        self.assertEqual([title for title, _ in result], ["目标与验收", "当前进度"])
        self.assertIn("## 不是新章节", result[0][1])
        self.assertIn("### 补充", result[0][1])

    def test_project_checklist_is_populated_from_sqlite_tasks_only(self):
        snapshot = overview_snapshot("# 状态", "", [])
        merged = project_checklist_snapshot(snapshot, [{
            "id": "sqlite-task", "title": "SQLite 项目任务", "source": "ai",
            "status": "planned", "done": False, "due_date": None,
        }])
        self.assertEqual([row["text"] for row in merged["checklist"]["tasks"]], ["SQLite 项目任务"])
        self.assertEqual(merged["checklist"]["total"], 1)
        self.assertIn("SQLite", snapshot["next"])

    def test_snapshot_uses_real_records_and_never_infers_project_percent(self):
        status = "---\nupdated: 2026-10-05\n---\n## 目标与验收\n- 原目标\n## 当前进度\n- 当前阶段：正在研究复杂方案\n- 阻塞：等待验证\n## 其他个人说明\n不分类的原文仍在完整页\n"
        handoff = "## 2026-10-04 — 初始化\n建立结构\n## 交接格式\n不是工作记录\n## 2026-10-05 — 检查\n真实验证\n"
        result = overview_snapshot(status, handoff, ["上一阶段总结", "最新阶段总结"])
        self.assertEqual(result["logs"], 2)
        self.assertEqual(result["summary"], "最新阶段总结")
        self.assertEqual(result["updated"], "2026-10-05")
        self.assertIn("等待验证", result["risks"])
        self.assertIn("SQLite", result["next"])
        self.assertIn("真实验证", result["recent"])
        self.assertNotIn("percent", result)
        legacy = overview_snapshot(status.replace("阻塞：", "阻塞/待决定："), "", [])
        self.assertIn("等待验证", legacy["risks"])

    def test_recent_preview_uses_log_body_instead_of_form_heading(self):
        result = overview_snapshot("# 状态", "## 2026-10-05T18:58:18+08:00 — 用户日志\n\n### 完成了什么\n\n实际日志内容\n\n### 如何确认\n\n实际依据", [])
        self.assertIn("实际日志内容", result["recent"])
        self.assertIn("2026-10-05 18:58", result["recent"])
        self.assertNotIn("完成了什么", result["recent"])


if __name__ == "__main__":
    unittest.main()
