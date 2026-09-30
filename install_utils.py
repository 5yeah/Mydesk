# -*- coding: utf-8 -*-
"""Windows 快捷方式创建与卸载注册工具"""
import os
import sys
import shutil
import winreg
import ctypes
from ctypes import wintypes

# ---------- COM 接口创建 .lnk 快捷方式 ----------
import pythoncom
from win32com.shell import shell, shellcon


def create_shortcut(lnk_path, target_path, arguments='', description='', icon_path=None):
    """创建 Windows .lnk 快捷方式"""
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    shell_link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    shell_link.SetPath(target_path)
    if arguments:
        shell_link.SetArguments(arguments)
    if description:
        shell_link.SetDescription(description)
    if icon_path and os.path.exists(icon_path):
        shell_link.SetIconLocation(icon_path, 0)
    # 工作目录设为目标所在目录
    shell_link.SetWorkingDirectory(os.path.dirname(target_path))
    persist = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
    persist.Save(lnk_path, True)


def desktop_path():
    """桌面路径"""
    return shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0)


def start_menu_path():
    """开始菜单程序路径"""
    p = shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0)
    return os.path.join(p, 'MyDesk')


def get_uninstall_key():
    """卸载注册表键路径"""
    return r'Software\Microsoft\Windows\CurrentVersion\Uninstall\MyDesk'


def register_uninstall(install_dir, exe_path, version='1.0.0'):
    """在注册表中注册卸载信息(控制面板可见)"""
    key = get_uninstall_key()
    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        winreg.SetValueEx(k, 'DisplayName', 0, winreg.REG_SZ, 'MyDesk 远程桌面')
        winreg.SetValueEx(k, 'DisplayVersion', 0, winreg.REG_SZ, version)
        winreg.SetValueEx(k, 'Publisher', 0, winreg.REG_SZ, 'MyDesk')
        winreg.SetValueEx(k, 'DisplayIcon', 0, winreg.REG_SZ, exe_path)
        winreg.SetValueEx(k, 'InstallLocation', 0, winreg.REG_SZ, install_dir)
        # 卸载命令: 运行 exe --uninstall
        winreg.SetValueEx(k, 'UninstallString', 0, winreg.REG_SZ,
                          f'"{exe_path}" --uninstall')
        winreg.SetValueEx(k, 'NoModify', 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, 'NoRepair', 0, winreg.REG_DWORD, 1)
        # 估算大小 (KB)
        try:
            size = sum(os.path.getsize(os.path.join(install_dir, f))
                       for f in os.listdir(install_dir)
                       if os.path.isfile(os.path.join(install_dir, f)))
            winreg.SetValueEx(k, 'EstimatedSize', 0, winreg.REG_DWORD, size // 1024)
        except Exception:
            pass


def unregister_uninstall():
    """移除卸载注册信息"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'Software\Microsoft\Windows\CurrentVersion\Uninstall',
                            0, winreg.KEY_SET_VALUE) as parent:
            winreg.DeleteKey(parent, 'MyDesk')
    except FileNotFoundError:
        pass
    except PermissionError:
        # 无管理员权限时尝试 HKCU
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Uninstall',
                                0, winreg.KEY_SET_VALUE) as parent:
                winreg.DeleteKey(parent, 'MyDesk')
        except Exception:
            pass


def is_admin():
    """检测是否具有管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_as_admin(exe_path, args=''):
    """请求管理员权限重启"""
    ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe_path, args, None, 1)
