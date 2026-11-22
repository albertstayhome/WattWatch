@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [WattWatch] Starting in debug console mode...
py app.py
pause
