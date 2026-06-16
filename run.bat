@echo off
REM Activity Monitor – Windows setup and launcher
REM Run once to install dependencies, then again to start the app.

setlocal
set "SCRIPT_DIR=%~dp0"
set PYTHONUNBUFFERED=1
cd /d "%SCRIPT_DIR%"

echo ================================================
echo   Activity Monitor – Setup and Launcher
echo ================================================

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

REM Create virtual environment if missing
if not exist ".venv\" (
    echo [SETUP] Creating virtual environment…
    python -m venv .venv
)

REM Activate venv
call .venv\Scripts\activate.bat

REM Install / upgrade dependencies
echo [SETUP] Installing dependencies…
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

REM Launch
echo [START] Launching Activity Monitor…
python main.py

endlocal
