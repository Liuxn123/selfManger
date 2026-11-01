from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain import Cancelled, UserError
from .security import redact


class TaskContext:
    def __init__(self, logger=lambda message: None) -> None:
        self.cancel = threading.Event()
        self.logger = logger

    def log(self, message: str) -> None:
        self.logger(redact(message))

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("已在安全检查点取消。")


class ResourceLocks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    @contextmanager
    def acquire(self, keys: list[str]) -> Iterator[None]:
        keys = sorted(set(keys))
        with self._lock:
            overlap = bool(self._active.intersection(keys))
            for key in keys:
                if key.startswith("path:"):
                    path = Path(key[5:])
                    for active in self._active:
                        if active.startswith("path:"):
                            other = Path(active[5:])
                            overlap |= path.is_relative_to(other) or other.is_relative_to(path)
            if overlap:
                raise UserError("此资源已有任务运行，请等待完成。")
            self._active.update(keys)
        try:
            yield
        finally:
            with self._lock:
                self._active.difference_update(keys)


def run_process(command: list[str], context: TaskContext, *, cwd: Path | None = None,
                timeout: int = 600, input_text: str | None = None, cancellable: bool = True,
                env: dict[str, str] | None = None, kill_tree: bool = False) -> str:
    """No shell; output is bounded and is never logged automatically."""
    context.checkpoint()
    context.log("正在执行工具，请等待…")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=creationflags)
    except OSError as exc:
        raise UserError("无法启动工具，请检查可执行文件及权限。") from exc
    result: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    output_lock = threading.Lock()
    overflow = threading.Event()
    read_error = threading.Event()

    def read_pipe(pipe, name: str) -> None:
        try:
            while chunk := pipe.read1(65536):
                with output_lock:
                    if sum(map(len, result.values())) + len(chunk) > 8_000_000:
                        overflow.set()
                        break
                    result[name].extend(chunk)
        except (OSError, ValueError):
            read_error.set()
        finally:
            pipe.close()

    readers = [threading.Thread(target=read_pipe, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=read_pipe, args=(process.stderr, "stderr"), daemon=True)]
    for reader in readers:
        reader.start()
    def send_input() -> None:
        try:
            if input_text is not None:
                process.stdin.write(input_text.encode("utf-8"))
        except (OSError, BrokenPipeError):
            pass
        finally:
            process.stdin.close()
    sender = threading.Thread(target=send_input, daemon=True)
    sender.start()
    deadline = time.monotonic() + timeout
    stopped = ""
    while process.poll() is None:
        if overflow.is_set():
            stopped = "overflow"
        elif cancellable and context.cancel.is_set():
            stopped = "cancelled"
        elif time.monotonic() > deadline:
            stopped = "timeout"
        if stopped:
            if kill_tree:
                stop_process_tree(process)
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            break
        time.sleep(0.1)
    for reader in readers:
        reader.join(timeout=5)
    if overflow.is_set() or stopped == "overflow":
        raise UserError("工具结果超过 8 MB，已停止任务；请检查目标实际状态。")
    if stopped == "cancelled":
        raise Cancelled("任务已停止；请检查远端或外部工具实际状态。")
    if stopped == "timeout":
        raise UserError("任务超时；请检查任务记录和目标实际状态，确认后再重试。")
    if read_error.is_set() or any(reader.is_alive() for reader in readers):
        raise UserError("读取工具结果失败。")
    stdout = bytes(result.get("stdout", b""))
    stderr = bytes(result.get("stderr", b""))
    if len(stdout) + len(stderr) > 8_000_000:
        raise UserError("工具结果过大，已拒绝展示。")
    if process.returncode:
        # Raw stderr can contain arbitrary credentials; only explain known causes.
        lowered = stderr.decode("utf-8", "replace").lower()
        reasons = [("host key verification failed", "SSH 主机指纹未通过校验，请先核对 known_hosts。"),
                   ("permission denied", "认证或文件权限不足，请检查 SSH Agent 和文件权限。"),
                   ("could not resolve hostname", "无法解析服务器地址或 SSH 别名。"),
                   ("connection refused", "服务器拒绝连接，请检查端口与服务。"),
                   ("passphrase", "凭据解密失败，请检查口令或口令文件。")]
        for fragment, message in reasons:
            if fragment in lowered:
                raise UserError(message)
        raise UserError(f"工具执行失败（退出码 {process.returncode}）。请检查配置、依赖和工具自身诊断；原始输出未写入管理工具日志。")
    context.log("工具执行完成。")
    return stdout.decode("utf-8", "replace")


def stop_process_tree(process) -> None:
    """Stop the owned task and the descendants observed before its parent exits."""
    import psutil
    try:
        parent = psutil.Process(process.pid)
        owned = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(owned, timeout=1)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(alive, timeout=1)


def json_result(output: str) -> dict:
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise UserError("工具未返回有效 JSON 结果，请检查工具版本。") from exc
    if not isinstance(result, dict):
        raise UserError("工具返回了不支持的结果格式。")
    return result
