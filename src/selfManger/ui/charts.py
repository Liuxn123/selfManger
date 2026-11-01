"""Small data-only charts: no timers, filesystem reads or chart dependencies."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy

from .design import tokens


class DistributionChart(QWidget):
    """Accessible donut with an exact-count legend, including a real empty state."""

    def __init__(self, title, empty_text="暂无数据", parent=None, *, compact=False):
        super().__init__(parent)
        self.title, self.empty_text = title, empty_text
        self.segments = ()
        self.caption = "总计"
        self.compact = compact
        self.setMinimumWidth(210)
        self.setFixedHeight(112 if compact else 142)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(title)

    def set_data(self, segments, caption="总计"):
        # Copy the tiny summary; never retain or mutate business records.
        values = tuple((str(name), max(0, int(count)), role) for name, count, role in segments)
        if (values, caption) == (self.segments, self.caption):
            return
        self.segments, self.caption = values, caption
        summary = "；".join(f"{name} {count}" for name, count, _ in values)
        self.setAccessibleDescription(summary if sum(row[1] for row in values) else self.empty_text)
        self.setToolTip(self.title + "\n" + (summary or self.empty_text))
        self.update()

    def paintEvent(self, event):
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(t["accent_soft"]))
        gradient.setColorAt(1, QColor(t["soft"]))
        p.setBrush(gradient)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        font = self.font()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(t["text"]))
        p.drawText(14, 23, self.title)
        total = sum(row[1] for row in self.segments)
        if self.compact:
            track = QRectF(14, 38, self.width() - 28, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["border"]))
            p.drawRoundedRect(track, 4, 4)
            x = track.left()
            for _, count, role in self.segments:
                if total and count:
                    width = track.width() * count / total
                    p.setBrush(QColor(t[role]))
                    p.drawRect(QRectF(x, track.top(), width, track.height()))
                    x += width
            font.setBold(False)
            font.setPixelSize(11)
            p.setFont(font)
            p.setPen(QColor(t["secondary"]))
            if total:
                for i, (name, count, role) in enumerate(self.segments):
                    x = 14 + (i % 2) * (self.width() - 28) / 2
                    y = 57 + (i // 2) * 22
                    p.setPen(QColor(t[role]))
                    p.drawText(QRectF(x, y, (self.width() - 28) / 2, 20), Qt.AlignmentFlag.AlignVCenter, f"● {name}  {count}")
            else:
                p.drawText(QRectF(14, 57, self.width() - 28, 35), Qt.AlignmentFlag.AlignVCenter, self.empty_text)
            p.end()
            return
        ring = QRectF(19, 43, 76, 76)
        p.setPen(QPen(QColor(t["border"]), 9))
        p.drawEllipse(ring)
        start = 90 * 16
        for _, count, role in self.segments:
            if total and count:
                span = round(count / total * 360 * 16)
                p.setPen(QPen(QColor(t[role]), 9))
                p.drawArc(ring, start, -span)
                start -= span
        p.setPen(QColor(t["text"]))
        font.setPixelSize(21)
        p.setFont(font)
        p.drawText(ring.adjusted(0, 10, 0, -17), Qt.AlignmentFlag.AlignCenter, str(total) if total else "—")
        font.setPixelSize(10)
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor(t["muted"]))
        p.drawText(ring.adjusted(0, 35, 0, 0), Qt.AlignmentFlag.AlignCenter, self.caption)
        if not total:
            text = p.fontMetrics().elidedText(self.empty_text, Qt.TextElideMode.ElideRight, max(0, self.width() - 126))
            p.drawText(QRectF(113, 50, self.width() - 126, 60), Qt.AlignmentFlag.AlignVCenter, text)
        else:
            for i, (name, count, role) in enumerate(self.segments):
                y = 47 + i * 22
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t[role]))
                p.drawEllipse(QRectF(112, y + 1, 7, 7))
                p.setPen(QColor(t["secondary"]))
                text = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(0, self.width() - 166))
                p.drawText(QRectF(126, y - 5, self.width() - 166, 18), Qt.AlignmentFlag.AlignVCenter, text)
                p.drawText(QRectF(self.width() - 40, y - 5, 26, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(count))
        p.end()
