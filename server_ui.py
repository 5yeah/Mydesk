# -*- coding: utf-8 -*-
"""MyDesk 被控端 - 带系统托盘UI

双击 EXE 时显示托盘图标,可查看设备信息、修改密码、开机自启管理。
--silent 模式(开机自启)时无托盘,纯后台运行。
"""
import sys
import os
import threading
from PyQt5.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QAction, QDialog,
    QFormLayout, QLineEdit, QPushButton, QVBoxLayout, QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from gui import crash_log
from gui.icons import make_tray_icon
from gui.theme import THEME

from controlled import Controlled
from config import get_device_id, get_password, get_port, set_password
from autostart import is_installed_registry, install_registry, uninstall_registry
from network import get_lan_ip, get_tailscale_ip


class ServerThread(QThread):
    """在后台线程运行被控端服务"""
    log_msg = pyqtSignal(str)

    def __init__(self, password=None, port=None):
        super().__init__()
        self.app = Controlled(password, port, on_log=lambda m: self.log_msg.emit(m))

    def run(self):
        try:
            self.app.start()
        except Exception as e:
            self.log_msg.emit(f'[错误] {e}')

    def stop(self):
        self.app.stop()


class PasswordDialog(QDialog):
    """修改密码对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('修改连接密码')
        self.setFixedSize(300, 150)
        self.new_pwd = None

        form = QFormLayout()
        self.in_pwd = QLineEdit()
        self.in_pwd.setEchoMode(QLineEdit.Password)
        form.addRow('新密码:', self.in_pwd)
        self.in_pwd2 = QLineEdit()
        self.in_pwd2.setEchoMode(QLineEdit.Password)
        form.addRow('确认密码:', self.in_pwd2)

        btn = QPushButton('确定')
        btn.clicked.connect(self._ok)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btn)

    def _ok(self):
        p1 = self.in_pwd.text()
        p2 = self.in_pwd2.text()
        if not p1:
            QMessageBox.warning(self, '提示', '密码不能为空')
            return
        if p1 != p2:
            QMessageBox.warning(self, '提示', '两次密码不一致')
            return
        self.new_pwd = p1
        self.accept()


def main():
    crash_log('server_ui 启动')
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    args = sys.argv[1:]

    # 静默模式: 无托盘,纯后台
    if '--silent' in args:
        from autostart import hide_console
        hide_console()
        pwd = None
        port = None
        for a in args:
            if not a.startswith('--'):
                if pwd is None:
                    pwd = a
                elif port is None:
                    try:
                        port = int(a)
                    except ValueError:
                        pass
        server = ServerThread(pwd, port)
        server.start()
        sys.exit(app.exec_())

    # ---- 托盘模式 ----
    device_id = get_device_id()
    password = get_password()
    port = get_port()
    lan_ip = get_lan_ip()
    ts_ip = get_tailscale_ip()

    tray = QSystemTrayIcon(make_tray_icon(THEME['success']))
    tray.setToolTip(f'MyDesk 被控端 | 设备ID: {device_id}')

    menu = QMenu()

    # 设备信息
    act_info = QAction(f'设备ID: {device_id}', menu)
    act_info.setEnabled(False)
    menu.addAction(act_info)

    act_pwd = QAction(f'连接密码: {password}', menu)
    act_pwd.setEnabled(False)
    menu.addAction(act_pwd)

    act_port = QAction(f'监听端口: {port}', menu)
    act_port.setEnabled(False)
    menu.addAction(act_port)

    menu.addSeparator()

    act_lan = QAction(f'局域网: {lan_ip}:{port}', menu)
    act_lan.setEnabled(False)
    menu.addAction(act_lan)

    if ts_ip:
        act_ts = QAction(f'Tailscale: {ts_ip}:{port}', menu)
        act_ts.setEnabled(False)
        menu.addAction(act_ts)

    menu.addSeparator()

    # 修改密码
    act_chg_pwd = QAction('修改密码...', menu)

    # 开机自启
    act_autostart = QAction('开机自启', menu)
    act_autostart.setCheckable(True)
    act_autostart.setChecked(is_installed_registry())

    # 退出
    act_quit = QAction('退出', menu)

    menu.addAction(act_chg_pwd)
    menu.addAction(act_autostart)
    menu.addSeparator()
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()

    # ---- 启动服务线程 ----
    server = ServerThread()
    server.log_msg.connect(lambda m: tray.setToolTip(m[:80]))
    server.start()

    # ---- 信号连接 ----
    def on_chg_pwd():
        dlg = PasswordDialog()
        if dlg.exec_() == QDialog.Accepted and dlg.new_pwd:
            set_password(dlg.new_pwd)
            QMessageBox.information(None, '成功', '密码已修改,新密码将在下次连接时生效')
            act_pwd.setText(f'连接密码: {dlg.new_pwd}')

    def on_autostart(checked):
        if checked:
            install_registry()
        else:
            uninstall_registry()

    def on_quit():
        server.stop()
        tray.hide()
        app.quit()

    act_chg_pwd.triggered.connect(on_chg_pwd)
    act_autostart.toggled.connect(on_autostart)
    act_quit.triggered.connect(on_quit)
    tray.activated.connect(
        lambda reason: menu.exec_(tray.geometry().center())
        if reason == QSystemTrayIcon.Trigger else None
    )

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
