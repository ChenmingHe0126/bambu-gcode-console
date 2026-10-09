@echo off
title Bambu G-code Console
cd /d "%~dp0"

rem Find a real Python 3. "python" alone can be the Microsoft Store placeholder,
rem so prefer the "py" launcher that python.org installs, and verify it runs.
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
  echo [!] Python 3 was not found.
  echo     Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  echo     If a Microsoft Store window opened just now, close it - that is a placeholder, not Python.
  pause
  exit /b 1
)

%PY% -c "import paho.mqtt.client" >nul 2>nul
if errorlevel 1 (
  echo Installing the one dependency ^(paho-mqtt^)...
  %PY% -m pip install "paho-mqtt>=2.0"
  if errorlevel 1 (
    echo [!] Could not install paho-mqtt. Try in a terminal:  %PY% -m pip install --user "paho-mqtt>=2.0"
    pause
    exit /b 1
  )
)

%PY% bambu_web.py %*
echo.
echo Server stopped. The console only works while this window is open.
pause
