from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

from .design import palette, stylesheet


def setup_theme(app) -> None:
    """Bundle a CJK font so fresh machines and offscreen QA stay readable."""
    font = Path(__file__).resolve().parents[1] / "assets/fonts/NotoSansSC-Regular.otf"
    identity = QFontDatabase.addApplicationFont(str(font))
    families = QFontDatabase.applicationFontFamilies(identity) if identity >= 0 else []
    ui_font = QFont()
    ui_font.setFamilies(["Microsoft YaHei UI", "Segoe UI", *families, "sans-serif"])
    ui_font.setPixelSize(13)
    ui_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(ui_font)
    app.setStyle("Fusion")
    apply_theme(app, "light")


def apply_theme(app, mode="light"):
    if app is None:
        return
    mode = "dark" if mode == "dark" else "light"
    app.setProperty("appearance", mode)
    app.setPalette(palette(mode))
    app.setStyleSheet(stylesheet(STYLE, mode))
    for window in app.topLevelWidgets():
        window.update()



STYLE = """
QWidget { font-size: 13px; color: #27334f; }
QMainWindow, QWidget#Content { background: #f5f8fd; }
QWidget#DashboardBody { background: #f5f6fb; }
QWidget#Sidebar { background: #f0f6fd; border-right: 1px solid #e1eaf5; }
QLabel#BrandIcon { background: transparent; border: none; }
QLabel#Brand { color: #182943; font-size: 15px; font-weight: 700; }
QLabel#BrandSub { color: #8998ad; font-size: 10px; }
QListWidget#Navigation { background: transparent; border: none; color: #40516b; outline: none; }
QListWidget#Navigation::item { padding: 10px 12px; margin: 3px 0px; border-radius: 9px; border: none; }
QListWidget#Navigation::item:selected { background: #dcecff; color: #176be2; font-weight: 650; }
QListWidget#Navigation::item:hover { background: #e8f2fd; }
QWidget#GlobalTopbar { background: #f8fbff; border-bottom: 1px solid #e5edf7; }
QLabel#GlobalTopbarMeta { color: #697b94; font-size: 12px; }
QPushButton#TopbarIconButton { border: none; background: transparent; color: #536680; font-size: 18px; padding: 4px 8px; }
QPushButton#TopbarIconButton:hover { background: #e8f2fd; }
QLabel#ProfileBadge { background: #2879f6; border-radius: 16px; color: #ffffff; font-weight: 700; }
QListWidget#ActionList { background: #ffffff; border: none; }
QListWidget#ActionList::item { padding: 5px; border-bottom: 1px solid #edf0f6; }
QListWidget { background: #ffffff; border: 1px solid #e8ebf4; border-radius: 10px; outline: none; }
QListWidget::item { padding: 10px 12px; border-bottom: 1px solid #f0f2f7; }
QListWidget::item:selected { background: #eef1fe; color: #34499a; }
QLabel#Title { color: #17213b; font-size: 27px; font-weight: 700; }
QLabel#Subtitle { color: #7a849c; }
QLabel#SectionTitle { color: #27334f; font-size: 15px; font-weight: 600; }
QLabel#HeroTitle { color: #ffffff; font-size: 26px; font-weight: 700; }
QLabel#HeroNote { color: #cad5fc; }
QFrame#Hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #293866,stop:1 #4a5d9a); border-radius: 14px; }
QLabel#CardValue { font-size: 31px; font-weight: 700; color: #3e55ad; }
QFrame#Card { background: #ffffff; border: 1px solid #e8ebf4; border-radius: 12px; }
QPushButton { background: #ffffff; border: 1px solid #dfe4ef; padding: 9px 15px; border-radius: 8px; min-height: 18px; }
QPushButton:hover { background: #edf0fc; border-color: #aab7e1; }
QPushButton:pressed { background: #dfe5f9; }
QPushButton:focus { border: 1px solid #647bd1; }
QPushButton:disabled { color: #a4acc0; background: #eff1f6; border-color: #e6e9f1; }
QPushButton[primary="true"] { background: #5368c5; border-color: #5368c5; color: #ffffff; }
QPushButton[primary="true"]:hover { background: #6479d5; }
QPushButton[primary="true"]:disabled { background: #b2bce0; border-color: #b2bce0; }
QPushButton[danger="true"] { color: #b85060; }
QLineEdit, QComboBox, QSpinBox, QDateEdit { background: #ffffff; border: 1px solid #dfe4ef; border-radius: 7px; padding: 8px; min-height: 20px; selection-background-color: #5368c5; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: #7184d0; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #edf0fc; selection-color: #27334f; }
QTableWidget { background: #ffffff; alternate-background-color: #fafbfe; border: 1px solid #e8ebf4; border-radius: 10px; gridline-color: #f0f2f7; selection-background-color: #eef1fe; selection-color: #34499a; outline: none; }
QTableWidget::item { padding: 4px 8px; border: none; }
QHeaderView::section { background: #f9faff; border: none; border-bottom: 1px solid #e8ebf4; padding: 12px 8px; color: #8590a8; font-weight: 500; }
QTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #e8ebf4; border-radius: 10px; padding: 14px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #ced5e7; min-height: 32px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QDialog { background: #f5f6fb; }
QLabel#TodayDialogTitle { color: #17213b; font-size: 21px; font-weight: 700; }
QFrame#TodayDialogCard { background: #ffffff; border: 1px solid #e2e7f1; border-radius: 12px; }
QDialogButtonBox QPushButton { min-width: 74px; }
QProgressBar { border: none; background: #e3e7f5; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #7489d8; }
QStatusBar { background: #ffffff; border-top: 1px solid #e8ebf4; color: #8590a8; padding: 3px; }
QCheckBox { spacing: 8px; }
QGroupBox { border: 1px solid #e2e7f1; border-radius: 9px; margin-top: 16px; padding-top: 15px; }
QGroupBox::title { subcontrol-origin: margin; padding: 0 8px; color: #687594; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 9px 12px; color: #8490aa; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #5368c5; border-bottom: 2px solid #5368c5; }
QSplitter::handle { background: transparent; width: 12px; height: 12px; }
QFrame#TrendBar { background: #9dadE9; border-radius: 3px; }
QFrame#DirectoryCard { background: #ffffff; border: 1px solid #e2e7f1; border-radius: 8px; }
QTextEdit#DirectoryPath { border: none; padding: 0px; background: transparent; color: #394f92; }
QToolTip { background: #24314f; color: #ffffff; border: none; padding: 6px; }
QWidget#SettingsBody { background: #f5f6fb; }
QWidget#SettingsPage QLabel#Title { font-size: 22px; }
QWidget#SettingsPage QPushButton { border-radius: 3px; padding: 6px 12px; }
QWidget#SettingsPage QPushButton[primary="true"] { background: #4d5b76; border-color: #4d5b76; }
QWidget#SettingsPage QPushButton[primary="true"]:hover { background: #3c4860; }
QWidget#SettingsPage QLineEdit, QWidget#SettingsPage QSpinBox { border-radius: 3px; padding: 6px; }
QWidget#SettingsPage QTextEdit { border-radius: 3px; padding: 8px; }
QWidget#SettingsPage QToolButton { border: none; background: transparent; padding: 6px 0px; color: #4d5b76; }
QWidget#SettingsPage QToolButton:hover { color: #26334f; }
QFrame#SettingsCard { background: #ffffff; border: 1px solid #e8edf5; border-radius: 12px; }
QLabel#SettingsIcon { background: #edf4ff; color: #3979ff; border-radius: 12px; font-size: 22px; font-weight: 600; }
QLabel#SettingsCardTitle { color: #20385e; font-size: 16px; font-weight: 650; }
QWidget#SettingsStatusPanel QLabel#SettingsStatusTile { background: #f6f8fc; border: 1px solid #edf1f7; border-radius: 10px; padding: 12px; color: #425776; }
QWidget#SettingsPage QPushButton { border-radius: 8px; padding: 8px 13px; }
QWidget#SettingsPage QPushButton[primary="true"] { background: #3979ff; border-color: #3979ff; color: #ffffff; }
QWidget#SettingsPage QPushButton[primary="true"]:hover { background: #2866e8; }
QWidget#SettingsPage QLineEdit, QWidget#SettingsPage QSpinBox { border-radius: 7px; padding: 8px; }
QWidget#SettingsPage QToolButton { border-radius: 6px; padding: 7px 9px; }
QPushButton#SidebarHelp { background: transparent; color: #526783; border: 1px solid #d6e2f0; border-radius: 8px; padding: 6px; }
QPushButton#SidebarHelp:hover { background: #e8f2fd; color: #176be2; }
QWidget#TodayPage, QWidget#TodayBody { background: transparent; }
QLabel#TodayDate { color: #15294e; font-size: 27px; font-weight: 700; }
QLabel#TodayMuted { color: #8592ac; font-size: 11px; }
QFrame#TodayMetric, QFrame#TodayCard { background: #ffffff; border: 1px solid #edf1f8; border-radius: 14px; }
QFrame#TodayMetric { min-height: 88px; }
QLabel#TodayMetricTitle, QLabel#TodaySectionTitle { color: #20385e; font-size: 13px; font-weight: 600; }
QLabel#TodayMetricValue { color: #17305c; font-size: 24px; font-weight: 700; }
QWidget#TodayPage QPushButton[primary="true"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4e8aff,stop:1 #2864ed); border-color: #3976f4; }
QWidget#TodayPage QPushButton[primary="true"]:hover { background: #3976f4; }
QFrame#TodayFocus { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #f4f9ff,stop:1 #eef5ff); border: 1px solid #e2ebf9; border-radius: 14px; }
QFrame#TodayFocus QFrame#TodayFocusDivider { background: #dee9f8; border: none; }
QWidget#TodayPage QLabel#TodayFocusValue { color: #17305c; font-size: 26px; font-weight: 700; }
QWidget#TodayPage QLabel#TodayFocusPercent { color: #5b8ad6; font-size: 12px; font-weight: 600; }
QWidget#TodayPage QLabel#TodayFocusKicker { color: #7f90ab; font-size: 12px; }
QWidget#TodayPage QLabel#TodayFocusNext { color: #16294a; font-size: 15px; font-weight: 650; }
QFrame#TodayFocus QProgressBar { background: #e2eaf6; border: none; border-radius: 3px; max-height: 6px; }
QFrame#TodayFocus QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6fa5ff,stop:1 #2f6fd8); border-radius: 3px; }
QWidget#TodayPage QLabel#TodayGroupTitle { color: #64748b; font-size: 12px; font-weight: 700; padding: 4px 0px 1px 0px; }
QWidget#TodayPage QLabel#TodayGroupTitle[tone="danger"] { color: #c04f4b; }
QWidget#TodayPage QLabel#TodayGroupTitle[tone="accent"] { color: #2f6fd8; }
QWidget#TodayPage QPushButton#TodayChip { background: #f5f8fd; border: 1px solid #e6edf8; border-radius: 8px; color: #526783; font-size: 12px; padding: 5px 11px; min-height: 18px; }
QWidget#TodayPage QPushButton#TodayChip:hover { background: #edf5ff; border-color: #c8ddff; color: #2866d7; }
QWidget#TodayPage QPushButton#TodayChip:pressed { background: #e2edff; }
QWidget#TodayPage QLabel#TodaySectionTitle { font-size: 15px; font-weight: 650; }
QWidget#TodayPage QLabel#TodayEmptyTitle { color: #20385e; font-size: 18px; font-weight: 650; }
QWidget#TodayPage QLabel#TodayEmptyNote { color: #71809e; font-size: 12px; }
QWidget#TodayPage QScrollArea#TodayScroll { border: none; background: transparent; }
QWidget#TodayPage QWidget#TodayDateBar QPushButton { background: transparent; border: none; padding: 4px; }
QWidget#TodayPage QWidget#TodayDateBar QPushButton:hover { background: #eaf2ff; }

QDialog#PlanEntryDialog { background: #f4f7fc; }
QDialog#PlanEntryDialog QWidget#PlanEntryContent { background: #f4f7fc; }
QDialog#PlanEntryDialog QLabel#PlanEntryIcon { background: #e9f3ff; border-radius: 13px; }
QDialog#PlanEntryDialog QLabel#PlanEntryHeading { color: #17213b; font-size: 24px; font-weight: 750; }
QDialog#PlanEntryDialog QLabel#PlanEntryDescription { color: #71809e; font-size: 13px; }
QDialog#PlanEntryDialog QLabel#PlanEntryFieldLabel { color: #27334f; font-size: 14px; font-weight: 650; }
QDialog#PlanEntryDialog QLineEdit, QDialog#PlanEntryDialog QComboBox, QDialog#PlanEntryDialog QDateEdit { min-height: 26px; border: 1px solid #d5dfef; border-radius: 9px; padding: 10px 12px; background: #ffffff; color: #27334f; font-size: 14px; }
QDialog#PlanEntryDialog QLineEdit:focus, QDialog#PlanEntryDialog QComboBox:focus, QDialog#PlanEntryDialog QDateEdit:focus { border-color: #6b9df5; }
QDialog#PlanEntryDialog QComboBox::drop-down, QDialog#PlanEntryDialog QDateEdit::drop-down { width: 32px; border: none; }
QDialog#PlanEntryDialog QFrame#PlanEntryDateControl { background: #ffffff; border: 1px solid #d5dfef; border-radius: 9px; }
QDialog#PlanEntryDialog QLineEdit#PlanEntryDate { min-height: 26px; border: none; border-radius: 0px; padding: 7px 4px; background: transparent; }
QDialog#PlanEntryDialog QPushButton#PlanEntryCalendarButton { min-width: 30px; min-height: 30px; padding: 4px; border: none; border-radius: 7px; background: transparent; }
QDialog#PlanEntryDialog QPushButton#PlanEntryCalendarButton:hover { background: #edf4ff; }
QDialog#PlanEntryCalendarPopup { background: #ffffff; border: 1px solid #dce4f0; border-radius: 9px; }
QDialog#PlanEntryCalendarPopup QCalendarWidget { background: #ffffff; }
QDialog#PlanEntryDialog QFrame#PlanEntryDateGroup { background: #edf4ff; border: 1px solid #e5efff; border-radius: 13px; }
QDialog#PlanEntryDialog QFrame#PlanEntryDateDivider { background: #ccdaee; border: none; }
QDialog#PlanEntryDialog QLabel#PlanEntryDateHint { color: #71809e; font-size: 12px; }
QDialog#PlanEntryDialog QCheckBox#PlanEntryWeekday { spacing: 5px; padding: 5px 7px; border: 1px solid #d5dfef; border-radius: 8px; background: #ffffff; color: #52627c; }
QDialog#PlanEntryDialog QCheckBox#PlanEntryWeekday:checked { border-color: #8bb4f7; background: #eaf2ff; color: #2866d7; font-weight: 650; }
QDialog#PlanEntryDialog QTextEdit#PlanEntryBody { min-height: 120px; background: #ffffff; border: 1px solid #dce4f0; border-radius: 10px; padding: 13px 15px; font-family: "Cascadia Code", "Consolas", monospace; }
QDialog#PlanEntryDialog QFrame#PlanEntryFooter { background: #ffffff; border-top: 1px solid #e1e8f2; }
QDialog#PlanEntryDialog QPushButton { min-width: 94px; min-height: 24px; padding: 9px 16px; border-radius: 9px; background: #ffffff; border-color: #dce4f0; color: #42516b; }
QDialog#PlanEntryDialog QPushButton:hover { background: #f3f7fd; }
QDialog#PlanEntryDialog QPushButton[primary="true"] { background: #2878f5; border-color: #2878f5; color: #ffffff; font-weight: 650; }
QDialog#PlanEntryDialog QPushButton[primary="true"]:hover { background: #1769e8; border-color: #1769e8; }
QWidget#PlanRecordsPanel { background: transparent; }
QWidget#PlanRecordsPanel QWidget#PlanManagerListPanel, QWidget#PlanRecordsPanel QWidget#PlanManagerDetailPanel { background: #ffffff; border: 1px solid #e4eaf3; border-radius: 14px; }
QWidget#PlanRecordsPanel QLabel#PlanManagerIcon, QWidget#PlanRecordsPanel QLabel#PlanDetailIcon { background: #eaf2ff; border-radius: 12px; }
QWidget#PlanRecordsPanel QLabel#PlanManagerTitle { color: #17213b; font-size: 23px; font-weight: 750; }
QWidget#PlanRecordsPanel QLabel#PlanManagerDescription { color: #71809e; font-size: 12px; }
QWidget#PlanRecordsPanel QLineEdit#PlanManagerSearch { min-height: 28px; padding: 9px 13px; border-radius: 10px; background: #ffffff; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter { background: #f4f7fc; border: 1px solid #e4eaf3; border-radius: 9px; padding: 3px; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter::tab { background: transparent; border: 1px solid transparent; border-radius: 7px; color: #71809e; padding: 7px 12px; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter::tab:selected { background: #ffffff; border-color: #dce4f0; color: #2866d7; font-weight: 650; }
QWidget#PlanRecordsPanel QTabBar#PlanManagerFilter::tab:hover:!selected { background: #eaf2ff; }
QWidget#PlanRecordsPanel QLabel#PlanManagerCount { color: #71809e; font-size: 11px; }
QWidget#PlanRecordsPanel QTableWidget#PlanManagerTable { background: #ffffff; alternate-background-color: #f8faff; border: 1px solid #edf1f7; border-radius: 10px; gridline-color: transparent; selection-background-color: #edf4ff; selection-color: #20385e; }
QWidget#PlanRecordsPanel QTableWidget#PlanManagerTable::item { padding: 8px 9px; border-bottom: 1px solid #edf1f7; }
QWidget#PlanRecordsPanel QTableWidget#PlanManagerTable::item:selected { background: #eaf2ff; color: #1d4d96; }
QWidget#PlanRecordsPanel QTableWidget#PlanManagerTable QHeaderView::section { background: #f5f8fd; color: #71809e; padding: 10px 9px; border: none; border-bottom: 1px solid #e8edf5; font-weight: 650; }
QWidget#PlanRecordsPanel QProgressBar#PlanManagerRowProgress { min-height: 14px; max-height: 14px; border: none; border-radius: 7px; background: #e9eef6; color: #405575; font-size: 9px; text-align: center; }
QWidget#PlanRecordsPanel QProgressBar#PlanManagerRowProgress::chunk { border-radius: 6px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #54a0ff,stop:1 #2878f5); }
QWidget#PlanRecordsPanel QLabel#PlanDetailSectionTitle { color: #17213b; font-size: 18px; font-weight: 750; padding: 1px 2px; }
QWidget#PlanRecordsPanel QFrame#PlanDetailTitleCard { background: #ffffff; border: 1px solid #e4eaf3; border-radius: 11px; }
QWidget#PlanRecordsPanel QLabel#PlanDetailTitle { color: #17213b; font-size: 20px; font-weight: 750; }
QWidget#PlanRecordsPanel QScrollArea#PlanDetailScroll { background: transparent; border: none; }
QWidget#PlanRecordsPanel QFrame#PlanDetailMetadata { background: #f7f9fd; border: 1px solid #e8edf5; border-radius: 10px; }
QWidget#PlanRecordsPanel QLabel#PlanDetailMetaLabel { color: #71809e; font-size: 11px; }
QWidget#PlanRecordsPanel QLabel#PlanDetailMetaValue { color: #293a59; font-size: 12px; font-weight: 600; }
QWidget#PlanRecordsPanel QLabel#PlanDetailBodyHeading { color: #405575; font-size: 12px; font-weight: 700; padding: 2px 1px; }
QWidget#PlanRecordsPanel QTextBrowser#PlanDetailBody { background: #ffffff; border: 1px solid #e8edf5; border-radius: 9px; padding: 9px 11px; color: #405575; }
QPushButton#TodayLink { color: #3979ff; border: none; background: transparent; padding: 2px 0px; font-size: 11px; }
QPushButton#TodayLink:hover { color: #1e54c5; }
QListWidget#TodayList { border: none; border-radius: 0px; background: transparent; }
QListWidget#TodayList::item { padding: 0px; border: none; }
QPushButton#FocusButton { background: #f1f6ff; color: #54729b; padding: 4px 12px; border: 1px solid #e3ebf8; border-radius: 9px; font-size: 11px; }
QPushButton#TodayBackup { background: #f8faff; border: 1px solid #e7edf7; border-radius: 8px; padding: 9px 6px; font-size: 11px; min-height: 42px; text-align: left; }
QPushButton#TodayBackup:hover { border-color: #93b4f8; background: #eff5ff; }
QPushButton#TodayQuickAction { min-height: 46px; padding: 7px 9px; font-size: 11px; text-align: left; border-radius: 9px; }
QWidget#TodayPage QPushButton[quickColor="blue"] { background: #edf5ff; color: #3979ff; border-color: #edf5ff; padding: 7px 11px; }
QWidget#TodayPage QPushButton[quickColor="purple"] { background: #f3eeff; color: #8057df; border-color: #f3eeff; padding: 7px 11px; }
QWidget#TodayPage QPushButton[quickColor="green"] { background: #edf8f2; color: #25976a; border-color: #edf8f2; padding: 7px 11px; }
QWidget#TodayPage QPushButton#TodayQuickAction { min-height: 48px; text-align: left; padding: 7px 9px; }
QWidget#TodayPage QPushButton#TodayQuickAction:hover { border-color: #b9cdef; background: #f7faff; }
QWidget#ProjectPage QPushButton[primary="true"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4e8aff,stop:1 #2864ed); border-color: #3976f4; }
QWidget#ProjectPage QPushButton[primary="true"]:disabled, QWidget#TodayPage QPushButton[primary="true"]:disabled { background: #eff1f6; border-color: #e6e9f1; color: #a4acc0; }
QTableWidget#ProjectCards { border: none; background: transparent; }
QFrame#ProjectSidebar { background: #ffffff; border: 1px solid #e8edf5; border-radius: 14px; }
QLabel#ProjectListIcon { background: #eaf2ff; border-radius: 12px; }
QWidget#ProjectDetail { background: transparent; }
QFrame#ProjectHero { background: transparent; border: 1px solid #dce9fa; border-radius: 14px; }
QFrame#ProjectOverviewCard { background: #ffffff; border: 1px solid #e8edf5; border-radius: 12px; }
QWidget#ProjectOverview QFrame#ProjectInfoField { background: #f7f9fd; border: 1px solid #e8edf5; border-radius: 9px; }
QWidget#ProjectOverview QLabel#ProjectInfoLabel { color: #71809e; font-size: 10px; }
QWidget#ProjectOverview QLabel#ProjectInfoValue { color: #172b52; font-size: 12px; font-weight: 600; }
QWidget#ProjectOverview QLabel#ProjectTaskCardTitle[taskSource="ai"] { color: #6849a8; font-weight: 700; }
QWidget#ProjectOverview QLabel#ProjectTaskCardTitle[taskSource="manual"] { color: #277454; font-weight: 700; }
QWidget#ProjectOverview QLabel#ProjectTaskEmpty { color: #8592ac; font-size: 10px; }
QWidget#ProjectOverview QLabel#ProjectTaskText { color: #27334f; font-size: 12px; padding: 2px 1px; }
QWidget#ProjectOverview QLabel#ProjectTaskMore { color: #71809e; font-size: 10px; padding-left: 17px; }
QWidget#ProjectOverview QFrame#ProjectMaterialRow { background: #f7f9fd; border: 1px solid #e9eef6; border-radius: 9px; }
QWidget#ProjectOverview QFrame#ProjectMaterialRow:hover { background: #f0f6ff; border-color: #cfdef3; }
QWidget#ProjectOverview QLabel#ProjectMaterialType { background: #eaf2ff; color: #2874dc; border-radius: 7px; font-size: 9px; font-weight: 700; }
QWidget#ProjectOverview QLabel#ProjectMaterialType[fileKind="file"] { background: #f1f3f7; color: #6c7890; }
QWidget#ProjectOverview QPushButton#ProjectMaterialName { min-height: 18px; padding: 0; border: none; background: transparent; color: #263b5e; text-align: left; font-size: 11px; font-weight: 600; }
QWidget#ProjectOverview QPushButton#ProjectMaterialName:hover { color: #2874dc; }
QWidget#ProjectOverview QLabel#ProjectMaterialMeta, QWidget#ProjectOverview QLabel#ProjectMaterialCount { color: #8290a7; font-size: 9px; }
QWidget#ProjectOverview QLabel#ProjectMaterialChevron { color: #93a1b6; font-size: 18px; }
QWidget#ProjectOverview QLabel#ProjectOverviewValue { color: #172b52; font-size: 19px; font-weight: 700; }
QWidget#ProjectPage QToolButton#ProjectFilterChip { background: #f4f7fc; color: #6e7d96; border: 1px solid #edf1f7; border-radius: 9px; padding: 5px 7px; font-size: 10px; }
QWidget#ProjectPage QToolButton#ProjectFilterChip:checked { background: #eaf2ff; color: #2866d7; border-color: #a9c8ff; font-weight: 600; }
QWidget#ProjectPage QToolButton#ProjectFilterChip:hover { border-color: #a9c8ff; }
QTableWidget#ProjectCards::item { padding: 0px; background: transparent; }
QLabel#ProjectName { color: #142b52; font-size: 24px; font-weight: 700; }
QLabel#ProjectIcon { background: #3979ff; border-radius: 12px; }
QLabel#ProjectNext { color: #345a92; font-size: 13px; }
QLabel#ProjectDescriptionLabel { color: #345a92; font-size: 13px; }
QLabel#ProjectMetricValue { color: #17305c; font-size: 18px; font-weight: 700; }
QTextBrowser#ProjectMarkdownPreview { border: 1px solid #e7ecf5; border-radius: 10px; background: #ffffff; padding: 12px; color: #405575; }
QListWidget#ProjectNotesList { background: #ffffff; border: 1px solid #e7ecf5; border-radius: 10px; padding: 6px; }
QListWidget#ProjectNotesList::item { padding: 10px 11px; margin: 2px 0px; border: 1px solid transparent; border-radius: 7px; color: #405575; }
QListWidget#ProjectNotesList::item:selected { background: #edf4ff; border-color: #b8d0ff; color: #1f5fbf; }
QWidget#KnowledgePage { background: #f5f6fb; }
QWidget#KnowledgePage QSplitter::handle { background: #e9edf5; width: 5px; }
QWidget#KnowledgePage QWidget#KnowledgePanel { background: #ffffff; border: 1px solid #e7ecf5; border-radius: 13px; }
QWidget#KnowledgePage QListWidget, QWidget#KnowledgePage QTextBrowser { background: #ffffff; border: 1px solid #e7ecf5; border-radius: 10px; padding: 5px; }
QWidget#KnowledgePage QListWidget::item { padding: 8px; border-bottom: 1px solid #eef1f6; }
QWidget#KnowledgePage QListWidget::item:selected { background: #edf4ff; color: #1769e8; border-radius: 6px; }
QWidget#KnowledgePage QPushButton { min-height: 28px; padding: 5px 8px; }
QWidget#KnowledgePage QToolButton[primary="true"] { background: #2878f0; color: #ffffff; border: 1px solid #2878f0; border-radius: 8px; padding: 8px 14px; font-weight: 650; }
QWidget#KnowledgePage QToolButton[primary="true"]:hover { background: #1d68dc; }
QWidget#KnowledgePage QTabBar::tab { padding: 8px 12px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree { background: #ffffff; border: none; padding: 0px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item { min-height: 32px; padding: 3px 4px; border: none; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::item:selected { background: #edf4ff; color: #1769e8; border-radius: 0px; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch { background: transparent; border: none; }
QWidget#KnowledgePage QTreeWidget#KnowledgeTree::branch:selected { background: #edf4ff; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick { border: none; padding: 0px; }
QWidget#KnowledgePage QListWidget#KnowledgeQuick::item { border: none; padding: 4px 8px; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles { border: none; border-radius: 0px; selection-background-color: #edf4ff; selection-color: #173d76; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles::item { padding: 8px 6px; border-bottom: 1px solid #eef1f6; }
QWidget#KnowledgePage QTableWidget#KnowledgeFiles QHeaderView::section { background: #ffffff; color: #71809e; padding: 10px 6px; border: none; border-bottom: 1px solid #e7ecf5; }
QWidget#KnowledgePage QListWidget#KnowledgeCards { border: none; padding: 0px; background: transparent; }
QWidget#KnowledgePage QListWidget#KnowledgeCards::item { border: none; margin: 0px; padding: 0px; background: transparent; }
QWidget#KnowledgePage QListWidget#KnowledgeCards::item:selected { background: #edf4ff; border-color: #8eb6ff; }
QWidget#KnowledgePage QLabel#KnowledgeEmpty { color: #71809e; padding: 30px; }
QWidget#SafetyPage { background: #f5f6fb; }
QWidget#SafetyPage QLabel#Title { font-size: 25px; }
QWidget#SafetyPage QFrame#SafetyAttention { background: #fff5e7; border: 1px solid #f0dfc0; border-radius: 10px; }
QWidget#SafetyPage QLabel#SafetyAttentionIcon { background: #ffecd1; color: #a9660d; border-radius: 15px; font-size: 16px; font-weight: 700; }
QWidget#SafetyPage QLabel#SafetyEmpty { background: #ffffff; border: 1px dashed #d7e3f5; border-radius: 10px; padding: 18px; color: #607493; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable { border: none; border-top: 1px solid #e3e7ee; background: transparent; alternate-background-color: #f8faff; }
QWidget#SafetyPage QTableWidget#SafetyResourcesTable::item { padding: 7px 6px; }
QWidget#SafetyPage QListWidget#SafetyVersionList { background: #ffffff; border: 1px solid #e3e7ee; border-radius: 10px; padding: 5px; }
QWidget#SafetyPage QListWidget#SafetyVersionList::item { padding: 11px 10px; border-bottom: 1px solid #edf1f8; }
QWidget#SafetyPage QListWidget#SafetyVersionList::item:selected { background: #edf3ff; color: #2157c5; border-radius: 6px; }
QWidget#SafetyPage QPushButton#TextButton { background: transparent; border: none; color: #2157c5; }
QWidget#SafetyPage QFrame#SafetyControlCard { background: #ffffff; border: 1px solid #e6ecf5; border-radius: 12px; }
QWidget#SafetyPage QFrame#SafetyMetric { background: #ffffff; border: 1px solid #e6ecf5; border-radius: 12px; }
QWidget#SafetyPage QLabel#SafetyMetricValue { color: #17305c; font-size: 18px; font-weight: 700; }
QWidget#SafetyPage QLabel#SafetyMetricNote { color: #7685a1; font-size: 11px; }
QWidget#SafetyPage QLabel#SafetyStorageTotal { color: #172b52; font-size: 21px; font-weight: 700; }
QWidget#SafetyPage QLabel#SafetyHealthTitle { color: #27845d; font-size: 17px; font-weight: 700; }
QWidget#SafetyPage QListWidget#SafetyDashboardList { border: none; border-radius: 0px; background: transparent; }
QWidget#SafetyPage QListWidget#SafetyDashboardList::item { padding: 5px 3px; border-bottom: 1px solid #edf1f8; }
QWidget#SafetyPage QTableWidget#SafetyRecentTable { border: none; border-radius: 0px; background: transparent; alternate-background-color: #f8faff; }
QWidget#SafetyPage QTableWidget#SafetyRecentTable::item { padding: 7px 6px; }
QWidget#SafetyPage QTableWidget#SafetyRecentTable QHeaderView::section { background: #f7f9fc; padding: 8px 6px; }
QWidget#SafetyPage QLabel#SafetyEmpty { background: #edf4ff; border: 1px solid #dfeaff; border-radius: 10px; padding: 10px 13px; color: #607493; }
QWidget#SafetyPage QTabWidget#SafetyTabs::pane { background: transparent; border: none; }
QWidget#SafetyPage QTabWidget#SafetyTabs > QTabBar::tab { padding: 9px 14px; font-size: 13px; }
QWidget#SafetyPage QComboBox { min-height: 22px; }
QWidget#SafetyPage QPushButton[primary="true"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4e8aff,stop:1 #2864ed); border-color: #3976f4; }
QWidget#SafetyPage QPushButton[primary="true"]:hover { background: #3976f4; }
QWidget#ResourceManagementPage { background: transparent; }
QWidget#ResourceManagementPage QTabWidget::pane { background: #ffffff; border: 1px solid #e8edf5; border-radius: 12px; }
QWidget#ResourceManagementPage QTabBar::tab { padding: 9px 13px; }
QWidget#ResourceManagementPage QTextEdit { border: none; background: #ffffff; }
QWidget#PlanningDashboard QPushButton:checked { background: #3979ff; border-color: #3979ff; color: #ffffff; }
QWidget#PlanningDashboard QFrame#PlanHero { background: #ffffff; border: 1px solid #e6edf7; border-radius: 14px; }
QWidget#PlanningDashboard QFrame#PlanTitleIcon { background: #eaf3ff; border: 1px solid #dceaff; border-radius: 13px; }
QWidget#PlanningDashboard QLabel#PlanTitle { color: #17213b; font-size: 23px; font-weight: 750; }
QWidget#PlanningDashboard QLabel#PlanSubtitle { color: #71809e; font-size: 11px; }
QWidget#PlanningDashboard QFrame#PlanHero QPushButton[primary="true"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4e8aff,stop:1 #2864ed); border-color: #3976f4; color: #ffffff; font-weight: 650; }
QWidget#PlanningDashboard QFrame#PlanHero QPushButton[primary="true"]:hover { background: #3979f6; }
QWidget#PlanningDashboard QFrame#TodayMetric { min-height: 84px; }
QWidget#PlanningDashboard QFrame#TodayMetric[planMetric="daily"] { background: #f4f8ff; border: 1px solid #dce8fa; border-radius: 13px; }
QWidget#PlanningDashboard QFrame#TodayMetric[planMetric="projects"] { background: #f8f5ff; border: 1px solid #e9defa; border-radius: 13px; }
QWidget#PlanningDashboard QLabel#TodaySectionTitle { font-size: 14px; }
QWidget#PlanningDashboard QFrame#PlanDateBar { background: #f2f6ff; border: 1px solid #e1eaf8; border-radius: 10px; }
QWidget#PlanningDashboard QLabel#PlanDayLabel { color: #31517f; font-size: 12px; font-weight: 650; }
QWidget#PlanningDashboard QFrame#PlanDateBar QPushButton { min-height: 17px; padding: 6px 10px; }
QWidget#PlanningDashboard QFrame#PlanDateBar QDateEdit { min-height: 18px; padding-top: 6px; padding-bottom: 6px; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable { background: #ffffff; alternate-background-color: #f7faff; border: 1px solid #e3eaf4; border-radius: 11px; gridline-color: #edf1f7; selection-background-color: #eaf2ff; selection-color: #274b8e; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable::item { padding: 7px 9px; border-bottom: 1px solid #edf1f7; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable::item:hover { background: #f2f6ff; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QHeaderView::section { background: #f2f6ff; color: #617594; padding: 10px 8px; border-bottom: 1px solid #e3eaf4; font-weight: 650; }
QWidget#PlanningDashboard QLabel#PlanTypeTag { background: #edf5ff; color: #3979ff; border-radius: 7px; padding: 4px 6px; font-size: 11px; }
QWidget#PlanningDashboard QLabel#PlanTypeTag[kind="日程"] { background: #fff4e4; color: #c77719; }
QWidget#PlanningDashboard QLabel#PlanTypeTag[kind="周期任务"] { background: #f3eeff; color: #8057df; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag { background: #f1f4f9; color: #71809e; border-radius: 7px; padding: 3px 7px; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag[priority="高"] { background: #fff0f0; color: #d84b56; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag[priority="中"] { background: #fff5e7; color: #d38321; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag[priority="低"] { background: #eef3fa; color: #71809e; }
QWidget#PlanningDashboard QLabel#PlanPriorityTag[priority="none"] { background: transparent; color: #9aa7bb; padding: 0px; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QPushButton[danger="true"] { background: #fff1f0; border: 1px solid #f6d9d8; border-radius: 8px; color: #c34b55; padding: 6px 10px; }
QWidget#PlanningDashboard QTableWidget#PlanDayTable QPushButton[danger="true"]:hover { background: #ffe5e3; }
QWidget#PlanningDashboard QProgressBar { background: #eee9fb; border: none; border-radius: 4px; max-height: 7px; }
QWidget#PlanningDashboard QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #9a7af0,stop:1 #6d55cf); border-radius: 4px; }
QWidget#PlanningDashboard QWidget#PlanTimelineCanvas { background: #f7faff; border-radius: 10px; }
QWidget#PlanningDashboard QLabel#PlanTimelineDot { color: #3979ff; font-size: 11px; }
QWidget#PlanningDashboard QLabel#PlanSummaryItem { color: #405575; font-size: 11px; padding: 3px 1px; }
QWidget#PlanningDashboard QDateEdit { min-height: 24px; }
QWidget#PlanningDashboard QLabel#PlanTitleGlyph { color: #3979ff; font-size: 23px; font-weight: 700; }
QWidget#PlanningDashboard QFrame#PlanDayCell { background: #ffffff; border: 1px solid #e8edf5; border-radius: 9px; }
QWidget#PlanningDashboard QFrame#PlanDayCell[selected="true"] { background: #eef5ff; border: 1px solid #91baff; }
QWidget#PlanningDashboard QFrame#PlanDayCell[outsideMonth="true"] { background: #f8f9fc; }
QWidget#PlanningDashboard QPushButton#PlanDayHeading { border: none; background: transparent; color: #20385e; font-weight: 650; text-align: left; padding: 2px; }
QWidget#PlanningDashboard QLabel#PlanTaskPill { background: #edf5ff; border-left: 3px solid #3979ff; border-radius: 5px; padding: 4px; color: #344e72; font-size: 10px; }
QWidget#PlanningDashboard QLabel#PlanTaskPill[kind="周期任务"] { background: #f3eeff; border-left-color: #8760ed; }
QWidget#PlanningDashboard QLabel#PlanTaskPill[kind="日程"] { background: #fff4e4; border-left-color: #ed9a2c; }
QWidget#PlanningDashboard QLabel#PlanSummaryItem { color: #405575; font-size: 11px; padding: 3px 1px; }
QWidget#PlanningDashboard QLabel#PlanTimelineDot { color: #3979ff; font-size: 11px; }
QWidget#PlanningDashboard QProgressBar { background: #e9eef7; border: none; border-radius: 3px; }
QWidget#PlanningDashboard QProgressBar::chunk { background: #4c91ff; border-radius: 3px; }
QWidget#PlanningDashboard QDateEdit { min-height: 24px; }
"""
