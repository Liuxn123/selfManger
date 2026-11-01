from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ProjectGitStatusDialog(QDialog):
    def __init__(self, parent, project_name, repository, report, action_callback):
        super().__init__(parent)
        self.setWindowTitle(project_name + " · Git 状态")
        self.resize(900, 680)
        self.setMinimumSize(760, 540)
        self.repository = repository
        self.action_callback = action_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.branch_label = QLabel()
        self.branch_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        heading.addWidget(self.branch_label)
        heading.addStretch(1)
        self.sync_label = QLabel()
        self.sync_label.setObjectName("Subtitle")
        heading.addWidget(self.sync_label)
        layout.addLayout(heading)

        metrics = QHBoxLayout()
        self.metric_labels = {}
        for key, title in (("change_count", "变更文件"), ("staged", "已暂存"),
                           ("unstaged", "未暂存"), ("untracked", "未跟踪")):
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            value = QLabel("0")
            value.setStyleSheet("font-size: 20px; font-weight: 600;")
            caption = QLabel(title)
            caption.setObjectName("Subtitle")
            card_layout.addWidget(value)
            card_layout.addWidget(caption)
            metrics.addWidget(card, 1)
            self.metric_labels[key] = value
        layout.addLayout(metrics)

        self.tabs = QTabWidget()
        self.files_tree = QTreeWidget()
        self.files_tree.setColumnCount(3)
        self.files_tree.setHeaderLabels(["文件", "状态", "操作"])
        self.files_tree.setRootIsDecorated(True)
        self.files_tree.setAlternatingRowColors(True)
        self.files_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.empty_files = QLabel("工作区干净，没有本地变更。")
        self.empty_files.setAlignment(Qt.AlignmentFlag.AlignCenter)
        files_page = QWidget()
        files_layout = QVBoxLayout(files_page)
        files_layout.setContentsMargins(4, 6, 4, 4)
        files_layout.addWidget(self.empty_files)
        files_layout.addWidget(self.files_tree, 1)
        self.tabs.addTab(files_page, "工作区变更")

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setAcceptRichText(False)
        self.tabs.addTab(self.details, "仓库信息")
        layout.addWidget(self.tabs, 1)
        self.action_status = QLabel("")
        self.action_status.setWordWrap(True)
        self.action_status.setObjectName("Subtitle")
        layout.addWidget(self.action_status)

        commit_row = QHBoxLayout()
        self.commit_input = QLineEdit()
        self.commit_input.setPlaceholderText("提交说明（填写后提交已暂存的更改）")
        self.commit_input.textChanged.connect(
            lambda value: self.commit_button.setEnabled(bool(value.strip()) and getattr(self, "_has_staged", False)))
        self.commit_input.returnPressed.connect(self.commit_staged)
        commit_row.addWidget(self.commit_input, 1)
        self.commit_button = QPushButton("提交")
        self.commit_button.clicked.connect(self.commit_staged)
        commit_row.addWidget(self.commit_button)
        layout.addLayout(commit_row)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("刷新状态")
        self.refresh_button.clicked.connect(lambda: self.action_callback("refresh", ""))
        buttons.addWidget(self.refresh_button)
        self.stage_button = QPushButton("暂存全部变更")
        self.stage_button.clicked.connect(self.stage_all)
        buttons.addWidget(self.stage_button)
        self.unstage_button = QPushButton("取消全部暂存")
        self.unstage_button.clicked.connect(lambda: self.action_callback("unstage_all", ""))
        buttons.addWidget(self.unstage_button)
        self.fetch_button = QPushButton("获取")
        self.fetch_button.clicked.connect(lambda: self.action_callback("fetch", ""))
        buttons.addWidget(self.fetch_button)
        self.pull_button = QPushButton("↓ 拉取")
        self.pull_button.clicked.connect(lambda: self.action_callback("pull", ""))
        buttons.addWidget(self.pull_button)
        self.push_button = QPushButton("↑ 推送")
        self.push_button.clicked.connect(lambda: self.action_callback("push", ""))
        buttons.addWidget(self.push_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.set_report(report)

    def set_busy(self, busy, message=""):
        for control in (self.refresh_button, self.stage_button, self.unstage_button, self.commit_button,
                        self.commit_input, self.fetch_button, self.pull_button, self.push_button, self.close_button):
            control.setEnabled(not busy)
        self.files_tree.setEnabled(not busy)
        self.action_status.setText(message or ("Git 操作进行中…" if busy else ""))

    def set_report(self, report, message=""):
        if not report.get("ok"):
            self.action_status.setText(report.get("text", "Git 状态读取失败。"))
            return
        self.branch_label.setText(f"分支  {report.get('branch') or 'HEAD（游离状态）'}")
        tracking = report.get("tracking")
        self.sync_label.setText(f"上游 {tracking}  ·  ↑ {report.get('ahead', 0)}  ↓ {report.get('behind', 0)}"
                                if tracking else "未设置上游分支")
        for key, value in self.metric_labels.items():
            value.setText(str(report.get(key, 0)))
        files = report.get("files", [])
        staged_files = [item for item in files if item.get("staged")]
        unstaged_files = [item for item in files if item.get("unstaged")]
        self._has_staged = bool(staged_files)
        self.stage_button.setEnabled(bool(unstaged_files))
        self.unstage_button.setEnabled(bool(staged_files))
        self.commit_button.setEnabled(bool(staged_files) and bool(self.commit_input.text().strip()))
        has_remote = bool(report.get("remotes"))
        self.fetch_button.setEnabled(has_remote)
        self.pull_button.setText(f"↓ 拉取 {report.get('behind', 0)}")
        self.push_button.setText(f"↑ 推送 {report.get('ahead', 0)}")
        self.pull_button.setEnabled(has_remote and bool(report.get("tracking")) and report.get("behind", 0) > 0)
        self.push_button.setEnabled(has_remote and (report.get("ahead", 0) > 0 or not report.get("tracking")))
        self.empty_files.setVisible(not files)
        self.files_tree.setVisible(bool(files))
        self.files_tree.clear()
        staged_group = QTreeWidgetItem([f"已暂存的更改 ({len(staged_files)})", "", ""])
        unstaged_group = QTreeWidgetItem([f"更改 ({len(unstaged_files)})", "", ""])
        self.files_tree.addTopLevelItems([staged_group, unstaged_group])
        for group, group_files, action, label_text in (
                (staged_group, staged_files, "unstage_file", "取消暂存"),
                (unstaged_group, unstaged_files, "stage_file", "暂存")):
            for file in group_files:
                child = QTreeWidgetItem([file.get("path", ""), file.get("state", ""), ""])
                child.setToolTip(0, file.get("path", ""))
                group.addChild(child)
                button = QToolButton()
                button.setText(label_text)
                button.clicked.connect(lambda checked=False, path=file.get("path", ""), op=action:
                                       self.action_callback(op, "", path))
                self.files_tree.setItemWidget(child, 2, button)
            group.setExpanded(True)
        staged_group.setHidden(not staged_files)
        unstaged_group.setHidden(not unstaged_files)

        commit = report.get("commit")
        info = [f"仓库目录：{report.get('directory', self.repository)}",
                f"当前分支：{report.get('branch') or 'HEAD（游离状态）'}",
                f"上游分支：{tracking or '未设置'}",
                f"同步情况：领先 {report.get('ahead', 0)} 个提交，落后 {report.get('behind', 0)} 个提交"
                if tracking else "同步情况：尚未设置上游分支",
                "", "最近提交："]
        info.append(f"{commit['hash']}  {commit['subject']}（{commit['time']}）" if commit else "仓库还没有提交记录。")
        info.extend(("", "远程仓库："))
        remotes = report.get("remotes", [])
        info.extend(f"{name}（{'获取' if direction == 'fetch' else '推送'}）：{address}"
                    for name, direction, address in remotes)
        if not remotes:
            info.append("尚未登记远程仓库。")
        if report.get("registered_repository"):
            info.extend(("", f"项目登记地址：{report['registered_repository']}"))
        info.extend(("", "领先/落后数量来自本地远程跟踪记录；刷新状态不会联网 fetch。"))
        self.details.setPlainText("\n".join(info))
        self.action_status.setText(message or report.get("summary", "状态已刷新。"))
        self.commit_button.setEnabled(bool(staged_files) and bool(self.commit_input.text().strip()))

    def stage_all(self):
        self.action_callback("stage", "")

    def commit_staged(self):
        message = self.commit_input.text().strip()
        if message and self.commit_button.isEnabled():
            self.action_callback("commit", message)

    def confirm_remote(self, action):
        label = "拉取远程更新（仅允许快进，不会自动创建合并提交）" if action == "pull" else "推送本地提交"
        if QMessageBox.question(self, "Git 远程操作", f"确认{label}？") == QMessageBox.StandardButton.Yes:
            self.action_callback(action, "")
