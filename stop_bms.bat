@echo off
setlocal enabledelayedexpansion
title Stop Barangay Management System (BMS)

echo ===================================================
echo   STOPPING BMS SERVER
echo ===================================================
echo.

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -Verbose }"

echo.
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1 -ErrorAction Stop; exit 1 } catch { exit 0 }" >nul 2>&1

if %errorlevel% equ 0 (
    echo [SUCCESS] BMS server has been stopped.
) else (
    echo [NOTICE] Port 8000 is still active or server was already closed.
)
echo.
timeout /t 2 >nul
