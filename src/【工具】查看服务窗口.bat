@echo off
chcp 65001 >nul
title Felix 报价系统 - 后台服务
cd /d "%~dp0"
echo.
echo Felix 报价系统正在后台运行。
echo.
echo * 关闭本窗口不会停止服务（服务已独立运行）
echo * 要停止服务，请右键桌面右下角的金色托盘图标 -^> 停止服务
echo * 或双击本目录下的【工具】停止服务.bat
echo.
echo 访问地址：http://127.0.0.1:8765/
echo.
start "" http://127.0.0.1:8765/
timeout /t 5 >nul
