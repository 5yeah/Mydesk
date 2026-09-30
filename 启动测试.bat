@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   MyDesk 测试启动（有控制台）
echo ========================================
echo.
echo 如果界面没有弹出，请查看此窗口的错误信息
echo.

C:\Users\Administrator\.conda\envs\mydesk\python.exe run_test.py

pause
