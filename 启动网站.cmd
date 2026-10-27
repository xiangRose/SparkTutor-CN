@echo off
setlocal
cd /d "%~dp0"
echo SparkTutor Web - http://localhost:8888
echo Keep this window open while using the website. Press Ctrl+C to stop.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-web.ps1" %*
if errorlevel 1 pause
endlocal
