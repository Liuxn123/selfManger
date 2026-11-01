from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView


class MigrationChecklist(QWidget):
    steps = ("备份文件", "密码与解密", "文件完整性", "恢复位置", "实际恢复")

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("换电脑任务清单 · 在上方填写备份文件路径和本次口令，解锁后再输入恢复目标")
        label.setWordWrap(True)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        self.table = QTableWidget(len(self.steps), 2)
        self.table.setHorizontalHeaderLabels(["需要什么", "检查结果 / 下一步"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setFixedHeight(228)
        layout.addWidget(self.table)
        for index, name in enumerate(self.steps):
            self.table.setItem(index, 0, QTableWidgetItem(name))
        self.reset()
        self.app_ready = QCheckBox("原应用已安装；恢复后仍需核对登录和资料路径")
        layout.addWidget(self.app_ready)
        self.continue_note = QLabel("资料恢复完成后，请核对恢复位置和资料内容。")
        self.continue_note.setWordWrap(True)
        layout.addWidget(self.continue_note)

    def reset(self):
        for index, text in enumerate(("待提供 .amb 文件", "待输入备份原密码", "解密后自动检查每个文件", "待选择新目录 / 空目录", "全部检查通过后，确认执行")):
            self.set_step(index, text)

    def set_step(self, index, text):
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self.table.setItem(index, 1, item)
