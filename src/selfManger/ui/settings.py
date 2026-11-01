from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QSpinBox, QTabBar, QTextEdit, QToolButton, QVBoxLayout, QWidget, QApplication)

from .components import nav_icon
from ..maintenance import backup_retention_count


def build_settings(window):
    from .window import button
    from .theme import apply_theme
    from ..workbench import work_root

    page = QWidget()
    page.setObjectName("SettingsPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(10)
    title = QLabel("设置")
    title.setObjectName("Title")
    subtitle = QLabel("调整外观、资料位置与备份偏好。每项设置独立保存。")
    subtitle.setObjectName("Subtitle")
    subtitle.setWordWrap(True)
    layout.addWidget(title)
    layout.addWidget(subtitle)

    window.settings_tabs = QTabBar()
    window.settings_tabs.setObjectName("SettingsTabs")
    window.settings_tabs.setExpanding(False)
    window.settings_tabs.setDrawBase(False)
    for name in ("通用", "资料位置", "备份与安全", "高级维护"):
        window.settings_tabs.addTab(name)
    layout.addWidget(window.settings_tabs)
    groups = {0: [], 1: [], 2: [], 3: []}

    def card(title_text, hint, icon, group=0):
        frame = QFrame()
        frame.setObjectName("SettingsCard")
        groups[group].append(frame)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(18, 15, 18, 16)
        outer.setSpacing(12)
        header = QHBoxLayout()
        badge = QLabel()
        icon_index = {"☼": 7, "▱": 3, "◇": 3, "▤": 5, "▣": 12, "⚙": 7, "✓": 12, "i": 11}
        badge.setPixmap(nav_icon(icon_index.get(icon, 7), "#2563eb").pixmap(24, 24))
        badge.setObjectName("SettingsIcon")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(48, 48)
        header.addWidget(badge)
        text = QVBoxLayout()
        name = QLabel(title_text)
        name.setObjectName("SettingsCardTitle")
        description = QLabel(hint)
        description.setObjectName("Subtitle")
        description.setWordWrap(True)
        text.addWidget(name)
        if hint:
            text.addWidget(description)
        header.addLayout(text, 1)
        outer.addLayout(header)
        content = QVBoxLayout()
        content.setSpacing(10)
        outer.addLayout(content)
        return frame, content

    left_scroll = QScrollArea()
    window.settings_scroll = left_scroll
    left_scroll.setWidgetResizable(True)
    left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    left_body = QWidget()
    left_body.setObjectName("SettingsBody")
    left_layout = QVBoxLayout(left_body)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(12)

    appearance_card, appearance = card("外观", "选择更适合当前环境的显示主题，切换后立即生效并保存在本机。", "☼")
    appearance_row = QHBoxLayout()
    appearance_row.addWidget(QLabel("界面主题"))
    window.appearance_mode = QComboBox()
    window.appearance_mode.addItem("浅色", "light")
    window.appearance_mode.addItem("深色", "dark")
    current_appearance = window.service.setting("appearance", "light")
    window.appearance_mode.setCurrentIndex(max(0, window.appearance_mode.findData(current_appearance)))
    def change_appearance(index):
        mode = window.appearance_mode.itemData(index)
        if mode in {"light", "dark"}:
            saved_mode = window.service.set_appearance(mode)
            apply_theme(QApplication.instance(), saved_mode)
    window.appearance_mode.currentIndexChanged.connect(change_appearance)
    appearance_row.addWidget(window.appearance_mode, 1)
    appearance.addLayout(appearance_row)
    left_layout.addWidget(appearance_card)

    markdown_card, markdown = card("个人计划资料目录",
        "这里保留旧计划 Markdown、每日历史记录和资源文档。新的行动、计划与复盘保存在 SQLite；知识库使用单独的 Vault 路径。", "▱", 1)
    path_row = QHBoxLayout()
    path_row.addWidget(QLabel("当前目录"))
    window.workbench_location = QLineEdit(str(work_root(window.store)))
    window.workbench_location.setReadOnly(True)
    path_row.addWidget(window.workbench_location, 1)
    path_row.addWidget(button("更改目录…", window.change_workbench_location))
    markdown.addLayout(path_row)
    path_hint = QLabel("选择后立即生效；更换位置不会搬移旧资料。旧计划文档只在首次迁移时读取，原文件保留。")
    path_hint.setObjectName("Subtitle")
    path_hint.setWordWrap(True)
    markdown.addWidget(path_hint)
    left_layout.addWidget(markdown_card)

    obsidian_card, obsidian = card("知识库与 Obsidian Vault",
        "知识库正文与附件保留在 Vault 文件中；SQLite 保存可重建索引、资源元数据、外部引用和合集登记。旧登记只迁入一次并保留原件。项目笔记仍通过 Obsidian 打开。", "▱", 1)
    obsidian_row = QHBoxLayout()
    obsidian_row.addWidget(QLabel("知识库根目录"))
    from ..knowledge import knowledge_root
    default_vault = knowledge_root(window.store)
    window.obsidian_vault_root = QLineEdit(str(window.service.setting("obsidian_vault_root", "") or default_vault or ""))
    window.obsidian_vault_root.setPlaceholderText("例如 D:\\Notes\\my-vault")
    obsidian_row.addWidget(window.obsidian_vault_root, 1)
    obsidian_row.addWidget(button("浏览…", window.change_obsidian_vault_root))
    obsidian.addLayout(obsidian_row)
    obsidian_actions = QHBoxLayout()
    obsidian_actions.addWidget(button("保存 Obsidian 路径", window.save_obsidian_vault_root, True))
    obsidian_actions.addStretch(1)
    obsidian.addLayout(obsidian_actions)
    window.settings_obsidian_status = QLabel()
    window.settings_obsidian_status.setObjectName("Subtitle")
    window.settings_obsidian_status.setWordWrap(True)
    obsidian.addWidget(window.settings_obsidian_status)
    left_layout.addWidget(obsidian_card)

    backup_card, backup = card("统一备份位置",
        "文件夹和项目的压缩加密备份统一保存到这里。可选择本机网盘客户端的同步目录；网盘同步由客户端负责。", "▤", 2)
    backup_row = QHBoxLayout()
    backup_row.addWidget(QLabel("当前目录"))
    window.backup_path = QLineEdit(str(window.service.backup_root()))
    window.backup_path.setPlaceholderText("输入备份目录的完整路径")
    backup_row.addWidget(window.backup_path, 1)
    backup_row.addWidget(button("浏览…", window.change_backup_location))
    backup.addLayout(backup_row)
    backup_actions = QHBoxLayout()
    backup_actions.addWidget(button("打开备份文件夹", lambda: window.open_path(window.service.backup_root())))
    backup_actions.addWidget(button("保存备份目录", window.save_settings, True))
    backup_actions.addStretch(1)
    backup.addLayout(backup_actions)
    backup_hint = QLabel("更改位置只影响之后创建的管家归档；旧备份不会自动搬移。请选择本机可写目录。")
    backup_hint.setObjectName("Subtitle")
    backup_hint.setWordWrap(True)
    backup.addWidget(backup_hint)
    window.settings_backup_feedback = QLabel()
    window.settings_backup_feedback.setObjectName("Subtitle")
    window.settings_backup_feedback.setWordWrap(True)
    backup.addWidget(window.settings_backup_feedback)
    # Kept as a compatibility field for the existing save handler.
    window.backup_location = QTextEdit(str(window.service.backup_root()))
    window.backup_location.setReadOnly(True)
    window.backup_location.hide()
    left_layout.addWidget(backup_card)

    password_card, password = card("统一备份密码",
        "用于管家的加密 .amb 备份，保存在系统凭据库；便携模式保存在已解锁的加密口令库。", "▣", 2)
    window.common_password_status = QLabel()
    window.common_password_status.setWordWrap(True)
    password.addWidget(window.common_password_status)
    password_view_row = QHBoxLayout()
    window.common_password_view = QLineEdit()
    window.common_password_view.setReadOnly(True)
    window.common_password_view.setPlaceholderText("尚未保存统一备份口令")
    window.common_password_view.setEchoMode(QLineEdit.EchoMode.Password)
    password_view_row.addWidget(window.common_password_view, 1)
    window.common_password_reveal = button("显示", lambda: None)
    window.common_password_reveal.setCheckable(True)
    window.common_password_reveal.toggled.connect(window.toggle_common_password_visibility)
    password_view_row.addWidget(window.common_password_reveal)
    password.addLayout(password_view_row)
    password_actions = QHBoxLayout()
    password_actions.addWidget(button("输入 / 更换密码", window.set_common_password, True))
    password_actions.addWidget(button("移除已保存口令", window.forget_common_password))
    password_actions.addStretch(1)
    password.addLayout(password_actions)
    left_layout.addWidget(password_card)

    git_card, git_content = card("Git HTTPS 访问令牌",
        "用于 GitHub / GitLab 等 HTTPS 仓库的拉取和推送。令牌保存在系统凭据库；便携模式保存在加密口令库。SSH 仓库继续使用本机 SSH 密钥或 ssh-agent。", "▣", 3)
    window.git_token_status = QLabel()
    window.git_token_status.setWordWrap(True)
    window.git_token_status.setObjectName("Subtitle")
    git_content.addWidget(window.git_token_status)
    git_actions = QHBoxLayout()
    git_actions.addWidget(button("设置 / 更换令牌", window.set_git_token, True))
    git_actions.addWidget(button("移除已保存令牌", window.remove_git_token))
    git_actions.addStretch(1)
    git_content.addLayout(git_actions)
    left_layout.addWidget(git_card)

    options_card, options = card("自动备份与维护",
        "调整自动备份版本数量，或导入、导出本机配置。", "⚙", 3)
    window.settings_advanced_toggle = QToolButton()
    window.settings_advanced_toggle.setText("自动备份最多保留")
    window.settings_advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    window.settings_advanced_toggle.setCheckable(True)
    window.settings_advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
    options.addWidget(window.settings_advanced_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
    window.settings_advanced = QWidget()
    advanced = QVBoxLayout(window.settings_advanced)
    advanced.setContentsMargins(8, 0, 8, 4)
    advanced.setSpacing(8)
    form = QFormLayout()
    window.backup_keep = QSpinBox()
    window.backup_keep.setRange(1, 100)
    window.backup_keep.setValue(backup_retention_count(window.store))
    window.backup_keep.setSuffix(" 份")
    form.addRow("每个资源", window.backup_keep)
    advanced.addLayout(form)
    advanced.addWidget(button("保存保留数量", window.save_backup_retention), alignment=Qt.AlignmentFlag.AlignLeft)
    automatic = QLabel("仅在自动备份成功后按规则清理旧版本；在“数据安全”的对应登记中开启自动备份。")
    automatic.setWordWrap(True)
    automatic.setObjectName("Subtitle")
    advanced.addWidget(automatic)
    options.addWidget(window.settings_advanced)
    window.settings_advanced.hide()
    window.settings_advanced_toggle.toggled.connect(window.toggle_settings_advanced)
    options.addWidget(button("电脑维护与配置迁移…", window.maintenance_tools), alignment=Qt.AlignmentFlag.AlignLeft)
    left_layout.addWidget(options_card)
    status_card, status = card("当前状态", "查看已保存的位置与备份口令状态。", "✓")
    window.settings_mode_status = QLabel()
    window.settings_data_status = QLabel()
    window.settings_backup_status = QLabel()
    window.settings_password_status = QLabel()
    for widget in (window.settings_mode_status, window.settings_data_status,
                   window.settings_backup_status, window.settings_password_status):
        widget.setObjectName("SettingsStatusTile")
        widget.setWordWrap(True)
        status.addWidget(widget)
    left_layout.insertWidget(1, status_card)
    tips_card, tips = card("提示", "", "i")
    tips_text = QLabel("资料目录可与 Obsidian 共用。\n\n更换资料或备份位置不会迁移已有文件。\n\n换电脑时，请带上备份文件并记住对应密码。")
    tips_text.setWordWrap(True)
    tips_text.setObjectName("Subtitle")
    tips.addWidget(tips_text)
    left_layout.addWidget(tips_card)
    left_layout.addStretch(1)
    left_scroll.setWidget(left_body)
    layout.addWidget(left_scroll, 1)

    def select_group(index):
        for group, cards in groups.items():
            for frame in cards:
                frame.setVisible(group == index)
        left_scroll.verticalScrollBar().setValue(0)
    window.settings_tabs.currentChanged.connect(select_group)
    select_group(0)

    def update_status(*_):
        data_root = Path(window.workbench_location.text()).expanduser()
        backup_root = window.service.backup_root()
        portable = bool(window.store.portable_root)
        window.settings_mode_status.setText("运行模式\n" + ("便携模式" if portable else "标准模式") +
            (" · 数据随程序目录保存" if portable else " · 数据保存在当前电脑"))
        window.settings_data_status.setText("计划文档目录\n" + ("已连接" if data_root.is_dir() else
            "目录需要检查" if data_root.exists() else "首次保存时创建") + "\n" + str(data_root))
        backup_state = "正常" if backup_root.is_dir() else "首次备份时创建"
        window.settings_backup_status.setText("备份目录\n" + backup_state + "\n" + str(backup_root))
        draft = window.backup_path.text().strip().strip('"')
        try:
            pending = bool(draft) and Path(draft).expanduser().resolve() != backup_root.resolve()
        except (OSError, ValueError):
            pending = True
        window.settings_backup_feedback.setText("目录尚未保存；点击“保存备份目录”后生效。" if pending else
            "请输入备份目录。" if not draft else "当前使用已保存的备份目录。")
        for field in (window.workbench_location, window.backup_path, window.obsidian_vault_root):
            field.setToolTip(field.text())
        has_password = bool(window.service.common_password)
        window.settings_password_status.setText("备份口令\n" + ("已保存" if has_password else "尚未保存；首次备份时可设置"))
        configured_vault = str(window.service.setting("obsidian_vault_root", "") or "")
        saved_vault = str(configured_vault or knowledge_root(window.store) or "")
        obsidian_root = Path(saved_vault).expanduser() if saved_vault else None
        if not obsidian_root:
            obsidian_state = "尚未设置"
        elif obsidian_root.is_dir() and (obsidian_root / ".obsidian").is_dir():
            obsidian_state = "已连接"
        else:
            obsidian_state = "目录需要检查"
        window.settings_obsidian_status.setText("Obsidian 知识库\n" + obsidian_state +
                                                ("\n" + str(obsidian_root) if obsidian_root else "") +
            ("\n输入内容尚未保存；点击“保存 Obsidian 路径”后生效。"
             if window.obsidian_vault_root.text().strip().strip('"') not in {configured_vault, saved_vault} else ""))

    window.settings_status_update = update_status
    window.workbench_location.textChanged.connect(update_status)
    window.backup_path.textChanged.connect(update_status)
    window.obsidian_vault_root.textChanged.connect(update_status)
    window.backup_location.setText(str(window.service.backup_root()))
    window.refresh_common_password()
    window.refresh_git_token_status()
    update_status()
    window.stack.addWidget(page)
