@echo off
cd /d "%~dp0"

:: Check virtual environment exists
if not exist venv\Scripts\python.exe (
    echo Virtual environment not found. Please run install.bat first.
    pause
    exit /b 1
)

:: Launch the GUI
venv\Scripts\python.exe launcher.py
if errorlevel 1 (
    echo.
    echo The launcher exited with an error. Check logs\launcher.log for details.
    pause
)
