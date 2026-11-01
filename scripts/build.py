from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from selfManger import __version__


def build_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if sys.platform == "win32":
        # PyInstaller resolves native dependencies using PATH. Unrelated tools
        # (for example Poppler/Conda) can supply an incompatible icuuc.dll and
        # shadow the Windows ICU ABI used by Qt. Keep only build/runtime paths.
        windows = Path(os.environ["SystemRoot"])
        directories = [Path(sys.executable).parent, Path(sys.base_prefix),
                       Path(sys.base_prefix) / "DLLs", windows / "System32", windows]
        environment["PATH"] = os.pathsep.join(str(path) for path in directories if path.is_dir())
    return environment


def pyinstaller_command(root: Path, distpath: Path, workpath: Path) -> list[str]:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
               "--name", "selfManger", "--paths", str(root / "src"), "--collect-submodules", "keyring.backends",
               "--add-data", f"{root / 'src/selfManger/assets'}{os.pathsep}selfManger/assets",
               "--distpath", str(distpath), "--workpath", str(workpath), str(root / "scripts/desktop_entry.py")]
    if sys.platform == "win32":
        command[3:3] = ["--icon", str(root / "src/selfManger/assets/ui/selfManger.ico")]
    return command


def main() -> None:
    existing = ROOT / "dist/selfManger"
    if (existing / "data/manager.sqlite3").exists() or (existing / "backups").exists():
        raise RuntimeError("Build output contains portable user data; move the whole folder to a separate location before rebuilding.")
    command = pyinstaller_command(ROOT, ROOT / "dist", ROOT / "build")
    subprocess.run(command, cwd=ROOT, env=build_environment(), check=True)
    application = ROOT / "dist" / ("selfManger.app" if sys.platform == "darwin" else "selfManger")
    portable_root = application.parent if sys.platform == "darwin" else application
    (portable_root / "portable.json").write_text(json.dumps({"mode": "portable", "schema_version": 1}), encoding="utf-8")
    shutil.copytree(ROOT / "licenses", portable_root / "licenses", dirs_exist_ok=True)
    # A new staging directory prevents a removed DLL from surviving in a zip
    # produced by a subsequent build.
    with tempfile.TemporaryDirectory(prefix="package-", dir=ROOT / "build") as temporary:
        payload = Path(temporary)
        shutil.copytree(application, payload / application.name, symlinks=True)
        if sys.platform == "darwin":
            shutil.copy2(portable_root / "portable.json", payload / "portable.json")
        shutil.copy2(ROOT / "README.md", payload / "README.md")
        shutil.copy2(ROOT / "src/selfManger/assets/docs/USER_GUIDE.md", payload / "使用说明.md")
        shutil.copy2(ROOT / "src/selfManger/assets/fonts/OFL.txt", payload / "Noto-Font-OFL.txt")
        metadata = {"version": __version__, "platform": sys.platform, "architecture": platform.machine(), "python": platform.python_version(), "data_mode": "portable"}
        if shutil.which("git"):
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
            if revision.returncode == 0:
                metadata["source_commit"] = revision.stdout.strip()
        (payload / "BUILD-INFO.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        name = ROOT / "dist" / f"selfManger-{__version__}-{sys.platform}-{platform.machine()}"
        result = shutil.make_archive(str(name), "zip" if sys.platform == "win32" else "gztar", root_dir=payload)
    print("Package:", result)


if __name__ == "__main__":
    main()
