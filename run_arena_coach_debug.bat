@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Arena Coach virtual environment was not found.
    echo Run scripts\setup_windows.ps1 first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
set "PYTHONPATH=%CD%\src"
python -m arena_coach.gui.app
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Arena Coach exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
