# -*- coding: utf-8 -*-
"""MyDesk GUI 包

界面层统一组织:
  - logger   集中日志
  - theme    统一主题与样式表
  - icons    图标/托盘生成
  - widgets  共享控件
  - main_window  主控端主窗口
  - viewer_window 远程会话窗口 + 文件传输
  - tray      系统托盘
"""
from .logger import LOG_DIR, crash_log, debug_log, Logger
from .theme import THEME, MAIN_STYLE, VIEWER_STYLE, FLOAT_BAR_STYLE
from .tray import SystemTray
from .main_window import MainWindow
from .viewer_window import ViewerWindow, FileTransferDialog

__all__ = [
    'LOG_DIR', 'crash_log', 'debug_log', 'Logger',
    'THEME', 'MAIN_STYLE', 'VIEWER_STYLE', 'FLOAT_BAR_STYLE',
    'SystemTray', 'MainWindow', 'ViewerWindow', 'FileTransferDialog',
]
