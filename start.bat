@echo off
setlocal
cd /d "%~dp0_Launcher"
call start.bat %*
endlocal & exit /b %ERRORLEVEL%
