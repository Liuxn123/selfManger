"""Shared recurrence date controls for creation and editing."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QSpinBox, QLabel


class RepeatOptions(QWidget):
    changed = Signal()

    def __init__(self, frequency, date_edit, parent=None):
        super().__init__(parent)
        self.frequency = frequency
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.week = QWidget()
        self.week_layout = QGridLayout(self.week)
        self.week_layout.setContentsMargins(0, 0, 0, 0)
        self.columns = 4
        self.days = []
        for text in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"):
            box = QCheckBox(text)
            box.toggled.connect(self.changed)
            self.week_layout.addWidget(box, len(self.days) // 4, len(self.days) % 4)
            self.days.append(box)
        root.addWidget(self.week)
        self.month = QWidget()
        month = QHBoxLayout(self.month)
        month.setContentsMargins(0, 0, 0, 0)
        month.addWidget(QLabel("每月"))
        self.month_day = QSpinBox()
        self.month_day.setRange(0, 31)
        self.month_day.setSpecialValueText("沿用开始日规则")
        self.month_day.setToolTip("1–31 号为固定日期；沿用开始日规则保留旧计划的月末行为。")
        self.month_day.setSuffix(" 号")
        self.month_day.valueChanged.connect(self.changed)
        month.addWidget(self.month_day)
        hint = QLabel("没有该日期的月份跳过")
        hint.setWordWrap(True)
        hint.setObjectName("Subtitle")
        month.addWidget(hint)
        month.addStretch()
        root.addWidget(self.month)
        self.reset(date_edit.date())
        frequency.currentIndexChanged.connect(self.update_visibility)
        self.update_visibility()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 7 if self.width() >= 540 else 4
        if columns != self.columns:
            self.columns = columns
            for i, box in enumerate(self.days):
                self.week_layout.removeWidget(box)
                self.week_layout.addWidget(box, i // columns, i % columns)

    def reset(self, day, rule=None):
        rule = rule or {}
        weekdays = rule.get("weekdays") or [day.dayOfWeek()]
        for index, box in enumerate(self.days, 1):
            box.setChecked(index in weekdays)
        self.month_day.setValue(rule.get("month_day") or (0 if rule.get("frequency") == "monthly" else day.day()))

    def update_visibility(self, *_):
        value = self.frequency.currentData()
        self.week.setVisible(value == "weekly")
        self.month.setVisible(value == "monthly")
        self.setVisible(value in {"weekly", "monthly"})

    def values(self):
        return {"repeat_days": [i for i, box in enumerate(self.days, 1) if box.isChecked()]
                if self.frequency.currentData() == "weekly" else [],
                "repeat_month_day": self.month_day.value() or None
                if self.frequency.currentData() == "monthly" else None}

    def snapshot(self):
        return [[i for i, box in enumerate(self.days, 1) if box.isChecked()], self.month_day.value()]

    def restore(self, value):
        if (not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], list)
                or any(type(i) is not int or i not in range(1, 8) for i in value[0])
                or type(value[1]) is not int or value[1] not in range(32)):
            return
        for i, box in enumerate(self.days, 1):
            box.setChecked(i in value[0])
        self.month_day.setValue(value[1])
