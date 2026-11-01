"""User-facing backup health and safe restore entry points."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QScrollArea, QSizePolicy,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..domain import Resource, UserError
from ..application import MANAGER_BACKUP_ID, OVERALL_BACKUP_ID
from .tasks import ReadWorker
from .components import FlowLayout, nav_icon


BACKUP_KINDS = ("project", "folder", "script", "vault")
HEALTHY_STATES = {"已校验", "已演练"}


class SafetyPage(QWidget):
    """A compact protection overview plus a guided entry to existing restore flows."""

    def __init__(self, window) -> None:
        super().__init__()
        self.setObjectName("SafetyPage")
        self.window = window
        self.service = window.service
        self.entries: list[dict] = []
        self.preferred_resource_id = ""
        self._worker: ReadWorker | None = None
        self._closed = False
        self._pending_refresh = False
        self._build_ui()
        self.refresh()

    @staticmethod
    def _label(text: str, name: str = "Subtitle") -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _panel(title: str = ""):
        frame = QFrame()
        frame.setObjectName("SafetyDetailCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        if title:
            layout.addWidget(SafetyPage._label(title, "SectionTitle"))
        return frame, layout

    def _build_ui(self) -> None:
        from .window import button

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(self._label("数据安全", "Title"))
        titles.addWidget(self._label("管理资料保护状态，查看备份并找回需要的版本。"))
        header.addLayout(titles, 1)
        self.refresh_button = button("刷新状态", self.refresh)
        self.add_button = button("＋ 添加资料", self._add_menu, True)
        header.addWidget(self.refresh_button)
        header.addWidget(self.add_button)
        outer.addLayout(header)
        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setObjectName("SafetyWorkspace")
        self.workspace.setChildrenCollapsible(False)
        registry, registry_layout = self._panel()
        registry.setObjectName("SafetyRegistry")
        registry.setMinimumWidth(200)
        registry.setMaximumWidth(300)
        registry_heading = QHBoxLayout()
        registry_heading.addWidget(self._label("我的资料", "SectionTitle"), 1)
        self.resource_count = self._label("0/0", "SafetyCount")
        registry_heading.addWidget(self.resource_count)
        registry_layout.addLayout(registry_heading)
        self.registry_summary = self._label("正在读取保护状态…")
        registry_layout.addWidget(self.registry_summary)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索资料名称…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render_list)
        registry_layout.addWidget(self.search)

        self.resources_list = QListWidget()
        self.resources_list.setObjectName("SafetyRegistryList")
        self.resources_list.setIconSize(QSize(24, 24))
        self.resources_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.resources_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.resources_list.currentItemChanged.connect(self._render_selected)
        registry_layout.addWidget(self.resources_list, 1)
        self.empty_state = self._label("尚未登记可备份的资料，点击右上角添加。")
        registry_layout.addWidget(self.empty_state)
        registry_layout.addWidget(self._label("只显示已登记且支持备份的资料。"))
        self.workspace.addWidget(registry)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SafetyDetailBody")
        detail = QVBoxLayout(body)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(12)
        self.hero, hero = self._panel()
        self.hero.setObjectName("SafetyHero")
        self.detail_name = self._label("选择一项资料", "SafetyDetailName")
        self.detail_kind = self._label("从左侧选择资料，查看保护状态和备份版本。")
        hero.addWidget(self.detail_name)
        hero.addWidget(self.detail_kind)
        hero_actions = FlowLayout()
        self.primary_button = button("立即备份", self._run_primary_action, True)
        self.restore_button = button("查看备份版本", self.show_restore)
        self.edit_button = button("登记设置", self._edit_selected)
        self.location_button = button("打开资料位置", self._open_selected)
        self.remove_button = button("移除登记", self._remove_selected)
        self.remove_button.setProperty("danger", True)
        for widget in (self.primary_button, self.restore_button, self.edit_button,
                       self.location_button, self.remove_button):
            hero_actions.addWidget(widget)
        hero.addLayout(hero_actions)
        detail.addWidget(self.hero)

        self.views = QTabWidget()
        self.views.setObjectName("SafetyDetailTabs")
        self.views.setDocumentMode(True)
        self.views.setMinimumHeight(420)
        self.views.currentChanged.connect(lambda _: self.detail_scroll.verticalScrollBar().setValue(0))
        overview = QWidget()
        layout = QVBoxLayout(overview)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)
        self.attention, attention_layout = self._panel()
        self.attention.setObjectName("SafetyAttention")
        self.attention_title = self._label("尚未选择资料", "SafetyHealthState")
        self.attention_detail = self._label("选择资料后显示下一步建议。")
        attention_layout.addWidget(self.attention_title)
        attention_layout.addWidget(self.attention_detail)
        layout.addWidget(self.attention)

        metrics = QGridLayout()
        metrics.setSpacing(10)
        self.summary = []
        for index, title in enumerate(("最近备份", "内容校验", "恢复演练", "可见备份版本")):
            frame, metric = self._panel()
            frame.setObjectName("SafetyMetric")
            metric.addWidget(self._label(title, "SafetyMetricNote"))
            value = self._label("—", "SafetyMetricValue")
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            metric.addWidget(value)
            metrics.addWidget(frame, index // 2, index % 2)
            self.summary.append(value)
        metrics.setColumnStretch(0, 1)
        metrics.setColumnStretch(1, 1)
        layout.addLayout(metrics)
        location_card, locations = self._panel("资料与备份位置")
        self.source_path = self._label("")
        self.backup_path = self._label("")
        for label in (self.source_path, self.backup_path):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            locations.addWidget(label)
        layout.addWidget(location_card)
        layout.addStretch(1)
        self.views.addTab(overview, "保护概览")

        restore = QWidget()
        restore_layout = QVBoxLayout(restore)
        restore_layout.setContentsMargins(0, 12, 0, 0)
        restore_layout.setSpacing(12)
        restore_layout.addWidget(self._label("选择备份版本", "SectionTitle"))
        self.version_caption = self._label("版本与左侧所选资料对应。")
        restore_layout.addWidget(self.version_caption)
        self.version_list = QListWidget()
        self.version_list.setObjectName("SafetyVersionList")
        self.version_list.setWordWrap(True)
        self.version_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.version_list.currentItemChanged.connect(self._version_selected)
        restore_layout.addWidget(self.version_list, 1)
        note_frame, note_layout = self._panel()
        self.restore_note = self._label("继续后会先检查备份内容并展示恢复目标。")
        note_layout.addWidget(self.restore_note)
        restore_layout.addWidget(note_frame)
        restore_actions = FlowLayout()
        self.continue_restore = button("继续恢复检查  ›", self._continue_restore, True)
        self.rehearse_version = button("演练所选备份", self._rehearse_selected_version)
        self.delete_version = button("删除所选版本", self._delete_selected_version)
        self.delete_version.setProperty("danger", True)
        restore_actions.addWidget(self.continue_restore)
        restore_actions.addWidget(self.rehearse_version)
        restore_actions.addWidget(self.delete_version)
        restore_layout.addLayout(restore_actions)
        self.views.addTab(restore, "备份版本")
        detail.addWidget(self.views, 1)
        detail.addWidget(self._label("仅覆盖登记范围和可读取的备份；云端独有内容与应用账号不在此范围内。"))
        self.detail_scroll.setWidget(body)
        self.workspace.addWidget(self.detail_scroll)
        self.workspace.setStretchFactor(1, 1)
        self.workspace.setSizes([250, 900])
        outer.addWidget(self.workspace, 1)
        self._render_selected()

    def _add_menu(self) -> None:
        menu = QMenu(self)
        titles = (
            ("本地文件夹", "folder"), ("脚本备份", "script"),
            ("本地项目", "project"), ("Obsidian 资料库", "vault"),
        )
        for title, kind in titles:
            action = menu.addAction(title)
            action.triggered.connect(lambda _checked=False, kind=kind: self.window.add_resource(kind))
        menu.exec(self.add_button.mapToGlobal(self.add_button.rect().bottomLeft()))

    def refresh(self) -> None:
        if self._closed:
            return
        if self._worker is not None:
            self._pending_refresh = True
            return
        self.refresh_button.setEnabled(False)
        resources = [resource for resource in self.window.service.backup_resources()
                     if resource.kind in BACKUP_KINDS
                     and "backup" in self.window.service.registry.get(resource).capabilities]
        backup_root = self.window.service.backup_root()

        def collect(cancel):
            entries = []
            for resource in resources:
                if cancel.is_set():
                    return None
                try:
                    inventory = self.service.backup_inventory(resource, backup_root)
                    health, versions = inventory["health"], inventory["versions"]
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    health = {"state": "暂时无法读取", "created_at": None, "source": "",
                              "verified": False, "rehearsed": False, "error": type(exc).__name__}
                    versions = []
                entries.append({"resource": resource, "health": health, "versions": versions})
            return entries

        worker = ReadWorker(collect)
        self._worker = worker

        def finished(entries):
            self._refresh_finished(entries)

        worker.signals.finished.connect(finished)
        self.window.pool.start(worker)

    def _refresh_finished(self, entries):
        self._worker = None
        self.refresh_button.setEnabled(True)
        if self._closed:
            return
        if entries is not None:
            self.entries = entries
            self._fill_overview()
            if hasattr(self.window, "today_page"):
                self.window.today_page.refresh_module_tiles()
        else:
            self.registry_summary.setText("保护状态读取失败；当前保留上次结果。请检查资料路径和权限后刷新重试。")
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh()

    def _fill_overview(self) -> None:
        needs = sum(entry["health"].get("state") not in HEALTHY_STATES for entry in self.entries)
        self.registry_summary.setText(f"{len(self.entries)} 项资料 · {needs} 项需要处理")
        self.render_list()

    def render_list(self, *_, preferred: str = "") -> None:
        from ..domain import KINDS

        previous = self._selected_entry()
        identity = (preferred or getattr(self, "_pending_resource_id", "")
                    or (previous["resource"].id if previous else self.preferred_resource_id))
        query = self.search.text().strip().casefold()
        self.resources_list.blockSignals(True)
        self.resources_list.clear()
        for entry in self.entries:
            resource, health = entry["resource"], entry["health"]
            if query and query not in resource.name.casefold():
                continue
            item = QListWidgetItem(resource.name + (" · 内置" if resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID} else "") + "\n" + str(health.get("state", "待检查")))
            item.setData(Qt.ItemDataRole.UserRole, resource.id)
            item.setToolTip(f"{resource.name}\n{KINDS.get(resource.kind, resource.kind)} · {health.get('state', '待检查')}")
            item.setIcon(nav_icon(5, "#3979ff"))
            item.setSizeHint(QSize(180, 66))
            self.resources_list.addItem(item)
            if resource.id == identity:
                self.resources_list.setCurrentItem(item)
                self._pending_resource_id = ""
        if self.resources_list.currentRow() < 0 and self.resources_list.count():
            self.resources_list.setCurrentRow(0)
        self.resources_list.blockSignals(False)
        self.resource_count.setText(f"{self.resources_list.count()}/{len(self.entries)}")
        self.empty_state.setVisible(not self.resources_list.count())
        self.empty_state.setText("没有匹配资料，试试调整搜索关键词。" if self.entries else "尚未登记可备份的资料，点击右上角添加。")
        self._render_selected()

    def _selected_entry(self) -> dict | None:
        item = self.resources_list.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else ""
        return next((entry for entry in self.entries if entry["resource"].id == identity), None)

    def _render_selected(self, *_) -> None:
        from .presentation import readable_time
        from ..domain import KINDS

        entry = self._selected_entry()
        identity = entry["resource"].id if entry else ""
        if identity != getattr(self, "_displayed_id", None):
            self._displayed_id = identity
            self.detail_scroll.verticalScrollBar().setValue(0)
        for widget in (self.primary_button, self.restore_button, self.edit_button,
                       self.location_button, self.remove_button):
            widget.setEnabled(bool(entry))
        self.views.setEnabled(bool(entry))
        if not entry:
            self.views.setCurrentIndex(0)
            self.attention.setProperty("attentionState", "empty")
            for widget in (self.attention, self.attention_title):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            self.detail_name.setText("选择一项资料" if self.entries else "从保护第一项资料开始")
            self.detail_kind.setText("从左侧选择资料，查看保护状态与可恢复版本。")
            self.attention_title.setText("尚未选择资料")
            self.attention_detail.setText("添加可备份资料，或在左侧搜索名称。")
            for value in self.summary:
                value.setText("—")
            self.source_path.clear()
            self.backup_path.clear()
            self._fill_versions()
            return
        resource, health = entry["resource"], entry["health"]
        self.preferred_resource_id = resource.id
        busy = self.window.is_busy(resource.id)
        capabilities = self.window.service.registry.get(resource).capabilities
        self.detail_name.setText(resource.name)
        self.detail_kind.setText("软件内置资料 · 不可移除" if resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID} else KINDS.get(resource.kind, resource.kind) + " · 保护状态来自已读取的备份记录")
        primary_label = ("立即备份" if health.get("state") in {"未备份", "已过期", "备份已移走"}
                         else "校验备份" if health.get("state") == "待校验"
                         else "检查状态" if health.get("state") in {"未检测", "快照读取失败", "暂时无法读取"}
                         else "更新备份")
        self.primary_button.setText(primary_label)
        self.primary_button.setEnabled(not busy)
        self.restore_button.setEnabled("restore" in capabilities)
        self.edit_button.setEnabled(not busy and resource.id not in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID})
        self.edit_button.setToolTip("内置资料位置随当前管家数据库确定" if resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID} else "")
        self.location_button.setEnabled("open" in capabilities and not busy)
        self.remove_button.setEnabled(not busy and resource.id not in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID})
        self.remove_button.setToolTip("软件内置资料不可移除" if resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID} else "")
        self._attention(entry)
        if resource.id == MANAGER_BACKUP_ID:
            self.attention_detail.setText(self.attention_detail.text() + "\n备份包含管家数据库；计划、笔记和附件原件需另行备份。")
        elif resource.id == OVERALL_BACKUP_ID:
            self.attention_detail.setText(self.attention_detail.text() + "\n包含数据库（含设置与知识索引）、个人计划和登记项目 Markdown。项目非 Markdown 附件、知识库文件、凭据及系统/依赖目录不包含；遇到其他联接会停止。采集期间请暂停外部编辑；程序写入可能等待，超时会报错。请将备份另存到独立磁盘或云端，并单独保管口令，丢失无法解密。")
        created = health.get("created_at")
        self.summary[0].setText(readable_time(created) if created else "尚无备份")
        self.summary[1].setText("已通过校验" if health.get("verified") else "待校验" if created else "尚无备份")
        self.summary[2].setText("已通过演练" if health.get("rehearsed") else "未演练" if created else "尚无备份")
        self.summary[3].setText(f"{len(entry['versions'])} 个版本")
        options = resource.options
        path = options.get("path") or options.get("script_path") or "未设置"
        if resource.id in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}:
            path = self.service.database_path
        self.source_path.setText("资料位置：" + str(path))
        self.backup_path.setText("备份位置：" + str(health.get("source") or self.window.service.backup_root()))
        self.source_path.setToolTip(self.source_path.text())
        self.backup_path.setToolTip(self.backup_path.text())
        self._fill_versions()

    def _attention(self, entry: dict) -> None:
        health = entry["health"]
        state = str(health.get("state", "待检查"))
        self.attention_title.setText(state)
        details = {
            "未备份": "尚未找到这项资料的备份。点击“立即备份”创建一份副本。",
            "已过期": "最近备份已超过有效时限，建议创建新备份，再完成校验。",
            "备份已移走": "已记录的备份位置不可用，请核对路径或创建新备份。",
            "待校验": "已有备份，尚缺少有效校验证据。先校验内容，再用于恢复。",
            "已校验": "当前备份已有有效的内容校验证据，可在备份版本中继续恢复检查。",
            "已演练": "当前备份已有恢复演练证据，可在备份版本中查看和选择。",
            "暂时无法读取": "保护状态暂时无法读取，请检查登记设置和资料访问权限。",
        }
        self.attention_detail.setText(details.get(state, "请检查当前资料的保护状态。"))
        self.attention.setProperty("attentionState", "healthy" if state in HEALTHY_STATES else "attention")
        self.attention.style().unpolish(self.attention)
        self.attention.style().polish(self.attention)
        self.attention_title.style().unpolish(self.attention_title)
        self.attention_title.style().polish(self.attention_title)

    def _run_primary_action(self) -> None:
        entry = self._selected_entry()
        if entry and not self.window.is_busy(entry["resource"].id):
            self._primary_action(entry["resource"], entry["health"])

    def _edit_selected(self) -> None:
        entry = self._selected_entry()
        if entry and entry["resource"].id not in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}:
            self.window.edit_resource(entry["resource"])

    def _open_selected(self) -> None:
        entry = self._selected_entry()
        if entry and not self.window.is_busy(entry["resource"].id):
            if "open" in self.window.service.registry.get(entry["resource"]).capabilities:
                self.window.perform(entry["resource"], "open")

    def _remove_selected(self) -> None:
        entry = self._selected_entry()
        if entry and entry["resource"].id not in {MANAGER_BACKUP_ID, OVERALL_BACKUP_ID}:
            self.window.remove_resource(entry["resource"])

    def _primary_action(self, resource: Resource, health: dict) -> None:
        state = health.get("state")
        action = ("verify" if state == "待校验" else "observe"
                  if state in {"未检测", "快照读取失败", "暂时无法读取"} else "backup")
        source = health.get("source", "") if action == "verify" else ""
        self.window.perform(resource, action, source)

    def _fill_versions(self, _index: int = -1) -> None:
        from .presentation import readable_size, readable_time

        previous = self.version_list.currentItem()
        previous_source = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        self.version_list.blockSignals(True)
        self.version_list.clear()
        entry = self._selected_entry()
        if not entry:
            self.version_caption.setText("从左侧选择资料后查看对应备份。")
            self.restore_note.setText("继续后会检查备份内容，并让你确认恢复目标。")
            self.version_list.blockSignals(False)
            self._version_selected()
            return
        resource, health = entry["resource"], entry["health"]
        self.version_caption.setText(f"{len(entry['versions'])} 个可见版本 · 选择一项继续检查")
        for version in entry["versions"]:
            source = version.get("archive", "")
            if not source:
                continue
            created = readable_time(version.get("created_at", "未知时间"))
            files, size = version.get("file_count"), version.get("size")
            detail = " · ".join(part for part in (f"{files} 个文件" if files is not None else "", readable_size(size) if size is not None else "") if part)
            label = created + " · " + str(version.get("integrity", "待校验"))
            item = QListWidgetItem(label + ("\n" + detail if detail else ""))
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setData(Qt.ItemDataRole.UserRole + 1, True)
            item.setToolTip(str(source))
            self.version_list.addItem(item)
        if not self.version_list.count():
            item = QListWidgetItem("尚无可用备份版本。请先在保护概览中创建备份或检查状态。")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.version_list.addItem(item)
        else:
            selected = next((i for i in range(self.version_list.count())
                             if self.version_list.item(i).data(Qt.ItemDataRole.UserRole) == previous_source), 0)
            self.version_list.setCurrentRow(selected)
        self.restore_note.setText("继续后输入备份口令、检查备份内容，再选择新文件夹；原资料不会被直接覆盖。")
        if resource.id == OVERALL_BACKUP_ID:
            self.restore_note.setText("先校验并恢复到新目录。数据库将隔离为 manager.sqlite3.recovered，默认数据库阻止启动；提取后请使用“准备可用数据目录…”生成已校验并完成路径调整的新目录。")
        self.version_list.blockSignals(False)
        self._version_selected()

    def _version_selected(self, *_) -> None:
        entry = self._selected_entry()
        item = self.version_list.currentItem()
        available = bool(item and item.data(Qt.ItemDataRole.UserRole + 1))
        supported = bool(entry and "restore" in self.window.service.registry.get(entry["resource"]).capabilities)
        busy = bool(entry and self.window.is_busy(entry["resource"].id))
        self.continue_restore.setEnabled(available and supported and not busy)
        source = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        deletable = bool(entry and source)
        self.rehearse_version.setEnabled(deletable and not busy)
        self.delete_version.setEnabled(deletable and not busy)

    def _rehearse_selected_version(self) -> None:
        entry = self._selected_entry()
        item = self.version_list.currentItem()
        source = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if entry and source and not self.window.is_busy(entry["resource"].id):
            self.window.rehearse_backup(entry["resource"], source)

    def _continue_restore(self) -> None:
        entry = self._selected_entry()
        item = self.version_list.currentItem()
        if not entry or not item or not item.data(Qt.ItemDataRole.UserRole + 1):
            QMessageBox.information(self, "请选择备份", "请选择一个可用的备份版本。")
            return
        resource = entry["resource"]
        if self.window.is_busy(resource.id) or "restore" not in self.window.service.registry.get(resource).capabilities:
            return
        self.window.perform(resource, "restore", item.data(Qt.ItemDataRole.UserRole) or "")

    def _delete_selected_version(self) -> None:
        entry = self._selected_entry()
        item = self.version_list.currentItem()
        source = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if not entry or not source:
            return
        resource = entry["resource"]
        if self.window.is_busy(resource.id):
            return
        from .presentation import readable_time
        version = next((value for value in entry["versions"] if value.get("archive") == source), {})
        created = readable_time(version.get("created_at", "未知时间"))
        answer = QMessageBox.question(self, "删除备份版本",
            f"确定永久删除 {resource.name} 的备份版本（{created}）吗？\n\n只删除此版本的归档和元数据，不影响原始资料及其他版本。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.window.service.delete_backup_version(resource, Path(source))
        except (OSError, UserError) as exc:
            QMessageBox.warning(self, "删除未完成", str(exc))
            return
        self.refresh()

    def _select_resource(self, resource: Resource | None) -> None:
        if not isinstance(resource, Resource):
            return
        self.preferred_resource_id = resource.id
        # A route can arrive while refresh still contains only the old list.
        # Keep that request until its resource appears in a completed snapshot.
        self._pending_resource_id = resource.id
        for control in (self.search,):
            control.blockSignals(True)
        self.search.clear()
        for control in (self.search,):
            control.blockSignals(False)
        self.render_list(preferred=resource.id)

    def show_overview(self, resource: Resource | None = None) -> None:
        self._select_resource(resource)
        self.views.setCurrentIndex(0)

    def show_restore(self, resource: Resource | None = None) -> None:
        self._select_resource(resource)
        self.views.setCurrentIndex(1)

    def stop_updates(self) -> None:
        self._closed = True
        if self._worker is not None:
            self._worker.cancel.set()
