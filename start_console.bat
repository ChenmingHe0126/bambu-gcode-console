@echo off
title Bambu A1 Console
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install it from python.org and check "Add Python to PATH".
  pause
  exit /b 1
)
python bambu_web.py
echo.
echo Server stopped. The console only works while this window is open.
pause
