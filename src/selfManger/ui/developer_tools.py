from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFileIconProvider, QFormLayout,
    QHeaderView,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..domain import UserError
from .components import ReadableTable, nav_icon


def _button(label: str, callback, primary: bool = False) -> QPushButton:
    control = QPushButton(label)
    control.setProperty("primary", primary)
    control.clicked.connect(callback)
    return control


class DeveloperToolDialog(QDialog):
    def __init__(self, parent, service, item: dict | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.identity = item.get("id") if item else None
        self.setWindowTitle("编辑工具" if item else "添加工具")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit(str((item or {}).get("name", "")))
        self.name.setMaxLength(120)
        self.kind = QComboBox()
        self.kind.addItem("本地软件", "application")
        self.kind.addItem("网站", "website")
        self.target = QLineEdit(str((item or {}).get("target", "")))
        self.target.setMaxLength(2048)
        target_row = QWidget(self)
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(8)
        target_layout.addWidget(self.target, 1)
        self.browse = _button("选择程序…", self.choose_program)
        target_layout.addWidget(self.browse)
        self.summary = QLineEdit(str((item or {}).get("summary", "")))
        self.summary.setMaxLength(500)
        self.summary.setPlaceholderText("可选，简要说明用途")
        form.addRow("名称", self.name)
        form.addRow("类型", self.kind)
        form.addRow("启动目标", target_row)
        form.addRow("用途", self.summary)
        layout.addLayout(form)
        note = QLabel("只登记本地程序或 HTTP(S) 网站。不会安装软件、运行命令或保存网址中的账号口令。")
        note.setWordWrap(True)
        note.setObjectName("Subtitle")
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = _button("取消", self.reject)
        save = _button("保存", self.save, True)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.kind.currentIndexChanged.connect(self.update_target_mode)
        if item:
            self.kind.setCurrentIndex(max(0, self.kind.findData(item.get("kind"))))
        self.update_target_mode()

    def update_target_mode(self, *_args) -> None:
        is_application = self.kind.currentData() == "application"
        self.browse.setVisible(is_application)
        self.target.setPlaceholderText("选择 .exe、.com 或 .lnk" if is_application else "https://example.com")

    def choose_program(self) -> None:
        start = self.target.text().strip()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要登记的程序", start,
            "程序 (*.exe *.com *.lnk);;所有文件 (*)",
        )
        if path:
            self.target.setText(path)

    def save(self) -> None:
        try:
            self.service.save_developer_tool({
                "name": self.name.text(), "kind": self.kind.currentData(),
                "target": self.target.text(), "summary": self.summary.text(),
            }, self.identity)
        except UserError as exc:
            QMessageBox.warning(self, "工具未保存", str(exc))
            return
        self.accept()


