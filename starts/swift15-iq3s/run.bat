@echo off
rem swift15-iq3s - live console output + new per-run log (server-YYYYMMDD-HHMMSS.log)
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server" >nul
if not errorlevel 1 taskkill /f /im llama-server.exe
echo Starting llama-server, model loads in 1-2 minutes...
echo A new log file (server-*.log) is created in this folder for each run.
echo Ctrl+C or close window to stop.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause