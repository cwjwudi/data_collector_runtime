@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title SD SMA Unified Launcher

set "PACKAGE_ROOT=%~dp0.."
for %%I in ("%PACKAGE_ROOT%") do set "PACKAGE_ROOT=%%~fI"
set "BUNDLED_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"
set "BUNDLED_PYTHON_HOME=%PACKAGE_ROOT%\_Python"
set "VENV_CFG=%PACKAGE_ROOT%\.venv\pyvenv.cfg"

if exist "%VENV_CFG%" if exist "%BUNDLED_PYTHON_HOME%\python.exe" (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
    "$cfg='%VENV_CFG%'; $pythonHome='%BUNDLED_PYTHON_HOME%';" ^
    "$lines=Get-Content -LiteralPath $cfg;" ^
    "$out=foreach($line in $lines){ if($line -match '^\s*home\s*='){ 'home = ' + $pythonHome } else { $line } };" ^
    "Set-Content -LiteralPath $cfg -Value $out -Encoding ASCII"
)

if exist "%BUNDLED_PYTHON%" (
  "%BUNDLED_PYTHON%" sd_sma_launcher.py %*
) else (
  python sd_sma_launcher.py %*
)
echo.
echo Launcher exited.
pause
endlocal
