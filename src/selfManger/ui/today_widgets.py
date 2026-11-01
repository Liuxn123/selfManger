"""Compact painted rows and cards for Today; no per-row widgets or background polling."""
from __future__ import annotations

import re
from datetime import date

from PySide6.QtCore import Qt, QSize, QRect, QRectF, QEvent, QDate
from PySide6.QtGui import QColor, QFont, QPen, QPainter
from PySide6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout, QListWidget,
    QStyledItemDelegate, QStyle, QDialog, QFormLayout, QLineEdit, QWidget,
    QDialogButtonBox, QPushButton, QProgressBar, QSizePolicy, QDateEdit, QComboBox)

from .components import nav_icon
from .design import theme_color, ui_color
from ..workbench import task_details

ROW_DATA = Qt.ItemDataRole.UserRole + 1
COLORS = ("#3979ff", "#ee5264", "#ed9a2c", "#8760ed")
ATTENTION_TONES = {
    "danger": ("#c04f4b", "#fff0ef"),
    "warning": ("#b77718", "#fff5e7"),
    "accent": ("#2f6fd8", "#edf5ff"),
    "muted": ("#71809e", "#f2f4f8"),
}


def label(text, name):
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


def link(text, callback):
    widget = QPushButton(text)
    widget.setObjectName("TodayLink")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


class Metric(QFrame):
    def __init__(self, title, color, icon, note):
        super().__init__()
        self.setObjectName("TodayMetric")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        tile = QLabel()
        tile.setPixmap(nav_icon(icon, color).pixmap(28, 28))
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setFixedSize(48, 48)
        tile.setObjectName("TodayMetricIcon")
        tone = {COLORS[0]: "accent", COLORS[1]: "danger", COLORS[2]: "warning", COLORS[3]: "purple",
                "#22a66b": "success"}
        tile.setProperty("tone", tone.get(color, "accent"))
        self.icon = tile
        layout.addWidget(tile)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = label(title, "TodayMetricTitle")
        text.addWidget(self.title)
        self.value = label("0", "TodayMetricValue")
        text.addWidget(self.value)
        self.note = label(note, "TodayMuted")
        self.note.setWordWrap(True)
        text.addWidget(self.note)
        layout.addLayout(text, 1)
        self.progress = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add_progress(self):
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.layout().itemAt(1).layout().insertWidget(2, self.progress)


