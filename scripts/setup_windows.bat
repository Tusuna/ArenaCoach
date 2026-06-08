@echo off
setlocal
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File ".\scripts\setup_windows.ps1"
