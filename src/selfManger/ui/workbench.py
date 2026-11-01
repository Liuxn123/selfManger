from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QFileSystemWatcher, QEvent, QUrl, QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidgetItem, QScrollArea, QMessageBox, QGridLayout, QDateEdit

from ..domain import UserError
from ..action_center import ActionCenter
from ..project_workspaces import ProjectWorkspace
from ..runtime import TaskContext
from ..workbench import work_root, markdown_uri, project_context
from .tasks import ReadWorker
from .today_widgets import Section, TodayList, FocusCard, WeekRhythm, ROW_DATA, label


def control(text, action, primary=False):
    widget = QPushButton(text)
    widget.setProperty("primary", primary)
    widget.clicked.connect(action)
    return widget




class MarkdownPage(QWidget):
    """Debounced file watching, one background read, and no body in operation history."""
    def __init__(self, window):
        super().__init__()
        self.window, self.worker, self.closed, self.pending = window, None, False, False
        self.auto_refresh = True
        self.watcher = QFileSystemWatcher(self)
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.setInterval(350)
        self.reload_timer.timeout.connect(lambda: self.refresh() if self.auto_refresh and self.isVisible() else None)
        self.watcher.fileChanged.connect(lambda *_: self.reload_timer.start() if self.auto_refresh else None)
        self.watcher.directoryChanged.connect(lambda *_: self.reload_timer.start() if self.auto_refresh else None)
        window.installEventFilter(self)

    def read_background(self, operation, apply):
        if self.closed:
            return
        if self.worker:
            self.pending = True
            return
        def read(cancel):
            context = TaskContext()
            context.cancel = cancel
            return operation(context)
        worker = ReadWorker(read)
        self.worker = worker
        def finished(report):
            self.worker = None
            if self.closed:
                return
            if report is None:
                self.read_failed()
            else:
                apply(report)
            if self.pending:
                self.pending = False
                if self.auto_refresh:
                    self.reload_timer.start()
                else:
                    self.reload_timer.stop()
                    QTimer.singleShot(0, self.refresh)
        worker.signals.finished.connect(finished)
        self.window.pool.start(worker)

    def read_failed(self):
        QMessageBox.warning(self, "资料读取失败", "请检查资料格式、路径与文件权限。原文件未改变。")

    def watch(self, paths):
        if self.watcher.property("projectMovePaused"):
            return
        previous = self.watcher.files() + self.watcher.directories()
        if not self.auto_refresh:
            if previous:
                self.watcher.removePaths(previous)
            return
        available = []
        for value in paths:
            path = Path(value)
            while not path.exists() and path.parent != path:
                path = path.parent
            if path.exists() and str(path) not in available:
                available.append(str(path))
        removed = [value for value in previous if value not in available]
        added = [value for value in available if value not in previous]
        if removed:
            self.watcher.removePaths(removed)
        if added:
            self.watcher.addPaths(added)

    def eventFilter(self, watched, event):
        if self.auto_refresh and watched is self.window and event.type() == QEvent.Type.WindowActivate and self.isVisible() and not self.closed:
            self.reload_timer.start()
        return super().eventFilter(watched, event)

    def open_obsidian(self, path):
        try:
            uri = markdown_uri(path)
            if not QDesktopServices.openUrl(QUrl(uri)):
                raise UserError("请安装并运行 Obsidian 一次以注册打开接口。")
        except UserError as exc:
            QMessageBox.information(self, "打开 Markdown", str(exc))

    def stop_updates(self):
        self.closed = True
        if self.worker:
            self.worker.cancel.set()
        self.reload_timer.stop()
        self.window.removeEventFilter(self)
        previous = self.watcher.files() + self.watcher.directories()
        if previous:
            self.watcher.removePaths(previous)


