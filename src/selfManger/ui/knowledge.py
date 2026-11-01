"""Resource-centered knowledge collection over a portable filesystem library."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import html

from PySide6.QtCore import Qt, QFile, QUrl, QSize, QFileInfo, QTimer
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QImageReader, QTextDocument
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
    QAbstractItemView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget, QFileDialog, QComboBox, QTabWidget, QToolButton,
    QMenu, QSizePolicy, QFileIconProvider, QTreeWidget, QTreeWidgetItem, QTableWidget, QFrame,
    QTableWidgetItem, QHeaderView, QStackedWidget, QScrollArea, QStyle)

from ..knowledge import KnowledgeIndex, knowledge_root
from ..knowledge_collection import collect_knowledge_resource
from ..naming import readable_name
from ..knowledge_resources import topic_name, resource_type, note_header
from ..workbench import work_root
from ..obsidian import open_uri, require_vault
from ..domain import UserError
from .components import FlowLayout, nav_icon
from .knowledge_cards import ResourceCardDelegate, TopicCountDelegate, LibraryIllustration
from .presentation import markdown_preserving_linebreaks
from .knowledge_collection_dialog import KnowledgeCollectionDialog, QUICK_CAPTURE_FOLDER


class KnowledgeFileList(QTableWidget):
    def __init__(self, page):
        super().__init__(0, 4)
        self.page = page
        self.setObjectName("KnowledgeFiles")
        self.setHorizontalHeaderLabels(["资源", "类型", "标签", "用途"])
        # Name column carries an icon, so its title sits left like the rows do;
        # the short value columns keep the centred default and centre their text.
        self.horizontalHeaderItem(0).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(48)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 88), (2, 100), (3, 180)):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        self.page.drop_files(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setColumnHidden(3, self.viewport().width() < 410)


class KnowledgeCards(QListWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setAcceptDrops(True)
        self.setItemDelegate(ResourceCardDelegate(self))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Reserve scrollbar space against the stable outer width to avoid
        # column-count oscillation when the vertical scrollbar toggles.
        width = max(180, self.width() - self.frameWidth() * 2 - 28)
        columns = max(1, width // 220)
        cell_width = width // columns
        card_size = QSize(cell_width, 112)
        if self.gridSize() == card_size:
            return
        self.setGridSize(card_size)
        for row in range(self.count()):
            if self.item(row).sizeHint() != card_size:
                self.item(row).setSizeHint(card_size)

    dragEnterEvent = KnowledgeFileList.dragEnterEvent
    dragMoveEvent = KnowledgeFileList.dragMoveEvent
    dropEvent = KnowledgeFileList.dropEvent


def _button(text, callback, primary=False):
    button = QPushButton(text)
    button.setProperty("primary", primary)
    button.clicked.connect(callback)
    return button


class KnowledgePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("KnowledgePage")
        self.index = None
        self.items = []
        self.folders = [""]
        self.current_folder = ""
        self.scope_project = ""
        self.scope_agent = ""
        self.loading = False
        self.loaded_once = False
        self.pending_task_id = ""
        self.refresh_pending = False
        self.quick_filter = "all"
        self.grid_view = False
        self.editing_note = None
        self.tag_filter = ""
        self.directory_mode = False
        self.preferred_identity = ""
        self.tree_items = {}
        self.shown = []
        self.scan_errors = []
        self.page_index, self.page_size = 0, 200
        self.last_filter = None
        self.file_icon_provider = QFileIconProvider()
        self.current_detail = None
        self.detail_requested = False
        self.details_dismissed = False
        self._build_page_frame()
        self._build_heading()
        self.columns = QSplitter(Qt.Orientation.Horizontal)
        self.columns.setObjectName("KnowledgeWorkspace")
        self.columns.setChildrenCollapsible(False)
        self._build_sidebar()
        self._build_listing()
        self._build_detail_panel()
        self._finish_workspace()
        self.window.task_completed.connect(self.task_completed)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.search_shortcut.activated.connect(self.search.setFocus)
        configured_root = knowledge_root(self.window.store)
        self.root_label.setText(configured_root.name + " · 当前资料库" if configured_root else "请先在设置中选择资料库")
        self.root_label.setToolTip(str(configured_root) if configured_root else "请在设置中选择 Obsidian Vault")
        self.render_items()


    def _build_page_frame(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("KnowledgeBody")
        body.setMinimumHeight(0)
        outer.addWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 18, 20, 18)
        self.root_layout = root
        root.setSpacing(14)

    def _build_heading(self):
        heading = QHBoxLayout()
        heading.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(nav_icon(6, "#2878f0").pixmap(36, 36))
        icon.setObjectName("KnowledgeIcon")
        icon.setFixedWidth(42)
        icon.setStyleSheet("color: #2878f0; font-size: 32px; font-weight: 700;")
        heading.addWidget(icon)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("知识库")
        title.setObjectName("Title")
        subtitle = QLabel("整理资料、沉淀笔记、连接项目。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box, 1)
        tools_row = QHBoxLayout()
        tools_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索知识、链接、标签与笔记…")
        self.search.setToolTip("Ctrl+K 聚焦搜索；同时检索名称、路径、标签和文本内容")
        self.search.setMinimumWidth(180)
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(160)
        self.search_timer.timeout.connect(self.render_items)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.create_button = _button("＋ 添加资料", self.add_resource, True)
        self.import_button = self.create_button
        self.new_note_button = _button("新建笔记", self.new_note)
        self.new_note_button.hide()
        tools_row.addWidget(self.create_button)
        self.manage_button = QToolButton()
        self.manage_button.setText("管理")
        self.manage_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(self.manage_button)
        self.manage_button.setMenu(add_menu)
        tools_row.addWidget(self.manage_button)
        self.index_refresh_button = _button("", self.refresh)
        self.index_refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.index_refresh_button.setFixedWidth(38)
        self.index_refresh_button.setToolTip("刷新索引")
        self.index_refresh_button.setAccessibleName("刷新索引")
        self.refresh_button = self.index_refresh_button
        add_menu.addAction("刷新索引", self.refresh)
        add_menu.addAction("迁入旧工作台资源…", self.import_legacy_catalog)
        self.index_refresh_button.hide()
        self.open_root_button = _button("", self.reveal_root)
        self.open_root_button.setIcon(nav_icon(3, "#7890b1"))
        self.open_root_button.setFixedWidth(38)
        self.open_root_button.setAccessibleName("打开根目录")
        self.open_root_button.setToolTip("在资源管理器中打开知识库根目录")
        add_menu.addAction("打开当前专题目录", self.reveal_current_folder)
        add_menu.addAction("打开知识库目录", self.reveal_root)
        self.open_root_button.hide()
        self.root_layout.addLayout(heading)
        heading.addLayout(tools_row)


    def _build_sidebar(self):
        folder_panel = QWidget()
        self.folder_panel = folder_panel
        folder_panel.setProperty("knowledgeRole", "sidebar")
        folder_layout = QVBoxLayout(folder_panel)
        folder_panel.setObjectName("KnowledgePanel")
        folder_layout.setContentsMargins(0, 12, 10, 10)
        folder_layout.setSpacing(7)
        dir_heading = QHBoxLayout()
        directory_title = QLabel("知识空间")
        directory_title.setObjectName("SectionTitle")
        dir_heading.addWidget(directory_title, 1)
        root_button = _button("", self.reveal_root)
        root_button.setIcon(nav_icon(3, "#7890b1"))
        root_button.setFixedWidth(32)
        root_button.setObjectName("KnowledgeRootButton")
        root_button.setToolTip("打开知识库根目录")
        dir_heading.addWidget(root_button)
        folder_layout.addLayout(dir_heading)
        self.root_label = QLabel("尚未选择 Vault")
        self.root_label.setWordWrap(True)
        self.root_label.setObjectName("KnowledgeVaultName")
        folder_layout.addWidget(self.root_label)
        self.new_category_button = _button("＋ 新建专题", self.new_folder)
        self.new_category_button.setObjectName("KnowledgeNewTopic")
        folder_layout.addWidget(self.new_category_button)
        quick_heading = QLabel("快捷浏览")
        quick_heading.setObjectName("KnowledgeNavigationHeading")
        folder_layout.addWidget(quick_heading)
        self.quick_list = QListWidget()
        self.quick_list.setObjectName("KnowledgeQuick")
        self.quick_list.setFixedHeight(4 * 32 + 4)
        self.quick_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quick_list.setIconSize(QSize(17, 17))
        self.quick_list.setSpacing(0)
        self.quick_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quick_list.currentItemChanged.connect(self.select_quick_filter)
        for label, key in (("收集箱", "inbox"), ("最近使用", "recent"),
                           ("我的收藏", "favorite"), ("回收站", "deleted")):
            row = QListWidgetItem(label)
            row.setSizeHint(QSize(0, 32))
            row.setIcon(nav_icon({"inbox": 5, "recent": 13, "favorite": 16, "deleted": 18}[key], "#7890b1"))
            row.setData(Qt.ItemDataRole.UserRole, key)
            if key == "recent":
                row.setToolTip("最近通过知识库打开或编辑的资料")
            elif key == "deleted":
                row.setToolTip("移入回收站的资料会显示在这里，原始文件仍然保留")
            self.quick_list.addItem(row)
        self.quick_list.setCurrentRow(-1)
        self.folder_tree_title = QLabel("主题目录")
        self.folder_tree_title.setObjectName("KnowledgeNavigationHeading")
        folder_layout.addWidget(self.quick_list)
        folder_layout.addWidget(self.folder_tree_title)
        self.folder_list = QTreeWidget()
        self.folder_list.setObjectName("KnowledgeTree")
        self.folder_list.setColumnCount(2)
        self.folder_list.setHeaderHidden(True)
        self.folder_list.setIndentation(12)
        self.folder_list.setUniformRowHeights(True)
        self.folder_list.setAllColumnsShowFocus(True)
        self.folder_list.setMinimumHeight(90)
        self.folder_list.setItemDelegate(TopicCountDelegate(self.folder_list))
        self.folder_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.folder_list.header().setStretchLastSection(False)
        self.folder_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.folder_list.header().setMinimumSectionSize(28)
        self.folder_list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.folder_list.setColumnWidth(1, 34)
        self.folder_list.currentItemChanged.connect(self.select_folder)
        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_list.customContextMenuRequested.connect(self.folder_menu)
        folder_layout.addWidget(self.folder_list, 1)
        # Legacy collection metadata remains readable for compatibility; it is not exposed in the UI.
        self.columns.addWidget(folder_panel)


    def _build_listing(self):
        list_panel = QWidget()
        self.list_panel = list_panel
        list_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        list_panel.setMinimumHeight(190)
        list_panel.setObjectName("KnowledgePanel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 10, 12, 10)
        list_layout.setSpacing(6)
        list_layout.addWidget(self.search)
        list_heading = QHBoxLayout()
        self.parent_button = _button("↑", self.parent_folder)
        self.parent_button.setFixedWidth(34)
        self.parent_button.setToolTip("上一级专题")
        list_heading.addWidget(self.parent_button)
        self.breadcrumb = QLabel("全部资源")
        self.breadcrumb.setObjectName("SectionTitle")
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        list_heading.addWidget(self.breadcrumb, 1)
        self.inbox_summary = QLabel()
        self.inbox_summary.setObjectName("Subtitle")
        self.inbox_summary.hide()
        list_heading.addWidget(self.inbox_summary)
        self.inbox_sort_button = _button("归类所选…", self.move_selected, True)
        self.inbox_sort_button.setToolTip("把收件箱中的资料移动到知识主题或文件夹")
        self.inbox_sort_button.hide()
        list_heading.addWidget(self.inbox_sort_button)
        self.type_filter = QComboBox()
        for label, key in (("全部类型", "all"), ("文档", "documents"), ("代码", "code"),
                           ("链接", "links"), ("Markdown 笔记", "notes"), ("其他文件", "other")):
            self.type_filter.addItem(label, key)
        self.type_filter.currentIndexChanged.connect(self.render_items)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.type_filter)
        self.type_filter.addItem("图片", "images")
        self.type_filter.addItem("外部引用", "external")
        self.type_filter.addItem("资料合集", "bundles")
        self.type_filter.addItem("Skill", "skills")
        self.sort_choice = QComboBox()
        self.sort_choice.addItem("按修改时间", "modified")
        self.sort_choice.addItem("按名称", "name")
        self.sort_choice.addItem("按大小", "size")
        self.sort_choice.currentIndexChanged.connect(self.render_items)
        filter_row.addWidget(self.sort_choice)
        filter_row.addStretch(1)
        self.clear_filter_button = _button("清除筛选", self.clear_filters)
        self.clear_filter_button.setText("×")
        self.clear_filter_button.setFixedWidth(34)
        self.clear_filter_button.setToolTip("清除搜索、类型及项目 / Agent 筛选")
        self.type_filter.setMinimumWidth(70)
        self.sort_choice.setMinimumWidth(90)
        self.type_filter.setMaximumWidth(160)
        self.sort_choice.setMaximumWidth(150)
        self.type_filter.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.sort_choice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.tag_button = _button("标签", self.choose_tag)
        self.tag_button.setToolTip("按标签筛选资源")
        self.tag_button.setFixedWidth(48)
        filter_row.addWidget(self.tag_button)
        filter_row.addWidget(self.clear_filter_button)
        self.purge_button = _button("清空回收站", self.purge_deleted)
        self.purge_button.setToolTip("把回收站里的资料移入系统回收站，并从知识库索引中移除")
        self.purge_button.hide()
        list_heading.addWidget(self.purge_button)
        self.view_button = _button("列表视图", self.toggle_view)
        list_heading.addWidget(self.view_button)
        self.detail_button = _button("预览", self.toggle_details)
        self.detail_button.setToolTip("查看所选资料的内容与信息")
        list_heading.addWidget(self.detail_button)
        list_layout.addLayout(list_heading)
        # Keep the physical destination visible while browsing a topic.
        self.location_bar = QFrame()
        self.location_bar.setObjectName("KnowledgeLocation")
        location_layout = QHBoxLayout(self.location_bar)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.setSpacing(8)
        self.location_icon = QLabel()
        self.location_icon.setPixmap(nav_icon(3, "#2563eb").pixmap(18, 18))
        self.location_icon.setObjectName("KnowledgeLocationIcon")
        location_layout.addWidget(self.location_icon)
        location_copy = QVBoxLayout()
        location_copy.setContentsMargins(0, 0, 0, 0)
        location_copy.setSpacing(1)
        location_caption = QLabel("资源保存位置")
        location_caption.setObjectName("KnowledgeLocationCaption")
        self.location_path = QLabel("02-知识")
        self.location_path.setObjectName("KnowledgeLocationPath")
        self.location_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.location_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        location_caption.hide()
        location_copy.addWidget(self.location_path)
        location_layout.addLayout(location_copy, 1)
        self.location_open_button = _button("打开文件夹", self.reveal_current_folder)
        self.location_open_button.setObjectName("KnowledgeLocationOpen")
        location_layout.addWidget(self.location_open_button)
        self.location_bar.hide()
        self.filter_panel = QWidget()
        self.filter_panel.setLayout(filter_row)
        self.filter_panel.hide()
        self.filter_toggle = QToolButton()
        self.filter_toggle.setText("筛选")
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.toggled.connect(self.filter_panel.setVisible)
        list_heading.insertWidget(1, self.filter_toggle)
        list_layout.addWidget(self.filter_panel)
        self.filter_hint = QLabel("")
        self.filter_hint.setObjectName("Subtitle")
        list_layout.addWidget(self.filter_hint)
        self.file_list = KnowledgeFileList(self)
        self.file_list.setMinimumHeight(100)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.itemSelectionChanged.connect(self.select_file)
        self.file_list.cellDoubleClicked.connect(lambda *_: self.open_file())
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(lambda pos: self.resource_context_menu(self.file_list, pos))
        self.cards = KnowledgeCards(self)
        self.cards.setObjectName("KnowledgeCards")
        self.cards.setViewMode(QListWidget.ViewMode.IconMode)
        self.cards.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.cards.setMovement(QListWidget.Movement.Static)
        self.cards.setGridSize(QSize(240, 112))
        self.cards.setIconSize(QSize(36, 36))
        self.cards.setWordWrap(True)
        self.cards.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.cards.currentItemChanged.connect(self.select_file)
        self.cards.itemSelectionChanged.connect(self.select_file)
        self.cards.itemDoubleClicked.connect(lambda *_: self.open_file())
        self.cards.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cards.customContextMenuRequested.connect(lambda pos: self.resource_context_menu(self.cards, pos))
        self.file_views = QStackedWidget()
        self.file_views.addWidget(self.file_list)
        self.file_views.addWidget(self.cards)
        self.file_views.setCurrentIndex(0)
        self.view_button.setText("卡片")
        list_layout.addWidget(self.file_views, 1)
        self.empty_panel = QFrame()
        self.empty_panel.setObjectName("KnowledgeEmptyCard")
        self.empty_panel.setMaximumWidth(680)
        empty_layout = QVBoxLayout(self.empty_panel)
        self.empty_layout = empty_layout
        empty_layout.setContentsMargins(22, 18, 22, 18)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)
        self.empty_art = LibraryIllustration()
        empty_layout.addWidget(self.empty_art, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_icon = QLabel("知")
        self.empty_icon.setObjectName("KnowledgeEmptyIcon")
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon.setFixedSize(46, 46)
        empty_layout.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("KnowledgeEmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_description = QLabel()
        self.empty_description.setObjectName("KnowledgeEmptyDescription")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)
        empty_layout.addWidget(self.empty_description)
        self.empty_action = _button("新建专题", self.handle_empty_action, True)
        self.empty_action.setObjectName("KnowledgeEmptyAction")
        empty_actions = QHBoxLayout()
        empty_actions.addStretch(1)
        empty_actions.addWidget(self.empty_action)
        self.empty_secondary = QPushButton("查看收件箱")
        self.empty_secondary.clicked.connect(self.empty_secondary_action)
        empty_actions.addWidget(self.empty_secondary)
        empty_actions.addStretch(1)
        empty_layout.addLayout(empty_actions)
        self.empty_steps = QLabel("01  随手收集     ·     02  归入专题     ·     03  随时查阅")
        self.empty_steps.setObjectName("KnowledgeWelcomeSteps")
        self.empty_steps.setWordWrap(True)
        self.empty_steps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_steps)
        empty_layout.addStretch(1)
        self.empty_panel.hide()
        list_layout.addWidget(self.empty_panel, 1, Qt.AlignmentFlag.AlignHCenter)
        self.paging_bar = QWidget()
        list_footer = QHBoxLayout(self.paging_bar)
        list_footer.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setObjectName("Subtitle")
        self.status.setWordWrap(True)
        list_layout.addWidget(self.status)
        self.previous_button = _button("‹", lambda: self.change_page(-1))
        self.next_button = _button("›", lambda: self.change_page(1))
        for button, tooltip in ((self.previous_button, "上一页"), (self.next_button, "下一页")):
            button.setFixedWidth(30)
            button.setToolTip(tooltip)
        self.page_label = QLabel("1 / 1")
        list_footer.addWidget(self.previous_button)
        list_footer.addWidget(self.page_label)
        list_footer.addWidget(self.next_button)
        list_footer.addStretch(1)
        list_layout.addWidget(self.paging_bar)
        self.reading_split = QSplitter(Qt.Orientation.Vertical)
        self.reading_split.setObjectName("KnowledgeReadingWorkspace")
        self.reading_split.setChildrenCollapsible(False)
        self.reading_split.addWidget(list_panel)
        self.columns.addWidget(self.reading_split)


    def _build_detail_panel(self):
        detail_panel = QWidget()
        self.detail_panel = detail_panel
        detail_panel.setMinimumHeight(250)
        detail_panel.setObjectName("KnowledgePanel")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(18, 14, 18, 14)
        detail_layout.setSpacing(7)
        self.detail_title = QLabel("选择一项资料")
        self.detail_title.setObjectName("KnowledgeDetailTitle")
        self.detail_title.setWordWrap(True)
        self.detail_close = _button("← 返回列表", self.close_details)
        self.detail_close.setObjectName("KnowledgeDetailBack")
        self.detail_facts = QLabel("")
        self.detail_facts.setObjectName("Subtitle")
        self.detail_facts.setWordWrap(True)
        detail_head = QHBoxLayout()
        self.detail_facts.hide()
        detail_head.addWidget(self.detail_title, 1)
        self.favorite_button = _button("☆", self.toggle_favorite)
        self.favorite_button.setToolTip("收藏/取消收藏")
        detail_head.addWidget(self.favorite_button)
        detail_head.addWidget(self.detail_close)
        detail_layout.addLayout(detail_head)
        self.detail_context = QLabel()
        self.detail_context.setWordWrap(True)
        self.detail_context.setTextFormat(Qt.TextFormat.RichText)
        self.detail_context.setObjectName("Subtitle")
        detail_layout.addWidget(self.detail_context)
        self.related_summary = QLabel()
        self.related_summary.setWordWrap(True)
        self.related_summary.setTextFormat(Qt.TextFormat.RichText)
        self.related_summary.linkActivated.connect(self.show_related)
        detail_layout.addWidget(self.related_summary)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.detail_tabs.setMinimumHeight(100)
        self.detail_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.detail_tabs.tabBar().setUsesScrollButtons(False)
        self.detail_tabs.tabBar().setExpanding(True)
        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        preview_layout.setContentsMargins(0, 7, 0, 0)
        self.reading_label = QLabel()
        self.reading_label.setWordWrap(True)
        self.reading_label.setObjectName("Subtitle")
        self.reading_label.hide()
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.anchorClicked.connect(self.open_preview_link)
        self.note_editor = QTextEdit()
        self.note_editor.setAcceptRichText(False)
        self.note_editor.setPlaceholderText("在这里编辑 Markdown；Ctrl+S 保存")
        self.reading_stack = QStackedWidget()
        self.reading_stack.addWidget(self.preview)
        self.reading_stack.addWidget(self.note_editor)
        preview_layout.addWidget(self.reading_stack, 1)
        self.note_save_bar = QWidget()
        save_row = QHBoxLayout(self.note_save_bar)
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addStretch(1)
        save_row.addWidget(_button("取消编辑", self.cancel_note_edit))
        save_row.addWidget(_button("保存笔记", self.save_note_edit, True))
        preview_layout.addWidget(self.note_save_bar)
        self.note_save_bar.hide()
        self.note_save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self.note_editor)
        self.note_save_shortcut.activated.connect(self.save_note_edit)
        self.detail_tabs.addTab(preview_tab, "内容")
        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        self.info_text = QTextBrowser()
        self.info_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        info_layout.addWidget(self.info_text, 1)
        self.detail_tabs.addTab(info_tab, "资料信息")
        tags_tab = QWidget()
        tags_layout = QVBoxLayout(tags_tab)
        self.tags_tab_summary = QLabel("暂无标签")
        self.tags_tab_summary.setWordWrap(True)
        tags_layout.addWidget(self.tags_tab_summary, 1)
        tags_layout.addWidget(_button("编辑标签", self.edit_metadata))
        self.detail_tabs.addTab(tags_tab, "标签 (0)")
        relations_tab = QWidget()
        relations_layout = QVBoxLayout(relations_tab)
        self.relations_tab_summary = QLabel("暂无关联")
        self.relations_tab_summary.setWordWrap(True)
        relations_layout.addWidget(self.relations_tab_summary, 1)
        relations_layout.addWidget(_button("编辑关联", self.edit_metadata))
        self.detail_tabs.addTab(relations_tab, "关联 (0)")
        contents_tab = QWidget()
        contents_layout = QVBoxLayout(contents_tab)
        self.member_list = QListWidget()
        self.member_list.itemDoubleClicked.connect(self.read_member)
        self.member_list.currentItemChanged.connect(self.update_member_actions)
        contents_layout.addWidget(self.member_list, 1)
        member_actions = QHBoxLayout()
        member_actions.addWidget(_button("阅读", self.read_member))
        member_actions.addWidget(_button("打开原件", self.open_member))
        member_actions.addWidget(_button("设为首页", self.set_bundle_entry))
        contents_layout.addLayout(member_actions)
        contents_layout.addWidget(_button("添加已有资料…", self.add_bundle_members))
        contents_layout.addWidget(_button("移出资源 · 保留原文件", self.remove_bundle_member))
        contents_layout.addWidget(_button("取消资源组合", self.ungroup_selected))
        self.detail_tabs.addTab(contents_tab, "包含内容")
        detail_layout.addWidget(self.detail_tabs, 1)
        actions = FlowLayout()
        self.open_button = _button("打开原文件", self.open_file)
        self.note_button = _button("编辑笔记", self.edit_note, True)
        self.more_button = QToolButton()
        self.more_button.setText("更多操作")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.resource_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.resource_menu)
        actions.addWidget(self.note_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.more_button)
        detail_layout.addLayout(actions)
        self.secondary_actions = QWidget(self)
        self.secondary_actions.hide()
        self.reveal_button = _button("打开文件夹", self.reveal_file)
        self.metadata_button = _button("编辑资料信息", self.edit_metadata)
        self.delete_button = _button("删除", self.toggle_deleted)
        for button in (self.reveal_button, self.metadata_button, self.delete_button):
            button.setParent(self.secondary_actions)
        def menu_actions():
            self.resource_menu.clear()
            for button in (self.metadata_button, self.reveal_button, self.delete_button):
                action = self.resource_menu.addAction(button.text(), button.click)
                action.setEnabled(button.isEnabled() and not self.editing_note)
        self.resource_menu.aboutToShow.connect(menu_actions)
        self.detail_tabs.setTabVisible(2, False)
        self.detail_tabs.setTabVisible(3, False)

    def _finish_workspace(self):
        self.reading_split.addWidget(self.detail_panel)
        self.reading_split.setSizes([190, 420])
        for panel, minimum_width in ((self.folder_panel, 170), (self.list_panel, 300), (self.detail_panel, 280)):
            panel.setMinimumWidth(minimum_width)
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.columns.setStretchFactor(0, 0)
        self.columns.setStretchFactor(1, 1)
        self.columns.setSizes([190, 980])
        self.root_layout.addWidget(self.columns, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "detail_panel"):
            self.update_workspace()

    def update_workspace(self):
        orientation = Qt.Orientation.Horizontal if self.width() >= 1100 else Qt.Orientation.Vertical
        self.list_panel.setMinimumHeight(190 if orientation == Qt.Orientation.Horizontal else 250)
        if self.reading_split.orientation() != orientation:
            self.reading_split.setOrientation(orientation)
            self.reading_split.setSizes([380, 580] if orientation == Qt.Orientation.Horizontal else [250, 400])
        self.empty_layout.setStretch(0, 0 if self.height() < 740 else 1)
        selected = bool(self.current_detail and self.current_detail["kind"] != "folder")
        preview = selected and (bool(self.editing_note) or not self.details_dismissed)
        self.detail_panel.setVisible(preview)
        self.list_panel.setVisible(not bool(self.editing_note))
        self.detail_close.setText("收起阅读区")
        self.detail_close.setVisible(not bool(self.editing_note))
        self.detail_button.setEnabled(selected and not bool(self.editing_note))
        self.detail_button.setText("收起阅读" if preview else "打开阅读")

    def toggle_details(self):
        if self.detail_panel.isVisible():
            self.close_details()
        elif self.current_detail:
            self.detail_requested = True
            self.details_dismissed = False
            self.update_workspace()

    def close_details(self):
        self.detail_requested = False
        self.details_dismissed = True
        self.update_workspace()

    def ensure_index(self):
        root = knowledge_root(self.window.store)
        if root is None:
            QMessageBox.information(self, "知识库目录", "请先在设置中选择知识库根目录。")
            return False
        try:
            if self.index is None or self.index.root != root.absolute():
                self.index = KnowledgeIndex(self.window.store, root)
            return True
        except (OSError, UserError) as exc:
            QMessageBox.information(self, "知识库不可用", str(exc))
            return False

    def parent_folder(self):
        if self.directory_mode and self.current_folder:
            parent = Path(self.current_folder).parent.as_posix()
            self.folder_list.setCurrentItem(self.tree_items.get("" if parent == "." else parent))

    def clear_filters(self):
        self.tag_filter = ""
        self.set_scope()

    def choose_tag(self):
        tags = sorted({tag for entry in self.items for tag in entry["tags"]})
        choices = ["全部标签", *tags]
        selected, ok = QInputDialog.getItem(self, "标签筛选", "选择标签", choices, 0, False)
        if ok:
            self.tag_filter = "" if selected == "全部标签" else selected
            self.render_items()

    def add_to_current_topic(self):
        folder = self.current_folder if self.directory_mode else ""
        if not folder.startswith("02-知识/") or folder == QUICK_CAPTURE_FOLDER or folder.startswith(QUICK_CAPTURE_FOLDER + "/"):
            QMessageBox.information(self, "添加到主题", "请先在左侧选择一个知识主题。")
            return
        self.add_resource()

    def open_inbox(self):
        for row in range(self.quick_list.count()):
            if self.quick_list.item(row).data(Qt.ItemDataRole.UserRole) == "inbox":
                self.quick_list.setCurrentRow(row)
                return

    def handle_empty_action(self):
        if knowledge_root(self.window.store) is None:
            self.window.navigation.setCurrentRow(self.window.SETTINGS)
            return
        if (self.search.text().strip() or self.type_filter.currentData() != "all" or self.tag_filter
              or self.scope_project or self.scope_agent or self.quick_filter not in {"all", "inbox"}):
            self.clear_filters()
        elif self.directory_mode and self.current_folder.startswith("02-知识/") and self.current_folder != QUICK_CAPTURE_FOLDER:
            self.add_to_current_topic()
        else:
            self.add_resource()

    def empty_secondary_action(self):
        if self.directory_mode:
            self.open_inbox()
        elif self.quick_filter == "inbox":
            self.clear_filters()
        else:
            self.new_folder()

    def add_resource(self):
        self.collect_resource("file", default_folder=self.capture_folder())

    def import_legacy_catalog(self):
        source_root = work_root(self.window.store)
        resource_root = source_root / "资源"
        if not resource_root.is_dir():
            QMessageBox.information(self, "没有旧资源库", "当前工作台中没有“资源”目录；无需迁入。")
            return
        if not self.ensure_index():
            return
        if QMessageBox.question(
                self, "迁入旧工作台资源",
                "将把“资源”目录中的 Markdown 登记为知识库外部引用，并把标签、项目关联等可变元数据导入 SQLite。\n"
                "原文件不会移动、修改或删除；以后可从知识库中打开。附件仍保留在原目录。\n\n继续吗？\n" + str(resource_root),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        index = self.index

        def completed(report):
            message = f"迁入 {report['imported']} 篇；已存在 {report['already_registered']} 篇。"
            if report["errors"]:
                message += "\n\n以下文件未迁入，可修复后重试：\n" + "\n".join(report["errors"][:20])
                QMessageBox.warning(self, "旧资源迁入完成但有文件待处理", message)
            else:
                QMessageBox.information(self, "旧资源迁入完成", message)
            self.refresh()

        self.window.submit(None, "迁入旧工作台资源",
                           lambda context: index.import_legacy_catalog(source_root, context),
                           completed, persist_result=False)

    def update_topic_actions(self):
        folder = self.capture_folder()
        self.create_button.setToolTip(f"添加文件、链接、笔记或目录\n默认保存位置：{folder}，可在弹窗中更改")
        self.new_note_button.setToolTip(f"新建笔记到 {folder}")

    def active_storage_folder(self):
        if self.directory_mode:
            return self.current_folder or "02-知识"
        if self.quick_filter == "inbox":
            return QUICK_CAPTURE_FOLDER
        return "02-知识"

    def update_location(self):
        relative = self.active_storage_folder()
        root = self.index.root if self.index else knowledge_root(self.window.store)
        path = root / relative if root else Path(relative)
        self.location_path.setText(f"{root.name} / {relative}" if root else relative)
        self.location_path.setToolTip(str(path))
        self.breadcrumb.setToolTip(str(path))

    def reveal_current_folder(self):
        if not self.ensure_index():
            return
        path = self.index.root / self.active_storage_folder()
        if not path.is_dir():
            QMessageBox.information(self, "文件夹尚未创建", f"该位置会在首次收集资料时创建：\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def collect_resource(self, kind="link", prefill="", sources=None, project_id="", default_folder=None,
                         quick_capture=False):
        if not self.ensure_index():
            return
        dialog = KnowledgeCollectionDialog(
            self, folders=self.folders, capture_folder=self.capture_folder(), kind=kind,
            prefill=prefill, sources=sources, project_id=project_id,
            scope_project=self.scope_project, scope_agent=self.scope_agent,
            default_folder=default_folder,
        )
        pending = {"id": ""}

        def finished(task_id, _state):
            if pending["id"] == task_id:
                pending["id"] = ""
                dialog.set_busy(False)

        self.window.task_completed.connect(finished)

        def save():
            request = dialog.request()
            if request is None:
                return
            index = self.index

            def operation(context):
                return collect_knowledge_resource(index, request, context=context)

            def completed(report):
                if report["added"]:
                    dialog.accept()
                    self.preferred_identity = report["preferred"]
                    if request.folder == QUICK_CAPTURE_FOLDER:
                        self.current_folder, self.directory_mode, self.quick_filter = "", False, "inbox"
                    else:
                        self.current_folder = report.get("folder", request.folder)
                        self.directory_mode = self.current_folder != "02-知识"
                    if request.folder != QUICK_CAPTURE_FOLDER:
                        self.quick_filter = "all"
                    self.search.clear()
                    self.type_filter.setCurrentIndex(0)
                    self.tag_filter = ""
                    self.refresh()
                    self.window.statusBar().showMessage(f"已添加 {report['added']} 条资料 · {request.folder}", 6000)
                    if project_id:
                        self.window.show_resource_hub()
                if report["errors"]:
                    QMessageBox.information(self, "收集结果", f"已添加 {report['added']} 条资源。\n" + "\n".join(report["errors"][:20]))

            dialog.set_busy(True)
            pending["id"] = self.window.submit(None, "收集知识资源", operation, completed, persist_result=False)

        dialog.buttons.accepted.connect(save)
        try:
            dialog.exec()
        finally:
            self.window.task_completed.disconnect(finished)
    def change_page(self, delta):
        self.page_index = max(0, self.page_index + delta)
        self.preferred_identity = ""
        self.render_items()

    def folder_menu(self, position):
        item = self.folder_list.itemAt(position)
        if not item:
            return
        self.folder_list.setCurrentItem(item)
        menu = QMenu(self)
        relative = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        menu.addAction("新建子分类…", self.new_folder)
        if relative:
            menu.addAction("重命名…", self.rename_folder)
            menu.addAction("删除空分类…", self.delete_folder)
            menu.addSeparator()
            menu.addAction("上移", lambda: self.move_folder_order(-1))
            menu.addAction("下移", lambda: self.move_folder_order(1))
        menu.exec(self.folder_list.viewport().mapToGlobal(position))

    def open_preview_link(self, url):
        if url.scheme() in {"http", "https"} and url.host() and not url.userName() and not url.password():
            QDesktopServices.openUrl(url)

    def show_related(self, relative):
        if any(entry["relative"] == relative for entry in self.items):
            self.clear_filters()
            self.preferred_identity = relative
            self.render_items()

    def group_selected(self):
        selected = self.selected_entries()
        if not selected:
            QMessageBox.information(self, "整理到文件夹", "先选择需要整理的资料，可按 Ctrl 多选。")
            return
        if any(entry["kind"] == "bundle" for entry in selected):
            QMessageBox.information(self, "整理到文件夹", "请先从资料合集打开并选择其中的原件。")
            return
        title, ok = QInputDialog.getText(self, "整理到新文件夹", "文件夹名称")
        if not ok or not title.strip():
            return
        parent = self.target_folder()
        folder = (Path(parent) / title.strip()).as_posix()
        moved = []
        try:
            self.index.create_folder(folder)
            for entry in selected:
                old = entry["relative"]
                new = self.index.move_file(old, folder)
                moved.append((new, entry["folder"]))
            self.current_folder = folder
            self.directory_mode = True
            self.quick_filter = "all"
            self.refresh()
        except (OSError, ValueError, UserError) as exc:
            rollback_errors = []
            cleanup_error = ""
            for relative, original_folder in reversed(moved):
                try:
                    self.index.move_file(relative, original_folder)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            try:
                self.index.delete_empty_folder(folder)
            except Exception as folder_exc:
                cleanup_error = str(folder_exc)
            message = "新建文件夹未完成：" + str(exc)
            if rollback_errors:
                message += "\n部分原件回退失败，请检查：" + "；".join(rollback_errors)
            if cleanup_error:
                message += "\n空文件夹清理失败，请检查：" + cleanup_error
            QMessageBox.information(self, "整理未完成", message)
            self.refresh()

    def selected_member(self):
        row = self.member_list.currentItem()
        group = self.selected()
        if row and group and group["kind"] == "bundle":
            return next((entry for entry in group["members"] if entry["relative"] == row.data(Qt.ItemDataRole.UserRole)), None)
        return None

    def update_member_actions(self, *_):
        member = self.selected_member()
        if member:
            self.note_button.setEnabled(member["kind"] == "note" and not member.get("missing") and member["size"] <= 1_000_000)

    def read_member(self, *_):
        member, group = self.selected_member(), self.selected()
        if member and group:
            try:
                group["accessed_ns"] = self.index.record_access(group["relative"])
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "最近使用未记录", str(exc))
            self.preview_entry(member)
            self.detail_tabs.setCurrentIndex(0)

    def open_member(self):
        member = self.selected_member()
        if member:
            self.open_entry(member)

    def set_bundle_entry(self):
        member, group = self.selected_member(), self.selected()
        if member and group:
            try:
                self.index.change_bundle(group, entry=member["relative"])
                self.preferred_identity = group["relative"]
                self.refresh()
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "首页未保存", str(exc))

    def ungroup_selected(self):
        group = self.selected()
        if group and group["kind"] == "bundle":
            try:
                self.index.change_bundle(group, active=False)
                self.refresh()
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "取消组合失败", str(exc))

    def remove_bundle_member(self):
        member, group = self.selected_member(), self.selected()
        if member and group:
            try:
                excluded = list(dict.fromkeys(group["bundle_record"].get("excluded", []) + [member["relative"]]))
                self.index.change_bundle(group, excluded=excluded)
                self.preferred_identity = group["relative"]
                self.refresh()
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "移出资料失败", str(exc))

    def add_bundle_members(self):
        group = self.selected()
        if not group or group["kind"] != "bundle":
            return
        choices = [entry for entry in self.items if entry["kind"] != "bundle"]
        dialog = QDialog(self)
        dialog.setWindowTitle("添加已有资料 · 原件保留原位")
        dialog.resize(480, 440)
        layout = QVBoxLayout(dialog)
        options = QListWidget()
        for entry in choices:
            row = QListWidgetItem(entry["name"])
            row.setData(Qt.ItemDataRole.UserRole, entry["relative"])
            row.setCheckState(Qt.CheckState.Unchecked)
            options.addItem(row)
        layout.addWidget(options, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            additions = [options.item(row).data(Qt.ItemDataRole.UserRole) for row in range(options.count()) if options.item(row).checkState() == Qt.CheckState.Checked]
            try:
                members = list(dict.fromkeys(group["bundle_record"].get("members", []) + additions))
                self.index.change_bundle(group, members=members, excluded=[key for key in group["bundle_record"].get("excluded", []) if key not in additions])
                self.preferred_identity = group["relative"]
                self.refresh()
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "添加资料失败", str(exc))

    def edit_note(self):
        if self.loading or self.editing_note:
            return
        entry = self.selected()
        if entry and entry["kind"] == "bundle":
            entry = self.selected_member()
        if not entry or entry["kind"] != "note" or not self.index or entry["size"] > 1_000_000:
            return
        try:
            original = self.index.read_note(entry["relative"])
            self.reading_sizes_before_edit = self.reading_split.sizes()
            self.editing_note = (dict(entry), original)
            self.note_editor.setPlainText(original)
            self.reading_stack.setCurrentIndex(1)
            self.note_save_bar.show()
            self.detail_tabs.setCurrentIndex(0)
            self.set_note_editing(True)
            self.update_workspace()
            self.note_editor.setFocus()
        except (OSError, UnicodeError, ValueError, UserError) as exc:
            QMessageBox.information(self, "笔记未保存", str(exc))

    def set_note_editing(self, editing):
        for widget in (self.file_views, self.folder_panel, self.search, self.import_button,
                       self.new_note_button, self.create_button, self.manage_button, self.type_filter, self.sort_choice,
                       self.open_button, self.note_button, self.more_button, self.filter_toggle, self.view_button):
            widget.setEnabled(not editing)
        for widget in (self.open_button, self.note_button, self.more_button):
            widget.setVisible(not editing)

    def cancel_note_edit(self):
        self.editing_note = None
        self.reading_stack.setCurrentIndex(0)
        self.note_save_bar.hide()
        self.set_note_editing(False)
        self.show_detail(self.current_detail)
        self.reading_split.setSizes(getattr(self, "reading_sizes_before_edit", [190, 420]))

    def save_note_edit(self, *, refresh=True):
        if not self.editing_note:
            return
        entry, original = self.editing_note
        try:
            self.index.save_note(entry["relative"], original, self.note_editor.toPlainText())
        except (OSError, UnicodeError, ValueError, UserError) as exc:
            QMessageBox.information(self, "笔记未保存", str(exc))
            return
        self.preferred_identity = entry["relative"]
        self.cancel_note_edit()
        if refresh:
            self.refresh()
        return True

    def confirm_note_close(self):
        if not self.editing_note or self.note_editor.toPlainText() == self.editing_note[1]:
            return True
        answer = QMessageBox.question(self, "未保存的笔记", "保存笔记修改后退出？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return bool(self.save_note_edit(refresh=False))
        return answer == QMessageBox.StandardButton.Discard

    def refresh(self):
        if self.editing_note:
            self.status.setText("正在编辑笔记，请保存或取消后刷新。")
            return
        if self.loading:
            self.refresh_pending = True
            return
        root = knowledge_root(self.window.store)
        if root is None:
            self.index = None
            self.items, self.folders = [], [""]
            self.current_folder = self.preferred_identity = ""
            self.current_detail = None
            self.content_count = 0
            self.scan_errors = []
            self.loaded_once = False
            self.refresh_pending = False
            self.refresh_tree()
            self.render_items()
            self.root_label.setText("设置中尚未指定 Obsidian Vault。")
            self.root_label.setToolTip("请在设置中选择 Obsidian Vault")
            self.status.setText("在设置中选择知识库根目录")
            return
        try:
            if self.index is None or self.index.root != Path(root).expanduser().absolute():
                self.index = KnowledgeIndex(self.window.store, root)
            self.root_label.setText(root.name + " · 当前资料库")
            self.root_label.setToolTip(str(root))
            self.loading = True
            self.refresh_button.setEnabled(False)
            self.status.setText("正在读取知识库…")
            index = self.index
            def loaded_current(report):
                selected_root = knowledge_root(self.window.store)
                if selected_root is None or Path(selected_root).expanduser().absolute() != index.root:
                    self.loading = False
                    self.refresh_pending = False
                    self.refresh()
                    return
                self.loaded(report)
            self.pending_task_id = self.window.submit(None, "扫描知识库索引", lambda context: index.scan(context), loaded_current, persist_result=False, record_history=False)
        except Exception as exc:
            self.loading = False
            self.refresh_button.setEnabled(True)
            QMessageBox.information(self, "知识库不可用", str(exc))

    def loaded(self, report):
        self.loading = False
        self.refresh_button.setEnabled(True)
        self.loaded_once = True
        self.items, self.folders = report["items"], report["folders"]
        self.content_count = report.get("content_count", len(self.items))
        self.scan_errors = report.get("errors", [])
        if self.current_folder not in self.folders:
            self.current_folder = ""
        self.refresh_tree()
        self.render_items()
        if self.refresh_pending:
            self.refresh_pending = False
            self.refresh()

    def refresh_tree(self):
        """Rebuild topic counts in place so deletes and restores update the sidebar."""
        expanded = {key for key, item in self.tree_items.items() if item.isExpanded()}
        counts = {}
        active_items = [entry for entry in self.items if entry["kind"] != "folder" and not entry.get("archived") and not entry.get("deleted")]
        for entry in active_items:
            folder = entry["folder"]
            while folder and folder != ".":
                counts[folder] = counts.get(folder, 0) + 1
                folder = Path(folder).parent.as_posix()
        self.folder_list.blockSignals(True)
        self.folder_list.clear()
        root_name = self.index.root.name if self.index else "知识库"
        root_item = QTreeWidgetItem([root_name, str(len(active_items))])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root_item.setToolTip(1, f"{len(active_items)} 项资料")
        self.folder_list.addTopLevelItem(root_item)
        self.tree_items = {"": root_item}
        organized = "02-知识" in self.folders
        if organized:
            self.folder_list.takeTopLevelItem(0)
            root_item = self.folder_list.invisibleRootItem()
            self.tree_items[""] = root_item
            self.tree_items["02-知识"] = root_item
        saved_order = self.index.folder_order() if self.index else []
        ordering = {folder: position for position, folder in enumerate(saved_order) if isinstance(folder, str)}
        for folder in sorted((f for f in self.folders if f), key=lambda f: (not f.startswith("02-知识/"), f.count("/"), ordering.get(f, 1_000_000), f.casefold())):
            if organized and (folder in {"02-知识", QUICK_CAPTURE_FOLDER} or not folder.startswith("02-知识/") or folder.count("/") > 3 or Path(folder).name == "附件"):
                continue
            parent = Path(folder).parent.as_posix()
            label = topic_name(Path(folder).name)
            node = QTreeWidgetItem([label, str(counts.get(folder, 0))])
            node.setData(0, Qt.ItemDataRole.UserRole, folder)
            node.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            node.setToolTip(1, f"{counts.get(folder, 0)} 项资料（含子专题）")
            node.setToolTip(0, label + " · 包含子专题的全部资料")
            node.setIcon(0, nav_icon(3, "#7890b1"))
            node.setSizeHint(0, QSize(0, 36))
            self.tree_items.get(parent, root_item).addChild(node)
            node.setExpanded(folder in expanded or (folder == "02-知识/02-开发与技术" and not expanded))
            self.tree_items[folder] = node
        root_item.setExpanded(True)
        header = self.folder_list.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(28)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(1, 34)
        if self.directory_mode:
            self.folder_list.setCurrentItem(self.tree_items[self.current_folder])
        self.folder_list.blockSignals(False)
        if not self.directory_mode:
            self.quick_list.blockSignals(True)
            for row in range(self.quick_list.count()):
                if self.quick_list.item(row).data(Qt.ItemDataRole.UserRole) == self.quick_filter:
                    self.quick_list.setCurrentRow(row)
            self.quick_list.blockSignals(False)

    def task_completed(self, identity, state):
        if identity == self.pending_task_id and state != "success":
            self.loading = False
            self.refresh_button.setEnabled(True)
            self.refresh_pending = False
            self.status.setText("索引扫描未完成；可查看任务记录或重试。")

    def select_folder(self, item, _previous=None):
        if item:
            self.quick_filter = "all"
            value = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            self.current_folder = value
            self.directory_mode = True
            self.quick_list.blockSignals(True)
            self.quick_list.setCurrentRow(-1)
            self.quick_list.blockSignals(False)
            self.render_items()

    def move_folder_order(self, delta):
        item = self.folder_list.currentItem()
        if not item:
            return
        parent = item.parent() or self.folder_list.invisibleRootItem()
        index = parent.indexOfChild(item)
        destination = index + delta
        if destination < 0 or destination >= parent.childCount():
            return
        parent.insertChild(destination, parent.takeChild(index))
        folders = []
        def visit(node):
            for i in range(node.childCount()):
                child = node.child(i)
                value = str(child.data(0, Qt.ItemDataRole.UserRole) or "")
                if value and not value.startswith("@"):
                    folders.append(value)
                visit(child)
        visit(self.folder_list.invisibleRootItem())
        try:
            self.index.save_folder_order(folders)
        except Exception as exc:
            QMessageBox.information(self, "调整顺序", str(exc))
            self.refresh()

    def selected(self):
        item = self.cards.currentItem() if self.grid_view else self.file_list.item(self.file_list.currentRow(), 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((entry for entry in self.shown if value and entry["relative"] == value["relative"]), None)

    def selected_entries(self):
        if self.grid_view:
            selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.cards.selectedItems()]
        else:
            selected = [self.file_list.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                        for index in self.file_list.selectionModel().selectedRows()]
        identities = {entry["relative"] for entry in selected}
        return [entry for entry in self.shown if entry["relative"] in identities]

    def resource_context_menu(self, view, position):
        item = view.itemAt(position)
        if item is None:
            return
        if view is self.file_list:
            row = item.row()
            selected_rows = {index.row() for index in view.selectionModel().selectedRows()}
            if row not in selected_rows:
                view.clearSelection()
                view.selectRow(row)
        elif item not in view.selectedItems():
            view.clearSelection()
            item.setSelected(True)
            view.setCurrentItem(item)
        selected = [entry for entry in self.selected_entries() if entry["kind"] != "folder"]
        if not selected:
            return
        self.build_resource_menu(selected).exec(view.viewport().mapToGlobal(position))

    def build_resource_menu(self, selected):
        menu = QMenu(self)
        if len(selected) > 1 and all(entry["kind"] != "bundle" for entry in selected):
            menu.addAction("整理到新文件夹…", self.group_selected)
        menu.addAction("整理到专题…", self.move_selected)
        menu.addSeparator()
        if all(entry.get("deleted") for entry in selected):
            menu.addAction("恢复到知识库", lambda checked=False, entries=tuple(selected): self.set_deleted(entries, False))
        else:
            menu.addAction("删除", lambda checked=False, entries=tuple(selected): self.set_deleted(entries, True))
        return menu

    def render_items(self, *_):
        if self.editing_note:
            return
        if not hasattr(self, "file_views"):
            return
        query = self.search.text().strip().casefold()
        shown = []
        type_key = self.type_filter.currentData()
        documents = {".md", ".txt", ".pdf", ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx"}
        code = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini", ".sh", ".ps1", ".go", ".rs", ".java", ".c", ".cpp", ".h"}
        for entry in self.items:
            archived = bool(entry.get("archived"))
            deleted = bool(entry.get("deleted"))
            if self.quick_filter == "deleted":
                state_match = deleted
            elif self.quick_filter == "archived":
                state_match = archived and not deleted
            else:
                state_match = not archived and not deleted
            folder = "" if entry["folder"] == "." else entry["folder"]
            in_folder = (not self.directory_mode or not self.current_folder or folder == self.current_folder
                or folder.startswith(self.current_folder + "/"))
            if not in_folder or not state_match:
                entry["matched_members"] = []
                continue
            searchable = " ".join((entry["name"], entry["relative"], " ".join(entry["tags"]), entry.get("search_text", ""), entry.get("summary", ""), entry.get("notes", ""))) if query else ""
            hits = [member for member in entry.get("members", []) if query in " ".join((member["name"], member.get("filename", ""), member.get("search_text", ""), " ".join(member.get("tags", [])), member.get("summary", ""))).casefold()] if query else []
            entry["matched_members"] = [member["relative"] for member in hits]
            suffix = entry.get("suffix", "").casefold()
            matches_type = {"all": True, "documents": suffix in documents, "code": suffix in code,
                "links": entry["kind"] == "link", "notes": suffix == ".md",
                "images": suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
                "external": entry["relative"].startswith("external:"),
                "other": suffix not in documents | code and entry["kind"] != "link"}.get(type_key, True)
            if type_key == "bundles":
                matches_type = entry["kind"] == "bundle"
            elif type_key == "skills":
                matches_type = entry.get("package_type") == "Skill"
            elif entry["kind"] == "bundle" and type_key != "all":
                matches_type = any({"documents": member.get("suffix") in documents,
                    "code": member.get("suffix") in code, "notes": member.get("suffix") == ".md",
                    "links": member["kind"] == "link", "images": member.get("suffix") in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
                    "external": member["relative"].startswith("external:"), "other": member.get("suffix") not in documents | code}.get(type_key, False) for member in entry["members"])
            quick_match = state_match and (self.directory_mode or {"all": True,
                "inbox": any(part.startswith(("00_收集箱", "00-收件箱", "收集箱")) for part in folder.split("/")),
                "recent": bool(entry.get("accessed_ns")),
                "projects": bool(entry["projects"]) or folder.startswith("03-项目"),
                "favorite": entry["favorite"], "tagged": bool(entry["tags"]),
                "links": entry["kind"] == "link" or any(member["kind"] == "link" for member in entry.get("members", [])), "large": entry["size"] > 100 * 1024 * 1024,
                "archived": True, "deleted": True}.get(self.quick_filter, True))
            matches_scope = (not self.scope_project or self.scope_project in entry["projects"]) and (not self.scope_agent or self.scope_agent in entry["agents"])
            if in_folder and quick_match and matches_type and matches_scope and (not self.tag_filter or self.tag_filter in entry["tags"]) and (not query or query in searchable.casefold() or hits):
                shown.append(entry)
        sort_key = self.sort_choice.currentData()
        shown.sort(key=lambda entry: (entry["kind"] != "folder", -entry.get("accessed_ns", 0) if self.quick_filter == "recent" and not self.directory_mode else entry["name"].casefold() if sort_key == "name"
            else -entry["size"] if sort_key == "size" else -entry["modified_ns"]))
        total_count = len(shown)
        in_inbox = self.quick_filter == "inbox" and not self.directory_mode
        self.inbox_summary.setVisible(in_inbox and total_count > 0)
        self.inbox_sort_button.setVisible(in_inbox and total_count > 0)
        self.purge_button.setVisible(self.quick_filter == "deleted" and not self.directory_mode and total_count > 0)
        if in_inbox:
            self.inbox_summary.setText(f"{total_count} 项待整理")
            self.inbox_sort_button.setEnabled(False)
        filter_state = (query, type_key, sort_key, self.current_folder, self.directory_mode,
                        self.quick_filter, self.scope_project, self.scope_agent, self.tag_filter)
        if filter_state != self.last_filter:
            self.page_index = 0
        else:
            preferred = next((i for i, entry in enumerate(shown) if entry["relative"] == self.preferred_identity), None)
            if preferred is not None:
                self.page_index = preferred // self.page_size
        self.last_filter = filter_state
        page_count = max(1, (total_count + self.page_size - 1) // self.page_size)
        self.page_index = min(self.page_index, page_count - 1)
        shown = shown[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        self.page_label.setText(f"{self.page_index + 1} / {page_count}")
        self.previous_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index < page_count - 1)
        self.shown = shown
        self.file_list.blockSignals(True)
        self.cards.blockSignals(True)
        self.file_list.setRowCount(len(shown))
        self.cards.clear()
        target = 0
        for row, entry in enumerate(shown):
            folder = entry["kind"] == "folder"
            file_type = resource_type(entry)
            values = (entry["name"], file_type, " · ".join(entry["tags"]), entry.get("summary", ""))
            # Cards paint semantic icons themselves; shell lookups can be slow
            # for network paths and are only needed for the visible table.
            icon = None if self.grid_view else (self.file_icon_provider.icon(QFileIconProvider.IconType.Folder) if folder else self.file_icon_provider.icon(QFileInfo(entry["path"])))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                # Headers default to centre alignment; match it so column
                # titles stay above their values instead of drifting left.
                if column:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, entry)
                    if icon is not None:
                        cell.setIcon(icon)
                self.file_list.setItem(row, column, cell)
            card = QListWidgetItem(entry["name"])
            card.setData(Qt.ItemDataRole.UserRole, entry)
            card.setToolTip(entry["name"] + "\n" + entry.get("summary", "") + "\n" + " · ".join(entry["tags"]))
            card.setSizeHint(self.cards.gridSize())
            self.cards.addItem(card)
            if entry["relative"] == self.preferred_identity:
                target = row
        if shown:
            self.file_list.setCurrentCell(target, 0)
            self.file_list.selectRow(target)
            self.cards.setCurrentRow(target)
        self.file_list.blockSignals(False)
        self.cards.blockSignals(False)
        self.file_views.setVisible(bool(shown))
        self.empty_panel.setVisible(not shown)
        self.update_topic_actions()
        self.update_location()
        has_filter = bool(query or type_key != "all" or self.tag_filter or self.scope_project or self.scope_agent
                          or self.quick_filter not in {"all", "inbox"})
        self.empty_icon.setVisible(has_filter)
        self.empty_art.setVisible(not has_filter)
        self.empty_steps.setVisible(not has_filter and not self.directory_mode and self.quick_filter == "all")
        if has_filter:
            self.empty_icon.setPixmap(nav_icon(19, "#3979ff").pixmap(24, 24))
            self.empty_title.setText("没有找到匹配的资料")
            self.empty_description.setText("试试换一个关键词，或清除当前筛选条件。")
            self.empty_action.setText("清除筛选")
            self.empty_action.show()
            self.empty_secondary.hide()
            if not (query or type_key != "all" or self.tag_filter or self.scope_project or self.scope_agent):
                empty_views = {
                    "favorite": ("还没有收藏的资料", "点击资料卡片或预览区的星标，把常用内容留在这里。"),
                    "recent": ("还没有浏览记录", "从知识库打开或编辑资料后，可以在这里继续查阅。"),
                    "links": ("还没有收集网页", "把值得留下的链接收进知识库，随时回来查阅。"),
                    "projects": ("还没有关联项目的资料", "在资料信息中添加项目关联，便于按工作查找。"),
                    "archived": ("归档中还没有资料", "暂时不用的资料可以归档，需要时再恢复。"),
                    "deleted": ("回收站是空的", "移入回收站的资源会显示在这里，原件仍然保留。"),
                }
                if self.quick_filter in empty_views:
                    title, description = empty_views[self.quick_filter]
                    self.empty_title.setText(title)
                    self.empty_description.setText(description)
                    self.empty_action.setText("浏览全部资源")
        elif self.directory_mode and self.current_folder.startswith("02-知识/"):
            label = topic_name(Path(self.current_folder).name)
            self.empty_icon.setText("知")
            self.empty_title.setText(f"“{label}”还没有资源")
            self.empty_description.setText("可添加文件、链接、笔记或整个目录。\n默认保存到此专题，保存前可更改位置。")
            self.empty_action.setText("＋ 添加资料")
            self.empty_action.show()
            self.empty_secondary.setText("查看收件箱")
            self.empty_secondary.show()
        elif self.quick_filter == "inbox" and not self.directory_mode:
            self.empty_icon.setText("收")
            self.empty_title.setText("收件箱暂时为空")
            self.empty_description.setText("快速收集的内容会先放在这里，等你有空时再归入主题。")
            self.empty_action.setText("＋ 添加资料")
            self.empty_action.show()
            self.empty_secondary.setText("浏览全部资源")
            self.empty_secondary.show()
        else:
            self.empty_icon.setText("知")
            self.empty_title.setText("让零散的灵感，有一个归处")
            self.empty_description.setText("网页、文件、笔记，都可以从这里收集。\n先放进收件箱，再按专题慢慢整理。")
            self.empty_action.setText("＋ 添加资料")
            self.empty_action.show()
            self.empty_secondary.setText("新建专题")
            self.empty_secondary.show()
            if self.index is None and knowledge_root(self.window.store) is None:
                self.empty_title.setText("连接你的知识空间")
                self.empty_description.setText("先在设置中选择 Obsidian Vault，\n让文件、笔记和链接在同一个地方相遇。")
                self.empty_action.setText("前往设置")
                self.empty_secondary.hide()
                self.empty_steps.hide()
        quick_names = {"all": "全部资源", "inbox": "收集箱", "recent": "最近使用", "favorite": "我的收藏", "projects": "项目资料", "tagged": "有标签", "links": "网页收藏", "large": "大型资源", "archived": "归档", "deleted": "回收站"}
        self.breadcrumb.setText((" › ".join(topic_name(part) for part in self.current_folder.split("/") if part != "02-知识") or "全部主题") if self.directory_mode else quick_names[self.quick_filter])
        self.breadcrumb.setToolTip(self.current_folder)
        scope = " · ".join(value for value in (self.scope_project, self.scope_agent) if value)
        self.filter_hint.setText(f"{total_count} 条资源" + (" · 标签：" + self.tag_filter if self.tag_filter else "") + (" · 关联：" + scope if scope else ""))
        self.tag_button.setProperty("primary", bool(self.tag_filter))
        self.tag_button.setToolTip("当前标签：" + self.tag_filter if self.tag_filter else "按标签筛选资源")
        self.clear_filter_button.setVisible(has_filter)
        resource_count = sum(entry["kind"] != "folder" for entry in self.items)
        active_count = sum(entry["kind"] != "folder" and not entry.get("archived") and not entry.get("deleted") for entry in self.items)
        self.paging_bar.setVisible(page_count > 1)
        self.detail_button.setVisible(bool(shown))
        self.parent_button.setVisible(self.directory_mode and bool(self.current_folder))
        available_count = resource_count if self.quick_filter in {"archived", "deleted"} else active_count
        for widget in (self.type_filter, self.sort_choice, self.tag_button, self.view_button):
            widget.setVisible(bool(available_count or has_filter))
        self.filter_hint.setVisible(has_filter)
        self.status.setVisible(bool(self.scan_errors))
        if not self.loading:
            self.status.setText(f"{resource_count} 项资料 · 索引已更新" + (f" · {len(self.scan_errors)} 个读取提示" if self.scan_errors else ""))
        self.status.setToolTip("\n".join(self.scan_errors))
        if shown:
            self.select_file()
        else:
            self.show_detail(None)

    @staticmethod
    def modified_label(timestamp):
        try:
            return datetime.fromtimestamp(timestamp / 1_000_000_000).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return "未知时间"

    @staticmethod
    def type_name(entry):
        suffix = entry.get("suffix", "").lstrip(".").upper()
        return suffix or "文件"

    def select_quick_filter(self, item, _previous=None):
        if item:
            self.quick_filter = str(item.data(Qt.ItemDataRole.UserRole) or "all")
            self.current_folder = ""
            self.directory_mode = False
            if hasattr(self, "folder_list"):
                self.folder_list.clearSelection()
            self.render_items()

    def toggle_deleted(self):
        entry = self.selected()
        if not entry or not self.index:
            return
        self.set_deleted([entry], not bool(entry.get("deleted")))

    def set_deleted(self, entries, deleted):
        """Move resources to the recycle bucket (or back) from list or detail."""
        entries = [entry for entry in entries if entry.get("kind") != "folder"]
        if not entries or not self.index:
            return
        if deleted:
            if len(entries) == 1:
                text = f"将“{entries[0]['name']}”的知识库本地文件移入系统回收站？资料合集的整个目录也会移入。"
            else:
                text = f"将选中的 {len(entries)} 项知识库本地文件移入系统回收站？资料合集的整个目录也会移入。"
            paths = []
            for entry in entries[:5]:
                bundle = entry.get("bundle_record") if entry.get("kind") == "bundle" else None
                paths.append(str(bundle.get("directory", "") if bundle else entry.get("path", "")) or "无独立目录")
            text += "\n\n" + "\n".join(paths)
            if len(entries) > 5:
                text += f"\n另有 {len(entries) - 5} 项。"
            text += "\n库外引用不会删除。请确认所选文件和目录。"
            if QMessageBox.question(self, "删除资源", text) != QMessageBox.StandardButton.Yes:
                return
            failed = []
            for entry in entries:
                try:
                    self.index.delete_local_resource(entry, QFile.moveToTrash)
                except (OSError, UserError) as exc:
                    failed.append(f"{entry['name']}：{exc}")
            self.preferred_identity = ""
            self.refresh()
            if failed:
                QMessageBox.information(self, "资源未删除", "以下资料未完成删除：\n" + "\n".join(failed[:5]))
            return
        failed = []
        for entry in entries:
            changes = {"deleted": deleted}
            if not deleted:
                changes["archived"] = False
            try:
                self.index.update_resource(entry["relative"], **changes)
            except Exception as exc:
                failed.append(f"{entry['name']}：{exc}")
                continue
            entry.update(changes)
        self.preferred_identity = ""
        self.refresh_tree()
        self.render_items()
        if failed:
            QMessageBox.information(self, "资源删除/恢复", "以下资料未处理：\n" + "\n".join(failed[:5]))

    def purge_deleted(self):
        """Empty the recycle bucket: originals go to the system trash, records disappear."""
        entries = [entry for entry in self.items if entry.get("deleted") and entry["kind"] != "folder"]
        if not entries or not self.index:
            return
        if QMessageBox.question(
                self, "清空回收站",
                f"将回收站中的 {len(entries)} 项资料移入系统回收站？\n"
                "知识库中的本地副本会进入系统回收站；库外引用只移除登记，源文件保留。系统回收站清空后无法找回。") != QMessageBox.StandardButton.Yes:
            return
        failed = []
        failed_entries = []
        cleared = 0
        for entry in entries:
            try:
                self.index.purge_deleted_resource(entry, QFile.moveToTrash)
            except Exception as exc:
                failed.append(f"{entry['name']}：{exc}")
                failed_entries.append(entry)
                continue
            cleared += 1
        self.preferred_identity = ""
        failed_relatives = {entry["relative"] for entry in failed_entries}
        self.items = [entry for entry in self.items
                      if not entry.get("deleted") or entry["relative"] in failed_relatives]
        self.refresh_tree()
        self.render_items()
        if failed:
            QMessageBox.information(self, "清空回收站", "以下资料未完成清理：\n" + "\n".join(failed[:5]))
        else:
            self.status.setText(f"已清空回收站 {cleared} 项，原文件在系统回收站中")

    def toggle_view(self):
        entry = self.selected()
        if entry:
            self.preferred_identity = entry["relative"]
        self.grid_view = not self.grid_view
        self.file_views.setCurrentIndex(1 if self.grid_view else 0)
        self.view_button.setText("列表" if self.grid_view else "卡片")
        self.render_items()

    def reveal_root(self):
        root = knowledge_root(self.window.store)
        if root and root.exists():
            self.window.open_path(root)
        else:
            QMessageBox.information(self, "知识库目录", "请先在设置中选择知识库根目录。")

    @staticmethod
    def pretty_size(size):
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024

    def select_file(self, *_):
        entry = self.selected()
        if entry:
            self.preferred_identity = entry["relative"]
        self.show_detail(entry)
        if self.quick_filter == "inbox" and not self.directory_mode:
            self.inbox_sort_button.setEnabled(bool(self.selected_entries()))

    def show_detail(self, entry):
        self.current_detail = entry
        if not entry:
            self.detail_requested = False
        self.update_workspace()
        available = bool(entry and not entry.get("missing") and not entry.get("deleted"))
        file_entry = bool(entry and entry["kind"] != "folder")
        self.open_button.setEnabled(available)
        self.reveal_button.setEnabled(available)
        self.favorite_button.setEnabled(bool(file_entry and not (entry and entry.get("deleted"))))
        self.metadata_button.setEnabled(file_entry)
        self.note_button.setEnabled(bool(available and entry["kind"] == "note" and entry["size"] <= 1_000_000))
        self.delete_button.setEnabled(bool(entry))
        self.delete_button.setText("恢复资源" if entry and entry.get("deleted") else "删除")
        self.metadata_button.setEnabled(bool(available and file_entry))
        self.parent_button.setEnabled(self.directory_mode and bool(self.current_folder))
        for view in (self.preview, self.info_text):
            view.clear()
        self.detail_tabs.setTabText(2, "标签")
        self.detail_tabs.setTabText(3, "关联")
        self.detail_context.clear()
        self.related_summary.clear()
        self.related_summary.hide()
        self.reading_label.clear()
        self.member_list.clear()
        self.detail_tabs.setTabVisible(4, bool(entry and entry["kind"] == "bundle"))
        if not entry:
            self.detail_title.setText("资料详情")
            self.detail_facts.setText("选择资源，阅读内容与个人记录")
            self.tags_tab_summary.setText("暂无标签")
            self.relations_tab_summary.setText("暂无关联")
            self.favorite_button.setText("☆")
            self.preview.setPlainText("选择一条资源，查看内容、用途与关联信息。")
            return
        self.detail_title.setText(entry["name"])
        folder = entry["kind"] == "folder"
        type_label = "文件夹" if folder else {"note": "Markdown", "link": "链接"}.get(entry["kind"], self.type_name(entry))
        self.detail_facts.setText(resource_type(entry) + (f" · {len(entry['members'])} 份内容" if entry["kind"] == "bundle" else ""))
        context = []
        if entry.get("summary"):
            context.append("<b>用途</b><br>" + html.escape(entry["summary"][:300]))
        if entry.get("tags"):
            context.append(" · ".join("#" + html.escape(tag) for tag in entry["tags"][:8]))
        if entry.get("projects") or entry.get("agents"):
            context.append("关联：" + html.escape(" · ".join(entry["projects"] + entry["agents"])))
        if entry.get("notes"):
            context.append("<b>我的记录</b><br>" + html.escape(entry["notes"][:200]).replace("\n", "<br>"))
        self.detail_context.setText("<br><br>".join(context))
        self.detail_context.setVisible(bool(context))
        related = [resource for resource in self.items if resource["relative"] in entry.get("related", [])]
        if related:
            self.related_summary.setText("<b>关联资料</b><br>" + "<br>".join('<a href="' + html.escape(resource["relative"], quote=True) + '">' + html.escape(resource["name"]) + '</a>' for resource in related[:6]))
            self.related_summary.show()
        self.favorite_button.setText("★" if entry["favorite"] else "☆")
        self.open_button.setText("打开原文件")
        self.note_button.setVisible(entry["kind"] == "note")
        storage = "外部文件引用" if entry["relative"].startswith("external:") else "知识库本地"
        self.info_text.setPlainText("标签：" + (" · ".join(entry["tags"]) or "无") + "\n关联：" + (" · ".join(entry["projects"] + entry["agents"]) or "无") + "\n\n" + f"名称\n{entry.get('filename', entry['name'])}\n\n位置\n{entry['path']}\n\n修改时间\n{self.modified_label(entry['modified_ns']) if not folder else '—'}\n\n存储方式\n{storage}\n\n状态\n{'已失效，索引记录仍保留' if entry.get('missing') else '可访问'}")
        self.tags_tab_summary.setText("\n\n".join("# " + tag for tag in entry["tags"]) or "暂无标签\n点击下方「编辑资料信息」添加。")
        self.tags_tab_summary.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.relations_tab_summary.setText(("我的记录\n" + entry.get("notes", "") + "\n\n" if entry.get("notes") else "") + "关联项目\n" + ("\n".join(entry["projects"]) or "暂无") + "\n\n关联 Agent\n" + ("\n".join(entry["agents"]) or "暂无"))
        self.relations_tab_summary.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_tabs.setTabText(2, "标签")
        self.detail_tabs.setTabToolTip(2, f"{len(entry['tags'])} 个标签")
        self.detail_tabs.setTabText(3, "关联")
        self.detail_tabs.setTabToolTip(3, f"{len(entry['projects']) + len(entry['agents'])} 个关联")
        path = Path(entry["path"])
        if entry.get("missing"):
            self.preview.setPlainText("原文件当前无法访问。\n记录与标签仍然保留，文件恢复后可刷新重新读取。")
            return
        if folder:
            self.preview.setPlainText("双击文件夹或展开左侧目录树，浏览其中的文件。")
            return
        if entry["kind"] == "bundle":
            for member in entry["members"]:
                label = member.get("filename", member["name"])
                directory = entry["bundle_record"].get("directory", "")
                if directory:
                    base = Path(directory) if Path(directory).is_absolute() else self.index.root / directory
                    if Path(member["path"]).is_relative_to(base):
                        label = Path(member["path"]).relative_to(base).as_posix()
                row = QListWidgetItem(("★ " if member["relative"] == entry["entry_relative"] else "") + label + (" · 位置失效" if member.get("missing") else ""))
                row.setData(Qt.ItemDataRole.UserRole, member["relative"])
                row.setToolTip(member["path"])
                self.member_list.addItem(row)
            hits = entry.get("matched_members", [])
            identity = hits[0] if hits else entry["entry_relative"]
            member = next((member for member in entry["members"] if member["relative"] == identity), None)
            if member:
                self.preview_entry(member)
                self.reading_label.setText(("搜索命中 · " if hits else "首页 · ") + member.get("filename", member["name"]))
                for row in range(self.member_list.count()):
                    if self.member_list.item(row).data(Qt.ItemDataRole.UserRole) == identity:
                        self.member_list.setCurrentRow(row)
            else:
                self.preview.setPlainText("选择“包含内容”中的资料阅读，或添加内容后设置首页。")
            self.detail_tabs.setTabText(4, "资料")
            self.detail_tabs.setTabToolTip(4, f"{len(entry['members'])} 份内部内容")
            return
        self.preview_entry(entry)

    def preview_entry(self, entry):
        self.reading_label.setText(entry.get("filename", entry["name"]))
        self.preview.clear()
        type_label = resource_type(entry)
        if entry.get("missing"):
            self.preview.setPlainText("这份内容当前无法访问，资源记录仍保留。")
            return
        suffix = entry.get("suffix", "").casefold()
        try:
            path = self.index.resolve_entry_path(entry["relative"])
            if entry["kind"] == "link":
                text = self.index.read_entry_text(entry["relative"], 48_000)[:12_000]
                target = next((line[4:].strip() for line in text.splitlines() if line.startswith("URL=")), "")
                self.preview.setHtml("<h3>" + html.escape(entry["name"]) + "</h3><p>网页收藏</p><p>" + html.escape(target) + "</p><p>点击下方「打开链接」访问。</p>")
            elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} and entry["size"] <= 20_000_000:
                reader = QImageReader(str(path))
                dimensions = reader.size()
                if dimensions.width() * dimensions.height() > 40_000_000:
                    self.preview.setPlainText("图片分辨率较大，请通过系统图片查看器打开。")
                    return
                if dimensions.width() > 640:
                    reader.setScaledSize(dimensions.scaled(QSize(640, 640), Qt.AspectRatioMode.KeepAspectRatio))
                picture = reader.read()
                if picture.isNull():
                    self.preview.setPlainText("此图片无法预览，请使用系统应用打开。")
                    return
                width = max(200, self.preview.viewport().width() - 28)
                picture = picture.scaledToWidth(min(width, picture.width()), Qt.TransformationMode.SmoothTransformation)
                self.preview.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl("knowledge:image"), picture)
                self.preview.setHtml('<p align="center"><img src="knowledge:image"></p>')
            elif suffix in KnowledgeIndex.TEXT_EXTENSIONS and entry["size"] <= 500_000:
                text = self.index.read_entry_text(entry["relative"], 500_000)
                if suffix == ".md":
                    self.preview.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
                    self.preview.setMarkdown(markdown_preserving_linebreaks(note_header(text)[1]))
                else:
                    self.preview.setPlainText(text)
            else:
                self.preview.setHtml("<h3>" + html.escape(type_label) + " 文件</h3><p>" + html.escape(entry["name"]) + "</p><p>" + self.pretty_size(entry["size"]) + "</p><p>使用下方「打开文件」，通过默认应用查看完整内容。</p>")
        except (OSError, UserError, UnicodeError) as exc:
            self.preview.setPlainText("预览不可用：" + str(exc))

    def add_file(self, project_id=""):
        self.collect_resource("file", project_id=project_id if isinstance(project_id, str) else "",
                              default_folder=self.capture_folder())

    def associate_created(self, path):
        relative = Path(path).relative_to(self.index.root).as_posix()
        values = {}
        if self.scope_project:
            values["projects"] = [self.scope_project]
        if self.scope_agent:
            values["agents"] = [self.scope_agent]
        if values:
            self.index.update_metadata(relative, **values)
        self.preferred_identity = relative

    def target_folder(self):
        if self.directory_mode:
            return self.current_folder
        return "02-知识" if "02-知识" in self.folders else ""

    def import_files(self, sources, project_id=""):
        if not sources or not self.ensure_index():
            return
        index = self.index
        folder = self.capture_folder()
        project, agent = project_id or self.scope_project, self.scope_agent
        def operation(context):
            if not (index.root / folder).is_dir():
                index.create_folder(folder)
            added, errors, preferred = 0, [], ""
            for source in sources:
                context.checkpoint()
                try:
                    path = index.add_file(source, folder, copy=True, context=context)
                    relative = Path(path).relative_to(index.root).as_posix()
                    index.update_metadata(relative, projects=[project] if project else [], agents=[agent] if agent else [])
                    preferred, added = relative, added + 1
                except (OSError, ValueError, UserError) as exc:
                    errors.append(Path(source).name + "：" + str(exc))
            return {"added": added, "errors": errors, "preferred": preferred}
        def completed(report):
            if report["added"]:
                self.preferred_identity = report["preferred"]
                self.current_folder, self.directory_mode = folder, folder != QUICK_CAPTURE_FOLDER
                self.quick_filter = "inbox" if folder == QUICK_CAPTURE_FOLDER else "all"
                self.search.clear()
                self.type_filter.setCurrentIndex(0)
                self.tag_filter = ""
                self.refresh()
            message = f"已导入 {report['added']} 个文件，原文件保留。"
            self.status.setText(message)
            self.window.statusBar().showMessage(message, 6000)
            if report["errors"]:
                QMessageBox.information(self, "导入结果", message + "\n" + "\n".join(report["errors"][:20]))
        self.window.submit(None, "导入知识文件", operation, completed, persist_result=False)

    def new_folder(self):
        if not self.ensure_index():
            return
        name, ok = QInputDialog.getText(self, "新建分类目录", "文件夹名称")
        if ok and name.strip():
            parent = self.current_folder or ("02-知识" if "02-知识" in self.folders else "")
            relative = (Path(parent) / name.strip()).as_posix()
            try:
                self.index.create_folder(relative)
                self.current_folder = relative
                self.directory_mode = True
                self.refresh()
            except Exception as exc:
                QMessageBox.information(self, "新建目录", str(exc))

    def rename_folder(self):
        item = self.folder_list.currentItem()
        relative = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        if not self.index or not relative:
            return
        name, ok = QInputDialog.getText(self, "重命名目录", "新名称", text=Path(relative).name)
        if ok:
            try:
                parent = Path(relative).parent.as_posix()
                self.index.rename_folder(relative, name)
                self.current_folder = (Path(parent) / name).as_posix() if parent != "." else name
                self.directory_mode = True
                self.refresh()
            except Exception as exc:
                QMessageBox.information(self, "重命名目录", str(exc))

    def delete_folder(self):
        item = self.folder_list.currentItem()
        relative = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        if not self.index or not relative:
            return
        if QMessageBox.question(self, "删除空目录", f"确认删除空目录“{relative}”？只允许删除空文件夹。") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.index.delete_empty_folder(relative)
            self.current_folder = Path(relative).parent.as_posix() if Path(relative).parent.as_posix() != "." else ""
            self.refresh()
        except Exception as exc:
            QMessageBox.information(self, "删除目录", str(exc))

    def new_url(self):
        self.collect_resource("link")

    def capture_folder(self):
        return self.current_folder if self.directory_mode and self.current_folder else QUICK_CAPTURE_FOLDER

    def new_note(self):
        folder = self.current_folder if self.directory_mode and self.current_folder else QUICK_CAPTURE_FOLDER
        self.collect_resource("note", default_folder=folder)

    def toggle_favorite(self):
        entry = self.selected()
        if entry and entry["kind"] != "folder" and self.index:
            try:
                value = not entry["favorite"]
                self.index.update_metadata(entry["relative"], favorite=value, tags=entry["tags"])
                entry["favorite"] = value
                self.preferred_identity = entry["relative"]
                self.render_items()
            except Exception as exc:
                QMessageBox.information(self, "收藏资料", str(exc))

    def edit_metadata(self):
        entry = self.selected()
        if not entry or entry["kind"] == "folder" or not self.index:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑资料信息")
        dialog.resize(580, 550)
        form = QFormLayout(dialog)
        title = QLineEdit(entry["name"])
        summary = QLineEdit(entry.get("summary", ""))
        notes = QTextEdit()
        notes.setAcceptRichText(False)
        notes.setPlainText(entry.get("notes", ""))
        notes.setMaximumHeight(140)
        notes.setPlaceholderText("记录用途、使用心得、待解决的问题…")
        form.addRow("标题", title)
        form.addRow("用途", summary)
        form.addRow("我的记录", notes)
        related = list(entry.get("related", []))
        related_button = QPushButton(f"选择关联资料（{len(related)}）")
        def choose_related():
            chooser = QDialog(dialog)
            chooser.setWindowTitle("关联已有资料")
            chooser.resize(480, 480)
            chooser_layout = QVBoxLayout(chooser)
            search = QLineEdit()
            search.setPlaceholderText("搜索已有资料标题…")
            chooser_layout.addWidget(search)
            options = QListWidget()
            for resource in self.items:
                if resource["relative"] == entry["relative"]:
                    continue
                row = QListWidgetItem(resource["name"])
                row.setData(Qt.ItemDataRole.UserRole, resource["relative"])
                row.setCheckState(Qt.CheckState.Checked if resource["relative"] in related else Qt.CheckState.Unchecked)
                options.addItem(row)
            chooser_layout.addWidget(options, 1)
            def filter_rows(text):
                for index in range(options.count()):
                    row = options.item(index)
                    row.setHidden(text.casefold() not in row.text().casefold())
            search.textChanged.connect(filter_rows)
            controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            controls.accepted.connect(chooser.accept)
            controls.rejected.connect(chooser.reject)
            chooser_layout.addWidget(controls)
            if chooser.exec() == QDialog.DialogCode.Accepted:
                related[:] = [options.item(index).data(Qt.ItemDataRole.UserRole) for index in range(options.count()) if options.item(index).checkState() == Qt.CheckState.Checked]
                related_button.setText(f"选择关联资料（{len(related)}）")
        related_button.clicked.connect(choose_related)
        form.addRow("关联资料", related_button)
        fields = {}
        for key, label, hint in (("tags", "标签", "多个标签用逗号分隔"),
                                 ("projects", "关联项目", "项目编号，多个用逗号分隔"),
                                 ("agents", "关联 Agent", "Agent 登记编号，多个用逗号分隔")):
            editor = QLineEdit(", ".join(entry[key]))
            editor.setPlaceholderText(hint)
            form.addRow(label, editor)
            fields[key] = editor
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = {key: list(dict.fromkeys(value.strip() for value in editor.text().replace("，", ",").replace("、", ",").split(",") if value.strip())) for key, editor in fields.items()}
        details = {"title": title.text().strip(), "summary": summary.text().strip(), "related": related,
                   "notes": notes.toPlainText()}
        try:
            accessed_ns = self.index.save_resource_details(entry["relative"], values, details)
            entry.update(values)
            entry.update(details)
            entry["name"] = details["title"] or entry["name"]
            entry["accessed_ns"] = accessed_ns
            self.preferred_identity = entry["relative"]
            self.render_items()
        except Exception as exc:
            QMessageBox.information(self, "资料信息未保存", str(exc))

    def open_file(self):
        entry = self.selected()
        if entry and entry["kind"] == "bundle":
            try:
                entry["accessed_ns"] = self.index.record_access(entry["relative"])
            except (OSError, ValueError, UserError) as exc:
                QMessageBox.information(self, "最近使用未记录", str(exc))
            entry = next((member for member in entry["members"] if member["relative"] == entry["entry_relative"]), None) or next((member for member in entry["members"] if not member.get("missing")), None)
        self.open_entry(entry)

    def open_entry(self, entry):
        root = knowledge_root(self.window.store)
        if (not entry or entry.get("missing") or self.index is None or root is None
                or Path(root).expanduser().absolute() != self.index.root):
            return
        if entry["kind"] == "folder":
            self.folder_list.setCurrentItem(self.tree_items.get(entry["relative"]))
            return
        path = Path(entry["path"])
        try:
            entry["accessed_ns"] = self.index.record_access(entry["relative"])
        except (OSError, ValueError, UserError) as exc:
            QMessageBox.information(self, "最近使用未记录", str(exc))
        try:
            vault = self.index.root.resolve()
            require_vault(self.index.root)
            path = path.resolve(strict=True)
            if not path.is_relative_to(vault):
                raise UserError("这项资料位于 Obsidian 知识库之外，无法通过 Obsidian 打开。可用“打开文件夹”查看原件位置。")
            if not QDesktopServices.openUrl(QUrl(open_uri(path))):
                raise UserError("未能启动 Obsidian，请确认已安装并注册 obsidian:// 协议。")
        except (OSError, ValueError, UserError) as exc:
            QMessageBox.information(self, "在 Obsidian 中打开", str(exc))

    def reveal_file(self):
        entry = self.selected()
        if entry:
            path = Path(entry["path"])
            folder = path if entry["kind"] == "folder" else path.parent
            self.window.open_path(folder)

    def move_selected(self):
        if not self.ensure_index():
            return
        selected = [entry for entry in self.selected_entries() if entry["kind"] != "folder"]
        if not selected:
            QMessageBox.information(self, "归类资料", "请在文件列表中选择要移动的资料。")
            return
        choices = [folder for folder in self.folders
                   if (folder == "02-知识" or folder.startswith("02-知识/"))
                   and folder != QUICK_CAPTURE_FOLDER and not folder.startswith(QUICK_CAPTURE_FOLDER + "/")]
        if not choices:
            QMessageBox.information(self, "归类资料", "知识库中还没有可用的主题文件夹。")
            return
        default_index = choices.index("02-知识") if "02-知识" in choices else 0
        destination_label, ok = QInputDialog.getItem(self, "选择目标位置", "移动到知识库文件夹", choices, default_index, False)
        if not ok:
            return
        destination = destination_label
        if QMessageBox.question(self, "移动原件", f"将 {len(selected)} 项资料及其原件移动到\n“{destination}”？") != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for entry in selected:
            try:
                if entry["kind"] == "bundle":
                    self.index.move_bundle(entry, destination)
                else:
                    self.index.move_file(entry["relative"], destination)
            except Exception as exc:
                errors.append(entry["name"] + "：" + str(exc))
        self.refresh()
        if errors:
            QMessageBox.information(self, "部分资料未移动", "\n".join(errors))

    def set_scope(self, project="", agent=""):
        self.scope_project, self.scope_agent = project, agent
        self.tag_filter = ""
        self.search.clear()
        self.current_folder = ""
        self.quick_filter = "all"
        self.directory_mode = False
        self.type_filter.setCurrentIndex(0)
        self.quick_list.blockSignals(True)
        self.quick_list.setCurrentRow(-1)
        self.quick_list.blockSignals(False)
        self.folder_list.blockSignals(True)
        self.folder_list.setCurrentItem(self.tree_items.get(""))
        self.folder_list.blockSignals(False)
        self.render_items()

    def drop_files(self, event):
        sources = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not sources:
            event.ignore()
            return
        event.acceptProposedAction()
        directories = [source for source in sources if Path(source).is_dir()]
        if directories:
            if len(sources) == 1:
                self.collect_resource("directory", prefill=directories[0], project_id=self.scope_project)
            else:
                QMessageBox.information(self, "收集完整目录", "目录请一次收集一个；多个文件可以组成一条资源。")
            return
        self.collect_resource("file", sources=sources, project_id=self.scope_project,
                              default_folder=self.capture_folder())

