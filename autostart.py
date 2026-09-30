# -*- coding: utf-8 -*-
"""MyDesk 无人值守模块: 开机自启 + 后台静默运行 + 崩溃保活

自启方式:
  1. 注册表 Run 键 (默认): 用户登录时启动, 无需管理员权限
  2. 任务计划程序 (可选): 开机即启动(无需登录), 崩溃自动重启, 需管理员权限

静默运行: 使用 pythonw.exe (无控制台窗口) + --silent 参数
"""
import os
import sys
import ctypes
import winreg

APP_NAME = 'MyDesk'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def _get_pythonw():
    """获取 pythonw.exe 路径 (无控制台窗口的 Python)"""
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, 'pythonw.exe')
    if os.path.exists(pythonw):
        return pythonw
    # conda 环境: pythonw 在同目录
    return sys.executable


def get_target_command(script_path=None):
    """构造后台静默运行命令 (自动识别 EXE 打包模式)"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后: 直接用 EXE + --silent
        return f'"{sys.executable}" --silent'
    pythonw = _get_pythonw()
    # 静默模式运行被控端 (server_ui.py 支持 --silent: 无托盘, 纯后台)
    script = os.path.abspath(script_path or os.path.join(os.path.dirname(__file__), 'server_ui.py'))
    return f'"{pythonw}" "{script}" --silent'


# ---------- 注册表自启 ----------
def install_registry():
    """安装注册表 Run 键自启 (用户登录时启动)"""
    cmd = get_target_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
    print(f'[自启] 已安装 (注册表 Run 键): {cmd}')
    return True


def uninstall_registry():
    """卸载注册表自启"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        print('[自启] 已卸载')
        return True
    except FileNotFoundError:
        print('[自启] 未找到自启项')
        return False


def is_installed_registry():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False


# ---------- 任务计划程序自启 (可选, 需管理员) ----------
def install_task_scheduler():
    """通过 schtasks 创建开机自启任务 (需管理员权限)"""
    import subprocess
    cmd = get_target_command()
    # /SC ONLOGON: 用户登录时; /RL HIGHEST: 最高权限
    result = subprocess.run(
        ['schtasks', '/Create', '/TN', APP_NAME, '/TR', f'"{cmd}"',
         '/SC', 'ONLOGON', '/RL', 'HIGHEST', '/F'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print('[自启] 任务计划已创建 (开机自启)')
        return True
    print(f'[自启] 任务计划创建失败: {result.stderr.strip()}')
    print('[自启] 请以管理员身份运行')
    return False


def uninstall_task_scheduler():
    import subprocess
    result = subprocess.run(
        ['schtasks', '/Delete', '/TN', APP_NAME, '/F'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print('[自启] 任务计划已删除')
        return True
    print(f'[自启] 删除失败: {result.stderr.strip()}')
    return False


# ---------- 静默运行 ----------
def hide_console():
    """隐藏控制台窗口 (被控端静默运行)"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


# ---------- 便捷方法 ----------
def install(use_task=False):
    if use_task:
        return install_task_scheduler()
    return install_registry()


def uninstall(use_task=False):
    if use_task:
        return uninstall_task_scheduler()
    return uninstall_registry()


if __name__ == '__main__':
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'install':
        install()
    elif action == 'install-task':
        install_task_scheduler()
    elif action == 'uninstall':
        uninstall()
    elif action == 'status':
        print(f'注册表自启: {"已安装" if is_installed_registry() else "未安装"}')
