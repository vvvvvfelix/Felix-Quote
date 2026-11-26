@echo off
taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq FelixServer" >nul 2>&1
echo 已发送停止指令
