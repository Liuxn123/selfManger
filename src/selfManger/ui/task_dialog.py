"""Shared, date-precise task entry used by the workbench, plan and project pages."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QCheckBox, QTextEdit, QVBoxLayout,
    QToolButton, QWidget, QScrollArea)

from ..project_workspaces import ProjectWorkspace
from ..domain import UserError


class UnifiedTaskDialog(QDialog):
    def __init__(self, window, *, kind="task", due_date: date | None = None,
                 project_id="", title="", record=None, goal_id=""):
        super().__init__(window)
        self.window = window
        self.record = record or {}
        self.saving = False
        self.setWindowTitle(("编辑" if record else "新建") + ("任务" if kind == "task" else "日程"))
        self.setMinimumSize(520, 420)
        self.resize(600, 530)
        root = self._build_root_layout()
        form = self._build_task_form(kind, due_date, project_id, title, goal_id, record)
        advanced_form = self._build_advanced_form(record)
        self._build_dialog_content(root, form, advanced_form, title, record)

    def _build_root_layout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        heading = QLabel(self.windowTitle())
        heading.setObjectName("TodayDialogTitle")
        root.addWidget(heading)
        subtitle = QLabel("填写名称和日期即可；可关联长期计划，完成后同步更新。")
        subtitle.setObjectName("TodayMuted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        return root

    def _build_task_form(self, kind, due_date, project_id, title, goal_id, record):
        form = QFormLayout()
        form.setContentsMargins(16, 14, 16, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.title = QLineEdit(title)
        self.title.setMaxLength(240)
        self.title.setPlaceholderText("例如：整理知识库专题文档")
        self.title.setMinimumHeight(40)
        form.addRow("名称 *", self.title)

        self.kind = QComboBox()
        self.kind.addItem("普通任务", "task")
        self.kind.addItem("日程", "schedule")
        self.kind.setCurrentIndex(0 if kind == "task" else 1)
        self.kind.setMinimumHeight(38)
        form.addRow("类型", self.kind)

        self.has_date = QCheckBox("设置日期")
        self.has_date.setChecked(due_date is not None)
        self.date = QDateEdit(QDate(due_date.year, due_date.month, due_date.day) if due_date else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setMinimumHeight(38)
        date_row = QHBoxLayout()
        date_row.addWidget(self.has_date)
        date_row.addWidget(self.date, 1)
        form.addRow("日期", date_row)
        self.has_date.toggled.connect(self.date.setEnabled)
        self.date.setEnabled(due_date is not None)

        self.project = QComboBox()
        self.project.setEditable(True)
        self.project.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.project.setMinimumHeight(38)
        self.project.setPlaceholderText("可选择或输入项目编号")
        self._load_projects(project_id)
        form.addRow("关联项目", self.project)

        self.goal = QComboBox()
        self.goal.setEditable(True)
        self.goal.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.goal.setMinimumHeight(38)
        self.goal.setPlaceholderText("可选择或输入目标编号")
        self._load_goals(record.get("goal_id", "") if record else goal_id)
        form.addRow("所属长期计划", self.goal)
        return form

    def _build_advanced_form(self, record):
        advanced_form = QFormLayout()
        advanced_form.setContentsMargins(16, 4, 16, 12)
        advanced_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.priority = QComboBox()
        for text, value in (("普通", ""), ("高", "high"), ("中", "medium"), ("低", "low")):
            self.priority.addItem(text, value)
        self.priority.setMinimumHeight(38)
        advanced_form.addRow("优先级", self.priority)

        self.status = QComboBox()
        for text, value in (("未开始", "planned"), ("进行中", "active"), ("已完成", "done")):
            self.status.addItem(text, value)
        self.status.setMinimumHeight(38)
        advanced_form.addRow("状态", self.status)

        self.source = QComboBox()
        self.source.addItem("手动编写", "manual")
        self.source.addItem("AI 规划", "ai")
        self.source.setMinimumHeight(38)
        advanced_form.addRow("来源", self.source)

        self.notes = QTextEdit()
        self.notes.setPlaceholderText("补充说明（可选）")
        self.notes.setFixedHeight(92)
        advanced_form.addRow("备注", self.notes)
        if record:
            for widget, key in ((self.priority, "priority"), (self.source, "source"), (self.status, "status")):
                index = widget.findData(record.get(key, ""))
                if index >= 0:
                    widget.setCurrentIndex(index)
            self.notes.setPlainText(record.get("notes", ""))
        self.source.currentIndexChanged.connect(self._sync_ai_edit_policy)
        self._sync_ai_edit_policy()
        return advanced_form

    def _build_dialog_content(self, root, form, advanced_form, title, record):
        card = QFrame()
        card.setObjectName("TodayDialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.addLayout(form)
        self.advanced_button = QToolButton()
        self.advanced_button.setText("更多选项 · 优先级、状态、来源、备注")
        self.advanced_button.setCheckable(True)
        self.advanced_fields = QWidget()
        self.advanced_fields.setLayout(advanced_form)
        self.advanced_button.toggled.connect(self.advanced_fields.setVisible)
        card_layout.addWidget(self.advanced_button)
        card_layout.addWidget(self.advanced_fields)
        expanded = bool(record and (record.get("notes") or record.get("priority") or record.get("source") == "ai"))
        self.advanced_button.setChecked(expanded)
        self.advanced_fields.setVisible(expanded)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(card)
        root.addWidget(scroll, 1)
        self.save_error = QLabel("保存未完成，输入内容已保留，请检查通知后重试。")
        self.save_error.setObjectName("ActionFormError")
        self.save_error.setWordWrap(True)
        self.save_error.hide()
        root.addWidget(self.save_error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(title.strip()))
        self.title.textChanged.connect(lambda value: buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(value.strip())))
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def confirm(self):
        handler = getattr(self, "save_handler", None)
        if handler:
            handler()
        else:
            self.accept()

    def reject(self):
        if not self.saving:
            super().reject()

    def _sync_ai_edit_policy(self, *_):
        ai_source = self.source.currentData() == "ai"
        existing_ai = bool(self.record and self.record.get("source") == "ai")
        if ai_source and not existing_ai and self.status.currentData() == "done":
            self.status.setCurrentIndex(max(0, self.status.findData("planned")))
        self.source.setEnabled(not existing_ai)
        self.status.setEnabled(not ai_source)
        if ai_source:
            self.status.setToolTip("AI 规划任务必须由 Agent 执行并验证后才能完成。")
        else:
            self.status.setToolTip("")

    def _load_projects(self, selected):
        self.project.addItem("", "")
        root = self.window.service.project_workspace_root()
        if root:
            try:
                entries = ProjectWorkspace(Path(root)).registry().get("projects", [])
                for item in entries:
                    self.project.addItem(f'{item["id"]} · {item["name"]}', item["id"])
            except (OSError, ValueError, KeyError, UserError) as exc:
                self.project.setToolTip(
                    f"项目列表加载失败（{type(exc).__name__}）；可以手动输入项目编号。"
                )
        if selected:
            match = next((i for i in range(self.project.count()) if self.project.itemData(i) == selected), None)
            if match is None:
                self.project.setCurrentText(selected)
            else:
                self.project.setCurrentIndex(match)

    def _load_goals(self, selected):
        self.goal.addItem("", "")
        from ..action_center import ActionCenter
        goals = [item for item in ActionCenter(self.window.store).plans()
                 if item["status"] != "archived" or item["id"] == selected]
        for item in goals:
            self.goal.addItem(f'{item["id"]} · {item["title"]}', item["id"])
        if selected:
            match = next((i for i in range(self.goal.count()) if self.goal.itemData(i) == selected), None)
            if match is None:
                self.goal.addItem("原计划（未找到，可取消关联）", selected)
                self.goal.setCurrentIndex(self.goal.count() - 1)
            else:
                self.goal.setCurrentIndex(match)

    def values(self):
        project_id = self.project.currentData()
        if project_id is None or self.project.currentText() != self.project.itemText(self.project.currentIndex()):
            project_id = self.project.currentText().split(" · ", 1)[0].strip()
        goal_id = self.goal.currentData()
        if goal_id is None or self.goal.currentText() != self.goal.itemText(self.goal.currentIndex()):
            goal_id = self.goal.currentText().split(" · ", 1)[0].strip()
        project_root = self.window.service.project_workspace_root()
        if self.record and project_id == self.record.get("project_id"):
            project_root = self.record.get("project_root") or project_root
        project_root = str(Path(project_root).expanduser().resolve()) if project_root and project_id else None
        return {
            "title": self.title.text().strip(),
            "kind": self.kind.currentData(),
            "due_date": self.date.date().toPython().isoformat() if self.has_date.isChecked() else None,
            "project_id": project_id,
            "project_root": project_root,
            "goal_id": goal_id,
            "priority": self.priority.currentData(),
            "status": self.status.currentData(),
            "source": self.source.currentData(),
            "notes": self.notes.toPlainText(),
        }
