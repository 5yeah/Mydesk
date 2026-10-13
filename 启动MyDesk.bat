@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: ============================================================
::  MyDesk 一键启动脚本 - 双击即可运行主程序
::  配置区: 需要指定特定解释器时, 把 PYTHON 改成完整路径即可
::          留空则使用 PATH 里的 python
:: ============================================================
set PYTHON=python
set ENTRY=main.py

echo ========================================
echo   MyDesk 启动中...
echo ========================================
echo.

where "%PYTHON%" >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python: %PYTHON%
    echo 请安装 Python 3 并加入 PATH, 或在上方配置区把 PYTHON 改成完整路径
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
echo   另可查看: %USERPROFILE%\MyDesk_Logs\faulthandler.log
echo             %USERPROFILE%\MyDesk_Logs\crash.log
echo   请把以上日志内容反馈, 便于定位问题。
echo --------------------------------------------------------
pause
