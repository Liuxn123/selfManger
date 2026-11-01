"""Category-local Markdown creation without changing the planning page or its draft."""
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from .markdown_editor import MarkdownSourceEditor
from .repeat_options import RepeatOptions


class PlanDocumentDialog(QDialog):
    def __init__(self, page, kind, template, *, record=None, defaults=None):
        super().__init__(page)
        self.page, self.kind, self.record = page, kind, record
        self.busy = False
        self.write_state = {}
        defaults = defaults or {}
        self.setObjectName("PlanDocumentDialog")
        labels = {"daily": "每日计划", "longterm": "长期计划", "recurring": "周期计划",
                  "project": "项目计划", "weekly-review": "周复盘", "monthly-review": "月复盘"}
        self.setWindowTitle(("编辑" if record else "新建") + labels[kind])
        self.resize(760, 740)
        self.setMinimumSize(560, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        heading = QLabel(self.windowTitle())
        heading.setObjectName("ActionSectionTitle")
        root.addWidget(heading)
        hint = QLabel("保存后留在当前分类；计划、行动和复盘统一保存在 SQLite。")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit((record or {}).get("title", defaults.get("title", "")))
        self.title.setMaxLength(160)
        self.title.setPlaceholderText("计划名称，也可以使用正文的一级标题")
        form.addRow("名称", self.title)
        self.has_date = QCheckBox("设置日期")
        self.has_date.setChecked(kind in {"daily", "recurring"})
        self.date = QDateEdit(QDate(page.day.year, page.day.month, page.day.day))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setEnabled(self.has_date.isChecked())
        self.has_date.toggled.connect(self.date.setEnabled)
        date_row = QHBoxLayout()
        date_row.addWidget(self.date, 1)
        date_row.addWidget(self.has_date)
        self.has_date.setVisible(kind in {"longterm", "project"})
        self.is_review = kind in {"weekly-review", "monthly-review"}
        if self.is_review:
            weekly, monthly = page.review_targets()
            start, end, key = weekly if kind == "weekly-review" else monthly
            self.period = {"period_type": "weekly" if kind == "weekly-review" else "monthly",
                           "period_key": key, "start_date": start.isoformat(), "end_date": end.isoformat()}
            if record:
                self.period.update({field: record[field] for field in self.period})
            form.addRow("复盘区间", QLabel(self.period["start_date"] + "  —  " + self.period["end_date"]))
            self.date.hide()
        else:
            form.addRow("日期" if kind != "recurring" else "开始日期", date_row)
        self.project = QComboBox()
        self.project.addItem("不关联项目", "")
        for project in page.projects:
            if project.get("state") == "active":
                self.project.addItem(project["name"] + " · " + project["id"], project["id"])
        selected_project = defaults.get("project_id", "")
        if selected_project and self.project.findData(selected_project) < 0:
            self.project.addItem(selected_project, selected_project)
        self.project.setCurrentIndex(max(0, self.project.findData(selected_project)))
        self.plan = QComboBox()
        self.plan.addItem("独立行动", "")
        for plan in page.plans:
            if plan["status"] not in {"done", "archived"} or plan["id"] == defaults.get("goal_id"):
                self.plan.addItem(plan["title"], plan["id"])
        self.plan.setCurrentIndex(max(0, self.plan.findData(defaults.get("goal_id", ""))))
        self.plan.currentIndexChanged.connect(self.link_project)
        self.frequency = QComboBox()
        for name, value in (("每天", "daily"), ("每周", "weekly"), ("每月", "monthly")):
            self.frequency.addItem(name, value)
        self.repeat_options = RepeatOptions(self.frequency, self.date)
        if not self.is_review:
            form.addRow("关联项目" + ("（必选）" if kind == "project" else "（可选）"), self.project)
            if kind in {"daily", "recurring"}:
                form.addRow("所属计划", self.plan)
            if kind == "recurring":
                form.addRow("重复", self.frequency)
                form.addRow("重复日期", self.repeat_options)
                repeat_label = form.labelForField(self.repeat_options)
                repeat_label.hide()
                self.frequency.currentIndexChanged.connect(lambda: repeat_label.setVisible(
                    self.frequency.currentData() in {"weekly", "monthly"}))
        root.addLayout(form)
        self.body = MarkdownSourceEditor()
        self.body.setMinimumHeight(180)
        self.body.setPlainText((record or {}).get("body", defaults.get("notes", template)))
        root.addWidget(self.body, 1)
        self.date.dateChanged.connect(self.update_metadata)
        self.has_date.toggled.connect(self.update_metadata)
        self.update_metadata()
        self.error = QLabel()
        self.error.setObjectName("ActionFormError")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)
        footer = QHBoxLayout()
        if record:
            delete = QPushButton("删除复盘")
            delete.clicked.connect(lambda: page.request_delete("review", record, on_deleted=self.accept))
            footer.addWidget(delete)
        footer.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.save = QPushButton("保存" + ("复盘" if self.is_review else "计划"))
        self.save.setProperty("primary", True)
        self.save.clicked.connect(self.save_document)
        footer.addWidget(self.save)
        root.addLayout(footer)
        self.initial = self.snapshot()

    def update_metadata(self, *_):
        self.body.set_metadata(self.kind, self.date.date().toString("yyyy-MM-dd") if self.has_date.isChecked() else "")

    def link_project(self, *_):
        selected = next((row for row in self.page.plans if row["id"] == self.plan.currentData()), None)
        if selected and selected.get("project_id"):
            self.project.setCurrentIndex(max(0, self.project.findData(selected["project_id"])))

    def snapshot(self):
        return (self.title.text(), self.body.toPlainText(), self.has_date.isChecked(),
                self.date.date().toString("yyyy-MM-dd"), self.project.currentData(),
                self.plan.currentData(), self.frequency.currentData(), self.repeat_options.snapshot())

    def reject(self):
        if self.busy:
            return
        if self.snapshot() != self.initial and QMessageBox.question(self, "保留未保存修改", "放弃当前弹窗中尚未保存的内容？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Discard:
            return
        super().reject()

    def save_document(self):
        if self.busy or self.page.saving:
            return
        title = self.title.text().strip() or next((line[2:].strip() for line in self.body.toPlainText().splitlines() if line.startswith("# ")), "")
        project_id = self.project.currentData() or ""
        if not title or (self.kind == "project" and not project_id):
            self.error.setText("请填写计划名称。" if not title else "请选择进行中的关联项目。")
            self.error.show()
            return
        body = self.body.toPlainText()
        if (self.kind == "recurring" and self.frequency.currentData() == "weekly"
                and not self.repeat_options.values()["repeat_days"]):
            self.error.setText("请至少选择一个重复星期。")
            self.error.show()
            return
        service = self.page.service
        existing = self.record or self.write_state.get("record")
        identity = existing["id"] if existing else None
        if self.is_review:
            values = {**self.period, "title": title, "body": body,
                      **({"_expected": existing} if existing else {})}
            entity_type = "review"
            save_record = lambda: service.save_review(values, identity)
        else:
            values = {"title": title, "notes": body,
                      "due_date": self.date.date().toString("yyyy-MM-dd") if self.has_date.isChecked() else None,
                      "project_id": project_id,
                      "project_root": self.page.window.service.project_workspace_root() if project_id else "",
                      "goal_id": self.plan.currentData() or ""}
            if existing:
                values["_expected"] = existing
            if self.kind in {"project", "longterm"}:
                entity_type = "plan"
                save_record = lambda: service.save_plan(values, identity)
            else:
                entity_type = "action"
                values["repeat"] = self.frequency.currentData() if self.kind == "recurring" else ""
                values.update(self.repeat_options.values() if self.kind == "recurring" else {})
                save_record = lambda: service.save_action(values, identity)
        self.busy = self.page.saving = True
        self.error.hide()
        self.setEnabled(False)
        def saved(_):
            self.busy = self.page.saving = False
            self.accept()
            self.page.notify_saved()
        write_state = self.write_state
        def persist_record():
            record = save_record()
            write_state["record"] = record
            return record
        task_id = self.page.window.submit(None, self.windowTitle(), lambda context:
            self.page.save_plan_record(context, persist_record), saved, persist_result=False)
        def finished(identity, state):
            if identity != task_id:
                return
            self.page.window.task_completed.disconnect(finished)
            self.busy = self.page.saving = False
            self.setEnabled(True)
            if state != "success":
                self.error.setText("保存未完成，内容已保留，请检查字段后重试。")
                self.error.show()
        if task_id:
            self.page.window.task_completed.connect(finished)
        else:
            self.busy = self.page.saving = False
            self.setEnabled(True)
