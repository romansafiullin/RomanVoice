@echo off
rem Explicit UI/debug launcher. Use this only when you want the full window.

setlocal
set "REPO=%~dp0.."

cd /d "%REPO%"
set ROMANVOICE_FORCE_SHOW=1
set ROMANVOICE_ENABLE_GLOBAL_HOTKEYS=1
if not defined ROMANVOICE_SERVICE_HOST set ROMANVOICE_SERVICE_HOST=0.0.0.0

if exist "%REPO%\.venv\Scripts\pythonw.exe" (
    "%REPO%\.venv\Scripts\pythonw.exe" app_qt.py %*
) else (
    uv run --python 3.12 pythonw app_qt.py %*
)
endlocal
