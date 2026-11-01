"""Project dashboard widgets for shared tasks, user materials, and Markdown summaries."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QRect, QPoint, QRectF
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QPolygonF, QPen, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QSizePolicy, QStyle, QFrame, QLabel, QPushButton, QGridLayout,
    QCheckBox, QScrollArea, QMenu, QToolButton, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem)

from ..project_read_models import (milestone_rows, milestone_snapshot, overview_snapshot,
                                   outside_fences, sections)
from .components import nav_icon
from .design import tokens, ui_color
from .today_widgets import Metric, TodayDelegate, COLORS, ROW_DATA, label, link

class ProjectCardDelegate(TodayDelegate):
    def __init__(self, parent):
        super().__init__(parent, "project")
        self.row_height = 112

    def paint(self, painter, option, index):
        rect = option.rect.adjusted(4, 3, -4, -3)
        data = index.data(ROW_DATA) or {}
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(ui_color("accent" if selected else "border"))
        painter.setBrush(ui_color("accent_soft" if selected else "surface"))
        painter.drawRoundedRect(rect, 10, 10)
        color = COLORS[data.get("position", 0) % len(COLORS)]
        tile = QRect(rect.x() + 12, rect.y() + 25, 38, 38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ui_color(color))
        painter.drawRoundedRect(tile, 10, 10)
        painter.drawPixmap(tile.x() + 9, tile.y() + 9, nav_icon(3, "#ffffff").pixmap(20, 20))
        x, width = rect.x() + 61, rect.width() - 72
        t = tokens()
        for dy, text, ink, bold, size in (
            (7, data.get("id", ""), t["muted"], False, 10),
            (25, data.get("name", ""), t["text"], True, 14),
            (47, data.get("summary") or data.get("state_label", ""), color, False, 11)):
            self.text(painter, QRect(x, rect.y() + dy, width, 20), text, ink, bold, size)
        progress = data.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            bar = QRect(x, rect.bottom() - 24, max(20, width - 42), 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ui_color("border"))
            painter.drawRoundedRect(bar, 3, 3)
            painter.setBrush(ui_color("#3979ff"))
            painter.drawRoundedRect(QRect(bar.x(), bar.y(), int(bar.width() * max(0, min(100, progress)) / 100), bar.height()), 3, 3)
            self.text(painter, QRect(bar.right() + 6, bar.y() - 6, 35, 17), f"{int(progress)}%", t["muted"], False, 9)
        self.text(painter, QRect(x, rect.bottom() - 17, width, 15), data.get("date_label") or "点击继续工作", tokens()["muted"], False, 9)
        painter.restore()


class ProjectMetric(Metric):
    def __init__(self, title, color, icon):
        super().__init__(title, color, icon, "")
        self.layout().setContentsMargins(12, 10, 12, 10)
        self.icon.setFixedSize(32, 32)
        self.value.setObjectName("ProjectMetricValue")
        self.value.setWordWrap(True)
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self.setMinimumHeight(64)


class ProjectMetrics(QWidget):
    def __init__(self, metrics):
        super().__init__()
        self.metrics = metrics
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for metric in metrics:
            row.addWidget(metric, 1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 700
        for metric in self.metrics:
            metric.icon.setVisible(not compact)
            metric.note.setVisible(not compact)


class ProjectHero(QFrame):
    """Theme-aware project banner with a soft, vector-drawn mountain scene."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cover_path = ""
        self._cover = QPixmap()

    def set_cover(self, path=""):
        self.cover_path = str(path or "")
        self._cover = QPixmap(self.cover_path) if self.cover_path else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        t = tokens()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        painter.setClipPath(clip)
        if not self._cover.isNull():
            scaled = self._cover.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(rect, scaled, QRect((scaled.width()-rect.width())//2,
                                                   (scaled.height()-rect.height())//2,
                                                   rect.width(), rect.height()))
            shade = QColor("#f4f8ff"); shade.setAlpha(188); painter.fillRect(rect, shade)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(t["hero_start"]))
        gradient.setColorAt(1, QColor(t["hero_end"]))
        if self._cover.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, 14, 14)
        else:
            tint = QColor(t["hero_start"]); tint.setAlpha(72); painter.fillRect(rect, tint)
        if self.width() < 760:
            painter.end()
            super().paintEvent(event)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        ridge = QColor("#6a9fe9"); ridge.setAlpha(48); painter.setBrush(ridge)
        painter.drawPolygon(QPolygonF([QPoint(rect.right() - 390, rect.bottom()), QPoint(rect.right() - 230, rect.top() + 5),
                                       QPoint(rect.right() - 140, rect.top() + 75), QPoint(rect.right() - 45, rect.top() + 25),
                                       QPoint(rect.right() + 10, rect.bottom())]))
        ridge = QColor("#8bb4ed"); ridge.setAlpha(35); painter.setBrush(ridge)
        painter.drawPolygon(QPolygonF([QPoint(rect.right() - 280, rect.bottom()), QPoint(rect.right() - 125, rect.top() + 52),
                                       QPoint(rect.right() - 25, rect.top() + 105), QPoint(rect.right() + 40, rect.top() + 76),
                                       QPoint(rect.right() + 80, rect.bottom())]))
        snow = QColor("#ffffff"); snow.setAlpha(185); painter.setBrush(snow)
        painter.drawPolygon(QPolygonF([QPoint(rect.right()-230, rect.top()+5), QPoint(rect.right()-188, rect.top()+57),
                                       QPoint(rect.right()-151, rect.top()+35), QPoint(rect.right()-123, rect.top()+68)]))
        painter.drawPolygon(QPolygonF([QPoint(rect.right()-126, rect.top()+51), QPoint(rect.right()-82, rect.top()+105),
                                       QPoint(rect.right()-44, rect.top()+85), QPoint(rect.right()-24, rect.top()+114)]))
        painter.end()
        super().paintEvent(event)

    def fit(self):
        if self.layout():
            height = self.layout().totalHeightForWidth(self.width())
            self.setMinimumHeight(max(190, height if height >= 0 else self.layout().sizeHint().height()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "body_grid"):
            narrow = self.width() < 760
            if getattr(self, "narrow", None) != narrow:
                self.narrow = narrow
                self.body_grid.removeWidget(self.description_widget)
                self.body_grid.removeWidget(self.facts_widget)
                self.body_grid.addWidget(self.description_widget, 0, 0)
                self.body_grid.addWidget(self.facts_widget, 1 if narrow else 0, 0 if narrow else 1)
                self.body_grid.setColumnStretch(0, 1 if narrow else 3)
                self.body_grid.setColumnStretch(1, 0 if narrow else 4)
        self.fit()


class MilestoneRail(QWidget):
    def __init__(self, color, last=False, parent=None):
        super().__init__(parent)
        self.color, self.last = color, last
        self.setFixedWidth(18)
        self.setMinimumHeight(38)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#dce6f5"), 2))
        if not self.last:
            painter.drawLine(9, 10, 9, self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(4, 4, 11, 11)
        painter.end()


class ProjectOverview(QWidget):
    """Project dashboard made from the selected project's existing records."""
    def __init__(self, open_tasks, show_resources, edit_info, open_documents, open_milestones, toggle_task,
                 toggle_milestone, open_document, open_resource, upload_document):
        super().__init__()
        self.toggle_task = toggle_task
        self.toggle_milestone = toggle_milestone
        self.open_document = open_document
        self.open_resource = open_resource
        self.open_documents = open_documents
        self.upload_document_button = None
        self.upload_resource_button = None
        self.materials = []
        self.setObjectName("ProjectOverview")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(9)
        root.addLayout(self.grid)
        self.progress_card, self.progress_body = self.card("里程碑进度", 9)
        self.progress_card.setMaximumHeight(240)
        self.milestones_card, self.milestones_body = self.card(
            "里程碑", 15, "查看全部 →", open_milestones, scrollable=True)
        self.milestones_card.setMaximumHeight(240)
        self.ai_tasks_title = label("AI 规划（0）", "TodaySectionTitle")
        self.ai_tasks_title.setObjectName("ProjectTaskCardTitle")
        self.ai_tasks_title.setProperty("taskSource", "ai")
        self.ai_tasks_card, self.ai_tasks_body = self.card(
            "AI 规划任务", 9, title_widget=self.ai_tasks_title, scrollable=True)
        self.ai_tasks_body.setSpacing(3)
        self.manual_tasks_title = label("手动编写（0）", "TodaySectionTitle")
        self.manual_tasks_title.setObjectName("ProjectTaskCardTitle")
        self.manual_tasks_title.setProperty("taskSource", "manual")
        self.manual_tasks_card, self.manual_tasks_body = self.card(
            "手动编写任务", 9, "＋ 新建任务", open_tasks,
            title_widget=self.manual_tasks_title, scrollable=True)
        self.manual_tasks_body.setSpacing(3)
        self.materials_title = label("项目资料", "TodaySectionTitle")
        self.documents_card, self.documents_body = self.card("项目资料", 10, title_widget=self.materials_title, scrollable=True)
        self.documents_body.setSpacing(5)
        heading = self.documents_card.layout().itemAt(0).layout()
        self.add_material_button = QToolButton()
        self.add_material_button.setText("＋ 添加资料")
        self.add_material_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_material_button)
        self.upload_document_button = menu.addAction("上传项目文件", upload_document)
        self.upload_resource_button = menu.addAction("添加知识库资料", show_resources)
        self.add_material_button.setMenu(menu)
        heading.addWidget(self.add_material_button)
        heading.addWidget(link("查看全部 →", self.show_all_materials))
        self.activity_card, self.activity_body = self.card("项目动态", 3, "查看全部 →", open_documents, scrollable=True)
        for row, column, span, card in (
            (0, 0, 1, self.progress_card), (0, 1, 1, self.milestones_card),
            (0, 2, 1, self.activity_card), (1, 0, 1, self.ai_tasks_card),
            (1, 1, 1, self.manual_tasks_card), (2, 0, 2, self.documents_card),
        ):
            self.grid.addWidget(card, row, column, 3 if card is self.activity_card else 1, span)
        for column in range(3):
            self.grid.setColumnStretch(column, 1)
        for row, stretch in enumerate((0, 0, 1)):
            self.grid.setRowStretch(row, stretch)

    def open_material(self, material):
        (self.open_document if material["source"] == "document" else self.open_resource)(material["identity"])

    def show_all_materials(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("项目资料")
        dialog.resize(680, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"{len(self.materials)} 项资料 · 双击打开原条目", "TodayMuted"))
        listing = QListWidget()
        listing.setWordWrap(True)
        for material in self.materials:
            row = QListWidgetItem(material["name"] + "\n" + material["detail"])
            row.setData(Qt.ItemDataRole.UserRole, material)
            listing.addItem(row)
        def opened(row):
            self.open_material(row.data(Qt.ItemDataRole.UserRole))
            dialog.accept()
        listing.itemDoubleClicked.connect(opened)
        layout.addWidget(listing, 1)
        if not self.materials:
            layout.addWidget(label("暂无资料，可从“添加资料”上传文件或添加知识库资料。", "TodayMuted"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        mode = "wide" if self.width() >= 960 else "medium" if self.width() >= 680 else "narrow"
        if getattr(self, "layout_mode", None) == mode:
            return
        self.layout_mode = mode
        cards = (self.progress_card, self.milestones_card, self.ai_tasks_card,
                 self.manual_tasks_card, self.documents_card, self.activity_card)
        for card in cards:
            self.grid.removeWidget(card)
        for row in range(6):
            self.grid.setRowStretch(row, 0)
        columns = {"wide": 3, "medium": 2, "narrow": 1}[mode]
        for column in range(3):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        if mode == "wide":
            positions = ((0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1), (2, 0, 1, 2), (0, 2, 3, 1))
            for row, stretch in enumerate((0, 0, 1)):
                self.grid.setRowStretch(row, stretch)
        elif mode == "medium":
            positions = ((0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1), (2, 0, 1, 2), (3, 0, 1, 2))
            self.grid.setRowStretch(3, 1)
        else:
            positions = tuple((row, 0, 1, 1) for row in range(6))
            self.grid.setRowStretch(5, 1)
        for card, position in zip(cards, positions):
            card.setMinimumHeight(160 if card in {self.progress_card, self.milestones_card, self.documents_card, self.activity_card}
                                  else 170 if getattr(card, "has_tasks", False) else 110)
            self.grid.addWidget(card, *position)

    def card(self, title, icon, action=None, callback=None, title_widget=None, scrollable=False):
        frame = QFrame()
        frame.setObjectName("ProjectOverviewCard")
        frame.setMinimumWidth(0)
        frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        glyph = QLabel()
        glyph.setPixmap(nav_icon(icon, "#2879f6").pixmap(17, 17))
        heading.addWidget(glyph)
        heading.addWidget(title_widget or label(title, "TodaySectionTitle"), 1)
        if action:
            button = link(action, callback)
            heading.addWidget(button)
            if title == "项目资料":
                self.upload_document_button = button
            elif title == "项目资源":
                self.upload_resource_button = button
        layout.addLayout(heading)
        body_widget = QWidget()
        body = QVBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        if scrollable:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.viewport().setAutoFillBackground(False)
            scroll.viewport().setStyleSheet("background: transparent;")
            body_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            scroll.setWidget(body_widget)
            layout.addWidget(scroll, 1)
        else:
            layout.addWidget(body_widget, 1)
        return frame, body

    @staticmethod
    def clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                ProjectOverview.clear(item.layout())
                item.layout().deleteLater()

    @staticmethod
    def empty(layout, text):
        view = label(text, "TodayMuted")
        view.setWordWrap(True)
        layout.addWidget(view)

    @staticmethod
    def _progress_ring(value):
        class Ring(QWidget):
            def __init__(self, amount):
                super().__init__()
                self.amount = amount
                self.setFixedSize(112, 112)

            def paintEvent(self, event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                box = QRectF(9, 9, 94, 94)
                pen = QPen(ui_color("border"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(pen); painter.drawArc(box, 0, 360 * 16)
                if self.amount:
                    pen = QPen(ui_color("accent"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                    painter.setPen(pen); painter.drawArc(box, 90 * 16, -int(360 * 16 * max(0, min(100, self.amount)) / 100))
                painter.setPen(ui_color("text")); painter.setFont(painter.font())
                font = painter.font(); font.setPixelSize(18); font.setBold(True); painter.setFont(font)
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{self.amount}%")
                painter.end()
        return Ring(value)

    def set_data(self, snapshot, project, project_info, resources, agents, milestones=None, milestones_exists=False,
                 project_documents=None, milestones_text=""):
        checklist = snapshot["checklist"]
        milestone_items = milestones or []
        milestone_stats = milestone_snapshot(milestone_items)
        self.clear(self.progress_body)
        progress = milestone_stats["progress"]
        summary = QHBoxLayout(); summary.setSpacing(10)
        summary.addWidget(self._progress_ring(progress), 0, Qt.AlignmentFlag.AlignVCenter)
        stats = QVBoxLayout(); stats.setSpacing(9)
        for title, value, color in (("已完成", milestone_stats["done"], "#14aa72"),
                                    ("进行中", milestone_stats["in_progress"], "#2879f6"),
                                    ("待开始", milestone_stats["pending"], "#8795ad"),
                                    ("已逾期", milestone_stats["overdue"], "#ed5361")):
            line = QHBoxLayout(); dot = QLabel("●"); dot.setStyleSheet(f"color:{color};")
            line.addWidget(dot); line.addWidget(label(title, "TodayMuted"), 1); line.addWidget(label(str(value), "PlanSummaryItem"))
            stats.addLayout(line)
        summary.addLayout(stats, 1)
        self.progress_body.addLayout(summary)

        self.clear(self.milestones_body)
        if milestone_items:
            can_edit_milestones = project.get("state") not in {"archived", "unknown"}
            for index, item in enumerate(milestone_items):
                line = QHBoxLayout(); line.setSpacing(8)
                done = item["status"].casefold() in {"已完成", "完成", "completed", "done"}
                color = "#16a978" if done else "#2879f6"
                rail = MilestoneRail(color, index == len(milestone_items) - 1)
                line.addWidget(rail)
                details = QVBoxLayout(); details.setSpacing(2)
                title_row = QHBoxLayout(); title_row.setSpacing(5)
                check = QCheckBox()
                check.setObjectName("ProjectMilestoneCheck")
                check.setChecked(done)
                check.setEnabled(can_edit_milestones and item.get("status_editable", False))
                check.setAccessibleName("完成里程碑：" + item["name"])
                check.setToolTip("取消完成后状态将设为“待开始”。" if done else "标记此里程碑为已完成。")
                def change_milestone_status(checked, row=dict(item), source=milestones_text,
                                            control=check, initial_done=done):
                    if self.toggle_milestone(row, checked, source):
                        control.setEnabled(False)
                    else:
                        control.blockSignals(True)
                        control.setChecked(initial_done)
                        control.blockSignals(False)
                check.toggled.connect(change_milestone_status)
                title_row.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
                name = label(item["name"], "PlanSummaryItem")
                name.setWordWrap(True)
                title_row.addWidget(name, 1)
                details.addLayout(title_row)
                detail_text = " · ".join(value for value in (item.get("status", ""), item.get("date", "")) if value)
                if detail_text: details.addWidget(label(detail_text, "TodayMuted"))
                line.addLayout(details, 1)
                self.milestones_body.addLayout(line)
            self.milestones_body.addStretch(1)
        else:
            self.empty(self.milestones_body, "里程碑由你在 Obsidian 的项目文档中维护。" if milestones_exists else "还没有里程碑文档。创建空白文档后可在 Obsidian 编辑。")
        self.clear(self.ai_tasks_body)
        self.clear(self.manual_tasks_body)
        ai_tasks = checklist.get("ai_tasks", [])
        manual_tasks = checklist.get("manual_tasks", checklist["tasks"])
        self.ai_tasks_title.setText(f"AI 规划（{len(ai_tasks)}）")
        self.manual_tasks_title.setText(f"关联行动（{len(manual_tasks)}）")
        for card, tasks in ((self.ai_tasks_card, ai_tasks), (self.manual_tasks_card, manual_tasks)):
            card.has_tasks = bool(tasks)
            card.setMinimumHeight(170 if tasks else 110)
            card.setMaximumHeight(16777215 if tasks else 130)
        self._render_task_group(self.ai_tasks_body, ai_tasks, "暂无 AI 规划任务", "在任务页编辑状态或删除；已完成项可查看完成记录。", "ai")
        self._render_task_group(self.manual_tasks_body, manual_tasks, "暂无关联行动或手动任务", "在计划与待办中新建行动并关联此项目，日期和完成状态共用同一记录。", "manual")

        self.clear(self.documents_body)
        material_rows = project_documents or []
        documents = [{"source": "document", "identity": row["relative"], "name": row["name"],
                      "detail": "项目文件 · " + row["relative"]} for row in material_rows]
        linked = [{"source": "resource", "identity": row["id"], "name": row["name"],
                   "detail": "关联资源 · " + str(row.get("path") or row.get("type") or "已有资源")}
                  for row in resources]
        self.materials = []
        seen = set()
        for index in range(max(len(documents), len(linked))):
            for rows in (documents, linked):
                if index >= len(rows):
                    continue
                material = rows[index]
                key = (material["source"], material["identity"])
                if key not in seen:
                    seen.add(key)
                    self.materials.append(material)
        self.materials_title.setText(f"项目资料（{len(self.materials)}）")
        for material in self.materials[:6]:
            row = QFrame()
            row.setObjectName("ProjectMaterialRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 9, 5)
            badge = QLabel()
            badge.setPixmap(nav_icon(10 if material["source"] == "document" else 6, "#2879f6").pixmap(22, 22))
            row_layout.addWidget(badge)
            content = QVBoxLayout()
            content.setSpacing(2)
            name = link(material["name"], lambda _=False, value=material: self.open_material(value))
            name.setObjectName("ProjectMaterialName")
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            name.setToolTip(material["name"] + "\n" + material["detail"])
            content.addWidget(name)
            detail = label("项目文件" if material["source"] == "document" else "关联资源", "ProjectMaterialMeta")
            content.addWidget(detail)
            row_layout.addLayout(content, 1)
            row_layout.addWidget(label("›", "ProjectMaterialChevron"))
            self.documents_body.addWidget(row)
        if not self.materials:
            self.empty(self.documents_body, "暂无资料。通过“添加资料”上传项目文件或添加知识库资料。")
        self.documents_body.addWidget(label(
            f"{sum(row['source'] == 'document' for row in self.materials)} 个项目文件 · "
            f"{sum(row['source'] == 'resource' for row in self.materials)} 项关联资源"
            + (" · 概览展示前6项，查看全部可浏览完整列表" if len(self.materials) > 6 else ""), "ProjectMaterialCount"))
        self.documents_body.addStretch(1)

        self.clear(self.activity_body)
        recent = snapshot["recent"]
        entries = re.findall(r"- \*\*(.*?)\*\*\s*\n\s*(.*?)(?=\n\s*- \*\*|\Z)", recent, re.S)
        if entries:
            for title, detail in entries[:4]:
                row = QVBoxLayout(); row.setSpacing(1)
                heading = label(title.strip(), "PlanSummaryItem"); heading.setWordWrap(True); row.addWidget(heading)
                body = label(re.sub(r"\s+", " ", detail).strip(), "TodayMuted"); body.setWordWrap(True); row.addWidget(body)
                self.activity_body.addLayout(row)
        else:
            self.empty(self.activity_body, recent)
        agent_names = agents or []
        self.activity_body.addWidget(label("关联 Agent：" + ("、".join(agent_names) if agent_names else "未设置"), "TodayMuted"))
        self.activity_body.addStretch(1)

    def _render_task_group(self, layout, tasks, empty_text, hint, source):
        if not tasks:
            view = label(empty_text, "ProjectTaskEmpty")
            view.setWordWrap(True)
            view.setToolTip(hint)
            layout.addWidget(view)
            layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            return
        for task in tasks:
            row = QHBoxLayout(); row.setSpacing(8)
            title = str(task.get("text") or task.get("title") or "（无标题任务）").strip()
            if task.get("personal"):
                title = "行动 · " + title
            view = label(title, "ProjectTaskText")
            view.setWordWrap(True)
            view.setMinimumHeight(18)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            view.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            view.setToolTip(title)
            content = QVBoxLayout(); content.setSpacing(2)
            content.addWidget(view)
            done = task.get("status") == "done" or task["done"]
            if done:
                completion_note = str(task.get("completion_note", "")).strip() if source == "ai" else ""
                if completion_note:
                    compact_note = completion_note.replace("\n", " · ")
                    if len(compact_note) > 220:
                        compact_note = compact_note[:217].rstrip() + "…"
                    record = label("完成记录：" + compact_note, "TodayMuted")
                    record.setWordWrap(True)
                    record.setToolTip(completion_note)
                    content.addWidget(record)
            if task.get("due_date"):
                content.addWidget(label(task["due_date"], "TodayMuted"))
            row.addLayout(content, 1)
            row.setAlignment(content, Qt.AlignmentFlag.AlignTop)
            check = QCheckBox()
            check.setChecked(bool(task["done"]))
            if source == "ai":
                can_toggle = bool(task.get("task_id"))
                check.setEnabled(can_toggle)
                if task.get("task_id"):
                    check.setToolTip("手动修改任务完成状态")
                else:
                    check.setToolTip("这条规划尚未导入统一任务库，无法修改状态")
            else:
                check.setEnabled(bool(task.get("task_id")))
                check.setToolTip("标记完成状态" if task.get("task_id") else "这条旧清单任务尚未导入统一任务库")
            if task.get("task_id") and check.isEnabled():
                check.toggled.connect(lambda checked, identity=task["task_id"]: self.toggle_task(identity, checked))
            row.insertWidget(0, check, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(row)
        layout.addStretch(1)
