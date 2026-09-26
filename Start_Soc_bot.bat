@echo off
rem ============================================================
rem  SOC_BOT launcher: double-click to start Soc_bot.
rem  Works from anywhere (also from a desktop shortcut).
rem ============================================================
title SOC_BOT
chcp 65001 >nul

rem Always run from the folder this file is in (no "D:" / "cd" needed).
cd /d "%~dp0"

rem Use the project's virtual environment if there is one, otherwise the installed Python.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install it from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH". See setup_guide\00_MASTER_SETUP_GUIDE.md
    pause
    exit /b 1
)

rem First run on a new computer: install the libraries once.
"%PY%" -c "import textual, httpx, sqlalchemy, dotenv, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Installing Soc_bot's libraries, first run only - takes 1 to 3 minutes...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Installing the libraries failed. See the messages above.
        pause
        exit /b 1
    )
)

if not exist ".env" (
    echo [WARNING] No .env file yet: copy .env.example to .env and fill it in
    echo           ^(setup_guide\00_MASTER_SETUP_GUIDE.md, PART 4^).
    pause
)

"%PY%" main.py %*
if errorlevel 1 pause
