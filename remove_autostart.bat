@echo off
setlocal enabledelayedexpansion
title Remove BMS Windows Auto-Start

echo ===================================================
echo   REMOVE BMS WINDOWS AUTO-START
echo ===================================================
echo.

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\BMS_AutoStart.lnk"

if exist "%SHORTCUT_PATH%" (
    del /f /q "%SHORTCUT_PATH%"
    echo [SUCCESS] BMS Auto-Start shortcut removed successfully.
) else (
    echo [NOTICE] BMS Auto-Start shortcut was not found in Windows Startup.
)

echo.
pause
