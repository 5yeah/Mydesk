# -*- coding: utf-8 -*-
"""MyDesk 输入执行模块 (被控端)

基于 Win32 SendInput API, 直接使用 Windows 虚拟键码 (VK_*),
延迟最低且支持全部按键/组合键。

MSG_INPUT 载荷格式 (事件类型定义见 protocol.EV_*):
  0x01 鼠标移动: [x 2B][y 2B]          (虚拟桌面绝对坐标)
  0x02 鼠标按下: [button 1B]            (1左 2右 3中)
  0x03 鼠标抬起: [button 1B]
  0x04 鼠标滚轮: [dx 2B][dy 2B]        (signed, 通常 dy=±120)
  0x05 键按下:   [vk 2B]
  0x06 键抬起:   [vk 2B]

坐标系说明 (多显示器关键):
  主控端发来的是 **虚拟桌面坐标** = 目标显示器的 left/top 偏移 + 该屏内的局部坐标。
  注入时按整个虚拟桌面的尺寸归一化并带上 MOUSEEVENTF_VIRTUALDESK,
  这样副屏上的点击才会落到副屏, 而不是被解释成主屏比例位置。
"""
import ctypes
import struct

# 事件类型从协议层引入 (同时 re-export, 供 viewer/controller 使用)
from protocol import (  # noqa: F401
    EV_MOUSE_MOVE, EV_MOUSE_DOWN, EV_MOUSE_UP, EV_MOUSE_SCROLL,
    EV_KEY_DOWN, EV_KEY_UP,
)

user32 = ctypes.windll.user32

# Win32 SendInput 常量
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002

# GetSystemMetrics 索引
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# 按钮 -> (按下标志, 抬起标志)
_BTN_MAP = {
    1: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    2: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    3: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]


# SendInput 的返回值检查: 0 表示被 UIPI/其它原因拒绝
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint


def _send_input(*inputs):
    """注入输入事件; 返回实际注入条数"""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    try:
        return user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    except Exception:
        return 0


def virtual_desktop_rect():
    """返回整个虚拟桌面 (所有显示器合并) 的 (left, top, width, height)。

    这是多显示器正确注入的关键: 绝对坐标必须相对整个虚拟桌面归一化。
    """
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if width <= 0 or height <= 0:
        # 兜底: 退化为主显示器
        left, top = 0, 0
        width = max(1, user32.GetSystemMetrics(SM_CXSCREEN))
        height = max(1, user32.GetSystemMetrics(SM_CYSCREEN))
    return left, top, width, height


def mouse_move(x, y):
    """移动鼠标到虚拟桌面坐标 (x, y)。

    要求被控进程为 DPI 感知(见 controlled.py 的 SetProcessDPIAware),
    否则 GetSystemMetrics 返回缩放后的逻辑尺寸, 会与 mss 捕获的物理分辨率错位。
    """
    try:
        vleft, vtop, vw, vh = virtual_desktop_rect()
        # 夹到虚拟桌面范围内, 防止越界坐标被系统四舍五入到错误的屏幕
        x = max(vleft, min(int(x), vleft + vw - 1))
        y = max(vtop, min(int(y), vtop + vh - 1))
        nx = int(round((x - vleft) * 65535 / (vw - 1))) if vw > 1 else 0
        ny = int(round((y - vtop) * 65535 / (vh - 1))) if vh > 1 else 0
        nx = max(0, min(65535, nx))
        ny = max(0, min(65535, ny))
        ok = _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(
            dx=nx, dy=ny,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)))
        if not ok:
            # SendInput 被拒(例如被更高完整性级别的前台窗口拦截)时退回 SetCursorPos
            user32.SetCursorPos(int(x), int(y))
    except Exception:
        try:
            user32.SetCursorPos(int(x), int(y))
        except Exception:
            pass


def mouse_button(button, down):
    flags = _BTN_MAP.get(button)
    if not flags:
        return
    flag = flags[0] if down else flags[1]
    _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(dwFlags=flag)))


def mouse_scroll(dx, dy):
    """滚轮: dy>0 向上, dy<0 向下; dx 为横向滚动 (单位 WHEEL_DELTA=120)"""
    if dy:
        data = int(dy) & 0xFFFFFFFF
        _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(
            mouseData=data, dwFlags=MOUSEEVENTF_WHEEL)))
    if dx:
        # 横向滚轮是独立事件(MOUSEEVENTF_HWHEEL), 不能和纵向合并成一条
        data = int(dx) & 0xFFFFFFFF
        _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(
            mouseData=data, dwFlags=MOUSEEVENTF_HWHEEL)))


def key_event(vk, down):
    flags = 0 if down else KEYEVENTF_KEYUP
    _send_input(INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(
        wVk=int(vk) & 0xFFFF, dwFlags=flags)))


def handle_input(payload):
    """解析并执行一条 MSG_INPUT 消息"""
    if not payload:
        return
    ev = payload[0]
    try:
        if ev == EV_MOUSE_MOVE and len(payload) >= 5:
            x, y = struct.unpack('<HH', payload[1:5])
            mouse_move(x, y)
        elif ev == EV_MOUSE_DOWN and len(payload) >= 2:
            mouse_button(payload[1], True)
        elif ev == EV_MOUSE_UP and len(payload) >= 2:
            mouse_button(payload[1], False)
        elif ev == EV_MOUSE_SCROLL and len(payload) >= 5:
            dx, dy = struct.unpack('<hh', payload[1:5])
            mouse_scroll(dx, dy)
        elif ev == EV_KEY_DOWN and len(payload) >= 3:
            vk = struct.unpack('<H', payload[1:3])[0]
            key_event(vk, True)
        elif ev == EV_KEY_UP and len(payload) >= 3:
            vk = struct.unpack('<H', payload[1:3])[0]
            key_event(vk, False)
    except Exception:
        # 输入执行失败属高频路径, 不写日志(由调用方的 debug_log 汇总)
        pass
