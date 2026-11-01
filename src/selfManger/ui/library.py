from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextEdit, QVBoxLayout


class TextReportDialog(QDialog):
    def __init__(self, parent, title: str, report: dict):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 620)
        layout = QVBoxLayout(self)
        note = QLabel(report.get("note", ""))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(report.get("text") or "暂时没有日志记录。")
        layout.addWidget(self.text, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
