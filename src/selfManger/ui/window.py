from __future__ import annotations

import json
import os
import html
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl, QUrlQuery, QSize, Signal
from PySide6.QtGui import QDesktopServices, QTextOption, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMenu, QMessageBox, QPushButton, QProgressBar, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QScrollArea,
    QTabWidget, QListWidgetItem, QFormLayout, QSizePolicy, QComboBox, QApplication,
)

from .. import __version__
from ..application import ApplicationService, MANAGER_BACKUP_ID, OVERALL_BACKUP_ID
from ..restore_migration import restored_vault_root
from ..domain import KINDS, Resource, RestorePlan, UserError
from ..security import safe_result
from ..storage import Store
from .dialogs import ArchiveRestoreDialog, PasswordDialog, PlanDialog, ResourceDialog, TransferDialog
from .tasks import Worker, ReadWorker
from .presentation import ACTION_LABELS, readable_report, readable_time, readable_size
from .components import nav_icon, card, FlowLayout, ReadableTable
from ..storage import now

STATE_LABELS = {"running": "运行中", "success": "已完成", "failed": "失败", "cancelled": "已取消", "interrupted": "中断待检查"}


def button(text: str, callback, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setProperty("primary", primary)
    widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    widget.clicked.connect(callback)
    return widget


def table(headers: list[str]) -> QTableWidget:
    widget = ReadableTable(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(42)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setShowGrid(False)
    return widget


def text_view() -> QTextEdit:
    view = QTextEdit()
    view.setReadOnly(True)
    return view


class ResourcePage(QWidget):
    def __init__(self, window: MainWindow, title: str, subtitle: str, kinds: list[str]) -> None:
        super().__init__()
        self.setObjectName("ResourceManagementPage")
        self.window, self.kinds = window, kinds
        self.rows: list[Resource] = []
        self.compact = len(kinds) == 1
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("Title")
        self.page_heading = heading
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName("Subtitle")
        description.setWordWrap(True)
        self.page_description = description
        layout.addWidget(description)
        toolbar = QHBoxLayout()
        self.add = button("＋ 添加" + title, self.add_resource, True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称或目录")
        self.search.textChanged.connect(self.refresh)
        toolbar.addWidget(self.add)
        self.selector = QComboBox(self)
        self.selector.setMinimumContentsLength(16)
        self.selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.selector.currentIndexChanged.connect(self.select_instance)
        self.selector.setVisible(self.compact)
        toolbar.addWidget(self.selector if self.compact else self.search, 1)
        if self.compact:
            self.search.hide()
        self.edit_button = button("编辑登记", lambda: window.edit_resource(self.selected()))
        toolbar.addWidget(self.edit_button)
        self.toolbar_controls = [self.add, self.search, self.selector, self.edit_button]
        layout.addLayout(toolbar)
        self.observe_button = button("检查状态", lambda: self.dispatch("observe"))
        self.backup_button = button("立即备份", lambda: self.dispatch("backup"), True)
        self.verify_button = button("校验备份", lambda: self.dispatch("verify"))
        self.restore_button = button("恢复备份", lambda: self.dispatch("restore"))
        self.more_button = button("登记管理", self.more)
        self.page_action_widgets = [self.observe_button, self.backup_button, self.verify_button, self.restore_button]
        actions = FlowLayout()
        for widget in (self.observe_button, self.backup_button, self.verify_button, self.restore_button):
            actions.addWidget(widget)
        actions.addWidget(self.more_button)
        self.page_action_widgets.extend(actions.itemAt(i).widget() for i in range(actions.count()) if actions.itemAt(i).widget())
        layout.addLayout(actions)
        self.metrics = []
        self.metric_panels = []
        if self.compact:
            metrics = QHBoxLayout()
            for label in ("最近备份", "恢复检查", "运行 / 连接", "资料范围"):
                panel, value, note = card(label, "等待检查", "")
                self.metric_panels.append(panel)
                note.hide()
                value.setStyleSheet("font-size: 17px; font-weight: 600;")
                value.setWordWrap(True)
                metrics.addWidget(panel, 1)
                self.metrics.append(value)
            layout.addLayout(metrics)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.resources_table = table(["名称", "类型", "位置", "状态"])
        self.resources_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.resources_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.resources_table.setColumnWidth(0, 200)
        self.resources_table.setColumnHidden(1, len(kinds) == 1)
        self.resources_table.setMinimumWidth(340)
        self.resources_table.itemSelectionChanged.connect(self.selection_changed)
        self.resources_table.doubleClicked.connect(lambda index: self.show_paths())
        if self.compact:
            self.resources_table.setParent(self)
            self.resources_table.hide()
        else:
            split.addWidget(self.resources_table)
        tabs = QTabWidget()
        self.details = text_view()
        self.technical_details = text_view()
        self.details.setMinimumWidth(240)
        tabs.addTab(self.details, "概览")
        self.tabs = tabs
        self.paths_page = QWidget()
        self.paths_layout = QVBoxLayout(self.paths_page)
        self.paths_layout.setContentsMargins(8, 8, 8, 8)
        paths_scroll = QScrollArea()
        paths_scroll.setWidgetResizable(True)
        paths_scroll.setWidget(self.paths_page)
        tabs.addTab(paths_scroll, "目录与操作")
        self.recent_details = text_view()
        tabs.addTab(self.recent_details, "最近操作")
        tabs.addTab(self.technical_details, "配置与诊断")
        split.addWidget(tabs)
        split.setSizes([650, 360])
        layout.addWidget(split, 1)
        self.empty = QLabel("还没有添加。先选择项目或资料文件夹。")
        self.empty.setWordWrap(True)
        self.empty.setObjectName("Subtitle")
        layout.addWidget(self.empty)
        self.resources_table.setObjectName("SafetyResourceTable")
        self.selection_changed()

    def add_resource(self) -> None:
        if len(self.kinds) == 1:
            self.window.add_resource(self.kinds[0])
        else:
            menu = QMenu(self)
            for kind in self.kinds:
                menu.addAction(KINDS[kind], lambda kind=kind: self.window.add_resource(kind))
            menu.exec(self.add.mapToGlobal(self.add.rect().bottomLeft()))

    def selected(self) -> Resource | None:
        row = self.resources_table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def select_instance(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            self.resources_table.selectRow(row)

    def refresh(self) -> None:
        selected = self.selected()
        query = self.search.text().strip().casefold()
        self.rows = [r for r in self.window.service.resources() if r.kind in self.kinds and query in (r.name + json.dumps(r.options, ensure_ascii=False)).casefold()]
        self.resources_table.blockSignals(True)
        self.resources_table.setRowCount(len(self.rows))
        for row, resource in enumerate(self.rows):
            location = str(resource.options.get("path") or resource.options.get("script_path") or "未配置")
            observation = self.window.observations.get(resource.id, {})
            state = "任务运行中" if self.window.is_busy(resource.id) else str(observation.get("state") or self.window.resource_states.get(resource.id, "等待检查"))
            for column, value in enumerate([resource.name, KINDS[resource.kind], location, state]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.resources_table.setItem(row, column, item)
        row = next((i for i, r in enumerate(self.rows) if selected and r.id == selected.id), 0)
        if self.rows:
            self.resources_table.selectRow(row)
        self.resources_table.blockSignals(False)
        self.selector.blockSignals(True)
        self.selector.clear()
        for resource in self.rows:
            self.selector.addItem(resource.name, resource.id)
        self.selector.setCurrentIndex(row if self.rows else -1)
        self.selector.blockSignals(False)
        self.empty.setVisible(not self.rows)
        self.selection_changed()

    def selection_changed(self) -> None:
        resource = self.selected()
        if resource:
            self.selector.blockSignals(True)
            self.selector.setCurrentIndex(self.resources_table.currentRow())
            self.selector.blockSignals(False)
        enabled = bool(resource and not self.window.is_busy(resource.id))
        for widget in [self.observe_button, self.backup_button, self.verify_button, self.restore_button, self.more_button]:
            widget.setEnabled(enabled)
        if not resource:
            self.details.setPlainText("选择一个资源，查看状态和可用操作。")
            self.technical_details.clear()
            self.recent_details.clear()
            self.populate_paths(None)
            return
        observation = self.window.observations.get(resource.id)
        capabilities = self.window.service.registry.get(resource).capabilities
        if not resource.options.get("manage_git"):
            capabilities = capabilities - {"git_pull"}
        payload = {"名称": resource.name, "类型": KINDS[resource.kind], "可用操作": "、".join(ACTION_LABELS.get(action, action) for action in sorted(capabilities) if action != "open_vault" or resource.kind == "vault")}
        if observation is not None:
            payload["最近检查结果"] = observation
            payload["提示"] = "最近结果仅代表执行时观测；点击检查状态获取新结果。"
        else:
            payload["提示"] = "尚未检查。配置已保存到本机；不会自动连接服务器。"
        self.technical_details.setPlainText(readable_report(payload))
        health = self.window.service.backup_health(resource, self.window.service.backup_root())
        if self.metrics:
            counts = (observation or {}).get("counts", {})
            values = [health["state"], "已演练" if health.get("rehearsed") else "已校验" if health["state"] == "已校验" else "尚待验证",
                      str((observation or {}).get("state") or ("已连接" if (observation or {}).get("connected") else "等待检查")),
                      "独立资料目录"]
            for value, label in zip(values, self.metrics):
                label.setText(str(value))
        summary = f"<p style='color:#7a849c'>{KINDS[resource.kind]}</p>"
        summary += "<h3>项目位置</h3><p>" + html.escape(str(resource.options.get("path", ""))) + "</p>"
        tasks = self.window.service.task_summaries(limit=5, resource_id=resource.id)
        recent_text = "\n\n".join(readable_time(task['started_at']) + " · " + STATE_LABELS.get(task['state'], task['state']) + "\n" + task['title'] for task in tasks) or "这项资料还没有操作记录。检查状态、备份或恢复后，会显示在这里。"
        self.recent_details.setPlainText(recent_text)
        policy_note = "每天自动备份 · 管家打开时生效" if resource.options.get("automatic_backup") else "自动备份未开启 · 可在编辑中设置"
        recent_html = "<br>".join(html.escape(readable_time(task['started_at']) + " · " + STATE_LABELS.get(task['state'], task['state']) + " · " + task['title']) for task in tasks[:3]) or "还没有操作记录。完成检查、备份或恢复后，会显示在这里。"
        overview = (
            "<h2>" + html.escape(resource.name) + "</h2>"
            "<table width='100%' cellspacing='8' cellpadding='12'>"
            "<tr><td width='42%' bgcolor='#f3f7ff' valign='top'><h3>保护状态</h3>"
            "<p><b>" + html.escape(health['state']) + "</b></p><p>上次备份：" + html.escape(readable_time(health.get('created_at') or '尚无备份')) + "</p>"
            "<p>资料范围：" + html.escape(str((observation or {}).get('counts', {}).get('sessions', '尚未读取')) + (' 个会话' if (observation or {}).get('counts', {}).get('sessions') is not None else '')) + "</p></td>"
            "<td width='58%' bgcolor='#f7f9fc' valign='top'><h3>当前资料</h3>" + summary + "</td></tr>"
            "<tr><td bgcolor='#fff8ed' valign='top'><h3>恢复演练 / 校验</h3><p><b>" + ("已通过" if health.get('rehearsed') else "已校验" if health.get('verified') else "尚未执行") + "</b></p>"
            "<p>建议至少定期校验一次，确认备份完整且可恢复。</p><p>" + html.escape(policy_note) + "</p></td>"
            "<td bgcolor='#f7f9fc' valign='top'><h3>最近操作</h3><p>" + recent_html + "</p></td></tr>"
            "</table>"
        )
        self.details.setHtml(overview)
        self.populate_paths(resource)

    def show_paths(self) -> None:
        self.tabs.setCurrentIndex(1)

    def populate_paths(self, resource: Resource | None) -> None:
        key = resource.to_dict() if resource else None
        if hasattr(self, "paths_key") and self.paths_key == key:
            return
        self.paths_key = key
        while item := self.paths_layout.takeAt(0):
            if widget := item.widget():
                widget.deleteLater()
        if resource is None:
            self.paths_layout.addWidget(QLabel("选中资料后显示完整目录和快捷操作。"))
            return
        title = QLabel(resource.name)
        title.setWordWrap(True)
        title.setObjectName("SectionTitle")
        self.paths_layout.addWidget(title)
        paths = [(label, str(resource.options.get(key, ""))) for key, label in
                 (("path", "资料目录"),)]
        for label, path in paths:
            if not path:
                continue
            panel = QFrame()
            panel.setObjectName("DirectoryCard")
            content = QVBoxLayout(panel)
            content.addWidget(QLabel(label))
            view = QTextEdit()
            view.setObjectName("DirectoryPath")
            view.setReadOnly(True)
            view.setPlainText(path)
            view.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
            view.setMinimumHeight(52)
            view.setMaximumHeight(90)
            view.setToolTip(path)
            content.addWidget(view)
            controls = QHBoxLayout()
            controls.addWidget(button("复制路径", lambda checked=False, path=path: QApplication.clipboard().setText(path)))
            controls.addWidget(button("打开目录", lambda checked=False, path=path: self.window.open_path(Path(path))))
            controls.addStretch()
            content.addLayout(controls)
            self.paths_layout.addWidget(panel)
        self.paths_layout.addWidget(button("查看全部操作记录", lambda: self.window.show_resource_activity(resource)))
        self.paths_layout.addStretch()

    def dispatch(self, action: str) -> None:
        self.window.perform(self.selected(), action)

    def more(self) -> None:
        resource = self.selected()
        if not resource:
            return
        capabilities = self.window.service.registry.get(resource).capabilities
        labels = {"start": "启动", "stop": "停止", "restart": "重启网关", "git_pull": "Git 拉取（仅快进）", "versions": "查看备份提交", "open": "打开目录", "open_vault": "在 Obsidian 打开"}
        menu = QMenu(self)
        if resource.kind in {"project", "vault"}:
            menu.addAction("试一次恢复（临时目录）", lambda: self.window.rehearse_backup(resource))
        menu.addSeparator()
        for action, label in labels.items():
            if action in capabilities and (action != "open_vault" or resource.kind == "vault") and (action != "git_pull" or resource.options.get("manage_git")):
                menu.addAction(label, lambda action=action: self.dispatch(action))
        menu.addSeparator()
        menu.addAction("移除登记", lambda: self.window.remove_resource(resource))
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))


class MainWindow(QMainWindow):
    task_completed = Signal(str, str)
    TODAY, PLAN, PROJECT, TOOLS, LIBRARY, SAFETY, SETTINGS = range(7)

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.service = ApplicationService(store)
        from .theme import apply_theme
        apply_theme(QApplication.instance(), self.service.setting("appearance", "light"))
        from ..workbench import migrate_workbench
        migrate_workbench(store)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.jobs: dict[str, tuple[Worker, str, object]] = {}
        self.closed = False
        self.resource_states: dict[str, str] = {r.id: "最近已检测" for r in self.service.resources() if self.service.evidence("observe:" + r.id)}
        self.observations: dict[str, dict] = {r.id: self.service.evidence("observe:" + r.id) for r in self.service.resources() if self.service.evidence("observe:" + r.id)}
        self.automatic_retry: dict[str, float] = {}
        self.setWindowTitle(f"selfManger · {__version__}")
        app_icon = QIcon(str(Path(__file__).resolve().parents[1] / "assets/ui/selfManger.svg"))
        self.setWindowIcon(app_icon)
        self.resize(1280, 820)
        self.setMinimumSize(960, 660)
        self._build_shell(app_icon)
        self._build_pages()
        self._start_refresh_timers()

    def _build_shell(self, app_icon):
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(216)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 22, 16, 18)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(9)
        brand_icon = QLabel()
        brand_icon.setObjectName("BrandIcon")
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_icon.setFixedSize(40, 40)
        brand_icon.setPixmap(app_icon.pixmap(40, 40))
        brand_icon.setAccessibleName("selfManger图标")
        brand_row.addWidget(brand_icon)
        brand_titles = QVBoxLayout()
        brand_titles.setSpacing(0)
        brand = QLabel("selfManger")
        brand.setObjectName("Brand")
        brand_version = QLabel("v" + __version__)
        brand_version.setObjectName("BrandSub")
        brand_titles.addWidget(brand)
        brand_titles.addWidget(brand_version)
        brand_row.addLayout(brand_titles, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(24)
        sidebar_section = QLabel("工作空间")
        sidebar_section.setObjectName("SidebarSection")
        sidebar_layout.addWidget(sidebar_section)
        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setIconSize(QSize(20, 20))
        for index, label in zip((0, 8, 3, 14, 6, 5, 7),
                                ("今日工作台", "计划与待办", "项目管理", "工具与环境", "知识库", "数据安全", "设置")):
            self.navigation.addItem(QListWidgetItem(nav_icon(index, "#a4b8d4"), label))
        sidebar_layout.addWidget(self.navigation, 1)
        help_button = button("使用说明", self.show_guide)
        help_button.setObjectName("SidebarHelp")
        version = QLabel("便携模式" if self.store.portable_root else "本机模式")
        version.setObjectName("BrandSub")
        version.setToolTip("当前工作空间的运行模式")
        shell_layout.addWidget(sidebar)
        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.sidebar_footer = QWidget(sidebar)
        self.sidebar_footer.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(self.sidebar_footer)
        footer_layout.setContentsMargins(0, 12, 0, 0)
        footer_layout.setSpacing(6)
        help_button.setIcon(nav_icon(6, "#536680"))
        help_button.setIconSize(QSize(16, 16))
        help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        help_button.setToolTip("打开使用说明")
        topbar_layout = QHBoxLayout()
        topbar_layout.setContentsMargins(0, 0, 0, 0)
        topbar_layout.setSpacing(8)
        today = datetime.now()
        self.global_date = QLabel(today.strftime("%m月%d日") + " · 周" + "一二三四五六日"[today.weekday()])
        self.global_date.setToolTip(today.strftime("%Y年%m月%d日"))
        self.global_date.setObjectName("GlobalTopbarMeta")
        topbar_layout.addWidget(self.global_date, 1)
        self.notification_button = button("", lambda: self.navigation.setCurrentRow(self.TODAY))
        self.notification_button.setIcon(nav_icon(11, "#536680"))
        self.notification_button.setIconSize(QSize(20, 20))
        self.notification_button.setObjectName("TopbarIconButton")
        self.notification_button.setFixedSize(30, 30)
        self.notification_button.setToolTip("查看今日提醒")
        self.notification_button.setAccessibleName("查看今日提醒")
        topbar_layout.addWidget(self.notification_button)
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.addWidget(help_button)
        help_row.addStretch(1)
        help_row.addWidget(version)
        footer_layout.addLayout(topbar_layout)
        footer_layout.addLayout(help_row)
        sidebar_layout.addWidget(self.sidebar_footer)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        content_layout.addWidget(self.progress)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().hide()

    def _build_pages(self):
        # ProjectPage owns its compatibility backup controls.
        from .projects import ProjectPage
        project_backup_page = ResourcePage(
            self, "本地项目", "管理代码、知识库与 Obsidian；加密备份工作文件和附件。", ["project", "vault"])
        self.resource_pages: list[ResourcePage] = [project_backup_page]
        self.project_page = ProjectPage(self, project_backup_page)
        self.stack.addWidget(self.project_page)
        self.build_backups()
        self.build_tasks()
        self.build_settings()
        self.organize_workbench()
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.currentRowChanged.connect(self.page_changed)
        self.navigation.setCurrentRow(0)

    def _start_refresh_timers(self):
        self.statusBar().showMessage("准备就绪 · 不会自动连接服务器或执行恢复")
        self.refresh_resources()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_tasks)
        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(60_000)
        # Legacy backup scheduler is intentionally inactive; data safety now tracks local files.
        QTimer.singleShot(0, self.import_legacy_tasks)
        if self.service.project_workspace_root():
            QTimer.singleShot(0, self.project_page.refresh)
        self._set_initial_window_size()

    def _set_initial_window_size(self) -> None:
        """Open at a generous, centered size based on the current display."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(self.minimumWidth(), min(round(available.width() * 0.73), available.width() - 48))
        height = max(self.minimumHeight(), min(round(available.height() * 0.80), available.height() - 72))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def organize_workbench(self):
        """Build the application pages and the local file tracking workspace."""
        from .workbench import TodayPage
        from .knowledge import KnowledgePage
        from .developer_tools import DeveloperToolsPage
        self.settings_page = self.stack.widget(self.stack.count() - 1)
        old = [self.stack.widget(i) for i in range(self.stack.count())]
        # Compatibility actions still hold references to controls in hidden pages.
        self._legacy_pages = old
        for page in old:
            self.stack.removeWidget(page)
        self.today_page = TodayPage(self)
        from .action_page import ActionPage
        self.planning_page = ActionPage(self, self.today_page)
        self.knowledge_page = KnowledgePage(self)
        self.developer_tools_page = DeveloperToolsPage(self)
        from .safety import SafetyPage
        self.safety_page = SafetyPage(self)
        safety = self.safety_page
        self.activity_tabs.setTabVisible(0, False)
        self.activity_tabs.setCurrentIndex(1)
        for page in (self.today_page, self.planning_page, self.project_page, self.developer_tools_page,
                     self.knowledge_page, safety, self.settings_page):
            self.stack.addWidget(page)


    def open_safety_resource(self, resource):
        self.navigation.setCurrentRow(self.SAFETY)
        self.safety_page.show_overview(resource)

    def open_backup_history(self):
        self.navigation.setCurrentRow(self.SAFETY)
        self.safety_page.show_restore()


    def open_project_identity(self, identity):
        self.service.record_project_used(identity)
        self.project_page.preferred_project = identity
        self.project_page.filter.setCurrentIndex(0)
        self.navigation.setCurrentRow(self.PROJECT)
        self.project_page.refresh()

    def change_workbench_location(self):
        from ..workbench import MarkdownFiles, work_root
        selected = QFileDialog.getExistingDirectory(self, "选择计划文档目录", str(work_root(self.store)))
        if not selected:
            return
        try:
            files = MarkdownFiles(selected)
            files.path("每日")
            self.service.set_workbench_root(files.root)
            self.workbench_location.setText(str(files.root))
            self.today_page.refresh()
            self.planning_page.refresh()
        except UserError as exc:
            QMessageBox.warning(self, "资料位置未改变", str(exc))

    def change_obsidian_vault_root(self):
        current = self.obsidian_vault_root.text().strip().strip('"')
        start = current or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择 Obsidian 知识库根目录", start)
        if directory:
            self.obsidian_vault_root.setText(directory)

    def save_obsidian_vault_root(self):
        from ..knowledge import configure_knowledge_root
        value = self.obsidian_vault_root.text()
        try:
            result = configure_knowledge_root(self.store, value)
        except (OSError, TypeError, ValueError, UserError) as exc:
            QMessageBox.warning(self, "Obsidian 路径未保存", str(exc))
            return
        if result["requires_choice"]:
            choice = QMessageBox.question(
                self, "知识库路径已变化",
                "如果这是原知识库移动后的新位置，选择“是”迁移 SQLite 中的索引和关联；\n"
                "如果要切换到另一个知识库，选择“否”并保留原索引；选择“取消”不保存路径。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            try:
                result = configure_knowledge_root(
                    self.store, value, migrate_index=choice == QMessageBox.StandardButton.Yes)
            except (OSError, TypeError, ValueError, UserError) as exc:
                QMessageBox.warning(self, "知识库索引未迁移", str(exc))
                return
        saved_path = result["saved_path"]
        migrated_index = result["migrated"]
        self.obsidian_vault_root.setText(saved_path)
        if hasattr(self, "settings_status_update"):
            self.settings_status_update()
        message = "已清除 Obsidian 知识库路径" if not saved_path else "Obsidian 知识库路径已保存"
        if migrated_index:
            message = "Obsidian 路径已保存并迁移索引"
        self.statusBar().showMessage(message)


    def build_backups(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("备份历史与换电脑恢复")
        title.setObjectName("Title")
        layout.addWidget(title)
        hint = QLabel("恢复已备份的项目、笔记、数据库和附件，并重新登记新路径。不会安装原应用或恢复云端独有记录。")
        hint.setWordWrap(True)
        hint.setObjectName("Subtitle")
        layout.addWidget(hint)
        outer = layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        bar = FlowLayout()
        bar.addWidget(button("刷新备份列表", self.refresh_backups))
        bar.addWidget(button("恢复选中备份…", self.restore_selected_backup, True))
        bar.addWidget(button("准备可用数据目录…", self.prepare_restored_data_directory))
        bar.addWidget(button("开始换电脑检查", self.import_backup))
        bar.addWidget(button("恢复演练", self.rehearse_selected_backup))
        bar.addWidget(button("打开备份目录", lambda: self.open_path(self.service.backup_root())))
        bar.addWidget(button("能恢复什么", self.show_restore_scope))
        layout.addLayout(bar)
        restore_form = QFormLayout()
        archive_row = QWidget()
        archive_layout = QHBoxLayout(archive_row)
        archive_layout.setContentsMargins(0, 0, 0, 0)
        self.migration_archive_path = QLineEdit()
        self.migration_archive_path.setPlaceholderText("输入 .amb 备份文件的完整路径，或点击浏览")
        archive_layout.addWidget(self.migration_archive_path, 1)
        archive_layout.addWidget(button("浏览…", self.choose_migration_archive))
        restore_form.addRow("备份文件", archive_row)
        password_row = QWidget()
        password_layout = QHBoxLayout(password_row)
        password_layout.setContentsMargins(0, 0, 0, 0)
        self.migration_password = QLineEdit()
        self.migration_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.migration_password.setPlaceholderText("输入创建该备份时使用的口令；仅用于本次恢复")
        password_layout.addWidget(self.migration_password, 1)
        self.migration_show_password = QCheckBox("显示")
        self.migration_show_password.toggled.connect(lambda visible: self.migration_password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        password_layout.addWidget(self.migration_show_password)
        restore_form.addRow("本次口令", password_row)
        layout.addLayout(restore_form)
        layout.addWidget(button("检查并恢复此备份…", self.start_migration_restore, True))
        from .migration import MigrationChecklist
        self.migration_checklist = MigrationChecklist()
        self.migration_task_ids = set()
        layout.addWidget(self.migration_checklist)
        self.backup_table = table(["资源", "时间", "文件数", "大小", "校验状态"])
        self.backup_table.setMinimumHeight(180)
        layout.addWidget(self.backup_table)
        self.backup_rows: list[tuple[Resource, dict]] = []
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.stack.addWidget(page)

    def choose_migration_archive(self) -> None:
        source = QFileDialog.getOpenFileName(self, "选择要恢复的备份文件", self.migration_archive_path.text() or str(self.service.backup_root()), "加密资料备份 (*.amb)")[0]
        if source:
            self.migration_archive_path.setText(source)

    def prepare_restored_data_directory(self) -> None:
        source = QFileDialog.getExistingDirectory(self, "选择已解密并隔离的整体恢复目录")
        if not source:
            return
        source_path = Path(source)
        try:
            old_vault = restored_vault_root(source_path)
        except UserError as exc:
            QMessageBox.warning(self, "恢复目录不可用", str(exc))
            return
        mappings = {}
        if old_vault and QMessageBox.question(
                self, "重新映射知识库路径",
                "备份记录的知识库路径是：\n" + old_vault +
                "\n\n是否将它映射到这台电脑现有的 Obsidian 知识库？\n知识库文件不会从备份中复制。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) == QMessageBox.StandardButton.Yes:
            new_vault = QFileDialog.getExistingDirectory(self, "选择这台电脑现有的 Obsidian 知识库")
            if not new_vault:
                return
            mappings[old_vault] = new_vault
        suggested = str(Path.home() / "Documents" / "selfManger-恢复数据")
        destination, ok = QInputDialog.getText(
            self, "选择新的启用目录", "输入尚不存在的目标目录完整路径；已有目录不会被覆盖。",
            QLineEdit.EchoMode.Normal, suggested)
        destination = destination.strip().strip('"')
        if not ok or not destination:
            return

        def done(result):
            message = ("已准备独立的数据目录。原隔离恢复目录保持不变。\n\n"
                       "新数据目录：\n" + result["data_directory"] +
                       "\n\n启动命令：\nselfManger.exe --data-dir \"" + result["data_directory"] + "\""
                       "\n\n项目工作区：\n" + result["project_workspace"] +
                       "\n个人计划原件：\n" + result["personal_markdown"] +
                       "\n\n核对启动说明和恢复映射报告后，再使用新数据目录启动。")
            QMessageBox.information(self, "可用数据目录已准备", message)

        self.submit(None, "准备隔离恢复数据目录",
                    lambda context: self.service.prepare_overall_restore(
                        source_path, Path(destination), context, path_mappings=mappings), done)

    def start_migration_restore(self) -> None:
        source = self.migration_archive_path.text().strip().strip('"')
        password = self.migration_password.text()
        if not source:
            QMessageBox.warning(self, "缺少备份文件", "请填写 .amb 文件路径，或点击“浏览…”。")
            return
        if not password and not self.service.common_password:
            QMessageBox.warning(self, "缺少备份口令", "请输入创建这份备份时使用的口令。")
            return
        self.import_backup(source, supplied=(password, False))

    def show_restore_scope(self) -> None:
        from .help import guide_path
        from .library import TextReportDialog
        path = guide_path().with_name("RESTORE_SCOPE.md")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = "说明文件缺失，请重新解压完整运行包。"
        TextReportDialog(self, "换电脑恢复 · 范围与验证", {"text": text, "note": "先恢复资料，再在原应用核对路径与登录。"}).exec()

    def build_tasks(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        title = QLabel("操作记录")
        title.setObjectName("Title")
        outer.addWidget(title)
        self.activity_tabs = QTabWidget()
        summaries = QWidget()
        summary_layout = QVBoxLayout(summaries)
        note = QLabel("这是你自己写的工作笔记，不会自动总结或读取 Agent 对话。每次填写一段，点击保存后会加上时间并追加到下方，之前的内容会保留。笔记保存在 SQLite，并包含在数据库备份中；旧版 work-summary.md 只迁入一次，原文件保留。\n\n可以写：今天完成了什么？遇到什么问题？下一步做什么？项目专属的进展请记在“本地项目”的日志里。")
        note.setWordWrap(True)
        note.setObjectName("Subtitle")
        summary_layout.addWidget(note)
        self.summary_input = QTextEdit()
        self.summary_input.setPlaceholderText("例如：\n完成：整理了备份与迁移页面\n问题：恢复目标目录还需要手动确认\n下一步：试用恢复流程")
        self.summary_input.setMaximumHeight(150)
        summary_layout.addWidget(self.summary_input)
        summary_layout.addWidget(button("追加这条笔记", self.save_work_summary, True), alignment=Qt.AlignmentFlag.AlignLeft)
        summary_layout.addWidget(QLabel("已保存的笔记（按时间排列）"))
        self.summary_history = text_view()
        from ..work_summaries import render_work_summaries
        try:
            history = render_work_summaries(self.service.work_summaries())
            if history:
                self.summary_history.setMarkdown(history)
            else:
                self.summary_history.setPlainText("还没有笔记。写一条并点击“追加这条笔记”，保存后会显示在这里。")
        except (OSError, UserError) as exc:
            self.summary_history.setPlainText("工作总结暂时无法读取：" + str(exc))
        summary_layout.addWidget(self.summary_history, 1)
        self.activity_tabs.addTab(summaries, "我写的笔记")
        operations = QWidget()
        layout = QVBoxLayout(operations)
        hint = QLabel("这里是管家自动生成的操作流水，用来查看备份、恢复等任务的结果和排查失败；不是 Agent 对话记录。内容保存在这台电脑。取消任务会在安全检查点生效。")
        hint.setWordWrap(True)
        hint.setObjectName("Subtitle")
        layout.addWidget(hint)
        bar = QHBoxLayout()
        self.task_filter = QComboBox()
        self.task_filter.setMaximumWidth(300)
        self.task_filter.currentIndexChanged.connect(self.refresh_tasks)
        bar.addWidget(self.task_filter)
        bar.addWidget(button("刷新", self.refresh_tasks))
        bar.addWidget(button("请求取消选中任务", self.cancel_task))
        bar.addStretch()
        layout.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        self.task_table = table(["任务", "状态", "开始时间"])
        self.task_table.itemSelectionChanged.connect(self.task_details)
        split.addWidget(self.task_table)
        self.task_detail = text_view()
        split.addWidget(self.task_detail)
        split.setSizes([330, 220])
        layout.addWidget(split, 1)
        self.task_rows: list[dict] = []
        self.activity_tabs.addTab(operations, "管家操作流水")
        self.activity_tabs.currentChanged.connect(self.activity_tab_changed)
        outer.addWidget(self.activity_tabs, 1)
        self.stack.addWidget(page)

    def save_work_summary(self) -> None:
        from ..work_summaries import render_work_summaries
        try:
            rows = self.service.append_work_summary(self.summary_input.toPlainText())
            self.summary_history.setMarkdown(render_work_summaries(rows))
            self.summary_input.clear()
            self.statusBar().showMessage("笔记已保存到 SQLite，并随数据库备份")
        except (OSError, UserError) as exc:
            QMessageBox.warning(self, "总结未保存", str(exc))

    def build_settings(self) -> None:
        from .settings import build_settings
        build_settings(self)

    def toggle_settings_advanced(self, expanded: bool) -> None:
        self.settings_advanced.setVisible(expanded)
        self.settings_advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def show_guide(self) -> None:
        from .help import GuideDialog
        GuideDialog(self).exec()

    def refresh_vault_status(self) -> None:
        if not hasattr(self, "vault_status"):
            return
        secrets = self.service.secrets
        exists = secrets.vault and secrets.vault.exists()
        self.vault_status.setText("密码记忆：" + ("已解锁" if secrets.unlocked else "已锁定" if exists else "未设置，备份时手动输入即可"))
        self.vault_unlock_button.setText("输入总密码解锁" if exists else "开启记住密码")

    def is_busy(self, identity: str) -> bool:
        return any(resource_id == identity for _, resource_id, _ in self.jobs.values())

    def refresh_dashboard(self) -> None:
        if hasattr(self, "today_page"):
            self.today_page.refresh_attention()
        # Process polling has its own timer; document reads must not trigger it.

    def run_automatic_backup(self) -> None:
        if self.jobs:
            return
        for resource in self.service.backup_resources():
            if self.service.backup_due(resource) and time.monotonic() >= self.automatic_retry.get(resource.id, 0):
                self.automatic_retry[resource.id] = time.monotonic() + 300
                self.submit(resource, resource.name + " · 自动备份", lambda context, resource=resource: self.service.automatic_backup(resource, context))
                break

    def unlock_vault(self) -> None:
        if self.jobs:
            QMessageBox.information(self, "任务运行中", "请在任务结束后更改口令库状态。")
            return
        exists = self.service.secrets.vault and self.service.secrets.vault.exists()
        password, ok = QInputDialog.getText(self, "记住备份密码", "输入总密码，解锁已记住的备份密码" if exists else "设置一个总密码，保护保存的备份密码（至少 8 个字符，请单独记住）", QLineEdit.EchoMode.Password)
        if not ok or not password:
            return
        if self.service.secrets.vault and not self.service.secrets.vault.exists():
            confirm, ok = QInputDialog.getText(self, "确认总密码", "再输入一次刚才设置的总密码", QLineEdit.EchoMode.Password)
            if not ok or confirm != password:
                QMessageBox.warning(self, "未解锁", "两次主口令不一致。")
                return
        try:
            self.service.secrets.unlock(password)
            self.service.common_password = self.service.secrets.get("common:backup") or ""
            self.refresh_vault_status()
            self.refresh_common_password()
            self.statusBar().showMessage("便携口令库已解锁；关闭程序后自动锁定")
        except UserError as exc:
            QMessageBox.warning(self, "未解锁", str(exc))

    def lock_vault(self) -> None:
        if self.jobs:
            QMessageBox.information(self, "任务运行中", "请在任务结束后锁定口令库。")
            return
        self.service.secrets.lock()
        self.service.common_password = ""
        self.refresh_vault_status()
        self.refresh_common_password()
        self.statusBar().showMessage("便携口令库已锁定")

    def rehearse_selected_backup(self) -> None:
        row = self.backup_table.currentRow()
        if 0 <= row < len(self.backup_rows):
            resource, report = self.backup_rows[row]
            self.rehearse_backup(resource, report.get("archive", ""))

    def rehearse_backup(self, resource: Resource, source: str = "") -> None:
        source = source or self.service.backup_health(resource, self.service.backup_root()).get("source", "")
        if not source:
            QMessageBox.information(self, "还没有备份", "先为这项资料创建备份，再执行恢复演练。")
            return
        supplied = self.password_input(False)
        if not supplied:
            return
        def done(report):
            self.refresh_backups()
            self.refresh_dashboard()
            QMessageBox.information(self, "恢复演练通过", f"实际恢复并校验了 {report['identical_files']} 个文件，原目录未覆盖。")
        self.submit(resource, resource.name + " · 恢复演练", lambda context: self.service.rehearse(resource, Path(source), self.resolve_password(resource, *supplied), context), done)

    def refresh_resources(self) -> None:
        if hasattr(self, "safety_page"):
            self.safety_page.refresh()

    def add_resource(self, kind: str) -> None:
        if not self.service.supports_resource_kind(kind):
            QMessageBox.information(self, "功能已移除", "此类资源登记入口已移除。")
            return
        dialog = ResourceDialog(self, kind)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_resource:
            self.service.save_resource(dialog.result_resource)
            self.refresh_resources()

    def edit_resource(self, resource: Resource | None) -> None:
        if resource is None or resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}:
            return
        if not self.service.supports_resource_kind(resource.kind):
            QMessageBox.information(self, "功能已移除", "此旧登记的编辑入口已移除；原登记和资料仍保留。")
            return
        if self.is_busy(resource.id) or resource.id in self.service.running_owned_ids():
            QMessageBox.information(self, "资源正在使用", "请等待任务完成并停止本工具启动的进程后修改配置。")
            return
        dialog = ResourceDialog(self, resource.kind, resource)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_resource:
            self.service.save_resource(dialog.result_resource)
            self.refresh_resources()

    def remove_resource(self, resource: Resource | None) -> None:
        if resource is None or resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID} or self.is_busy(resource.id):
            return
        if resource.id in self.service.running_owned_ids():
            QMessageBox.information(self, "进程仍在运行", "请先停止本工具启动的进程，再移除资源登记。")
            return
        if QMessageBox.question(self, "移除资源登记", f"移除 {resource.name} 的登记？磁盘资料和备份保留。") == QMessageBox.StandardButton.Yes:
            self.service.remove_resource(resource.id)
            self.refresh_resources()

    def submit(self, resource: Resource | None, title: str, operation, callback=None, persist_result: bool = True,
               *, record_history: bool = True) -> str:
        if resource and self.is_busy(resource.id):
            QMessageBox.information(self, "任务运行中", "同一资源已有任务，请等待完成。")
            return ""
        identity = uuid4().hex
        resource_id = resource.id if resource else "system"
        if record_history:
            self.service.start_task(identity, resource_id, title)
        worker = Worker(identity, self.service, operation, persist_result, record_history=record_history)
        worker.signals.finished.connect(self.task_finished)
        self.jobs[identity] = (worker, resource_id, callback)
        self.pool.start(worker)
        self.progress.show()
        self.statusBar().showMessage(title + " · 后台执行中")
        if persist_result and record_history:
            self.refresh_resources()
        self.update_task_timer()
        if record_history and self.navigation.currentRow() == self.SAFETY:
            self.safety_page.refresh()
        return identity

    def task_finished(self, identity: str, result, state: str) -> None:
        if identity == getattr(self, "legacy_task_import_id", None):
            self.legacy_task_import_id = None
            self.legacy_task_import_running = False
        entry = self.jobs.pop(identity, None)
        if not entry:
            return
        _, resource_id, callback = entry
        if identity in self.migration_task_ids:
            self.migration_task_ids.discard(identity)
            if state in {"failed", "cancelled"}:
                for index in (1, 2, 3, 4):
                    text = self.migration_checklist.table.item(index, 1).text()
                    if not text.startswith(("通过", "已恢复")):
                        self.migration_checklist.set_step(index, "未通过：" + str(result.get("error", STATE_LABELS.get(state, state))))
                        break
        if entry[0].record_history:
            observation = self.service.evidence("observe:" + resource_id)
            if observation:
                self.observations[resource_id] = observation
            self.resource_states[resource_id] = "最近任务完成" if state == "success" else STATE_LABELS.get(state, state)
        self.progress.setVisible(bool(self.jobs))
        if entry[0].persist_result and entry[0].record_history:
            self.refresh_resources()
        if entry[0].record_history and self.navigation.currentRow() == self.SAFETY:
            self.safety_page.refresh()
        self.update_task_timer()
        self.statusBar().showMessage(f"任务{STATE_LABELS.get(state, state)} · 详情见任务记录")
        if state == "success" and callback:
            try:
                callback(result)
            except (UserError, OSError, ValueError) as exc:
                QMessageBox.warning(self, "任务完成，后续处理未完成", "资料操作已结束，但登记或界面更新未完成。请核对操作结果与文件权限。（" + type(exc).__name__ + "）")
        elif state == "failed":
            QMessageBox.warning(self, "操作未完成", str(result.get("error", "请查看任务记录。")))
        if entry[0].record_history:
            self.refresh_common_password()

        self.task_completed.emit(identity, state)

    def password_input(self, creating: bool) -> tuple[str, bool] | None:
        dialog = PasswordDialog(self, creating)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.password.text(), dialog.remember.isChecked()

    def resolve_password(self, resource: Resource, supplied: str, remember: bool = False) -> str:
        password = supplied or self.service.common_password or self.service.secrets.get("common:backup") or self.service.secrets.get(resource.id + ":backup")
        if not password:
            raise UserError("请在设置中输入统一备份密码，或输入这份旧备份原来的密码。")
        if remember:
            self.service.secrets.save("common:backup", password)
            self.service.common_password = password
        return password

    def refresh_common_password(self) -> None:
        password = self.service.common_password
        if not password:
            try:
                password = self.service.secrets.get("common:backup") or ""
            except UserError:
                password = ""
        if hasattr(self, "common_password_view"):
            self.common_password_view.setText(password)
            self.common_password_view.setEchoMode(QLineEdit.EchoMode.Normal if self.common_password_reveal.isChecked() else QLineEdit.EchoMode.Password)
            self.common_password_reveal.setEnabled(bool(password))
            self.common_password_reveal.setText("隐藏" if self.common_password_reveal.isChecked() else "显示")
        if hasattr(self, "common_password_status"):
            self.common_password_status.setText("已保存 · 所有新建 .amb 备份共用它" if password else "尚未保存统一口令 · 第一次备份时也可以设置")
        if hasattr(self, "settings_status_update"):
            self.settings_status_update()

    def toggle_common_password_visibility(self, visible: bool) -> None:
        self.common_password_reveal.setText("隐藏" if visible else "显示")
        self.common_password_view.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def set_common_password(self) -> None:
        supplied = self.password_input(True)
        if supplied and supplied[0]:
            try:
                self.service.secrets.save("common:backup", supplied[0])
                self.service.common_password = supplied[0]
                self.refresh_common_password()
            except UserError as exc:
                QMessageBox.warning(self, "口令未保存", str(exc))

    def forget_common_password(self) -> None:
        try:
            self.service.secrets.remove("common:backup")
            self.service.common_password = ""
            self.common_password_reveal.setChecked(False)
            self.refresh_common_password()
        except UserError as exc:
            QMessageBox.warning(self, "口令未移除", str(exc))

    def refresh_git_token_status(self) -> None:
        if not hasattr(self, "git_token_status"):
            return
        secrets = self.service.secrets
        if secrets.vault and not secrets.unlocked:
            self.git_token_status.setText("便携口令库已锁定；请先在“电脑维护与配置迁移”中解锁。")
            return
        try:
            saved = bool(secrets.get("git:https-token"))
        except UserError as exc:
            self.git_token_status.setText(str(exc))
            return
        self.git_token_status.setText("已保存 HTTPS 令牌；输入内容不会显示或写入项目配置。" if saved else
                                      "尚未设置；SSH 仓库可直接使用本机 SSH 密钥。")

    def set_git_token(self) -> None:
        token, accepted = QInputDialog.getText(
            self, "设置 Git HTTPS 令牌", "粘贴 GitHub / GitLab 访问令牌：", QLineEdit.EchoMode.Password
        )
        token = token.strip()
        if not accepted:
            return
        if not token or len(token) > 4096 or any(char.isspace() for char in token):
            QMessageBox.warning(self, "令牌未保存", "令牌不能为空、不能包含空白字符，且长度不能超过 4096。")
            return
        try:
            self.service.secrets.save("git:https-token", token)
            self.refresh_git_token_status()
            self.statusBar().showMessage("Git HTTPS 令牌已安全保存")
        except UserError as exc:
            QMessageBox.warning(self, "令牌未保存", str(exc))
        finally:
            token = ""

    def remove_git_token(self) -> None:
        try:
            if not self.service.secrets.get("git:https-token"):
                QMessageBox.information(self, "没有已保存令牌", "当前没有可移除的 Git HTTPS 令牌。")
                return
            self.service.secrets.remove("git:https-token")
            self.refresh_git_token_status()
            self.statusBar().showMessage("Git HTTPS 令牌已从凭据库移除")
        except UserError as exc:
            QMessageBox.warning(self, "令牌未移除", str(exc))

    def maintenance_tools(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("电脑维护")
        dialog.resize(600, 340)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("这些操作用于排查问题或迁移登记清单。完整资料恢复请到“备份与换电脑”。"))
        actions = FlowLayout()
        for label, action in [("检查电脑环境", self.diagnostics), ("导出登记清单", self.export_config), ("导入登记清单", self.import_config),
                              ("解锁旧版密码记忆", self.unlock_vault), ("打开管家资料", lambda: self.open_path(self.store.root))]:
            actions.addWidget(button(label, action))
        layout.addLayout(actions)
        self.settings_info = text_view()
        layout.addWidget(self.settings_info)
        dialog.exec()

    def perform(self, resource: Resource | None, action: str, archive_source: str = "") -> None:
        if resource is None:
            return
        if resource.kind not in {"project", "folder", "script", "vault"}:
            QMessageBox.information(self, "功能已移除", "此旧登记的相关功能已移除；登记与原始资料仍保留。")
            return
        if action == "open_vault":
            root = Path(resource.options.get("path", "")).expanduser()
            if not root.is_dir():
                QMessageBox.warning(self, "目录不存在", "请检查 Vault 目录配置。")
                return
            url = QUrl("obsidian://open")
            query = QUrlQuery()
            query.addQueryItem("vault", str(root.resolve()))
            url.setQuery(query)
            if not QDesktopServices.openUrl(url):
                QMessageBox.warning(self, "未能打开", "请确认这台电脑已安装 Obsidian 并登记了此 Vault。")
            return
        if action == "open":
            self.open_path(Path(resource.options.get("path") or
                                (Path(resource.options.get("script_path", "")).parent if resource.kind == "script" else "")))
            return
        if action == "observe":
            def observed(report):
                self.observations[resource.id] = safe_result(report)
                self.observations[resource.id]["observed_at"] = now()
                self.resource_states[resource.id] = "已检查 · 详情见右侧"
                self.refresh_resources()
            self.submit(resource, resource.name + " · 检查状态", lambda context: self.service.observe(resource, context), observed)
            return
        if action == "backup":
            supplied = ("", False) if self.service.common_password else self.password_input(True)
            if supplied:
                self.submit(resource, resource.name + " · 加密备份", lambda context: self.service.backup(resource, context, self.resolve_password(resource, *supplied)), self.backup_completed)
            return
        if action == "verify":
            source = archive_source or QFileDialog.getOpenFileName(self, "选择加密备份", str(self.service.backup_root()), "加密备份 (*.amb)")[0]
            supplied = self.password_input(False) if source else None
            if source and supplied:
                self.submit(resource, resource.name + " · 解密校验", lambda context: self.service.verify_backup(Path(source), self.resolve_password(resource, *supplied), context), lambda report: self.refresh_backups())
            return
        if action == "restore":
            self.begin_restore(resource, archive_source)
            return
        if action == "git_pull":
            if QMessageBox.question(self, "确认操作", f"对 {resource.name} 执行 Git 拉取？") != QMessageBox.StandardButton.Yes:
                return
        self.submit(resource, resource.name + " · " + action, lambda context: self.service.action(resource, action, context))

    def begin_restore(self, resource: Resource, archive_source: str = "") -> None:
        self.import_backup(archive_source, resource)

    def backup_completed(self, report: dict) -> None:
        self.refresh_backups()
        QMessageBox.information(self, "备份已保存", f"已保存 {report['file_count']} 个文件。\n\n备份文件：\n{report['archive']}\n\n换电脑时复制这个 .amb 文件，并准备好备份口令；在新电脑点击“从备份文件恢复”。")

    def import_backup(self, archive_source: str = "", resource: Resource | None = None,
                      supplied: tuple[str, bool] | None = None) -> None:
        source = archive_source or QFileDialog.getOpenFileName(self, "选择要恢复的备份文件", str(self.service.backup_root()), "加密资料备份 (*.amb)")[0]
        if not source:
            return
        source = source.strip().strip('"')
        self.open_backup_history()
        checklist = self.migration_checklist
        checklist.reset()
        checklist.set_step(0, "已选择：" + Path(source).name)
        supplied = supplied if supplied is not None else self.password_input(False)
        if supplied is None:
            checklist.set_step(1, "尚未输入密码，可重新开始检查")
            return
        password = supplied[0] or self.service.common_password
        if not password and resource:
            try:
                password = self.resolve_password(resource, "")
            except UserError as exc:
                QMessageBox.warning(self, "需要备份口令", str(exc))
                return
        if not password:
            checklist.set_step(1, "缺少创建备份时使用的密码")
            QMessageBox.warning(self, "需要备份口令", "换电脑恢复时请输入创建这份备份时使用的口令。")
            return
        def inspected(report):
            checklist.set_step(1, "通过 · 密码可以解锁这份备份")
            checklist.set_step(2, f"通过 · 已校验 {report['file_count']} 个文件")
            if report["kind"] not in {"project", "folder", "script", "vault"}:
                QMessageBox.warning(self, "不支持的备份", "此旧类型的恢复入口已移除；原备份文件保持原样。")
                return
            identity = report["resource_id"]
            if resource and identity not in {resource.id, resource.options.get("source_resource_id")}:
                QMessageBox.warning(self, "备份不匹配", "这份备份属于其他资料。请在“备份与换电脑”中使用“从备份文件恢复”。")
                return
            original = resource or Resource(report["resource_name"], report["kind"], {}, identity)
            safe_title = "".join(char for char in report["resource_name"] if char not in '/\\:*?"<>|').strip(". ") or identity[:8]
            restore_root = self.store.portable_root / "restored" if self.store.portable_root else Path.home() / "Documents/selfManger恢复"
            dialog = TransferDialog(self, report, str(restore_root / safe_title))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                checklist.set_step(3, "尚未选定目标目录，可重新开始")
                return
            target = dialog.target.text().strip()
            folders = {key: edit.text().strip() for key, edit in dialog.folder_fields.items()}
            def preview(context):
                plan = self.service.plan_restore(original, context, source=source, target=target, password=password, folders=folders)
                if plan.token != report["archive_sha256"]:
                    raise UserError("备份文件在解锁后改变，请重新选择文件。")
                return plan
            def confirm(plan):
                checklist.set_step(3, "通过 · 新目录 / 空目录：" + plan.target)
                if PlanDialog(self, original, plan).exec() != QDialog.DialogCode.Accepted:
                    checklist.set_step(4, "已取消执行，原目录未修改")
                    return
                checklist.set_step(4, "正在恢复 · 请等待文件校验完成")
                def completed(result):
                    checklist.set_step(4, "已恢复并校验 · 资料登记完成")
                    new_resource = Resource(report["resource_name"], "folder" if report["kind"] == "script" else report["kind"], {"path": plan.target}, report["resource_id"])
                    if any(item.id == new_resource.id for item in self.service.backup_resources()):
                        new_resource.options["source_resource_id"] = new_resource.id
                        new_resource.id = uuid4().hex
                        new_resource.name += "（恢复）"
                    self.service.save_resource(new_resource)
                    if supplied[1]:
                        self.service.common_password = password
                        self.refresh_common_password()
                    self.refresh_resources()
                    self.open_safety_resource(new_resource)
                    for page in self.resource_pages:
                        row = next((i for i, item in enumerate(page.rows) if item.id == new_resource.id), None)
                        if row is not None:
                            page.resources_table.selectRow(row)
                    QMessageBox.information(self, "恢复完成", "资料已恢复并在这台电脑登记。\n\n位置：" + plan.target + "\n\n在原应用继续工作前，请核对项目路径。")
                self.migration_task_ids.add(self.submit(original, original.name + " · 换电脑恢复", lambda context: self.service.restore(original, plan, context, password), completed))
            self.migration_task_ids.add(self.submit(original, original.name + " · 恢复预览", preview, confirm))
        self.migration_task_ids.add(self.submit(resource, "解锁并检查备份内容", lambda context: self.service.verify_backup(Path(source), password, context), inspected))

    def refresh_tasks(self) -> None:
        if not hasattr(self, "task_table"):
            return
        resource_id = self.task_filter.currentData()
        resources = self.service.resources()
        choices = [(r.name, r.id) for r in resources]
        if choices != getattr(self, "task_filter_choices", None):
            self.task_filter_choices = choices
            self.task_filter.blockSignals(True)
            self.task_filter.clear()
            self.task_filter.addItem("全部资源", None)
            for name, identity in choices:
                self.task_filter.addItem(name, identity)
                self.task_filter.setItemData(self.task_filter.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
            self.task_filter.setCurrentIndex(max(0, self.task_filter.findData(resource_id)))
            self.task_filter.blockSignals(False)
        previous = self.task_table.currentRow()
        selected = self.task_rows[previous]["id"] if 0 <= previous < len(self.task_rows) else None
        rows = self.service.task_summaries(resource_id=self.task_filter.currentData())
        if rows == self.task_rows:
            self.task_details()
            return
        self.task_rows = rows
        self.task_table.blockSignals(True)
        self.task_table.setRowCount(len(self.task_rows))
        for row, task in enumerate(self.task_rows):
            timestamp = datetime.fromisoformat(task["started_at"]).astimezone().strftime("%m-%d %H:%M")
            for column, value in enumerate([task["title"], STATE_LABELS.get(task["state"], task["state"]), timestamp]):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.task_table.setItem(row, column, item)
        if self.task_rows:
            self.task_table.selectRow(next((i for i, task in enumerate(self.task_rows) if task["id"] == selected), 0))
        self.task_table.blockSignals(False)
        self.task_details()

    def show_resource_activity(self, resource: Resource) -> None:
        self.navigation.setCurrentRow(self.SAFETY)
        self.safety_page.show_overview(resource)

    def task_details(self) -> None:
        row = self.task_table.currentRow()
        if 0 <= row < len(self.task_rows):
            task = self.service.task(self.task_rows[row]["id"])
            if task:
                text = task["log"] + "\n结果\n" + readable_report(json.loads(task["result"]))
                if text != self.task_detail.toPlainText():
                    self.task_detail.setPlainText(text)
            else:
                self.task_detail.clear()
        else:
            self.task_detail.clear()

    def cancel_task(self) -> None:
        row = self.task_table.currentRow()
        if 0 <= row < len(self.task_rows):
            identity = self.task_rows[row]["id"]
            if identity in self.jobs:
                self.jobs[identity][0].context.cancel.set()
                self.service.append_log(identity, "已请求取消；不可中断的写入步骤将等待完成。")
                self.refresh_tasks()

    def update_task_timer(self) -> None:
        if hasattr(self, "timer"):
            self.timer.stop()

    def activity_tab_changed(self, index: int) -> None:
        self.update_task_timer()
        if index == 1 and self.navigation.currentRow() == self.SAFETY:
            self.refresh_tasks()

    def page_changed(self, index: int) -> None:
        for row, icon in enumerate((0, 8, 3, 14, 6, 5, 7)):
            self.navigation.item(row).setIcon(nav_icon(icon, "#1769e8" if row == index else "#7890b1"))
        self.update_task_timer()
        if index == self.TODAY and not self.today_page.loaded_once:
            self.today_page.refresh()
        if index == self.TODAY:
            self.today_page.refresh_module_tiles()
        elif index == self.PLAN:
            self.planning_page.refresh()
        elif index == self.PROJECT:
            self.project_page.refresh()
        elif index == self.TOOLS:
            self.developer_tools_page.refresh()
            self.developer_tools_page.refresh_diagnostics()
        elif index == self.LIBRARY:
            if not self.knowledge_page.loading and not self.knowledge_page.loaded_once:
                self.knowledge_page.refresh()
        elif index == self.SAFETY:
            self.safety_page.refresh()

    def show_resource_hub(self, tool_mode: bool = False, view: str = "knowledge") -> None:
        self.navigation.setCurrentRow(self.LIBRARY)

    def import_legacy_tasks(self) -> None:
        """Preserve old personal planning, without importing project checklists."""
        if getattr(self, "legacy_task_import_running", False):
            return
        from ..workbench import work_root
        daily_root = work_root(self.store)
        configured = self.service.project_workspace_root()
        project_root = Path(configured) if configured else None
        self.legacy_task_import_running = True
        def operation(context):
            from ..task_migration import migrate_legacy_tasks
            from ..action_center import ActionCenter
            report = migrate_legacy_tasks(self.store, daily_root, context)
            report["personal_plans"] = ActionCenter(self.store).import_legacy_plans(daily_root, project_root)
            return report
        def imported(report):
            self.legacy_task_import_running = False
            if sum(report.get(key, 0) for key in ("daily", "personal_plans")):
                self.today_page.refresh()
                self.planning_page.refresh()
                self.project_page.refresh()
                self.statusBar().showMessage(
                    f'已保留旧个人资料（每日行动 {report["daily"]} 项，计划/规则 {report["personal_plans"]} 项）；原文件保留。', 8000)
        identity = self.submit(None, "兼容旧个人计划", operation, imported, persist_result=False)
        self.legacy_task_import_id = identity or None
        if not identity:
            self.legacy_task_import_running = False

    def refresh_backups(self) -> None:
        if not hasattr(self, "backup_table"):
            return
        backup_rows = []
        try:
            for resource in self.service.backup_resources():
                if resource.kind in {"project", "folder", "script", "vault"}:
                    backup_rows.extend((resource, report) for report in self.service.backup_versions(resource))
        except OSError as exc:
            self.statusBar().showMessage(
                f"备份列表读取失败（{type(exc).__name__}），保留上次结果；请检查备份目录权限后刷新。", 8000)
            return
        self.backup_rows = backup_rows
        self.backup_table.setRowCount(len(self.backup_rows))
        for row, (resource, report) in enumerate(self.backup_rows):
            values = [resource.name, readable_time(report.get("created_at", "未知")), str(report.get("file_count", "—")),
                      readable_size(report.get("size", 0)), report.get("integrity", "待校验")]
            for column, value in enumerate(values):
                self.backup_table.setItem(row, column, QTableWidgetItem(value))
        if hasattr(self, "safety_page"):
            self.safety_page.refresh()


    def restore_selected_backup(self) -> None:
        row = self.backup_table.currentRow()
        if 0 <= row < len(self.backup_rows):
            resource, report = self.backup_rows[row]
            self.perform(resource, "restore", report.get("archive", ""))

    def save_settings(self) -> None:
        value = self.backup_path.text().strip().strip('"')
        if not value:
            QMessageBox.warning(self, "未保存", "请选择备份目录。")
            return
        if self.jobs:
            QMessageBox.information(self, "任务运行中", "请在任务完成后修改备份目录。")
            return
        self.service.set_backup_root(value)
        self.backup_path.setText(str(self.service.backup_root()))
        self.backup_location.setPlainText(str(self.service.backup_root()))
        if hasattr(self, "settings_status_update"):
            self.settings_status_update()
        self.statusBar().showMessage("设置已保存")

    def save_backup_retention(self) -> None:
        if self.jobs:
            QMessageBox.information(self, "任务运行中", "请在任务完成后修改保留数量。")
            return
        self.service.set_backup_retention(self.backup_keep.value())
        self.statusBar().showMessage("自动备份保留数量已保存")

    def change_backup_location(self) -> None:
        if self.jobs:
            QMessageBox.information(self, "任务运行中", "请等待任务完成后再更改位置。")
            return
        directory = QFileDialog.getExistingDirectory(self, "以后创建的备份放在哪里", self.backup_path.text() or str(self.service.backup_root()))
        if directory:
            self.backup_path.setText(directory)

    def export_config(self) -> None:
        destination = QFileDialog.getSaveFileName(self, "导出资源配置", "selfManger-resources.json", "JSON (*.json)")[0]
        if destination:
            try:
                self.service.export_config(Path(destination))
                self.statusBar().showMessage("资源配置已导出；凭据和任务记录未包含")
            except (OSError, UserError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def import_config(self) -> None:
        source = QFileDialog.getOpenFileName(self, "导入资源配置", "", "JSON (*.json)")[0]
        if source:
            try:
                count = self.service.import_config(Path(source))
                self.refresh_resources()
                self.statusBar().showMessage(f"已合并 {count} 项新资源，请核对这台电脑的路径")
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.warning(self, "导入失败", str(exc))

    def diagnostics(self) -> None:
        def show(report):
            self.settings_info.setPlainText("本机数据目录：" + report["data_directory"] + "\n\n可用工具：\n" + "\n".join(name + "：" + value for name, value in report["tools"].items()) + "\n\n本机文件备份不依赖远端环境。")
            self.navigation.setCurrentRow(self.SETTINGS)
            self.settings_advanced_toggle.setChecked(True)
        self.submit(None, "检查运行依赖", lambda context: self.service.diagnostics(), show)

    def remove_secret(self) -> None:
        resources = [r for r in self.service.resources() if r.kind in {"project", "folder", "script", "vault"}]
        if not resources:
            QMessageBox.information(self, "尚无资源", "登记资料后可管理对应备份口令。")
            return
        labels = [f"{r.name} · {r.id[:8]}" for r in resources]
        label, ok = QInputDialog.getItem(self, "移除系统凭据", "选择资源；加密备份保留，请确保口令另有保存", labels, editable=False)
        if ok and QMessageBox.question(self, "确认移除口令", "从这台电脑的系统凭据存储移除此口令？") == QMessageBox.StandardButton.Yes:
            resource = resources[labels.index(label)]
            def remove(context):
                self.service.secrets.remove(resource.id + ":backup")
                return {"removed": True}
            self.submit(resource, resource.name + " · 移除系统口令", remove)

    def open_path(self, path: Path) -> bool:
        path = path.expanduser()
        if not path.is_dir():
            QMessageBox.warning(self, "目录不存在", "请检查目录配置。备份目录会在首次备份时创建。")
            return False
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        if not opened:
            QMessageBox.warning(self, "未能打开目录", "系统没有打开这个目录。请检查文件管理器设置或目录权限。")
        return opened

    def closeEvent(self, event) -> None:
        if not self.planning_page.confirm_close(include_quick=True) or not self.knowledge_page.confirm_note_close():
            event.ignore()
            return
        if self.jobs:
            QMessageBox.information(self, "任务仍在运行", "请等待任务完成，或在任务记录中请求取消。恢复切换期间不能关闭程序。")
            event.ignore()
            return
        self.timer.stop()
        self.schedule_timer.stop()
        self.closed = True
        self.project_page.stop_updates()
        self.today_page.stop_updates()
        self.planning_page.stop_updates()
        self.safety_page.stop_updates()
        self.service.common_password = ""
        self.service.secrets.lock()
        event.accept()
