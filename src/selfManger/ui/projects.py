from __future__ import annotations

import hashlib
import copy
import os
import re
import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QFileSystemWatcher, QTimer
from PySide6.QtGui import QDesktopServices, QTextOption
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QPushButton,
    QComboBox, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QTextBrowser, QDialog, QDialogButtonBox, QFormLayout, QTextEdit, QCheckBox,
    QFileDialog, QMessageBox, QInputDialog, QListWidget, QListWidgetItem, QToolButton,
    QFrame, QSizePolicy, QScrollArea, QTreeWidget, QTreeWidgetItem, QApplication, QGridLayout)

from ..domain import Resource, UserError
from ..action_center import ActionCenter, is_personal_action
from ..project_workspaces import ProjectWorkspace, STATES, today
from ..project_prompts import project_prompt
from ..knowledge_resources import note_header
from ..obsidian import project_uri, project_notes_uri, open_uri
from ..project_details import ProjectDetailReader
from ..project_read_models import milestone_rows, milestone_snapshot
from ..project_git import (project_git_repositories, read_project_git_status, run_project_git_action,
                           validate_project_repository_url)
from .components import FlowLayout, nav_icon
from .tasks import ReadWorker
from .project_initialization import ProjectInitializationPanel
from .today_widgets import COLORS, ROW_DATA, label
from .project_widgets import (ProjectCardDelegate, ProjectMetric, ProjectMetrics, ProjectHero,
    ProjectOverview)
from .task_dialog import UnifiedTaskDialog
from .plan_module_widgets import ProjectTaskStatusDialog
from .project_git_dialog import ProjectGitStatusDialog
from .presentation import markdown_preserving_linebreaks




class ProjectLogView(QWidget):
    """Present dated entries while keeping HANDOFF as the single source."""
    def __init__(self, view):
        super().__init__()
        self.view, self.text, self.entries = view, "", []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self.render)
        row.addWidget(self.selector, 1)
        self.all = QCheckBox("查看完整交接原文")
        self.all.toggled.connect(self.render)
        row.addWidget(self.all)
        layout.addLayout(row)
        layout.addWidget(view, 1)

    def set_text(self, text):
        self.text = text
        headings = list(re.finditer(r"(?m)^## (.+)$", text))
        self.entries = [(match.group(1), text[match.start():headings[i + 1].start() if i + 1 < len(headings) else len(text)].strip())
                        for i, match in enumerate(headings) if re.search(r"\d{4}-\d{2}-\d{2}", match.group(1))]
        self.entries.reverse()
        self.selector.blockSignals(True)
        self.selector.clear()
        for title, _ in self.entries:
            self.selector.addItem(title)
        self.selector.blockSignals(False)
        self.render()

    def render(self, *_):
        row = self.selector.currentIndex()
        if self.all.isChecked():
            self.view.setMarkdown(self.text)
        elif 0 <= row < len(self.entries):
            self.view.setMarkdown(self.entries[row][1])
        else:
            self.view.setPlainText("还没有按日期记录的日志。可在 Obsidian 中打开 HANDOFF.md，直接编写日志与交接内容。")


