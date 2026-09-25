@echo off
setlocal enabledelayedexpansion
title Setup BMS Windows Auto-Start

cd /d "%~dp0"

echo ===================================================
echo   SETUP BMS WINDOWS AUTO-START
echo ===================================================
echo.
echo This utility configures BMS to start automatically in the
echo background whenever Windows starts.
echo.

set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_VBS=%~dp0start_bms.vbs"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\BMS_AutoStart.lnk"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_VBS%'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'Barangay Management System Auto-Start'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [SUCCESS] BMS Auto-Start shortcut created:
    echo "%SHORTCUT_PATH%"
    echo.
    echo Tip: Set BMS_AUTO_OPEN_BROWSER=false in .env if you want the server
    echo to start silently without popping up browser windows on Windows boot.
) else (
    echo [ERROR] Could not create startup shortcut.
)

echo.
pause