class DeveloperToolsPage(QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.service = window.service
        self.setObjectName("DeveloperToolsPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        title = QLabel("工具与环境")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel("管理常用本地程序和网站入口；环境检测只读，不会安装或更新软件。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        diagnostics_card = QWidget(self)
        diagnostics_card.setObjectName("SettingsCard")
        diagnostics_layout = QVBoxLayout(diagnostics_card)
        diagnostics_layout.setContentsMargins(16, 12, 16, 12)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("开发环境检查"), 1)
        heading.addWidget(_button("重新检查", self.refresh_diagnostics))
        diagnostics_layout.addLayout(heading)
        self.diagnostics_grid = QGridLayout()
        self.diagnostics_grid.setHorizontalSpacing(16)
        self.diagnostics_grid.setVerticalSpacing(6)
        self.diagnostics_grid.setColumnStretch(0, 1)
        self.diagnostics_grid.setColumnStretch(1, 1)
        diagnostics_layout.addLayout(self.diagnostics_grid)
        layout.addWidget(diagnostics_card)

        toolbar = QHBoxLayout()
        self.add_button = _button("＋ 添加工具", self.add_tool, True)
        self.edit_button = _button("编辑", self.edit_tool)
        self.open_button = _button("打开", self.open_tool)
        self.delete_button = _button("删除", self.delete_tool)
        for control in (self.add_button, self.edit_button, self.open_button, self.delete_button):
            toolbar.addWidget(control)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.items = ReadableTable(0, 3)
        self.items.setHorizontalHeaderLabels(["名称", "类型", "启动目标 / 用途"])
        self.items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items.setAlternatingRowColors(True)
        self.items.verticalHeader().hide()
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.items.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.items.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.items.itemSelectionChanged.connect(self.selection_changed)
        self.items.doubleClicked.connect(lambda *_: self.open_tool())
        layout.addWidget(self.items, 1)
        self.empty = QLabel("还没有登记工具。添加本地程序或网站后，可在这里查看和打开。")
        self.empty.setObjectName("Subtitle")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.refresh()

    def refresh_diagnostics(self) -> None:
        while self.diagnostics_grid.count():
            item = self.diagnostics_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            report = self.service.diagnostics()
        except (OSError, UserError) as exc:
            QMessageBox.warning(self, "环境检查失败", str(exc))
            return
        values = [("操作系统", report.get("platform", "未知")),
                  ("Python", report.get("python", "未知")),
                  ("本机数据目录", report.get("data_directory", "未知"))]
        values.extend((name.upper(), path or "未找到") for name, path in report.get("tools", {}).items())
        for index, (name, value) in enumerate(values):
            row, column = divmod(index, 2)
            label = QLabel(f"{name}：{value}")
            label.setToolTip(str(value))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.diagnostics_grid.addWidget(label, row, column)

    def refresh(self) -> None:
        selected = self.selected_id()
        self.items.setRowCount(0)
        try:
            rows = self.service.developer_tools()
        except UserError as exc:
            rows = []
            QMessageBox.warning(self, "工具登记无法读取", str(exc))
        icon_provider = QFileIconProvider()
        for tool in rows:
            row = self.items.rowCount()
            self.items.insertRow(row)
            name = QTableWidgetItem(str(tool.get("name", "")))
            name.setData(Qt.ItemDataRole.UserRole, tool["id"])
            if tool.get("kind") == "application":
                name.setIcon(icon_provider.icon(QFileInfo(str(tool.get("target", "")))))
            else:
                name.setIcon(nav_icon(17))
            self.items.setItem(row, 0, name)
            self.items.setItem(row, 1, QTableWidgetItem("本地软件" if tool.get("kind") == "application" else "网站"))
            self.items.setItem(row, 2, QTableWidgetItem(str(tool.get("target", "")) +
                                                        (" · " + tool["summary"] if tool.get("summary") else "")))
            if tool["id"] == selected:
                self.items.selectRow(row)
        self.empty.setVisible(self.items.rowCount() == 0)
        if self.items.currentRow() < 0 and self.items.rowCount():
            self.items.selectRow(0)
        self.selection_changed()

    def selected_id(self) -> str | None:
        item = self.items.item(self.items.currentRow(), 0) if self.items.currentRow() >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_tool(self) -> dict | None:
        identity = self.selected_id()
        return self.service.developer_tool(identity) if identity else None

    def selection_changed(self) -> None:
        available = self.selected_id() is not None
        self.edit_button.setEnabled(available)
        self.open_button.setEnabled(available)
        self.delete_button.setEnabled(available)

    def add_tool(self) -> None:
        dialog = DeveloperToolDialog(self, self.service)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def edit_tool(self) -> None:
        item = self.selected_tool()
        if item is None:
            return
        dialog = DeveloperToolDialog(self, self.service, item)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def open_tool(self) -> None:
        item = self.selected_tool()
        if item is None:
            return
        if item["kind"] == "application":
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item["target"]).expanduser())))
        else:
            opened = QDesktopServices.openUrl(QUrl(item["target"]))
        if not opened:
            QMessageBox.warning(self, "无法打开", "系统未能打开此目标，请检查默认程序或地址。")

    def delete_tool(self) -> None:
        item = self.selected_tool()
        if item is None:
            return
        answer = QMessageBox.question(self, "删除工具登记", f"删除“{item['name']}”的本地登记？\n不会卸载或删除目标文件。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.remove_developer_tool(item["id"])
        except UserError as exc:
            QMessageBox.warning(self, "工具未删除", str(exc))
            return
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self.refresh_diagnostics()
