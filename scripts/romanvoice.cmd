@echo off
rem Canonical hidden launcher. The ensure script owns duplicate prevention,
rem interpreter selection, service exposure, and bounded recovery.

setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0ensure-romanvoice-running.ps1" -Quiet
exit /b %ERRORLEVEL%
