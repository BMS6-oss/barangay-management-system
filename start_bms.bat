@echo off
setlocal enabledelayedexpansion
title Barangay Management System (BMS) Launcher

:: Navigate to script directory
cd /d "%~dp0"

echo ===================================================
echo   BARANGAY MANAGEMENT SYSTEM (BMS) LAUNCHER
echo ===================================================
echo.

:: 1. Detect Python Executable
set "PY_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_EXE=python"
    ) else (
        where py >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_EXE=py"
        )
    )
)

if "%PY_EXE%"=="" (
    echo [ERROR] Python was not found on your system.
    echo Please install Python 3.8 or higher and add it to your PATH.
    echo Download from: https://www.python.org/
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Python environment...
%PY_EXE% --version
if !errorlevel! neq 0 (
    echo [ERROR] Python failed to execute.
    pause
    exit /b 1
)
echo [OK] Python is ready.
echo.

:: Load the same host and port values used by server.py/config.py.
for /f "tokens=1,2" %%A in ('%PY_EXE% -c "import config; print(config.HOST, config.PORT)"') do (
    set "BMS_HOST=%%A"
    set "BMS_PORT=%%B"
)
if "%BMS_HOST%"=="" if "%BMS_PORT%"=="" (
    echo [ERROR] Could not read the BMS network configuration from config.py.
    pause
    exit /b 1
)
set "BMS_URL=http://%BMS_HOST%:%BMS_PORT%"

:: 2. Check if Server is ALREADY Running on the configured port
echo [2/4] Checking if BMS server is already running...
powershell -NoProfile -Command "try { $r = (Invoke-WebRequest -Uri '%BMS_URL%/api/health' -TimeoutSec 2 -UseBasicParsing).Content; if ($r -match '\"status\":\s*\"ok\"') { exit 0 } else { exit 1 } } catch { exit 1 }"
if !errorlevel! equ 0 (
    echo.
    echo ===================================================
    echo [OK] BMS server is ALREADY running and healthy!
    echo ===================================================
    echo Reusing existing server instance to avoid duplicate port conflicts.
    echo Opening %BMS_URL%/ in your browser...
    echo.
    start %BMS_URL%/
    exit /b 0
)

:: 3. Check SQLite database
echo [OK] %BMS_URL% is available.
echo [3/4] Initializing SQLite database...
%PY_EXE% -c "import server; server.init_db(); print('SQLite database verified.')"
if !errorlevel! neq 0 (
    echo [ERROR] Database initialization failed.
    pause
    exit /b 1
)
echo.

:: 4. Start server.py to keep backend running while BMS is being used
echo [4/4] Starting BMS server at %BMS_URL%/ ...
echo Server logs will appear below. Keep this window open while using BMS.
echo Press Ctrl+C to shut down the server.
echo ===================================================
echo.
"%PY_EXE%" server.py
if !errorlevel! neq 0 (
    echo.
    echo ===================================================
    echo [ERROR] BMS Server exited unexpectedly (code: !errorlevel!).
    echo Please check bms_server.log for more details.
    echo ===================================================
    pause
    exit /b !errorlevel!
)
