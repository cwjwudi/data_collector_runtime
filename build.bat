@echo off
setlocal
call "%~dp0_Launcher\build.bat" %*
endlocal & exit /b %ERRORLEVEL%