class Section(QFrame):
    def __init__(self, title, icon, action=None, callback=None):
        super().__init__()
        self.setObjectName("TodayCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(17, 12, 17, 12)
        self.body.setSpacing(8)
        heading = QHBoxLayout()
        image = QLabel()
        image.setPixmap(nav_icon(icon, COLORS[0]).pixmap(18, 18))
        heading.addWidget(image)
        self.title = label(title, "TodaySectionTitle")
        heading.addWidget(self.title, 1)
        if action:
            self.action_button = link(action, callback)
            heading.addWidget(self.action_button)
        else:
            self.action_button = None
        self.body.addLayout(heading)


class TodayList(QListWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setObjectName("TodayList")
        self.setItemDelegate(TodayDelegate(self, kind))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setMinimumWidth(0)

    def fit(self, maximum=5):
        height = self.itemDelegate().row_height
        self.setFixedHeight(max(1, min(maximum, self.count())) * height + 2)

    def sizeHint(self):
        # A fitted list reports the height it was given, so cards size to content
        # instead of to the whole item collection.
        hint = super().sizeHint()
        if self.minimumHeight() == self.maximumHeight():
            return QSize(hint.width(), self.maximumHeight())
        return hint


class TodayDelegate(QStyledItemDelegate):
    def __init__(self, parent, kind):
        super().__init__(parent)
        self.kind = kind
        self.row_height = {"project": 78, "schedule": 54, "agent": 54, "recent": 40,
                           "alert": 44, "attention": 52, "task": 56}.get(kind, 40)

    def sizeHint(self, option, index):
        return QSize(100, self.row_height)

    def text(self, painter, rect, text, color, bold=False, size=13, strike=False):
        font = QFont(painter.font())
        font.setPixelSize(size)
        font.setBold(bold)
        font.setStrikeOut(strike)
        painter.setFont(font)
        painter.setPen(QColor(theme_color(color, "color")))
        text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, rect.width()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, text)

    def paint(self, painter, option, index):
        rect = option.rect
        data = index.data(ROW_DATA) or {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(rect)
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.fillRect(rect, ui_color("#eff5ff"))
        painter.setPen(ui_color("#edf1f7"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if not data:
            self.text(painter, rect.adjusted(8, 0, -8, 0), index.data() or "", "#8390aa", size=12)
        elif self.kind == "project":
            color = COLORS[data.get("position", 0) % 4]
            tile = QRect(rect.x() + 4, rect.y() + 17, 40, 40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color(color))
            painter.drawRoundedRect(tile, 10, 10)
            self.text(painter, tile.adjusted(11, 0, 0, 0), data["name"][:1], "#ffffff", True, 19)
            x = rect.x() + 56
            phase = data["phase"]
            width = min(110, max(48, len(phase) * 13 + 20))
            badge = QRect(rect.right() - width - 4, rect.y() + 8, width, 23)
            painter.setBrush(ui_color("#eaf2ff"))
            painter.drawRoundedRect(badge, 10, 10)
            self.text(painter, badge.adjusted(9, 0, -6, 0), phase, COLORS[0], size=11)
            self.text(painter, QRect(x, rect.y() + 6, max(10, badge.x() - x - 8), 25), data["name"], "#192c52", True, 14)
            self.text(painter, QRect(x, rect.y() + 31, rect.right() - x - 4, 20), "下一步：" + data["next_step"], "#71809c", size=12)
            self.text(painter, QRect(x, rect.y() + 52, rect.right() - x - 4, 18), data.get("id", "") + " · 双击继续", "#97a2b6", size=11)
        elif self.kind == "schedule":
            match = re.match(r"^((?:[01]\d|2[0-3]):[0-5]\d(?:\s*[-–—~]\s*(?:[01]\d|2[0-3]):[0-5]\d)?)\s+(.+)$", data["text"])
            clock, title = (match[1], match[2]) if match else ("未定时间", data["text"])
            painter.setPen(QPen(ui_color("#e2e9f4"), 2))
            painter.drawLine(rect.x() + 9, rect.top(), rect.x() + 9, rect.bottom())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color(COLORS[0]))
            painter.drawEllipse(rect.x() + 5, rect.center().y() - 4, 8, 8)
            self.text(painter, QRect(rect.x() + 24, rect.y() + 6, rect.width() - 32, 19), clock, COLORS[0], size=11)
            self.text(painter, QRect(rect.x() + 24, rect.y() + 26, rect.width() - 32, 22), title, "#263c60", True)
        elif self.kind == "agent":
            running = bool(data.get("running"))
            x = rect.x() + 7
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color("#22a66b" if running else "#9aa7bb"))
            painter.drawEllipse(x, rect.y() + 12, 9, 9)
            badge = QRect(rect.right() - 70, rect.y() + 5, 65, 20)
            painter.setBrush(ui_color("#e7f8ef" if running else "#eff2f7"))
            painter.drawRoundedRect(badge, 8, 8)
            self.text(painter, badge.adjusted(5, 0, -5, 0), "运行中" if running else "空闲",
                      "#218b60" if running else "#78869d", size=10)
            self.text(painter, QRect(x + 17, rect.y() + 2, max(10, badge.x() - x - 22), 24),
                      data.get("title", "Agent"), "#263c60", True, 13)
            self.text(painter, QRect(x + 17, rect.y() + 27, rect.right() - x - 21, 20),
                      data.get("detail", ""), "#7a88a0", size=11)
        elif self.kind == "attention":
            tone = data.get("tone", "accent")
            foreground, background = ATTENTION_TONES.get(tone, ATTENTION_TONES["accent"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color(foreground))
            painter.drawRoundedRect(QRectF(rect.x() + 6, rect.y() + 11, 3, rect.height() - 22), 1.5, 1.5)
            end = rect.right() - 4
            if data.get("count"):
                text = str(data["count"])
                width = max(26, painter.fontMetrics().horizontalAdvance(text) + 16)
                badge = QRect(end - width, rect.center().y() - 10, width, 20)
                painter.setBrush(ui_color(background))
                painter.drawRoundedRect(badge, 9, 9)
                self.text(painter, badge, text, foreground, True, 11)
                end -= width + 8
            self.text(painter, QRect(rect.x() + 18, rect.y() + 4, max(20, end - rect.x() - 18), 24),
                      data.get("title", ""), "#263c60", True, 13)
            self.text(painter, QRect(rect.x() + 18, rect.y() + 27, max(20, end - rect.x() - 18), 18),
                      data.get("detail", ""), "#7e8ba3", size=11)
        elif self.kind in {"task", "focus", "todo"}:
            details = task_details(data["text"])
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
            x = rect.x() + 6
            if self.kind == "task":
                box = QRect(x, rect.center().y() - 8, 16, 16)
                painter.setPen(QPen(ui_color("accent" if checked else "border_strong"), 1))
                painter.setBrush(ui_color("accent" if checked else "surface"))
                painter.drawRoundedRect(box, 4, 4)
                if checked:
                    painter.setPen(QPen(ui_color("white"), 2))
                    painter.drawLine(box.x() + 3, box.y() + 8, box.x() + 7, box.y() + 12)
                    painter.drawLine(box.x() + 7, box.y() + 12, box.x() + 13, box.y() + 4)
            elif self.kind == "focus":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ui_color(COLORS[data.get("position", 0) % 4]))
                painter.drawEllipse(x, rect.center().y() - 9, 18, 18)
                self.text(painter, QRect(x + 5, rect.y(), 15, rect.height()), str(data.get("position", 0) + 1), "white", True, 11)
            end = rect.right() - 5
            if self.kind == "todo":
                badge = QRect(end - 78, rect.center().y() - 10, 78, 20)
                overdue = data.get("day", "") < date.today().isoformat()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ui_color("#fff0ef" if overdue else "#edf4ff"))
                painter.drawRoundedRect(badge, 9, 9)
                self.text(painter, badge.adjusted(5, 0, -5, 0), data.get("day", "")[5:] + ("逾期" if overdue else "截止"), "#c04f4b" if overdue else COLORS[0], size=10)
                end -= 85
            if details["time"]:
                self.text(painter, QRect(end - 42, rect.y(), 42, rect.height()), details["time"], "#7e8ba3", size=11)
                end -= 53
            if details["priority"]:
                colors = {"高": ("#fff0ef", "#c04f4b"), "中": ("#fff5e5", "#b77718"), "低": ("#edf4ff", COLORS[0])}
                background, foreground = colors[details["priority"]]
                badge = QRect(end - 34, rect.center().y() - 10, 34, 20)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ui_color(background))
                painter.drawRoundedRect(badge, 8, 8)
                self.text(painter, badge, details["priority"], foreground, size=10)
                end -= 42
            context = []
            if self.kind == "task":
                if data.get("plan_title"):
                    context.append("来自：" + data["plan_title"])
                if data.get("project_id"):
                    context.append(data["project_id"])
                if data.get("repeat"):
                    context.append("周期打卡")
            title_rect = QRect(x + 28, rect.y() + 5, max(10, end - x - 28), 24) if context else QRect(x + 28, rect.y(), max(10, end - x - 28), rect.height())
            self.text(painter, title_rect, details["title"], "#94a0b5" if checked else "#263c60", strike=checked)
            if context:
                self.text(painter, QRect(x + 28, rect.y() + 30, max(10, end - x - 28), 19),
                          " · ".join(context), "#7e8ba3", size=11)
        else:
            color = COLORS[1] if self.kind == "alert" else COLORS[0]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color(color))
            painter.drawEllipse(rect.x() + 5, rect.center().y() - 3, 6, 6)
            end = rect.right() - 5
            if data.get("at") and rect.width() > 380:
                self.text(painter, QRect(end - 115, rect.y(), 115, rect.height()), data["at"], "#8997b0", size=11)
                end -= 123
            self.text(painter, QRect(rect.x() + 22, rect.y(), max(10, end - rect.x() - 22), rect.height()), data.get("display_text", data.get("title", index.data() or "")), "#344768", size=12)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if self.kind == "task" and index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            mouse = event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and QRect(option.rect.x() + 4, option.rect.center().y() - 11, 22, 22).contains(event.position().toPoint())
            keyboard = event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
            if mouse or keyboard:
                state = Qt.CheckState.Unchecked if index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value else Qt.CheckState.Checked
                return model.setData(index, state, Qt.ItemDataRole.CheckStateRole)
            return False
        return super().editorEvent(event, model, option, index)


