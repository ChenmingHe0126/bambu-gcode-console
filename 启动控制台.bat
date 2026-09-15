@echo off
chcp 65001 >nul
title Bambu A1 控制台
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 没找到 Python:请先到 python.org 安装,并勾选 "Add Python to PATH"
  pause
  exit /b 1
)
python bambu_web.py
echo.
echo 服务已退出(窗口开着的时候控制台才能用)。
pause
