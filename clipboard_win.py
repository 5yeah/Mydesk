# -*- coding: utf-8 -*-
"""Win32 剪贴板读写 (纯 ctypes, 线程安全, 不依赖 Qt)

被控端的消息循环在后台线程中运行, 不能直接操作 QApplication.clipboard(),
本模块通过 Win32 API 完成剪贴板读写, 在 GUI 线程和非 GUI 线程都可用。

仅支持文本 (CF_UNICODETEXT)。
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 显式声明参数/返回值类型, 防止 64 位指针被截断
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def _open_clipboard():
    """打开剪贴板, 被占用时重试 (最多约 1 秒)"""
    for _ in range(50):
        if user32.OpenClipboard(None):
            return True
        kernel32.Sleep(20)
    return False


def get_text():
    """读取剪贴板文本; 无文本或读取失败返回 ''"""
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ''
    if not _open_clipboard():
        return ''
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ''
        p = kernel32.GlobalLock(h)
        if not p:
            return ''
        try:
            buf = ctypes.cast(p, ctypes.c_wchar_p)
            return buf.value or ''
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_text(text):
    """写入剪贴板文本; text 为空则清空剪贴板。成功返回 True"""
    if text is None:
        text = ''
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if text == '':
            return True  # 已清空
        # UTF-16LE 编码 (含结尾 NUL), 直接作为剪贴板数据
        data = (text + '\x00').encode('utf-16-le')
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        try:
            p = kernel32.GlobalLock(h)
            if not p:
                return False
            try:
                ctypes.memmove(p, data, len(data))
            finally:
                kernel32.GlobalUnlock(h)
            # 成功后剪贴板接管句柄所有权
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                return False
            h = None
        finally:
            if h:
                kernel32.GlobalFree(h)
        return True
    finally:
        user32.CloseClipboard()


if __name__ == '__main__':
    # 自测: 写入再读回 (控制台可能是 GBK, 避免打印非 ASCII 内容)
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    test = 'MyDesk clipboard self-test 剪贴板 🎯'
    ok = set_text(test)
    got = get_text()
    print(f'set_text -> {ok}')
    print(f'roundtrip_equal -> {got == test}')
    print('PASS' if ok and got == test else 'FAIL')