class FocusCard(QFrame):
    """One band answering "how far am I" and "what is next", instead of three counters."""

    def __init__(self):
        super().__init__()
        self.setObjectName("TodayFocus")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(16)

        progress = QWidget()
        progress_layout = QVBoxLayout(progress)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        self.fraction = label("0 / 0", "TodayFocusValue")
        head.addWidget(self.fraction)
        self.percent = label("", "TodayFocusPercent")
        head.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        progress_layout.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setMaximumWidth(196)
        progress_layout.addWidget(self.bar)
        self.note = label("", "TodayMuted")
        progress_layout.addWidget(self.note)
        progress_layout.addStretch(0)
        progress.setFixedWidth(212)
        layout.addWidget(progress)

        divider = QFrame()
        divider.setObjectName("TodayFocusDivider")
        divider.setFixedWidth(1)
        layout.addWidget(divider)

        upcoming = QWidget()
        upcoming_layout = QVBoxLayout(upcoming)
        upcoming_layout.setContentsMargins(0, 1, 0, 1)
        upcoming_layout.setSpacing(3)
        self.kicker = label("接下来", "TodayFocusKicker")
        upcoming_layout.addWidget(self.kicker)
        self.next_title = label("", "TodayFocusNext")
        self.next_title.setWordWrap(True)
        upcoming_layout.addWidget(self.next_title)
        self.next_note = label("", "TodayMuted")
        self.next_note.setWordWrap(True)
        upcoming_layout.addWidget(self.next_note)
        upcoming_layout.addStretch(1)
        layout.addWidget(upcoming, 1)

    def update_state(self, done, total, next_title, next_note, kicker="接下来"):
        self.fraction.setText(f"{done} / {total}" if total else "待安排")
        ratio = round(done * 100 / total) if total else 0
        self.percent.setText(f"{ratio}%" if total else "")
        self.bar.setValue(ratio)
        self.bar.setVisible(bool(total))
        self.note.setText(f"已完成 {done} 项 · 待办 {max(0, total - done)} 项" if total else "为今天留一个明确的目标")
        self.kicker.setText(kicker)
        self.next_title.setText(next_title)
        self.next_note.setText(next_note)


