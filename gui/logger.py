# -*- coding: utf-8 -*-
"""MyDesk 集中日志模块

替代散落在各文件中的 _crash_log / _debug_log。
所有日志统一写入 ~/MyDesk_Logs/ 目录，线程安全。

带大小上限: 单个日志文件超过 MAX_BYTES 就轮转一次 (xxx.log -> xxx.log.1)。
旧实现只追加从不清理, 长期挂机后日志能涨到几百 MB。
"""
import os
import threading
import datetime

LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
_lock = threading.RLock()

# 单个日志文件上限 (2 MB) —— 状态类日志不需要保留更多
MAX_BYTES = 2 * 1024 * 1024
# 检查大小的频率 (每 N 次写入检查一次, 避免每次都 stat)
_CHECK_EVERY = 64
_write_count = {}


def _ensure_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def _rotate_if_needed(path):
    """超过上限就把当前文件滚动成 .1 (只保留一代, 避免占满磁盘)"""
    try:
        if not os.path.exists(path):
            return
        if os.path.getsize(path) < MAX_BYTES:
            return
        backup = path + '.1'
        try:
            if os.path.exists(backup):
                os.remove(backup)
        except OSError:
            pass
        os.replace(path, backup)
    except Exception:
        pass


def _write(path, line):
    """线程安全追加一行, 顺带做低频的大小检查与轮转"""
    try:
        with _lock:
            n = _write_count.get(path, 0) + 1
            _write_count[path] = n
            if n % _CHECK_EVERY == 0:
                _rotate_if_needed(path)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception:
        pass


def _log(name, msg, with_ts=True):
    try:
        _ensure_dir()
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        path = os.path.join(LOG_DIR, name)
        line = f'[{ts}] {msg}\n' if with_ts else f'{msg}\n'
        _write(path, line)
    except Exception:
        pass


def crash_log(msg):
    """致命/崩溃级日志 -> crash.log"""
    _log('crash.log', msg)


def debug_log(msg, name='app'):
    """一般调试日志 -> {name}_debug.log"""
    _log(f'{name}_debug.log', msg)


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
