"""Compact resource cards: content and purpose take precedence over storage facts."""
from PySide6.QtCore import Qt, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPen, QPainter, QPainterPath
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem, QWidget

from ..knowledge_resources import resource_type
from .design import tokens
from .components import nav_icon


class LibraryIllustration(QWidget):
    """Theme-aware vector artwork for the library's first collection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(260, 132)
        self.setAccessibleName("将笔记、文件与链接整理到知识空间")

    def paintEvent(self, event):
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["accent_soft"]))
        p.drawEllipse(QRectF(36, 7, 188, 118))
        p.setBrush(QColor(t["purple_soft"]))
        p.drawEllipse(QRectF(190, 22, 27, 27))
        p.setBrush(QColor(t["success_soft"]))
        p.drawEllipse(QRectF(26, 88, 22, 22))
        # A book and two floating source cards, drawn in the app's icon language.
        for x, y, role, lines in ((54, 18, "purple", 2), (165, 47, "success", 3)):
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(QColor(t["surface"]))
            p.drawRoundedRect(QRectF(x, y, 49, 63), 8, 8)
            p.setPen(QPen(QColor(t[role]), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(lines):
                p.drawLine(x + 12, y + 18 + i * 12, x + 34 - i * 4, y + 18 + i * 12)
        book = QPainterPath()
        book.moveTo(80, 60)
        book.quadTo(103, 50, 130, 64)
        book.quadTo(157, 50, 180, 60)
        book.lineTo(180, 114)
        book.quadTo(154, 105, 130, 118)
        book.quadTo(104, 105, 80, 114)
        book.closeSubpath()
        p.setPen(QPen(QColor(t["accent"]), 2))
        p.setBrush(QColor(t["surface"]))
        p.drawPath(book)
        p.drawLine(130, 64, 130, 116)
        p.setPen(QPen(QColor(t["border_strong"]), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in (77, 88, 99):
            p.drawLine(92, y, 117, y + 2)
            p.drawLine(143, y + 2, 168, y)
        p.setPen(QPen(QColor(t["accent"]), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(225, 84, 225, 96)
        p.drawLine(219, 90, 231, 90)
        p.end()


class TopicCountDelegate(QStyledItemDelegate):
    """Keep the tree row continuous and draw a compact, theme-aware count."""

    def paint(self, painter, option, index):
        if index.column() != 1:
            super().paint(painter, option, index)
            return
        prepared = QStyleOptionViewItem(option)
        self.initStyleOption(prepared, index)
        count = prepared.text
        prepared.text = ""
        prepared.state &= ~QStyle.StateFlag.State_HasFocus
        prepared.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, prepared, painter, prepared.widget)
        theme = tokens()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        font = QFont(option.font)
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        width = max(22, min(option.rect.width() - 4, painter.fontMetrics().horizontalAdvance(count) + 12))
        badge = QRect(option.rect.center().x() - width // 2, option.rect.center().y() - 10, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme["surface"] if selected else theme["soft"]))
        painter.drawRoundedRect(badge, 6, 6)
        painter.setPen(QColor(theme["accent_ink"] if selected else theme["muted"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter,
                         painter.fontMetrics().elidedText(count, Qt.TextElideMode.ElideRight, width - 6))
        painter.restore()


class ResourceCardDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        entry = index.data(Qt.ItemDataRole.UserRole) or {}
        if not entry:
            return
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        t = tokens()
        painter.setPen(QPen(QColor(t["accent"] if selected else t["border"])))
        painter.setBrush(QColor(t["accent_soft"] if selected else t["surface"]))
        painter.drawRoundedRect(rect, 11, 11)
        body = rect.adjusted(13, 10, -13, -10)
        foreground = QColor(t["text"])
        secondary = QColor(t["muted"])
        role, icon = {"note": ("accent", 6), "link": ("success", 17),
                      "bundle": ("purple", 3), "folder": ("warning", 3)}.get(entry.get("kind"), ("purple", 9))
        tile = QRect(body.x(), body.y(), 28, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t[role + "_soft"]))
        painter.drawRoundedRect(tile, 7, 7)
        painter.drawPixmap(tile.x() + 5, tile.y() + 5, nav_icon(icon, t[role]).pixmap(18, 18))
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        title_rect = QRect(body.x() + 37, body.y(), body.width() - 59, 28)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(entry.get("name", ""), Qt.TextElideMode.ElideRight, title_rect.width()))
        painter.setPen(QColor(t["warning"] if entry.get("favorite") else t["disabled"]))
        painter.drawText(QRect(body.right() - 18, body.y(), 20, 25), Qt.AlignmentFlag.AlignCenter, "★" if entry.get("favorite") else "☆")
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(secondary)
        summary = entry.get("summary") or entry.get("filename") or entry.get("relative", "")
        hits = entry.get("matched_members", [])
        if hits:
            member = next((member for member in entry.get("members", []) if member["relative"] == hits[0]), None)
            if member:
                summary = "命中：" + member.get("filename", member["name"])
        painter.drawText(QRect(body.x(), body.y() + 30, body.width(), 22), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(summary, Qt.TextElideMode.ElideRight, body.width()))
        painter.setPen(QColor(t[role]))
        badge = resource_type(entry)
        if entry["kind"] == "bundle":
            badge += f" · {len(entry['members'])} 份内容"
        tags = " · ".join(entry.get("tags", [])[:2])
        footer = badge + ("  ·  " + tags if tags else "")
        painter.drawText(QRect(body.x(), body.bottom() - 23, body.width(), 22), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(footer, Qt.TextElideMode.ElideRight, body.width()))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(240, 112)