class WeekRhythm(QWidget):
    """Seven painted columns: planned volume behind, completed volume in front."""

    def __init__(self):
        super().__init__()
        self.days: list[dict] = []
        self.setFixedHeight(136)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_days(self, days):
        self.days = list(days or [])
        self.update()

    def paintEvent(self, event):
        if not self.days:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(self.days)
        cell = self.width() / count
        column = min(30.0, cell * .48)
        top, bottom = 20.0, self.height() - 22.0
        span = max(12.0, bottom - top)
        peak = max([max(day["planned"], day["done"]) for day in self.days] + [1])
        for index, day in enumerate(self.days):
            x = index * cell + (cell - column) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color("#eef2f9"))
            painter.drawRoundedRect(QRectF(x, top, column, span), 5, 5)
            if day["planned"]:
                height = max(6.0, span * day["planned"] / peak)
                painter.setBrush(ui_color("#d8e4f6"))
                painter.drawRoundedRect(QRectF(x, top + span - height, column, height), 5, 5)
            if day["done"]:
                height = max(6.0, span * day["done"] / peak)
                painter.setBrush(ui_color("#2f6fd8" if day.get("today") else "#7ba7ec"))
                painter.drawRoundedRect(QRectF(x, top + span - height, column, height), 5, 5)
            value = str(day["done"]) if day["done"] else ""
            if value:
                font = QFont(painter.font())
                font.setPixelSize(11)
                font.setBold(bool(day.get("today")))
                painter.setFont(font)
                painter.setPen(QColor(theme_color("#2f6fd8" if day.get("today") else "#7e8ba3", "color")))
                painter.drawText(QRect(int(x), 0, int(column), 18), Qt.AlignmentFlag.AlignCenter, value)
            font = QFont(painter.font())
            font.setPixelSize(11)
            font.setBold(bool(day.get("today")))
            painter.setFont(font)
            painter.setPen(QColor(theme_color("#1d4d96" if day.get("today") else "#93a1b6", "color")))
            painter.drawText(QRect(int(index * cell), int(self.height()) - 19, int(cell), 18),
                             Qt.AlignmentFlag.AlignCenter, day["label"] + (" · 今" if day.get("today") else ""))
        painter.end()


