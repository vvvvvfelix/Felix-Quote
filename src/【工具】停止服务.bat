@echo off
chcp 65001 >nul
title 停止服务
cd /d "%~dp0"
echo.
echo 正在停止 Felix 报价系统...
echo.
"runtime\python\python.exe" -c "import sys;sys.path.insert(0,'.');from app.launcher import stop_service;stop_service()"
echo 已停止。下次双击桌面图标即可重新启动。
echo.
timeout /t 3 >nul
