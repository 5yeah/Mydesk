@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: ============================================================
::  MyDesk 一键启动脚本 - 双击即可运行主程序
::  配置区: 路径集中在此, 迁移时只改这里
:: ============================================================
set PYTHON=C:\Users\Administrator\.conda\envs\mydesk\python.exe
set ENTRY=main.py

echo ========================================
echo   MyDesk 启动中...
echo ========================================
echo.

if not exist "%PYTHON%" (
    echo [错误] 未找到 Python: %PYTHON%
    echo 请在上方配置区把 PYTHON 改成你机器上的 python.exe 路径
    pause
    exit /b 1
)

if not exist "%ENTRY%" (
    echo [错误] 未找到入口文件: %ENTRY%
    echo 请在上方配置区确认 ENTRY 指向正确的启动脚本
    pause
    exit /b 1
)

"%PYTHON%" "%ENTRY%" > "_launch.log" 2>&1
set RC=%ERRORLEVEL%

if %RC%==0 (
    echo.
    echo   MyDesk 已正常启动, GUI 窗口应已弹出
    echo   本控制台可关闭; 崩溃日志见 _launch.log
    exit /b 0
)

echo.
echo ========================================
echo   [MyDesk] 启动异常! 退出码=%RC%
echo   以下内容为崩溃日志 _launch.log:
echo ========================================
type "_launch.log"
echo.
echo --------------------------------------------------------
echo   另可查看: C:\Users\Administrator\MyDesk_Logs\faulthandler.log
echo             C:\Users\Administrator\MyDesk_Logs\crash.log
echo   请把以上日志内容反馈, 便于定位问题。
echo --------------------------------------------------------
pause
