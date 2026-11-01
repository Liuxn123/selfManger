@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Development environment not found: .venv\Scripts\pythonw.exe
    echo Create the project virtual environment before starting the source version.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
rem Source startup reads the local-config.json data location.
rem An explicit SELFMANGER_DATA_DIR always takes precedence.
start "" ".venv\Scripts\pythonw.exe" -m selfManger --startup-page workbench
exit /b