class TodayPage(MarkdownPage):
    """One screen for today: the next action, the decisions waiting, and this week."""

    GROUPS = (("overdue", "逾期与遗留", "danger"), ("today", "今天", "accent"), ("done", "已完成", "muted"))

    def __init__(self, window):
        super().__init__(window)
        self.auto_refresh = False
        self.setObjectName("TodayPage")
        self.day, self.report, self.projects = date.today(), None, []
        self.rhythm: list[dict] = []
        self.reminders: list[dict] = []
        self.module_attention: list[dict] = []
        self.group_titles = {key: title for key, title, _tone in self.GROUPS}
        self.follow_today = True
        self.loaded_once = False
        self.project_errors = []
        self.narrow = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        top = self.top_layout = QHBoxLayout()
        top.setSpacing(8)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self.heading = label("今天", "TodayDate")
        self.heading.setWordWrap(True)
        title_block.addWidget(self.heading)
        self.subtitle = label("今天要做什么、还有什么在等你。", "TodayMuted")
        title_block.addWidget(self.subtitle)
        top.addLayout(title_block, 1)
        self.date_bar = QWidget()
        self.date_bar.setObjectName("TodayDateBar")
        note = QHBoxLayout(self.date_bar)
        note.setContentsMargins(4, 2, 4, 2)
        note.setSpacing(5)
        self.previous_day_button = self._day_button("‹", lambda: self.set_day(self.day - timedelta(days=1)))
        note.addWidget(self.previous_day_button)
        self.date_picker = QDateEdit(QDate.currentDate())
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("yyyy-MM-dd")
        self.date_picker.setFixedWidth(148)
        self.date_picker.setToolTip("选择日期，查看或安排过去与未来的行动。")
        self.date_picker.dateChanged.connect(lambda value: self.set_day(value.toPython()))
        note.addWidget(self.date_picker)
        self.next_day_button = self._day_button("›", lambda: self.set_day(self.day + timedelta(days=1)))
        note.addWidget(self.next_day_button)
        self.today_button = control("今天", lambda: self.set_day(date.today()))
        self.today_button.setToolTip("回到今天的行动与提醒")
        note.addWidget(self.today_button)
        top.addWidget(self.date_bar)
        self.open_daily_button = control("计划与待办", self.open_daily)
        self.open_daily_button.setParent(self)
        self.open_daily_button.setToolTip("打开计划与待办：每日、长期、周期、周月复盘与项目计划")
        self.open_daily_button.hide()
        self.add_task_button = control("＋ 记录行动", self.add_current_entry, True)
        self.add_task_button.setToolTip("新建一条行动，保存后进入计划与待办")
        top.addWidget(self.add_task_button)
        self.refresh_button = control("刷新", self.manual_refresh)
        self.refresh_button.setToolTip("刷新今日行动、待我处理与本周节奏")
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        self.date_strip = QWidget()
        self.date_strip_layout = QHBoxLayout(self.date_strip)
        self.date_strip_layout.setContentsMargins(0, 0, 0, 0)
        self.date_strip_layout.addStretch(1)
        self.date_strip.hide()
        layout.addWidget(self.date_strip)

        self.focus = FocusCard()
        layout.addWidget(self.focus)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("TodayScroll")
        self.body = QWidget()
        self.body.setObjectName("TodayBody")
        self.columns_layout = QGridLayout(self.body)
        self.columns_layout.setContentsMargins(0, 0, 2, 0)
        self.columns_layout.setSpacing(16)

        self.task_card = Section("今日行动", 9, "＋ 添加行动", self.add_current_entry)
        self.task_card.body.setSpacing(8)
        self.task_card.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.group_lists = {}
        self.group_headings = {}
        self.group_blocks = {}
        for key, title, tone in self.GROUPS:
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(4)
            block_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            heading = label(title, "TodayGroupTitle")
            heading.setProperty("tone", tone)
            listing = TodayList("task")
            listing.itemChanged.connect(self.toggle_task)
            listing.itemDoubleClicked.connect(self.open_task_item)
            listing.setToolTip("勾选完成行动；周期计划只记录所选日打卡。双击编辑对应行动。")
            block_layout.addWidget(heading)
            block_layout.addWidget(listing)
            self.task_card.body.addWidget(block)
            self.group_lists[key] = listing
            self.group_headings[key] = heading
            self.group_blocks[key] = block
        self.tasks = self.group_lists["today"]
        self.overdue_tasks = self.group_lists["overdue"]
        self.done_tasks = self.group_lists["done"]
        self.empty_action = QWidget()
        empty_layout = QVBoxLayout(self.empty_action)
        empty_layout.setContentsMargins(16, 22, 16, 24)
        empty_layout.setSpacing(10)
        empty_title = label("从一件小事开始", "TodayEmptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_note = label("写下要推进的事，让今天有个清晰的起点。", "TodayEmptyNote")
        empty_note.setWordWrap(True)
        empty_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_note)
        self.empty_add_button = control("＋ 记录第一件事", self.add_current_entry, True)
        empty_layout.addWidget(self.empty_add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.task_card.body.addWidget(self.empty_action)
        self.empty_action.hide()
        self.daily_hint = label("", "TodayMuted")
        self.daily_hint.setWordWrap(True)
        self.daily_hint.setMaximumHeight(36)
        self.task_card.body.addWidget(self.daily_hint)

        self.task_card.setMinimumHeight(260)

        self.attention_card = Section("待我处理", 11, "全部计划 →", self.open_plan_records)
        self.attention = TodayList("attention")
        self.attention.itemClicked.connect(self.open_attention)
        self.attention.setToolTip("这里只列需要你决定或补做的事，点击打开对应位置。")
        self.attention_card.body.addWidget(self.attention, 1)
        self.attention_hint = attention_hint = label("点击事项，前往对应位置处理", "TodayMuted")
        attention_hint.setWordWrap(True)
        self.attention_card.body.addWidget(attention_hint)

        self.week_card = Section("本周节奏", 8, "写复盘 →", lambda: self.open_plan_view("review"))
        self.rhythm_view = WeekRhythm()
        legend = label("浅色 · 计划量     蓝色 · 完成量", "TodayMuted")
        self.week_card.body.addWidget(legend)
        self.week_card.body.addWidget(self.rhythm_view)
        self.week_summary = label("", "TodayMuted")
        self.week_summary.setWordWrap(True)
        self.week_card.body.addWidget(self.week_summary)
        self.week_review = label("", "TodayMuted")
        self.week_review.setWordWrap(True)
        self.week_card.body.addWidget(self.week_review)
        self.week_card.body.addStretch(1)

        self.project_card = Section("继续项目", 3, "查看全部 →", lambda: window.navigation.setCurrentRow(window.PROJECT))
        self.continue_projects = TodayList("project")
        self.continue_projects.itemDoubleClicked.connect(self.open_project)
        self.project_card.body.addWidget(self.continue_projects)

        self.left_column = QWidget()
        self.left_layout = QVBoxLayout(self.left_column)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(16)
        self.left_layout.addWidget(self.task_card)
        self.left_layout.addWidget(self.week_card)
        self.left_layout.addStretch(1)

        self.right_column = QWidget()
        self.right_layout = QVBoxLayout(self.right_column)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(16)
        self.right_layout.addWidget(self.attention_card)
        self.right_layout.addWidget(self.project_card)
        self.right_layout.addStretch(1)

        for card in (self.task_card, self.attention_card, self.week_card, self.project_card):
            card.body.setContentsMargins(20, 18, 20, 18)
            card.body.setSpacing(12)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        for column in range(12):
            self.columns_layout.setColumnStretch(column, 1)
        self.arrange_columns()

    @staticmethod
    def _day_button(text, action):
        button = control(text, action)
        button.setFixedWidth(32)
        button.setToolTip("前一天" if text == "‹" else "后一天")
        return button

    def arrange_columns(self):
        narrow = self.width() < 880
        if narrow == self.narrow:
            return
        self.narrow = narrow
        if narrow:
            self.top_layout.removeWidget(self.date_bar)
            self.date_strip_layout.insertWidget(0, self.date_bar)
        else:
            self.date_strip_layout.removeWidget(self.date_bar)
            self.top_layout.insertWidget(1, self.date_bar)
        self.date_strip.setVisible(narrow)
        cards = (self.task_card, self.attention_card, self.week_card, self.project_card)
        for widget in (self.left_column, self.right_column, *cards):
            self.columns_layout.removeWidget(widget)
        for card in cards:
            self.left_layout.removeWidget(card)
            self.right_layout.removeWidget(card)
        if narrow:
            for row, card in enumerate(cards):
                self.columns_layout.addWidget(card, row, 0, 1, 12)
        else:
            for column in (self.left_layout, self.right_layout):
                for index in reversed(range(column.count())):
                    if column.itemAt(index).spacerItem() is not None:
                        column.takeAt(index)
            self.left_layout.addWidget(self.task_card)
            self.left_layout.addWidget(self.week_card)
            self.left_layout.addStretch(1)
            self.right_layout.addWidget(self.attention_card)
            self.right_layout.addWidget(self.project_card)
            self.right_layout.addStretch(1)
            self.columns_layout.addWidget(self.left_column, 0, 0, 1, 7)
            self.columns_layout.addWidget(self.right_column, 0, 7, 1, 5)
        for column in range(12):
            self.columns_layout.setColumnStretch(column, 1)
        QTimer.singleShot(0, self.sync_body_height)

    def sync_body_height(self):
        """Let the page scroll instead of squeezing cards below their content."""
        if self.closed or not hasattr(self, "columns_layout"):
            return
        gap = self.columns_layout.horizontalSpacing()
        if self.narrow:
            needed = sum(card.sizeHint().height() for card in
                         (self.task_card, self.attention_card, self.week_card, self.project_card)) + gap * 3
        else:
            left = (self.task_card.sizeHint().height() + self.week_card.sizeHint().height()
                    + self.left_layout.spacing())
            right = (self.attention_card.sizeHint().height() + self.project_card.sizeHint().height()
                     + self.right_layout.spacing())
            needed = max(left, right)
        self.body.setMinimumHeight(0)
        self.body.setMinimumHeight(needed)

    def add_current_entry(self):
        self.add_entry("今日任务")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "columns_layout"):
            self.arrange_columns()

    def add_entry(self, section, day=None, parent=None, on_saved=None):
        page = self.window.planning_page
        page.set_day(day or self.day)
        page.open_document_dialog("daily")

    def open_plan_view(self, view):
        self.window.navigation.setCurrentRow(self.window.PLAN)
        page = self.window.planning_page
        page.set_day(self.day)
        page.select_view(view)

    def open_task_item(self, item):
        row = item.data(ROW_DATA) or {}
        identity = row.get("planned_task_id")
        if not identity:
            return
        task = self.window.service.planned_task(identity)
        if task:
            self.edit_unified_task(task)

    def edit_unified_task(self, task):
        self.window.navigation.setCurrentRow(self.window.PLAN)
        self.window.planning_page.open_editor(record=task)

    def set_day(self, day):
        self.follow_today = day == date.today()
        if day != self.day:
            self.day = day
            self.report = None
            for listing in self.group_lists.values():
                listing.setEnabled(False)
                listing.blockSignals(True)
                listing.clear()
                listing.blockSignals(False)
            self.tasks.addItem("正在读取所选日期…")
        self.date_picker.blockSignals(True)
        self.date_picker.setDate(QDate(day.year, day.month, day.day))
        self.date_picker.blockSignals(False)
        self.refresh()

    def manual_refresh(self):
        today = date.today()
        if self.follow_today and self.day != today:
            self.set_day(today)
        else:
            self.refresh()

    def refresh(self):
        if self.closed:
            return
        self.loaded_once = True
        weekday = "一二三四五六日"[self.day.weekday()]
        self.heading.setText(f"{self.day.month} 月 {self.day.day} 日 · 星期{weekday}")
        is_today = self.day == date.today()
        self.today_button.setEnabled(not is_today)
        self.task_card.title.setText("今日行动" if is_today else "当日行动")
        notes_root = work_root(self.window.store)
        self.notes_root = notes_root
        root = self.window.service.project_workspace_root()
        selected_day = self.day
        def read(context):
            from ..task_migration import migrate_legacy_tasks
            migrate_legacy_tasks(self.window.store, notes_root, context)
            center = ActionCenter(self.window.store)
            document_report = center.documents.migrate_legacy_documents(context)
            report = {"day": selected_day.isoformat(), "tasks": [], "schedule": []}
            priority_labels = {"high": "高", "medium": "中", "low": "低"}
            for task in center.actions("today", day=selected_day):
                entry = {"line": -1, "text": task["title"], "title": task["title"],
                         "done": task["done"], "priority": priority_labels.get(task["priority"], ""),
                         "time": task.get("scheduled_time", ""), "day": task["occurrence_day"], "planned_task_id": task["id"],
                         "overdue_origin": task["due_date"] if task["overdue"] else None,
                         "project_id": task["project_id"], "source": task["source"], "notes": task["notes"]}
                entry["plan_title"] = task.get("plan_title", "")
                entry["repeat"] = task.get("repeat", "")
                report["tasks"].append(entry)
            context.checkpoint()
            week_start = selected_day - timedelta(days=selected_day.weekday())
            rhythm = center.rhythm(week_start, week_start + timedelta(days=6), today=date.today())
            projects, errors = [], []
            watches = []
            if root:
                workspace = ProjectWorkspace(Path(root))
                try:
                    workspace = workspace.read_snapshot()
                    listing = workspace.list_projects(context)
                except (UserError, OSError) as exc:
                    listing = {"projects": []}
                    errors.append("项目工作区需要检查：" + str(exc))
                for item in listing["projects"]:
                    if item["state"] == "archived" or item.get("legacy"):
                        continue
                    try:
                        doc = workspace.document(item["id"], "agent/STATUS.md")
                        projects.append({**item, **project_context(doc["text"], item["state"])})
                        watches.append(doc["path"])
                    except (UserError, OSError):
                        errors.append(item["name"] + "：项目状态需要检查。")
                watches += [str(workspace.registry_path), str(workspace.registry_path.parent)]
            reminders = [{"kind": "plan_due", "title": item["title"], "day": item["due_date"],
                          "detail": "今天到期" if item["due_date"] == selected_day.isoformat() else "已逾期", "plan_id": item["id"]}
                         for item in center.plans() if item["status"] not in {"done", "archived"}
                         and item["due_date"] and item["due_date"] <= selected_day.isoformat()]
            completed_reviews = {item["period_key"] for item in center.reviews()}
            week_end = selected_day - timedelta(days=(selected_day.weekday() + 1) % 7)
            week_start_review = week_end - timedelta(days=6)
            week_iso = week_start_review.isocalendar()
            week_key = f"weekly:{week_iso.year}-W{week_iso.week:02d}"
            if week_key not in completed_reviews:
                reminders.append({"kind": "review", "title": "周复盘", "day": week_end.isoformat(),
                                  "detail": "今天完成周复盘" if selected_day == week_end else "周复盘待完成",
                                  "period_key": week_key})
            month_start = selected_day.replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            if selected_day < month_end:
                month_end = month_start - timedelta(days=1)
                month_start = month_end.replace(day=1)
            month_key = f"monthly:{month_start:%Y-%m}"
            if month_key not in completed_reviews:
                reminders.append({"kind": "review", "title": "月度复盘", "day": month_end.isoformat(),
                                  "detail": "今天完成月度复盘" if selected_day == month_end else "月度复盘待完成",
                                  "period_key": month_key})
            return {"daily": report, "projects": projects, "errors": errors, "watches": watches,
                    "reminders": reminders, "rhythm": rhythm, "plan_errors": document_report["errors"],
                    "plan_migrated": document_report["updated"]}
        self.read_background(read, self.render)

    def render(self, report):
        if not self.matches_day(report["daily"]):
            return
        self.projects = report["projects"]
        self.render_rhythm(report.get("rhythm", []))
        self.render_daily(report["daily"], report.get("reminders", []), report.get("plan_errors", []),
                          report.get("plan_migrated", 0))
        self.render_projects(report)
        self.render_week_review()
        QTimer.singleShot(0, self.sync_body_height)

    def render_week_review(self):
        pending = any(str(item.get("period_key", "")).startswith("weekly") for item in self.reminders)
        self.week_review.setText("本周复盘：待写，点右上角补一条" if pending else "本周复盘：已写")

    def render_rhythm(self, rhythm):
        self.rhythm = rhythm or []
        self.rhythm_view.set_days(self.rhythm)
        planned = sum(day["planned"] for day in self.rhythm)
        done = sum(day["done"] for day in self.rhythm)
        if planned:
            self.week_summary.setText(f"本周完成 {done} / 计划 {planned} 项 · 完成率 {round(done * 100 / planned)}%")
        elif self.rhythm:
            self.week_summary.setText("这一周还没有登记行动。")
        else:
            self.week_summary.setText("")

    def render_daily(self, report, reminders=None, plan_errors=None, plan_migrated=0):
        if not self.matches_day(report):
            return
        self.report = report
        self.reminders = reminders or []
        groups = {"overdue": [], "today": [], "done": []}
        for row in report["tasks"]:
            if row.get("done"):
                groups["done"].append(row)
            elif row.get("overdue_origin"):
                groups["overdue"].append(row)
            else:
                groups["today"].append(row)
        empty_text = "这一天还没有安排。点“记录行动”开始。"
        limits = {"overdue": 4, "today": 8, "done": 3}
        for key, listing in self.group_lists.items():
            rows = groups[key]
            self.fill_group(listing, rows, limits[key], empty_text if key == "today" else "")
            block = self.group_blocks[key]
            block.setVisible(bool(rows))
            if rows:
                self.group_headings[key].setText(f"{self.group_titles[key]} · {len(rows)}")
        count = len(report["tasks"])
        done = len(groups["done"])
        is_today = self.day == date.today()
        self.empty_action.setVisible(not count)
        self.task_card.setMinimumHeight(260 if not count else 0)
        self.task_card.title.setText(("今日行动" if is_today else "当日行动") + (f"  ·  {count} 项" if count else ""))
        hint = "与计划页共用同一份行动记录；周期事项按所选日期打卡"
        if plan_errors:
            hint += f" · {len(plan_errors)} 份旧计划文档需检查，原件已保留"
        elif plan_migrated:
            hint += f" · 已将 {plan_migrated} 份旧计划文档迁入 SQLite，原件保留"
        self.daily_hint.setText(hint)
        self.daily_hint.setVisible(bool(plan_errors or plan_migrated))
        self.update_focus(groups, count, done)
        self.refresh_attention()
        if not self.window.jobs:
            self.set_lists_enabled(True)

    def fill_group(self, listing, rows, limit, empty_text=""):
        listing.blockSignals(True)
        listing.clear()
        for row in rows:
            title = row.get("title", row.get("text", ""))
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, ("planned", row["planned_task_id"]))
            if row.get("source") == "ai" and row.get("planned_task_id"):
                item.setToolTip(title + "\nAI 规划任务请在项目概览中交给 Agent 执行。")
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if row.get("done") else Qt.CheckState.Unchecked)
            item.setData(ROW_DATA, row)
            if row.get("overdue_origin"):
                item.setToolTip(title + "\n遗留任务 · 原计划 " + row["overdue_origin"])
            elif row.get("source") != "ai":
                item.setToolTip(title)
            context = [row.get("plan_title"), row.get("project_id")]
            if row.get("repeat"):
                context.append("周期计划 · 本日打卡，不结束系列")
            tail = " · ".join(value for value in context if value)
            if tail:
                item.setToolTip(item.toolTip() + "\n" + tail)
            listing.addItem(item)
        if not rows and empty_text:
            placeholder = QListWidgetItem(empty_text)
            placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
            listing.addItem(placeholder)
        listing.blockSignals(False)
        listing.fit(limit)

    def update_focus(self, groups, count, done):
        pending = groups["overdue"] + groups["today"]
        if pending:
            first = pending[0]
            title = first.get("title") or first.get("text", "")
            bits = []
            if first.get("overdue_origin"):
                bits.append("逾期 · 原计划 " + first["overdue_origin"])
            if first.get("plan_title"):
                bits.append("来自「" + first["plan_title"] + "」")
            if first.get("project_id"):
                bits.append(first["project_id"])
            if first.get("repeat"):
                bits.append("周期事项，今天打卡")
            self.focus.update_state(done, count, title, " · ".join(bits) or "双击可编辑这条行动", "接下来")
        elif count:
            self.focus.update_state(done, count, "今天的安排都完成了", "可以写一条复盘，或者提前准备明天。", "已全部完成")
        else:
            self.focus.update_state(done, count, "留一点专注，推进重要的事", "从下面的行动清单开始，也可以继续手头的项目。", "今日焦点" if self.day == date.today() else "当日焦点")
        is_today = self.day == date.today()
        if not is_today:
            self.subtitle.setText("查看这一天的安排与提醒；切回“回到今天”继续当天节奏。")
        elif groups["overdue"]:
            self.subtitle.setText(f"先处理 {len(groups['overdue'])} 项逾期，再推进今天的 {len(groups['today'])} 件事。")
        elif groups["today"]:
            self.subtitle.setText(f"今天还有 {len(groups['today'])} 件事，已完成 {done} 件。")
        elif count:
            self.subtitle.setText(f"今天的 {done} 件事都完成了。")
        else:
            self.subtitle.setText("行动、项目与本周进展，在这里接着做。")

    def matches_day(self, report):
        return report.get("day") == self.day.isoformat()

    def render_projects(self, report):
        self.continue_projects.clear()
        self.project_errors = report.get("errors", [])
        active = []
        for row in self.projects:
            if row.get("state") in {"completed", "paused"}:
                continue
            active.append(row)
            step = row.get("next_step", "")
            preview = step if len(step) <= 100 else step[:99] + "…"
            item = QListWidgetItem(row["name"] + " · " + row["phase"] + "\n下一步：" + preview)
            item.setToolTip(row["name"] + "\n" + step)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setData(ROW_DATA, {**row, "position": len(active) - 1})
            self.continue_projects.addItem(item)
        if not self.continue_projects.count():
            self.continue_projects.addItem("暂无待继续项目。在“项目”中选择工作区或新建项目。")
        self.continue_projects.fit(5)
        self.project_card.title.setText(f"继续项目  ({len(active)})")
        self.watch(report.get("watches", []))

    def refresh_module_tiles(self):
        """Reuse what neighbouring pages already loaded; the workbench never scans on its own."""
        rows = []
        knowledge = getattr(self.window, "knowledge_page", None)
        if knowledge is not None and getattr(knowledge, "loaded_once", False):
            inbox = [entry for entry in getattr(knowledge, "items", [])
                     if entry.get("kind") != "folder" and not entry.get("archived") and not entry.get("deleted")
                     and "00-收件箱" in str(entry.get("relative", ""))]
            if inbox:
                rows.append({"title": "知识库收件箱待整理", "detail": f"{len(inbox)} 项资料还没归类到主题",
                             "count": len(inbox), "tone": "accent", "goto": ("library", None)})
        from .safety import HEALTHY_STATES
        safety = getattr(self.window, "safety_page", None)
        entries = list(getattr(safety, "entries", []) or [])
        if entries:
            pending = [entry for entry in entries
                       if (entry.get("health") or {}).get("state") not in HEALTHY_STATES]
            if pending:
                first = pending[0]["resource"].name
                rows.append({"title": "资料备份待校验", "detail": first + (f" 等 {len(pending)} 项" if len(pending) > 1 else ""),
                             "count": len(pending), "tone": "warning", "goto": ("safety", None)})
        self.module_attention = rows
        self.refresh_attention()

    def attention_rows(self):
        rows = []
        tasks = (self.report or {}).get("tasks", [])
        overdue = [row for row in tasks if row.get("overdue_origin") and not row.get("done")]
        if overdue:
            rows.append({"title": "逾期与遗留行动", "detail": "最早 " + str(overdue[0].get("overdue_origin")) + " · 补做或放弃",
                         "count": len(overdue), "tone": "danger", "goto": ("plan", "today")})
        due = [item for item in self.reminders if item.get("kind") == "plan_due"]
        if due:
            rows.append({"title": "到期或逾期的计划", "detail": due[0]["title"] + f" · 期限 {due[0]['day']}",
                         "count": len(due), "tone": "warning", "goto": ("plan", "plans")})
        for item in self.reminders:
            if item.get("kind") != "review":
                continue
            monthly = str(item.get("period_key", "")).startswith("monthly:")
            rows.append({"title": "月度复盘待写" if monthly else "周复盘待写",
                         "detail": f"{item['detail']} · {item['day']}",
                         "count": 1, "tone": "accent", "goto": ("review", "monthly" if monthly else "weekly")})
        rows += self.module_attention
        return rows

    def refresh_attention(self, reminders=None):
        if reminders is not None:
            self.reminders = reminders
        rows = self.attention_rows()
        self.attention.blockSignals(True)
        self.attention.clear()
        for row in rows:
            item = QListWidgetItem(row["title"])
            item.setData(ROW_DATA, row)
            item.setToolTip(row.get("detail", "") + "\n点击打开对应位置。")
            self.attention.addItem(item)
        self.attention.blockSignals(False)
        if not rows:
            empty = QListWidgetItem("没有悬而未决的事，按今天的节奏推进就好。")
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.attention.addItem(empty)
        # Fit the rows exactly, so the card reports the height it really needs.
        self.attention.setFixedHeight(max(1, min(self.attention.count(), 8))
                                      * self.attention.itemDelegate().row_height + 2)
        self.attention_hint.setVisible(bool(rows))
        QTimer.singleShot(0, self.sync_body_height)
        self.attention_card.title.setText("待我处理" + (f"  ({len(rows)})" if rows else ""))

    def open_plan_records(self):
        self.open_plan_view("plans")

    def toggle_task(self, item):
        if not self.report or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        target = item.data(Qt.ItemDataRole.UserRole)
        done = item.checkState() == Qt.CheckState.Checked
        if not isinstance(target, tuple) or len(target) != 2 or target[0] != "planned":
            return
        identity = target[1]
        row = item.data(ROW_DATA) or {}
        operation = lambda context: ActionCenter(self.window.store).complete(identity, done, row.get("day"))
        self.task_save_refreshed = False
        def saved(_):
            self.task_save_refreshed = True
            self.refresh()
            self.window.planning_page.refresh()
            self.window.project_page.refresh()
        self.window.submit(None, "更新今日任务", operation, saved, persist_result=False)
        # A second click waits for the first save, so it cannot overwrite another checkbox.
        self.set_lists_enabled(False)
        QTimer.singleShot(400, self.enable_tasks)

    def set_lists_enabled(self, enabled):
        for listing in self.group_lists.values():
            listing.setEnabled(enabled)

    def enable_tasks(self):
        if self.closed:
            return
        if self.window.jobs or self.worker:
            QTimer.singleShot(200, self.enable_tasks)
        else:
            self.set_lists_enabled(True)
            # Success already reloads the day. Retry only if saving failed,
            # so the optimistic checkbox is restored from persisted data.
            if not getattr(self, "task_save_refreshed", False):
                self.refresh()

    def open_daily(self):
        self.open_plan_view("today")

    def open_project(self, item):
        identity = item.data(Qt.ItemDataRole.UserRole)
        if identity:
            self.window.open_project_identity(identity)

    def open_attention(self, item):
        data = item.data(ROW_DATA)
        if not data:
            return
        if "goto" not in data:
            # Reminder rows delivered by the older dashboard shape.
            if data.get("kind") == "review":
                self.open_plan_view("review")
                if data.get("day"):
                    self.window.planning_page.set_day(date.fromisoformat(data["day"]))
                mode = "monthly" if data.get("period_key", "").startswith("monthly:") else "weekly"
                self.window.planning_page.review_overview.select_mode(mode)
            else:
                self.open_plan_records()
                self.window.planning_page.open_plan_editor(data.get("plan_id"))
            return
        target, value = data["goto"]
        if target == "plan":
            self.open_plan_view(value or "all")
        elif target == "review":
            self.open_plan_view("review")
            self.window.planning_page.review_overview.select_mode(value or "weekly")
        elif target == "library":
            self.window.navigation.setCurrentRow(self.window.LIBRARY)
        elif target == "safety":
            self.window.navigation.setCurrentRow(self.window.SAFETY)

    def stop_updates(self):
        super().stop_updates()
