@echo off
title Bambu G-code Console
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install it from python.org and tick "Add Python to PATH".
  pause
  exit /b 1
)
python -c "import paho.mqtt" >nul 2>nul
if errorlevel 1 (
  echo Installing the one dependency ^(paho-mqtt^)...
  python -m pip install "paho-mqtt>=2.0"
)
python bambu_web.py %*
echo.
echo Server stopped. The console only works while this window is open.
pause