class ProjectPromptDialog(QDialog):
    def __init__(self, parent, item, *, initial=False):
        super().__init__(parent)
        self.setWindowTitle("项目 AI 提示词")
        self.resize(720, 600)
        layout = QVBoxLayout(self)
        hint = QLabel("选择用途，复制给 AI，并附上本轮需求。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.mode = QComboBox()
        self.mode.addItems(["新项目初始化", "已有项目接手"])
        self.mode.setCurrentIndex(0 if initial else 1)
        layout.addWidget(self.mode)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        self.copy_button = buttons.addButton("复制提示词", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.setProperty("primary", True)
        self.copy_button.clicked.connect(self.copy_prompt)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.item = item
        self.mode.currentIndexChanged.connect(self.refresh_prompt)
        self.refresh_prompt()

    def refresh_prompt(self, *_):
        self.preview.setPlainText(project_prompt(
            self.item["name"], self.item["id"], self.item["directory"], initial=self.mode.currentIndex() == 0))
        self.copy_button.setText("复制提示词")

    def copy_prompt(self):
        QApplication.clipboard().setText(self.preview.toPlainText())
        self.copy_button.setText("已复制")


class ProjectDialog(QDialog):
    def __init__(self, parent, legacy=None):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        note = QLabel("填写项目目标，其余按需调整。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：个人工作台")
        self.folder_name = QLineEdit()
        self.folder_name.setPlaceholderText("例如：personal-workbench")
        self.translate_button = QPushButton("翻译为英文")
        self.translate_button.setToolTip("点击后会将项目名称发送至 Google 翻译；也可以直接手动填写英文名。")
        english_row = QHBoxLayout()
        english_row.addWidget(self.folder_name, 1)
        english_row.addWidget(self.translate_button)
        self.folder_hint = QLabel("将自动整理为仅含 a-z、0-9 和连字符的目录名。翻译仅在点击时进行。")
        self.folder_hint.setWordWrap(True)
        self.translation_worker = None
        self.goal = QTextEdit()
        self.goal.setPlaceholderText("这个项目希望完成什么？")
        self.entry = QCheckBox("在 Obsidian 接入笔记、文档和项目任务")
        self.entry.setChecked(True)
        self.entry.setToolTip("仅接入笔记、文档和 agent 管理文档；源码、依赖、数据和构建产物保留在项目目录。")
        if legacy:
            self.setWindowTitle("从旧归档继续 · 新建编号项目")
            note.setText("原归档保留原位。为接下来的工作分配新编号，创建状态、任务和日志，并引用原资料；不会搬动或复制业务文件。")
            self.name.setText(legacy["name"] + "-接续")
            self.folder_name.setText(re.sub(r"[^a-z0-9]+", "-", legacy["name"].lower()).strip("-") + "-continuation")
            self.goal.setPlainText("接续旧项目 " + legacy["name"] + "（" + legacy["id"] + "）。\n原归档资料：" + legacy["directory"] + "\n下一步：核对原项目状态、业务代码位置和本次目标。")
        form.addRow("项目名称", self.name)
        form.addRow("英文目录名", english_row)
        form.addRow("", self.folder_hint)
        form.addRow("项目目标", self.goal)
        form.addRow("", self.entry)
        basic_page = QWidget()
        basic_layout = QVBoxLayout(basic_page)
        basic_layout.addLayout(form)
        self.goal.setMaximumHeight(160)
        basic_layout.addStretch()
        self.initialization_panel = ProjectInitializationPanel(
            self, getattr(parent, "window", None), self.initialization_preview_context)
        self.initialization_panel.insertTab(0, basic_page, "项目信息")
        for i in range(self.initialization_panel.count()):
            self.initialization_panel.setTabText(i, f"{i + 1}. " + self.initialization_panel.tabText(i))
        self.initialization_panel.setCurrentIndex(0)
        layout.addWidget(self.initialization_panel, 1)
        self.initialization_selection = None
        self.finished.connect(self.initialization_panel.cancel_reads)
        footer = QHBoxLayout()
        self.step_label = QLabel()
        footer.addWidget(self.step_label, 1)
        self.back_button = QPushButton("上一步")
        self.back_button.clicked.connect(lambda: self.initialization_panel.setCurrentIndex(self.initialization_panel.currentIndex() - 1))
        footer.addWidget(self.back_button)
        self.next_button = QPushButton("下一步")
        self.next_button.setProperty("primary", True)
        self.next_button.clicked.connect(self.next_step)
        footer.addWidget(self.next_button)
        self.create_button = QPushButton("创建项目")
        self.create_button.setProperty("primary", True)
        self.create_button.clicked.connect(self.confirm)
        footer.addWidget(self.create_button)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)
        self.translate_button.clicked.connect(self.translate_name)
        self.initialization_panel.currentChanged.connect(self.update_step)
        layout.addLayout(footer)
        self.update_step()

    def update_step(self, *_):
        index = self.initialization_panel.currentIndex()
        last = index == self.initialization_panel.count() - 1
        self.step_label.setText(f"第 {index + 1} 步，共 4 步")
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(not last)
        self.next_button.setDefault(not last)
        self.create_button.setVisible(last)
        self.create_button.setDefault(last)

    def next_step(self):
        if self.initialization_panel.currentIndex() == 0 and not self.validate_basics():
            return
        try:
            self.initialization_panel.selection()
        except UserError as exc:
            QMessageBox.warning(self, "请检查所选内容", str(exc))
            return
        self.initialization_panel.setCurrentIndex(min(self.initialization_panel.currentIndex() + 1, 3))

    @staticmethod
    def _translate_name(cancel, name):
        if cancel.is_set():
            return None
        query = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": name})
        request = urllib.request.Request(
            "https://translate.googleapis.com/translate_a/single?" + query,
            headers={"User-Agent": "selfManger/0.8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(256_000).decode("utf-8"))
            result = "".join(part[0] for part in payload[0] if part and isinstance(part[0], str)).strip()
            return result or None
        except Exception:
            return None

    def translate_name(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.information(self, "填写项目名称", "先填写项目中文名称，再点击翻译。")
            return
        self.translate_button.setEnabled(False)
        self.translate_button.setText("翻译中…")
        worker = ReadWorker(lambda cancel: self._translate_name(cancel, name))
        self.translation_worker = worker
        worker.signals.finished.connect(self._translation_finished)
        self.parent().window.pool.start(worker)

    def _translation_finished(self, translated):
        self.translate_button.setEnabled(True)
        self.translate_button.setText("翻译为英文")
        self.translation_worker = None
        if not translated:
            QMessageBox.warning(self, "翻译未完成", "在线翻译暂时不可用。你可以手动填写英文目录名后继续。")
            return
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", translated).strip("-").lower()[:60].strip("-")
        self.folder_name.setText(slug)
        self.folder_hint.setText("翻译建议：" + translated + "。保存时会规范化为安全目录名：" + slug)

    def validate_basics(self):
        if not self.name.text().strip() or not self.folder_name.text().strip() or not self.goal.toPlainText().strip():
            QMessageBox.information(self, "补充项目信息", "请填写项目名称、英文目录名和项目目标。")
            return False
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", self.folder_name.text()).strip("-").lower()
        if not slug or len(slug) > 60:
            QMessageBox.information(self, "英文目录名无效", "目录名需为 1–60 个英文或数字字符；空格和标点会转换为连字符。")
            return False
        self.folder_name.setText(slug)
        return True

    def confirm(self):
        if self.initialization_panel.currentIndex() != 3:
            self.next_step()
            return
        if not self.validate_basics():
            self.initialization_panel.setCurrentIndex(0)
            return
        try:
            self.initialization_selection = self.initialization_panel.selection()
        except UserError as exc:
            QMessageBox.warning(self, "初始化选项需要检查", str(exc))
            return
        self.accept()

    def initialization_preview_context(self):
        project_root = self.parent().root() if self.parent() is not None else Path.cwd()
        folder = self.folder_name.text().strip() or "project-folder"
        identity = "P-" + today()[:4] + "-待分配"
        return Path(project_root) / "projects" / (identity + "-" + folder), {
            "NAME": self.name.text().strip() or "项目名称", "GOAL": self.goal.toPlainText().strip() or "待补充",
            "ID": identity, "DATE": today()}


class ProjectInfoDialog(QDialog):
    """Edit optional project metadata without changing the Markdown source files."""
    def __init__(self, parent, info=None, description=""):
        super().__init__(parent)
        info = info if isinstance(info, dict) else {}
        self.setWindowTitle("编辑项目信息")
        self.resize(680, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("在这里统一编辑项目介绍、封面和登记信息。内容保存在本机项目登记中。"))
        layout.addWidget(QLabel("项目介绍"))
        self.description = QTextEdit(self)
        self.description.setPlaceholderText("写下项目背景、目标、范围或其他希望长期展示的说明…")
        self.description.setPlainText(description)
        self.description.setMaximumHeight(120)
        self.description.textChanged.connect(self.update_description_length)
        layout.addWidget(self.description)
        self.description_length = QLabel(self)
        layout.addWidget(self.description_length, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QLabel("项目登记信息"))
        form = QFormLayout()
        self.owner = QLineEdit(str(info.get("owner", "")))
        member_values = info.get("members", [])
        self.members = QLineEdit("、".join(name for name in member_values if isinstance(name, str)) if isinstance(member_values, list) else "")
        self.members.setPlaceholderText("多人用中文顿号或逗号分隔")
        self.start_date = QLineEdit(str(info.get("start_date", "")))
        self.start_date.setPlaceholderText("YYYY-MM-DD")
        self.end_date = QLineEdit(str(info.get("end_date", "")))
        self.end_date.setPlaceholderText("YYYY-MM-DD")
        self.priority = QComboBox()
        self.priority.addItem("未设置", "")
        for title, value in (("高", "high"), ("中", "medium"), ("低", "low")):
            self.priority.addItem(title, value)
        index = self.priority.findData(info.get("priority", ""))
        self.priority.setCurrentIndex(max(0, index))
        self.repository = QLineEdit(str(info.get("repository", "")))
        self.repository.setPlaceholderText("https://github.com/owner/repo")
        tag_values = info.get("tags", [])
        self.tags = QLineEdit("、".join(tag for tag in tag_values if isinstance(tag, str)) if isinstance(tag_values, list) else "")
        self.tags.setPlaceholderText("用顿号分隔，例如：个人项目、效率工具、Python")
        self.cover_image = QLineEdit(str(info.get("cover_image", "")))
        self.cover_image.setPlaceholderText("可选：选择本地封面图片")
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.cover_image, 1)
        cover_button = QPushButton("选择图片…")
        cover_button.clicked.connect(self.choose_cover)
        cover_row.addWidget(cover_button)
        form.addRow("项目负责人", self.owner)
        form.addRow("项目成员", self.members)
        form.addRow("开始日期", self.start_date)
        form.addRow("结束日期", self.end_date)
        form.addRow("优先级", self.priority)
        form.addRow("代码仓库", self.repository)
        form.addRow("项目标签", self.tags)
        form.addRow("项目封面", cover_row)
        layout.addLayout(form, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_description_length()

    def update_description_length(self):
        length = len(self.description.toPlainText())
        self.description_length.setText(f"{length} / 4000")
        self.description_length.setStyleSheet("color: #d9485f;" if length > 4000 else "")

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择项目封面", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.cover_image.setText(path)

    def confirm(self):
        if len(self.description.toPlainText()) > 4000:
            QMessageBox.information(self, "项目介绍过长", "请将项目介绍控制在 4000 个字符以内。")
            return
        start, end = self.start_date.text().strip(), self.end_date.text().strip()
        parsed = []
        for value, title in ((start, "开始日期"), (end, "结束日期")):
            if value:
                try:
                    parsed.append(date.fromisoformat(value))
                except ValueError:
                    QMessageBox.information(self, "日期格式不正确", f"{title}请使用 YYYY-MM-DD 格式。")
                    return
            else:
                parsed.append(None)
        if parsed[0] and parsed[1] and parsed[0] > parsed[1]:
            QMessageBox.information(self, "日期范围不正确", "开始日期不能晚于结束日期。")
            return
        repository = self.repository.text().strip()
        if repository and not validate_project_repository_url(repository):
            QMessageBox.information(self, "仓库地址不正确", "请填写不含凭据的 HTTPS/SSH 仓库地址。")
            return
        self.values = {
            "owner": self.owner.text().strip(),
            "members": [name.strip() for name in re.split(r"[、,，;；]", self.members.text()) if name.strip()][:20],
            "start_date": start,
            "end_date": end,
            "priority": self.priority.currentData(),
            "repository": repository,
            "tags": [tag.strip().lstrip("#") for tag in re.split(r"[、,，;；]", self.tags.text()) if tag.strip()][:12],
            "cover_image": self.cover_image.text().strip(),
        }
        if len(self.values["owner"]) > 100 or any(len(name) > 100 for name in self.values["members"] + self.values["tags"]):
            QMessageBox.information(self, "内容过长", "负责人和成员名称请控制在 100 个字符以内。")
            return
        cover_path = self.values["cover_image"]
        if cover_path and (not Path(cover_path).is_file() or not cover_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))):
            QMessageBox.information(self, "封面图片无效", "请选择存在的 PNG、JPG、WEBP 或 BMP 图片文件。")
            return
        self.accept()


class DeleteProjectDialog(QDialog):
    def __init__(self, parent, item, references=None):
        super().__init__(parent)
        self.setWindowTitle("删除已归档项目")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        warning = QLabel(
            "此操作会永久删除项目完整目录，以及登记的 Obsidian 目录联接；无法在管家中撤销。",
            self,
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        details = QLabel(
            f"项目：{item['name']}\n编号：{item['id']}\n目录：{item['directory']}\n\n"
            "已发编号会保留；统一任务、计划和资源记录不会随项目目录删除。",
            self,
        )
        details.setWordWrap(True)
        layout.addWidget(details)
        if references is not None:
            reference_label = QLabel(
                f"当前数据库关联：{references[0]} 条任务、{references[1]} 条计划。"
                "这些记录不会删除，删除后可能指向不存在的项目。"
                "请另行检查知识库、备份和其他外部引用。", self)
            reference_label.setWordWrap(True)
            layout.addWidget(reference_label)
        layout.addWidget(QLabel(f"请输入项目名称以确认：{item['name']}", self))

        self.confirmation = QLineEdit(self)
        self.confirmation.setAccessibleName("输入项目名称确认删除")
        self.confirmation.setPlaceholderText("准确输入项目名称")
        layout.addWidget(self.confirmation)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            self,
        )
        self.delete_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.delete_button.setText("永久删除")
        self.delete_button.setProperty("danger", True)
        self.delete_button.setEnabled(False)
        self.confirmation.textChanged.connect(
            lambda text: self.delete_button.setEnabled(text == item["name"])
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class ProjectPage(QWidget):
    def __init__(self, window, backup_page):
        super().__init__()
        self.window = window
        self.backup_page = backup_page
        self.rows = []
        self.document_version = 0
        self.project_detail_task_id = ""
        self.project_detail_cache = {}
        self.pending_project_detail_start = None
        self.project_detail_timer = QTimer(self)
        self.project_detail_timer.setSingleShot(True)
        self.project_detail_timer.setInterval(140)
        self.project_detail_timer.timeout.connect(self._start_pending_project_details)
        self.note_preview_version = 0
        self.notes_project_id = ""
        self.move_task = ""
        self.move_watchers = []
        self.move_selection = None
        self.project_document_upload_jobs = set()
        self.milestone_status_jobs = {}
        window.task_completed.connect(self.move_finished)
        window.task_completed.connect(self.project_document_upload_finished)
        window.task_completed.connect(self.milestone_status_finished)
        self.preferred_project = ""
        self.last_used_project_id = ""
        self.status_text, self.task_text, self.project_knowledge = "", "", []
        self.document_errors = {}
        self.project_details = {}
        self.closed = False
        self.milestones_document_exists = False
        self.git_worker = None
        self.git_repositories = None
        self.setObjectName("ProjectPage")
        self._build_project_sidebar()
        self._build_project_detail()
        self._build_project_hero()
        self._build_project_tabs()
        self._finish_project_page()

    def _build_project_sidebar(self):
        layout = QHBoxLayout(self)
        self.page_layout = layout
        layout.setContentsMargins(10, 12, 14, 12)
        layout.setSpacing(12)
        self.project_list = QFrame()
        self.project_list.setObjectName("ProjectSidebar")
        self.project_list.setFixedWidth(294)
        left = QVBoxLayout(self.project_list)
        left.setContentsMargins(4, 0, 4, 0)
        left.setSpacing(10)
        heading = QHBoxLayout()
        heading.setSpacing(9)
        title_icon = QLabel()
        title_icon.setObjectName("ProjectListIcon")
        title_icon.setPixmap(nav_icon(3, "#2675ee").pixmap(24, 24))
        title_icon.setFixedSize(40, 40)
        title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.addWidget(title_icon)
        title_group = QVBoxLayout()
        title_group.setSpacing(0)
        title_group.addWidget(label("项目", "Title"))
        heading.addLayout(title_group, 1)
        self.new_button = QPushButton("选择工作区")
        self.new_button.setProperty("primary", True)
        self.new_button.setIcon(nav_icon(9))
        self.new_button.clicked.connect(self.create_project)
        heading.addWidget(self.new_button)
        self.obsidian_button = QPushButton("在 Obsidian 中编辑")
        self.obsidian_button.setToolTip("打开当前标签对应的项目原文；笔记请使用目录入口")
        self.obsidian_button.clicked.connect(self.open_in_obsidian)
        self.obsidian_button.hide()
        left.addLayout(heading)
        subtitle = label("管理你的所有项目，跟踪进度，沉淀成果。", "Subtitle")
        subtitle.setWordWrap(True)
        left.addWidget(subtitle)
        toolbar = QHBoxLayout()
        self.root_label = QLabel(self)
        self.root_label.hide()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索项目名称或编号…")
        self.search.textChanged.connect(self.render)
        toolbar.addWidget(self.search, 1)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setToolTip("刷新项目登记与状态")
        # The global button style adds 15 px horizontal padding on each side;
        # 48 px clipped the two-character label in the project search row.
        self.refresh_button.setFixedWidth(68)
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)
        left.addLayout(toolbar)
        self.filter = QComboBox()
        for title, value in (("进行中", "current"), ("已归档", "archived")):
            self.filter.addItem(title, value)
        self.filter.currentIndexChanged.connect(self._legacy_filter_changed)
        self.filter.hide()
        self.filter_chips = {}
        filter_row = FlowLayout()
        filter_row.setSpacing(5)
        for key, text in (("active", "进行中"), ("archived", "已归档")):
            chip = QToolButton()
            chip.setObjectName("ProjectFilterChip")
            chip.setText(text)
            chip.setProperty("caption", text)
            chip.setCheckable(True)
            chip.clicked.connect(lambda checked=False, value=key: self.set_state_filter(value))
            filter_row.addWidget(chip)
            self.filter_chips[key] = chip
        self.state_filter = "active"
        self.filter_chips["active"].setChecked(True)
        left.addLayout(filter_row)

        self.myself_status = QLabel(self)
        self.myself_status.hide()
        self.open_project_button = QPushButton("打开项目文件夹")
        self.open_project_button.clicked.connect(self.open_project)
        self.git_status_button = QPushButton("Git 状态")
        self.git_status_button.clicked.connect(self.inspect_git_status)
        self.archive_button = QPushButton("归档项目")
        self.archive_button.clicked.connect(self.move_project)

        self.list_title = label("项目列表 · 0", "TodaySectionTitle")
        left.addWidget(self.list_title)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("ProjectCards")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.setColumnHidden(1, True)
        self.table.setColumnHidden(2, True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        self.table.setItemDelegateForColumn(0, ProjectCardDelegate(self.table))
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setDefaultSectionSize(106)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        left.addWidget(self.table, 1)
        self.message = QLabel("请选择已有工作区，或选择一个空文件夹初始化。")
        self.message.setObjectName("Subtitle")
        self.message.setWordWrap(True)
        left.addWidget(self.message)

    def _build_project_detail(self):
        detail = QWidget()
        self.project_detail_content = detail
        detail.setObjectName("ProjectDetail")
        detail.setMinimumWidth(0)
        details = QVBoxLayout(detail)
        self.project_detail_layout = details
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(9)

    def _build_project_hero(self):
        hero = ProjectHero()
        self.hero = hero
        hero.setObjectName("ProjectHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(20, 16, 20, 16)
        hero_heading = QHBoxLayout()
        self.project_icon = QLabel()
        self.project_icon.setObjectName("ProjectIcon")
        self.project_icon.setPixmap(nav_icon(3, "#ffffff").pixmap(26, 26))
        self.project_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.project_icon.setFixedSize(48, 48)
        hero_heading.addWidget(self.project_icon)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(3)
        self.project_name = label("选择一个项目", "ProjectName")
        self.project_name.setWordWrap(True)
        self.project_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.project_identity = label("项目的状态、任务、记录都在这里", "TodayMuted")
        self.project_identity.setWordWrap(True)
        hero_text.addWidget(self.project_name)
        hero_text.addWidget(self.project_identity)
        hero_heading.addLayout(hero_text, 1)
        self.obsidian_button.show()
        self.edit_project_info_button = QPushButton("编辑项目信息")
        self.edit_project_info_button.clicked.connect(self.edit_project_info)
        hero_heading.addWidget(self.edit_project_info_button, 0, Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(hero_heading)
        self.hero_body = QWidget()
        self.hero_body.setObjectName("ProjectHeroBody")
        self.hero_body_layout = QGridLayout(self.hero_body)
        self.hero_body_layout.setContentsMargins(0, 0, 0, 0)
        self.hero_body_layout.setHorizontalSpacing(22)
        self.hero_body_layout.setVerticalSpacing(10)
        self.hero_description = QWidget()
        description = QVBoxLayout(self.hero_description)
        description.setContentsMargins(0, 0, 0, 0)
        description.setSpacing(6)
        description.addWidget(label("项目介绍", "TodaySectionTitle"))
        self.delete_project_button = QPushButton("删除项目")
        self.delete_project_button.setProperty("danger", True)
        self.delete_project_button.clicked.connect(self.delete_project)
        self.description_label = label("由你编写项目介绍，补充项目背景、目标或说明。", "ProjectDescriptionLabel")
        self.description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.description_label.setWordWrap(True)
        self.description_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        description.addWidget(self.description_label)
        description.addStretch(1)
        self.hero_facts = QWidget()
        self.hero_facts.setObjectName("ProjectHeroFacts")
        facts = QGridLayout(self.hero_facts)
        facts.setContentsMargins(12, 10, 12, 10)
        facts.setHorizontalSpacing(14)
        facts.setVerticalSpacing(10)
        self.hero_info_values = {}
        for index, (key, caption) in enumerate((("phase", "当前阶段"), ("owner", "负责人"),
                                               ("period", "计划周期"), ("priority", "优先级"))):
            block = QVBoxLayout()
            block.setSpacing(3)
            block.addWidget(label(caption, "TodayMuted"))
            value = label("未设置", "ProjectHeroFactValue")
            value.setWordWrap(True)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            block.addWidget(value)
            facts.addLayout(block, index // 2, index % 2)
            self.hero_info_values[key] = value
        facts.setColumnStretch(0, 1)
        facts.setColumnStretch(1, 1)
        self.hero_body_layout.addWidget(self.hero_description, 0, 0)
        self.hero_body_layout.addWidget(self.hero_facts, 0, 1)
        self.hero_body_layout.setColumnStretch(0, 3)
        self.hero_body_layout.setColumnStretch(1, 4)
        hero_layout.addWidget(self.hero_body)
        self.project_meta = label("", "TodayMuted")
        self.project_meta.setObjectName("ProjectHeroMeta")
        self.project_meta.setWordWrap(True)
        self.project_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hero_layout.addWidget(self.project_meta)
        hero_actions = FlowLayout()
        for action in (self.open_project_button, self.git_status_button, self.obsidian_button,
                       self.archive_button, self.delete_project_button):
            hero_actions.addWidget(action)
        hero_layout.addLayout(hero_actions)
        hero.body_grid = self.hero_body_layout
        hero.description_widget = self.hero_description
        hero.facts_widget = self.hero_facts
        self.project_detail_layout.addWidget(hero)

    def _build_project_tabs(self):
        self.metrics = [ProjectMetric(title, color, icon) for title, color, icon in (
            ("当前阶段", COLORS[0], 15), ("清单任务", COLORS[2], 9),
            ("项目日志", COLORS[3], 13), ("关联资源", COLORS[1], 6))]
        self.metrics_row = ProjectMetrics(self.metrics)
        self.documents = QTabWidget()
        self.documents.setObjectName("ProjectTabs")
        self.documents.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.documents.tabBar().setUsesScrollButtons(True)
        self.views = []
        self.document_paths = ["agent/STATUS.md", "agent/TASKS.md", "agent/HANDOFF.md"]
        for name in ("当前状态", "任务入口", "日志与交接"):
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            text_option = QTextOption()
            text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            view.document().setDefaultTextOption(text_option)
            self.views.append(view)

        self.overview = ProjectOverview(
            self.create_shared_task,
            self.upload_project_resource,
            self.edit_project_info,
            self.show_project_activity,
            lambda: self.documents.setCurrentWidget(self.milestones_page),
            self.toggle_shared_task,
            self.set_milestone_status,
            self.open_project_document,
            self.open_project_resource,
            self.upload_project_document,
        )
        self.documents.addTab(self.overview, "概览")
        self._build_task_page()
        self._build_agent_documents()
        self._build_materials_page()
        self._build_notes_page()
        self._build_related_page()
        self._build_milestones_page()
        self.documents.addTab(self.project_docs, "Agent 文档")
        self.documents.setMinimumWidth(0)
        self.documents.setMinimumHeight(400)
        self.documents.currentChanged.connect(self.on_project_tab_changed)
        self.project_docs.currentChanged.connect(self.update_selection_actions)
        self.project_detail_layout.addWidget(self.documents, 1)

    def _finish_project_page(self):
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(self.project_detail_content)
        detail_scroll.setMinimumWidth(0)
        self.page_layout.addWidget(self.project_list)
        self.page_layout.addWidget(detail_scroll, 1)
        self.backup_page.setParent(self)
        self.backup_page.hide()
        self.refresh_myself()
        self.project_rows = []
        self.render()
        self.new_button.setEnabled(bool(self.root()))
        if not self.root():
            self.refresh()
        # Load project files when this page is opened, rather than at startup.


    def _build_task_page(self):
        self.saving = False
        self.task_page = QWidget()
        task_layout = QVBoxLayout(self.task_page)
        task_layout.setContentsMargins(16, 14, 16, 14)
        task_layout.setSpacing(12)
        task_hint = QLabel("项目任务保存在 SQLite；关联行动与计划、工作台共用同一记录。旧任务文档可迁入一次，原文保留为历史资料。")
        task_hint.setWordWrap(True)
        task_layout.addWidget(task_hint)
        task_toolbar = QHBoxLayout()
        task_toolbar.addWidget(QLabel("任务创建、编辑和删除只修改 SQLite；迁入旧文档后，原文不会再同步更新。"), 1)
        self.import_legacy_project_tasks_button = QPushButton("迁入旧任务（一次）")
        self.import_legacy_project_tasks_button.clicked.connect(self.import_legacy_project_tasks)
        task_toolbar.addWidget(self.import_legacy_project_tasks_button)
        task_layout.addLayout(task_toolbar)
        task_columns = QHBoxLayout()
        task_columns.setSpacing(12)
        self.task_lists = {}
        self.task_add_buttons = {}
        self.task_edit_buttons = {}
        self.task_document_buttons = {}
        self.task_document_paths = {}
        self.task_status_buttons = {}
        self.task_delete_buttons = {}
        for source, title, document_hint, document_path in (
            ("ai", "AI 规划任务", "应用任务保存在 SQLite；历史文档仅支持一次迁入。", "agent/TASKS-AI.md"),
            ("manual", "关联行动", "与计划和工作台共用同一 SQLite 记录。", "agent/TASKS-MANUAL.md"),
        ):
            card_frame = QFrame()
            card_frame.setObjectName("ProjectOverviewCard")
            card_layout = QVBoxLayout(card_frame)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(8)
            heading = QHBoxLayout()
            heading.addWidget(label(title, "TodaySectionTitle"), 1)
            add_button = QPushButton("＋ 新建行动" if source == "manual" else "＋ 新建任务")
            add_button.clicked.connect(lambda _checked=False, value=source: self.create_shared_task(value))
            heading.addWidget(add_button)
            document_button = QPushButton("查看旧文档")
            document_button.clicked.connect(lambda _checked=False, path=document_path: self.open_task_source_document(path))
            heading.addWidget(document_button)
            card_layout.addLayout(heading)
            hint = QLabel(document_hint)
            hint.setObjectName("Subtitle")
            card_layout.addWidget(hint)
            task_list = QListWidget()
            task_list.setObjectName("ProjectNotesList")
            task_list.setWordWrap(True)
            task_list.setSpacing(4)
            task_list.itemDoubleClicked.connect(lambda _item, value=source: self.edit_shared_task(value))
            card_layout.addWidget(task_list, 1)
            edit_button = QPushButton("编辑所选任务")
            edit_button.setEnabled(False)
            edit_button.clicked.connect(lambda _checked=False, value=source: self.edit_shared_task(value))
            task_list.currentItemChanged.connect(lambda *_args: self.update_selection_actions())
            actions = QHBoxLayout()
            actions.addStretch()
            status_button = QPushButton("编辑状态")
            status_button.clicked.connect(lambda _checked=False, value=source: self.edit_shared_task_status(value))
            delete_button = QPushButton("删除任务")
            delete_button.clicked.connect(lambda _checked=False, value=source: self.delete_shared_task(value))
            for button in (edit_button, status_button, delete_button):
                button.setEnabled(False)
                actions.addWidget(button)
            card_layout.addLayout(actions)
            self.task_status_buttons[source] = status_button
            self.task_delete_buttons[source] = delete_button
            self.task_lists[source] = task_list
            self.task_add_buttons[source] = add_button
            self.task_edit_buttons[source] = edit_button
            self.task_document_buttons[source] = document_button
            self.task_document_paths[source] = document_path
            task_columns.addWidget(card_frame, 1)
        task_layout.addLayout(task_columns, 1)
        self.documents.addTab(self.task_page, "任务")

    def _build_agent_documents(self):
        self.project_docs = QTabWidget()
        self.project_docs.addTab(self.views[0], "当前状态")
        self.log_view = ProjectLogView(self.views[2])
        self.project_docs.addTab(self.log_view, "日志与交接")
        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(True)
        self.project_docs.addTab(self.summary_view, "阶段总结")
        prompt_page = QWidget()
        prompt_layout = QVBoxLayout(prompt_page)
        prompt_hint = QLabel("交给 AI 前复制提示词：首次明确目标与验收，后续接续任务；每轮实质进展写入项目动态。")
        prompt_hint.setWordWrap(True)
        prompt_layout.addWidget(prompt_hint)
        self.project_prompt_button = QPushButton("查看 / 复制 AI 提示词")
        self.project_prompt_button.clicked.connect(self.show_project_prompt)
        prompt_layout.addWidget(self.project_prompt_button, 0, Qt.AlignmentFlag.AlignLeft)
        prompt_layout.addStretch()
        self.project_docs.addTab(prompt_page, "AI 提示词")

    def _build_materials_page(self):
        project_material_page = QWidget()
        material_layout = QVBoxLayout(project_material_page)
        material_layout.setContentsMargins(16, 14, 16, 14)
        material_layout.setSpacing(10)
        material_hint = QLabel("这里管理你维护的项目资料。上传会将副本放入当前项目的“文档”目录，原文件不会移动或修改；同名文件不会覆盖。")
        material_hint.setWordWrap(True)
        material_layout.addWidget(material_hint)
        material_toolbar = QHBoxLayout()
        self.project_material_directory_label = QLabel("选择项目后查看项目资料目录。")
        self.project_material_directory_label.setObjectName("Subtitle")
        self.project_material_directory_label.setWordWrap(True)
        material_toolbar.addWidget(self.project_material_directory_label, 1)
        self.upload_project_material_button = QPushButton("上传文件")
        self.upload_project_material_button.setProperty("primary", True)
        self.upload_project_material_button.clicked.connect(self.upload_project_document)
        material_toolbar.addWidget(self.upload_project_material_button)
        self.refresh_project_material_button = QPushButton("刷新")
        self.refresh_project_material_button.clicked.connect(self.refresh_project_documents)
        material_toolbar.addWidget(self.refresh_project_material_button)
        self.open_project_material_directory_button = QPushButton("打开资料目录")
        self.open_project_material_directory_button.clicked.connect(self.open_project_documents_directory)
        material_toolbar.addWidget(self.open_project_material_directory_button)
        self.open_project_material_button = QPushButton("打开所选文件")
        self.open_project_material_button.clicked.connect(self.open_selected_project_document)
        material_toolbar.addWidget(self.open_project_material_button)
        self.edit_project_material_button = QPushButton("在 Obsidian 中编辑")
        self.edit_project_material_button.clicked.connect(self.open_selected_project_document)
        material_toolbar.addWidget(self.edit_project_material_button)
        material_layout.addLayout(material_toolbar)
        material_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.project_material_list = QListWidget()
        self.project_material_list.setObjectName("ProjectNotesList")
        self.project_material_list.setMinimumWidth(220)
        self.project_material_list.currentItemChanged.connect(self.preview_project_document)
        self.project_material_list.itemDoubleClicked.connect(lambda _item: self.open_selected_project_document())
        material_splitter.addWidget(self.project_material_list)
        self.project_material_preview = QTextBrowser()
        self.project_material_preview.setObjectName("ProjectMarkdownPreview")
        self.project_material_preview.setOpenExternalLinks(False)
        self.project_material_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        material_splitter.addWidget(self.project_material_preview)
        material_splitter.setStretchFactor(0, 1)
        material_splitter.setStretchFactor(1, 3)
        material_layout.addWidget(material_splitter, 1)
        self.project_material_rows = []
        self.project_material_directory = ""
        self.project_material_preview_version = 0
        self.documents.addTab(project_material_page, "文档")

    def _build_notes_page(self):
        notes_page = QWidget()
        notes_layout = QVBoxLayout(notes_page)
        notes_layout.setContentsMargins(18, 18, 18, 18)
        notes_layout.setSpacing(12)
        notes_hint = QLabel("笔记保存在当前项目的“笔记”目录，可在管家中新建和编辑，支持多行文字与 Markdown；也可在 Obsidian 中继续编写。")
        notes_hint.setWordWrap(True)
        notes_layout.addWidget(notes_hint)
        self.notes_directory_label = QLabel("选择项目后打开它的笔记目录。")
        self.notes_directory_label.setObjectName("Subtitle")
        self.notes_directory_label.setWordWrap(True)
        self.notes_directory_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        notes_toolbar = QHBoxLayout()
        notes_toolbar.addWidget(self.notes_directory_label, 1)
        self.refresh_notes_button = QPushButton("刷新列表")
        self.refresh_notes_button.clicked.connect(self.refresh_notes)
        notes_toolbar.addWidget(self.refresh_notes_button)
        notes_layout.addLayout(notes_toolbar)
        note_actions = FlowLayout()
        self.new_note_button = QPushButton("新建笔记")
        self.new_note_button.clicked.connect(self.new_project_note)
        note_actions.addWidget(self.new_note_button)
        self.inline_note_button = QPushButton("在管家中编辑")
        self.inline_note_button.clicked.connect(self.edit_project_note)
        note_actions.addWidget(self.inline_note_button)
        self.edit_note_button = QPushButton("在 Obsidian 中编辑")
        self.edit_note_button.setProperty("primary", True)
        self.edit_note_button.clicked.connect(self.open_selected_note)
        note_actions.addWidget(self.edit_note_button)
        notes_layout.addLayout(note_actions)
        notes_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.notes_list = QListWidget()
        self.notes_list.setObjectName("ProjectNotesList")
        self.notes_list.setMinimumWidth(190)
        self.notes_list.currentItemChanged.connect(self.preview_selected_note)
        notes_splitter.addWidget(self.notes_list)
        self.notes_preview = QTextBrowser()
        self.notes_preview.setObjectName("ProjectMarkdownPreview")
        self.notes_preview.setOpenExternalLinks(True)
        self.notes_preview.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        notes_splitter.addWidget(self.notes_preview)
        notes_splitter.setStretchFactor(0, 1)
        notes_splitter.setStretchFactor(1, 3)
        notes_layout.addWidget(notes_splitter, 1)
        self.open_notes_directory_button = QPushButton("打开项目笔记目录")
        self.open_notes_directory_button.clicked.connect(self.open_notes_directory)
        notes_layout.addWidget(self.open_notes_directory_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.documents.insertTab(1, notes_page, "笔记")

    def _build_related_page(self):
        related_page = QWidget()
        self.related_page = related_page
        related_layout = QVBoxLayout(related_page)
        related_hint = QLabel("这里显示在知识库资料信息中关联到当前项目的资料。双击可在知识库中查看。")
        related_hint.setWordWrap(True)
        related_layout.addWidget(related_hint)
        self.related_list = QListWidget()
        self.related_list.setWordWrap(True)
        self.related_list.itemDoubleClicked.connect(self.open_related)
        related_layout.addWidget(self.related_list)
        knowledge_button = QPushButton("查看关联知识库资料")
        knowledge_button.clicked.connect(self.show_project_knowledge)
        related_layout.addWidget(knowledge_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.documents.addTab(related_page, "资源")

    def _build_milestones_page(self):
        milestones_page = QWidget()
        self.milestones_page = milestones_page
        milestones_layout = QVBoxLayout(milestones_page)
        milestones_layout.setContentsMargins(16, 14, 16, 14)
        milestones_heading = QHBoxLayout()
        milestones_heading.addWidget(QLabel("里程碑由你维护在项目 Markdown 文档中。"), 1)
        self.create_milestones_button = QPushButton("创建空白里程碑文档")
        self.create_milestones_button.clicked.connect(self.create_milestones_document)
        milestones_heading.addWidget(self.create_milestones_button)
        self.edit_milestones_button = QPushButton("在 Obsidian 中编辑")
        self.edit_milestones_button.clicked.connect(self.open_in_obsidian)
        milestones_heading.addWidget(self.edit_milestones_button)
        milestones_layout.addLayout(milestones_heading)
        self.milestones_view = QTextBrowser()
        self.milestones_view.setOpenExternalLinks(True)
        milestones_layout.addWidget(self.milestones_view, 1)
        self.documents.addTab(milestones_page, "里程碑")

    def render_related(self, item):
        self.related_list.clear()
        for resource in self.project_knowledge:
            cell = QListWidgetItem("知识库资料 · " + resource["name"])
            cell.setData(Qt.ItemDataRole.UserRole, ("knowledge", resource["id"]))
            self.related_list.addItem(cell)
        if not self.related_list.count():
            self.related_list.addItem("暂无关联知识库资料。可在知识库资料信息中维护项目关联。")

    def open_related(self, item):
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value:
            return
        _, identity = value
        self.open_project_resource(identity)

    def open_project_resource(self, identity):
        resource = next((item for item in self.project_knowledge if item["id"] == identity), None)
        self.show_project_resources()
        if resource:
            self.window.knowledge_page.search.setText(resource["name"])

    def open_project_document(self, relative):
        project = self.selected()
        if not project:
            return
        workspace = self.workspace()
        if relative.startswith("文档/"):
            path = workspace.path(project["path"] + "/" + relative)
            if path.suffix.lower() == ".md":
                self.submit("定位 Obsidian 项目资料", lambda context: project_uri(workspace, project["id"], relative),
                            self.launch_obsidian, persist_result=False)
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, "无法打开文件", "没有可用于打开此文件的应用程序：\n" + str(path))
            return
        self.submit("定位 Obsidian Agent 文档", lambda context: project_uri(workspace, project["id"], relative),
                    self.launch_obsidian, persist_result=False)

    def show_project_prompt(self):
        item = self.selected()
        if item and item.get("state") not in {"archived", "unknown"}:
            ProjectPromptDialog(self, item).exec()

    def populate_project_documents(self, report):
        self.project_material_rows = report.get("documents", [])
        self.project_material_directory = report.get("directory", "")
        self.project_material_directory_label.setText(self.project_material_directory or "项目资料目录尚未建立。")
        self.project_material_list.blockSignals(True)
        self.project_material_list.clear()
        for document in self.project_material_rows:
            size = document.get("size", 0)
            size_text = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f} MB"
            row = QListWidgetItem(f'{document["name"]}  ·  {size_text}')
            row.setData(Qt.ItemDataRole.UserRole, document["relative"])
            row.setToolTip(document["relative"] + "\n修改时间：" + document.get("modified", "未知"))
            self.project_material_list.addItem(row)
        self.project_material_list.blockSignals(False)
        self.project_material_preview.setPlainText(
            f"共 {len(self.project_material_rows)} 个项目资料。选择文件可预览；双击或使用上方按钮打开。"
            if self.project_material_rows else
            "这里是你维护的项目资料区。上传的文件会复制到项目的“文档”目录，原文件不会移动。"
        )
        self.update_selection_actions()

    def refresh_project_documents(self):
        if self.selected():
            self.selection_changed()

    def preview_project_document(self, current, _previous=None):
        project = self.selected()
        relative = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.project_material_preview_version += 1
        version = self.project_material_preview_version
        if not project or not relative:
            return
        workspace = self.workspace()
        def show(report):
            if self.closed or version != self.project_material_preview_version:
                return
            if not isinstance(report, dict):
                self.project_material_preview.setPlainText("预览失败；可尝试打开原文件。")
                return
            path = Path(report["path"])
            if report.get("markdown"):
                self.project_material_preview.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + os.sep))
                self.project_material_preview.setMarkdown(report["text"])
            else:
                self.project_material_preview.setPlainText(report["text"])
            self.open_project_material_button.setEnabled(True)
            self.edit_project_material_button.setEnabled(path.suffix.lower() == ".md")
        self.window.submit(None, "预览项目资料", lambda context: workspace.project_document(project["id"], relative),
                           show, persist_result=False)

    def upload_project_document(self):
        project = self.selected()
        if not project:
            return
        if project.get("state") == "archived":
            QMessageBox.information(self, "项目已归档", "请先重新启用项目，再添加项目资料。")
            return
        sources, _ = QFileDialog.getOpenFileNames(self, "上传项目资料（可多选）", "", "所有文件 (*)")
        if not sources:
            return
        workspace = self.workspace()
        def completed(report):
            self.selection_changed()
            uploaded = report.get("uploaded", [])
            failed = report.get("failed", [])
            lines = [f"已复制 {len(uploaded)} / {len(sources)} 个文件到项目文档目录。", "原文件未移动或修改。"]
            if failed:
                lines.append("\n未复制的文件：")
                lines.extend(f"• {entry['name']}：{entry['error']}" for entry in failed[:8])
                if len(failed) > 8:
                    lines.append(f"另有 {len(failed) - 8} 个文件未复制。")
            QMessageBox.information(self, "项目资料上传结果", "\n".join(lines))

        def copy_selected(context):
            report = {"uploaded": [], "failed": []}
            for source in sources:
                context.checkpoint()
                try:
                    report["uploaded"].append(workspace.upload_project_document(project["id"], source, context))
                except (UserError, OSError) as exc:
                    report["failed"].append({"name": Path(source).name, "error": str(exc)})
            return report

        job_id = self.submit(f"复制 {len(sources)} 个项目资料文件",
                             copy_selected, completed, persist_result=False)
        if job_id:
            self.project_document_upload_jobs.add(job_id)
            self.update_selection_actions()

    def project_document_upload_finished(self, job_id, state):
        if job_id not in self.project_document_upload_jobs:
            return
        self.project_document_upload_jobs.discard(job_id)
        if state != "success":
            self.selection_changed()
        self.update_selection_actions()

    def set_milestone_status(self, milestone, completed, expected_text):
        project = self.selected()
        if not project or project.get("state") == "archived" or not milestone.get("status_editable"):
            return False
        workspace = self.workspace()
        identity = project["id"]
        job_id = self.submit(
            "更新里程碑状态",
            lambda context: workspace.set_milestone_status(
                identity, expected_text, milestone["line_number"], milestone["status_column"],
                milestone["status"], completed, context),
            persist_result=False,
        )
        if not job_id:
            return False
        self.milestone_status_jobs[job_id] = identity
        return True

    def milestone_status_finished(self, job_id, state):
        identity = self.milestone_status_jobs.pop(job_id, None)
        if not identity:
            return
        root = self.root()
        if root:
            self.project_detail_cache.pop((str(root.resolve()), identity), None)
        if self.selected() and self.selected()["id"] == identity:
            self.selection_changed()

    def open_project_documents_directory(self):
        project = self.selected()
        if not project:
            return
        workspace = self.workspace()
        def opened(report):
            path = report["path"]
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                QMessageBox.warning(self, "无法打开目录", "项目资料目录：\n" + path)
        self.submit("打开项目资料目录",
                    lambda context: workspace.ensure_project_documents_directory(project["id"], context), opened,
                    persist_result=False)

    def open_selected_project_document(self):
        row = self.project_material_list.currentItem()
        if row:
            self.open_project_document(row.data(Qt.ItemDataRole.UserRole))

    def show_project_resources(self):
        item = self.selected()
        if item:
            self.window.knowledge_page.set_scope(project=item["id"])
            self.window.show_resource_hub()

    def show_project_knowledge(self):
        item = self.selected()
        if item:
            self.window.knowledge_page.set_scope(project=item["id"])
            self.window.show_resource_hub()

    def upload_project_resource(self):
        item = self.selected()
        if not item or item.get("state") == "archived":
            return
        project_id = item["id"]
        self.window.knowledge_page.set_scope(project=project_id)
        self.window.knowledge_page.add_file(project_id=project_id)

    def show_project_activity(self):
        if self.project_docs.indexOf(self.log_view) < 0:
            return
        self.documents.setCurrentWidget(self.project_docs)
        self.project_docs.setCurrentWidget(self.log_view)

    def populate_shared_project_task_lists(self, project, *, tasks=None):
        if tasks is None:
            tasks = self.linked_project_tasks(project)
        for source, task_list in self.task_lists.items():
            task_list.blockSignals(True)
            task_list.clear()
            group = sorted([task for task in tasks if task.get("source") == source], key=lambda task: task.get("done", task["status"] == "done"))
            for task in group:
                status = "done" if task.get("done") else task["status"]
                state = {"planned": "未开始", "active": "进行中", "done": "已完成"}.get(status, status)
                due = task.get("occurrence_day") or task.get("due_date") or "未设置日期"
                label = "个人行动 · " if task.get("personal") else ""
                row = QListWidgetItem(f"{task['title']}\n{label}{state} · {due}")
                row.setData(Qt.ItemDataRole.UserRole, task)
                origin = "个人行动：在计划中编辑正文" if task.get("personal") else "AI 项目任务" if source == "ai" else "项目行动"
                row.setToolTip(origin + "\n" + task["title"] + ("\n" + task.get("notes", "") if task.get("notes") else ""))
                task_list.addItem(row)
            if not group:
                task_list.addItem("暂无任务")
                task_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
            task_list.blockSignals(False)
            self.task_edit_buttons[source].setEnabled(False)

    def selected_shared_task(self, source):
        row = self.task_lists[source].currentItem()
        task = row.data(Qt.ItemDataRole.UserRole) if row else None
        project, root = self.selected(), self.root()
        if (not task or not project or not root or task.get("project_id") != project["id"]
                or not task.get("project_root") or Path(task["project_root"]).resolve() != root.resolve()):
            return None
        return task

    def edit_shared_task_status(self, source):
        task = self.selected_shared_task(source)
        project = self.selected()
        if task and project and project.get("state") == "active" and not self.saving:
            dialog = ProjectTaskStatusDialog(self, {**task, "done": task.get("done", task["status"] == "done")})
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()

    def change_project_task_status(self, row, combo, *, on_saved=None, on_released=None):
        if self.saving:
            return
        status = combo.currentData()
        self.saving = True
        def operation(context):
            context.checkpoint()
            if row["source"] == "ai":
                return ActionCenter(self.window.store).set_ai_project_task_status(
                    row["id"], status, expected=row)
            return ActionCenter(self.window.store).set_project_task_status(row["id"], status, expected=row, day=row.get("occurrence_day"))
        def saved(_result):
            if on_saved:
                on_saved()
            self.refresh_project_task_views()
        identity = self.window.submit(None, "编辑项目任务状态", operation, saved, persist_result=False)
        def released(finished, *_):
            if finished != identity:
                return
            self.window.task_completed.disconnect(released)
            self.saving = False
            if on_released:
                on_released()
        self.window.task_completed.connect(released)

    def delete_shared_task(self, source):
        task = self.selected_shared_task(source)
        project = self.selected()
        if not task or not project or project.get("state") == "archived":
            return
        if QMessageBox.question(self, "删除任务", f"删除“{task['title']}”？\n删除记录会保留；旧任务文档作为历史资料保留原样。") != QMessageBox.StandardButton.Yes:
            return
        def operation(context):
            context.checkpoint()
            return ActionCenter(self.window.store).delete_project_task(task["id"], expected=task)
        def saved(report):
            self.refresh_project_task_views()
            if report.get("warning"):
                QMessageBox.warning(self, "任务已删除", report["warning"])
        self.window.submit(None, "删除项目任务", operation, saved, persist_result=False)

    def edit_shared_task(self, source):
        project = self.selected()
        if not project or project.get("state") != "active":
            return
        task = self.selected_shared_task(source)
        if not task or not task.get("id"):
            return
        if is_personal_action(task):
            self.window.navigation.setCurrentRow(self.window.PLAN)
            self.window.planning_page.open_editor(record=self.window.service.planned_task(task["id"]))
            return
        due_date = date.fromisoformat(task["due_date"]) if task.get("due_date") else None
        dialog = UnifiedTaskDialog(self.window, kind=task.get("kind", "task"), due_date=due_date,
                                   project_id=task.get("project_id", ""), title=task.get("title", ""), record=task)
        dialog.project.setEnabled(False)
        dialog.save_handler = lambda: self.save_shared_task_dialog(dialog, task)
        dialog.exec()

    def save_shared_task_dialog(self, dialog, task=None):
        if dialog.saving:
            return
        values = dialog.values()
        dialog.saving = True
        dialog.setEnabled(False)
        dialog.save_error.hide()
        def operation(context):
            context.checkpoint()
            return ActionCenter(self.window.store).save_project_task(
                values, task["id"] if task else None, expected=task)
        def saved(_result):
            dialog.accept()
            self.refresh_project_task_views()
        identity = self.window.submit(None, "编辑项目任务" if task else "新建项目任务", operation, saved, persist_result=False)
        def released(finished, *_):
            if finished != identity:
                return
            self.window.task_completed.disconnect(released)
            dialog.saving = False
            dialog.setEnabled(True)
            if dialog.result() != QDialog.DialogCode.Accepted:
                dialog.save_error.show()
        self.window.task_completed.connect(released)

    def refresh_project_task_views(self):
        self.refresh()
        self.window.today_page.refresh()
        self.window.planning_page.refresh()

    def on_project_tab_changed(self, index):
        self.update_selection_actions()

    def import_legacy_project_tasks(self):
        project = self.selected()
        if not project:
            return
        identity = project["id"]
        workspace = self.workspace()
        from ..project_task_documents import preview_legacy_project_task_import, import_legacy_project_task_documents
        def synced(_report):
            if self.selected() and self.selected()["id"] == identity:
                self.refresh_project_task_views()
        def previewed(report):
            if not self.selected() or self.selected()["id"] != identity:
                return
            if report.get("already_imported"):
                QMessageBox.information(self, "已迁入", "该项目的旧任务文档已经迁入过；之后只使用 SQLite 中的任务记录。")
                self.update_selection_actions()
                return
            message = (f"将新增 {report['added']} 条旧任务；与数据库中现有任务冲突的 {report['changed']} 条保留数据库版本。\n"
                       f"已删除记录忽略 {report['ignored']} 条。导入不会修改或删除任何 Markdown 文件。\n"
                       "请核对后确认一次性迁移。")
            if QMessageBox.question(self, "确认导入任务", message,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.submit("一次性迁入项目任务",
                        lambda context: import_legacy_project_task_documents(self.window.store, workspace, identity, context,
                                                                    preview_token=report["token"]),
                        synced, persist_result=False)
        self.submit("预览旧项目任务迁移",
                    lambda context: preview_legacy_project_task_import(self.window.store, workspace, identity, context),
                    previewed, persist_result=False)

    def open_task_source_document(self, relative):
        project = self.selected()
        if not project:
            return
        identity = project["id"]
        workspace = self.workspace()
        self.submit("在 Obsidian 中打开任务文档",
                    lambda _context: project_uri(workspace, identity, relative),
                    self.launch_obsidian, persist_result=False)

    def render_shared_project_tasks(self, view, project):
        tasks = self.linked_project_tasks(project)
        lines = ["关联个人行动与计划与待办共用记录，日期和完成状态不需重复维护。",
                 "项目文档任务留在项目管理，不自动进入个人收集箱。", ""]
        for source, heading in (("ai", "AI 规划"), ("manual", "手动编写")):
            group = [task for task in tasks if task["source"] == source]
            lines.append(f"## {heading}（{len(group)}）")
            if not group:
                lines.append("暂无任务。")
            for task in group:
                mark = "x" if task["status"] == "done" else " "
                state = {"planned": "未开始", "active": "进行中", "done": "已完成"}.get(task["status"], task["status"])
                due = f" · {task['due_date']}" if task["due_date"] else " · 待排期"
                lines.append(f"- [{mark}] {task['title']} · {state}{due}")
            lines.append("")
        view.setPlainText("\n".join(lines))

    def create_shared_task(self, source="manual"):
        project = self.selected()
        if not project:
            QMessageBox.information(self, "选择项目", "先选择一个项目，再为它创建关联任务。")
            return
        if project.get("state") != "active" or source not in self.task_lists:
            return
        if source == "manual":
            self.window.planning_page.new_dialog(project_id=project["id"])
            return
        dialog = UnifiedTaskDialog(self.window, project_id=project["id"])
        source_index = dialog.source.findData(source)
        if source_index >= 0:
            dialog.source.setCurrentIndex(source_index)
        dialog.project.setEnabled(False)
        dialog.save_handler = lambda: self.save_shared_task_dialog(dialog)
        dialog.exec()

    def toggle_shared_task(self, identity, done):
        status = "done" if done else "planned"
        def saved(_):
            self.refresh()
            self.window.today_page.refresh()
            self.window.planning_page.refresh()
        task = self.window.service.planned_task(identity)
        project, root = self.selected(), self.root()
        if (not task or not project or project.get("state") != "active" or not root
                or task.get("project_id") != project["id"] or not task.get("project_root")
                or Path(task["project_root"]).resolve() != root.resolve()):
            return
        def update(context):
            context.checkpoint()
            if task and is_personal_action(task):
                center = ActionCenter(self.window.store)
                row = next((row for row in center.actions() if row["id"] == identity), None)
                return center.complete(identity, done, row["occurrence_day"] if row else None)
            center = ActionCenter(self.window.store)
            if task.get("source") == "ai":
                return center.set_ai_project_task_status(identity, status, expected=task)
            return center.set_project_task_status(identity, status, expected=task)
        submitted = self.window.submit(None, "更新项目任务状态", update,
                                       saved, persist_result=False)
        def released(finished, state):
            if finished != submitted:
                return
            self.window.task_completed.disconnect(released)
            if state != "success":
                self.refresh_project_task_views()
        self.window.task_completed.connect(released)

    def linked_project_tasks(self, project, *, root=None):
        """Project context and the personal center display the same action identities."""
        root = str(Path(root).expanduser().resolve()) if root is not None else str(self.workspace().root.resolve())
        return ActionCenter(self.window.store).project_tasks(project["id"], root)

    def edit_project_info(self):
        item = self.selected()
        if not item or item["state"] == "archived":
            return
        dialog = ProjectInfoDialog(self, item.get("project_info", {}), item.get("description", ""))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        identity = item["id"]

        def saved(_):
            self.preferred_project = identity
            self.refresh()

        self.submit("保存项目信息",
                    lambda context: self.workspace().set_project_info(
                        identity, dialog.values, context, description=dialog.description.toPlainText()
                    ),
                    saved, persist_result=False)

    def myself_resource(self):
        return next((r for r in self.window.service.resources() if r.kind in {"project", "vault"} and Path(r.options.get("path", "")).name.casefold() == "myself"), None)

    def refresh_myself(self):
        resource = self.myself_resource()
        self.myself_status.setText("myself Vault：" + ("已登记" if resource else "未登记"))

    def myself_action(self, action):
        resource = self.myself_resource()
        if not resource:
            self.window.add_resource("vault")
        elif action == "edit":
            self.window.edit_resource(resource)
        else:
            self.window.perform(resource, action)

    def show_backup_resource(self, resource):
        self.window.open_safety_resource(resource)

    def root(self) -> Path | None:
        value = self.window.service.project_workspace_root()
        return Path(value) if value else None

    def workspace(self) -> ProjectWorkspace:
        root = self.root()
        if not root:
            raise UserError("请先选择项目工作区。")
        return ProjectWorkspace(root)

    def owner(self) -> Resource | None:
        root = self.root()
        return Resource("项目工作区", "project", {"path": str(root)}, hashlib.sha256(str(root).encode()).hexdigest()) if root else None

    def submit(self, title, operation, callback=None, persist_result=True, *, record_history=True):
        owner = self.owner()
        if owner is None:
            QMessageBox.information(self, "选择工作区", "先选择保存 projects 和 agent 的工作区根目录。")
            return
        def locked(context):
            with self.window.service.locks.acquire(self.window.service.lock_keys(owner)):
                return operation(context)
        return self.window.submit(owner, title, locked, callback, persist_result=persist_result, record_history=record_history)

    def choose_workspace(self):
        if self.owner() and self.window.is_busy(self.owner().id):
            QMessageBox.information(self, "任务运行中", "请等待本工作区任务结束后切换。")
            return
        selected = QFileDialog.getExistingDirectory(self, "选择项目工作区根目录", str(self.root() or Path.home()))
        if not selected:
            return
        root = Path(selected)
        try:
            workspace = ProjectWorkspace(root)
            if not workspace.registry_path.exists():
                if any(root.iterdir()):
                    raise UserError("这不是规范工作区，也不是空目录。请选择含 agent/projects.json 的根目录，或新建空文件夹。")
                if QMessageBox.question(self, "初始化工作区", "在这个空目录建立 projects、agent 和 Obsidian 项目索引？") != QMessageBox.StandardButton.Yes:
                    return
                self.window.service.set_project_workspace_root(root)
                self.submit("初始化项目工作区", workspace.initialize, lambda report: self.refresh())
            else:
                workspace.registry()
                self.window.service.set_project_workspace_root(root)
                self.refresh()
        except (OSError, UserError) as exc:
            QMessageBox.warning(self, "无法使用工作区", str(exc))

    def refresh(self):
        if self.closed or self.move_watchers:
            return
        root = self.root()
        self.root_label.setText("工作区：" + str(root) if root else "未选择项目工作区")
        self.new_button.setText("新建项目" if root else "选择工作区")
        self.new_button.setEnabled(True)
        if not root:
            self.project_rows = []
            self.render()
            self.project_name.setText("先选择项目工作区")
            self.description_label.setText("选择项目后，可在这里编写项目介绍。")
            self.message.setText("当前数据位置：" + str(self.window.store.root) + " · 尚未选择工作区，原有项目文件不会被删除。")
            return
        if self.owner() and self.window.is_busy(self.owner().id):
            self.message.setText("项目任务正在运行；完成后可点击“刷新”更新列表。")
            return
        def loaded(report):
            vault_sync = report.get("vault_sync", {})
            milestone_progress = report.get("milestone_progress", {})
            self.project_rows = []
            for project in report["projects"]:
                item = dict(project)
                item["progress"] = milestone_progress.get(item["id"])
                info = item.get("project_info", {})
                if not isinstance(info, dict):
                    info = {}
                item["summary"] = item.get("description", "").strip() or ("已归档" if item.get("state") == "archived" else "进行中")
                start, end = info.get("start_date", ""), info.get("end_date", "")
                item["date_label"] = (str(start) + " – " + str(end)).strip(" –")
                self.project_rows.append(item)
            self.render()
            message = f"{len(self.project_rows)} 个项目 · Obsidian 与管家使用同一份文件；外部修改后可点击“刷新”同步。归档不等于备份。"
            if vault_sync.get("hidden_count"):
                message += f" 已从 Obsidian 隐藏 {vault_sync['hidden_count']} 个归档项目入口。"
            errors = vault_sync.get("errors", [])
            if errors:
                details = "；".join(f"{entry['project_id']}：{entry['message']}" for entry in errors[:3])
                message += f" {len(errors)} 个归档入口未完成同步：{details}"
                if len(errors) > 3:
                    message += "；…"
            self.message.setText(message)
        def load_projects(context):
            workspace = self.workspace()
            vault_sync = workspace.sync_archived_vault_entries(context)
            workspace = workspace.read_snapshot()
            report = workspace.list_projects(context)
            milestone_progress = {}
            for project in report["projects"]:
                context.checkpoint()
                try:
                    document = workspace.milestones_document(project["id"])
                    milestones = milestone_rows(document["text"]) if document.get("exists") else []
                    milestone_progress[project["id"]] = milestone_snapshot(milestones)["progress"]
                except (OSError, UserError):
                    milestone_progress[project["id"]] = None
            report["vault_sync"] = vault_sync
            report["milestone_progress"] = milestone_progress
            return report
        self.submit("同步归档项目入口并读取登记", load_projects, loaded, persist_result=False, record_history=False)

    def selected(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def _legacy_filter_changed(self, index):
        mode = self.filter.itemData(index)
        self.state_filter = "archived" if mode == "archived" else "active"
        self._sync_filter_chips()
        self.render()

    def _sync_filter_chips(self):
        for key, button in self.filter_chips.items():
            button.setChecked(key == self.state_filter)

    def set_state_filter(self, value):
        self.state_filter = "archived" if value == "archived" else "active"
        self._sync_filter_chips()
        # Keep the index in sync for archive overview and lifecycle actions.
        index = 1 if self.state_filter == "archived" else 0
        self.filter.blockSignals(True)
        self.filter.setCurrentIndex(index)
        self.filter.blockSignals(False)
        self.render()

    def render(self):
        selected = self.selected()
        query = self.search.text().strip().casefold()
        counts = {"active": 0, "archived": 0}
        for project in self.project_rows:
            state = project.get("state", "unknown")
            counts["archived" if state == "archived" else "active"] += 1
        for key, button in self.filter_chips.items():
            button.setText(f'{button.property("caption")} {counts[key]}')
        self.rows = [p for p in self.project_rows
                     if query in (p["id"] + " " + p["name"]).casefold()
                     and ((p.get("state")) == "archived"
                          if self.state_filter == "archived" else
                          (p.get("state")) != "archived")]
        if self.state_filter != "archived":
            self.rows.sort(key=lambda project: project.get("last_used_at", ""), reverse=True)
        self.list_title.setText(f"项目列表 · {len(self.rows)}")
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for row, item in enumerate(self.rows):
            state_label = ("已归档" if item["state"] == "archived" else
                           "状态待检查" if item["state"] == "unknown" else "进行中")
            for column, value in enumerate((item["id"], item["name"], state_label)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item["name"] + "\n" + item["id"] + " · " + state_label + "\n" + (item.get("error") or item["directory"]))
                cell.setData(ROW_DATA, {**item, "position": row, "state_label": state_label})
                self.table.setItem(row, column, cell)
        if self.rows:
            identity = self.preferred_project or (selected["id"] if selected else "")
            self.table.selectRow(next((i for i, p in enumerate(self.rows) if p["id"] == identity), 0))
            self.preferred_project = ""
        self.table.blockSignals(False)
        self.selection_changed()

    def update_selection_actions(self, *_):
        item = self.selected()
        enabled = bool(item) and not self.move_watchers
        archived = bool(item and item["state"] == "archived")
        state_valid = bool(item and item.get("state") != "unknown")
        self.edit_project_info_button.setEnabled(enabled and not archived)
        self.project_prompt_button.setEnabled(enabled and not archived and state_valid)
        current_tab = self.documents.currentWidget()
        has_obsidian_document = any(
            current_tab is page for page in (self.overview, self.task_page, self.milestones_page, self.project_docs)
        ) and not (current_tab is self.project_docs and self.project_docs.currentIndex() == 3)
        self.obsidian_button.setEnabled(enabled and has_obsidian_document)
        self.obsidian_button.setText("在 Obsidian 中编辑")
        self.obsidian_button.setToolTip("打开当前标签对应的项目原文；任务标签打开任务索引")
        imported = False
        legacy_documents = set()
        if item:
            try:
                from ..project_task_documents import legacy_project_task_documents_exist, project_task_documents_imported
                workspace = self.workspace()
                imported = project_task_documents_imported(self.window.store, workspace, item["id"])
                if legacy_project_task_documents_exist(workspace, item["id"]):
                    for source, relative_path in self.task_document_paths.items():
                        path = workspace.path(item["path"] + "/" + relative_path)
                        if path.is_file():
                            legacy_documents.add(source)
            except (OSError, UserError):
                imported = False
        self.import_legacy_project_tasks_button.setVisible(bool(item) and bool(legacy_documents) and not imported)
        self.import_legacy_project_tasks_button.setEnabled(enabled and not archived and not imported)
        for source in self.task_lists:
            self.task_add_buttons[source].setEnabled(enabled and not archived)
            task = self.selected_shared_task(source)
            mutable = enabled and not archived and bool(task and task.get("id"))
            self.task_edit_buttons[source].setEnabled(mutable)
            self.task_status_buttons[source].setEnabled(mutable)
            self.task_delete_buttons[source].setEnabled(mutable)
            self.task_document_buttons[source].setVisible(source in legacy_documents)
            self.task_document_buttons[source].setEnabled(enabled and source in legacy_documents)
        self.task_add_buttons["ai"].setEnabled(enabled and not archived)
        self.open_notes_directory_button.setEnabled(enabled)
        self.refresh_notes_button.setEnabled(enabled)
        self.new_note_button.setEnabled(enabled and not archived)
        self.inline_note_button.setEnabled(enabled and not archived and self.notes_list.currentItem() is not None)
        self.edit_note_button.setEnabled(enabled and self.documents.currentIndex() == 1 and self.notes_list.currentItem() is not None)
        uploading_documents = bool(self.project_document_upload_jobs)
        self.upload_project_material_button.setEnabled(enabled and not archived and not uploading_documents)
        if self.overview.upload_document_button:
            self.overview.upload_document_button.setEnabled(enabled and not archived and not uploading_documents)
        if self.overview.upload_resource_button:
            self.overview.upload_resource_button.setEnabled(enabled and not archived)
        self.refresh_project_material_button.setEnabled(enabled)
        self.open_project_material_directory_button.setEnabled(enabled and not archived)
        has_material = bool(self.project_material_list.currentItem()) and enabled
        self.open_project_material_button.setEnabled(has_material)
        selected_relative = self.project_material_list.currentItem().data(Qt.ItemDataRole.UserRole) if has_material else ""
        self.edit_project_material_button.setEnabled(bool(selected_relative and selected_relative.lower().endswith(".md")))
        self.create_milestones_button.setEnabled(enabled and not archived and not self.milestones_document_exists)
        self.edit_milestones_button.setEnabled(enabled and self.milestones_document_exists)
        self.open_project_button.setEnabled(enabled)
        has_repository = self.git_repositories is None or bool(self.git_repositories)
        self.git_status_button.setText("Git 状态" if has_repository else "初始化 Git")
        self.git_status_button.setEnabled(bool(item and not self.git_worker))
        self.git_status_button.setToolTip(
            "查看状态并管理暂存、提交、拉取和推送" if has_repository else "在项目目录初始化 Git 仓库"
        )
        self.archive_button.setEnabled(enabled and state_valid)
        self.archive_button.setText("重新启用" if archived else "归档项目")
        self.archive_button.setToolTip(
            "只有有效的项目 STATUS.md 才能归档或重新启用。" +
            ("\n" + str(item.get("error", "请修复项目目录或登记路径后刷新。"))
             if item and not state_valid else "")
        )
        self.delete_project_button.setEnabled(enabled and state_valid and archived)
        self.delete_project_button.setToolTip(
            "仅允许删除已归档项目；点击后还需准确输入项目名称确认。" if state_valid else
            "项目状态待检查，修复项目目录或登记路径后再操作。"
        )

    def selection_changed(self):
        if self.move_watchers:
            return
        self.project_detail_timer.stop()
        self.pending_project_detail_start = None
        if self.project_detail_task_id:
            running = self.window.jobs.get(self.project_detail_task_id)
            if running:
                running[0].context.cancel.set()
            self.project_detail_task_id = ""
        item = self.selected()
        self.git_repositories = None
        cache_key = (str(self.root().resolve()), item["id"]) if item and self.root() else None
        cached_report = copy.deepcopy(self.project_detail_cache.get(cache_key)) if cache_key else None
        if cache_key and cached_report is not None:
            self.project_detail_cache.pop(cache_key, None)
            self.project_detail_cache[cache_key] = copy.deepcopy(cached_report)
        if cached_report is None:
            self.status_text, self.task_text = "", ""
            self.document_errors = {}
            self.milestones_document_exists = False
            self.milestones_view.setPlainText("读取中…" if item else "选择项目后查看其里程碑文档。")
            self.project_details = {}
        self.update_selection_actions()
        self.document_version += 1
        version = self.document_version
        self.note_preview_version += 1
        self.project_material_preview_version += 1
        self.notes_project_id = item["id"] if item else ""
        self.notes_directory_label.setText(str(Path(item["directory"]) / "笔记") if item else "选择项目后打开它的笔记目录。")
        if cached_report is None:
            self.notes_list.clear()
            self.notes_preview.setPlainText("选择左侧笔记进行预览。" if item else "选择项目后查看笔记。")
            self.project_material_list.clear()
            self.project_material_rows = []
            self.project_material_directory = ""
            self.project_material_directory_label.setText(str(Path(item["directory"]) / "文档") if item else "选择项目后查看项目资料目录。")
            self.project_material_preview.setPlainText("选择项目后查看项目资料。" if item else "选择左侧项目查看资料。")
            for view in self.views:
                view.setPlainText("选择项目查看管理资料。" if not item else "读取中…")
            self.project_name.setText(item["name"] if item else "选择一个项目")
            state_label = ("已归档" if item and item["state"] == "archived" else
                           "状态待检查" if item and item["state"] == "unknown" else "进行中")
            self.project_identity.setText(item["id"] + " · " + state_label if item else "从左侧选择，或新建项目")
            self.project_meta.clear()
            for value in self.hero_info_values.values():
                value.setText("读取中…" if item else "—")
            self.hero.set_cover("")
            self.description_label.setText((item.get("description", "").strip() or "尚未编写项目介绍。点击“编辑项目信息”，补充项目背景、目标或说明。") if item else "选择项目后，可在这里编写项目介绍。")
            for metric in self.metrics:
                metric.value.setText("—")
                metric.note.setText("读取中…" if item else "请选择项目")
            self.hero.fit()
        if not item:
            self.status_text, self.task_text = "", ""
            for task_list in self.task_lists.values():
                task_list.clear()
            self.summary_view.clear()
            self.related_list.clear()
            self.project_knowledge = []
            self.overview.set_data(
                {"checklist": {"tasks": [], "total": 0, "done": 0, "in_progress": 0,
                               "pending": 0, "overdue": 0, "progress": 0},
                 "recent": "请选择项目查看动态。"},
                {}, {}, [], [], [], False,
            )
            self.milestones_view.setPlainText("选择项目后查看其里程碑文档。")
            return
        root, identity = self.root(), item["id"]
        record_use = item.get("state") != "archived" and identity != self.last_used_project_id
        def read(context):
            return ProjectDetailReader(self.window.store, root).read(
                identity, item["state"], tuple(self.document_paths), context, record_use=record_use
            )
        displayed_report = None
        def show(report):
            nonlocal displayed_report
            if self.closed or version != self.document_version:
                return
            if displayed_report == report:
                return
            displayed_report = report
            self.project_detail_cache[cache_key] = copy.deepcopy(report)
            while len(self.project_detail_cache) > 6:
                self.project_detail_cache.pop(next(iter(self.project_detail_cache)))
            self.project_name.setText(item["name"])
            self.description_label.setText(item.get("description", "").strip() or "尚未编写项目介绍。点击“编辑项目信息”，补充项目背景、目标或说明。")
            self.document_errors = {i: d["error"] for i, d in enumerate(report["documents"]) if d.get("error")}
            milestone_doc = report["milestones"]
            self.milestones_document_exists = bool(milestone_doc.get("exists"))
            self.milestones_view.document().setBaseUrl(QUrl.fromLocalFile(str(Path(milestone_doc["path"]).parent) + os.sep))
            if milestone_doc.get("error"):
                self.milestones_view.setPlainText("里程碑文档读取失败：" + milestone_doc["error"])
            elif self.milestones_document_exists:
                self.milestones_view.setMarkdown(milestone_doc["text"])
            else:
                self.milestones_view.setPlainText("还没有里程碑文档。点击“创建空白里程碑文档”后，可在 Obsidian 中自行编写。")
            self.status_text, self.task_text = report["documents"][0]["text"], report["documents"][1]["text"]
            self.project_details = report["details"]
            if 0 in self.document_errors:
                self.status_text = ""
            if report["used_at"]:
                self.last_used_project_id = identity
                for project in self.project_rows:
                    if project["id"] == identity:
                        project["last_used_at"] = report["used_at"]
                        break
            self.project_knowledge = report["knowledge_resources"]
            self.populate_project_documents(report["project_documents"])
            if report.get("project_documents_error"):
                self.project_material_preview.setPlainText("读取项目资料目录失败：" + report["project_documents_error"])
            self.populate_notes(report["notes"], report.get("notes_error", ""))
            blocks = report["summaries"]
            self.summary_view.setMarkdown("\n\n---\n\n".join(reversed(blocks)) if blocks else "还没有阶段总结。可在 Obsidian 中打开 HANDOFF.md 并自行补充。")
            self.render_related(item)
            snapshot = report["snapshot"]
            self.populate_shared_project_task_lists(item, tasks=report["linked_tasks"])
            phase = self.project_details["phase"]
            lifecycle = ("已归档" if item["state"] == "archived" else
                         "状态待检查" if item["state"] == "unknown" else "进行中")
            self.project_identity.setText(item["id"] + " · " + lifecycle)
            project_info = item.get("project_info", {})
            if not isinstance(project_info, dict):
                project_info = {}
            date_range = " – ".join(str(value or "未设置") for value in
                                    (project_info.get("start_date", ""), project_info.get("end_date", "")))
            member_values = project_info.get("members", [])
            if not isinstance(member_values, list):
                member_values = []
            members = "、".join(str(name) for name in member_values if isinstance(name, str))
            tag_values = project_info.get("tags", [])
            if not isinstance(tag_values, list):
                tag_values = []
            tags = "  ·  ".join("#" + str(tag) for tag in tag_values if isinstance(tag, str) and tag)
            facts = {"phase": phase or "待明确", "owner": project_info.get("owner") or "未设置",
                     "period": date_range if project_info.get("start_date") or project_info.get("end_date") else "未设置",
                     "priority": {"high": "高", "medium": "中", "low": "低"}.get(project_info.get("priority", ""), "未设置")}
            for key, value in facts.items():
                self.hero_info_values[key].setText(value)
                self.hero_info_values[key].setToolTip(value)
            repository = project_info.get("repository", "")
            self.project_meta.setText("  ·  ".join(part for part in (("成员：" + members) if members else "", tags,
                                                                     ("仓库：" + repository) if repository else "",
                                                                     "最近更新 " + snapshot["updated"]) if part))
            self.hero.set_cover(project_info.get("cover_image", ""))
            if report["errors"]:
                self.project_meta.setText("部分资料需要检查：" + "；".join(report["errors"]))
            linked = [self.related_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.related_list.count())]
            linked_ids = {value[1] for value in linked if value and value[0] == "knowledge"}
            overview_resources = [resource for resource in self.project_knowledge if resource["id"] in linked_ids]
            resources = len(overview_resources)
            checklist = snapshot["checklist"]
            values = (phase, f'{checklist["done"]} / {checklist["total"]}' if checklist["total"] else "任务入口",
                f'{snapshot["logs"]} 条', f'{resources} 项')
            notes = ("可自定义阶段", "清单完成 / 总数" if checklist["total"] else "查看任务原记录",
                "日期日志，保留原文", "关联知识库条目")
            for metric, value, note in zip(self.metrics, values, notes):
                metric.value.setText(value[:7] + "…" if metric is self.metrics[0] and len(value) > 7 else value)
                metric.value.setToolTip(value)
                metric.note.setText(note)
            self.hero.fit()
            self.update_selection_actions()
            self.overview.set_data(snapshot, item, project_info, overview_resources,
                                   self.project_details.get("agents", []),
                                   report["milestone_rows"], self.milestones_document_exists,
                                   report["project_documents"].get("documents", []), milestone_doc.get("text", ""))
            self.update_selection_actions()
            for index, (view, document) in enumerate(zip(self.views, report["documents"])):
                view.document().setBaseUrl(QUrl.fromLocalFile(str(Path(document["path"]).parent) + os.sep))
                if index in self.document_errors:
                    view.setPlainText("这份文档暂时无法读取：" + self.document_errors[index] + "\n请打开项目文件夹检查；其他资料仍可查看。原文件未改动。")
                elif index == 2:
                    self.log_view.set_text(document["text"])
                else:
                    view.setMarkdown(document["text"])
        # A dedicated read does not compete with the selected lifecycle task's resource identity.
        def start_loading():
            current = self.selected()
            if not current or current["id"] != identity or version != self.document_version:
                return ""
            return self.window.submit(None, "读取项目管理文档", read, show, persist_result=False, record_history=False)

        self.pending_project_detail_start = start_loading
        if cached_report is not None:
            show(cached_report)
        self.project_detail_timer.start()

    def _start_pending_project_details(self):
        start = self.pending_project_detail_start
        self.pending_project_detail_start = None
        if start and not self.closed:
            self.project_detail_task_id = start()

    def stop_updates(self):
        self.closed = True
        self.project_detail_timer.stop()
        self.pending_project_detail_start = None
        if self.project_detail_task_id:
            running = self.window.jobs.get(self.project_detail_task_id)
            if running:
                running[0].context.cancel.set()
        if self.git_worker:
            self.git_worker.cancel.set()

    def populate_notes(self, notes, error=""):
        selected = self.notes_list.currentItem()
        preferred = getattr(self, "preferred_note", "") or (selected.data(Qt.ItemDataRole.UserRole) if selected else "")
        self.preferred_note = ""
        self.notes_list.blockSignals(True)
        self.notes_list.clear()
        for note in notes:
            entry = QListWidgetItem(note["name"])
            entry.setData(Qt.ItemDataRole.UserRole, note["relative"])
            entry.setToolTip(note["relative"])
            self.notes_list.addItem(entry)
        self.notes_list.blockSignals(False)
        if error:
            self.notes_preview.setPlainText("读取项目笔记目录失败：" + error)
        elif notes:
            self.notes_list.setCurrentRow(next((i for i, note in enumerate(notes) if note["relative"] == preferred), 0))
        else:
            self.notes_preview.setPlainText("这个项目的“笔记”目录中还没有 Markdown 笔记。")
        self.update_selection_actions()

    def refresh_notes(self):
        item = self.selected()
        if not item:
            return
        identity = item["id"]
        self.submit("刷新项目笔记列表",
                    lambda context: self.workspace().notes(identity, context)["notes"],
                    lambda notes: self.populate_notes(notes) if self.notes_project_id == identity else None,
                    persist_result=False)

    def preview_selected_note(self, current, _previous=None):
        if getattr(self, "note_editor_open", False):
            return
        item = self.selected()
        relative = current.data(Qt.ItemDataRole.UserRole) if current else ""
        if not item or not relative:
            self.update_selection_actions()
            return
        identity = item["id"]
        self.note_preview_version += 1
        version = self.note_preview_version
        self.notes_preview.setPlainText("读取笔记预览…")
        def show(report):
            selected = self.notes_list.currentItem()
            current_project = self.selected()
            if (self.closed or version != self.note_preview_version or self.notes_project_id != identity
                    or not current_project or current_project["id"] != identity
                    or not selected or selected.data(Qt.ItemDataRole.UserRole) != relative):
                return
            self.notes_preview.document().setBaseUrl(QUrl.fromLocalFile(str(Path(report["path"]).parent) + os.sep))
            self.notes_preview.setMarkdown(markdown_preserving_linebreaks(note_header(report["text"])[1]))
            self.update_selection_actions()
        self.window.submit(None, "预览项目笔记", lambda _context: self.workspace().note_document(identity, relative),
                    show, persist_result=False)

    def new_project_note(self):
        item = self.selected()
        if not item:
            return
        title, ok = QInputDialog.getText(self, "新建笔记", "笔记名称")
        if not ok or not title.strip():
            return
        workspace, identity = self.workspace(), item["id"]
        def created(report):
            self.preferred_note = report["relative"]
            self.edit_project_note(relative=report["relative"], identity=identity)
        self.submit("新建项目笔记", lambda context: workspace.create_note(identity, title, context),
                    created, persist_result=False)

    def edit_project_note(self, _checked=False, *, relative=None, identity=None):
        item = self.selected()
        note = self.notes_list.currentItem()
        if not item or (identity and item["id"] != identity):
            return
        relative = relative or (note.data(Qt.ItemDataRole.UserRole) if note else "")
        if not relative:
            return
        identity, workspace = item["id"], self.workspace()
        def edit(report):
            current = self.selected()
            if self.closed or not current or current["id"] != identity:
                return
            self.note_editor_open = True
            dialog = QDialog(self)
            dialog.setWindowTitle("编辑笔记 · " + Path(relative).stem)
            dialog.resize(780, 600)
            layout = QVBoxLayout(dialog)
            editor = QTextEdit()
            editor.setAcceptRichText(False)
            editor.setPlainText(report["text"])
            layout.addWidget(editor, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            pending = {"id": ""}
            def finished(task_id, _state):
                if task_id == pending["id"]:
                    pending["id"] = ""
                    buttons.setEnabled(True)
                    editor.setReadOnly(False)
            self.window.task_completed.connect(finished)
            def save():
                text = editor.toPlainText()
                # Keep the editor open, including its draft, if conflict checks fail.
                buttons.setEnabled(False)
                editor.setReadOnly(True)
                def saved(_result):
                    dialog.accept()
                    self.preferred_note = relative
                pending["id"] = self.submit("保存项目笔记", lambda context: workspace.save_note(identity, relative, text, report["text"], context),
                                      saved, persist_result=False)
                if not pending["id"]:
                    buttons.setEnabled(True)
                    editor.setReadOnly(False)
            buttons.accepted.connect(save)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            try:
                dialog.exec()
            finally:
                self.note_editor_open = False
                self.window.task_completed.disconnect(finished)
                self.refresh_notes()
        self.submit("读取笔记原文", lambda _: workspace.note_document(identity, relative), edit, persist_result=False)

    def open_selected_note(self):
        item = self.selected()
        note = self.notes_list.currentItem()
        relative = note.data(Qt.ItemDataRole.UserRole) if note else ""
        if not item or not relative:
            return
        identity = item["id"]
        workspace = self.workspace()
        self.submit("在 Obsidian 中编辑项目笔记",
                    lambda context: project_uri(workspace, identity, relative),
                    self.launch_obsidian, persist_result=False)

    def open_notes_directory(self):
        item = self.selected()
        if not item:
            return
        configured_vault = self.window.service.setting("obsidian_vault_root", "")
        if not configured_vault:
            QMessageBox.information(self, "设置 Obsidian 知识库", "请先到“设置”选择 Obsidian 知识库根目录，再打开项目笔记。")
            return
        workspace = self.workspace()
        def open_notes(context):
            report = project_notes_uri(workspace, item["id"], Path(configured_vault))
            context.checkpoint()
            workspace.ensure_notes_directory(item["id"], context)
            return report
        self.submit("在 Obsidian 中打开项目笔记", open_notes, self.launch_obsidian, persist_result=False)

    def launch_obsidian(self, report):
        if not QDesktopServices.openUrl(QUrl(report["uri"])):
            QMessageBox.warning(self, "未能打开 Obsidian", "请安装并运行 Obsidian 一次以注册 obsidian:// 接口。原文件仍在：\n" + report["path"])

    def open_in_obsidian(self):
        item = self.selected()
        current_tab = self.documents.currentWidget()
        if not item or not any(current_tab is page for page in (self.overview, self.task_page, self.milestones_page, self.project_docs)):
            return
        if current_tab is self.overview:
            relative = "agent/STATUS.md"
        elif current_tab is self.task_page:
            relative = "agent/TASKS.md"
        elif current_tab is self.project_docs:
            relative = "agent/STATUS.md" if self.project_docs.currentIndex() == 0 else "agent/HANDOFF.md"
        else:
            relative = "agent/MILESTONES.md"
        workspace = self.workspace()
        self.submit("定位 Obsidian 项目文档", lambda context: project_uri(workspace, item["id"], relative), self.launch_obsidian, persist_result=False)

    def create_milestones_document(self):
        item = self.selected()
        if not item or self.milestones_document_exists:
            return
        self.submit("创建空白里程碑文档",
                    lambda context: self.workspace().create_milestones_document(item["id"], context),
                    lambda _report: self.selection_changed(), persist_result=False)

    def create_project(self, legacy=None):
        if not self.root():
            self.choose_workspace()
            return
        dialog = ProjectDialog(self, legacy if isinstance(legacy, dict) else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, goal, entry = dialog.name.text().strip(), dialog.goal.toPlainText().strip(), dialog.entry.isChecked()
        folder_name = dialog.folder_name.text().strip()
        workspace = self.workspace()
        def completed(report):
            self.search.clear()
            self.set_state_filter("active")
            self.preferred_project = report["project_id"]
            self.refresh()
            ProjectPromptDialog(self, {"id": report["project_id"], "name": name,
                                       "directory": report["directory"]}, initial=True).exec()
        initialization = dialog.initialization_selection
        self.submit("初始化新项目", lambda context: workspace.create(name, goal, entry, context, folder_name,
                                                                    initialization), completed)

    def open_project(self):
        item = self.selected()
        if item:
            self.window.open_path(Path(item["directory"]))

    def inspect_git_status(self):
        item = self.selected()
        if not item or self.git_worker:
            return
        version = self.document_version
        worker = ReadWorker(lambda cancel: project_git_repositories(item["directory"]))
        self.git_worker = worker
        self.git_status_button.setEnabled(False)
        self.git_status_button.setText("读取中…")
        def discovered(repositories):
            self.git_worker = None
            if self.closed:
                return
            if version != self.document_version:
                self.update_selection_actions()
                return
            if repositories is None:
                self.update_selection_actions()
                QMessageBox.warning(self, "Git 状态", "项目目录暂时无法读取，请刷新重试。")
                return
            self.git_repositories = repositories
            self.update_selection_actions()
            self._inspect_git_status_ready(item, repositories)
        worker.signals.finished.connect(discovered)
        self.window.pool.start(worker)

    def _inspect_git_status_ready(self, item, repositories):
        project_info = item.get("project_info", {})
        if not isinstance(project_info, dict):
            project_info = {}
        registered_repository = project_info.get("repository", "")
        if not repositories:
            if QMessageBox.question(
                self, "初始化 Git", f"要在项目目录初始化 Git 仓库吗？\n\n{item['directory']}\n\n初始化不会自动暂存或提交文件。"
            ) != QMessageBox.StandardButton.Yes:
                return
            self.run_git_action("init", Path(item["directory"]), None, "", registered_repository)
            return
        repository = repositories[0]
        if len(repositories) > 1:
            choices = [path.relative_to(Path(item["directory"]).resolve()).as_posix() or "." for path in repositories]
            selected, accepted = QInputDialog.getItem(self, "选择 Git 仓库", "请选择要查看的仓库：", choices, 0, False)
            if not accepted:
                return
            repository = repositories[choices.index(selected)]
        worker = ReadWorker(lambda cancel: read_project_git_status(repository, cancel, registered_repository))
        self.git_worker = worker
        self.git_status_button.setEnabled(False)
        self.git_status_button.setText("读取中…")

        def finished(report):
            self.git_worker = None
            if self.closed:
                return
            self.git_status_button.setText("Git 状态")
            self.update_selection_actions()
            if report is None:
                QMessageBox.warning(self, item["name"] + " · Git 状态",
                                    "没有收到 Git 状态读取结果，请重试或检查项目目录和 Git 安装。")
                return
            if report["ok"]:
                repo_name = repository.relative_to(Path(item["directory"]).resolve()).as_posix()
                title = item["name"] if repo_name == "." else item["name"] + " · " + repo_name
                dialog = ProjectGitStatusDialog(
                    self, title, repository, report,
                    lambda action, message="", path="": self.run_git_action(action, repository, dialog, message,
                                                                                registered_repository, path),
                )
                dialog.exec()
            else:
                QMessageBox.warning(self, item["name"] + " · Git 状态", report["text"])

        worker.signals.finished.connect(finished)
        self.window.pool.start(worker)

    def run_git_action(self, action, repository, dialog, commit_message, registered_repository="", file_path=""):
        if self.git_worker:
            return
        item = self.selected()
        if not item:
            return
        token = ""
        if action in {"fetch", "pull", "push"}:
            try:
                token = self.window.service.secrets.get("git:https-token") or ""
            except UserError as exc:
                QMessageBox.warning(self, "Git 凭据不可用", str(exc))
                return
        def operation(cancel):
            if action == "refresh":
                status = read_project_git_status(repository, cancel, registered_repository)
                return {"ok": bool(status and status.get("ok")), "message": "状态已刷新", "status": status}
            return run_project_git_action(repository, action, cancel, commit_message, token, registered_repository,
                                          file_path)

        worker = ReadWorker(operation)
        self.git_worker = worker
        self.git_status_button.setEnabled(False)
        if dialog:
            dialog.set_busy(True)
        else:
            self.git_status_button.setText("Git 操作中…")

        def finished(report):
            self.git_worker = None
            if self.closed:
                return
            self.update_selection_actions()
            if report is None:
                if dialog and dialog.isVisible():
                    dialog.set_busy(False, "Git 操作没有返回结果，请刷新状态后检查仓库。")
                else:
                    QMessageBox.warning(self, "Git 操作", "操作没有返回结果，请刷新状态后检查仓库。")
                return
            if not report.get("ok"):
                if dialog and dialog.isVisible():
                    dialog.set_busy(False, report.get("message", "Git 操作未完成。"))
                else:
                    QMessageBox.warning(self, "Git 操作未完成", report.get("message", "请检查 Git 安装、网络和仓库配置。"))
                return
            current = report.get("status")
            if dialog and dialog.isVisible():
                dialog.set_busy(False, report.get("message", "操作完成。"))
                if current and current.get("ok"):
                    if action == "commit":
                        dialog.commit_input.clear()
                    dialog.set_report(current, report.get("message", "操作完成。"))
            elif current and current.get("ok"):
                repo_name = repository.relative_to(Path(item["directory"]).resolve()).as_posix()
                title = item["name"] if repo_name == "." else item["name"] + " · " + repo_name
                new_dialog = ProjectGitStatusDialog(
                    self, title, repository, current,
                    lambda next_action, message="", path="": self.run_git_action(next_action, repository, new_dialog,
                                                                                    message, registered_repository, path),
                )
                new_dialog.exec()
            else:
                QMessageBox.information(self, "Git 操作已完成", report.get("message", "操作完成。"))

        worker.signals.finished.connect(finished)
        self.window.pool.start(worker)

    def move_project(self):
        item = self.selected()
        if not item:
            return
        if item.get("state") == "unknown":
            detail = item.get("error") or (str(Path(item["directory"]) / "agent/STATUS.md"))
            QMessageBox.warning(
                self, "无法归档项目",
                "项目状态文档无法读取，未执行归档。请恢复项目工作区或修正登记路径后刷新。\n\n" + str(detail),
            )
            return
        workspace = self.workspace()
        resume = item["state"] == "archived"
        action = "重新启用" if resume else "归档"
        effect = ("重新建立 Obsidian 项目入口" if resume else "从 Obsidian 的 03-项目目录隐藏项目入口")
        message = f"{action}“{item['name']}”吗？\n\n将更新 STATUS.md 并{effect}。真实项目文件夹、编号和登记路径保留。"
        if QMessageBox.question(self, action + "项目", message) != QMessageBox.StandardButton.Yes:
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("再次确认" + action)
        confirm.setText(f"请再次确认：{action}项目“{item['name']}”？")
        confirm.setInformativeText("只会隐藏或恢复经核验的 Obsidian 文档入口，不会移动或删除真实项目文件。")
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        def completed(_report):
            if resume:
                self.last_used_project_id = ""
            self.set_state_filter("active" if resume else "archived")
            self.preferred_project = item["id"]
            self.refresh()
            self.window.today_page.refresh()
        self.submit(action + "项目", lambda context: workspace.set_archived(item["id"], not resume, context), completed)

    def delete_project(self):
        item = self.selected()
        if not item:
            return
        if item.get("state") == "unknown":
            detail = item.get("error") or (str(Path(item["directory"]) / "agent/STATUS.md"))
            QMessageBox.warning(
                self, "无法删除项目",
                "项目状态文档无法读取，未执行删除。请恢复项目工作区或修正登记路径后刷新。\n\n" + str(detail),
            )
            return
        if item["state"] != "archived":
            QMessageBox.information(self, "请先归档项目", "为避免误删正在使用的项目，请先归档，再执行删除。")
            return
        task_count, plan_count = ActionCenter(self.window.store).project_linked_record_counts(
            item["id"], str(self.root()))
        dialog = DeleteProjectDialog(self, item, (task_count, plan_count))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        confirmation = dialog.confirmation.text()
        workspace = self.workspace()
        owner = self.owner()
        if owner and self.window.is_busy(owner.id):
            QMessageBox.information(self, "项目操作进行中", "请等待当前工作区操作完成后再删除。")
            return

        self.pause_move_watches()
        def completed(report):
            if self.closed:
                return
            QMessageBox.information(
                self, "项目已删除",
                f"“{report['name']}”（{report['project_id']}）已从项目登记中移除。\n"
                "项目目录和已登记的 Obsidian 入口已删除；编号仍保留。\n"
                "统一任务、计划和资源记录未删除。",
            )
        task_id = self.submit(
            "删除项目",
            lambda context: workspace.delete_project(item["id"], confirmation, context),
            completed,
        )
        if task_id:
            self.move_task = task_id
            self.move_selection = None
        else:
            self.restore_move_watches()

    def pause_move_watches(self):
        # Windows directory notification handles prevent rename. Today also
        # watches project STATUS files, including while its page is hidden.
        self.document_version += 1
        for watcher in self.window.findChildren(QFileSystemWatcher):
            paths = watcher.files() + watcher.directories()
            self.move_watchers.append((watcher, paths, watcher.signalsBlocked()))
            watcher.setProperty("projectMovePaused", True)
            watcher.blockSignals(True)
            if paths:
                watcher.removePaths(paths)
        self.update_selection_actions()

    def restore_move_watches(self):
        for watcher, paths, blocked in self.move_watchers:
            watcher.setProperty("projectMovePaused", False)
            available = [p for p in paths if Path(p).exists()]
            if available:
                watcher.addPaths(available)
            watcher.blockSignals(blocked)
        self.move_watchers = []
        self.move_task = ""
        self.update_selection_actions()

    def move_finished(self, identity, state):
        if identity != self.move_task:
            return
        selection = self.move_selection
        self.move_selection = None
        self.restore_move_watches()
        if self.closed:
            return
        if state == "success" and selection:
            self.set_state_filter("active" if selection[1] else "archived")
            self.preferred_project = selection[0]
        self.refresh()
        self.window.today_page.refresh()
