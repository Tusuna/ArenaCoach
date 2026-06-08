@echo off
setlocal
cd /d "%~dp0\.."
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File ".\scripts\check_setup.ps1"
