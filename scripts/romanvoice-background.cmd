@echo off
setlocal
cd /d "%~dp0\.."
set ROMANVOICE_START_HIDDEN=1
set ROMANVOICE_ENABLE_GLOBAL_HOTKEYS=1
if not defined ROMANVOICE_SERVICE_HOST set ROMANVOICE_SERVICE_HOST=0.0.0.0
uv run --python 3.12 pythonw app_qt.py
