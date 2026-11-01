"""Fluent-inspired desktop tokens, shared by QSS and painted item delegates.

Design reference: https://github.com/ejacques11/pyside6-fluent-ui
The application keeps its own Qt widgets and interaction contracts.
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


LIGHT = {
    "canvas": "#f3f5f9", "surface": "#ffffff", "soft": "#f7f8fb",
    "border": "#e3e7ee", "border_strong": "#cbd3df",
    "text": "#1e293b", "secondary": "#526177", "muted": "#738197",
    "disabled": "#9aa6b8", "accent": "#2563eb", "accent_hover": "#1d4ed8",
    "accent_pressed": "#1e40af", "accent_soft": "#edf3ff",
    "accent_ink": "#2157c5", "success": "#138665", "success_soft": "#eaf8f2",
    "warning": "#b56b0c", "warning_soft": "#fff5e7",
    "danger": "#d13d56", "danger_soft": "#fff0f2",
    "purple": "#7651ce", "purple_soft": "#f3effd",
    "hero_start": "#edf3ff", "hero_end": "#fafbff", "inverse": "#ffffff",
    "sidebar": "#f0f6ff", "sidebar_text": "#395577", "sidebar_muted": "#8093b0",
    "sidebar_active": "#dceaff", "sidebar_hover": "#e5efff", "sidebar_brand": "#17325e",
}
DARK = {
    "canvas": "#141a26", "surface": "#1d2635", "soft": "#222e40",
    "border": "#303e53", "border_strong": "#465a74",
    "text": "#edf2fb", "secondary": "#c0cde0", "muted": "#97a8bf",
    "disabled": "#62738c", "accent": "#5392ff", "accent_hover": "#73a7ff",
    "accent_pressed": "#3479ef", "accent_soft": "#253b60",
    "accent_ink": "#9fc4ff", "success": "#65d2ad", "success_soft": "#203d36",
    "warning": "#f4bb70", "warning_soft": "#42362b",
    "danger": "#ff91a5", "danger_soft": "#462b38",
    "purple": "#b59aef", "purple_soft": "#352e4f",
    "hero_start": "#243753", "hero_end": "#1d2635", "inverse": "#ffffff",
    "sidebar": "#172338", "sidebar_text": "#b6c5da", "sidebar_muted": "#899bb5",
    "sidebar_active": "#2a4164", "sidebar_hover": "#23334e", "sidebar_brand": "#f4f7fc",
}

_SURFACE_ALIASES = {
    "accent_soft": ("#edf4ff", "#edf5ff", "#eaf2ff", "#eef5ff", "#e9f3ff", "#f2f6ff", "#eef1fe"),
    "danger_soft": ("#fff0f0", "#fff1f0", "#fff0f3", "#fff0f2", "#ffe5e3"),
    "warning_soft": ("#fff4e4", "#fff5e7", "#fff8ed", "#fff8ef"),
    "success_soft": ("#edf8f2", "#e7f8ef", "#e7f8f2", "#f3fbf7"),
    "purple_soft": ("#f3eeff", "#f2ecff", "#eee9fb", "#f8f5ff"),
}


def tokens(mode=None):
    app = QApplication.instance()
    mode = mode or (app.property("appearance") if app else "light")
    return DARK if mode == "dark" else LIGHT


def theme_color(value, role="auto", mode=None):
    """Resolve legacy illustration colors into semantic light/dark roles."""
    t = tokens(mode)
    if value in t:
        return t[value]
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)):
        return value
    color = QColor(value)
    if role in {"background", "background-color", "selection-background-color", "alternate-background-color", "auto"}:
        for name, colors in _SURFACE_ALIASES.items():
            if value.lower() in colors:
                return t[name]
    r, g, b = color.redF(), color.greenF(), color.blueF()
    hue, saturation, brightness = colorsys.rgb_to_hsv(r, g, b)
    if role == "auto":
        role = "background" if brightness > .86 and saturation < .25 else "color"
    if role in {"border", "border-color", "border-top", "border-bottom", "border-left", "border-right", "gridline-color"}:
        if brightness > .72 and saturation < .28:
            return t["border"]
    if role in {"color", "selection-color"} and .52 < hue < .72 and brightness < .68:
        return t["text" if brightness < .43 else "secondary"]
    if saturation < .28 or (.52 < hue < .72 and saturation < .55 and brightness < .76):
        if role in {"color", "selection-color"}:
            if min(r, g, b) > .93:
                return t["inverse"]
            return t["text" if brightness < .43 else "secondary" if brightness < .62 else "muted"]
        if min(r, g, b) > .98:
            return t["surface"]
        return t["canvas" if brightness > .93 else "soft" if brightness > .85 else "border"]
    if hue < .065 or hue > .90:
        name = "danger"
    elif hue < .16:
        name = "warning"
    elif hue < .49:
        name = "success"
    elif hue < .68:
        name = "accent"
    else:
        name = "purple"
    if role in {"color", "selection-color"}:
        return t[name if saturation > .42 else "secondary"]
    return t[name + "_soft" if saturation < .30 else name]


def ui_color(*args):
    if len(args) == 1 and isinstance(args[0], str):
        return QColor(theme_color(args[0]))
    return QColor(*args)


def palette(mode):
    t = tokens(mode)
    p = QPalette()
    roles = {
        "Window": "canvas", "WindowText": "text", "Base": "surface",
        "AlternateBase": "soft", "Text": "text", "Button": "surface",
        "ButtonText": "text", "Highlight": "accent", "HighlightedText": "inverse",
        "ToolTipBase": "surface", "ToolTipText": "text", "Link": "accent_ink",
        "PlaceholderText": "muted", "Light": "surface", "Mid": "border_strong",
        "Dark": "border", "Shadow": "border_strong",
    }
    for role, token in roles.items():
        p.setColor(getattr(QPalette.ColorRole, role), QColor(t[token]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["disabled"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(t["border"]))
    return p


def stylesheet(legacy, mode):
    """Retain page geometry while resolving every appearance from one palette."""
    t = tokens(mode)

    def declaration(match):
        prop, value = match.group(1), match.group(2)
        value = re.sub(r"#[0-9a-fA-F]{6}", lambda color: theme_color(color[0], prop, mode), value)
        if prop == "font-size":
            value = re.sub(r"\b(10|11)px", "12px", value)
        return prop + ":" + value

    base = re.sub(r"([\w-]+)\s*:\s*([^;}]+)", declaration, legacy)
    assets = (Path(__file__).resolve().parents[1] / "assets/ui").as_posix()
    arrow = "dark" if mode == "dark" else "light"
    values = {**t, "assets": assets, "arrow": arrow}
    overrides = _QSS
    for key, value in values.items():
        overrides = overrides.replace("@" + key + "@", value)
    # Page-specific rules previously gave primary actions conflicting colors.
    pages = ("TodayPage", "PlanningDashboard", "PlanRecordsPanel", "ProjectPage", "KnowledgePage", "SafetyPage", "SettingsPage")
    scopes = ["QPushButton[primary=\"true\"]", "QDialog QPushButton[primary=\"true\"]",
              "QWidget#PlanningDashboard QFrame#PlanHero QPushButton[primary=\"true\"]",
              "QDialog#PlanEntryDialog QPushButton[primary=\"true\"]"]
    scopes += [f'QWidget#{page} QPushButton[primary="true"]' for page in pages]
    selectors = ", ".join(scopes)
    overrides += selectors + f' {{ background: {t["accent"]}; border-color: {t["accent"]}; color: #ffffff; font-weight: 600; }}\n'
    for state, token in (("hover", "accent_hover"), ("pressed", "accent_pressed"), ("disabled", "border_strong")):
        overrides += ", ".join(s + ":" + state for s in scopes) + f' {{ background: {t[token]}; border-color: {t[token]}; color: #ffffff; }}\n'
    return base + overrides


_QSS = """
QWidget { font-family: "Microsoft YaHei UI", "Segoe UI", "Noto Sans SC"; color: @text@; font-size: 13px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:has-children:closed { image: url("@assets@/chevron-right-@arrow@.svg"); }
QWidget#KnowledgePage QFrame#KnowledgeEmptyCard { background: @surface@; border: 1px solid @border@; border-radius: 16px; }
QWidget#KnowledgePage QLabel#KnowledgeEmptyIcon { background: @accent_soft@; color: @accent_ink@; border-radius: 14px; font-size: 18px; font-weight: 700; }
QWidget#KnowledgePage QLabel#KnowledgeEmptyTitle { color: @text@; font-size: 18px; font-weight: 700; }
QWidget#KnowledgePage QLabel#KnowledgeEmptyDescription { color: @muted@; font-size: 12px; }
QWidget#KnowledgePage QPushButton#KnowledgeEmptyAction { min-height: 32px; padding: 6px 18px; }
QWidget#KnowledgePage QWidget#KnowledgePanel { background: @surface@; border: 1px solid @border@; border-radius: 14px; }
QWidget#KnowledgePage QSplitter#KnowledgeWorkspace::handle { background: transparent; width: 8px; }
QWidget#KnowledgePage QFrame#KnowledgeLocation { background: @soft@; border: 1px solid @border@; border-radius: 10px; }
QWidget#KnowledgePage QLabel#KnowledgeLocationIcon { color: @accent_ink@; font-size: 17px; }
QWidget#KnowledgePage QLabel#KnowledgeLocationCaption { color: @muted@; font-size: 10px; }
QWidget#KnowledgePage QLabel#KnowledgeLocationPath { color: @text@; font-size: 11px; font-weight: 600; }
QWidget#KnowledgePage QPushButton#KnowledgeLocationOpen { min-height: 24px; padding: 3px 9px; border-radius: 7px; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item { color: @secondary@; padding: 6px 10px; margin: 1px 2px; border-radius: 7px; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item:selected { background: @accent_soft@; color: @accent_ink@; font-weight: 600; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item { color: @secondary@; padding: 5px 6px; margin: 1px 2px; border-radius: 7px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item:selected { background: @accent_soft@; color: @accent_ink@; font-weight: 600; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:has-children:open { image: url("@assets@/chevron-@arrow@.svg"); }
QMainWindow, QWidget#Content, QWidget#DashboardBody, QWidget#SettingsBody { background: @canvas@; }
QWidget#Sidebar { background: @sidebar@; border-right: 1px solid @border@; }
QWidget#Sidebar QLabel#Brand { color: @sidebar_brand@; font-size: 16px; font-weight: 700; }
QWidget#Sidebar QLabel#BrandSub { color: @sidebar_muted@; font-size: 11px; }
QWidget#Sidebar QLabel#SidebarSection { color: @sidebar_muted@; font-size: 11px; padding: 4px 10px; }
QWidget#Sidebar QLabel#BrandIcon { background: transparent; border: none; }
QWidget#Sidebar QListWidget#Navigation { background: transparent; border: none; color: @sidebar_text@; font-size: 14px; }
QWidget#Sidebar QListWidget#Navigation::item { padding: 12px 13px; margin: 3px 0px; border: 1px solid transparent; border-radius: 9px; }
QWidget#Sidebar QListWidget#Navigation::item:hover { background: @sidebar_hover@; color: @accent_ink@; }
QWidget#Sidebar QListWidget#Navigation::item:selected { background: @sidebar_active@; color: @accent_ink@; border: 1px solid @border@; font-weight: 600; }
QWidget#Sidebar QWidget#SidebarFooter { border-top: 1px solid @border@; }
QWidget#Sidebar QPushButton#SidebarHelp { background: transparent; color: @secondary@; border: none; border-radius: 6px; padding: 5px 3px; min-height: 18px; text-align: left; }
QWidget#Sidebar QPushButton#SidebarHelp:hover { background: @accent_soft@; color: @accent_ink@; }
QWidget#GlobalTopbar { background: @surface@; border-bottom: 1px solid @border@; }
QLabel#GlobalTopbarMeta { color: @secondary@; font-size: 12px; }
QLabel#ProfileBadge { background: @accent_soft@; color: @accent_ink@; border: 1px solid @border@; border-radius: 17px; }
QPushButton#TopbarIconButton { min-width: 24px; min-height: 24px; border: 1px solid transparent; padding: 4px; border-radius: 8px; }
QPushButton#TopbarIconButton:hover { background: @accent_soft@; border-color: @border@; }
QPushButton, QToolButton { border: 1px solid @border_strong@; border-radius: 7px; padding: 7px 13px; min-height: 18px; background: @surface@; color: @secondary@; }
QPushButton:hover, QToolButton:hover { background: @soft@; border-color: @accent@; color: @text@; }
QPushButton:pressed, QToolButton:pressed { background: @accent_soft@; }
QPushButton:focus, QToolButton:focus { border-color: @accent@; }
QPushButton:disabled, QToolButton:disabled { background: @soft@; border-color: @border@; color: @disabled@; }
QPushButton[danger="true"] { background: @danger_soft@; color: @danger@; border-color: @danger_soft@; }
QPushButton[danger="true"]:hover { border-color: @danger@; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit { background: @surface@; border: 1px solid @border_strong@; border-radius: 7px; min-height: 20px; padding: 7px 10px; selection-background-color: @accent@; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus { border-color: @accent@; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled { color: @disabled@; background: @soft@; border-color: @border@; }
QLineEdit:read-only { color: @secondary@; background: @soft@; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down, QDateEdit::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border: none; background: transparent; }
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("@assets@/chevron-@arrow@.svg"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: @surface@; border: 1px solid @border@; padding: 4px; selection-background-color: @accent_soft@; selection-color: @accent_ink@; outline: none; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 4px 8px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none; margin: 2px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none; margin: 2px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@assets@/chevron-up-@arrow@.svg"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@assets@/chevron-@arrow@.svg"); width: 10px; height: 10px; }
QCheckBox, QRadioButton { spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; background: @surface@; border: 1px solid @border_strong@; border-radius: 4px; }
QCheckBox::indicator:hover { border-color: @accent@; background: @accent_soft@; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; image: url("@assets@/check.svg"); }
QCheckBox::indicator:indeterminate { background: @accent@; border-color: @accent@; image: url("@assets@/minus.svg"); }
QCheckBox::indicator:disabled { background: @soft@; border-color: @border@; }
QCheckBox::indicator:checked:disabled { background: @disabled@; image: url("@assets@/check.svg"); }
QCheckBox:focus { color: @accent_ink@; }
QRadioButton::indicator { width: 15px; height: 15px; border: 1px solid @border_strong@; border-radius: 8px; background: @surface@; }
QRadioButton::indicator:checked { border: 4px solid @accent@; background: @surface@; }
QTextEdit, QTextBrowser { background: @surface@; border: 1px solid @border@; border-radius: 9px; padding: 12px; selection-background-color: @accent@; selection-color: #ffffff; }
QTextEdit:focus { border-color: @accent@; }
QTableWidget { background: @surface@; alternate-background-color: @soft@; border: 1px solid @border@; border-radius: 9px; selection-background-color: @accent_soft@; selection-color: @text@; gridline-color: @border@; }
QHeaderView::section { background: @soft@; color: @secondary@; border: none; border-bottom: 1px solid @border@; padding: 10px 8px; font-size: 12px; font-weight: 600; }
QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid @border@; }
QTableWidget::item:selected { background: @accent_soft@; color: @text@; }
QTabBar::tab { background: transparent; padding: 9px 13px; color: @muted@; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: @accent_ink@; background: @soft@; }
QTabBar::tab:selected { color: @accent_ink@; border-bottom: 2px solid @accent@; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: @border_strong@; border-radius: 3px; min-height: 28px; min-width: 28px; }
QScrollBar::handle:hover { background: @muted@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu { background: @surface@; color: @text@; padding: 6px; border: 1px solid @border@; border-radius: 8px; }
QMenu::item { padding: 8px 24px 8px 12px; border-radius: 5px; }
QMenu::item:selected { background: @accent_soft@; color: @accent_ink@; }
QMenu::item:disabled { color: @disabled@; }
QMenu::separator { height: 1px; margin: 5px; background: @border@; }
QToolTip { background: @text@; color: @surface@; border: none; border-radius: 5px; padding: 7px 10px; }
QDialog { background: @canvas@; }
QDialogButtonBox QPushButton { min-width: 76px; padding: 8px 16px; }
QProgressBar { background: @border@; border: none; border-radius: 4px; }
QProgressBar::chunk { background: @accent@; border-radius: 4px; }
QCalendarWidget QToolButton { color: @text@; background: @surface@; border: none; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: @soft@; }
QCalendarWidget QAbstractItemView { background: @surface@; color: @text@; selection-background-color: @accent@; selection-color: #ffffff; }
QLabel#Title, QWidget#SettingsPage QLabel#Title, QWidget#SafetyPage QLabel#Title { font-size: 26px; font-weight: 700; color: @text@; }
QLabel#Subtitle, QLabel#TodayMuted { color: @muted@; font-size: 12px; }
QWidget#ProjectPage QLabel#ProjectName { color: @text@; font-size: 28px; font-weight: 700; }
QLabel#SectionTitle, QLabel#TodaySectionTitle, QWidget#PlanningDashboard QLabel#TodaySectionTitle { color: @text@; font-size: 14px; font-weight: 600; }
QLabel#TodayDate { color: @text@; font-size: 26px; font-weight: 700; }
QFrame#Card, QFrame#TodayMetric, QFrame#TodayCard, QFrame#SettingsCard, QFrame#ProjectOverviewCard { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#ProjectOverview QFrame#ProjectOverviewCard { background: @surface@; border: 1px solid @border@; border-radius: 14px; }
QWidget#ProjectOverview QFrame#ProjectInfoField { background: @soft@; border: 1px solid @border@; border-radius: 9px; }
QWidget#ProjectOverview QLabel#ProjectInfoLabel { color: @muted@; font-size: 10px; }
QWidget#ProjectOverview QLabel#ProjectInfoValue { color: @text@; font-size: 12px; font-weight: 600; }
QWidget#ProjectOverview QProgressBar#ProjectOverviewProgress { background: @soft@; border: none; border-radius: 5px; }
QWidget#ProjectOverview QProgressBar#ProjectOverviewProgress::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 @accent_hover@,stop:1 @accent@); border-radius: 5px; }
QWidget#ProjectOverview QLabel#ProjectMilestoneMark { color: @success@; min-width: 14px; font-weight: 700; }
QWidget#ProjectOverview QFrame#ProjectMaterialRow { background: @soft@; border: 1px solid @border@; border-radius: 9px; }
QWidget#ProjectOverview QFrame#ProjectMaterialRow:hover { background: @accent_soft@; border-color: @border_strong@; }
QWidget#ProjectOverview QLabel#ProjectMaterialType { background: @accent_soft@; color: @accent_ink@; border-radius: 7px; font-size: 9px; font-weight: 700; }
QWidget#ProjectOverview QLabel#ProjectMaterialType[fileKind="file"] { background: @soft@; color: @secondary@; }
QWidget#ProjectOverview QPushButton#ProjectMaterialName { min-height: 18px; padding: 0; border: none; background: transparent; color: @text@; text-align: left; font-size: 11px; font-weight: 600; }
QWidget#ProjectOverview QPushButton#ProjectMaterialName:hover { color: @accent_ink@; }
QWidget#ProjectOverview QLabel#ProjectMaterialMeta, QWidget#ProjectOverview QLabel#ProjectMaterialCount { color: @muted@; font-size: 9px; }
QWidget#ProjectOverview QLabel#ProjectMaterialChevron { color: @secondary@; font-size: 18px; }
QWidget#ProjectOverview QFrame#ProjectOverviewCard { border-radius: 13px; }
QWidget#ProjectOverview QFrame#ProjectInfoField { background: #f7faff; border-color: #e6edf8; }
QWidget#ProjectPage QTableWidget#ProjectCards::item:selected { background: transparent; }
QFrame#ProjectHero { border: 1px solid #d7e5f8; border-radius: 15px; }
QWidget#ProjectPage QFrame#ProjectHero QPushButton { background: rgba(255,255,255,190); color: #25466f; border-color: rgba(197,214,239,180); }
QWidget#ProjectPage QFrame#ProjectHero QPushButton:hover { background: #ffffff; border-color: @accent@; }
QWidget#ProjectPage QTableWidget#ProjectCards { border: none; background: transparent; }
QWidget#ProjectPage QTableWidget#ProjectCards::item { border: none; padding: 0; }
QWidget#ProjectPage QTabWidget#ProjectTabs::pane { border: 1px solid @border@; border-radius: 12px; top: -1px; background: @surface@; }
QWidget#ProjectPage QTabWidget#ProjectTabs QTabBar::tab { padding: 10px 15px; }
QWidget#ProjectOverview QLabel#ProjectOverviewValue { font-size: 28px; font-weight: 700; }
QWidget#ProjectOverview QFrame#ProjectInfoField { background: #f7faff; border: 1px solid #e6edf8; }
QWidget#ProjectOverview QLabel#ProjectInfoLabel { font-size: 11px; }
QWidget#ProjectOverview QLabel#ProjectInfoValue { font-size: 13px; }
QWidget#ProjectOverview QFrame#ProjectOverviewCard:hover { border-color: #cfdef3; }
QWidget#ProjectPage QLabel#ProjectHeroMeta { color: @secondary@; font-size: 12px; }
QWidget#ProjectHeroFacts { background: @surface@; border: 1px solid @border@; border-radius: 10px; }
QLabel#ProjectHeroFactValue { color: @text@; font-size: 13px; font-weight: 600; }
QWidget#ProjectPage QTabWidget#ProjectTabs::pane { border: 1px solid @border@; border-radius: 12px; background: @canvas@; top: -1px; }
QWidget#ProjectPage QTabWidget#ProjectTabs QTabBar::tab { padding: 9px 14px; margin-right: 4px; border: 1px solid transparent; border-radius: 9px; color: @muted@; }
QWidget#ProjectPage QTabWidget#ProjectTabs QTabBar::tab:selected { color: @accent@; background: @accent_soft@; border-color: @border@; font-weight: 700; }
QWidget#ProjectPage QTabWidget#ProjectTabs QTabBar::tab:hover:!selected { background: @soft@; }
QFrame#TodayMetric[planMetric="daily"], QWidget#PlanningDashboard QFrame#TodayMetric[planMetric="daily"] { background: @surface@; border: 1px solid @border@; }
QFrame#TodayMetric[planMetric="projects"], QWidget#PlanningDashboard QFrame#TodayMetric[planMetric="projects"] { background: @surface@; border: 1px solid @border@; }
QLabel#TodayMetricValue, QLabel#CardValue { color: @text@; font-size: 28px; font-weight: 700; }
QLabel#TodayMetricIcon { border: none; border-radius: 12px; background: @accent_soft@; }
QLabel#TodayMetricIcon[tone="purple"] { background: @purple_soft@; }
QLabel#TodayMetricIcon[tone="warning"] { background: @warning_soft@; }
QLabel#TodayMetricIcon[tone="danger"] { background: @danger_soft@; }
QLabel#TodayMetricIcon[tone="success"] { background: @success_soft@; }
QWidget#PlanningDashboard QFrame#PlanHero { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 @surface@,stop:1 @hero_start@); border: 1px solid @border@; border-radius: 12px; }
QWidget#PlanningDashboard QLabel#PlanTitle { color: @text@; font-size: 26px; font-weight: 700; }
QWidget#PlanningDashboard QLabel#PlanSubtitle { color: @secondary@; font-size: 12px; }
QWidget#PlanningDashboard QFrame#PlanTitleIcon { background: @accent_soft@; border: none; border-radius: 12px; }
QWidget#PlanningDashboard QFrame#PlanDateBar { background: @surface@; border: none; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable { border: 1px solid @border@; border-radius: 9px; background: @surface@; alternate-background-color: @soft@; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QHeaderView::section { background: @soft@; color: @secondary@; font-size: 12px; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag[priority="none"] { background: transparent; color: @secondary@; padding: 0px; }
QWidget#PlanSummaryPanel QTabBar#PlanSummaryTabs::tab { background: @surface@; border: 1px solid @border@; border-bottom: 1px solid @border@; color: @secondary@; }
QWidget#PlanSummaryPanel QTabBar#PlanSummaryTabs::tab:selected { background: @accent_soft@; border-color: @accent_soft@; color: @accent_ink@; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter { background: @soft@; border: 1px solid @border@; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter::tab { color: @secondary@; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter::tab:selected { background: @surface@; border-color: @border@; color: @accent_ink@; }
QWidget#PlanRecordsPanel QLabel#PlanManagerCount { color: @secondary@; }
QWidget#PlanRecordsPanel QPushButton#PlanCreateRecurringButton { color: @accent_ink@; border-color: @border@; }
QWidget#PlanRecordsPanel QPushButton#PlanCreateRecurringButton:hover { background: @accent_soft@; border-color: @accent@; }
QWidget#PlanRecordsPanel QWidget#PlanManagerListPanel, QWidget#PlanRecordsPanel QWidget#PlanManagerDetailPanel { background: @surface@; border-color: @border@; }
QWidget#PlanRecordsPanel QFrame#PlanDetailMetadata { background: @soft@; border: 1px solid @border@; border-radius: 10px; }
QWidget#PlanRecordsPanel QLabel#PlanDetailMetaLabel { color: @secondary@; }
QWidget#PlanRecordsPanel QLabel#PlanDetailMetaValue { color: @text@; }
QWidget#PlanRecordsPanel QLabel#PlanDetailBodyHeading { color: @text@; }
QWidget#PlanRecordsPanel QTextBrowser#PlanDetailBody { background: @surface@; border: 1px solid @border@; color: @text@; }
QWidget#PlanSummaryPanel QListWidget#PlanSummaryList, QWidget#PlanSummaryPanel QTextBrowser#PlanSummaryPreview { background: @surface@; border: 1px solid @border@; border-radius: 10px; }
QWidget#PlanRecordsPanel QTableWidget#PlanManagerTable::item:selected { background: @accent_soft@; color: @accent_ink@; }
QWidget#SettingsPage QPushButton { border-radius: 7px; padding: 7px 12px; }
QWidget#SettingsPage QLineEdit, QWidget#SettingsPage QSpinBox, QWidget#SettingsPage QTextEdit { border-radius: 7px; padding: 7px 10px; }
QWidget#SettingsPage QLabel#SettingsIcon { background: @accent_soft@; border-radius: 12px; }
QTabBar#SettingsTabs { background: @surface@; border: 1px solid @border@; border-radius: 10px; padding: 4px; }
QTabBar#SettingsTabs::tab { padding: 9px 18px; border: 1px solid transparent; border-radius: 7px; color: @secondary@; }
QTabBar#SettingsTabs::tab:selected { background: @accent_soft@; border-color: @border@; color: @accent_ink@; font-weight: 600; }
QDialog#PlanEntryDialog, QDialog#PlanEntryDialog QWidget#PlanEntryContent { background: @canvas@; }
QDialog#PlanEntryDialog QFrame#PlanEntryDateGroup { background: @accent_soft@; border: 1px solid @border@; }
QDialog#PlanEntryDialog QLineEdit, QDialog#PlanEntryDialog QComboBox { border-color: @border_strong@; }
QDialog#PlanEntryDialog QLineEdit:focus, QDialog#PlanEntryDialog QComboBox:focus { border-color: @accent@; }
QDialog#PlanEntryDialog QLineEdit#PlanEntryDate { border: none; background: transparent; }
QDialog#PlanEntryDialog QFrame#PlanEntryFooter { background: @surface@; border-top: 1px solid @border@; }
QDialog#PlanEntryDialog QLabel#PlanEntryHeading { font-size: 24px; color: @text@; }
QDialog#PlanEntryDialog QLabel#PlanEntryDescription { font-size: 12px; color: @secondary@; }
QDialog#PlanEntryDialog QCheckBox#PlanEntryWeekday { border-color: @border_strong@; background: @surface@; color: @secondary@; }
QDialog#PlanEntryDialog QCheckBox#PlanEntryWeekday:checked { border-color: @accent@; background: @accent_soft@; color: @accent_ink@; }
QDialog#PlanEntryDialog QTextEdit#PlanEntryBody { font-family: "Cascadia Code", "Microsoft YaHei UI", "Noto Sans SC"; background: @surface@; color: @text@; }
QDialog#KnowledgeCollectDialog { background: @canvas@; }
QDialog#KnowledgeCollectDialog QFrame#KnowledgeCollectHeader { background: @accent_soft@; border: 1px solid @border@; border-radius: 11px; }
QDialog#KnowledgeCollectDialog QLabel#KnowledgeCollectTitle { color: @text@; font-size: 16px; font-weight: 700; }
QDialog#KnowledgeCollectDialog QLabel#KnowledgeCollectDescription { color: @secondary@; font-size: 12px; }
QDialog#KnowledgeCollectDialog QTabWidget#KnowledgeCollectTabs::pane { background: @surface@; border: 1px solid @border@; border-radius: 10px; top: -1px; }
QDialog#KnowledgeCollectDialog QTabWidget#KnowledgeCollectTabs QTabBar::tab { background: transparent; border: 1px solid transparent; border-radius: 8px; color: @muted@; padding: 8px 14px; margin: 0px 3px 5px 0px; }
QDialog#KnowledgeCollectDialog QTabWidget#KnowledgeCollectTabs QTabBar::tab:selected { background: @accent_soft@; border-color: @border@; color: @accent_ink@; font-weight: 600; }
QDialog#KnowledgeCollectDialog QTabWidget#KnowledgeCollectTabs QTabBar::tab:hover:!selected { background: @soft@; color: @text@; }
QDialog#KnowledgeCollectDialog QLineEdit, QDialog#KnowledgeCollectDialog QComboBox, QDialog#KnowledgeCollectDialog QTextEdit { border-radius: 8px; }
QDialog#KnowledgeCollectDialog QFrame#KnowledgeCollectFooter { border-top: 1px solid @border@; }
QDialog#KnowledgeCollectDialog QDialogButtonBox QPushButton { min-width: 78px; padding: 8px 14px; }

/* Knowledge, Agent and protection workspace surfaces share semantic tokens. */
QWidget#KnowledgePage, QWidget#SafetyPage { background: @canvas@; }
QWidget#KnowledgePage QLabel#Title { font-size: 26px; font-weight: 700; color: @text@; }
QWidget#KnowledgePage QLineEdit#KnowledgeCapture { background: @soft@; border-color: @border@; border-radius: 9px; }
QWidget#KnowledgePage QLineEdit#KnowledgeCapture:focus { background: @surface@; border-color: @accent@; }
QWidget#KnowledgePage QTextBrowser, QWidget#KnowledgePage QListWidget, QWidget#KnowledgePage QTreeWidget#KnowledgeTree { background: @surface@; color: @text@; border-color: @border@; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick, QWidget#KnowledgePage QListWidget#KnowledgeCards { background: transparent; border: none; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item { padding: 4px 10px; margin: 0px 2px; border: none; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item:hover:!selected, QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item:hover:!selected { background: @soft@; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:selected { background: @accent_soft@; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles { background: @surface@; alternate-background-color: @soft@; border: none; color: @text@; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles QHeaderView::section { background: @soft@; color: @muted@; border-bottom: 1px solid @border@; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles::item { border-bottom: 1px solid @border@; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles::item:selected { background: @accent_soft@; color: @accent_ink@; }
QWidget#KnowledgePage QTabWidget::pane { border: none; background: @surface@; }
QWidget#KnowledgePage QTabBar::tab { padding: 8px 8px; }
QWidget#KnowledgePage QToolButton[primary="true"] { background: @accent@; border-color: @accent@; color: #ffffff; }
QWidget#KnowledgePage QToolButton[primary="true"]:hover { background: @accent_hover@; }
QWidget#KnowledgePage QToolButton[primary="true"]:pressed { background: @accent_pressed@; }
QWidget#KnowledgePage QToolButton[primary="true"]:disabled { background: @border_strong@; border-color: @border_strong@; }
QWidget#SafetyPage QFrame#SafetyMetric, QWidget#SafetyPage QFrame#SafetyChoice { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#SafetyPage QFrame#SafetyMetric[metricRole="attention"] { background: @warning_soft@; border-color: @border@; }
QWidget#SafetyPage QLabel#SafetyMetricValue { color: @text@; font-size: 22px; font-weight: 700; }
QWidget#SafetyPage QFrame#SafetyMetric[metricRole="attention"] QLabel#SafetyMetricValue { color: @warning@; }
QWidget#SafetyPage QLabel#SafetyMetricNote { color: @muted@; font-size: 12px; }
QWidget#SafetyPage QFrame#SafetyAttention { background: @soft@; border: 1px solid @border@; border-radius: 12px; }
QWidget#SafetyPage QLabel#SafetyAttentionIcon { background: @accent_soft@; color: @accent_ink@; }
QWidget#SafetyPage QLabel#SafetyEmpty { background: @surface@; color: @muted@; border: 1px dashed @border_strong@; border-radius: 12px; padding: 28px; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable { background: @surface@; alternate-background-color: @soft@; border: 1px solid @border@; border-radius: 12px; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable QHeaderView::section { background: @soft@; color: @muted@; padding: 12px 10px; border-bottom: 1px solid @border@; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable::item { padding: 10px; border-bottom: 1px solid @border@; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable::item:selected { background: @accent_soft@; color: @text@; }
QWidget#SafetyPage QLabel#SafetyResourceName { color: @text@; font-weight: 600; }
QWidget#SafetyPage QLabel#SafetyResourceType { color: @muted@; font-size: 12px; }
QWidget#SafetyPage QListWidget#SafetyVersionList { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 12px; padding: 8px; }
QWidget#SafetyPage QListWidget#SafetyVersionList::item { border: none; border-bottom: 1px solid @border@; padding: 14px 12px; margin: 2px; }
QWidget#SafetyPage QListWidget#SafetyVersionList::item:selected { background: @accent_soft@; color: @accent_ink@; border-radius: 8px; }
QWidget#SafetyPage QPushButton#TextButton { color: @accent_ink@; }
QWidget#SafetyPage QPushButton#TextButton:hover { background: @accent_soft@; }

/* Agent registration browser and management workspace. */

/* Data protection uses a two-column resource workspace. */
QWidget#SafetyPage QFrame#SafetyRegistry, QWidget#SafetyPage QFrame#SafetyDetailCard { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#SafetyPage QWidget#SafetyDetailBody { background: transparent; }
QWidget#SafetyPage QSplitter#SafetyWorkspace::handle { background: transparent; width: 12px; }
QWidget#SafetyPage QLabel#SafetyCount { color: @accent_ink@; background: @accent_soft@; border-radius: 10px; padding: 3px 10px; font-size: 12px; font-weight: 600; }
QWidget#SafetyPage QLabel#SafetyFilterLabel { color: @secondary@; font-size: 11px; font-weight: 600; padding: 2px 1px 0px 1px; }
QWidget#SafetyPage QComboBox#SafetyKindFilter, QWidget#SafetyPage QComboBox#SafetyStateFilter { min-height: 30px; padding-left: 9px; padding-right: 8px; border-radius: 8px; background: @soft@; }
QWidget#SafetyPage QComboBox#SafetyKindFilter:hover, QWidget#SafetyPage QComboBox#SafetyStateFilter:hover { border-color: @accent@; }
QWidget#SafetyPage QComboBox#SafetyKindFilter:focus, QWidget#SafetyPage QComboBox#SafetyStateFilter:focus { border-color: @accent@; }
QWidget#SafetyPage QListWidget#SafetyRegistryList { background: transparent; border: none; padding: 0px; outline: none; }
QWidget#SafetyPage QListWidget#SafetyRegistryList::item { color: @secondary@; background: transparent; margin: 2px 0px; padding: 7px 8px; border: 1px solid transparent; border-radius: 9px; }
QWidget#SafetyPage QListWidget#SafetyRegistryList::item:hover:!selected { background: @soft@; border-color: @border@; }
QWidget#SafetyPage QListWidget#SafetyRegistryList::item:selected { background: @accent_soft@; color: @accent_ink@; border-color: @border@; }
QWidget#SafetyPage QFrame#SafetyHero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 @hero_start@,stop:1 @hero_end@); border: 1px solid @border@; border-radius: 12px; }
QWidget#SafetyPage QLabel#SafetyDetailName { color: @text@; font-size: 22px; font-weight: 700; }
QWidget#SafetyPage QLabel#SafetyHealthState { color: @warning@; font-size: 20px; font-weight: 700; }
QWidget#SafetyPage QFrame#SafetyAttention[attentionState="healthy"] { background: @success_soft@; border-color: @border@; }
QWidget#SafetyPage QFrame#SafetyAttention[attentionState="healthy"] QLabel#SafetyHealthState { color: @success@; }
QWidget#SafetyPage QFrame#SafetyAttention[attentionState="attention"] { background: @warning_soft@; border-color: @border@; }
QWidget#SafetyPage QTabWidget#SafetyDetailTabs::pane { background: transparent; border: none; }
QWidget#SafetyPage QTabWidget#SafetyDetailTabs QTabBar::tab { padding: 8px 12px; margin: 0px 3px 0px 0px; color: @muted@; border: none; border-bottom: 2px solid transparent; border-radius: 0px; }
QWidget#SafetyPage QTabWidget#SafetyDetailTabs QTabBar::tab:selected { color: @accent_ink@; border-bottom: 2px solid @accent@; background: transparent; }
QWidget#SafetyPage QTabWidget#SafetyDetailTabs QLabel#SafetyMetricValue { font-size: 18px; }

/* Knowledge navigation: complete quick views and continuous topic selection. */
QWidget#KnowledgePage QLabel#KnowledgeVaultName { background: @soft@; color: @muted@; border-radius: 7px; padding: 7px 8px; font-size: 11px; }
QWidget#KnowledgePage QLabel#KnowledgeNavigationHeading { color: @muted@; font-size: 11px; font-weight: 600; padding: 4px 2px 0px 2px; }
QWidget#KnowledgePage QPushButton#KnowledgeRootButton { min-height: 24px; padding: 3px; border: none; background: transparent; border-radius: 7px; }
QWidget#KnowledgePage QPushButton#KnowledgeRootButton:hover { background: @soft@; }
QWidget#KnowledgePage QPushButton#KnowledgeNewTopic { background: @accent_soft@; border-color: @border@; color: @accent_ink@; font-weight: 600; padding: 6px 10px; min-height: 22px; border-radius: 8px; }
QWidget#KnowledgePage QPushButton#KnowledgeNewTopic:hover { background: @soft@; border-color: @accent@; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick { background: transparent; border: none; padding: 0px; outline: none; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item { color: @secondary@; margin: 0px; padding: 5px 8px; border: none; border-radius: 8px; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item:selected { background: @accent_soft@; color: @accent_ink@; font-weight: 600; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree { background: transparent; border: none; padding: 0px; outline: none; show-decoration-selected: 1; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item { color: @secondary@; min-height: 26px; padding: 5px 4px; margin: 0px; border: none; border-radius: 0px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item:selected, QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:selected { background: @accent_soft@; color: @accent_ink@; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:hover:!selected { background: @soft@; }

QWidget#KnowledgePage QWidget#KnowledgePanel[knowledgeRole="sidebar"] { background: transparent; border: none; border-radius: 0px; }
QWidget#KnowledgePage QFrame#KnowledgeEmptyCard { background: transparent; border: none; }
QWidget#KnowledgePage QLabel#KnowledgeEmptyTitle { font-size: 22px; font-weight: 700; color: @text@; }
QWidget#KnowledgePage QLabel#KnowledgeEmptyDescription { font-size: 13px; color: @secondary@; padding: 2px 0px 8px 0px; }
QWidget#KnowledgePage QLabel#KnowledgeWelcomeSteps { color: @muted@; font-size: 11px; padding-top: 12px; }
QWidget#KnowledgePage QFrame#KnowledgeLocation { background: transparent; border: none; border-radius: 0px; }
QWidget#KnowledgePage QLabel#KnowledgeLocationPath { color: @muted@; font-size: 11px; font-weight: 400; }
QWidget#KnowledgePage QPushButton#KnowledgeLocationOpen { background: transparent; border: none; color: @accent_ink@; padding: 2px 4px; }
QWidget#KnowledgePage QPushButton#KnowledgeDetailBack { background: transparent; border: none; color: @accent_ink@; padding: 3px 0px; text-align: left; }
QWidget#KnowledgePage QTextBrowser { border: none; background: @surface@; }
QWidget#KnowledgePage QLabel#KnowledgeVaultName { background: transparent; padding: 0px 2px; color: @muted@; }
QWidget#KnowledgePage QLineEdit#KnowledgeCapture { background: @soft@; border-color: @border@; border-radius: 9px; padding: 7px 10px; }

QWidget#PlanningDashboard QFrame#PlanHero { background: transparent; border: none; border-radius: 0px; }
QWidget#PlanningDashboard QFrame#TodayCard[planSection="daily"] { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable { border: none; border-radius: 0px; outline: none; background: @surface@; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable::item { padding: 12px 8px; border: none; border-bottom: 1px solid @border@; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable::item:selected { background: @accent_soft@; color: @text@; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QPushButton, QWidget#PlanningDashboard QTableWidget#PlanDayTable QToolButton { background: transparent; border: none; padding: 6px; color: @secondary@; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QPushButton:hover, QWidget#PlanningDashboard QTableWidget#PlanDayTable QToolButton:hover { background: @accent_soft@; color: @accent_ink@; }
QWidget#PlanningDashboard QLabel#PlanDayLabel { font-size: 14px; color: @text@; font-weight: 600; }
QWidget#PlanRecordsPanel QWidget#PlanDetailContent { background: @surface@; }
QWidget#PlanRecordsPanel QFrame#PlanDetailTitleCard { border: none; background: @surface@; }
QWidget#PlanRecordsPanel QFrame#PlanDetailMetadata { border: none; background: @soft@; }
QWidget#PlanRecordsPanel QTextBrowser#PlanDetailBody { border: none; }
QWidget#KnowledgePage QLabel#KnowledgeDetailTitle { font-size: 20px; font-weight: 700; color: @text@; padding: 2px 0px 6px 0px; }
QWidget#KnowledgePage QSplitter#KnowledgeReadingWorkspace::handle { background: transparent; width: 12px; height: 12px; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles QHeaderView::section { background: @surface@; padding: 10px 8px; border: none; border-bottom: 1px solid @border@; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles::item { padding: 8px; border: none; border-bottom: 1px solid @border@; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item { border-radius: 7px; margin: 2px 0px; }
QWidget#KnowledgePage QToolButton { padding: 8px 12px; border: 1px solid @border_strong@; border-radius: 7px; background: @surface@; color: @secondary@; }
QWidget#KnowledgePage QToolButton:hover { background: @soft@; }
QWidget#KnowledgePage QPushButton#KnowledgeDetailBack { color: @muted@; font-size: 12px; padding: 4px; }
QWidget#KnowledgePage QTabWidget QTabBar::tab { padding: 9px 12px; }

/* Personal action center: time navigation, a readable list and an optional drawer. */
QWidget#ActionPage { background: @canvas@; }
QWidget#ActionPage QLabel#ActionSidebarTitle { font-size: 21px; font-weight: 700; padding: 4px 2px 14px 2px; }
QWidget#ActionPage QLabel#ActionPageTitle { font-size: 27px; font-weight: 700; }
QWidget#ActionPage QListWidget#ActionNavigation { background: transparent; border: none; outline: none; padding: 8px 0px; }
QWidget#ActionPage QListWidget#ActionNavigation::item { border: none; border-radius: 8px; padding: 10px 8px; color: @secondary@; }
QWidget#ActionPage QListWidget#ActionNavigation::item:selected { background: @accent_soft@; color: @accent_ink@; font-weight: 600; }
QWidget#ActionPage QListWidget#ActionNavigation::item:hover { background: @soft@; }
QWidget#ActionPage QToolButton[primary="true"] { background: @accent@; color: #ffffff; border: none; border-radius: 8px; padding: 10px; font-weight: 600; }
QWidget#ActionPage QToolButton[primary="true"]:hover { background: @accent_hover@; }
QWidget#ActionPage QListWidget#ActionList { background: @surface@; border: 1px solid @border@; border-radius: 12px; outline: none; padding: 12px; }
QWidget#ActionPage QListWidget#ActionList::item { border: none; border-bottom: 1px solid @border@; padding: 13px 9px; color: @text@; }
QWidget#ActionPage QListWidget#ActionList::item:selected { background: @accent_soft@; color: @text@; border-radius: 8px; }
QWidget#ActionPage QListWidget#ActionList::item:disabled { color: @muted@; border: none; padding: 12px 8px 5px 8px; font-size: 12px; }
QWidget#ActionPage QListWidget#ActionList::indicator { width: 16px; height: 16px; background: @surface@; border: 1px solid @border_strong@; border-radius: 4px; }
QWidget#ActionPage QListWidget#ActionList::indicator:checked { background: @accent@; border-color: @accent@; image: url("@assets@/check.svg"); }
QWidget#ActionPage QLabel#ActionEmpty { color: @muted@; background: @surface@; border: 1px solid @border@; border-radius: 12px; padding: 24px; }
QWidget#ActionPage QFrame#ActionPlanSection { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#ActionPage QLabel#ActionSectionTitle { color: @text@; font-size: 18px; font-weight: 700; }
QWidget#ActionPage QFrame#ActionPlanCard { background: @surface@; border: 1px solid @border@; border-radius: 9px; }
QWidget#ActionPage QPushButton#ActionPlanTitleButton { color: @text@; background: transparent; border: none; padding: 2px 0px; text-align: left; font-weight: 600; }
QWidget#ActionPage QLabel#ActionProjectTag { color: @accent_ink@; background: @accent_soft@; border-radius: 8px; padding: 3px 8px; }
QWidget#ActionPage QLabel#ActionPlanEmpty { color: @muted@; padding: 8px 2px; }
QWidget#ActionPage QProgressBar#ActionPlanProgress { background: @soft@; border: none; border-radius: 4px; }
QWidget#ActionPage QProgressBar#ActionPlanProgress::chunk { background: @accent@; border-radius: 4px; }
QWidget#ActionPage QWidget#ActionDrawer { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#ActionPage QSplitter::handle { background: transparent; width: 12px; }
QWidget#ActionEditor { background: @surface@; border-radius: 12px; }
QWidget#ActionEditor QLabel#ActionEditorHeading { font-size: 20px; font-weight: 700; padding-bottom: 6px; }
QWidget#ActionEditor QScrollArea { background: transparent; border: none; }
QWidget#ActionEditor QWidget#ActionEditorContent { background: @surface@; }
QWidget#ActionEditor QLineEdit, QWidget#ActionEditor QComboBox, QWidget#ActionEditor QDateEdit { padding: 8px; min-height: 22px; }
QWidget#ActionEditor QToolButton { color: @accent_ink@; background: transparent; border: none; padding: 7px 0px; text-align: left; }
QWidget#ActionEditor QLabel#ActionFormError { color: @danger@; }
/* Markdown composer aligned to the six-entry planning prototype. */
QWidget#ActionPage QWidget#ActionSidebar { background: @surface@; border-radius: 10px; }
QWidget#ActionPage QLabel#PlanFieldLabel { color: @muted@; font-size: 12px; }
QWidget#ActionPage QLabel#PlanTemplateBadge { color: @accent_ink@; background: @accent_soft@; border-radius: 14px; padding: 6px 12px; font-size: 12px; }
QWidget#ActionPage QLabel#PlanDraftStatus { color: @muted@; background: @surface@; border: 1px solid @border@; border-radius: 16px; padding: 8px 15px; font-size: 12px; }
QWidget#ActionPage QFrame#PlanSeparator { background: @border@; border: none; }
QWidget#ActionPage QPushButton#PlanTypeTab { color: @muted@; background: @surface@; border: 1px solid @border@; border-radius: 8px; padding: 9px 6px; font-size: 12px; }
QWidget#ActionPage QPushButton#PlanTypeTab:checked { color: @accent_ink@; background: @accent_soft@; border-color: @border@; }
QWidget#ActionPage QPushButton#PlanTypeTab:hover { background: @soft@; }
QWidget#ActionPage QPushButton#PlanTemplateChip { color: @muted@; background: @soft@; border: none; border-radius: 12px; padding: 6px; font-size: 11px; }
QWidget#ActionPage QPushButton#PlanTemplateChip:checked { background: @accent_soft@; color: @accent_ink@; }
QWidget#ActionPage QFrame#PlanDocumentSource { background: @soft@; border: none; border-radius: 10px; padding: 10px; }
QWidget#ActionPage QLabel#PlanDocumentTarget { background: @accent_soft@; color: @accent_ink@; border: 1px solid @border@; border-radius: 10px; padding: 16px; }
QWidget#ActionPage QLabel#PlanFlowArrow { color: @muted@; font-size: 24px; }
QWidget#ActionPage QListWidget#PlanRecentDocuments { background: transparent; border: none; outline: none; }
QWidget#ActionPage QListWidget#PlanRecentDocuments::item { border: none; border-radius: 6px; padding: 10px 2px; color: @text@; }
QWidget#ActionPage QListWidget#PlanRecentDocuments::item:selected { background: @accent_soft@; }
QWidget#ActionPage QPushButton#PlanFolderButton { background: transparent; border: none; color: @secondary@; text-align: left; padding: 8px 0px; }
QWidget#ActionPage QToolButton#PlanMoreLinks { background: transparent; border: none; color: @muted@; padding: 0px; font-size: 12px; }
/* Category pages: compact summaries, actionable rows, and separate review sections. */
QWidget#ActionPage QFrame#PlanModuleSummary { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#ActionPage QLabel#PlanMetricValue { color: @text@; font-size: 23px; font-weight: 650; }
QWidget#ActionPage QLabel#PlanSecondary { color: @muted@; font-size: 12px; }
QWidget#ActionPage QListWidget#ActionList { background: transparent; border: none; padding: 0px; }
QWidget#ActionPage QListWidget#ActionList::item { border: none; padding: 0px; background: transparent; }
QWidget#ActionPage QListWidget#ActionList::item:selected { background: transparent; }
QWidget#ActionPage QListWidget#ActionList::item:disabled { padding: 4px 8px; color: @secondary@; font-weight: 600; }
QWidget#ActionPage QListWidget#ActionList::indicator { width: 0px; height: 0px; border: none; }
QWidget#ActionPage QFrame#PlanActionRow, QWidget#ActionPage QFrame#PlanListCard, QWidget#ActionPage QFrame#PlanReviewSection, QWidget#ActionPage QFrame#PlanEmptyCard { background: @surface@; border: 1px solid @border@; border-radius: 12px; }
QWidget#ActionPage QPushButton#PlanRowTitle { color: @text@; font-size: 15px; font-weight: 600; text-align: left; border: none; background: transparent; padding: 0px; }
QWidget#ActionPage QPushButton#PlanRowAction { color: @accent_ink@; text-align: left; border: none; background: transparent; padding: 5px; }
QWidget#ActionPage QLabel#PlanStatusBadge { color: @accent_ink@; background: @accent_soft@; border-radius: 8px; padding: 4px 9px; font-size: 12px; }
QWidget#ActionPage QLabel#PlanStatusBadge[done="true"] { color: @secondary@; background: @soft@; }
QWidget#ActionPage QLabel#ProjectTaskStatusBadge { border-radius: 8px; padding: 4px 9px; font-size: 12px; }
QWidget#ActionPage QLabel#ProjectTaskStatusBadge[status="planned"] { color: @warning@; background: @warning_soft@; }
QWidget#ActionPage QLabel#ProjectTaskStatusBadge[status="active"] { color: @accent_ink@; background: @accent_soft@; }
QWidget#ActionPage QLabel#ProjectTaskStatusBadge[status="done"] { color: @success@; background: @success_soft@; }
QWidget#ActionPage QPushButton#EditProjectTaskStatus { color: @accent_ink@; text-align: left; border: none; background: transparent; padding: 5px; }
QWidget#ActionPage QLabel#PlanSectionHeading { color: @text@; font-size: 17px; font-weight: 600; }
QWidget#ActionPage QLabel#PlanReviewActionTitle { color: @text@; font-size: 13px; }
QWidget#ActionPage QLabel#PlanEmptyText { color: @muted@; font-size: 14px; }
QWidget#ActionPage QProgressBar#PlanListProgress { background: @soft@; border: none; border-radius: 3px; }
QWidget#ActionPage QProgressBar#PlanListProgress::chunk { background: @accent@; border-radius: 3px; }
QDialog#PlanDocumentDialog { background: @surface@; }
QDialog#PlanDocumentDialog QLabel#ActionSectionTitle { color: @text@; font-size: 21px; font-weight: 650; }
QDialog#PlanDocumentDialog QLabel#ActionFormError { color: @danger@; }
QWidget#ActionPage QPlainTextEdit#MarkdownPlanEditor { background: @canvas@; color: @text@; border: 1px solid @border@; border-radius: 10px; padding: 0px; font-size: 14px; selection-background-color: @accent_soft@; }
QWidget#ActionPage QWidget#MarkdownMetadata { background: @soft@; border: none; border-bottom: 1px solid @border@; }
QWidget#ActionPage QLabel#MarkdownHeaderNumbers { color: @muted@; font-size: 13px; background: transparent; }
QWidget#ActionPage QLabel#MarkdownHeaderText { color: @secondary@; font-size: 13px; background: transparent; }

"""
