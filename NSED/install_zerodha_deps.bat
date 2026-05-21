@echo off
setlocal enabledelayedexpansion
title Zerodha NSE Data Manager - Dependency Installer
color 0A

echo ============================================================
echo   Zerodha NSE Data Manager - Fresh Setup Installer
echo ============================================================
echo.

:: ----------------------------------------------------------------
:: STEP 1 - Check if Python is installed
:: ----------------------------------------------------------------
echo [1/6] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python is not installed or not in PATH.
    echo.
    echo  Please install Python 3.8 or higher from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During install, check the box:
    echo    "Add Python to PATH"
    echo.
    echo  After installing Python, re-run this batch file.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  Found: %PYVER%
echo.

:: ----------------------------------------------------------------
:: STEP 2 - Check pip
:: ----------------------------------------------------------------
echo [2/6] Checking pip...
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  pip not found. Installing pip...
    python -m ensurepip --upgrade
)
echo  pip OK
echo.

:: ----------------------------------------------------------------
:: STEP 3 - Upgrade pip to latest
:: ----------------------------------------------------------------
echo [3/6] Upgrading pip to latest version...
python -m pip install --upgrade pip --quiet
echo  pip upgraded
echo.

:: ----------------------------------------------------------------
:: STEP 4 - Create virtual environment
:: ----------------------------------------------------------------
echo [4/6] Setting up virtual environment...
if not exist ".venv" (
    echo  Creating .venv ...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  .venv created successfully
) else (
    echo  .venv already exists -- skipping creation
)
echo.

:: Activate the virtual environment
echo  Activating .venv ...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo  ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)
echo  .venv activated
echo.

:: ----------------------------------------------------------------
:: STEP 5 - Install required packages into venv
:: ----------------------------------------------------------------
echo [5/6] Installing required Python packages into .venv...
echo.

set PACKAGES=kiteconnect pandas tqdm requests yfinance openpyxl numpy

for %%p in (%PACKAGES%) do (
    echo  Installing %%p ...
    pip install %%p --quiet
    if !errorlevel! neq 0 (
        echo   FAILED to install %%p
        set INSTALL_FAILED=1
    ) else (
        echo   %%p installed successfully
    )
)
echo.

:: ----------------------------------------------------------------
:: STEP 6 - Verify all imports work
:: ----------------------------------------------------------------
echo [6/6] Verifying all packages import correctly...
echo.

::python -c "import kiteconnect; print('  kiteconnect  v' + kiteconnect.__version__)"
python -c "import kiteconnect; print('  kiteconnect  v' + str(kiteconnect.__version__))"
if %errorlevel% neq 0 (
    echo   ERROR: kiteconnect import failed
    set VERIFY_FAILED=1
)

python -c "import pandas; print('  pandas       v' + pandas.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: pandas import failed
    set VERIFY_FAILED=1
)

python -c "import tqdm; print('  tqdm         v' + tqdm.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: tqdm import failed
    set VERIFY_FAILED=1
)

python -c "import requests; print('  requests     v' + requests.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: requests import failed
    set VERIFY_FAILED=1
)

python -c "import yfinance; print('  yfinance     v' + yfinance.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: yfinance import failed
    set VERIFY_FAILED=1
)

python -c "import openpyxl; print('  openpyxl     v' + openpyxl.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: openpyxl import failed
    set VERIFY_FAILED=1
)

python -c "import numpy; print('  numpy        v' + numpy.__version__)"
if %errorlevel% neq 0 (
    echo   ERROR: numpy import failed
    set VERIFY_FAILED=1
)

python -c "import datetime, os, time, logging; print('  stdlib       OK  (datetime, os, time, logging)')"
echo.

:: ----------------------------------------------------------------
:: RESULT
:: ----------------------------------------------------------------
if defined INSTALL_FAILED (
    echo ============================================================
    echo   SETUP INCOMPLETE - Some packages failed to install.
    echo   Check your internet connection and try again.
    echo ============================================================
) else if defined VERIFY_FAILED (
    echo ============================================================
    echo   SETUP INCOMPLETE - Some packages failed to import.
    echo   Try running: .venv\Scripts\pip install -r requirements.txt
    echo ============================================================
) else (
    echo ============================================================
    echo   ALL DEPENDENCIES INSTALLED SUCCESSFULLY
    echo ============================================================
    echo.
    echo   Virtual environment: .venv\
    echo   All packages installed inside .venv
    echo.
    echo   Next steps:
    echo     1. Open config.py and set your API_KEY and API_SECRET
    echo     2. Run: python gui.py
    echo     3. Use the GUI to generate your daily access token
    echo     4. Click Run Daily Routine to start downloading
    echo ============================================================
)

echo.
pause
