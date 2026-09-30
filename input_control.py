# -*- coding: utf-8 -*-
"""MyDesk 输入执行模块 (被控端)

基于 Win32 SendInput API, 直接使用 Windows 虚拟键码 (VK_*),
延迟最低且支持全部按键/组合键。

MSG_INPUT 载荷格式: [事件类型 1B][数据...]
  0x01 鼠标移动: [x 2B][y 2B]          (绝对坐标)
  0x02 鼠标按下: [button 1B]            (1左 2右 3中)
  0x03 鼠标抬起: [button 1B]
  0x04 鼠标滚轮: [dx 2B][dy 2B]        (signed, 通常 dy=±120)
  0x05 键按下:   [vk 2B]
  0x06 键抬起:   [vk 2B]
"""
import ctypes
import struct

user32 = ctypes.windll.user32

# 事件类型
EV_MOUSE_MOVE = 0x01
EV_MOUSE_DOWN = 0x02
EV_MOUSE_UP = 0x03
EV_MOUSE_SCROLL = 0x04
EV_KEY_DOWN = 0x05
EV_KEY_UP = 0x06

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
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002

# GetSystemMetrics 索引 (主显示器物理像素尺寸)
SM_CXSCREEN = 0
SM_CYSCREEN = 1
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


def _send_input(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def mouse_move(x, y):
    """绝对坐标移动鼠标。

    优先用 SendInput 绝对坐标(归一化到 0..65535, 相对主显示器), 比 SetCursorPos
    更可靠; 失败再退回 SetCursorPos。坐标按主显示器物理像素归一化 —— 因此要求
    进程为 DPI 感知(见 main.py 的高 DPI 缩放设置), 否则高 DPI 缩放下
    GetSystemMetrics 返回的是缩放后尺寸, 会与 mss 捕获的物理分辨率错位。
    """
    try:
        sw = user32.GetSystemMetrics(SM_CXSCREEN) or 1
        sh = user32.GetSystemMetrics(SM_CYSCREEN) or 1
        nx = max(0, min(65535, int(int(x) * 65535 / (sw - 1)))) if sw > 1 else 0
        ny = max(0, min(65535, int(int(y) * 65535 / (sh - 1)))) if sh > 1 else 0
        _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(
            dx=nx, dy=ny, dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)))
    except Exception:
        user32.SetCursorPos(int(x), int(y))


def mouse_button(button, down):
    flags = _BTN_MAP.get(button)
    if not flags:
        return
    flag = flags[0] if down else flags[1]
    _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(dwFlags=flag)))


def mouse_scroll(dx, dy):
    """滚轮, dy>0 向上, dy<0 向下 (单位为 WHEEL_DELTA=120)"""
    data = int(dy) & 0xFFFFFFFF
    _send_input(INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(mouseData=data, dwFlags=MOUSEEVENTF_WHEEL)))


def key_event(vk, down):
    flags = 0 if down else KEYEVENTF_KEYUP
    _send_input(INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=int(vk) & 0xFFFF, dwFlags=flags)))


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
    except Exception as e:
        print(f'[输入] 执行异常: {e}')
