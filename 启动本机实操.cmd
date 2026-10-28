@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-web.ps1" -TrustedLocal -Port 8889
if errorlevel 1 pause
