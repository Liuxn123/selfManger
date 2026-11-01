from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget, QListWidget)

from ..domain import KINDS, Resource, RestorePlan, UserError
from ..security import safe_result
from .presentation import readable_report


def choose_path(edit: QLineEdit, parent: QWidget, directory: bool = True) -> None:
    current = edit.text() or str(Path.home())
    value = QFileDialog.getExistingDirectory(parent, "选择目录", current) if directory else QFileDialog.getOpenFileName(parent, "选择文件", current)[0]
    if value:
        edit.setText(value)


class ResourceDialog(QDialog):
    def __init__(self, parent: QWidget, kind: str, resource: Resource | None = None) -> None:
        super().__init__(parent)
        if kind not in {"project", "folder", "script", "vault"}:
            raise UserError("该资源类型已移除，现有资料不会被删除。")
        self.kind, self.original, self.result_resource = kind, resource, None
        self.fields: dict[str, QLineEdit] = {}
        self.setWindowTitle(("编辑" if resource else "添加") + KINDS[kind])
        self.resize(660, 550)
        self.setMinimumSize(540, 380)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        self.form = QFormLayout(panel)
        self.form.setSpacing(12)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(panel)
        layout.addWidget(scroll)
        options = resource.options if resource else {}
        self.add_field("name", "名称", resource.name if resource else KINDS[kind])
        if kind == "script":
            self.add_field("script_path", "备份脚本", options.get("script_path", ""), "file")
            self.add_field("script_arguments", "脚本参数 JSON 数组", json.dumps(options.get("script_arguments", []), ensure_ascii=False))
            note = "脚本将资料写入 SELFMANGER_BACKUP_OUTPUT_DIR，之后压缩加密；请勿在参数中填写口令。"
        else:
            self.add_field("path", "文件夹位置", options.get("path", ""), "dir")
            self.add_field("exclude_dirs", "额外排除目录名", ", ".join(options.get("exclude_dirs", [])))
            if kind == "vault":
                self.manage_git = QCheckBox("独立管理知识库 Git")
                self.manage_git.setChecked(bool(options.get("manage_git")))
                self.form.addRow("", self.manage_git)
            note = "所选文件夹将压缩并加密保存到统一备份位置。"
        hint = QLabel(note)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.automatic = QCheckBox("管家打开时，每天自动备份这项资料")
        self.automatic.setChecked(bool(options.get("automatic_backup")))
        layout.addWidget(self.automatic)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存资源")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_field(self, key: str, label: str, value: str = "", chooser: str = "") -> None:
        edit = QLineEdit(str(value))
        self.fields[key] = edit
        edit.setAccessibleName(label)
        if chooser:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(edit)
            control = QPushButton("选择")
            control.clicked.connect(lambda checked=False: choose_path(edit, self, chooser == "dir"))
            layout.addWidget(control)
            self.form.addRow(label, row)
        else:
            self.form.addRow(label, edit)

    def save(self) -> None:
        try:
            values = {key: edit.text().strip() for key, edit in self.fields.items()}
            name = values.pop("name")
            if not name:
                raise UserError("请输入资源名称。")
            options = {**(self.original.options if self.original else {}), **values}
            options["automatic_backup"] = self.automatic.isChecked()
            options.setdefault("backup_interval_hours", 24)
            if self.kind == "script":
                script = Path(options.get("script_path", "")).expanduser()
                if not script.is_file():
                    raise UserError("请选择现有备份脚本。")
                arguments = json.loads(values["script_arguments"])
                if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
                    raise UserError("脚本参数需要字符串 JSON 数组。")
                options["script_path"], options["script_arguments"] = str(script.resolve()), arguments
            else:
                if not options["path"]:
                    raise UserError("请选择资源目录。")
                options["exclude_dirs"] = [part.strip() for part in values["exclude_dirs"].split(",") if part.strip()]
                if self.kind == "vault":
                    options["manage_git"] = self.manage_git.isChecked()
            self.result_resource = Resource(name, self.kind, options, self.original.id) if self.original else Resource(name, self.kind, options)
            self.accept()
        except (UserError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "配置未保存", str(exc))


