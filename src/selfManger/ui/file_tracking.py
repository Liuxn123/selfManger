from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..file_tracking import FileTrackingService, MAX_HISTORY
from .tasks import ReadWorker
from .window import button, card, table

class FileTrackingPage(QWidget):
    """Local file change tracking based on path, size and modification time."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.store = window.store
        self.service = FileTrackingService(self.store)
        saved = self.service.load()
        self.roots: list[dict] = saved.get("roots", [])
        self.events: list[dict] = saved.get("events", [])
        self.enabled = bool(saved.get("enabled", True))
        self._workers: set[ReadWorker] = set()
        self._busy = False
        self._scan_error = False
        self._initializing: set[str] = set()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._schedule_scan)
        self.scan_timer = QTimer(self)
        self.scan_timer.setSingleShot(True)
        self.scan_timer.setInterval(1200)
        self.scan_timer.timeout.connect(self.scan)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(120_000)
        self.poll_timer.timeout.connect(self.scan)
        if self.enabled:
            self.poll_timer.start()
        self._build_ui()
        self._restore_watches()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("数据安全")
        title.setObjectName("Title")
        subtitle = QLabel("追踪本机选定目录中的文件新增、修改与删除；只读取文件名、大小和修改时间，不读取正文。")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.metric_values = []
        for heading, initial, note in (
            ("追踪目录", "0", "你选择的本机目录"),
            ("索引文件", "0", "仅文件元数据"),
            ("今日变更", "0", "新增与修改"),
            ("追踪状态", "已开启", "目录变更后自动扫描"),
        ):
            panel, value, detail = card(heading, initial, note)
            panel.setObjectName("SafetyMetric")
            value.setObjectName("SafetyMetricValue")
            detail.setObjectName("SafetyMetricNote")
            self.metric_values.append((value, detail))
            metrics.addWidget(panel, 1)
        layout.addLayout(metrics)

        body = QGridLayout()
        body.setHorizontalSpacing(10)
        body.setVerticalSpacing(10)
        roots_card = self._panel("追踪目录", "添加你希望观察的项目资料或工作目录。")
        roots_layout = roots_card.layout()
        root_actions = QHBoxLayout()
        self.add_button = button("＋ 添加目录", self.add_root, True)
        self.remove_button = button("停止追踪", self.remove_root)
        self.open_button = button("打开目录", self.open_root)
        self.scan_button = button("立即扫描", self.scan)
        self.toggle_button = button("暂停追踪" if self.enabled else "继续追踪", self.toggle_tracking)
        for control in (self.add_button, self.remove_button, self.open_button, self.scan_button, self.toggle_button):
            root_actions.addWidget(control)
        root_actions.addStretch(1)
        roots_layout.addLayout(root_actions)
        self.roots_table = table(["目录名称", "本地路径", "文件数", "追踪状态"])
        self.roots_table.setObjectName("SafetyRecentTable")
        self.roots_table.setMinimumHeight(175)
        self.roots_table.itemSelectionChanged.connect(self._update_buttons)
        self.roots_table.doubleClicked.connect(lambda *_: self.open_root())
        self.roots_empty = QLabel("还没有追踪目录。添加后会先记录当前文件状态。")
        self.roots_empty.setObjectName("EmptyState")
        self.roots_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        roots_layout.addWidget(self.roots_empty)
        roots_layout.addWidget(self.roots_table, 1)
        body.addWidget(roots_card, 0, 0)

        changes_card = self._panel("最近文件变更", "通过文件系统通知和定时扫描发现；新登记目录只建立基线，不会回填旧变更。")
        changes_layout = changes_card.layout()
        self.changes_table = table(["时间", "变更", "文件", "目录"])
        self.changes_table.setObjectName("SafetyRecentTable")
        self.changes_table.setMinimumHeight(250)
        self.changes_table.setColumnWidth(0, 145)
        self.changes_table.setColumnWidth(1, 80)
        self.changes_table.setColumnWidth(2, 260)
        self.changes_table.doubleClicked.connect(self.open_changed_file)
        self.changes_empty = QLabel("暂无文件变更记录。追踪开启后，新增、修改和删除会显示在这里。")
        self.changes_empty.setObjectName("EmptyState")
        self.changes_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        changes_layout.addWidget(self.changes_empty, 1)
        changes_layout.addWidget(self.changes_table, 1)
        body.addWidget(changes_card, 1, 0)

        side = self._panel("追踪说明", "不包含备份、同步、加密或恢复操作。")
        side_layout = side.layout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        side_layout.addWidget(self.status_label)
        self.notice_label = QLabel("记录保存在selfManger的本机设置中。目录移除只会停止追踪，不会删除任何文件。")
        self.notice_label.setObjectName("Subtitle")
        self.notice_label.setWordWrap(True)
        side_layout.addWidget(self.notice_label)
        side_layout.addStretch(1)
        body.addWidget(side, 0, 1, 2, 1)
        body.setColumnStretch(0, 7)
        body.setColumnStretch(1, 3)
        body.setRowStretch(0, 2)
        body.setRowStretch(1, 3)
        layout.addLayout(body, 1)

    @staticmethod
    def _panel(title: str, note: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("SafetyControlCard")
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 12, 14, 14)
        box.setSpacing(8)
        heading = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        heading.addWidget(label, 1)
        box.addLayout(heading)
        hint = QLabel(note)
        hint.setObjectName("SafetyMetricNote")
        hint.setWordWrap(True)
        box.addWidget(hint)
        return panel

    def _save(self) -> None:
        self.service.save(self.roots, self.events, self.enabled)

    def _restore_watches(self) -> None:
        self.watcher.removePaths(self.watcher.directories())
        if not self.enabled:
            return
        paths = []
        for root in self.roots:
            snapshot = root.get("snapshot", {})
            paths.extend({str(Path(path).parent) for path in snapshot})
            paths.append(root.get("path", ""))
        for path in dict.fromkeys(paths):
            if Path(path).is_dir() and path not in self.watcher.directories():
                self.watcher.addPath(path)

    def _update_buttons(self) -> None:
        selected = self.roots_table.currentRow() >= 0
        self.remove_button.setEnabled(selected and not self._busy)
        self.open_button.setEnabled(selected)
        self.scan_button.setEnabled(bool(self.roots) and not self._busy)
        self.add_button.setEnabled(not self._busy)

    def add_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要追踪的目录")
        if not path:
            return
        canonical = str(Path(path).resolve())
        if any(str(Path(item.get("path", "")).resolve()).casefold() == canonical.casefold() for item in self.roots):
            QMessageBox.information(self, "已在追踪", "这个目录已经登记。")
            return
        root = {"id": uuid4().hex, "name": Path(canonical).name or canonical, "path": canonical,
                "snapshot": {}, "file_count": 0, "initialized": False}
        self.roots.append(root)
        self._initializing.add(root["id"])
        self._save()
        self.refresh()
        self._scan_root(root, baseline=True)

    def remove_root(self) -> None:
        row = self.roots_table.currentRow()
        if row < 0 or row >= len(self.roots):
            return
        item = self.roots.pop(row)
        self._initializing.discard(item["id"])
        self._save()
        self._restore_watches()
        self.refresh()

    def open_root(self) -> None:
        row = self.roots_table.currentRow()
        if 0 <= row < len(self.roots):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.roots[row]["path"]))

    def open_changed_file(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self.events):
            path = Path(self.events[row].get("path", ""))
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            elif path.parent.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def toggle_tracking(self) -> None:
        self.enabled = not self.enabled
        self.toggle_button.setText("暂停追踪" if self.enabled else "继续追踪")
        if self.enabled:
            self.poll_timer.start()
            self._restore_watches()
            self.scan()
        else:
            self.poll_timer.stop()
            self.scan_timer.stop()
            self.watcher.removePaths(self.watcher.directories())
        self._save()
        self.refresh()

    def _schedule_scan(self, _path: str = "") -> None:
        if self.enabled and self.roots:
            self.scan_timer.start()

    def scan(self) -> None:
        if self._busy or not self.enabled:
            return
        roots = list(self.roots)
        if not roots:
            self.refresh()
            return
        self._busy = True
        self._scan_error = False
        self.refresh()
        worker = ReadWorker(lambda cancel: [(root["id"], root["path"], root.get("snapshot", {}), *self.service.snapshot(root["path"], cancel)) for root in roots])
        self._workers.add(worker)
        worker.signals.finished.connect(lambda result, current=worker: self._scan_finished(current, result))
        self.window.pool.start(worker)

    def _scan_root(self, root: dict, baseline: bool) -> None:
        if self._busy:
            self.scan_timer.start()
            return
        self._busy = True
        self._update_buttons()
        worker = ReadWorker(lambda cancel: (root["id"], root["path"], root.get("snapshot", {}), *self.service.snapshot(root["path"], cancel)))
        self._workers.add(worker)
        worker.signals.finished.connect(lambda result, current=worker, initial=baseline: self._single_scan_finished(current, result, initial))
        self.window.pool.start(worker)

    def _single_scan_finished(self, worker: ReadWorker, result, baseline: bool) -> None:
        self._workers.discard(worker)
        self._busy = False
        if result:
            self._apply_scan([result], initial=baseline)
        else:
            self._scan_error = True
            self.refresh()
        self._update_buttons()

    def _scan_finished(self, worker: ReadWorker, result) -> None:
        self._workers.discard(worker)
        self._busy = False
        if result:
            self._apply_scan(result)
        else:
            self._scan_error = True
            self.refresh()
        self._update_buttons()
        if self.scan_timer.isActive():
            self.scan_timer.stop()

    def _apply_scan(self, results: list, initial: bool = False) -> None:
        current_by_id = {root["id"]: root for root in self.roots}
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for identity, path, previous, snapshot, directories, truncated in results:
            root = current_by_id.get(identity)
            if root is None or root.get("path") != path or not Path(path).is_dir():
                continue
            is_initial = initial or identity in self._initializing or not root.get("initialized", False)
            self._initializing.discard(identity)
            # A partial scan must never produce false deletion events.
            if not is_initial and not truncated:
                for action, file_path in self.service.changes(previous, snapshot):
                    self.events.insert(0, self._event(identity, root.get("name", ""), now, action, file_path))
            if not truncated or is_initial:
                root["snapshot"] = snapshot
                root["file_count"] = len(snapshot)
            if is_initial:
                root["initialized"] = True
            root["truncated"] = bool(truncated)
            if self.enabled:
                for directory in directories:
                    if directory not in self.watcher.directories():
                        self.watcher.addPath(directory)
        self.events = self.events[:MAX_HISTORY]
        self._save()
        self.refresh()

    @staticmethod
    def _event(root_id: str, root_name: str, timestamp: str, action: str, path: str) -> dict:
        return {"root_id": root_id, "root_name": root_name, "time": timestamp, "action": action, "path": path}

    def refresh(self) -> None:
        self.roots_table.setRowCount(len(self.roots))
        for row, root in enumerate(self.roots):
            values = (root.get("name", "目录"), root.get("path", ""), str(root.get("file_count", 0)),
                      "扫描中" if root["id"] in self._initializing else ("监控中" if self.enabled else "已暂停"))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setToolTip(value)
                self.roots_table.setItem(row, col, item)
        self.changes_table.setRowCount(len(self.events))
        name_by_id = {root["id"]: root.get("name", "") for root in self.roots}
        for row, event in enumerate(self.events):
            path = event.get("path", "")
            timestamp = event.get("time", "")
            values = (timestamp.replace("T", " ")[:19], event.get("action", ""), Path(path).name,
                      name_by_id.get(event.get("root_id"), event.get("root_name", "")))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2:
                    item.setToolTip(path)
                self.changes_table.setItem(row, col, item)
        self.roots_empty.setVisible(not self.roots)
        self.roots_table.setVisible(bool(self.roots))
        self.changes_empty.setVisible(not self.events)
        self.changes_table.setVisible(bool(self.events))
        today = datetime.now().astimezone().date().isoformat()
        changed_today = sum(1 for event in self.events if event.get("time", "").startswith(today) and event.get("action") != "删除")
        count = sum(int(root.get("file_count", 0)) for root in self.roots)
        self.metric_values[0][0].setText(str(len(self.roots)))
        self.metric_values[1][0].setText(f"{count:,}" + ("+" if any(r.get("truncated") for r in self.roots) else ""))
        self.metric_values[2][0].setText(str(changed_today))
        self.metric_values[3][0].setText("已开启" if self.enabled else "已暂停")
        self.status_label.setText(
            f"当前追踪 {len(self.roots)} 个目录、{count:,} 个文件。\n\n"
            + ("最近一次扫描未完成，旧快照已保留。请检查目录权限后点击‘立即扫描’重试。" if self._scan_error else
               "正在扫描目录变更。" if self._busy else "文件变化通过系统通知及每两分钟一次的元数据扫描发现。")
            + ("\n部分扫描不完整：可能有文件/目录无法访问，或单目录超过 30,000 个文件。此类扫描不会据此记录文件删除。"
               if any(r.get("truncated") for r in self.roots) else "")
        )
        self._update_buttons()

    def stop_updates(self) -> None:
        self.scan_timer.stop()
        self.poll_timer.stop()
        self.watcher.removePaths(self.watcher.directories())
        for worker in self._workers:
            worker.cancel.set()
