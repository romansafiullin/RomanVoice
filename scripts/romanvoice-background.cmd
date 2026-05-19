@echo off
setlocal
cd /d "%~dp0\.."
set ROMANVOICE_START_HIDDEN=1
set ROMANVOICE_ENABLE_GLOBAL_HOTKEYS=1
uv run --python 3.12 pythonw app_qt.py