class DailyEntryDialog(QDialog):
    def __init__(self, parent, section, day=None):
        super().__init__(parent)
        is_task = section in {"今日任务", "Todo"}
        self.is_task = is_task
        self.setWindowTitle("添加 Todo" if section == "Todo" else "添加任务" if is_task else "添加日程")
        self.setObjectName("DailyEntryDialog")
        self.resize(580, 390 if is_task else 330)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("安排一项任务" if is_task else "安排一段日程")
        heading.setObjectName("TodayDialogTitle")
        layout.addWidget(heading)
        intro = QLabel("记录截止日期、优先级和预计时间。新行动保存在 SQLite。" if is_task else "记录日期与时间。新行动保存在 SQLite。")
        intro.setObjectName("TodayMuted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setContentsMargins(16, 14, 16, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.date = QDateEdit(QDate(day.year, day.month, day.day) if day else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setMinimumHeight(40)
        form.addRow("截止日期" if section == "Todo" else "哪一天", self.date)
        self.title = QLineEdit()
        self.title.setMaxLength(240)
        self.title.setPlaceholderText("例如：检查项目进展")
        self.title.setMinimumHeight(42)
        form.addRow("做什么", self.title)
        self.time = QLineEdit()
        self.time.setPlaceholderText("选填，例如 09:30" if is_task else "选填，例如 09:00–10:00")
        self.time.setMinimumHeight(40)
        form.addRow("预计时间" if is_task else "时间", self.time)
        if is_task:
            self.priority = QComboBox()
            self.priority.addItem("普通", "")
            self.priority.addItem("高优先级", "高")
            self.priority.addItem("中优先级", "中")
            self.priority.addItem("低优先级", "低")
            self.priority.setMinimumHeight(40)
            form.addRow("优先级", self.priority)
        else:
            self.priority = None
        card = QFrame()
        card.setObjectName("TodayDialogCard")
        card.setLayout(form)
        layout.addWidget(card)
        note = label("新行动保存在 SQLite；旧 Markdown 原件保留。", "TodayMuted")
        layout.addWidget(note)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("添加")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.title.textChanged.connect(lambda text: buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(text.strip())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title.setFocus()

    def values(self):
        priority = self.priority.currentData() if self.priority else ""
        return self.title.text(), self.time.text(), priority


class BackupButton(QPushButton):
    def __init__(self, name, status):
        super().__init__()
        self.name, self.status = name, status
        self.setObjectName("TodayBackup")
        self.setToolTip(name + "\n" + status)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(name + "\n" + status)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        metrics = self.fontMetrics()
        self.setText("\n".join(metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(10, self.width() - 16)) for text in (self.name, self.status)))