class PasswordDialog(QDialog):
    def __init__(self, parent: QWidget, creating: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("加密备份口令" if creating else "解锁备份")
        self.resize(480, 230)
        layout = QVBoxLayout(self)
        hint = QLabel(("设置统一备份口令：新的加密备份共用它，并保存到安全凭据存储。" if creating else "输入这份备份原来的口令；已经保存统一口令时，可以留空使用它。") + "换电脑恢复时仍需提供相同口令。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("口令", self.password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        if creating:
            form.addRow("再次输入", self.confirm)
        layout.addLayout(form)
        portable = bool(getattr(getattr(parent, "store", None), "portable_root", None))
        self.remember = QCheckBox("保存为统一备份口令")
        self.remember.setChecked(creating)
        layout.addWidget(self.remember)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        def accept_password():
            if creating and self.password.text() != self.confirm.text():
                QMessageBox.warning(self, "口令不一致", "请重新输入相同口令。")
            else:
                self.accept()
        buttons.accepted.connect(accept_password)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ArchiveRestoreDialog(QDialog):
    def __init__(self, parent: QWidget, source: str, target: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择恢复资料与目标")
        self.resize(620, 240)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source = QLineEdit(source)
        self.target = QLineEdit(target)
        for label, edit, directory in [("加密备份 .amb", self.source, False), ("新目录 / 空目录", self.target, True)]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(edit)
            button = QPushButton("选择")
            button.clicked.connect(lambda checked=False, edit=edit, directory=directory: choose_path(edit, self, directory))
            row_layout.addWidget(button)
            form.addRow(label, row)
        layout.addLayout(form)
        hint = QLabel("可以直接输入尚未创建的新目录。恢复会先校验全部内容，不覆盖已有资料。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("校验并预览")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PlanDialog(QDialog):
    def __init__(self, parent: QWidget, resource: Resource, plan: RestorePlan) -> None:
        super().__init__(parent)
        self.setWindowTitle("确认恢复计划")
        self.resize(700, 570)
        layout = QVBoxLayout(self)
        title = QLabel(f"恢复 {resource.name}")
        title.setObjectName("Title")
        layout.addWidget(title)
        target = QLabel("目标：" + plan.target)
        target.setWordWrap(True)
        layout.addWidget(target)
        summary = QTextEdit()
        summary.setReadOnly(True)
        summary.setPlainText(readable_report(plan.summary))
        layout.addWidget(summary)
        self.confirm = QCheckBox("已核对恢复位置，并退出正在写入这些资料的应用")
        layout.addWidget(self.confirm)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        apply = buttons.button(QDialogButtonBox.StandardButton.Ok)
        apply.setText("执行恢复")
        apply.setProperty("primary", True)
        apply.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.confirm.toggled.connect(apply.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TransferDialog(QDialog):
    def __init__(self, parent: QWidget, report: dict, target: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("换电脑恢复 · 选择放在哪里")
        self.resize(740, 470)
        self.folder_fields = {}
        layout = QVBoxLayout(self)
        heading = QLabel(f"已解锁：{report['resource_name']}（{report['file_count']} 个文件）")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        hint = QLabel("选择一个新的总文件夹。项目文件和本地记录分别放在它里面；下方可以修改各自的文件夹名称。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.target = QLineEdit(target)
        self.target.setAccessibleName("新电脑上的恢复总文件夹")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.target)
        choose = QPushButton("选择")
        choose.clicked.connect(lambda: choose_path(self.target, self))
        row_layout.addWidget(choose)
        form.addRow("恢复总文件夹", row)
        for item in report.get("components", []):
            name = "项目文件" if item["role"] == "project" else ("本地记录" if item["id"] == "records" else "本地记录-" + item["id"])
            edit = QLineEdit(name)
            edit.setAccessibleName(item["label"] + "文件夹名称")
            self.folder_fields[item["id"]] = edit
            form.addRow(item["label"], edit)
        layout.addLayout(form)
        note = QLabel(RECORD_NOTE + "\n聊天记录查看支持本地文本、Markdown、JSON 和 JSONL；应用内部数据库保留原样。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("查看恢复清单")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
