"""Six peer planning modules with Markdown creation and shared action records."""
from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
    QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QProgressBar, QSplitter, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from ..action_center import ActionCenter
from ..domain import UserError
from ..project_workspaces import ProjectWorkspace
from ..workbench import work_root
from .components import button, nav_icon
from .repeat_options import RepeatOptions
from .plan_quick_create import MarkdownQuickCreate, QUICK_TEMPLATES, QUICK_TYPES
from .plan_dialog import PlanDocumentDialog
from .plan_module_widgets import (ModuleSummary, ReviewOverview, install_action_row,
    install_plan_row, install_project_schedule_row, ProjectTaskStatusDialog)


class ItemEditor(QWidget):
    """The same small form is used in the new-item dialog and the detail drawer."""
    def __init__(self, page, *, record=None, plan=False, day=None, project_id="", plan_id=""):
        super().__init__()
        self.page, self.record, self.is_plan = page, record or {}, plan
        self.setObjectName("ActionEditor")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        self.heading = QLabel(("编辑" if record else "新建") + ("计划" if plan else "行动"))
        self.heading.setObjectName("ActionEditorHeading")
        root.addWidget(self.heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("ActionEditorContent")
        form = QFormLayout(content)
        form.setContentsMargins(0, 4, 0, 12)
        form.setSpacing(12)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.title = QLineEdit(self.record.get("title", ""))
        self.title.setMaxLength(240)
        self.title.setPlaceholderText("想做什么？")
        form.addRow("名称", self.title)
        if plan and not record:
            self.template = QComboBox()
            self.template.addItem("空白计划", "")
            for item in page.templates:
                self.template.addItem(item["title"], item["notes"])
            form.addRow("从模板开始（可选）", self.template)
        selected_day = self.record.get("due_date") or (day.isoformat() if day else "")
        self.has_date = QCheckBox("设置期限" if plan else "安排日期")
        self.has_date.setChecked(bool(selected_day))
        self.date = QDateEdit(QDate.fromString(selected_day, "yyyy-MM-dd") if selected_day else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setEnabled(bool(selected_day))
        self.has_date.toggled.connect(self.date.setEnabled)
        date_row = QHBoxLayout()
        date_row.addWidget(self.has_date)
        date_row.addWidget(self.date, 1)
        form.addRow("期限（可选）" if plan else "执行日期", date_row)
        rule = page.rules.get(self.record.get("id"), {})
        self.original_rule = rule
        self.repeat = QComboBox()
        for title, value in (("不重复", ""), ("每天", "daily"), ("每周", "weekly"), ("每月", "monthly")):
            self.repeat.addItem(title, value)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(rule.get("frequency", ""))))
        self.repeat_options = RepeatOptions(self.repeat, self.date)
        self.repeat_options.reset(self.date.date(), rule)
        if not plan:
            form.addRow("重复", self.repeat)
            form.addRow(self.repeat_options)
        self.repeat.currentIndexChanged.connect(self.ensure_repeat_date)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("关联项目 / 计划 / 优先级 / 备注")
        self.advanced_toggle.setCheckable(True)
        form.addRow(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        advanced.setSpacing(10)
        self.project = QComboBox()
        self.project.addItem("不关联项目", "")
        selected_project = self.record.get("project_id") or project_id
        selected_root = self.record.get("project_root") or page.window.service.project_workspace_root()
        for item in page.projects:
            self.project.addItem(item["name"] + " · " + item["id"], item["id"])
        if selected_project and self.project.findData(selected_project) < 0:
            self.project.addItem(selected_project, selected_project)
        self.project.setCurrentIndex(max(0, self.project.findData(selected_project)))
        self.project_root = selected_root
        advanced.addRow("关联项目", self.project)
        self.plan = QComboBox()
        self.plan.addItem("独立行动", "")
        selected_plan = self.record.get("goal_id") or plan_id
        for item in page.plans:
            if item["status"] != "archived" or item["id"] == selected_plan:
                self.plan.addItem(item["title"], item["id"])
        if selected_plan and self.plan.findData(selected_plan) < 0:
            self.plan.addItem("原计划（未找到，可取消关联）", selected_plan)
        self.plan.setCurrentIndex(max(0, self.plan.findData(selected_plan)))
        if not plan:
            advanced.addRow("所属计划", self.plan)
        self.priority = QComboBox()
        for title, value in (("普通", ""), ("高", "high"), ("中", "medium"), ("低", "low")):
            self.priority.addItem(title, value)
        self.priority.setCurrentIndex(max(0, self.priority.findData(self.record.get("priority", ""))))
        if not plan:
            advanced.addRow("优先级", self.priority)
        self.status = QComboBox()
        for title, value in (("推进中", "planned"), ("已完成", "done"), ("已归档", "archived")):
            self.status.addItem(title, value)
        self.status.setCurrentIndex(max(0, self.status.findData(self.record.get("status", "planned"))))
        if plan:
            advanced.addRow("计划状态", self.status)
        self.end_enabled = QCheckBox("设置重复截止日期")
        self.end_enabled.setChecked(bool(rule.get("end_date")))
        self.end = QDateEdit(QDate.fromString(rule["end_date"], "yyyy-MM-dd") if rule.get("end_date") else QDate.currentDate())
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("yyyy-MM-dd")
        self.end.setEnabled(self.end_enabled.isChecked())
        self.end_enabled.toggled.connect(self.end.setEnabled)
        if not plan:
            advanced.addRow(self.end_enabled)
            advanced.addRow(self.end)
        self.notes = QTextEdit()
        self.notes.setPlainText(self.record.get("notes", ""))
        self.notes.setAcceptRichText(False)
        self.notes.setMinimumHeight(96)
        self.notes.setMaximumHeight(150)
        advanced.addRow("备注", self.notes)
        if hasattr(self, "template"):
            self.template.currentIndexChanged.connect(lambda _index: self.notes.setPlainText(self.template.currentData() or ""))
        form.addRow(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced_toggle.setChecked(True)  # 关联项目/计划一眼可见，不用再找折叠
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        if plan and record:
            shared_hint = QLabel("关联行动与计划共用数据，计划也显示在长期计划中。")
            shared_hint.setWordWrap(True)
            root.addWidget(shared_hint)
            root.addWidget(button("安排到每日计划…", lambda: page.schedule_plan(record)))
            root.addWidget(button("在长期计划中查看", lambda: page.select_view("plans")))
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setObjectName("ActionFormError")
        self.error.hide()
        root.addWidget(self.error)
        footer = QHBoxLayout()
        if record:
            footer.addWidget(button("删除计划", lambda: page.request_delete("plan" if plan else "action", record)))
        self.cancel = button("取消", lambda: None)
        self.save = button("保存", lambda: None, True)
        self.save.setEnabled(bool(self.title.text().strip()))
        self.title.textChanged.connect(lambda text: self.save.setEnabled(bool(text.strip())))
        footer.addStretch(1)
        footer.addWidget(self.cancel)
        footer.addWidget(self.save)
        root.addLayout(footer)
        self.initial = self.values()
        self.has_date.toggled.connect(lambda checked: self.repeat.setCurrentIndex(0) if not checked else None)

    def ensure_repeat_date(self):
        if self.repeat.currentData():
            self.has_date.setChecked(True)

    def values(self):
        project_id = self.project.currentData() or ""
        same_project = project_id == self.record.get("project_id")
        project_root = self.project_root if same_project else self.page.window.service.project_workspace_root()
        result = {"title": self.title.text().strip(), "kind": self.record.get("kind", "task"),
                  "due_date": self.date.date().toPython().isoformat() if self.has_date.isChecked() else None,
                  "project_id": project_id, "project_root": str(Path(project_root).resolve()) if project_id and project_root else "",
                  "goal_id": self.plan.currentData() or "", "notes": self.notes.toPlainText(),
                  "priority": self.priority.currentData(), "status": self.status.currentData() if self.is_plan else self.record.get("status", "planned")}
        if not self.is_plan:
            result.update(repeat=self.repeat.currentData(), repeat_end=self.end.date().toPython().isoformat() if self.end_enabled.isChecked() and self.repeat.currentData() else None,
                          **self.repeat_options.values())
        if self.record:
            result["_expected"] = self.record
            if not self.is_plan:
                result["_expected_rule"] = self.original_rule
        return result

    def dirty(self):
        return self.values() != self.initial


class ItemDialog(QDialog):
    def __init__(self, page):
        super().__init__(page.window)
        self.page = page

    def reject(self):
        if not self.page.saving:
            super().reject()

    def closeEvent(self, event):
        if self.page.saving:
            event.ignore()
        else:
            super().closeEvent(event)


class ActionPage(QWidget):
    VIEWS = (("快速新建", "quick-create", 9), ("每日计划", "today", 8),
             ("长期计划", "plans", 13), ("周期计划", "recurring", 8),
             ("周月复盘", "review", 9), ("项目计划", "project-plans", 3))

    def __init__(self, window, today_page=None):
        super().__init__()
        self.setObjectName("ActionPage")
        self.window = window
        self.service = ActionCenter(window.store)
        self.closed = self.loading = self.pending_refresh = self.saving = False
        self.plans, self.projects, self.templates, self.rows, self.all_rows, self.rules, self.reviews = [], [], [], [], [], {}, []
        self.view, self.plan_id, self.editor = "quick-create", None, None
        self.review_edit_record = None
        self.quick_edit_record = None
        self.return_after_quick_create = None
        self.period_rows = []
        self.project_records = []
        self.project_scope_root = ""
        self.day = date.today()
        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(20, 20, 20, 18)
        self.root_layout.setSpacing(16)
        self._build_navigation()
        self._build_main_content()
        self._build_drawer()
        self.navigation.setCurrentRow(0)
        self.navigation.currentItemChanged.connect(self.change_view)
        self.window.task_completed.connect(self.task_finished)
        QTimer.singleShot(0, self.refresh)


    def _build_navigation(self):
        side = QWidget()
        side.setObjectName("ActionSidebar")
        side.setFixedWidth(190)
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("计划与待办")
        heading.setObjectName("ActionSidebarTitle")
        sidebar.addWidget(heading)
        sidebar_hint = QLabel("个人计划空间")
        sidebar_hint.setObjectName("Subtitle")
        sidebar.addWidget(sidebar_hint)
        self.navigation = QListWidget()
        self.navigation.setObjectName("ActionNavigation")
        self.navigation.setSpacing(3)
        for title, key, icon in self.VIEWS:
            item = QListWidgetItem(nav_icon(icon, "#7890b1"), title)
            item.setData(Qt.ItemDataRole.UserRole, (key, None))
            self.navigation.addItem(item)
        sidebar.addWidget(self.navigation, 1)
        note = QLabel("所有入口编辑同一份计划来源")
        note.setWordWrap(True)
        note.setObjectName("Subtitle")
        sidebar.addWidget(note)
        self.root_layout.addWidget(side)

    def _build_main_content(self):
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.main = QWidget()
        main = QVBoxLayout(self.main)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)
        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.title = QLabel("今天")
        self.title.setWordWrap(True)
        self.title.setObjectName("ActionPageTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Subtitle")
        self.subtitle.setWordWrap(True)
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        top.addLayout(title_box, 1)
        self.draft_status = QLabel("● 准备就绪")
        self.draft_status.setObjectName("PlanDraftStatus")
        self.draft_status.setFixedHeight(34)
        top.addWidget(self.draft_status)
        self.plan_edit = button("编辑计划", self.edit_current_plan)
        self.plan_edit.hide()
        top.addWidget(self.plan_edit)
        self.day_previous = button("‹", lambda: self.set_day(self.day - timedelta(days=1)))
        self.day_picker = QDateEdit(QDate.currentDate())
        self.day_picker.setCalendarPopup(True)
        self.day_picker.setDisplayFormat("yyyy-MM-dd")
        self.day_picker.setToolTip("查看或编辑其他日期的计划")
        self.day_picker.dateChanged.connect(lambda value: self.set_day(value.toPython()))
        self.day_next = button("›", lambda: self.set_day(self.day + timedelta(days=1)))
        self.day_today = button("今天", lambda: self.set_day(date.today()))
        self.date_bar = QWidget()
        dates = QHBoxLayout(self.date_bar)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.addWidget(QLabel("查看日期"))
        for control in (self.day_previous, self.day_picker, self.day_next, self.day_today):
            control.hide()
            dates.addWidget(control)
        dates.addStretch(1)
        self.add_button = button("＋ 新建", self.add_for_current_view, True)
        top.addWidget(self.add_button)
        self.longterm_create_button = button(
            "＋ 新建长期计划", lambda _checked=False: self.open_document_dialog("longterm"))
        self.longterm_create_button.setObjectName("LongtermPlanCreateButton")
        self.recurring_create_button = button(
            "＋ 新建周期计划", lambda _checked=False: self.open_document_dialog("recurring"))
        self.recurring_create_button.setObjectName("RecurringPlanCreateButton")
        top.addWidget(self.longterm_create_button)
        top.addWidget(self.recurring_create_button)
        self.add_month_review_button = button("＋ 新建月复盘", lambda: self.open_document_dialog("monthly-review"))
        self.add_month_review_button.hide()
        top.addWidget(self.add_month_review_button)
        top.addWidget(button("刷新", self.refresh))
        main.addLayout(top)
        main.addWidget(self.date_bar)
        self.quick_create = MarkdownQuickCreate(self)
        main.addWidget(self.quick_create, 1)
        self.quick_bar = QWidget()
        quick = QHBoxLayout(self.quick_bar)
        quick.setContentsMargins(0, 0, 0, 0)
        self.quick_input = QLineEdit()
        self.quick_input.setPlaceholderText("添加今天要做的事…")
        self.quick_input.returnPressed.connect(self.quick_add)
        quick.addWidget(self.quick_input, 1)
        self.quick_button = button("＋ 添加", self.quick_add, True)
        quick.addWidget(self.quick_button)
        main.addWidget(self.quick_bar)
        self.filter_bar = QWidget()
        filters = QHBoxLayout(self.filter_bar)
        filters.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索事项、计划或项目编号…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh)
        filters.addWidget(self.search, 1)
        self.project_filter = QComboBox()
        self.project_filter.setObjectName("PlanProjectFilter")
        self.project_filter.setMinimumWidth(190)
        self.project_filter.addItem("全部进行中项目", "")
        self.project_filter.currentIndexChanged.connect(lambda _: self.render())
        filters.addWidget(self.project_filter)
        self.status = QComboBox()
        for title, value in (("全部状态", "all"), ("未完成", "open"), ("已完成", "done")):
            self.status.addItem(title, value)
        self.status.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status)
        main.addWidget(self.filter_bar)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("Subtitle")
        main.addWidget(self.notice)

        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setObjectName("ActionContentScroll")
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 4)
        self.content_layout.setSpacing(12)
        self.module_summary = ModuleSummary()
        self.content_layout.addWidget(self.module_summary)
        self.review_overview = ReviewOverview(self)
        self.content_layout.addWidget(self.review_overview)
        self.list = QListWidget()
        self.list.setObjectName("ActionList")
        self.list.setSpacing(8)
        self.list.setWordWrap(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemChanged.connect(self.toggle_item)
        self.list.itemClicked.connect(self.open_item)
        self.list.itemActivated.connect(self.open_item)
        self.content_layout.addWidget(self.list)
        self.project_records_section = QWidget()
        project_records_layout = QVBoxLayout(self.project_records_section)
        project_records_layout.setContentsMargins(0, 0, 0, 0)
        self.project_records_title = QLabel("已有项目安排")
        self.project_records_title.setObjectName("PlanSectionHeading")
        project_records_layout.addWidget(self.project_records_title)
        self.project_records_list = QListWidget()
        self.project_records_list.setObjectName("ActionList")
        self.project_records_list.setSpacing(8)
        self.project_records_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.project_records_list.itemActivated.connect(lambda item: self.open_project_record(item.data(Qt.ItemDataRole.UserRole)))
        project_records_layout.addWidget(self.project_records_list)
        self.content_layout.addWidget(self.project_records_section)
        self.empty_card = QFrame()
        self.empty_card.setObjectName("PlanEmptyCard")
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(24, 30, 24, 30)
        empty_layout.setSpacing(16)
        self.empty = QLabel()
        self.empty.setObjectName("PlanEmptyText")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        empty_layout.addWidget(self.empty)
        self.empty_create = button("新建计划", self.empty_primary, True)
        empty_layout.addWidget(self.empty_create, alignment=Qt.AlignmentFlag.AlignCenter)
        self.content_layout.addWidget(self.empty_card)
        self.today_plans_section, self.today_plans_grid = self.make_plan_section(
            "今日关联计划", "与今天行动相关的计划", "today")
        self.project_plans_section, self.project_plans_grid = self.make_plan_section(
            "项目计划", "直接关联项目的计划，在这里统一管理", "projects")
        self.content_layout.addWidget(self.today_plans_section)
        self.content_layout.addWidget(self.project_plans_section)
        self.content_layout.addStretch(1)
        self.content_scroll.setWidget(content)
        main.addWidget(self.content_scroll, 1)

    def _build_drawer(self):
        self.drawer = QWidget()
        self.drawer.setObjectName("ActionDrawer")
        self.drawer_layout = QVBoxLayout(self.drawer)
        self.drawer_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer.setMinimumWidth(310)
        self.drawer.setMaximumWidth(390)
        self.split.addWidget(self.main)
        self.split.addWidget(self.drawer)
        self.drawer.hide()
        self.split.setStretchFactor(0, 1)
        self.root_layout.addWidget(self.split, 1)

    def make_plan_section(self, title, subtitle, kind):
        section = QFrame()
        section.setObjectName("ActionPlanSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("ActionSectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        if kind == "projects":
            header.addWidget(button("＋ 新建计划", self.create_plan))
        layout.addLayout(header)
        description = QLabel(subtitle)
        description.setObjectName("Subtitle")
        layout.addWidget(description)
        hint = QLabel("还没有关联项目的计划，可用右上角“新建计划”创建并选择项目。") if kind == "projects" else QLabel()
        hint.setObjectName("ActionPlanEmpty")
        hint.setWordWrap(True)
        hint.hide()
        layout.addWidget(hint)
        section.empty_hint = hint
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)
        return section, grid

    def clear_plan_grid(self, grid):
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def add_plan_cards(self, grid, plans, project_names):
        self.clear_plan_grid(grid)
        for index, plan in enumerate(plans):
            grid.addWidget(self.plan_card(plan, project_names), index // 2, index % 2)

    def plan_card(self, plan, project_names):
        card = QFrame()
        card.setObjectName("ActionPlanCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        top = QHBoxLayout()
        title = QPushButton(plan["title"])
        title.setObjectName("ActionPlanTitleButton")
        title.setToolTip("打开计划及其关联行动")
        title.clicked.connect(lambda _checked=False, identity=plan["id"]: self.open_plan_editor(identity))
        top.addWidget(title, 1)
        menu_button = QToolButton()
        menu_button.setText("⋯")
        menu_button.setToolTip("编辑计划")
        menu_button.clicked.connect(lambda _checked=False, identity=plan["id"]: self.open_plan_editor(identity))
        top.addWidget(menu_button)
        layout.addLayout(top)
        project_id = plan.get("project_id") or ""
        project = project_names.get(project_id, project_id)
        tag = QLabel(("项目：" + project) if project else "个人计划")
        tag.setObjectName("ActionProjectTag")
        layout.addWidget(tag)
        linked = [row for row in self.all_rows if row.get("goal_id") == plan["id"]]
        done = sum(bool(row["done"]) for row in linked)
        progress = QProgressBar()
        progress.setObjectName("ActionPlanProgress")
        progress.setRange(0, 100)
        progress.setValue(round(done * 100 / len(linked)) if linked else 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(7)
        layout.addWidget(progress)
        footer = QHBoxLayout()
        percentage = QLabel(f"{done}/{len(linked)} 项 · {done * 100 // len(linked)}%" if linked else "暂无关联行动")
        percentage.setObjectName("Subtitle")
        footer.addWidget(percentage)
        footer.addStretch(1)
        status = {"done": "已完成", "archived": "已归档"}.get(plan["status"], "推进中")
        due = "无期限" if not plan.get("due_date") else "截止 " + plan["due_date"]
        footer.addWidget(QLabel(due + " · " + status))
        layout.addLayout(footer)
        return card

    def open_plan_editor(self, identity):
        record = next((plan for plan in self.service.plans() if plan["id"] == identity), None)
        if record:
            self.open_editor(record=record, plan=True)

    def schedule_plan(self, record):
        """Create a dated action linked to the existing plan, retaining its project."""
        if not self.close_drawer():
            return
        self.open_document_dialog("daily", defaults={"title": record["title"], "notes": record.get("notes", ""),
            "goal_id": record["id"], "project_id": record.get("project_id") or ""})

    def open_legacy_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(work_root(self.window.store) / "计划")))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange_drawer()

    def arrange_drawer(self):
        if not hasattr(self, "drawer"):
            return
        self.main.setVisible(not self.drawer.isVisible() or self.width() >= 1080)
        self.drawer.setMaximumWidth(390 if self.width() >= 1080 else 16777215)

    def confirm_close(self, *, include_quick=False):
        if self.saving:
            return False
        if (include_quick and self.quick_create.saved_draft_snapshot != self.quick_create.snapshot()
                and not self.quick_create.confirm_discard()):
            return False
        if self.editor and self.editor.dirty():
            return QMessageBox.question(self, "保留未保存修改", "当前修改尚未保存。放弃修改并离开？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard
        return True

    def close_drawer(self, force=False):
        if not force and not self.confirm_close():
            return False
        if self.editor:
            self.drawer_layout.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
        self.drawer.hide()
        self.arrange_drawer()
        return True

    def change_view(self, item, previous=None):
        if not item:
            return
        if not self.close_drawer():
            self.navigation.blockSignals(True)
            self.navigation.setCurrentItem(previous)
            self.navigation.blockSignals(False)
            return
        self.view, self.plan_id = item.data(Qt.ItemDataRole.UserRole)
        self.refresh()

    def default_day(self):
        return self.day if self.view == "today" else self.day + timedelta(days=1) if self.view == "upcoming" else None

    def set_day(self, value):
        if value == self.day:
            return
        self.day = value
        self.refresh()

    def refresh(self, *_):
        if self.closed:
            return
        if self.loading:
            self.pending_refresh = True
            return
        self.loading = True
        view, plan_id, query, status, day = self.view, self.plan_id, self.search.text(), self.status.currentData(), self.day
        def load(context):
            context.checkpoint()
            document_report = self.service.documents.migrate_legacy_documents(context)
            projects = []
            configured = self.window.service.project_workspace_root()
            project_error = ""
            if configured:
                try:
                    projects = ProjectWorkspace(Path(configured)).read_snapshot().list_projects(context)["projects"]
                except (OSError, ValueError, KeyError, UserError) as exc:
                    project_error = "项目列表暂不可用：" + str(exc)
            action_view = "today" if view == "today" else "all"
            rows = self.service.actions("all" if plan_id or view in {"quick-create", "plans", "project-plans", "recurring", "review"} else action_view,
                    day=day, plan_id=plan_id, query=query if view in {"today", "recurring"} else "",
                    status=status if view not in {"plans", "project-plans"} else "all")
            all_rows = self.service.actions("all", day=day) if plan_id or view in {"today", "plans", "project-plans", "quick-create", "recurring"} else []
            (week_start, week_end, _), (month_start, month_end, _) = self.service.review_periods(day)
            period_rows = self.service.period_actions(min(week_start, month_start), max(week_end, month_end)) if view == "review" else []
            scope_root = str(Path(configured).expanduser().resolve()) if configured else ""
            project_records = self.service.project_schedule(scope_root,
                {item["id"] for item in projects if item.get("state") == "active"}, day=day) if view == "project-plans" else []
            return {"plans": self.service.plans(), "rows": rows, "all_rows": all_rows, "projects": projects,
                    "project_records": project_records, "scope_root": scope_root,
                    "period_rows": period_rows,
                    "templates": self.service.templates(), "rules": self.service.rules(),
                    "reviews": self.service.reviews(),
                    "today_count": len(self.service.actions("today", day=day, status="open")), "project_error": project_error,
                    "document_errors": document_report["errors"],
                    "document_migrated": document_report["updated"]}
        def loaded(report):
            self.loading = False
            self.plans, self.projects, self.templates, self.rules, self.reviews = (
                report["plans"], report["projects"], report["templates"], report["rules"], report["reviews"])
            self.update_navigation(report["today_count"])
            if (view, plan_id, query, status, day) == (self.view, self.plan_id, self.search.text(), self.status.currentData(), self.day):
                self.rows, self.all_rows = report["rows"], report["all_rows"]
                self.period_rows = report["period_rows"]
                selected_project = self.project_filter.currentData() if self.project_scope_root == report["scope_root"] else ""
                self.project_scope_root = report["scope_root"]
                self.project_records = report["project_records"]
                self.project_filter.blockSignals(True)
                self.project_filter.clear()
                self.project_filter.addItem("全部进行中项目", "")
                for project in self.projects:
                    if project.get("state") == "active":
                        self.project_filter.addItem(project["name"] + " · " + project["id"], project["id"])
                self.project_filter.setCurrentIndex(max(0, self.project_filter.findData(selected_project)))
                self.project_filter.blockSignals(False)
                self.quick_create.load_options()
                self.render()
                if report["project_error"]:
                    self.notice.setText(report["project_error"])
                    self.notice.show()
                elif report["document_errors"]:
                    self.notice.setText("旧计划文档未能全部迁移，文件原件保留：" + "；".join(report["document_errors"][:2]))
                    self.notice.show()
                elif report["document_migrated"]:
                    self.notice.setText(f"已将 {report['document_migrated']} 份旧计划文档的最新内容迁入 SQLite；原文件保留。")
                    self.notice.show()
        self.read_id = self.window.submit(None, "读取个人行动", load, loaded, persist_result=False, record_history=False)
        if not self.read_id:
            self.loading = False

    def task_finished(self, identity, state):
        if identity == getattr(self, "read_id", None):
            self.loading = False
            if state != "success":
                self.notice.setText("读取未完成，请刷新重试。")
                self.notice.show()
            if self.pending_refresh:
                self.pending_refresh = False
                self.refresh()

    def update_navigation(self, count):
        self.navigation.blockSignals(True)
        self.navigation.item(1).setText(f"每日计划    {count}" if count else "每日计划")
        current = (self.view, self.plan_id)
        for index in range(self.navigation.count()):
            if self.navigation.item(index).data(Qt.ItemDataRole.UserRole) == (current[0], None):
                self.navigation.setCurrentRow(index)
                break
        self.navigation.blockSignals(False)

    def render(self):
        names = dict((item["id"], item["name"]) for item in self.projects)
        plan = next((item for item in self.plans if item["id"] == self.plan_id), None)
        title = plan["title"] if plan else dict((key, title) for title, key, _icon in self.VIEWS).get(self.view, "计划与待办")
        self.title.setText(title)
        self.plan_edit.setVisible(bool(plan))
        self.date_bar.setVisible(self.view in {"today", "recurring", "review"})
        is_daily = self.view == "today"
        for control in (self.day_previous, self.day_picker, self.day_next, self.day_today):
            control.setVisible(self.view in {"today", "recurring", "review"})
        self.day_picker.blockSignals(True)
        self.day_picker.setDate(QDate(self.day.year, self.day.month, self.day.day))
        self.day_picker.blockSignals(False)
        quick_mode = self.view == "quick-create"
        parallel_plan_entries = self.view in {"plans", "recurring"}
        self.draft_status.setVisible(quick_mode)
        add_titles = {
            "today": "＋ 新建每日计划", "plans": "＋ 新建长期计划",
            "recurring": "＋ 新建周期计划", "review": "＋ 新建周复盘",
            "project-plans": "＋ 新建项目计划",
        }
        self.add_button.setText(add_titles.get(self.view, "＋ 新建"))
        self.add_button.setVisible(not quick_mode and not parallel_plan_entries)
        self.longterm_create_button.setVisible(parallel_plan_entries)
        self.recurring_create_button.setVisible(parallel_plan_entries)
        self.add_month_review_button.setVisible(self.view == "review")
        self.quick_create.setVisible(quick_mode)
        self.content_scroll.setVisible(not quick_mode)
        self.quick_bar.setVisible(self.view == "today")
        self.filter_bar.setVisible(self.view in {"today", "plans", "project-plans", "recurring"})
        self.status.setVisible(self.view in {"today", "recurring"})
        self.notice.setVisible(self.view == "today" and any(row["overdue"] for row in self.rows))
        self.search.setVisible(self.view in {"today", "plans", "project-plans", "recurring"})
        self.project_filter.setVisible(self.view == "project-plans")
        done = sum(row["done"] for row in self.rows)
        self.subtitle.setText(("选一个类型，从模板开始；正文可以自由写。" if quick_mode else
                               f"{self.day:%m 月 %d 日} · 周{'一二三四五六日'[self.day.weekday()]} · {done}/{len(self.rows)} 完成" if self.view == "today" else
                               "尚未安排日期，想清楚后再放进日程" if self.view == "inbox" else
                               self.review_subtitle() if self.view == "review" else
                               "没有日期也可以先保存，确定后再补充安排" if self.view == "plans" else
                               "按周期规则每日出现，完成后记录当日打卡" if self.view == "recurring" else
                               "选择项目，查看关联计划与手动任务" if self.view == "project-plans" else
                               f"{done}/{len(self.rows)} 项行动完成"))
        self.quick_input.setPlaceholderText(
            "添加今天要做的事…" if self.view == "today" and self.day == date.today() else
            f"添加 {self.day:%m-%d} 的计划事项…" if self.view == "today" else "添加事项，回车保存…")
        reminder = self.review_reminder_text() if self.view == "review" else ""
        self.notice.setText(reminder or ("含此前未完成的行动" if any(row["overdue"] for row in self.rows) and self.view == "today" else ""))
        self.notice.setVisible(bool(self.notice.text()) and self.view in {"today", "review"})
        self.list.blockSignals(True)
        self.list.clear()
        displayed_plans = []
        selected_project = self.project_filter.currentData() or ""
        self.review_overview.setVisible(self.view == "review")
        self.module_summary.setVisible(not quick_mode and self.view != "review")
        if self.view == "review":
            self.review_overview.render()
        elif self.view in {"plans", "project-plans"}:
            active_project_ids = {item["id"] for item in self.projects if item.get("state") == "active"}
            for item in self.plans:
                if self.view == "project-plans" and (
                        not item.get("project_id") or item.get("project_id") not in active_project_ids or
                        (selected_project and item["project_id"] != selected_project) or
                        (item.get("project_root") and os.path.normcase(os.path.abspath(item["project_root"])) !=
                         os.path.normcase(os.path.abspath(self.project_scope_root)))):
                    continue
                if self.search.text().casefold() not in (item["title"] + item["notes"] +
                        (item.get("project_id") or "") + names.get(item.get("project_id"), "")).casefold():
                    continue
                linked = [row for row in self.rows if row["goal_id"] == item["id"]]
                progress = f"{sum(row['done'] for row in linked)}/{len(linked)} 项行动完成"
                state = {"done": "已完成", "archived": "已归档"}.get(item["status"], "推进中")
                project_name = names.get(item.get("project_id"), item.get("project_id", ""))
                details = [state, progress, item["due_date"] or "无期限"]
                if project_name:
                    details.insert(0, "项目：" + project_name)
                entry = QListWidgetItem(item["title"] + "\n" + " · ".join(details))
                entry.setData(Qt.ItemDataRole.UserRole, {"plan": item})
                self.list.addItem(entry)
                displayed_plans.append(item)
                install_plan_row(self, entry, item, names)
        else:
            section = None
            rows = [row for row in self.rows if row.get("repeat")] if self.view == "recurring" else self.rows
            for row in rows:
                group = "已完成" if row["done"] else "此前未完成" if row["overdue"] and self.view == "today" else "今日任务" if self.view == "today" else "周期打卡" if self.view == "recurring" else row["occurrence_day"] or "等待安排"
                if section != group:
                    section = group
                    header = QListWidgetItem(group)
                    header.setFlags(Qt.ItemFlag.NoItemFlags)
                    header.setSizeHint(QSize(0, 32))
                    self.list.addItem(header)
                details = [value for value in (row["plan_title"], names.get(row["project_id"], row["project_id"]),
                           "每天" if row["repeat"] == "daily" else "每周" if row["repeat"] == "weekly" else "每月" if row["repeat"] else "",
                           "原定 " + row["occurrence_day"] if row["overdue"] else "") if value]
                item = QListWidgetItem(row["title"] + ("\n" + " · ".join(details) if details else ""))
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setToolTip(row["title"] + "\n" + " · ".join(details))
                can_check = (not row["repeat"] or
                             (row["occurrence_day"] == self.day.isoformat() and self.day <= date.today()))
                if can_check:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if row["done"] else Qt.CheckState.Unchecked)
                else:
                    item.setText(item.text() + "\n本日不打卡 · 执行日 " + row["occurrence_day"])
                self.list.addItem(item)
                install_action_row(self, item, row, names, can_check)
        self.list.blockSignals(False)
        self.project_records_list.clear()
        displayed_records = []
        if self.view == "project-plans":
            query = self.search.text().casefold().strip()
            for row in self.project_records:
                if selected_project and row["project_id"] != selected_project:
                    continue
                if query not in (row["title"] + " " + row.get("notes", "") + " " + row["project_id"] + " " + names.get(row["project_id"], "")).casefold():
                    continue
                entry = QListWidgetItem(row["title"])
                entry.setData(Qt.ItemDataRole.UserRole, row)
                self.project_records_list.addItem(entry)
                install_project_schedule_row(self, entry, row, names)
                displayed_records.append(row)
        self.project_records_title.setText(f"关联行动 / 手动任务 · {len(displayed_records)}")
        self.project_records_section.setVisible(self.view == "project-plans" and bool(displayed_records))
        self.project_records_list.setFixedHeight(max(0, len(displayed_records) * 110 + 4))
        if quick_mode:
            self.quick_create.load_recent()
        empty = self.list.count() == 0
        is_today = self.view == "today"
        today_plan_ids = {row.get("goal_id") for row in self.rows if row.get("goal_id")}
        today_plans = [item for item in self.plans if item["status"] != "archived" and
                       (item["id"] in today_plan_ids or item.get("due_date") == self.day.isoformat())]
        project_plans = [item for item in self.plans if item.get("project_id") and item["status"] != "archived"]
        self.add_plan_cards(self.today_plans_grid, today_plans, names)
        self.add_plan_cards(self.project_plans_grid, project_plans, names)
        self.today_plans_section.setVisible(is_today and bool(today_plans))
        self.project_plans_section.setVisible(False)
        self.list.setVisible(not empty and not quick_mode and self.view != "review")
        self.empty_card.setVisible(empty and not displayed_records and not quick_mode and self.view != "review")
        self.project_plans_section.empty_hint.setVisible(is_today and not project_plans)
        row_height = sum(max(30, self.list.sizeHintForRow(index)) for index in range(self.list.count()))
        self.list.setFixedHeight(max(76, row_height + self.list.spacing() * self.list.count() * 2 + 4))
        filtered = bool(self.search.text().strip()) or (self.view in {"today", "recurring"} and self.status.currentData() != "all")
        self.empty.setText("没有匹配的计划\n试试其他关键词，或清空当前筛选。" if filtered else {
            "project-plans": ("这个项目还没有关联计划或安排\n点击新建，默认关联当前项目。" if selected_project else
                              "还没有进行中项目的计划或安排\n选择项目查看已有安排，或新建一个关联计划。"),
            "plans": "从一个长期目标开始\n暂时没有日期也可以保存；也可用上方按钮新建周期计划。",
            "recurring": "建立一个持续的习惯\n每次完成只记录当日打卡；也可用上方按钮新建长期计划。",
            "today": "这一天还没有安排\n写下最重要的事，或在上方快速添加一条行动。",
        }.get(self.view, "还没有计划"))
        self.empty_create.setText("清空筛选" if filtered else add_titles.get(self.view, "新建计划"))
        self.empty_create.setVisible(filtered or not parallel_plan_entries)
        if self.view == "project-plans":
            self.module_summary.set_values((("关联计划", len(displayed_plans)), ("手动任务", len(displayed_records)),
                ("已完成任务", sum(row["done"] for row in displayed_records))))
        elif self.view == "plans":
            self.module_summary.set_values((("当前计划", len(displayed_plans)),
                ("推进中", sum(item["status"] in {"planned", "active"} for item in displayed_plans)),
                ("已完成", sum(item["status"] == "done" for item in displayed_plans))))
        elif self.view == "recurring":
            series = [row for row in self.rows if row.get("repeat")]
            today = [row for row in series if row["occurrence_day"] == self.day.isoformat()]
            self.module_summary.set_values((("周期计划", len(series)), ("本日待打卡", sum(not row["done"] for row in today)),
                ("本日已打卡", sum(row["done"] for row in today))))
        else:
            self.module_summary.set_values((("所选日行动", len(self.rows)), ("已完成", done), ("待完成", len(self.rows) - done)))

    @staticmethod
    def timeline_label(day, overdue=False):
        try:
            value = date.fromisoformat(day)
        except (TypeError, ValueError):
            return day
        return f"{value:%m-%d} 周{'一二三四五六日'[value.weekday()]}" + (" · 逾期" if overdue else "")

    def review_range(self):
        (week_start, week_end, _), (month_start, month_end, _) = self.service.review_periods(self.day)
        return (week_start, week_end), (month_start, month_end)

    def review_subtitle(self):
        (week_start, week_end), (month_start, _month_end) = self.review_range()
        return f"本周 {week_start:%m-%d}~{week_end:%m-%d} · 本月 {month_start:%m 月} 的完成情况"

    def review_reminder_text(self):
        today = self.day
        weekly, monthly = self.review_targets()
        week_start, week_end, weekly_key = weekly
        month_start, month_end, monthly_key = monthly
        saved = {item["period_key"] for item in self.reviews}
        reminders = []
        if today == week_end and weekly_key not in saved:
            reminders.append("今天是本周最后一天，记得完成本周复盘。")
        if today == month_end and monthly_key not in saved:
            reminders.append("今天是本月最后一天，记得完成本月复盘。")
        if today > week_end and weekly_key not in saved:
            reminders.append("上周复盘还未完成。")
        if today > month_end and monthly_key not in saved:
            reminders.append("上月复盘还未完成。")
        return "  ·  ".join(reminders)

    def review_targets(self):
        return self.service.review_periods(self.day)

    def review_lines(self):
        weekly, monthly = self.review_targets()
        week_start, week_end, _ = weekly
        month_start, month_end, _ = monthly
        week = self.service.review_period_summary(week_start, week_end, self.period_rows, self.plans)
        month = self.service.review_period_summary(month_start, month_end, self.period_rows, self.plans)
        interval = self.service.review_period_summary(min(week_start, month_start), max(week_end, month_end),
                                                      self.period_rows, self.plans)
        overdue_count = sum(row["overdue"] for row in self.rows)
        lines = [
            f"本周（{week_start:%m-%d} ~ {week_end:%m-%d}）：完成 {week['done']} / {week['total']}"
            + (f"，完成率 {week['done'] * 100 // week['total']}%" if week["total"] else ""),
            f"本月（{month_start:%m 月}）：完成 {month['done']} / {month['total']}"
            + (f"，完成率 {month['done'] * 100 // month['total']}%" if month["total"] else ""),
            f"逾期未完成：{overdue_count} 项" + ("，建议重新安排日期" if overdue_count else ""),
        ]
        lines.append("本周与本月安排（周期计划按每次打卡统计）")
        for row in interval["rows"]:
            lines.append(f"{'✓' if row['done'] else '○'} {row['occurrence_day']} · {row['title']}")
        if interval["plans"]:
            lines.append("本周与本月关联计划")
            for plan in interval["plans"]:
                state = {"done": "已完成", "archived": "已归档"}.get(plan["status"], "推进中")
                lines.append(f"· {plan['title']} · {state} · {plan['due_date'] or '无期限'}")
        if interval["plan_counts"]:
            names = {item["id"]: item["title"] for item in self.plans}
            lines.extend(["", "按计划（所选周/月）"])
            lines.extend(f"· {names.get(identity, '原计划')}：{done}/{total}"
                         for identity, done, total in interval["plan_counts"])
        if interval["project_counts"]:
            names = {item["id"]: item["name"] for item in self.projects}
            lines.extend(["", "按项目（所选周/月）"])
            lines.extend(f"· {names.get(identity, identity)}：{done}/{total}"
                         for identity, done, total in interval["project_counts"])
        lines.extend(["", "统计按行动的计划日期归入周/月；没有日期的行动不计入。"])
        return lines

    def quick_add(self):
        title = self.quick_input.text().strip()
        if not title or self.saving:
            return
        target = self.default_day()
        values = {"title": title, "due_date": target.isoformat() if target else None, "goal_id": self.plan_id or ""}
        self.quick_input.setEnabled(False)
        self.quick_button.setEnabled(False)
        self.saving = True
        def saved(_):
            self.quick_input.clear()
            self.notify_saved()
        identity = self.window.submit(None, "快速添加行动", lambda context: self.service.save_action(values), saved, persist_result=False)
        self.release_after(identity, lambda: (self.quick_input.setEnabled(True), self.quick_button.setEnabled(True), self.quick_input.setFocus()))

    def add_for_current_view(self):
        kind = {"today": "daily", "plans": "longterm", "recurring": "recurring",
                "review": "weekly-review", "project-plans": "project"}.get(self.view)
        if kind:
            self.open_document_dialog(kind)

    def empty_primary(self):
        if self.search.text().strip() or (self.view in {"today", "recurring"} and self.status.currentData() != "all"):
            self.search.clear()
            self.status.setCurrentIndex(0)
            self.refresh()
        else:
            self.add_for_current_view()

    def open_document_dialog(self, kind, *, record=None, defaults=None):
        if not self.close_drawer():
            return
        if kind in {"weekly-review", "monthly-review"} and record is None:
            weekly, monthly = self.review_targets()
            key = (weekly if kind == "weekly-review" else monthly)[2]
            record = next((row for row in self.reviews if row["period_key"] == key), None)
        if kind == "project" and defaults is None and self.view == "project-plans":
            defaults = {"project_id": self.project_filter.currentData() or ""}
        dialog = PlanDocumentDialog(self, kind, QUICK_TEMPLATES[kind][0], record=record, defaults=defaults)
        self.document_dialog = dialog
        try:
            dialog.exec()
        finally:
            self.document_dialog = None
            dialog.deleteLater()

    def edit_project_task_status(self, row):
        if self.saving:
            return
        dialog = ProjectTaskStatusDialog(self, row)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def change_project_task_status(self, row, combo, *, on_saved=None, on_released=None):
        status = combo.currentData()
        if self.saving:
            combo.blockSignals(True)
            previous = "done" if row["done"] else "planned" if row.get("repeat") else row["status"]
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)
            return
        self.saving = True
        combo.setEnabled(False)
        def operation(context):
            context.checkpoint()
            return self.service.set_project_task_status(row["id"], status, expected=row, day=row.get("occurrence_day"))
        def saved(_result):
            if on_saved:
                on_saved()
            self.notify_saved()
        identity = self.window.submit(None, "修改手动任务状态", operation, saved, persist_result=False)
        def released():
            try:
                combo.setEnabled(True)
            except RuntimeError:
                pass  # A successful refresh may already have rebuilt the row.
            if on_released:
                on_released()
            self.refresh()  # Also restore the persisted value after a rejected write.
        self.release_after(identity, released)

    def open_project_record(self, row):
        if not row:
            return
        if row["personal"]:
            stored = self.window.service.planned_task(row["id"])
            if stored:
                self.open_editor(record=stored)
            return
        from .library import TextReportDialog
        names = {project["id"]: project["name"] for project in self.projects}
        report = {"项目": names.get(row["project_id"], row["project_id"]), "名称": row["title"],
            "来源": "AI 规划" if row["source"] == "ai" else "项目任务文档",
            "状态": "已完成" if row["done"] else "进行中" if row["status"] == "active" else "待完成",
            "日期": row.get("due_date") or "未设置", "备注": row.get("notes", ""),
            "说明": ("与项目管理共用原记录；AI 执行状态继续在项目管理维护。" if row["source"] == "ai" else
                     "与项目管理共用原记录；可在项目计划列表直接修改手动任务状态。")}
        dialog = TextReportDialog(self.window, "项目安排", {"note": report.pop("说明"),
            "text": "\n\n".join(key + "：" + str(value) for key, value in report.items())})
        dialog.exec()
        dialog.deleteLater()

    def begin_quick_create(self, kind):
        if not self.quick_create.confirm_discard():
            return False
        if not self.close_drawer():
            return False
        self.return_after_quick_create = self.view if self.view != "quick-create" else None
        self.review_edit_record = None
        self.quick_edit_record = None
        self.select_view("quick-create")
        self.quick_create.title_input.clear()
        self.quick_create.kind.setEnabled(True)
        self.quick_create.delete_button.hide()
        self.quick_create.kind.setCurrentIndex(max(0, self.quick_create.kind.findData(kind)))
        self.quick_create.apply_template()
        self.quick_create.title_input.clear()
        self.quick_create.date.setDate(QDate(self.day.year, self.day.month, self.day.day))
        self.quick_create.date_enabled.setChecked(kind in {"daily", "recurring"})
        self.quick_create.project.setCurrentIndex(0)
        self.quick_create.plan.setCurrentIndex(0)
        self.quick_create.frequency.setCurrentIndex(0)
        self.quick_create.repeat_options.reset(self.quick_create.date.date())
        self.quick_create.mark_clean()
        self.quick_create.body.setFocus()
        return True

    def release_after(self, identity, callback):
        if not identity:
            self.saving = False
            callback()
            return
        def finished(task_id, _state):
            if task_id == identity:
                self.saving = False
                callback()
                self.window.task_completed.disconnect(finished)
        self.window.task_completed.connect(finished)

    def request_delete(self, entity_type, record, *, on_deleted=None):
        if self.saving:
            return
        extra = ("已安排的每日行动会保留并解除关联。" if entity_type == "plan" else
                 "整个重复系列及打卡记录会一起删除。" if record["id"] in self.rules else
                 "这条记录会从 SQLite 中删除。")
        if QMessageBox.question(self, "删除计划", f"删除“{record['title']}”？\n\n{extra}\n旧 Markdown 原件不会被修改或删除。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.saving = True
        def operation(context):
            context.checkpoint()
            return self.service.delete_record(entity_type, record["id"], expected=record)
        def deleted(result):
            if self.editor and self.editor.record.get("id") == record["id"]:
                self.close_drawer(force=True)
            quick = self.quick_edit_record[1] if self.quick_edit_record else self.review_edit_record
            if quick and quick.get("id") == record["id"]:
                self.quick_edit_record = self.review_edit_record = None
                self.quick_create.kind.setEnabled(True)
                self.quick_create.title_input.clear()
                self.quick_create.apply_template()
                self.quick_create.delete_button.hide()
                self.quick_create.mark_clean()
            if on_deleted:
                on_deleted()
            if result.get("warning"):
                QMessageBox.warning(self, "删除已完成", result["warning"])
            self.notify_saved()
        identity = self.window.submit(None, "删除计划", operation, deleted, persist_result=False)
        self.release_after(identity, lambda: None)

    def notify_saved(self):
        self.refresh()
        self.window.today_page.refresh()
        self.window.project_page.refresh()

    def toggle_item(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if not row or "plan" in row or "review" in row or not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        done = item.checkState() == Qt.CheckState.Checked
        if done == row["done"]:
            return
        self.list.setEnabled(False)
        identity = self.window.submit(None, "更新行动完成状态", lambda context: self.service.complete(row["id"], done, row["occurrence_day"]),
                                      lambda _result: self.notify_saved(), persist_result=False)
        self.release_after(identity, lambda: (self.list.setEnabled(True), self.refresh()))

    def open_item(self, item):
        record = item.data(Qt.ItemDataRole.UserRole)
        if not record:
            return
        if "done" in record and item.flags() & Qt.ItemFlag.ItemIsUserCheckable and (item.checkState() == Qt.CheckState.Checked) != record["done"]:
            return  # Clicking a checkbox completes the action without opening its details.
        if "plan" in record:
            self.open_plan_editor(record["plan"]["id"])
            return
        if "review" in record:
            review = record["review"]
            self.open_document_dialog("weekly-review" if review["period_type"] == "weekly" else "monthly-review", record=review)
            return
        stored = self.window.service.planned_task(record["id"])
        if stored:
            self.open_editor(record=stored)
        else:
            self.refresh()

    def start_quick(self):
        """Focus the Markdown title after switching to the peer quick-create page."""
        self.select_view("quick-create")
        self.quick_create.body.setFocus()

    def select_view(self, view, plan_id=None):
        if view == "plan" and plan_id:
            self.open_plan_editor(plan_id)
            return
        for index in range(self.navigation.count()):
            item = self.navigation.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == (view, plan_id):
                self.navigation.setCurrentRow(index)
                return
        if self.close_drawer():
            self.view, self.plan_id = view, plan_id
            for index in range(self.navigation.count()):
                data = self.navigation.item(index).data(Qt.ItemDataRole.UserRole)
                if data == (view, None):
                    self.navigation.blockSignals(True)
                    self.navigation.setCurrentRow(index)
                    self.navigation.blockSignals(False)
                    break
            self.refresh()

    def open_recent_document(self, item):
        record = item.data(Qt.ItemDataRole.UserRole)
        if record:
            self.quick_create.load_document(*record)

    def open_recent_in_obsidian(self, item):
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value:
            return
        kind, record = value
        entity_type = "review" if kind == "review" else "plan" if kind in {"longterm", "project"} else "action"
        path = self.service.documents.path(entity_type, record["id"])
        if path and Path(path).is_file():
            self.window.today_page.open_obsidian(Path(path))
        else:
            self.open_recent_document(item)

    def save_quick_document(self):
        if self.saving:
            return
        composer = self.quick_create
        kind = composer.kind.currentData()
        title = composer.title_input.text().strip()
        if not title:
            lines = composer.body.toPlainText().splitlines()
            title = next((line.lstrip("#").strip() for line in lines if line.startswith("# ")),
                         next((line.strip() for line in lines if line.strip()), ""))[:160]
        if not title:
            composer.route_hint.setText("请先填写计划标题。")
            composer.body.setFocus()
            return
        body = composer.body.toPlainText()
        selected_date = composer.date.date().toPython()
        due_date = selected_date.isoformat() if composer.date_enabled.isChecked() else None
        project_id = composer.project.currentData() or ""
        edit = self.quick_edit_record
        edit_kind, record = edit if edit else (None, None)
        expected_edit_kind = {"daily": "daily", "recurring": "recurring",
                              "longterm": "longterm", "project": "project"}.get(kind)
        if edit and edit_kind != expected_edit_kind:
            edit_kind, record = None, None
            self.quick_edit_record = None
        project_root = ((record.get("project_root") if record and project_id == record.get("project_id") else
                         self.window.service.project_workspace_root()) if project_id else "")
        values = {"title": title, "notes": body, "due_date": due_date,
                  "project_id": project_id, "project_root": project_root,
                  "goal_id": composer.plan.currentData() or ""}
        entity_type = "review" if kind in {"weekly-review", "monthly-review"} else "plan" if kind in {"longterm", "project"} else "action"
        if kind == "daily":
            values["due_date"] = due_date or self.day.isoformat()
            if record:
                values.update(priority=record.get("priority", ""), status=record.get("status", "planned"))
                values["_expected"] = record
                values["_expected_rule"] = self.rules.get(record["id"], {})
            operation = lambda context: self.save_plan_record(
                context,
                lambda: self.service.save_action(values, record.get("id") if record else None))
            summary = "保存每日计划"
        elif kind == "recurring":
            if not due_date:
                composer.route_hint.setText("周期计划需要设置开始日期。")
                return
            if composer.frequency.currentData() == "weekly" and not composer.repeat_options.values()["repeat_days"]:
                composer.route_hint.setText("请至少选择一个重复星期。")
                return
            rule = self.rules.get(record["id"], {}) if record else {}
            values.update(due_date=due_date, repeat=composer.frequency.currentData(),
                          repeat_end=rule.get("end_date"),
                          **composer.repeat_options.values())
            if record:
                values.update(priority=record.get("priority", ""), status=record.get("status", "planned"))
                values["_expected"] = record
                values["_expected_rule"] = self.rules.get(record["id"], {})
            operation = lambda context: self.save_plan_record(
                context,
                lambda: self.service.save_action(values, record.get("id") if record else None))
            summary = "保存周期计划"
        elif kind in {"longterm", "project"}:
            if kind == "project" and not project_id:
                composer.route_hint.setText("项目计划需要选择关联项目。")
                composer.project.setFocus()
                return
            values.update(status=record.get("status", "planned") if record else "planned",
                          project_root=project_root)
            if record:
                values["_expected"] = record
            operation = lambda context: self.save_plan_record(
                context,
                lambda: self.service.save_plan(values, record.get("id") if record else None))
            summary = "保存项目计划" if kind == "project" else "保存长期计划"
        else:
            today = self.day
            period_type = "weekly" if kind == "weekly-review" else "monthly"
            weekly, monthly = self.review_targets()
            start, end, period_key = weekly if period_type == "weekly" else monthly
            expected = self.review_edit_record
            if expected and expected["period_type"] != period_type:
                expected = None
            if expected:
                period_key = expected["period_key"]
                period_type = expected["period_type"]
                start = date.fromisoformat(expected["start_date"])
                end = date.fromisoformat(expected["end_date"])
            review_values = {"period_type": period_type, "period_key": period_key, "title": title,
                             "start_date": start.isoformat(), "end_date": end.isoformat(), "body": body,
                             **({"_expected": expected} if expected else {})}
            operation = lambda context: self.save_plan_record(
                context,
                lambda: self.service.save_review(review_values, expected.get("id") if expected else None))
            summary = "保存周月复盘"

        composer.setEnabled(False)
        self.saving = True
        def saved(_result):
            self.saving = False  # Navigation can proceed once persistence has succeeded.
            self.quick_edit_record = None
            self.review_edit_record = None
            composer.kind.setEnabled(True)
            composer.delete_button.hide()
            composer.title_input.clear()
            composer.apply_template()
            composer.mark_clean()
            composer.route_hint.setText("计划、行动与复盘已保存到 SQLite。")
            self.notify_saved()
            return_view = self.return_after_quick_create
            self.return_after_quick_create = None
            self.select_view(return_view or {"daily": "today", "longterm": "plans", "recurring": "recurring",
                "project": "project-plans", "weekly-review": "review", "monthly-review": "review"}[kind])
        identity = self.window.submit(None, summary, operation, saved, persist_result=False)
        self.release_after(identity, lambda: composer.setEnabled(True))

    def save_plan_record(self, context, operation):
        context.checkpoint()
        return operation()

    def edit_current_plan(self):
        record = next((plan for plan in self.plans if plan["id"] == self.plan_id), None)
        if record:
            self.open_editor(record=record, plan=True)

    def open_editor(self, record=None, plan=False):
        if not self.close_drawer():
            return
        editor = ItemEditor(self, record=record, plan=plan)
        self.editor = editor
        self.drawer_layout.addWidget(editor)
        editor.cancel.clicked.connect(self.close_drawer)
        editor.save.clicked.connect(lambda: self.save_editor(editor, on_saved=lambda _result: self.close_drawer(force=True)))
        self.drawer.show()
        self.arrange_drawer()

    def save_editor(self, editor, on_saved):
        if self.saving:
            return
        values = editor.values()
        identity = editor.record.get("id")
        editor.setEnabled(False)
        editor.error.hide()
        self.saving = True
        def operation(context):
            context.checkpoint()
            return self.service.save_plan(values, identity) if editor.is_plan else self.service.save_action(values, identity)
        def saved(result):
            on_saved(result)
            self.notify_saved()
        task_id = self.window.submit(None, "保存个人计划" if editor.is_plan else "保存个人行动", operation, saved, persist_result=False)
        def released():
            # A successful drawer save schedules deletion, whereas failed saves keep the draft.
            try:
                if not self.closed:
                    editor.setEnabled(True)
            except RuntimeError:
                return
        self.release_after(task_id, released)

    def new_dialog(self, *, plan=False, project_id="", day=None):
        if not self.close_drawer():
            return
        dialog = ItemDialog(self)
        dialog.setObjectName("ActionDialog")
        dialog.setWindowTitle("新建计划" if plan else "新建行动")
        dialog.resize(480, 520)
        dialog.setMinimumSize(400, 400)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        editor = ItemEditor(self, plan=plan, day=day, project_id=project_id, plan_id=self.plan_id or "")
        layout.addWidget(editor)
        editor.cancel.clicked.connect(dialog.reject)
        def accepted(result):
            dialog.accept()
            if plan:
                self.view, self.plan_id = "plan", result["id"]
        editor.save.clicked.connect(lambda: self.save_editor(editor, on_saved=accepted))
        dialog.exec()

    def create_action(self, _checked=False, *, project_id="", day=None):
        self.new_dialog(project_id=project_id, day=day if day is not None else self.default_day())

    def create_plan(self, _checked=False):
        self.open_document_dialog("project" if self.view in {"today", "project-plans"} else "longterm")

    def stop_updates(self):
        self.closed = True
        self.search_timer.stop()
        self.quick_create.draft_timer.stop()
