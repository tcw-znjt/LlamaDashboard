@echo off
rem kvmem-swift-iq3xs - KVMem 启动脚本:llama-kvmem-server.exe + 每次运行新建日志
rem 单实例:两个受支持的 server 进程名只要还在跑就先结束(切换 profile 时避免双开)
tasklist /fi "imagename eq llama-kvmem-server.exe" 2>nul | find /i "llama-kvmem-server" >nul
if not errorlevel 1 taskkill /f /im llama-kvmem-server.exe
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server" >nul
if not errorlevel 1 taskkill /f /im llama-server.exe
echo Starting llama-kvmem-server (IQ3), model loads in 1-2 minutes...
echo A new log file (server-*.log) is created in this folder for each run.
echo Ctrl+C or close window to stop.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause
