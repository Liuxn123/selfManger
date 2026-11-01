"""Run unittest modules in isolated subprocesses with bounded parallelism."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SERIAL_MODULES = frozenset({"test_combined_backup"})


def partition_modules(modules: list[str]) -> tuple[list[str], list[str]]:
    """Separate full-application smoke modules from parallel unit modules."""
    serial = [name for name in modules if name in SERIAL_MODULES]
    parallel = [name for name in modules if name not in SERIAL_MODULES]
    return serial, parallel


def result_counts(log_text: str) -> dict:
    ran = re.search(r"Ran (\d+) tests? in ([0-9.]+)(?:s| seconds?)", log_text)
    failed = re.search(r"FAILED \(([^)]*)\)", log_text)
    passed = re.search(r"\bOK(?: \(([^)]*)\))?", log_text)
    details = failed.group(1) if failed else (passed.group(1) or "" if passed else "")

    def count(name: str) -> int:
        match = re.search(rf"\b{name}=(\d+)\b", details)
        return int(match.group(1)) if match else 0

    return {
        "tests": int(ran.group(1)) if ran else 0,
        "failures": count("failures"),
        "errors": count("errors"),
        "skipped": count("skipped"),
    }


def run_module(name: str, timeout: int, output: Path, mode: str) -> dict:
    module_root = output / "fixtures" / name
    temporary_root = output / "tmp" / name
    module_root.mkdir(parents=True, exist_ok=False)
    temporary_root.mkdir(parents=True, exist_ok=False)

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), str(ROOT), env.get("PYTHONPATH")]))
    env["SELFMANGER_TEST_ROOT"] = str(module_root)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(temporary_root)

    log_path = output / (name + ".log")
    diagnostic = max(1, min(60, timeout - 2))
    started = time.monotonic()
    print(f"START   {name} (limit {timeout}s, mode={mode})", flush=True)
    status = "FAIL"
    worker_args = [sys.executable, "-B", str(ROOT / "scripts" / "_unittest_worker.py"),
                   str(diagnostic), "tests." + name]
    if mode == "fast":
        worker_args.append("--fast")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(worker_args, cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT)
        try:
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    status = "TIMEOUT"
                    break
                try:
                    code = process.wait(timeout=min(15, remaining))
                    status = "OK" if code == 0 else "FAIL"
                    break
                except subprocess.TimeoutExpired:
                    print(f"RUNNING {name} {time.monotonic() - started:.0f}s; log: {log_path}", flush=True)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    seconds = round(time.monotonic() - started, 2)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    tail = [line for line in log_text.splitlines() if line.strip()]
    counts = result_counts(log_text)
    print(f"{status:7} {seconds:6.1f}s {name} | {counts['tests']} tests, "
          f"{counts['failures']} failures, {counts['errors']} errors, {counts['skipped']} skipped | "
          f"{' | '.join(tail[-3:])}", flush=True)
    return {"module": name, "status": status, "seconds": seconds, **counts, "log": str(log_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filters", nargs="*", help="module name substrings")
    parser.add_argument("--timeout", type=int, default=120, help="seconds per module")
    parser.add_argument("--output", type=Path, help="new directory below SELFMANGER_TEST_ROOT")
    default_jobs = min(max(1, (os.cpu_count() or 1) // 2), 6)
    parser.add_argument("--jobs", type=int, default=default_jobs,
                        help=f"parallel module processes (default: {default_jobs})")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--fast", action="store_true", help="exclude tests marked @visual")
    modes.add_argument("--full", action="store_true", help="run every discovered test (default)")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    modules = sorted(path.stem for path in (ROOT / "tests").glob("test_*.py"))
    if args.filters:
        modules = [name for name in modules if any(part in name for part in args.filters)]
    if not modules:
        parser.error("no test modules match")

    base_root = Path(os.environ.get("SELFMANGER_TEST_ROOT", tempfile.gettempdir())).resolve()
    base_root.mkdir(parents=True, exist_ok=True)
    output = (args.output.resolve() if args.output else
              base_root / ("test-run-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]))
    if not output.is_relative_to(base_root):
        parser.error("--output must be inside SELFMANGER_TEST_ROOT")
    output.mkdir(parents=True, exist_ok=False)
    mode = "fast" if args.fast else "full"
    print(f"Logs: {output}", flush=True)
    print(f"Modules: {len(modules)}; jobs: {args.jobs}; mode: {mode}", flush=True)

    started = time.monotonic()
    completed: dict[str, dict] = {}

    def save_summary() -> None:
        rows = [completed[module] for module in modules if module in completed]
        (output / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    serial_modules, parallel_modules = partition_modules(modules)
    for name in serial_modules:
        completed[name] = run_module(name, args.timeout, output, mode)
        save_summary()

    if parallel_modules:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {executor.submit(run_module, name, args.timeout, output, mode): name
                       for name in parallel_modules}
            for future in as_completed(futures):
                name = futures[future]
                completed[name] = future.result()
                save_summary()

    results = [completed[name] for name in modules]
    failures = sum(row["status"] != "OK" for row in results)
    elapsed = round(time.monotonic() - started, 2)
    test_totals = {key: sum(row[key] for row in results) for key in ("tests", "failures", "errors", "skipped")}
    print(f"{len(results) - failures}/{len(results)} modules passed in {elapsed:.1f}s; "
          f"{test_totals['tests']} tests ({test_totals['failures']} failures, "
          f"{test_totals['errors']} errors, {test_totals['skipped']} skipped); "
          f"summary: {output / 'summary.json'}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
