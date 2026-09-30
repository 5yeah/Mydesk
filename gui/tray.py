# -*- coding: utf-8 -*-
"""MyDesk 系统托盘

封装 QSystemTrayIcon: 托盘图标、右键菜单、双击/单击显示主窗口、
快速连接入口与退出。原先散落在 main.py 的 _setup_tray / _on_tray_activated
/ show_main_window / hide_main_window / quick_connect 全部收敛到此处。

设计: SystemTray 只负责"托盘交互", 业务动作通过回调 (on_quick_connect /
on_quit) 委托给 MyDeskApplication, 避免与具体业务逻辑耦合。
"""
from PyQt5.QtWidgets import (
    QSystemTrayIcon, QMenu, QAction, QInputDialog,
)
from PyQt5.QtCore import Qt

from . import icons, debug_log


class SystemTray(QSystemTrayIcon):
    def __init__(self, app, main_window, on_quick_connect, on_quit):
        """
        app:            QApplication 实例 (作为父对象, 防止 GC 误回收)
        main_window:    MainWindow 实例 (显示/隐藏目标)
        on_quick_connect: 回调 (device_id, password, mode) -> None
        on_quit:        回调 () -> None
        """
        super().__init__(icons.make_tray_icon(), app)
        self.app = app
        self.main_window = main_window
        self._on_quick_connect = on_quick_connect
        self._on_quit = on_quit

        # 远程桌面(RDP)/服务器会话可能没有系统托盘, 此时 show()/showMessage()
        # 会原生崩溃。先探测, 不可用则跳过托盘相关操作, 仅保留窗口本身。
        # 另外支持环境变量 MYDESK_NO_TRAY=1 强制跳过托盘(用于诊断/无托盘环境)。
        import os
        self._tray_ok = (
            QSystemTrayIcon.isSystemTrayAvailable()
            and os.environ.get('MYDESK_NO_TRAY') != '1'
        )
        if not self._tray_ok:
            reason = '环境变量 MYDESK_NO_TRAY=1' if os.environ.get('MYDESK_NO_TRAY') == '1' \
                else '系统托盘不可用(可能为远程桌面/无托盘会话)'
            debug_log(f'[MyDesk] 跳过系统托盘: {reason}')
            return

        self.setToolTip('MyDesk - 远程桌面 (双击打开)')
        self.show()

        self._build_menu()
        self.activated.connect(self._on_activated)

        self._safe_show_message(
            'MyDesk 已启动',
            '程序正在后台运行\n双击托盘图标打开主界面',
        )
        debug_log('[MyDesk] 系统托盘设置完成')

    def _safe_show_message(self, title, msg, timeout=3000):
        """托盘气泡提示, 失败静默忽略 (避免无托盘会话原生崩溃)"""
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.showMessage(title, msg, QSystemTrayIcon.Information, timeout)
        except Exception as e:
            debug_log(f'[MyDesk] 托盘消息发送失败(已忽略): {e}')

    def _build_menu(self):
        menu = QMenu()

        act_show = QAction('📂 显示主窗口', self.app)
        act_show.triggered.connect(self.show_main_window)
        menu.addAction(act_show)

        act_connect = QAction('🔌 快速连接', self.app)
        act_connect.triggered.connect(self.quick_connect)
        menu.addAction(act_connect)

        menu.addSeparator()

        act_quit = QAction('❌ 退出 MyDesk', self.app)
        act_quit.triggered.connect(self._on_quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_main_window()

    def show_main_window(self):
        # 与可正常打开的 test_minimal.py 保持一致:
        # 直接 show + 前置, 不做 QDesktopWidget 居中(多屏/特殊DPI下 move() 可能把
        # 窗口挪到屏外导致"看起来像闪退")。showNormal 确保从最小化/隐藏态恢复可见。
        self.main_window.showNormal()
        self.main_window.activateWindow()
        self.main_window.raise_()
        # 记录窗口实际位置/可见性, 便于排查"看起来闪退"是否仍是屏外
        try:
            g = self.main_window.geometry()
            debug_log(f'[MyDesk] 主窗口已显示 pos=({g.x()},{g.y()}) size=({g.width()}x{g.height()}) visible={self.main_window.isVisible()}')
        except Exception as e:
            debug_log(f'[MyDesk] 主窗口已显示(几何记录失败): {e}')

    def hide_main_window(self):
        self.main_window.hide()
        # 无托盘会话不调用 showMessage(会原生崩溃)
        if not getattr(self, '_tray_ok', False):
            return
        self.showMessage(
            'MyDesk 已最小化',
            '程序仍在后台运行\n双击托盘图标恢复窗口',
            QSystemTrayIcon.Information,
            2000,
        )

    def quick_connect(self):
        device_id, ok = QInputDialog.getText(None, '快速连接', '请输入设备ID (9位数字):')
        if ok and device_id:
            password, ok = QInputDialog.getText(None, '快速连接', '请输入访问密码:')
            if ok and password:
                self._on_quick_connect(device_id, password, 'control')
