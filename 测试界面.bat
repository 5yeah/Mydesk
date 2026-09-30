@echo off
chcp 65001 >nul
echo 正在启动 MyDesk 测试程序...
echo.
echo 如果界面没有弹出：
echo   1. 右键点击此 bat 文件，选择"以管理员身份运行"
echo   2. 检查 Windows 是否阻止了程序
echo   3. 查看是否有错误提示
echo.

C:\Users\Administrator\.conda\envs\mydesk\python.exe "%~dp0test_minimal.py"

pause
