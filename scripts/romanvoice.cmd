@echo off
rem RomanVoice background launcher. Keeps the reliable first release on uv
rem while avoiding a visible console/main window for everyday dictation.

setlocal
set "REPO=%~dp0.."

cd /d "%REPO%"
set ROMANVOICE_START_HIDDEN=1
set ROMANVOICE_ENABLE_GLOBAL_HOTKEYS=1
if not defined ROMANVOICE_SERVICE_HOST set ROMANVOICE_SERVICE_HOST=0.0.0.0
start "RomanVoice" /B uv run --python 3.12 pythonw app_qt.py %*
endlocal
