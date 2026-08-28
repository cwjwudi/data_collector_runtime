@echo off
setlocal
cd /d "%~dp0_Launcher"
call stop.bat %*
endlocal & exit /b %ERRORLEVEL%
