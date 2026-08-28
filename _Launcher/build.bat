@echo off
setlocal
chcp 65001 >nul 2>&1
title SD SMA Package Builder
set "SCRIPT_DIR=%~dp0"

set "BUILD_MODE=%~1"
if not defined BUILD_MODE goto menu
if /i "%BUILD_MODE%"=="1" goto installer_arg
if /i "%BUILD_MODE%"=="installer" goto installer_arg
if /i "%BUILD_MODE%"=="2" goto source_arg
if /i "%BUILD_MODE%"=="source" goto source_arg
if /i "%BUILD_MODE%"=="portable" goto source_arg
echo [ERROR] Unknown build mode: %BUILD_MODE%
echo Use "installer" or "source".
goto failed

:menu
cls
echo ========================================
echo   SD SMA Package Builder
echo ========================================
echo.
echo   1. Build installer package
echo   2. Build source package
echo   0. Exit
echo.
set "BUILD_CHOICE="
set /p "BUILD_CHOICE=Select an option [1/2/0]: "
if "%BUILD_CHOICE%"=="1" goto installer
if "%BUILD_CHOICE%"=="2" goto source
if "%BUILD_CHOICE%"=="0" goto cancelled
echo.
echo [ERROR] Invalid option.
pause
goto menu

:source
echo ========================================
echo   SD SMA Source Package Builder
echo ========================================
echo.
echo Output: %SCRIPT_DIR%..\_Build\SD_SMA_Runtime_Package
echo PyPI:   https://pypi.tuna.tsinghua.edu.cn/simple
echo.

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\build_portable_package.ps1" %1 %2 %3 %4 %5 %6 %7 %8 %9
set "ERR=%ERRORLEVEL%"

echo.
if "%ERR%"=="0" (
  echo [OK] Source package generated:
  echo   %SCRIPT_DIR%..\_Build\SD_SMA_Runtime_Package
  echo.
  echo Copy the whole directory to the target PC and run start.bat.
) else (
  echo [FAILED] Package build failed. Exit code: %ERR%
  echo Scroll up to inspect the error details.
)

echo.
pause
endlocal & exit /b %ERR%

:source_arg
shift /1
goto source

:installer_arg
shift /1
goto installer

:installer
echo ========================================
echo   SD SMA Lightweight Installer Builder
echo ========================================
echo.
echo Output: %SCRIPT_DIR%..\_Build\SD_SMA_Installer_Package
echo Layout: one Launcher EXE + one shared Python runtime
echo.

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\build_installer_package.ps1" %1 %2 %3 %4 %5 %6 %7 %8 %9
set "ERR=%ERRORLEVEL%"

echo.
if "%ERR%"=="0" (
  echo [OK] Installer generated under:
  echo   %SCRIPT_DIR%..\_Build\SD_SMA_Installer_Package
) else (
  echo [FAILED] Installer build failed. Exit code: %ERR%
  echo Scroll up to inspect the error details.
)

echo.
pause
endlocal & exit /b %ERR%

:failed
echo.
pause
endlocal & exit /b 2

:cancelled
endlocal & exit /b 0
