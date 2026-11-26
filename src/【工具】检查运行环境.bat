@echo off
chcp 65001 >nul
title 环境自检
cd /d "%~dp0"
"runtime\python\python.exe" -m app.selfcheck
echo.
echo 诊断代码含义：
echo     OS_TOO_LOW / OS_UNSUPPORTED_BY_ENGINE   操作系统版本不支持图片识别
echo     RUNTIME_MISSING                        缺少 VC++ 运行库（下载地址见上）
echo     ENGINE_MISSING                         解压不完整或被杀毒软件隔离
echo     MODEL_MISSING                          模型文件被删除或解压不完整
echo     LOW_MEMORY                             内存不足，关闭其他程序后重试
echo     INFER_FAILED                           引擎初始化失败（系统过旧或被安全软件限制）
echo.
echo 以上任何一项图片识别问题，都不影响产品库、客户、
echo 报价单、PDF 导出、Excel/PDF 导入等功能。
echo.
pause
