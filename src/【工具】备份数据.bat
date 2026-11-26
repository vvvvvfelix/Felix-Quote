@echo off
chcp 65001 >nul
title 数据备份
cd /d "%~dp0"
if not exist backup mkdir backup
if not exist "data\wv.db" (
  echo 尚无数据文件，无需备份。
  pause & exit /b 0
)
set "STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
"runtime\python\python.exe" -c "import sqlite3;s=sqlite3.connect('data/wv.db');d=sqlite3.connect(r'backup\wv_%STAMP%.db');s.backup(d);d.close();s.close()"
echo.
echo 备份完成：backup\wv_%STAMP%.db
echo.
pause
