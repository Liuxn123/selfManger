"""Readable planning rows, summaries, and a structured weekly/monthly review."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget)


def label(text, name="PlanSecondary"):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def action(text, callback):
    widget = QPushButton(text)
    widget.setObjectName("PlanRowAction")
    widget.clicked.connect(callback)
    return widget


class ModuleSummary(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("PlanModuleSummary")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        self.values = []
        for _ in range(3):
            column = QVBoxLayout()
            column.setSpacing(4)
            title, value = label(""), label("", "PlanMetricValue")
            column.addWidget(title)
            column.addWidget(value)
            layout.addLayout(column, 1)
            self.values.append((title, value))

    def set_values(self, values):
        for (title, value), (caption, number) in zip(self.values, values):
            title.setText(caption)
            value.setText(str(number))


def install_action_row(page, item, row, names, can_check):
    card = QFrame()
    card.setObjectName("PlanActionRow")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)
    check = QCheckBox()
    check.setObjectName("PlanRowCheck")
    check.setAccessibleName(("标记未完成：" if row["done"] else "标记完成：") + row["title"])
    check.setChecked(row["done"] if can_check else False)
    check.setEnabled(can_check)
    check.toggled.connect(lambda done: item.setCheckState(Qt.CheckState.Checked if done else Qt.CheckState.Unchecked))
    layout.addWidget(check)
    content = QVBoxLayout()
    content.setSpacing(5)
    title = action(row["title"], lambda: page.open_item(item))
    title.setObjectName("PlanRowTitle")
    title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    title.setToolTip(row["title"])
    content.addWidget(title)
    details = [value for value in ("时间 " + row["scheduled_time"] if row.get("scheduled_time") else "",
                                   row.get("plan_title"), names.get(row.get("project_id"), row.get("project_id"))) if value]
    if row.get("repeat"):
        details.append({"daily": "每天重复", "weekly": "每周重复", "monthly": "每月重复"}[row["repeat"]])
        details.append(("本日打卡" if can_check else "执行日 " + row["occurrence_day"]))
    elif row["overdue"]:
        details.append("原定 " + row["occurrence_day"])
    if details:
        content.addWidget(label(" · ".join(details)))
    layout.addLayout(content, 1)
    badge = label("已完成" if row["done"] and can_check else "待完成" if can_check else "本日不打卡", "PlanStatusBadge")
    badge.setProperty("done", bool(row["done"] and can_check))
    badge.setFixedHeight(26)
    layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(action("编辑", lambda: page.open_item(item)))
    layout.addWidget(action("删除", lambda: page.request_delete("action", page.window.service.planned_task(row["id"]) or row)))
    item.setSizeHint(QSize(0, 98 if details else 76))
    page.list.setItemWidget(item, card)


def install_plan_row(page, item, plan, names):
    card = QFrame()
    card.setObjectName("PlanListCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 17, 20, 16)
    layout.setSpacing(10)
    top = QHBoxLayout()
    title = action(plan["title"], lambda: page.open_plan_editor(plan["id"]))
    title.setObjectName("PlanRowTitle")
    title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    title.setToolTip(plan["title"])
    top.addWidget(title, 1)
    badge = label({"done": "已完成", "archived": "已归档"}.get(plan["status"], "推进中"), "PlanStatusBadge")
    badge.setProperty("done", plan["status"] == "done")
    badge.setFixedHeight(26)
    top.addWidget(badge)
    layout.addLayout(top)
    linked = [row for row in page.all_rows if row.get("goal_id") == plan["id"]]
    done = sum(row["done"] for row in linked)
    project = names.get(plan.get("project_id"), plan.get("project_id"))
    metadata = ["截止 " + plan["due_date"] if plan.get("due_date") else "未设置期限"]
    if project:
        metadata.insert(0, "项目 · " + project)
    layout.addWidget(label("   ·   ".join(metadata)))
    progress = QProgressBar()
    progress.setObjectName("PlanListProgress")
    progress.setRange(0, max(1, len(linked)))
    progress.setValue(done)
    progress.setTextVisible(False)
    progress.setFixedHeight(6)
    layout.addWidget(progress)
    footer = QHBoxLayout()
    footer.addWidget(label(f"{done} / {len(linked)} 项关联行动完成" if linked else "还没有关联行动，从这里安排下一步"), 1)
    footer.addWidget(action("编辑计划", lambda: page.open_plan_editor(plan["id"])))
    footer.addWidget(action("删除", lambda: page.request_delete("plan", plan)))
    if plan["status"] not in {"done", "archived"}:
        footer.addWidget(action("安排每日行动", lambda: page.schedule_plan(plan)))
    layout.addLayout(footer)
    item.setSizeHint(QSize(0, 166))
    page.list.setItemWidget(item, card)


def install_project_schedule_row(page, item, row, names):
    card = QFrame()
    card.setObjectName("PlanActionRow")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    content = QVBoxLayout()
    title = action(row["title"], lambda: page.open_project_record(row))
    title.setObjectName("PlanRowTitle")
    title.setToolTip(row["title"])
    title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    content.addWidget(title)
    source = "个人行动" if row["personal"] else "AI 规划" if row["source"] == "ai" else "项目任务文档"
    content.addWidget(label(" · ".join((names.get(row["project_id"], row["project_id"]), source,
        row.get("occurrence_day") or "未设置日期"))))
    layout.addLayout(content, 1)
    selected = "done" if row["done"] else "planned" if row.get("repeat") else row["status"]
    text = ("本日已完成" if row["done"] else "待打卡") if row.get("repeat") else {
        "planned": "待完成", "active": "进行中", "done": "已完成"}[selected]
    status = label(text, "ProjectTaskStatusBadge")
    status.setProperty("status", selected)
    status.setWordWrap(False)
    status.setFixedHeight(28)
    layout.addWidget(status, alignment=Qt.AlignmentFlag.AlignVCenter)
    edit_status = action("编辑状态", lambda: page.edit_project_task_status(row))
    edit_status.setObjectName("EditProjectTaskStatus")
    layout.addWidget(edit_status)
    layout.addWidget(action("编辑行动" if row["personal"] else "查看安排", lambda: page.open_project_record(row)))
    layout.addWidget(action("删除", lambda: page.request_delete("action", page.window.service.planned_task(row["id"]) or row)))
    item.setSizeHint(QSize(0, 94))
    page.project_records_list.setItemWidget(item, card)


class ProjectTaskStatusDialog(QDialog):
    """Status changes require explicit edit and save; selection alone never writes."""
    def __init__(self, page, row):
        super().__init__(page)
        self.page, self.row, self.busy = page, row, False
        self.setWindowTitle("编辑状态")
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        root.addWidget(label(row["title"], "ActionSectionTitle"))
        self.combo = QComboBox()
        self.combo.setObjectName("ProjectTaskStatus")
        options = (("待打卡", "planned"), ("本日已完成", "done")) if row.get("repeat") else (
            ("待完成", "planned"), ("进行中", "active"), ("已完成", "done"))
        for text, value in options:
            self.combo.addItem(text, value)
        self.initial = "done" if row["done"] else "planned" if row.get("repeat") else row["status"]
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.initial)))
        form = QFormLayout()
        form.addRow("状态", self.combo)
        root.addLayout(form)
        self.error = label("保存未完成，请检查通知后重试。", "ActionFormError")
        self.error.hide()
        root.addWidget(self.error)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save = QPushButton("保存")
        self.save.setProperty("primary", True)
        self.save.clicked.connect(self.save_status)
        footer.addWidget(self.save)
        root.addLayout(footer)

    def reject(self):
        if not self.busy:
            super().reject()

    def save_status(self):
        if self.busy or self.page.saving:
            return
        if self.combo.currentData() == self.initial:
            self.accept()
            return
        self.busy = True
        self.error.hide()
        self.setEnabled(False)
        def saved():
            self.busy = False
            self.accept()
        def released():
            try:
                self.busy = False
                self.setEnabled(True)
                if self.result() != QDialog.DialogCode.Accepted:
                    self.error.show()
            except RuntimeError:
                return  # The successful dialog may already have been disposed.
        self.page.change_project_task_status(self.row, self.combo, on_saved=saved, on_released=released)


class ReviewOverview(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.mode = "weekly"
        self.expanded = False
        self.history_expanded = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        tabs = QHBoxLayout()
        self.tabs = {}
        for title, mode in (("周复盘", "weekly"), ("月复盘", "monthly")):
            tab = QPushButton(title)
            tab.setObjectName("PlanTypeTab")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _checked=False, mode=mode: self.select_mode(mode))
            self.tabs[mode] = tab
            tabs.addWidget(tab)
        tabs.addStretch(1)
        self.write_button = action("撰写复盘", self.write_review)
        tabs.addWidget(self.write_button)
        root.addLayout(tabs)
        self.summary = ModuleSummary()
        root.addWidget(self.summary)
        self.sections = QWidget()
        self.section_layout = QVBoxLayout(self.sections)
        self.section_layout.setContentsMargins(0, 0, 0, 0)
        self.section_layout.setSpacing(14)
        root.addWidget(self.sections)

    def select_mode(self, mode):
        self.mode = mode
        self.expanded = self.history_expanded = False
        self.render()

    def write_review(self):
        self.page.open_document_dialog("weekly-review" if self.mode == "weekly" else "monthly-review")

    def section(self, title, hint=""):
        card = QFrame()
        card.setObjectName("PlanReviewSection")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(label(title, "PlanSectionHeading"))
        if hint:
            layout.addWidget(label(hint))
        self.section_layout.addWidget(card)
        return layout

    def render(self):
        for mode, button in self.tabs.items():
            button.setChecked(mode == self.mode)
        while self.section_layout.count():
            widget = self.section_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        weekly, monthly = self.page.review_targets()
        start, end, key = weekly if self.mode == "weekly" else monthly
        summary = self.page.service.review_period_summary(start, end, self.page.period_rows, self.page.plans)
        rows = summary["rows"]
        self.summary.set_values((("已完成 / 安排", f"{summary['done']} / {summary['total']}"),
            ("完成率", f"{summary['done'] * 100 // summary['total']}%" if summary["total"] else "—"),
            ("待完成", summary["total"] - summary["done"])))
        existing = next((review for review in self.page.reviews if review["period_key"] == key), None)
        self.write_button.setText("编辑本期复盘" if existing else "撰写本期复盘")
        content = self.section("本周安排" if self.mode == "weekly" else "本月安排",
            f"{start:%Y-%m-%d}  —  {end:%Y-%m-%d} · 周期计划按每次打卡统计")
        if not rows:
            content.addWidget(label("这个区间还没有安排。你仍然可以撰写复盘，记录收获与下一步。"))
        for row in rows if self.expanded else rows[:8]:
            line = QHBoxLayout()
            status = label("✓ 已完成" if row["done"] else "○ 待完成", "PlanStatusBadge")
            status.setProperty("done", row["done"])
            line.addWidget(status)
            line.addWidget(label(row["title"], "PlanReviewActionTitle"), 1)
            line.addWidget(label(row["occurrence_day"][5:]))
            content.addLayout(line)
        if len(rows) > 8:
            content.addWidget(action("收起安排" if self.expanded else f"展开全部 {len(rows)} 项安排", self.toggle_rows))
        if summary["plans"]:
            linked = self.section("关联计划", "点击计划继续编辑或安排每日行动")
            for plan in summary["plans"]:
                linked.addWidget(action(plan["title"] + " · " + ("已完成" if plan["status"] == "done" else "推进中"),
                    lambda _checked=False, identity=plan["id"]: self.page.open_plan_editor(identity)))
        history = self.section("历史周复盘" if self.mode == "weekly" else "历史月复盘", "保存的总结可以继续查看和编辑")
        self.section_layout.insertWidget(1, history.parentWidget())
        reviews = [review for review in self.page.reviews if review["period_type"] == self.mode]
        if not reviews:
            history.addWidget(label("还没有保存的复盘。把这一阶段的收获、问题和下一步写下来。"))
            history.addWidget(action("开始撰写", self.write_review))
        for review in reviews if self.history_expanded else reviews[:12]:
            line = QHBoxLayout()
            line.addWidget(action(review["title"] + "   ·   " + review["start_date"] + " — " + review["end_date"],
                lambda _checked=False, review=review: self.page.open_document_dialog(
                    "weekly-review" if review["period_type"] == "weekly" else "monthly-review", record=review)), 1)
            line.addWidget(action("删除", lambda _checked=False, review=review: self.page.request_delete("review", review)))
            history.addLayout(line)
        if len(reviews) > 12:
            history.addWidget(action("收起历史" if self.history_expanded else f"查看全部 {len(reviews)} 条复盘", self.toggle_history))

    def toggle_rows(self):
        self.expanded = not self.expanded
        self.render()

    def toggle_history(self):
        self.history_expanded = not self.history_expanded
        self.render()
