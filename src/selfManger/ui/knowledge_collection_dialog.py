"""Input-only dialog for collecting a knowledge resource."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QSizePolicy,
    QTabWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget, QPushButton,
)

from ..knowledge_collection import KnowledgeCollectionRequest
from ..knowledge_resources import topic_name

QUICK_CAPTURE_FOLDER = "02-知识/00-收件箱"


def _button(text, callback):
    button = QPushButton(text)
    button.clicked.connect(callback)
    return button


class KnowledgeCollectionDialog(QDialog):
    """Own the collection form and turn validated widget state into a DTO."""

    def __init__(self, parent, *, folders, capture_folder, kind="link", prefill="",
                 sources=None, project_id="", scope_project="", scope_agent="",
                 default_folder=None):
        super().__init__(parent)
        self.setObjectName("KnowledgeCollectDialog")
        self.setWindowTitle("添加资料")
        self.resize(640, 660)
        self.setMinimumSize(520, 440)
        self.project_id = project_id
        self.scope_project = scope_project
        self.scope_agent = scope_agent
        self.selected_files = list(sources or [])
        self.directory_path = prefill if kind == "directory" else ""
        self._build_form(folders, capture_folder, kind, prefill, default_folder)

    def _build_form(self, folders, capture_folder, kind, prefill, default_folder):
        outer = QVBoxLayout(self)
        self.form_content = QWidget()
        layout = QVBoxLayout(self.form_content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.form_content)
        outer.addWidget(scroll, 1)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        header = QFrame()
        header.setObjectName("KnowledgeCollectHeader")
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 11, 14, 11)
        header_layout.setSpacing(3)
        header_title = QLabel("新建笔记" if kind == "note" else "添加资料")
        header_title.setObjectName("KnowledgeCollectTitle")
        header_layout.addWidget(header_title)
        intro = QLabel("选择资料类型和保存位置。文件默认复制，原件保留；也可改为引用原文件。")
        intro.setObjectName("KnowledgeCollectDescription")
        intro.setWordWrap(True)
        intro.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout.addWidget(intro)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("KnowledgeCollectTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(False)
        link_tab, file_tab, note_tab, directory_tab = (QWidget() for _ in range(4))
        link_form, file_form, note_form = (QFormLayout(w) for w in (link_tab, file_tab, note_tab))
        for form_layout in (link_form, file_form, note_form):
            form_layout.setContentsMargins(12, 10, 12, 10)
            form_layout.setHorizontalSpacing(12)
            form_layout.setVerticalSpacing(10)
            form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.url = QLineEdit(prefill if kind == "link" else "")
        self.url.setPlaceholderText("粘贴网页、GitHub 或云盘分享链接")
        link_form.addRow("链接", self.url)
        self.file_summary = QLabel("尚未选择文件")
        self.file_summary.setWordWrap(True)
        self._update_files()
        file_form.addRow(self.file_summary)
        file_form.addRow(_button("选择文件…", self._choose_files))
        self.mode = QComboBox()
        self.mode.addItem("复制到知识库 · 原件保留", True)
        self.mode.addItem("引用原文件 · 仅保存引用记录", False)
        file_form.addRow("存储方式", self.mode)
        self.storage_hint = QLabel()
        self.storage_hint.setWordWrap(True)
        file_form.addRow(self.storage_hint)
        self.mode.currentIndexChanged.connect(self._update_storage_hint)
        self._update_storage_hint()
        self.grouping = QComboBox()
        self.grouping.addItem("多个文件放入同名文件夹", True)
        self.grouping.addItem("分别添加到当前目录", False)
        file_form.addRow("组织方式", self.grouping)
        directory_form = QFormLayout(directory_tab)
        directory_form.setContentsMargins(12, 10, 12, 10)
        directory_form.setHorizontalSpacing(12)
        directory_form.setVerticalSpacing(10)
        directory_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        directory_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.directory_label = QLabel(self.directory_path or "整个目录复制到所选保存位置，保留子目录结构，原目录不变。")
        self.directory_label.setWordWrap(True)
        directory_form.addRow(self.directory_label)
        directory_form.addRow(_button("选择目录…", self._choose_directory))
        self.body = QTextEdit()
        self.body.setAcceptRichText(False)
        self.body.setPlaceholderText("记录想法、代码或使用方法，支持 Markdown")
        if kind == "note":
            self.body.setPlainText(prefill)
        note_form.addRow(self.body)
        for widget, label in ((link_tab, "链接"), (file_tab, "文件"), (note_tab, "笔记"), (directory_tab, "整个目录")):
            self.tabs.addTab(widget, label)
        self.tabs.setCurrentIndex({"link": 0, "file": 1, "note": 2, "directory": 3}.get(kind, 0))
        heights = {0: 112, 1: 252, 2: 222, 3: 154}
        self.tabs.setFixedHeight(heights[self.tabs.currentIndex()])
        self.tabs.currentChanged.connect(lambda row: self.tabs.setFixedHeight(heights[row]))
        layout.addWidget(self.tabs)

        form = QFormLayout()
        form.setContentsMargins(12, 2, 12, 2)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.title = QLineEdit()
        self.title.setMaxLength(500)
        self.title.setPlaceholderText("选填 · 链接可用网站名称，文件可用原名")
        form.addRow("标题（可选）", self.title)
        form.addRow("标题（可选）", self.title)
        self.summary, self.tags = QLineEdit(), QLineEdit()
        self.summary.setMaxLength(500)
        self.summary.setPlaceholderText("一句话说明它有什么用（选填）")
        self.tags.setPlaceholderText("选填 · 逗号分隔")
        destination = default_folder if default_folder is not None else capture_folder
        self.destination = QComboBox()
        self.destination.setObjectName("KnowledgeDestination")
        self.destination.addItem("收件箱 · 稍后整理", QUICK_CAPTURE_FOLDER)
        self.destination.addItem("知识库根目录", "02-知识")
        for folder in folders:
            if folder.startswith("02-知识/") and folder != QUICK_CAPTURE_FOLDER and Path(folder).name != "附件":
                self.destination.addItem(" › ".join(topic_name(p) for p in folder.split("/")[1:]), folder)
        if self.destination.findData(destination) < 0:
            self.destination.addItem(destination, destination)
        self.destination.setCurrentIndex(self.destination.findData(destination))
        destination_form = QFormLayout()
        destination_form.setContentsMargins(12, 2, 12, 2)
        destination_form.addRow("保存位置", self.destination)
        layout.insertLayout(1, destination_form)
        layout.addLayout(form)
        self.extra_button = QToolButton()
        self.extra_button.setText("用途与标签（可选）")
        self.extra_button.setCheckable(True)
        extra_fields = QWidget()
        extra_form = QFormLayout(extra_fields)
        extra_form.addRow("用途", self.summary)
        extra_form.addRow("标签", self.tags)
        extra_fields.hide()
        self.extra_button.toggled.connect(extra_fields.setVisible)
        layout.addWidget(self.extra_button)
        layout.addWidget(extra_fields)
        layout.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.setObjectName("KnowledgeCollectActions")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存资源")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        self.buttons.rejected.connect(self.reject)
        footer = QFrame()
        footer.setObjectName("KnowledgeCollectFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.buttons)
        outer.addWidget(footer)

    def _update_files(self):
        text = f"已选择 {len(self.selected_files)} 个文件"
        if self.selected_files:
            text += " · " + Path(self.selected_files[0]).name
        self.file_summary.setText(text)
        self.file_summary.setToolTip("\n".join(self.selected_files))

    def _choose_files(self):
        values, _ = QFileDialog.getOpenFileNames(self, "选择资料", "", "所有文件 (*)")
        if values:
            self.selected_files[:] = values
            self._update_files()

    def _choose_directory(self):
        selected = QFileDialog.getExistingDirectory(self, "选择整个资料目录")
        if selected:
            self.directory_path = selected
            self.directory_label.setText(selected)

    def _update_storage_hint(self):
        self.storage_hint.setText("文件副本保存到下方位置，原文件不变。" if self.mode.currentData()
                                  else "下方位置仅保存引用记录；原文件移动或删除后，引用会失效。")

    def set_busy(self, busy):
        self.buttons.setEnabled(not busy)
        self.body.setReadOnly(busy)
        self.form_content.setEnabled(not busy)

    def request(self):
        selected_kind = self.tabs.currentIndex()
        folder = self.destination.currentData()
        resource_title = self.title.text().strip()
        parsed_tags = [v.strip() for v in self.tags.text().replace("，", ",").split(",") if v.strip()]
        if len(parsed_tags) > 100 or any(len(tag) > 100 for tag in parsed_tags):
            QMessageBox.information(self, "信息过长", "标签最多 100 个、单个名称最多 100 字。")
            return None
        resource_summary = self.summary.text().strip()
        urls = self.url.text().strip()
        content = self.body.toPlainText()
        if selected_kind == 3 and not self.directory_path:
            QMessageBox.information(self, "请选择目录", "选择完整的 Skill 或资料目录。")
            return None
        if selected_kind == 1 and not self.selected_files:
            QMessageBox.information(self, "请选择文件", "选择一个或多个需要收集的文件。")
            return None
        if selected_kind == 2 and not resource_title:
            resource_title = next((line.strip(" #")[:80] for line in content.splitlines() if line.strip()), "未命名笔记")
        if selected_kind == 0:
            parsed = QUrl(urls)
            if parsed.scheme() not in {"http", "https"} or not parsed.host() or parsed.userName() or parsed.password():
                QMessageBox.information(self, "链接不可保存", "请输入不含登录凭据的 http / https 链接。")
                return None
            resource_title = resource_title or (parsed.host() + (" · " + parsed.path().rstrip("/").split("/")[-1][:60] if parsed.path().strip("/") else ""))
        request = KnowledgeCollectionRequest(
            kind=("link", "file", "note", "directory")[selected_kind], folder=folder,
            title=resource_title, summary=resource_summary, tags=tuple(parsed_tags),
            project_id=self.project_id or self.scope_project, agent_id=self.scope_agent,
            url=urls, body=content, sources=tuple(self.selected_files),
            copy_files=bool(self.mode.currentData()), combine_files=bool(self.grouping.currentData()),
            directory_source=self.directory_path,
        )
        return request
