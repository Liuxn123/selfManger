from __future__ import annotations

import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from selfManger import __version__


def check_application(root: Path, payload: Path, portable: bool = False) -> None:
    if sys.platform == "win32":
        executable = payload / "selfManger/selfManger.exe"
    elif sys.platform == "darwin":
        executable = payload / "selfManger.app/Contents/MacOS/selfManger"
    else:
        executable = payload / "selfManger/selfManger"
    screenshot = root / "build/smoke.png"
    screenshot.unlink(missing_ok=True)
    command = [str(executable), "--smoke-test", "--screenshot", str(screenshot)]
    if not portable:
        command += ["--data-dir", str(payload / "smoke-data")]
    process = subprocess.Popen(command, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        stdout, stderr = process.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        process.kill()  # This is our own empty-data smoke process, never a real task.
        process.communicate(timeout=5)
        raise SystemExit("Frozen application startup timed out")
    if process.returncode or not screenshot.is_file():
        print("Frozen exit code:", process.returncode)
        # Encode for the console explicitly: some Windows terminals use GBK.
        message = stderr.decode("utf-8", "replace")[-6000:]
        print(message.encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8"))
        error = payload / "smoke-data/startup-error.json"
        if error.is_file():
            print(error.read_text(encoding="utf-8"))
        raise SystemExit("Frozen application startup failed")
    print("Extracted package opened, rendered and exited successfully")
    if portable:
        portable_root = payload if sys.platform == "darwin" else payload / "selfManger"
        if not (portable_root / "data/manager.sqlite3").is_file():
            raise SystemExit("Portable package wrote its data outside the application folder")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    extension = ".zip" if sys.platform == "win32" else ".tar.gz"
    package = root / "dist" / f"selfManger-{__version__}-{sys.platform}-{platform.machine()}{extension}"
    with tempfile.TemporaryDirectory(prefix="smoke-extract-", dir=root / "build") as temporary:
        payload = Path(temporary) / "original"
        payload.mkdir()
        if sys.platform == "win32":
            with zipfile.ZipFile(package) as archive:
                archive.extractall(payload)
        else:
            with tarfile.open(package, "r:gz") as archive:
                archive.extractall(payload, filter="data")
        guides = list(payload.rglob("USER_GUIDE.md"))
        if not guides or not any("13. 常见问题" in guide.read_text(encoding="utf-8") for guide in guides):
            raise SystemExit("Packaged offline user guide is missing or incomplete")
        check_application(root, payload)
        check_application(root, payload, portable=True)
        moved = Path(temporary) / "moved-folder"
        payload.rename(moved)
        check_application(root, moved, portable=True)
        print("Portable package kept its data after moving the entire folder")


if __name__ == "__main__":
    main()
