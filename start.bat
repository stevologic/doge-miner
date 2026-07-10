@echo off
REM DogeMiner launcher for Windows (double-click friendly)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
