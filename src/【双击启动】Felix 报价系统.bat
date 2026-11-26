@echo off
chcp 65001 >nul
title Felix 报价系统
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
set "PYW=%~dp0runtime\python\pythonw.exe"

if not exist "%PY%" goto broken
if not exist "app\launcher.py" goto broken

if exist "data\.initialized" goto silent

"%PY%" -m app.launcher boot
if errorlevel 2 goto failed
if errorlevel 1 goto initfail
pause
exit /b 0

rem 静默启动：pythonw 无控制台，start /b 让其脱离当前 cmd 会话
:silent
start "FelixQuotation" /b "%PYW%" -m app.launcher tray-host
exit /b 0

:failed
echo.
echo   [Cannot start] System requirements not met. See messages above.
echo.
pause
exit /b 1

:initfail
echo.
echo   [Error] Data init failed - folder may not be writable.
echo          Please move this folder to D:/VG , NOT C:/Program Files.
echo.
pause
exit /b 1

:broken
echo.
echo   [Error] Extraction incomplete - missing runtime\python or app\launcher.py
echo.
echo   Please check:
echo     1. Extract the FULL archive (do not run files inside the zip)
echo     2. Do not skip damaged files
echo     3. Path must not contain Chinese chars or spaces - use D:/VG
echo.
pause
exit /b 1
