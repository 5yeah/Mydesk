@echo off
chcp 65001 >nul
echo ========================================
echo   MyDesk 极简测试版（带控制台输出）
echo ========================================
echo.
echo 这个版本会显示详细的启动过程
echo 如果失败，会显示错误信息
echo.

cd /d "%~dp0"
C:\Users\Administrator\.conda\envs\mydesk\python.exe test_minimal.py

echo.
echo ========================================
echo   程序已退出
echo ========================================
pause
