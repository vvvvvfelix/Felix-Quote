@echo off
chcp 65001 >nul
set "WV_DB=E:\workbuddy\felix-quote\src\data\wv.db"
cd /d "%~dp0"
start "FelixServer" /b "%~dp0runtime\python\pythonw.exe" -m uvicorn app.main:app --port 8795
echo 服务已启动，访问 http://127.0.0.1:8795
timeout /t 3 >nul
