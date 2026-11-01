from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ..domain import Cancelled, Operation, RestorePlan, UserError
from ..runtime import TaskContext
from ..security import safe_result


class TaskSignals(QObject):
    finished = Signal(str, object, str)


class ReadSignals(QObject):
    finished = Signal(object)


class ReadWorker(QRunnable):
    """A single background observation, without changing task history."""
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancel = threading.Event()
        self.signals = ReadSignals()

    @Slot()
    def run(self):
        try:
            outcome = self.operation(self.cancel)
        except Exception:
            outcome = None
        self.signals.finished.emit(outcome)


class Worker(QRunnable):
    def __init__(self, identity: str, service, operation: Operation, persist_result: bool = True,
                 *, record_history: bool = True) -> None:
        super().__init__()
        self.identity, self.service, self.operation = identity, service, operation
        self.persist_result = persist_result
        self.record_history = record_history
        self.signals = TaskSignals()
        self.context = TaskContext((lambda message: service.append_log(identity, message)) if record_history else (lambda message: None))

    def save_result(self, state, report):
        if self.record_history:
            self.service.finish_task(self.identity, state, report)

    @Slot()
    def run(self) -> None:
        outcome = {"error": "操作未完成，请检查任务记录。"}
        state = "failed"
        operation_finished = False
        try:
            self.context.log("任务已开始。")
            outcome = self.operation(self.context)
            operation_finished = True
            report = outcome.summary if isinstance(outcome, RestorePlan) else outcome
            state = "success"
            self.save_result(state, safe_result(report) if self.persist_result else {"note": "内容仅在窗口显示，没有复制到操作记录。"})
            self.context.log("任务完成。")
        except Cancelled as exc:
            state = "cancelled"
            report = {"error": ("操作已执行，但结果或日志保存失败，请核对实际状态后重试。"
                                if operation_finished else str(exc))}
            outcome = report
            self.report_failure(state, report)
        except UserError as exc:
            state = "failed"
            report = {"error": ("操作已执行，但结果或日志保存失败，请核对实际状态后重试。"
                                if operation_finished else str(exc))}
            outcome = report
            self.report_failure(state, safe_result(report))
        except Exception as exc:
            state = "failed"
            # Never persist arbitrary exception values from external libraries.
            report = {"error": ("操作已执行，但结果或日志保存失败，请核对实际状态后重试。"
                                if operation_finished else "操作未完成，请检查配置、文件权限和依赖。"),
                      "error_type": type(exc).__name__}
            outcome = report
            self.report_failure(state, report)
        finally:
            self.signals.finished.emit(self.identity, outcome, state)

    def report_failure(self, state, report):
        """Always deliver a terminal signal even if history storage is unavailable."""
        try:
            self.save_result(state, report)
            self.context.log(report["error"])
        except Exception as exc:
            # Do not persist arbitrary exception values or retry the same broken
            # database indefinitely. The in-memory result reaches the window.
            report["history_error_type"] = type(exc).__name__
