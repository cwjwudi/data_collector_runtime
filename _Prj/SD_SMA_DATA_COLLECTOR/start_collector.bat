@echo off
setlocal
rem Single launcher: Web UI + embedded collector control (same Python process).
chcp 936 >nul 2>&1
cd /d "%~dp0"
title SD SMA Collector
rem Default bind is loopback-only; set SD_SMA_BIND_HOST=0.0.0.0 to expose on LAN (pair with SD_SMA_WEB_TOKEN).
set "BIND_HOST=127.0.0.1"
if defined SD_SMA_BIND_HOST set "BIND_HOST=%SD_SMA_BIND_HOST%"
echo.
echo   SD_SMA_DATA_COLLECTOR
echo   Web UI: http://127.0.0.1:8091
echo   Open / then /dashboard or /config. Start/stop collector on /dashboard.
echo.
python -m uvicorn web_config.main:app --host %BIND_HOST% --port 8091
pause
endlocal
