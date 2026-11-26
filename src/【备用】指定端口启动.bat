@echo off
chcp 65001 >nul
title Felix 报价系统 - 指定端口
cd /d "%~dp0"
set "PY=runtime\python\python.exe"
set /p PORT=请输入端口号（默认 8765）:
if "%PORT%"=="" set "PORT=8765"
if not exist "%PY%" (echo [错误] 缺少 runtime\python & pause & exit /b 1)
echo 正在启动，端口 %PORT% ...
start "" "http://127.0.0.1:%PORT%/"
timeout /t 1 /nobreak >nul
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
pause
