@echo off
setlocal

cd /d %~dp0
rem Default bind is loopback-only; set SD_SMA_BIND_HOST=0.0.0.0 to expose on LAN (pair with SD_SMA_WEB_TOKEN).
set "BIND_HOST=127.0.0.1"
if defined SD_SMA_BIND_HOST set "BIND_HOST=%SD_SMA_BIND_HOST%"
python -m uvicorn app.main:app --host %BIND_HOST% --port 8092 --log-config config\logging.json

endlocal
