"""Reproducible synthetic hot-path measurements; never opens user data."""
from datetime import date, timedelta
import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from selfManger.action_center import ActionCenter
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.storage import Store


def measure(operation, repeats):
    import sqlite3
    timings, queries, connections = [], [], []
    connect = sqlite3.connect
    for _ in range(repeats):
        count = {"selects": 0, "connections": 0}
        def trace(sql):
            count["selects"] += sql.lstrip().upper().startswith("SELECT")
        def tracked(*args, **kwargs):
            count["connections"] += 1
            db = connect(*args, **kwargs)
            db.set_trace_callback(trace)
            return db
        with patch("selfManger.storage.sqlite3.connect", tracked):
            started = time.perf_counter()
            operation()
            timings.append(round((time.perf_counter() - started) * 1000, 3))
        queries.append(count["selects"])
        connections.append(count["connections"])
    return {"milliseconds": timings, "median_ms": statistics.median(timings),
            "selects": queries, "connections": connections}


def reference_period_projection(center, start, end):
    """Test-oracle implementation retained for same-fixture comparison, not historical source."""
    rules = center.rules()
    with center.store.connect() as db:
        completed = {(row["task_id"], row["day"]) for row in db.execute(
            "SELECT task_id,day FROM action_occurrences WHERE day BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()))}
    result = []
    for row in center.actions("all", day=start):
        rule = rules.get(row["id"])
        if not rule:
            if row["due_date"] and start.isoformat() <= row["due_date"] <= end.isoformat():
                result.append(row)
            continue
        for offset in range((end - start).days + 1):
            target = start + timedelta(days=offset)
            if center.occurs(rule, target):
                key = target.isoformat()
                result.append({**row, "occurrence_day": key,
                               "done": (row["id"], key) in completed, "overdue": False})
    return sorted(result, key=lambda row: (row["occurrence_day"], row["title"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tasks", type=int, default=600)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 0 <= args.tasks <= 5000 or args.repeats < 1:
        parser.error("--tasks must be 0..5000 and --repeats must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    store = Store(output / "fixture/data")
    store.set_setting("workbench_root", str(output / "fixture/notes"))
    workspace = ProjectWorkspace(output / "fixture/workspace")
    workspace.initialize(TaskContext())
    created = workspace.create("性能虚构项目", "仅用于隔离测量", False, TaskContext())
    scope = {"project_id": created["project_id"], "project_root": str(workspace.root)}
    with store.atomic():
        for index in range(args.tasks):
            store.add_planned_task({**scope, "title": f"任务{index:04d}", "source": "ai"})
    project_task_read = lambda: store.planned_tasks(
        project_id=created["project_id"], project_root=str(workspace.root))
    results = {"task_count": args.tasks, "project_task_read": measure(project_task_read, args.repeats)}
    profile = cProfile.Profile()
    profile.runcall(project_task_read)
    with (output / "project-task-read-profile.txt").open("w", encoding="utf-8") as stream:
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(20)
    center = ActionCenter(store)
    start = date.today()
    due_date = start.isoformat()
    with store.atomic():
        for index in range(args.tasks):
            center.save_action({"title": f"周期{index:04d}", "due_date": due_date, "repeat": "weekly",
                                "repeat_days": [start.weekday()]})
    end = start + timedelta(days=13)
    optimized_rows = center.period_actions(start, end)
    reference_rows = reference_period_projection(center, start, end)
    if optimized_rows != reference_rows:
        raise RuntimeError("Reference and optimized period projections differ")
    results["period_actions"] = measure(lambda: center.period_actions(start, end), args.repeats)
    results["period_actions_reference"] = measure(lambda: reference_period_projection(center, start, end), args.repeats)
    results["period_actions_comparison"] = {"equivalent": True, "row_count": len(optimized_rows),
                                             "reference_label": "test-oracle behavior; not a historical source snapshot"}
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from selfManger.ui.window import MainWindow
    app = QApplication.instance() or QApplication([])
    launch_times, widgets, catalogs, created_widgets = [], [], [], []
    for index in range(args.repeats):
        ui_store = Store(output / f"fixture/ui-{index}")
        ui_store.set_setting("workbench_root", str(output / f"fixture/ui-notes-{index}"))
        with patch("selfManger.security.SecretStore.get", return_value=None):
            before_widgets = set(app.allWidgets())
            started = time.perf_counter()
            window = MainWindow(ui_store)
            launch_times.append(round((time.perf_counter() - started) * 1000, 3))
            widgets.append(len(window.findChildren(QWidget)))
            created_widgets.append(len(set(app.allWidgets()) - before_widgets))
            # Inspect instance state without invoking a future lazy property.
            catalogs.append(int("catalog_page" in window.__dict__ or window.__dict__.get("_catalog_page") is not None))
            deadline = time.monotonic() + 15
            quiet = None
            while time.monotonic() < deadline:
                app.processEvents()
                if window.jobs or window.pool.activeThreadCount():
                    quiet = None
                elif quiet is None:
                    quiet = time.monotonic()
                elif time.monotonic() - quiet > .25:
                    break
                time.sleep(.005)
            else:
                raise RuntimeError("Synthetic window did not settle")
            window.close()
            window.pool.waitForDone(5000)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    results["window_construction"] = {"milliseconds": launch_times, "median_ms": statistics.median(launch_times),
                                      "widgets": widgets, "created_widgets": created_widgets, "catalog_constructions": catalogs}
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
