@echo off
rem Explicit UI/debug launcher. Use this only when you want the full window.

setlocal
set "REPO=%~dp0.."

cd /d "%REPO%"
set ROMANVOICE_FORCE_SHOW=1
set ROMANVOICE_ENABLE_GLOBAL_HOTKEYS=1
uv run --python 3.12 python app_qt.py %*
endlocal
