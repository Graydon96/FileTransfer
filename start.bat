@echo off
chcp 65001 >nul 2>&1
title 局域网文件传输工具
cd /d "%~dp0"

echo ========================================
echo   正在检查 Python...
echo ========================================

:: 检查 Python 是否存在
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 未检测到 Python！
    echo.
    echo 请先安装 Python:
    echo   1. 访问 https://www.python.org/downloads/
    echo   2. 下载 Windows 安装包
    echo   3. 安装时务必勾选 "Add Python to PATH"
    echo   4. 重新运行 start.bat
    echo.
    pause
    exit /b 1
)

echo [OK] Python 已安装
python --version

echo.
echo ========================================
echo   正在安装依赖...
echo ========================================

pip install -r requirements.txt -q

echo.
echo ========================================
echo   正在启动服务...
echo ========================================
echo.

python main.py

pause
