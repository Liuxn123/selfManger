"""Quick creation and draft UI for SQLite-backed plans and reviews."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..domain import UserError
from .components import button
from .markdown_editor import MarkdownSourceEditor
from .repeat_options import RepeatOptions


QUICK_TYPES = (
    ("每日计划", "daily"), ("长期计划", "longterm"),
    ("周期计划", "recurring"), ("周复盘", "weekly-review"),
    ("月复盘", "monthly-review"), ("项目计划", "project"),
)

QUICK_TEMPLATES = {
    "daily": ("# 每日计划\n\n## 今天最重要的事\n\n## 时间安排\n\n## 日末复盘\n", "每日计划模板"),
    "longterm": ("# 长期计划\n\n## 目标\n\n## 下一步\n\n## 衡量方式\n", "长期计划模板"),
    "recurring": ("# 周期计划\n\n## 执行说明\n\n## 完成标准\n\n## 执行备注\n", "周期计划模板"),
    "weekly-review": ("# 周复盘\n\n## 本周完成\n\n## 未完成与原因\n\n## 下周计划\n", "周复盘模板"),
    "monthly-review": ("# 月复盘\n\n## 本月完成\n\n## 主要进展\n\n## 问题与复盘\n\n## 下月计划\n", "月复盘模板"),
    "project": ("# 项目计划\n\n## 计划目标\n\n## 阶段安排\n\n## 验收标准\n", "项目计划模板"),
}


class MarkdownQuickCreate(QWidget):
    """Markdown composer that routes the saved record into the shared plan/action store."""
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setObjectName("MarkdownQuickCreate")
        self._build_frame()
        self._build_editor_card()
        self._build_recent_rail()
        self._initialize_draft_state()

    def _build_frame(self):
        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(20)


    def _build_editor_card(self):
        editor_card = QFrame()
        editor_card.setObjectName("ActionPlanSection")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(24, 20, 24, 18)
        editor_layout.setSpacing(12)
        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(3)
        title = QLabel("快速新建")
        title.setObjectName("ActionSectionTitle")
        heading_text.addWidget(title)
        hint = QLabel("计划内容与行动状态统一保存在 SQLite")
        hint.setObjectName("Subtitle")
        heading_text.addWidget(hint)
        heading.addLayout(heading_text, 1)
        self.template = QComboBox()
        for kind, (_body, label_text) in QUICK_TEMPLATES.items():
            self.template.addItem(label_text, kind)
        self.template.currentIndexChanged.connect(self.apply_template)
        self.template.hide()
        self.template_badge = QLabel("●  每日计划模板")
        self.template_badge.setObjectName("PlanTemplateBadge")
        self.template_badge.setFixedHeight(30)
        heading.addWidget(self.template_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        editor_layout.addLayout(heading)

        separator = QFrame()
        separator.setObjectName("PlanSeparator")
        separator.setFixedHeight(1)
        editor_layout.addWidget(separator)
        type_label = QLabel("计划类型")
        type_label.setObjectName("PlanFieldLabel")
        editor_layout.addWidget(type_label)
        self.type_tabs = QGridLayout()
        self.type_tabs.setSpacing(7)
        self.type_buttons = {}
        self.type_group = QButtonGroup(self)
        self.template_buttons = {}
        for index, (label_text, value) in enumerate((("每日计划", "daily"), ("长期计划", "longterm"),
                ("周期计划", "recurring"), ("项目计划", "project"), ("复盘总结", "review"))):
            tab = QPushButton(label_text)
            tab.setObjectName("PlanTypeTab")
            tab.setCheckable(True)
            tab.clicked.connect(lambda _checked=False, value=value: self.choose_kind(value))
            self.type_group.addButton(tab)
            self.type_tabs.addWidget(tab, 0, index)
            self.type_buttons[value] = tab
        editor_layout.addLayout(self.type_tabs)

        row = QHBoxLayout()
        self.kind = QComboBox()
        for label_text, value in QUICK_TYPES:
            self.kind.addItem(label_text, value)
        self.kind.currentIndexChanged.connect(self.type_changed)
        self.kind.hide()
        self.date_enabled = QCheckBox("设置日期")
        self.date_enabled.setChecked(True)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date_enabled.toggled.connect(self.date.setEnabled)
        date_column = QVBoxLayout()
        self.date_label = QLabel("日期")
        self.date_label.setObjectName("PlanFieldLabel")
        date_column.addWidget(self.date_label)
        date_controls = QHBoxLayout()
        date_controls.addWidget(self.date)
        date_controls.addWidget(self.date_enabled)
        date_column.addLayout(date_controls)
        row.addLayout(date_column, 1)
        self.project = QComboBox()
        self.project.addItem("选择项目（可选）", "")
        project_column = QVBoxLayout()
        self.project_label = QLabel("关联项目")
        self.project_label.setObjectName("PlanFieldLabel")
        project_column.addWidget(self.project_label)
        project_column.addWidget(self.project)
        row.addLayout(project_column, 1)
        self.review_period = QComboBox()
        self.review_period.addItem("周复盘", "weekly-review")
        self.review_period.addItem("月复盘", "monthly-review")
        self.review_period.currentIndexChanged.connect(lambda _: self.choose_kind(self.review_period.currentData()))
        row.addWidget(self.review_period)
        editor_layout.addLayout(row)

        related = QHBoxLayout()
        self.plan = QComboBox()
        self.plan.addItem("不关联长期计划", "")
        self.frequency = QComboBox()
        for label_text, value in (("每天打卡", "daily"), ("每周打卡", "weekly"), ("每月打卡", "monthly")):
            self.frequency.addItem(label_text, value)
        self.more_links = QToolButton()
        self.more_links.setObjectName("PlanMoreLinks")
        self.more_links.setText("关联长期计划（可选）")
        self.more_links.setCheckable(True)
        self.more_links.toggled.connect(lambda checked: self.plan.setVisible(checked))
        related.addWidget(self.more_links, 0, Qt.AlignmentFlag.AlignLeft)
        related.addWidget(self.plan, 1)
        related.addWidget(self.frequency, 1)
        editor_layout.addLayout(related)
        self.repeat_options = RepeatOptions(self.frequency, self.date)
        editor_layout.addWidget(self.repeat_options)
        self.plan.currentIndexChanged.connect(self.sync_project_from_plan)

        self.title_input = QLineEdit()
        self.title_input.setMaxLength(160)
        self.title_input.setPlaceholderText("给这条计划起个标题…")
        self.title_input.hide()
        self.body = MarkdownSourceEditor()
        self.body.setPlaceholderText("在这里编写 Markdown 正文…")
        self.body.setMinimumHeight(310)
        editor_layout.addWidget(self.body, 1)
        footer = QHBoxLayout()
        self.route_hint = QLabel()
        self.route_hint.setObjectName("Subtitle")
        self.route_hint.setWordWrap(True)
        footer.addWidget(self.route_hint, 1)
        self.cancel_button = button("取消", self.cancel_document)
        self.cancel_button.setMinimumWidth(90)
        footer.addWidget(self.cancel_button)
        self.delete_button = button("删除计划", self.delete_document)
        self.delete_button.hide()
        footer.addWidget(self.delete_button)
        self.save_button = button("保存并打开", self.page.save_quick_document, True)
        self.save_button.setMinimumWidth(120)
        footer.addWidget(self.save_button)
        editor_layout.addLayout(footer)
        self.root_layout.addWidget(editor_card, 1)


    def _build_recent_rail(self):
        rail_widget = QWidget()
        self.rail = rail_widget
        rail_widget.setMaximumWidth(340)
        rail_widget.setMinimumWidth(280)
        rail = QVBoxLayout(rail_widget)
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(20)
        target_card = QFrame()
        target_card.setObjectName("ActionPlanSection")
        target_layout = QVBoxLayout(target_card)
        target_layout.setContentsMargins(18, 16, 18, 16)
        target_layout.setSpacing(8)
        target_title = QLabel("保存到哪里")
        target_title.setObjectName("ActionSectionTitle")
        target_layout.addWidget(target_title)
        target_subtitle = QLabel("计划、行动和复盘统一保存在 SQLite")
        target_subtitle.setObjectName("Subtitle")
        target_layout.addWidget(target_subtitle)
        source = QFrame()
        source.setObjectName("PlanDocumentSource")
        source_layout = QVBoxLayout(source)
        source_layout.addWidget(QLabel("▤   SQLite 数据源"))
        source_note = QLabel("唯一可变数据源 · 旧 Markdown 原件保留")
        source_note.setObjectName("Subtitle")
        source_layout.addWidget(source_note)
        target_layout.addWidget(source)
        arrow = QLabel("↓")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setObjectName("PlanFlowArrow")
        target_layout.addWidget(arrow)
        self.destination = QLabel()
        self.destination.setWordWrap(True)
        self.destination.setObjectName("PlanDocumentTarget")
        target_layout.addWidget(self.destination)
        self.destination_hint = QLabel("选择项目后，也会在项目计划中显示关联。")
        self.destination_hint.setWordWrap(True)
        self.destination_hint.setObjectName("Subtitle")
        target_layout.addWidget(self.destination_hint)
        rail.addWidget(target_card)

        recent_card = QFrame()
        recent_card.setObjectName("ActionPlanSection")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(18, 16, 18, 14)
        recent_layout.setSpacing(8)
        recent_title = QLabel("模板与最近记录")
        recent_title.setObjectName("ActionSectionTitle")
        recent_layout.addWidget(recent_title)
        recent_subtitle = QLabel("按类型选择模板；双击可打开仍存在的历史 Markdown 原件")
        recent_subtitle.setObjectName("Subtitle")
        recent_subtitle.setWordWrap(True)
        recent_layout.addWidget(recent_subtitle)
        chips = QGridLayout()
        chips.setSpacing(6)
        for index, (label_text, value) in enumerate((("每日计划", "daily"), ("长期计划", "longterm"),
                ("周期计划", "recurring"), ("项目计划", "project"), ("复盘总结", "review"))):
            chip = QPushButton(label_text)
            chip.setObjectName("PlanTemplateChip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _checked=False, value=value: self.choose_kind(value))
            chips.addWidget(chip, index // 3, index % 3)
            self.template_buttons[value] = chip
        recent_layout.addLayout(chips)
        recent_layout.addWidget(QLabel("最近编辑"))
        self.recent = QListWidget()
        self.recent.setObjectName("PlanRecentDocuments")
        self.recent.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.recent.itemClicked.connect(self.page.open_recent_document)
        self.recent.itemDoubleClicked.connect(self.page.open_recent_in_obsidian)
        recent_layout.addWidget(self.recent)
        folder_button = button("查看旧计划资料目录 →", self.page.open_legacy_folder)
        folder_button.setObjectName("PlanFolderButton")
        recent_layout.addWidget(folder_button)
        rail.addWidget(recent_card, 1)
        self.root_layout.addWidget(rail_widget)

    def _initialize_draft_state(self):
        self.apply_template()
        self.type_changed()
        self.mark_clean()
        self.draft_busy = False
        self.restoring = False
        self.draft_revision = 0
        self.saved_draft_snapshot = None
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(700)
        self.draft_timer.timeout.connect(self.persist_draft)
        self.last_body_heading = self.body_heading()
        self.body.textChanged.connect(self.update_title_from_body)
        for signal in (self.body.textChanged, self.title_input.textChanged,
                       self.kind.currentIndexChanged, self.project.currentIndexChanged,
                       self.plan.currentIndexChanged, self.frequency.currentIndexChanged,
                       self.date_enabled.toggled, self.date.dateChanged):
            signal.connect(self.draft_changed)
        self.repeat_options.changed.connect(self.draft_changed)
        self.date.dateChanged.connect(self.update_metadata)
        self.date_enabled.toggled.connect(self.update_metadata)
        self.restore_draft()


    def choose_kind(self, kind):
        if not self.kind.isEnabled():
            current = self.kind.currentData()
            current = "review" if current in {"weekly-review", "monthly-review"} else current
            self.type_buttons[current].setChecked(True)
            for value, chip in self.template_buttons.items():
                chip.setChecked(value == current)
            return
        if kind == "review":
            kind = self.review_period.currentData()
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))

    def cancel_document(self):
        if not self.confirm_discard():
            return
        self.page.quick_edit_record = self.page.review_edit_record = None
        self.delete_button.hide()
        target = self.page.return_after_quick_create
        self.page.return_after_quick_create = None
        self.kind.setEnabled(True)
        self.title_input.clear()
        self.apply_template()
        self.mark_clean()
        if target:
            self.page.select_view(target)

    def delete_document(self):
        if self.page.review_edit_record:
            self.page.request_delete("review", self.page.review_edit_record)
        elif self.page.quick_edit_record:
            kind, record = self.page.quick_edit_record
            self.page.request_delete("plan" if kind in {"longterm", "project"} else "action", record)

    def update_metadata(self, *_):
        self.body.set_metadata(self.kind.currentData(),
            self.date.date().toString("yyyy-MM-dd") if self.date_enabled.isChecked() else "")

    def body_heading(self):
        return next((line[2:].strip() for line in self.body.toPlainText().splitlines() if line.startswith("# ")), "")

    def update_title_from_body(self):
        heading = self.body_heading()
        if heading and heading != self.last_body_heading:
            self.title_input.setText(heading[:160])
        self.last_body_heading = heading

    def draft_changed(self, *_):
        if self.restoring:
            return
        self.draft_revision += 1
        self.page.draft_status.setText("● 正在编辑")
        self.draft_timer.setInterval(700)
        self.draft_timer.start()

    def persist_draft(self):
        if self.page.closed or self.draft_busy:
            return
        snapshot = self.snapshot()
        revision = self.draft_revision
        value = None if snapshot == self.initial else {
            "fields": list(snapshot), "edit": list(self.page.quick_edit_record) if self.page.quick_edit_record else None,
            "review": self.page.review_edit_record, "return_view": self.page.return_after_quick_create}
        self.draft_busy = True
        service = self.page.window.service
        def operation(context):
            context.checkpoint()
            service.save_planning_draft(value)
        def saved(_):
            if revision == self.draft_revision:
                self.saved_draft_snapshot = snapshot
                self.page.draft_status.setText("<span style='color:#62ad89'>●</span> 草稿已自动保存" if value else "● 准备就绪")
        identity = self.page.window.submit(None, "保存计划草稿", operation, saved,
                                         persist_result=False, record_history=False)
        if not identity:
            self.draft_busy = False
            self.page.draft_status.setText("草稿未保存，请保存文档")
            return
        def finished(task_id, state):
            if task_id != identity:
                return
            self.page.window.task_completed.disconnect(finished)
            self.draft_busy = False
            if state != "success":
                self.page.draft_status.setText("草稿保存失败，正文仍保留")
            elif revision != self.draft_revision:
                self.draft_timer.start(50)
        self.page.window.task_completed.connect(finished)

    def restore_draft(self):
        value = self.page.window.service.planning_draft()
        if not isinstance(value, dict) or not isinstance(value.get("fields"), list) or len(value["fields"]) not in {8, 9}:
            return
        kind, title, body, enabled, day, project, plan, frequency = value["fields"][:8]
        if kind not in QUICK_TEMPLATES or not all(isinstance(text, str) for text in (title, body, day, project or "", plan or "", frequency)):
            return
        self.restoring = True
        try:
            self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
            self.body.setPlainText(body)
            self.title_input.setText(title)
            self.date_enabled.setChecked(bool(enabled))
            self.date.setDate(QDate.fromString(day, "yyyy-MM-dd"))
            for combo, identity in ((self.project, project), (self.plan, plan)):
                if identity and combo.findData(identity) < 0:
                    combo.addItem(identity, identity)
                combo.setCurrentIndex(max(0, combo.findData(identity)))
            self.frequency.setCurrentIndex(max(0, self.frequency.findData(frequency)))
            self.repeat_options.reset(self.date.date())
            if len(value["fields"]) == 9:
                self.repeat_options.restore(value["fields"][8])
            self.page.quick_edit_record = value.get("edit")
            self.page.review_edit_record = value.get("review")
            self.delete_button.setVisible(bool(self.page.quick_edit_record or self.page.review_edit_record))
            self.page.return_after_quick_create = value.get("return_view")
            self.kind.setEnabled(not (self.page.quick_edit_record or self.page.review_edit_record))
            self.saved_draft_snapshot = self.snapshot()
            self.page.draft_status.setText("● 已恢复本机草稿")
        finally:
            self.restoring = False

    def snapshot(self):
        return (self.kind.currentData(), self.title_input.text(), self.body.toPlainText(),
                self.date_enabled.isChecked(), self.date.date().toString("yyyy-MM-dd"),
                self.project.currentData(), self.plan.currentData(), self.frequency.currentData(), self.repeat_options.snapshot())

    def mark_clean(self):
        self.initial = self.snapshot()
        if hasattr(self, "draft_timer"):
            self.draft_revision += 1
            self.draft_timer.stop()
            self.persist_draft()

    def confirm_discard(self):
        if self.snapshot() == self.initial:
            return True
        return QMessageBox.question(self, "保留未保存内容", "当前编辑内容尚未保存，是否放弃并继续？",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 920
        self.rail.setVisible(not narrow)
        self.body.setMinimumHeight(240 if narrow else 310)
        self.kind.setMinimumWidth(120)
        self.date.setMinimumWidth(125)
        self.template.setMinimumWidth(140)

    def apply_template(self, *_):
        kind = self.template.currentData() or "daily"
        body, _label = QUICK_TEMPLATES.get(kind, QUICK_TEMPLATES["daily"])
        self.body.setPlainText(body)
        self.template_badge.setText("●  " + _label)

    def type_changed(self, *_):
        kind = self.kind.currentData() or "daily"
        template_index = self.template.findData(kind)
        if template_index >= 0 and self.template.currentIndex() != template_index:
            draft = self.body.toPlainText()
            draft_title = self.title_input.text()
            self.template.blockSignals(True)
            self.template.setCurrentIndex(template_index)
            self.template.blockSignals(False)
            self.apply_template()
            if draft and draft not in {body for body, _label in QUICK_TEMPLATES.values()}:
                self.body.setPlainText(draft)
                self.title_input.setText(draft_title)
        is_review = kind in {"weekly-review", "monthly-review"}
        is_recurring = kind == "recurring"
        self.date_enabled.setVisible(kind in {"longterm", "project"})
        self.date.setVisible(not is_review)
        self.date_label.setVisible(not is_review)
        self.date_enabled.setChecked(kind in {"daily", "recurring"})
        self.date_enabled.setEnabled(kind not in {"daily", "recurring"})
        self.project.setVisible(kind in {"project", "longterm", "daily", "recurring"})
        self.project_label.setVisible(not is_review)
        self.plan.setVisible(kind in {"daily", "recurring"} and self.more_links.isChecked())
        self.more_links.setVisible(kind in {"daily", "recurring"})
        self.frequency.setVisible(is_recurring)
        self.repeat_options.update_visibility()
        if not is_recurring:
            self.repeat_options.hide()
        self.review_period.setVisible(is_review)
        if is_review:
            self.review_period.blockSignals(True)
            self.review_period.setCurrentIndex(max(0, self.review_period.findData(kind)))
            self.review_period.blockSignals(False)
        category = "review" if is_review else kind
        self.type_buttons[category].setChecked(True)
        for value, chip in self.template_buttons.items():
            chip.setChecked(value == category)
        self.update_metadata()
        self.title_input.setPlaceholderText(
            "例如：本周复盘 · 2026年第43周" if kind == "weekly-review" else
            "例如：月度复盘 · 2026年10月" if kind == "monthly-review" else "给这条计划起个标题…")
        if is_review and not self.title_input.text().strip():
            (week_start, _week_end, _week_key), (month_start, _month_end, _month_key) = self.page.review_targets()
            default_title = (f"周复盘 · {week_start.isocalendar().year}年第{week_start.isocalendar().week:02d}周"
                             if kind == "weekly-review" else f"月度复盘 · {month_start:%Y年%m月}")
            self.title_input.setText(default_title)
        self.destination.setText({
            "daily": "每日计划\n按日期安排 · 完成状态保存在 SQLite",
            "longterm": "长期计划 · 集中管理长期目标；日期可以留空。",
            "recurring": "周期计划 · 保存重复规则；每天打开即可打卡。",
            "weekly-review": "周月复盘 · 保存为本周复盘记录。",
            "monthly-review": "周月复盘 · 保存为本月复盘记录。",
            "project": "项目计划 · 同步显示在长期计划；可安排关联的每日行动。",
        }.get(kind, ""))
        self.route_hint.setText("固定字段由模板提供；标题、行动和备注都可自由补充。")

    def load_options(self):
        selected_project = self.project.currentData() or ""
        selected_plan = self.plan.currentData() or ""
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItem("选择项目（可选）", "")
        for project in self.page.projects:
            self.project.addItem(project["name"] + " · " + project["id"], project["id"])
        if selected_project and self.project.findData(selected_project) < 0:
            self.project.addItem(selected_project, selected_project)
        self.project.setCurrentIndex(max(0, self.project.findData(selected_project)))
        self.project.blockSignals(False)
        self.plan.blockSignals(True)
        self.plan.clear()
        self.plan.addItem("不关联长期计划", "")
        for plan in self.page.plans:
            if plan["status"] not in {"done", "archived"}:
                self.plan.addItem(plan["title"], plan["id"])
        if selected_plan and self.plan.findData(selected_plan) < 0:
            self.plan.addItem("原计划（未找到，可取消关联）", selected_plan)
        self.plan.setCurrentIndex(max(0, self.plan.findData(selected_plan)))
        self.plan.blockSignals(False)

    def sync_project_from_plan(self, *_):
        identity = self.plan.currentData()
        plan = next((row for row in self.page.plans if row["id"] == identity), None)
        if not plan or not plan.get("project_id") or self.project.currentData():
            return
        project_index = self.project.findData(plan["project_id"])
        if project_index >= 0:
            self.project.setCurrentIndex(project_index)

    def load_recent(self):
        self.recent.clear()
        documents = []
        for row in self.page.rows:
            if row.get("repeat"):
                documents.append(("recurring", row, "周期计划"))
            elif row.get("due_date"):
                documents.append(("daily", row, "每日计划"))
        for plan in self.page.plans:
            is_project = bool(plan.get("project_id"))
            documents.append(("project" if is_project else "longterm", plan,
                              "项目计划" if is_project else "长期计划"))
        for review in self.page.reviews:
            documents.append(("review", review, "周月复盘"))
        documents.sort(key=lambda row: row[1].get("updated_at", row[1].get("created_at", "")), reverse=True)
        for kind, record, label_text in documents[:5]:
            entity_type = "review" if kind == "review" else "plan" if kind in {"longterm", "project"} else "action"
            try:
                path = self.page.service.documents.path(entity_type, record["id"])
            except (UserError, OSError):
                path = None
            has_legacy_file = bool(path and Path(path).is_file())
            name = Path(path).name if has_legacy_file else record["title"]
            edited = record.get("updated_at", record.get("created_at", ""))[:10]
            edited_label = "今天" if edited == date.today().isoformat() else edited
            entry = QListWidgetItem("•  " + name + "\n    " + label_text + " · " + edited_label)
            entry.setToolTip(name + "\n" + ("双击打开保留的旧 Markdown 原件" if has_legacy_file else label_text))
            entry.setData(Qt.ItemDataRole.UserRole, (kind, record))
            self.recent.addItem(entry)
        if not documents:
            placeholder = QListWidgetItem("保存后的计划会列在这里")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(placeholder)

    def load_document(self, kind, record):
        if not self.confirm_discard():
            return
        origin = self.page.view
        self.page.select_view("quick-create")
        self.page.return_after_quick_create = origin if origin != "quick-create" else None
        if kind == "review":
            self.page.review_edit_record = record
            self.page.quick_edit_record = None
            value = "weekly-review" if record["period_type"] == "weekly" else "monthly-review"
            self.kind.setCurrentIndex(max(0, self.kind.findData(value)))
        else:
            self.page.review_edit_record = None
            identity = record.get("id")
            stored = (record if kind in {"longterm", "project"} else
                      self.page.window.service.planned_task(identity) if identity else record)
            self.page.quick_edit_record = (kind, stored)
            value = {"daily": "daily", "longterm": "longterm", "recurring": "recurring", "project": "project"}.get(kind, "daily")
            self.kind.setCurrentIndex(max(0, self.kind.findData(value)))
        self.kind.setEnabled(False)
        self.delete_button.show()
        self.body.setPlainText(record.get("body", record.get("notes", "")))
        self.title_input.setText(record["title"])
        self.date_enabled.setChecked(bool(record.get("due_date")))
        if record.get("due_date"):
            self.date.setDate(QDate.fromString(record["due_date"], "yyyy-MM-dd"))
        project_id = record.get("project_id") or ""
        if project_id and self.project.findData(project_id) < 0:
            self.project.addItem(project_id, project_id)
        self.project.setCurrentIndex(max(0, self.project.findData(project_id)))
        plan_id = record.get("goal_id") or ""
        if plan_id and self.plan.findData(plan_id) < 0:
            self.plan.addItem("原计划（未找到，可取消关联）", plan_id)
        self.plan.setCurrentIndex(max(0, self.plan.findData(plan_id)))
        rule = self.page.rules.get(record.get("id"), {})
        self.frequency.setCurrentIndex(max(0, self.frequency.findData(rule.get("frequency", "daily"))))
        self.repeat_options.reset(self.date.date(), rule)
        self.mark_clean()
        self.body.setFocus()
