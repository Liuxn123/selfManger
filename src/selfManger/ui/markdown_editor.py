"""Plain Markdown source editor with a fixed metadata header and line numbers."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QWidget


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, editor):
        super().__init__(editor.document())
        self.editor = editor

    def highlightBlock(self, text):
        style = QTextCharFormat()
        if text.startswith("#"):
            style.setFontWeight(700 if text.startswith("# ") else 500)
            if text.startswith("# "):
                style.setFontPointSize(13)
            style.setForeground(self.editor.palette().color(QPalette.ColorRole.Text))
            self.setFormat(0, len(text), style)
        elif text.lstrip().startswith(("- [ ]", "- [x]", "- [X]")):
            style.setForeground(QColor("#8798b5"))
            self.setFormat(0, text.index("]") + 1, style)


class LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(44, 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.palette().color(QPalette.ColorRole.AlternateBase))
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        block = self.editor.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.editor.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                painter.drawText(0, top, 32, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(number + 5))
            top += height
            block = block.next()
            number += 1


class MarkdownSourceEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MarkdownPlanEditor")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabStopDistance(32)
        self.document().setDocumentMargin(18)
        self.gutter = LineNumbers(self)
        self.header = QWidget(self)
        self.header.setObjectName("MarkdownMetadata")
        layout = QHBoxLayout(self.header)
        layout.setContentsMargins(0, 10, 16, 6)
        numbers = QLabel("1\n2\n3\n4")
        numbers.setObjectName("MarkdownHeaderNumbers")
        numbers.setFixedWidth(44)
        numbers.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(numbers)
        self.metadata = QLabel()
        self.metadata.setObjectName("MarkdownHeaderText")
        layout.addWidget(self.metadata, 1)
        self.setViewportMargins(44, 104, 0, 0)
        self.highlighter = MarkdownHighlighter(self)
        self.updateRequest.connect(self.update_numbers)
        self.blockCountChanged.connect(lambda _: self.gutter.update())

    def set_metadata(self, kind, day):
        names = {"daily": "daily_plan", "longterm": "longterm_plan", "recurring": "recurring_plan",
                 "project": "project_plan", "weekly-review": "weekly_review", "monthly-review": "monthly_review"}
        self.metadata.setText("---\ntype:    " + names.get(kind, kind) + "\ndate:    " + (day or "未设置") + "\n---")

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "highlighter"):
            self.highlighter.rehighlight()

    def update_numbers(self, rect, delta):
        if delta:
            self.gutter.scroll(0, delta)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        area = self.contentsRect()
        self.header.setGeometry(area.x(), area.y(), area.width(), 104)
        self.gutter.setGeometry(QRect(area.x(), area.y() + 104, 44, max(0, area.height() - 104)))
