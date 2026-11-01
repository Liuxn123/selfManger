"""Isolated test paths with inherited Windows permissions."""
import os
import stat
import shutil
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def evidence_root(namespace: str | None = None) -> Path:
    root = Path(os.environ.get("SELFMANGER_TEST_ROOT", tempfile.gettempdir())).resolve()
    if namespace:
        root = root / namespace
    root.mkdir(parents=True, exist_ok=True)
    return root


class VisualTimer:
    """Collect opt-in phase timings without changing visual test assertions."""

    def __init__(self, test_id: str):
        self.test_id = test_id
        self.started = time.perf_counter()
        self.samples: dict[str, list[float]] = defaultdict(list)

    @contextmanager
    def measure(self, phase: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.samples[phase].append(time.perf_counter() - started)

    def run(self, phase: str, operation, *args, **kwargs):
        with self.measure(phase):
            return operation(*args, **kwargs)

    def capture(self, widget, path: Path) -> bool:
        image = self.run("grab", widget.grab)
        return self.run("png_save", image.save, str(path))

    def report(self) -> None:
        names = ("construction", "settle", "processEvents", "grab", "png_save")
        values = [f"{name}={sum(self.samples[name]) * 1000:.1f}ms/{len(self.samples[name])}x"
                  for name in names]
        total = (time.perf_counter() - self.started) * 1000
        print(f"VISUAL_TIMING {self.test_id} case={total:.1f}ms " + " ".join(values), flush=True)


def start_visual_timing(test) -> VisualTimer:
    timer = VisualTimer(test.id())
    test._visual_timer = timer
    test.addCleanup(timer.report)
    test.addCleanup(lambda: delattr(test, "_visual_timer"))
    return timer


def visual(test_method):
    """Mark a screenshot-oriented unittest method for the opt-in full set."""
    test_method.__visual__ = True
    return test_method


def begin_ui_tracking(test, app) -> None:
    """Remember top-level widgets created by one unittest case."""
    test._ui_app = app
    test._ui_initial_widgets = {id(widget) for widget in app.topLevelWidgets()}


def _window_jobs(widgets) -> tuple[int, int, list[object]]:
    jobs = 0
    active_threads = 0
    windows = []
    for widget in widgets:
        task_map = getattr(widget, "jobs", None)
        pool = getattr(widget, "pool", None)
        if task_map is not None and pool is not None:
            windows.append(widget)
            jobs += len(task_map)
            active_threads += pool.activeThreadCount()
        page = getattr(widget, "today_page", None)
        if page is not None and getattr(page, "worker", None) is not None:
            jobs += 1
    return jobs, active_threads, windows


def cleanup_ui_test(test, app) -> None:
    """Drain synthetic UI work and dispose of every top-level test widget."""
    from unittest.mock import patch

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QDialog, QMessageBox

    if getattr(test, "_ui_cleanup_done", False):
        return
    widgets = list(app.topLevelWidgets())
    jobs, active_threads, windows = _window_jobs(widgets)
    before_widgets, before_jobs = len(widgets), jobs
    initial_ids = getattr(test, "_ui_initial_widgets", set())
    new_widgets = sum(id(widget) not in initial_ids for widget in widgets)
    print(f"UI_CLEANUP {test.id()} before_top_level={before_widgets} before_jobs={before_jobs} "
          f"before_active_threads={active_threads} new_top_level={new_widgets}", flush=True)

    def drain(deadline_seconds: float) -> None:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            app.processEvents()
            current_jobs, current_threads, _ = _window_jobs(list(app.topLevelWidgets()))
            if not current_jobs and not current_threads:
                return
            time.sleep(.01)

    drain(15)
    jobs, active_threads, windows = _window_jobs(list(app.topLevelWidgets()))
    if jobs or active_threads:
        for window in windows:
            for worker, _, _ in list(window.jobs.values()):
                worker.context.cancel.set()
            today_page = getattr(window, "today_page", None)
            worker = getattr(today_page, "worker", None)
            if worker is not None:
                worker.cancel.set()
        drain(10)

    widgets = list(app.topLevelWidgets())
    jobs, active_threads, windows = _window_jobs(widgets)
    if jobs or active_threads:
        print(f"UI_CLEANUP {test.id()} worker_timeout jobs={jobs} active_threads={active_threads}", flush=True)
        raise AssertionError(f"UI worker cleanup timed out: jobs={jobs}, active_threads={active_threads}")

    main_windows = set(windows)
    dialogs = [widget for widget in widgets if isinstance(widget, QDialog) and widget not in main_windows]
    others = [widget for widget in widgets if widget not in main_windows and widget not in dialogs]
    with (patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard),
          patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"),
          patch.object(QMessageBox, "critical")):
        for widget in dialogs + others + windows:
            try:
                widget.close()
            except RuntimeError:
                pass
        app.processEvents()
        for widget in list(app.topLevelWidgets()):
            try:
                widget.deleteLater()
            except RuntimeError:
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    after_widgets = list(app.topLevelWidgets())
    after_jobs, after_threads, _ = _window_jobs(after_widgets)
    print(f"UI_CLEANUP {test.id()} after_top_level={len(after_widgets)} after_jobs={after_jobs} "
          f"after_active_threads={after_threads}", flush=True)
    if after_widgets or after_jobs or after_threads:
        raise AssertionError(f"UI residue after cleanup: widgets={len(after_widgets)}, jobs={after_jobs}, "
                             f"active_threads={after_threads}")
    test._ui_cleanup_done = True


def register_ui_cleanup(test, app) -> None:
    """Register cleanup late so it runs before fixture directories are removed."""
    if not hasattr(test, "_ui_initial_widgets"):
        begin_ui_tracking(test, app)
    test.addCleanup(cleanup_ui_test, test, app)


class TestDirectory:
    """TemporaryDirectory interface, using ordinary mkdir to inherit parent ACLs.

    Python 3.12's private mode-0700 directories can reject restricted Windows
    tokens. These fixtures contain only synthetic data, never credentials.
    """

    def __init__(self):
        self.parent = evidence_root()
        self.path = self.parent / ("selfmanger-test-" + uuid4().hex)
        self.path.mkdir()
        self.name = str(self.path)

    def cleanup(self):
        if not self.path.exists():
            return
        resolved = self.path.resolve()
        if (resolved != self.path or resolved.parent != self.parent or self.path.is_symlink()
                or getattr(self.path, "is_junction", lambda: False)()):
            raise RuntimeError("Unexpected test cleanup path")
        def remove_readonly(function, name, failure):
            target = Path(name)
            error = failure[1]
            if (os.name != "nt" or not isinstance(error, PermissionError)
                    or target.is_symlink() or not target.is_file()
                    or not target.resolve().is_relative_to(resolved)
                    or not (target.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)):
                raise error
            # Git marks object files read-only. Clear only that file attribute,
            # inside this verified synthetic fixture; never change a DACL.
            target.chmod(stat.S_IREAD | stat.S_IWRITE)
            function(name)
        shutil.rmtree(self.path, onerror=remove_readonly)

    def __enter__(self):
        return self.name

    def __exit__(self, *_):
        self.cleanup()


def wait_for_idle(app, window, timeout=15):
    """Let startup jobs finish before exercising actions guarded by busy state."""
    deadline = time.monotonic() + timeout
    quiet = None
    while time.monotonic() < deadline:
        app.processEvents()
        if window.jobs or window.pool.activeThreadCount():
            quiet = None
        elif quiet is None:
            quiet = time.monotonic()
        elif time.monotonic() - quiet > .2:
            return
        time.sleep(.005)
    raise AssertionError("Isolated window did not settle")
