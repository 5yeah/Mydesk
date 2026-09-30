# -*- coding: utf-8 -*-
"""MyDesk 集中日志模块

替代散落在各文件中的 _crash_log / _debug_log。
所有日志统一写入 ~/MyDesk_Logs/ 目录，线程安全。
"""
import os
import threading
import datetime

LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
_lock = threading.Lock()


def _ensure_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def crash_log(msg):
    """致命/崩溃级日志 -> crash.log"""
    try:
        _ensure_dir()
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        path = os.path.join(LOG_DIR, 'crash.log')
        with _lock:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f'[{ts}] {msg}\n')
    except Exception:
        pass


def debug_log(msg, name='app'):
    """一般调试日志 -> {name}_debug.log"""
    try:
        _ensure_dir()
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        path = os.path.join(LOG_DIR, f'{name}_debug.log')
        with _lock:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f'[{ts}] {msg}\n')
    except Exception:
        pass


class Logger:
    """按模块命名的日志记录器，便于在类中使用"""

    def __init__(self, name='app'):
        self.name = name

    def debug(self, msg):
        debug_log(msg, self.name)

    def info(self, msg):
        debug_log(msg, self.name)

    def warn(self, msg):
        debug_log(f'[WARN] {msg}', self.name)

    def error(self, msg):
        debug_log(f'[ERROR] {msg}', self.name)

    def crash(self, msg):
        crash_log(msg)
