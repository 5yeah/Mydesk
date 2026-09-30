# -*- coding: utf-8 -*-
"""MyDesk 主界面 (ToDesk 风格, 主色调 #1677ff)

重构自原 ui.py: 清理调试脚手架 (改用 gui.logger), 套用统一主题 (gui.theme),
并吸收原 unified_ui.py 的历史记录下拉与网络诊断能力。
逻辑 (设备列表/设置/发现) 保持不变。
"""

import os
import socket
import secrets
import threading
from datetime import datetime

from config import (
    get_password, set_password, get_device_id, get_port, get_discovery_port,
    get_history, add_history, remove_history, clear_history,
    load_settings, save_settings, read_json, atomic_write_json,
    DEVICE_LIST_FILE,
)

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QObject
from PyQt5.QtGui import QIcon, QPixmap, QColor, QFont, QPainter, QLinearGradient, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QListWidget, QListWidgetItem, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QCheckBox,
    QSpinBox, QComboBox, QSlider, QGroupBox, QFormLayout, QMenu, QAction,
    QSystemTrayIcon, QToolTip, QScrollArea, QGridLayout,
    QSizePolicy, QGraphicsDropShadowEffect, QFileDialog, QMessageBox, QInputDialog,
)

from .logger import LOG_DIR, crash_log, debug_log
from .theme import MAIN_STYLE, THEME
from .widgets import StatusIndicator, SidebarButton, ToggleSwitch

# 数据目录 (兼容旧路径)
DATA_DIR = os.path.dirname(DEVICE_LIST_FILE)


class MainWindow(QMainWindow):
    """MyDesk 主窗口"""

    # 信号
    connect_requested = pyqtSignal(str, str, str)  # 设备ID, 模式, 密码
    devices_refreshed = pyqtSignal()               # 后台探测完成 -> GUI 线程刷新
    diagnostics_ready = pyqtSignal(str)            # 网络诊断结果(文本)

    def __init__(self):
        try:
            crash_log('MainWindow.__init__ 开始')
            super().__init__()
            crash_log('MainWindow: super().__init__ 完成')
            self.setWindowTitle('MyDesk - 个人远程桌面')
            self.setMinimumSize(1100, 700)
            self.resize(1280, 800)
            crash_log('MainWindow: 设置样式表')
            self.setStyleSheet(MAIN_STYLE)

            self._my_device_id = get_device_id()
            crash_log('MainWindow: 加载密码')
            self._my_password = get_password()
            crash_log('MainWindow: 加载设备列表')
            self._device_list = self._load_device_list()
            self._online_devices = set()
            self._port = get_port()

            # 设备列表可能被后台连接线程与 GUI 线程同时读写 -> 统一加锁
            self._device_lock = threading.RLock()
            # 网络探测防重入: 上一轮没跑完就不再起新线程
            self._scanning = False
            # IP 地址只解析一次 (解析要走 socket, 不要每次重建页面都做)
            self._local_ip = None

            self._latency = 0
            self._rtt_source = None   # 由 MyDeskApplication 注入: () -> ms or None
            self._latency_timer = QTimer(self)
            self._latency_timer.timeout.connect(self._update_latency)

            crash_log('MainWindow: 开始 _build_ui')
            self._build_ui()
            crash_log('MainWindow: _build_ui 完成')

            # 后台探测完成后在 GUI 线程刷新界面
            self.devices_refreshed.connect(self._on_devices_refreshed)
            self.diagnostics_ready.connect(self._on_diagnostics_ready)

            crash_log('MainWindow: 开始 _start_discovery')
            self._start_discovery()
            crash_log('MainWindow: _start_discovery 完成')

            debug_log(f'[UI] 主窗口初始化完成, 设备ID: {self._my_device_id}', 'ui')
            crash_log('MainWindow.__init__ 完成')
        except Exception as e:
            import traceback
            crash_log(f'MainWindow.__init__ CRASH: {e}\n{traceback.format_exc()}')
            raise

    # ============ 供 MyDeskApplication 调用的公共接口 ============
    def set_rtt_source(self, fn):
        """注入真实延迟来源: fn() -> 毫秒 或 None"""
        self._rtt_source = fn

    def set_connect_status(self, text, error=False):
        """更新主页连接状态文字"""
        try:
            self.lbl_connect_status.setText(text)
            self.lbl_connect_status.setStyleSheet(
                'color: #ff4d4f; font-size: 12px;' if error
                else 'color: #1677ff; font-size: 12px;')
        except Exception:
            pass

    def reset_connect_button(self):
        try:
            self.btn_connect.setEnabled(True)
            self.btn_connect.setText('🔌 连接')
        except Exception:
            pass

    def update_device_ip(self, device_id, ip):
        """更新设备记录里的地址 (可能由后台线程调用: 只操作数据 + 原子写文件)"""
        with self._device_lock:
            for device in self._device_list:
                if device.get('id') == device_id:
                    device['ip'] = ip
                    device['online'] = True
                    self._save_device_list()
                    return

    def add_history_entry(self, target, password, host=None):
        """记录一条连接历史 (可能由后台线程调用)"""
        try:
            add_history(target, password, host)
        except Exception as e:
            debug_log(f'[UI] 写入历史记录失败: {e}', 'ui')

    def _generate_password(self):
        """生成随机访问密码 (用 secrets 而非 random: 这是凭据, 必须是密码学安全随机)"""
        alphabet = 'abcdefghijkmnopqrstuvwxyz23456789'   # 去掉易混淆的 l/1/0/o
        return ''.join(secrets.choice(alphabet) for _ in range(9))

    def _load_device_list(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            data = read_json(DEVICE_LIST_FILE)
            if isinstance(data, list):
                return data
        except Exception as e:
            debug_log(f'[UI] 加载设备列表失败: {e}', 'ui')
        return []

    def _save_device_list(self):
        """原子写入设备列表 (由 self._device_lock 保护)"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with self._device_lock:
                snapshot = list(self._device_list)
            atomic_write_json(DEVICE_LIST_FILE, snapshot)
        except Exception as e:
            debug_log(f'[UI] 保存设备列表失败: {e}', 'ui')

    def _build_ui(self):
        crash_log('_build_ui: 开始')
        central = QWidget()
        crash_log('_build_ui: QWidget 创建完成')
        self.setCentralWidget(central)
        crash_log('_build_ui: setCentralWidget 完成')

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        crash_log('_build_ui: 创建 Sidebar')
        sidebar = QFrame()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 20, 12, 20)
        sidebar_layout.setSpacing(8)

        logo_label = QLabel('MyDesk')
        logo_label.setStyleSheet('font-size: 24px; font-weight: bold; color: #1677ff; padding: 10px 0 20px 0;')
        logo_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(logo_label)

        ver_label = QLabel('v1.0.0')
        ver_label.setStyleSheet('color: #999; font-size: 12px; text-align: center;')
        ver_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(ver_label)

        sidebar_layout.addSpacing(20)

        crash_log('_build_ui: 创建导航按钮')
        self._nav_buttons = {}
        nav_items = [
            ('🏠', '远程控制', 'home'),
            ('📋', '设备列表', 'devices'),
            ('⚙️', '高级设置', 'settings'),
        ]

        for icon, text, page_id in nav_items:
            crash_log(f'_build_ui: 创建按钮 {page_id}')
            btn = SidebarButton(icon, text, page_id)
            btn.nav_clicked.connect(self._on_nav_clicked)
            self._nav_buttons[page_id] = btn
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        copyright_label = QLabel('仅供个人使用')
        copyright_label.setStyleSheet('color: #ccc; font-size: 11px;')
        copyright_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(copyright_label)

        main_layout.addWidget(sidebar)

        crash_log('_build_ui: 创建右侧内容区')
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName('TopBar')
        topbar.setFixedHeight(50)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(20, 0, 20, 0)

        self.lbl_page_title = QLabel('远程控制')
        self.lbl_page_title.setStyleSheet('font-size: 18px; font-weight: bold; color: #333;')
        topbar_layout.addWidget(self.lbl_page_title)

        topbar_layout.addStretch()

        crash_log('_build_ui: 创建 StatusIndicator')
        self.status_indicator = StatusIndicator()
        self.status_indicator.set_color('#52c41a')
        topbar_layout.addWidget(self.status_indicator)

        self.lbl_status_text = QLabel('在线')
        self.lbl_status_text.setStyleSheet('color: #52c41a; font-size: 13px; margin-right: 15px;')
        topbar_layout.addWidget(self.lbl_status_text)

        self.lbl_latency = QLabel('延迟: --ms')
        self.lbl_latency.setStyleSheet('color: #666; font-size: 13px; margin-right: 15px;')
        topbar_layout.addWidget(self.lbl_latency)

        btn_refresh = QPushButton('🔄')
        btn_refresh.setFixedSize(32, 32)
        btn_refresh.setStyleSheet('background: transparent; border: none; border-radius: 6px; font-size: 16px;')
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._refresh_current_page)
        topbar_layout.addWidget(btn_refresh)

        content_layout.addWidget(topbar)

        crash_log('_build_ui: 构建页面堆栈')
        self._stack = QStackedWidget()
        crash_log('_build_ui: 构建首页')
        self._stack.addWidget(self._build_home_page())
        crash_log('_build_ui: 构建设备列表页')
        self._stack.addWidget(self._build_devices_page())
        crash_log('_build_ui: 构建设置页')
        self._stack.addWidget(self._build_settings_page())
        content_layout.addWidget(self._stack)

        main_layout.addWidget(content, 1)

        self._nav_buttons['home'].setChecked(True)
        crash_log('_build_ui: 完成')

    def _on_nav_clicked(self, page_id):
        page_map = {'home': 0, 'devices': 1, 'settings': 2}
        title_map = {'home': '远程控制', 'devices': '设备列表', 'settings': '高级设置'}

        for pid, btn in self._nav_buttons.items():
            btn.setChecked(pid == page_id)

        if page_id in page_map:
            self._stack.setCurrentIndex(page_map[page_id])
            self.lbl_page_title.setText(title_map[page_id])

    def _build_home_page(self):
        crash_log('_build_home_page: 开始')
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_scroll.setStyleSheet('QScrollArea { border: none; background: #f5f6fa; }')

        page = QWidget()
        page.setStyleSheet('background: #f5f6fa;')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        crash_log('_build_home_page: 创建本机信息卡片')
        self_my_card = QFrame()
        crash_log('_build_home_page: QFrame 创建完成')
        self_my_card.setObjectName('Card')
        card_layout = QVBoxLayout(self_my_card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(20, 15, 20, 15)

        title = QLabel('📱 本机设备信息')
        title.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        card_layout.addWidget(title)

        crash_log('_build_home_page: 创建设备ID行')
        id_row = QHBoxLayout()
        id_label = QLabel('设备代码:')
        id_label.setFixedWidth(70)
        id_label.setStyleSheet('color: #666; font-size: 13px;')
        id_row.addWidget(id_label)

        crash_log('_build_home_page: 调用 _format_id')
        self.lbl_my_id = QLabel(self._format_id(self._my_device_id))
        crash_log('_build_home_page: QLabel(id) 创建完成')
        self.lbl_my_id.setStyleSheet('font-size: 20px; font-weight: bold; color: #1677ff;')
        crash_log('_build_home_page: QLabel(id) 样式设置完成')
        id_row.addWidget(self.lbl_my_id)
        crash_log('_build_home_page: id_row.addWidget 完成')

        btn_copy_id = QPushButton('📋')
        crash_log('_build_home_page: btn_copy_id 创建完成')
        btn_copy_id.setFixedSize(28, 28)
        btn_copy_id.setToolTip('复制设备ID')
        btn_copy_id.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_id.setCursor(Qt.PointingHandCursor)
        btn_copy_id.clicked.connect(lambda: self._copy_to_clipboard(self._my_device_id))
        id_row.addWidget(btn_copy_id)
        crash_log('_build_home_page: btn_copy_id 添加完成')

        id_row.addStretch()
        card_layout.addLayout(id_row)
        crash_log('_build_home_page: id_row 添加到 card_layout')

        pwd_row = QHBoxLayout()
        pwd_label = QLabel('访问密码:')
        pwd_label.setFixedWidth(70)
        pwd_label.setStyleSheet('color: #666; font-size: 13px;')
        pwd_row.addWidget(pwd_label)
        crash_log('_build_home_page: 密码行创建')

        self.lbl_my_password = QLabel(self._my_password)
        self.lbl_my_password.setStyleSheet('font-size: 16px; font-weight: bold; color: #ff6b00;')
        pwd_row.addWidget(self.lbl_my_password)
        crash_log('_build_home_page: 密码Label创建')

        btn_copy_pwd = QPushButton('📋')
        btn_copy_pwd.setFixedSize(28, 28)
        btn_copy_pwd.setToolTip('复制密码')
        btn_copy_pwd.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_pwd.setCursor(Qt.PointingHandCursor)
        btn_copy_pwd.clicked.connect(lambda: self._copy_to_clipboard(self._my_password))
        pwd_row.addWidget(btn_copy_pwd)
        crash_log('_build_home_page: 复制密码按钮创建')

        btn_refresh_pwd = QPushButton('刷新')
        btn_refresh_pwd.setStyleSheet('background: transparent; border: 1px solid #e8e8e8; border-radius: 4px; padding: 2px 10px; color: #666; font-size: 12px;')
        btn_refresh_pwd.setCursor(Qt.PointingHandCursor)
        btn_refresh_pwd.clicked.connect(self._refresh_password)
        pwd_row.addWidget(btn_refresh_pwd)

        btn_edit_pwd = QPushButton('编辑')
        btn_edit_pwd.setStyleSheet('background: transparent; border: 1px solid #1677ff; border-radius: 4px; padding: 2px 10px; color: #1677ff; font-size: 12px;')
        btn_edit_pwd.setCursor(Qt.PointingHandCursor)
        btn_edit_pwd.clicked.connect(self._edit_password)
        pwd_row.addWidget(btn_edit_pwd)
        crash_log('_build_home_page: 刷新密码按钮创建')

        pwd_row.addStretch()
        card_layout.addLayout(pwd_row)
        crash_log('_build_home_page: 密码行添加完成')

        ip_row = QHBoxLayout()
        ip_label = QLabel('IP 地址:')
        ip_label.setFixedWidth(70)
        ip_label.setStyleSheet('color: #666; font-size: 13px;')
        ip_row.addWidget(ip_label)
        crash_log('_build_home_page: IP行创建, 准备获取IP')

        self.lbl_my_ip = QLabel(self._get_local_ip())
        crash_log('_build_home_page: IP获取完成')
        self.lbl_my_ip.setStyleSheet('font-size: 13px; color: #333;')
        ip_row.addWidget(self.lbl_my_ip)
        crash_log('_build_home_page: IP Label添加完成')

        btn_copy_ip = QPushButton('📋')
        btn_copy_ip.setFixedSize(28, 28)
        btn_copy_ip.setToolTip('复制IP地址')
        btn_copy_ip.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_ip.setCursor(Qt.PointingHandCursor)
        btn_copy_ip.clicked.connect(lambda: self._copy_to_clipboard(self.lbl_my_ip.text()))
        ip_row.addWidget(btn_copy_ip)
        crash_log('_build_home_page: btn_copy_ip 创建完成')

        ip_row.addStretch()
        card_layout.addLayout(ip_row)
        crash_log('_build_home_page: ip_row 添加完成')

        # 安全提示: 必须与事实一致。
        # 现状: 认证已改为挑战-响应(每次连接 nonce 不同, 抓到包也无法重放),
        # 但画面/输入/文件数据仍是明文传输 —— 局域网内可被嗅探。
        # 因此这里如实说明, 不再宣称"端到端加密已启用"。
        pwd_weak = (self._my_password == '123456' or len(self._my_password) < 8)
        if pwd_weak:
            security_tip = QLabel(
                '⚠️ 认证已启用防重放, 但数据未加密(局域网可被嗅探); '
                '当前连接密码较弱, 建议点「编辑」改成 8 位以上')
            security_tip.setStyleSheet(
                'color: #d46b08; font-size: 12px; padding: 8px; '
                'background-color: #fff7e6; border-radius: 6px;')
        else:
            security_tip = QLabel(
                '🔑 认证已启用挑战-响应(防重放), 数据未加密; '
                '请只在可信网络(局域网 / Tailscale)中使用')
            security_tip.setStyleSheet(
                'color: #8c8c8c; font-size: 12px; padding: 8px; '
                'background-color: #fafafa; border-radius: 6px;')
        security_tip.setWordWrap(True)
        card_layout.addWidget(security_tip)
        crash_log('_build_home_page: security_tip 创建完成')

        layout.addWidget(self_my_card)
        crash_log('_build_home_page: self_my_card 添加到 layout')

        crash_log('_build_home_page: 创建连接卡片')
        connect_card = QFrame()
        connect_card.setObjectName('Card')
        card_layout2 = QVBoxLayout(connect_card)
        card_layout2.setSpacing(10)
        card_layout2.setContentsMargins(20, 15, 20, 15)
        crash_log('_build_home_page: connect_card 创建完成')

        title2 = QLabel('🔗 连接远程设备')
        title2.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        card_layout2.addWidget(title2)
        crash_log('_build_home_page: title2 创建完成')

        # ===== 历史连接 (吸收自 unified_ui) =====
        hist_label = QLabel('🕘 历史连接:')
        hist_label.setStyleSheet('color: #666; font-size: 13px;')
        card_layout2.addWidget(hist_label)

        hist_row = QHBoxLayout()
        self.input_history = QComboBox()
        self.input_history.setMinimumHeight(36)
        self.input_history.setStyleSheet("""
            QComboBox {
                background: #ffffff; border: 1px solid #d9d9d9;
                border-radius: 8px; padding: 7px 12px; font-size: 14px;
            }
            QComboBox:hover { border: 1px solid #1677ff; }
            QComboBox::drop-down { border: none; width: 30px; }
            QComboBox QAbstractItemView {
                border: 1px solid #d1d5db; border-radius: 8px;
                background: white; selection-background-color: #e6f4ff;
            }
        """)
        self.input_history.setPlaceholderText('选择历史记录或手动输入')
        self.input_history.currentIndexChanged.connect(self._on_history_selected)
        hist_row.addWidget(self.input_history, 1)

        self.btn_clear_history = QPushButton('🗑')
        self.btn_clear_history.setFixedSize(36, 36)
        self.btn_clear_history.setToolTip('清空历史记录')
        self.btn_clear_history.clicked.connect(self._clear_history)
        hist_row.addWidget(self.btn_clear_history)
        card_layout2.addLayout(hist_row)
        self._refresh_history()

        crash_log('_build_home_page: 开始 mode_switch_row')
        mode_switch_row = QHBoxLayout()
        mode_switch_row.setSpacing(6)
        self.btn_mode_id = QPushButton('设备ID')
        self.btn_mode_id.setCheckable(True)
        self.btn_mode_id.setChecked(True)
        self.btn_mode_id.setFixedSize(88, 32)
        self.btn_mode_id.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 6px; font-size: 13px;')
        self.btn_mode_id.clicked.connect(lambda: self._set_conn_mode('id'))
        mode_switch_row.addWidget(self.btn_mode_id)
        crash_log('_build_home_page: btn_mode_id 创建完成')

        self.btn_mode_ip = QPushButton('IP地址')
        self.btn_mode_ip.setCheckable(True)
        self.btn_mode_ip.setFixedSize(88, 32)
        self.btn_mode_ip.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; font-size: 13px;')
        self.btn_mode_ip.clicked.connect(lambda: self._set_conn_mode('ip'))
        mode_switch_row.addWidget(self.btn_mode_ip)
        mode_switch_row.addStretch()
        card_layout2.addLayout(mode_switch_row)
        crash_log('_build_home_page: mode_switch_row 完成')

        crash_log('_build_home_page: 开始 id_input_row')
        id_input_row = QHBoxLayout()
        self.id_input_label = QLabel('设备代码:')
        self.id_input_label.setFixedWidth(70)
        self.id_input_label.setStyleSheet('color: #666; font-size: 13px;')
        id_input_row.addWidget(self.id_input_label)

        self.input_device_id = QLineEdit()
        self.input_device_id.setPlaceholderText('请输入9位设备ID 或 IP地址')
        self.input_device_id.setFixedHeight(36)
        self.input_device_id.setStyleSheet('font-size: 14px;')
        id_input_row.addWidget(self.input_device_id, 1)
        card_layout2.addLayout(id_input_row)
        crash_log('_build_home_page: id_input_row 完成')

        crash_log('_build_home_page: 开始 pwd_input_row')
        pwd_input_row = QHBoxLayout()
        pwd_input_label = QLabel('访问密码:')
        pwd_input_label.setFixedWidth(70)
        pwd_input_label.setStyleSheet('color: #666; font-size: 13px;')
        pwd_input_row.addWidget(pwd_input_label)

        self.input_password = QLineEdit()
        self.input_password.setPlaceholderText('请输入访问密码')
        self.input_password.setFixedHeight(36)
        self.input_password.setEchoMode(QLineEdit.Password)
        pwd_input_row.addWidget(self.input_password, 1)
        card_layout2.addLayout(pwd_input_row)
        crash_log('_build_home_page: pwd_input_row 完成')

        crash_log('_build_home_page: 开始 mode_row')
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QLabel('连接模式:')
        mode_label.setFixedWidth(70)
        mode_label.setStyleSheet('color: #666; font-size: 13px;')
        mode_row.addWidget(mode_label)

        self.mode_control = QPushButton('远程控制')
        self.mode_control.setCheckable(True)
        self.mode_control.setChecked(True)
        self.mode_control.setFixedHeight(36)
        self.mode_control.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 6px; padding: 0 18px; font-size: 13px;')
        self.mode_control.clicked.connect(lambda: self._set_mode('control'))
        mode_row.addWidget(self.mode_control)
        crash_log('_build_home_page: mode_control 完成')

        self.mode_file = QPushButton('文件传输')
        self.mode_file.setCheckable(True)
        self.mode_file.setFixedHeight(36)
        self.mode_file.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; padding: 0 18px; font-size: 13px;')
        self.mode_file.clicked.connect(lambda: self._set_mode('file'))
        mode_row.addWidget(self.mode_file)
        crash_log('_build_home_page: mode_file 完成')

        self.mode_view = QPushButton('仅观看')
        self.mode_view.setCheckable(True)
        self.mode_view.setFixedHeight(36)
        self.mode_view.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; padding: 0 18px; font-size: 13px;')
        self.mode_view.clicked.connect(lambda: self._set_mode('view'))
        mode_row.addWidget(self.mode_view)
        crash_log('_build_home_page: mode_view 完成')

        mode_row.addStretch()
        card_layout2.addLayout(mode_row)
        crash_log('_build_home_page: mode_row 完成')

        crash_log('_build_home_page: 开始 btn_connect')
        self.btn_connect = QPushButton('连接')
        self.btn_connect.setObjectName('PrimaryBtn')
        self.btn_connect.setFixedHeight(42)
        self.btn_connect.setStyleSheet('background-color: #1677ff; border: none; border-radius: 8px; padding: 10px 48px; color: white; font-size: 15px; font-weight: bold;')
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        card_layout2.addWidget(self.btn_connect)
        crash_log('_build_home_page: btn_connect 完成')

        self.lbl_connect_status = QLabel('')
        self.lbl_connect_status.setStyleSheet('color: #666; font-size: 12px;')
        self.lbl_connect_status.setAlignment(Qt.AlignCenter)
        card_layout2.addWidget(self.lbl_connect_status)
        crash_log('_build_home_page: lbl_connect_status 完成')

        # ===== 网络诊断 (吸收自 unified_ui) =====
        self.btn_diag = QPushButton('🔧 网络诊断')
        self.btn_diag.setStyleSheet('background: transparent; border: 1px solid #1677ff; border-radius: 4px; padding: 6px 14px; color: #1677ff; font-size: 12px;')
        self.btn_diag.setCursor(Qt.PointingHandCursor)
        self.btn_diag.clicked.connect(self._run_network_diagnostics)
        card_layout2.addWidget(self.btn_diag)

        layout.addWidget(connect_card)
        crash_log('_build_home_page: connect_card 添加到 layout')

        # 最近连接: 卡片常驻, 内容放进 self.recent_layout 以便局部刷新。
        # (旧版是"有设备才建卡片、删设备就整页重建", 结果每次删设备都会把
        #  用户已经填好的设备ID/密码清空, 还要重跑一次阻塞式取IP。)
        recent_card = QFrame()
        recent_card.setObjectName('Card')
        recent_card_layout = QVBoxLayout(recent_card)
        recent_card_layout.setSpacing(8)
        recent_card_layout.setContentsMargins(20, 15, 20, 15)

        title3 = QLabel('🕐 最近连接')
        title3.setStyleSheet('font-size: 14px; font-weight: bold; color: #333;')
        recent_card_layout.addWidget(title3)

        self.recent_scroll = QScrollArea()
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent_scroll.setMaximumHeight(200)
        self.recent_scroll.setStyleSheet('QScrollArea { border: none; }')

        recent_content = QWidget()
        self.recent_layout = QVBoxLayout(recent_content)
        self.recent_layout.setSpacing(6)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_scroll.setWidget(recent_content)
        recent_card_layout.addWidget(self.recent_scroll)

        self.recent_empty_label = QLabel('暂无记录，连接一次后会显示在这里')
        self.recent_empty_label.setStyleSheet('color: #bbb; font-size: 12px;')
        self.recent_layout.addWidget(self.recent_empty_label)
        self.recent_layout.addStretch()

        layout.addWidget(recent_card)
        self._refresh_recent_devices()

        layout.addStretch()
        crash_log('_build_home_page: layout.addStretch 完成')

        main_scroll.setWidget(page)
        outer_layout.addWidget(main_scroll)
        crash_log('_build_home_page: scroll.setWidget 完成')

        crash_log('_build_home_page: 返回 outer')
        return outer

    def _create_recent_device_item(self, device):
        item_widget = QFrame()
        item_widget.setObjectName('DeviceItem')
        item_layout = QHBoxLayout(item_widget)
        item_layout.setSpacing(12)

        status = StatusIndicator()
        status.set_color('#52c41a' if device.get('online', False) else '#d9d9d9')
        item_layout.addWidget(status)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        name_label = QLabel(device.get('name', '未知设备'))
        name_label.setStyleSheet('font-weight: bold; color: #333;')
        info_layout.addWidget(name_label)

        id_label = QLabel(f"ID: {device.get('id', '-----')} | {device.get('ip', '-----')}")
        id_label.setStyleSheet('color: #999; font-size: 12px;')
        info_layout.addWidget(id_label)

        item_layout.addLayout(info_layout, 1)

        btn_connect = QPushButton('连接')
        btn_connect.setObjectName('PrimaryBtn')
        btn_connect.setStyleSheet('''
            QPushButton {
                background-color: #1677ff;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                color: white;
                font-size: 12px;
            }
        ''')
        btn_connect.clicked.connect(lambda: self._quick_connect(device))
        item_layout.addWidget(btn_connect)

        return item_widget

    def _set_mode(self, mode):
        self._current_mode = mode
        active_style = 'background-color: #1677ff; color: white; border: none; border-radius: 6px; padding: 6px 14px; font-size: 13px; font-weight: bold;'
        inactive_style = 'background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; padding: 6px 14px; font-size: 13px;'
        self.mode_control.setChecked(mode == 'control')
        self.mode_file.setChecked(mode == 'file')
        self.mode_view.setChecked(mode == 'view')
        self.mode_control.setStyleSheet(active_style if mode == 'control' else inactive_style)
        self.mode_file.setStyleSheet(active_style if mode == 'file' else inactive_style)
        self.mode_view.setStyleSheet(active_style if mode == 'view' else inactive_style)

    def _set_conn_mode(self, mode):
        if mode == 'id':
            self.btn_mode_id.setChecked(True)
            self.btn_mode_ip.setChecked(False)
            self.btn_mode_id.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 4px; font-size: 12px; font-weight: bold;')
            self.btn_mode_ip.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 12px;')
            self.id_input_label.setText('设备代码:')
            self.input_device_id.setPlaceholderText('请输入9位设备ID')
            self.input_device_id.setMaxLength(9)
        else:
            self.btn_mode_id.setChecked(False)
            self.btn_mode_ip.setChecked(True)
            self.btn_mode_id.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 12px;')
            self.btn_mode_ip.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 4px; font-size: 12px; font-weight: bold;')
            self.id_input_label.setText('IP 地址:')
            self.input_device_id.setPlaceholderText('输入IP (如 192.168.1.100 或 192.168.1.100:7788)')
            self.input_device_id.setMaxLength(25)

    def _on_connect_clicked(self):
        device_id = self.input_device_id.text().strip()
        password = self.input_password.text().strip()

        if not device_id:
            self.set_connect_status('⚠️ 请输入设备ID或IP地址', error=True)
            return

        if not password:
            self.set_connect_status('⚠️ 请输入访问密码', error=True)
            return

        self.set_connect_status('⏳ 正在连接...')
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText('连接中...')

        # 不做"3 秒后无条件复位"了: 那会让慢速连接(跨网/Tailscale)的按钮
        # 在真正连上之前又变回可点, 重复点击会覆盖正在进行的 Controller。
        # 现在由 _on_session_ready / _on_connect_failed 明确复位。
        self._do_connect(device_id, password)

    def _do_connect(self, device_id, password):
        mode = getattr(self, '_current_mode', 'control')
        self.connect_requested.emit(device_id, mode, password)

    def _reset_connect_button(self):
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText('🔌 连接')

    def _quick_connect(self, device):
        self.input_device_id.setText(device.get('id', ''))
        self.input_password.setText(self._device_password(device))
        self._on_connect_clicked()

    @staticmethod
    def _device_password(device):
        """读取设备记录里的密码 (可能是 DPAPI 密文)"""
        from config import unprotect_secret
        try:
            return unprotect_secret(device.get('password', '') or '')
        except Exception:
            return device.get('password', '') or ''

    def _add_to_device_list(self, device_id, ip=None):
        """把一次连接尝试记入设备列表。

        注意: 这里 **不再** 无条件把设备标记为 online —— "点过连接"不等于"在线",
        真正的在线状态由 _check_devices_online 探测 / 连接成功后的 update_device_ip 决定。
        """
        from config import protect_secret
        is_ip = '.' in device_id and device_id.replace('.', '').isdigit()
        pwd = self.input_password.text()

        with self._device_lock:
            for device in self._device_list:
                id_match = device.get('id') == device_id
                ip_match = ip and device.get('ip') == ip and ip != '局域网'
                if is_ip and device.get('ip') == device_id:
                    ip_match = True

                if id_match or ip_match:
                    device['last_connected'] = datetime.now().isoformat()
                    if ip and ip != '局域网':
                        device['ip'] = ip
                    if not is_ip:
                        device['id'] = device_id
                    if pwd:
                        # 每次连接都刷新密码, 否则对方改密后永远连不上且无提示
                        device['password'] = protect_secret(pwd)
                    self._save_device_list()
                    return

            self._device_list.append({
                'id': device_id,
                'name': f'设备 {device_id}',
                'ip': ip or '局域网',
                'online': False,
                'favorite': False,
                'last_connected': datetime.now().isoformat(),
                'password': protect_secret(pwd) if pwd else '',
            })
            self._save_device_list()

    def _check_devices_online(self):
        """探测设备在线状态 (在工作线程中调用)

        修正两点:
          1. 端口取设备记录里的真实端口 (device['ip'] 形如 "1.2.3.4:7789"),
             旧实现硬编码 7788 —— 端口漂移过的设备会永远显示离线。
          2. 并发探测 + 单设备短超时, 不会被一台离线设备拖住整轮。
        """
        with self._device_lock:
            targets = []
            for device in self._device_list:
                ip = device.get('ip', '')
                if not ip or ip == '局域网':
                    continue
                if ':' in ip:
                    host, _, port_s = ip.partition(':')
                    try:
                        port = int(port_s)
                    except ValueError:
                        port = self._port
                else:
                    host, port = ip, self._port
                targets.append((device.get('id'), host, port))

        if not targets:
            return

        results = {}
        res_lock = threading.Lock()

        def probe(dev_id, host, port):
            ok = False
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                ok = (s.connect_ex((host, port)) == 0)
            except Exception:
                ok = False
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
            with res_lock:
                results[dev_id] = ok

        threads = []
        for dev_id, host, port in targets:
            t = threading.Thread(target=probe, args=(dev_id, host, port), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=3.0)

        for dev_id, is_online in results.items():
            if is_online:
                self._online_devices.add(dev_id)
            else:
                self._online_devices.discard(dev_id)

    def _refresh_password(self):
        self._my_password = self._generate_password()
        set_password(self._my_password)
        self.lbl_my_password.setText(self._my_password)
        self._copy_to_clipboard(self._my_password)

    def _edit_password(self):
        text, ok = QInputDialog.getText(self, '设置访问密码', '请输入新的访问密码:', text=self._my_password)
        if ok and text.strip():
            self._my_password = text.strip()
            set_password(self._my_password)
            self.lbl_my_password.setText(self._my_password)
            QMessageBox.information(self, '设置成功', f'访问密码已更新为: {self._my_password}')

    def _format_id(self, id_str):
        if len(id_str) == 9:
            return f'{id_str[:3]} {id_str[3:6]} {id_str[6:]}'
        return id_str

    def _get_local_ip(self):
        """获取本机局域网 IP (只解析一次并缓存; socket 无论成败都会关闭)"""
        if self._local_ip is not None:
            return self._local_ip
        ip = None
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = None
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        if not ip:
            # 退一步: 枚举本机地址, 取第一个非回环
            try:
                from network import get_lan_ip
                ip = get_lan_ip()
            except Exception:
                ip = None
        self._local_ip = ip or '未知(无网络)'
        return self._local_ip

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _start_discovery(self):
        self._discover_timer = QTimer(self)
        self._discover_timer.timeout.connect(self._discover_devices)
        self._discover_timer.start(5000)

    def _discover_devices(self):
        """启动一轮后台探测 (带防重入: 上一轮没结束就不起新的)"""
        if self._scanning:
            return
        self._scanning = True

        def _do_check():
            try:
                self._check_devices_online()
                with self._device_lock:
                    for device in self._device_list:
                        device['online'] = device.get('id') in self._online_devices
                # 只 emit 信号, 由 GUI 线程刷新界面 (不能在这里碰控件)
                self.devices_refreshed.emit()
            except Exception as e:
                debug_log(f'[UI] 设备探测异常: {e}', 'ui')
            finally:
                self._scanning = False

        threading.Thread(target=_do_check, daemon=True).start()

    def _on_devices_refreshed(self):
        """GUI 线程: 后台探测完成后刷新界面"""
        try:
            if self._stack.currentIndex() == 1:
                self._refresh_devices_page()
            self._refresh_recent_devices()
        except Exception as e:
            debug_log(f'[UI] 刷新设备界面异常: {e}', 'ui')

    def _update_latency(self):
        """显示真实 RTT (由当前会话的 Controller 心跳测量); 无会话时显示 --"""
        ms = None
        try:
            if self._rtt_source is not None:
                ms = self._rtt_source()
        except Exception:
            ms = None

        if ms is None:
            self._latency = 0
            self.lbl_latency.setText('延迟: --ms')
            self.lbl_latency.setStyleSheet('color: #999; font-size: 13px; margin-right: 15px;')
            return

        self._latency = int(round(ms))
        self.lbl_latency.setText(f'延迟: {self._latency}ms')
        if self._latency < 50:
            self.lbl_latency.setStyleSheet('color: #52c41a; font-size: 13px; margin-right: 15px;')
        elif self._latency < 100:
            self.lbl_latency.setStyleSheet('color: #faad14; font-size: 13px; margin-right: 15px;')
        else:
            self.lbl_latency.setStyleSheet('color: #ff4d4f; font-size: 13px; margin-right: 15px;')

    def start_latency_monitor(self):
        self._latency_timer.start(1000)

    def stop_latency_monitor(self):
        self._latency_timer.stop()
        self.lbl_latency.setText('延迟: --ms')

    def _refresh_current_page(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            self._refresh_recent_devices()
        elif idx == 1:
            self._refresh_devices_page()
        elif idx == 2:
            self._reload_settings_page()

    # ============ 设备列表页面 ============
    def _build_devices_page(self):
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { border: none; background: #f5f6fa; }')

        page = QWidget()
        page.setStyleSheet('background: #f5f6fa;')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)

        search_row = QHBoxLayout()
        search_label = QLabel('🔍 搜索:')
        search_label.setStyleSheet('color: #666;')
        search_row.addWidget(search_label)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText('搜索设备ID、名称...')
        self.input_search.setFixedHeight(36)
        self.input_search.textChanged.connect(lambda: self._refresh_devices_page())
        search_row.addWidget(self.input_search, 1)

        btn_clear_all = QPushButton('🗑 清空列表')
        btn_clear_all.setFixedHeight(36)
        btn_clear_all.setStyleSheet('background-color: white; color: #ff4d4f; border: 1px solid #ffa39e; border-radius: 6px; padding: 4px 12px; font-size: 12px;')
        btn_clear_all.setCursor(Qt.PointingHandCursor)
        btn_clear_all.setToolTip('删除全部设备记录')
        btn_clear_all.clicked.connect(self._clear_all_devices)
        search_row.addWidget(btn_clear_all)

        layout.addLayout(search_row)

        self.devices_scroll = QScrollArea()
        self.devices_scroll.setWidgetResizable(True)
        self.devices_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.devices_content = QWidget()
        self.devices_layout = QVBoxLayout(self.devices_content)
        self.devices_layout.setSpacing(10)

        self.devices_scroll.setWidget(self.devices_content)
        layout.addWidget(self.devices_scroll, 1)

        self.detail_card = QFrame()
        self.detail_card.setObjectName('Card')
        self.detail_card.setFixedHeight(120)
        self.detail_layout = QVBoxLayout(self.detail_card)
        self.detail_layout.setSpacing(6)
        self.detail_layout.setContentsMargins(20, 12, 20, 12)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.lbl_detail_name = QLabel('请选择设备')
        self.lbl_detail_name.setStyleSheet('font-size: 15px; font-weight: bold; color: #333;')
        info_row.addWidget(self.lbl_detail_name)

        self.lbl_detail_status = QLabel('')
        self.lbl_detail_status.setStyleSheet('font-size: 12px; color: #999;')
        info_row.addWidget(self.lbl_detail_status)
        info_row.addStretch()

        self.lbl_detail_id = QLabel('')
        self.lbl_detail_id.setStyleSheet('font-size: 12px; color: #666;')
        info_row.addWidget(self.lbl_detail_id)

        self.lbl_detail_ip = QLabel('')
        self.lbl_detail_ip.setStyleSheet('font-size: 12px; color: #666;')
        info_row.addWidget(self.lbl_detail_ip)

        self.detail_layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_action_control = QPushButton('远程控制')
        self.btn_action_control.setFixedHeight(32)
        self.btn_action_control.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 6px; padding: 4px 16px; font-size: 12px; font-weight: bold;')
        self.btn_action_control.setCursor(Qt.PointingHandCursor)
        self.btn_action_control.setEnabled(False)
        self.btn_action_control.clicked.connect(lambda: self._connect_detail_device('control'))
        btn_row.addWidget(self.btn_action_control)

        self.btn_action_file = QPushButton('文件传输')
        self.btn_action_file.setFixedHeight(32)
        self.btn_action_file.setStyleSheet('background-color: white; color: #333; border: 1px solid #d9d9d9; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_file.setCursor(Qt.PointingHandCursor)
        self.btn_action_file.setEnabled(False)
        self.btn_action_file.clicked.connect(lambda: self._connect_detail_device('file'))
        btn_row.addWidget(self.btn_action_file)

        self.btn_action_view = QPushButton('仅观看')
        self.btn_action_view.setFixedHeight(32)
        self.btn_action_view.setStyleSheet('background-color: white; color: #333; border: 1px solid #d9d9d9; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_view.setCursor(Qt.PointingHandCursor)
        self.btn_action_view.setEnabled(False)
        self.btn_action_view.setToolTip('只查看画面, 不向对方发送键鼠操作')
        self.btn_action_view.clicked.connect(lambda: self._connect_detail_device('view'))
        btn_row.addWidget(self.btn_action_view)

        self.btn_action_delete = QPushButton('🗑 删除设备')
        self.btn_action_delete.setFixedHeight(32)
        self.btn_action_delete.setStyleSheet('background-color: white; color: #ff4d4f; border: 1px solid #ffa39e; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_delete.setCursor(Qt.PointingHandCursor)
        self.btn_action_delete.setEnabled(False)
        self.btn_action_delete.setToolTip('从设备列表删除该设备')
        self.btn_action_delete.clicked.connect(lambda: self._confirm_remove_device(self._current_detail_device))
        btn_row.addWidget(self.btn_action_delete)

        btn_row.addStretch()
        self.detail_layout.addLayout(btn_row)

        layout.addWidget(self.detail_card)

        self._current_detail_device = None
        self._refresh_devices_page()

        scroll.setWidget(page)
        outer_layout.addWidget(scroll)
        return outer

    def _refresh_devices_page(self):
        while self.devices_layout.count():
            item = self.devices_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        search_text = self.input_search.text().strip() if hasattr(self, 'input_search') else ''
        with self._device_lock:
            snapshot = list(self._device_list)
        filtered = [d for d in snapshot
                    if not search_text or search_text in d.get('id', '')
                    or search_text in d.get('name', '')]

        if not filtered:
            empty_label = QLabel('📭 暂无设备，请在主页添加')
            empty_label.setStyleSheet('color: #999; font-size: 14px;')
            empty_label.setAlignment(Qt.AlignCenter)
            self.devices_layout.addWidget(empty_label)
        else:
            for device in filtered:
                item = self._create_device_list_item(device)
                self.devices_layout.addWidget(item)

        self.devices_layout.addStretch()

    def _create_device_list_item(self, device):
        item_widget = QFrame()
        item_widget.setObjectName('DeviceItem')
        item_widget.setMinimumHeight(60)
        item_widget.setMaximumHeight(80)
        item_layout = QHBoxLayout(item_widget)
        item_layout.setSpacing(15)
        item_layout.setContentsMargins(15, 8, 15, 8)

        status = StatusIndicator()
        is_online = device.get('online', False)
        status.set_color('#52c41a' if is_online else '#d9d9d9')
        item_layout.addWidget(status)

        icon_label = QLabel('💻')
        icon_label.setStyleSheet('font-size: 28px;')
        item_layout.addWidget(icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        name_row = QHBoxLayout()
        name_label = QLabel(device.get('name', '未知设备'))
        name_label.setStyleSheet('font-weight: bold; font-size: 15px; color: #333;')
        name_row.addWidget(name_label)

        if device.get('favorite', False):
            star_label = QLabel('⭐')
            star_label.setStyleSheet('font-size: 14px;')
            name_row.addWidget(star_label)

        name_row.addStretch()
        info_layout.addLayout(name_row)

        detail_text = f"ID: {device.get('id', '-----')} | IP: {device.get('ip', '-----')}"
        if is_online:
            detail_text += ' | ✅ 在线'
        else:
            detail_text += ' | ⚪ 离线'

        detail_label = QLabel(detail_text)
        detail_label.setStyleSheet('color: #999; font-size: 12px;')
        info_layout.addWidget(detail_label)

        item_layout.addLayout(info_layout, 1)

        btn_favorite = QPushButton('⭐' if not device.get('favorite', False) else '★')
        btn_favorite.setFixedSize(36, 36)
        btn_favorite.setStyleSheet('''
            QPushButton {
                background: transparent;
                border: none;
                font-size: 18px;
            }
        ''')
        btn_favorite.setCursor(Qt.PointingHandCursor)
        btn_favorite.clicked.connect(lambda: self._toggle_favorite(device))
        item_layout.addWidget(btn_favorite)

        btn_delete = QPushButton('🗑')
        btn_delete.setFixedSize(36, 36)
        btn_delete.setStyleSheet('''
            QPushButton {
                background: transparent;
                border: none;
                font-size: 18px;
            }
            QPushButton:hover { color: #ff4d4f; }
        ''')
        btn_delete.setToolTip('删除设备')
        btn_delete.setCursor(Qt.PointingHandCursor)
        btn_delete.clicked.connect(lambda: self._confirm_remove_device(device))
        item_layout.addWidget(btn_delete)

        item_widget.mousePressEvent = lambda e: self._select_device(device)

        return item_widget

    def _select_device(self, device):
        self._current_detail_device = device
        self.lbl_detail_name.setText(device.get('name', '未知设备'))
        self.lbl_detail_id.setText(f"ID: {device.get('id', '-----')}")
        self.lbl_detail_ip.setText(f"IP: {device.get('ip', '-----')}")

        is_online = device.get('online', False)
        self.lbl_detail_status.setText('在线' if is_online else '离线')
        self.lbl_detail_status.setStyleSheet('font-size: 12px; color: #52c41a;' if is_online else 'font-size: 12px; color: #999;')

        for btn in [self.btn_action_control, self.btn_action_file,
                    self.btn_action_view, self.btn_action_delete]:
            btn.setEnabled(True)

    def _toggle_favorite(self, device):
        with self._device_lock:
            device['favorite'] = not device.get('favorite', False)
            self._save_device_list()
        self._refresh_devices_page()

    def _confirm_remove_device(self, device):
        if not device:
            return
        name = device.get('name', device.get('id', '未知设备'))
        ret = QMessageBox.question(
            self, '删除设备',
            f'确定要删除设备「{name}」吗？\n删除后需要重新添加才能连接。',
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            self._remove_device(device)

    def _remove_device(self, device):
        with self._device_lock:
            if device in self._device_list:
                self._device_list.remove(device)
            self._save_device_list()
        # 该设备的连接历史也一并清掉, 否则历史下拉里还留着已删设备
        try:
            remove_history(device.get('id', ''))
        except Exception:
            pass
        if self._current_detail_device is device:
            self._clear_detail_panel()
        self._refresh_devices_page()
        self._refresh_recent_devices()

    def _clear_all_devices(self):
        with self._device_lock:
            count = len(self._device_list)
        if not count:
            return
        ret = QMessageBox.question(
            self, '清空设备列表',
            f'确定要清空全部 {count} 条设备记录吗？\n此操作不可恢复！',
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            with self._device_lock:
                self._device_list.clear()
                self._save_device_list()
            self._current_detail_device = None
            self._clear_detail_panel()
            self._refresh_devices_page()
            self._refresh_recent_devices()

    def _clear_detail_panel(self):
        self._current_detail_device = None
        self.lbl_detail_name.setText('请选择设备')
        self.lbl_detail_id.setText('')
        self.lbl_detail_ip.setText('')
        self.lbl_detail_status.setText('')
        for btn in [self.btn_action_control, self.btn_action_file,
                    self.btn_action_view, self.btn_action_delete]:
            btn.setEnabled(False)

    def _refresh_recent_devices(self):
        """只刷新"最近连接"列表。

        旧实现 _refresh_home_page() 会 removeWidget + deleteLater + 整页重建,
        副作用是: 用户已填好的设备ID/密码被清空、_current_mode 与显示不一致、
        每次都要重跑一次阻塞式取IP。这里改为局部刷新。
        """
        try:
            if not hasattr(self, 'recent_layout'):
                return
            while self.recent_layout.count():
                item = self.recent_layout.takeAt(0)
                w = item.widget()
                if w is not None and w is not self.recent_empty_label:
                    w.deleteLater()
            with self._device_lock:
                devices = list(self._device_list[:5])
            if not devices:
                self.recent_empty_label.setVisible(True)
                self.recent_layout.addWidget(self.recent_empty_label)
            else:
                self.recent_empty_label.setVisible(False)
                for device in devices:
                    self.recent_layout.addWidget(self._create_recent_device_item(device))
            self.recent_layout.addStretch()
        except Exception as e:
            debug_log(f'[UI] 刷新最近连接失败: {e}', 'ui')

    def _refresh_home_page(self):
        """保留旧名字作为兼容入口"""
        self._refresh_recent_devices()

    def _connect_detail_device(self, mode):
        if not self._current_detail_device:
            return

        device_id = self._current_detail_device.get('id', '')
        password = self._device_password(self._current_detail_device)

        if not password:
            QMessageBox.information(self, '需要密码',
                                    '该设备还没有保存密码，请在主页输入密码后连接。')
            return

        self.set_connect_status(f'⏳ 正在连接 {device_id} ...')
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText('连接中...')
        self.connect_requested.emit(device_id, mode, password)

    # ============ 高级设置页面 ============
    def _build_settings_page(self):
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { border: none; background: #f5f6fa; }')

        page = QWidget()
        page.setStyleSheet('background: #f5f6fa;')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)

        tabs = QTabWidget()

        basic_tab = QWidget()
        basic_layout = QVBoxLayout(basic_tab)
        basic_layout.setSpacing(15)

        group1 = QGroupBox('常规设置')
        form1 = QFormLayout()
        form1.setSpacing(12)

        self.chk_autostart = ToggleSwitch('开机自启动 MyDesk')
        self.chk_autostart.setChecked(self._get_setting('autostart', False))
        form1.addRow(self.chk_autostart)

        self.chk_prevent_sleep = ToggleSwitch('防止系统休眠（远程会话中）')
        self.chk_prevent_sleep.setChecked(self._get_setting('prevent_sleep', True))
        form1.addRow(self.chk_prevent_sleep)

        self.chk_show_tray = ToggleSwitch('最小化到系统托盘')
        self.chk_show_tray.setChecked(self._get_setting('show_tray', True))
        form1.addRow(self.chk_show_tray)

        group1.setLayout(form1)
        basic_layout.addWidget(group1)

        group2 = QGroupBox('文件传输')
        form2 = QFormLayout()
        form2.setSpacing(12)

        self.edit_save_path = QLineEdit(self._get_setting('save_path', os.path.expanduser('~')))
        self.edit_save_path.setFixedHeight(36)

        # 浏览按钮与输入框同行同高, 不再被表单拉满整行
        self.btn_browse = QPushButton('浏览...')
        self.btn_browse.setFixedSize(80, 36)
        self.btn_browse.setCursor(Qt.PointingHandCursor)
        self.btn_browse.clicked.connect(self._browse_save_path)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.edit_save_path, 1)
        path_row.addWidget(self.btn_browse)
        form2.addRow('默认保存路径:', path_row)

        group2.setLayout(form2)
        basic_layout.addWidget(group2)

        basic_layout.addStretch()
        tabs.addTab(basic_tab, '基本设置')

        connect_tab = QWidget()
        connect_layout = QVBoxLayout(connect_tab)
        connect_layout.setSpacing(15)

        group3 = QGroupBox('连接设置')
        form3 = QFormLayout()

        self.spn_port = QSpinBox()
        self.spn_port.setRange(1024, 65535)
        self.spn_port.setValue(int(self._get_setting('port', 7788)))
        self.spn_port.setToolTip('被控端监听端口。修改后需要重启 MyDesk 才生效。')
        form3.addRow('监听端口:', self.spn_port)

        self.chk_lan_only = ToggleSwitch('仅允许局域网/私有地址连接')
        self.chk_lan_only.setChecked(bool(self._get_setting('lan_only', False)))
        self.chk_lan_only.setToolTip(
            '开启后, 被控端会拒绝来自公网/非私有地址的连接请求。')
        form3.addRow(self.chk_lan_only)

        # 加密方式: 项目目前没有实现任何加密, 之前这里放了一个
        # "AES-256/AES-128/无加密" 下拉框纯属误导, 已移除并改为如实说明。
        enc_note = QLabel(
            '⚠️ 数据传输未加密：认证已用挑战-响应防重放, 但画面/输入/文件是明文。\n'
            '    请只在可信网络（局域网 / Tailscale 等加密隧道）中使用。')
        enc_note.setWordWrap(True)
        enc_note.setStyleSheet('color: #d46b08; font-size: 12px; '
                               'background: #fff7e6; padding: 8px; border-radius: 6px;')
        form3.addRow(enc_note)

        group3.setLayout(form3)
        connect_layout.addWidget(group3)

        connect_layout.addStretch()
        tabs.addTab(connect_tab, '连接设置')

        display_tab = QWidget()
        display_layout = QVBoxLayout(display_tab)
        display_layout.setSpacing(15)

        group4 = QGroupBox('画质设置')
        form4 = QFormLayout()

        # 画质直接对应 JPEG 质量, 不再用"流畅/清晰/高清"这类与实现脱节的说法
        self.cmb_quality = QComboBox()
        self.cmb_quality.addItems([
            '省流量 (JPEG 40)', '流畅 (JPEG 55)', '清晰 (JPEG 75)', '高清 (JPEG 90)'])
        self._quality_values = [40, 55, 75, 90]
        cur_q = int(self._get_setting('quality', 65))
        self.cmb_quality.setCurrentIndex(
            min(range(len(self._quality_values)),
                key=lambda i: abs(self._quality_values[i] - cur_q)))
        form4.addRow('默认画质:', self.cmb_quality)

        self.cmb_fps = QComboBox()
        self.cmb_fps.addItems([str(x) for x in [15, 24, 30, 45, 60]])
        self.cmb_fps.setCurrentText(str(self._get_setting('fps', 30)))
        form4.addRow('帧率上限:', self.cmb_fps)

        self.chk_auto_quality = ToggleSwitch('根据网络自动调整画质')
        self.chk_auto_quality.setChecked(False)
        self.chk_auto_quality.setEnabled(False)
        self.chk_auto_quality.setToolTip('暂未实现（需要带宽/丢包测量）, 保留占位以免误导')
        form4.addRow(self.chk_auto_quality)

        group4.setLayout(form4)
        display_layout.addWidget(group4)

        display_layout.addStretch()
        tabs.addTab(display_tab, '显示设置')

        security_tab = QWidget()
        security_layout = QVBoxLayout(security_tab)
        security_layout.setSpacing(15)

        group5 = QGroupBox('安全设置')
        form5 = QFormLayout()

        pwd_note = QLabel(
            '连接密码请在「远程控制」页用「编辑 / 刷新」按钮修改。\n'
            '密码以 DPAPI 加密后存放在本机配置里, 明文不再落盘。')
        pwd_note.setWordWrap(True)
        pwd_note.setStyleSheet('color: #666; font-size: 12px;')
        form5.addRow(pwd_note)

        self.chk_ip_whitelist = ToggleSwitch('仅允许白名单 IP 连接')
        self.chk_ip_whitelist.setChecked(bool(self._get_setting('ip_whitelist', False)))
        form5.addRow(self.chk_ip_whitelist)

        self.edit_whitelist = QLineEdit(self._get_setting('whitelist_ips', ''))
        self.edit_whitelist.setPlaceholderText('多个 IP 前缀用逗号分隔, 例如 192.168.1.,10.0.0.5')
        self.edit_whitelist.setFixedHeight(36)
        form5.addRow('白名单 IP:', self.edit_whitelist)

        self.chk_two_factor = ToggleSwitch('连接前需要被控端确认')
        self.chk_two_factor.setChecked(False)
        self.chk_two_factor.setEnabled(False)
        self.chk_two_factor.setToolTip('暂未实现, 保留占位以免误导')
        form5.addRow(self.chk_two_factor)

        group5.setLayout(form5)
        security_layout.addWidget(group5)

        security_layout.addStretch()
        tabs.addTab(security_tab, '安全设置')

        # 保存按钮放页面底部右侧, 固定尺寸 (修复原先与 Tab 栏重叠的问题)
        btn_save = QPushButton('💾 保存设置')
        btn_save.setObjectName('PrimaryBtn')
        btn_save.setFixedSize(160, 42)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save_settings)
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(btn_save)

        layout.addWidget(tabs)
        layout.addSpacing(12)
        layout.addLayout(save_row)

        scroll.setWidget(page)
        outer_layout.addWidget(scroll)
        return outer

    def _get_setting(self, key, default=None):
        """读取用户设置 (走 config 的缓存 + 原子写实现)"""
        try:
            from config import get_setting
            return get_setting(key, default)
        except Exception:
            return default

    def _reload_settings_page(self):
        """把设置页的控件重新按磁盘上的值填充 (供顶栏 🔄 使用)"""
        try:
            self.chk_autostart.setChecked(bool(self._get_setting('autostart', False)))
            self.chk_prevent_sleep.setChecked(bool(self._get_setting('prevent_sleep', True)))
            self.chk_show_tray.setChecked(bool(self._get_setting('show_tray', True)))
            self.edit_save_path.setText(self._get_setting('save_path', ''))
            self.spn_port.setValue(int(self._get_setting('port', 7788)))
            self.chk_lan_only.setChecked(bool(self._get_setting('lan_only', False)))
            cur_q = int(self._get_setting('quality', 65))
            self.cmb_quality.setCurrentIndex(
                min(range(len(self._quality_values)),
                    key=lambda i: abs(self._quality_values[i] - cur_q)))
            self.cmb_fps.setCurrentText(str(self._get_setting('fps', 30)))
            self.chk_ip_whitelist.setChecked(bool(self._get_setting('ip_whitelist', False)))
            self.edit_whitelist.setText(self._get_setting('whitelist_ips', ''))
        except Exception as e:
            debug_log(f'[UI] 重新加载设置失败: {e}', 'ui')

    def _save_settings(self):
        try:
            settings = dict(load_settings())
            settings.update({
                'autostart': self.chk_autostart.isChecked(),
                'prevent_sleep': self.chk_prevent_sleep.isChecked(),
                'show_tray': self.chk_show_tray.isChecked(),
                'save_path': self.edit_save_path.text().strip(),
                'port': self.spn_port.value(),
                'lan_only': self.chk_lan_only.isChecked(),
                'quality': self._quality_values[self.cmb_quality.currentIndex()],
                'fps': int(self.cmb_fps.currentText()),
                'auto_quality': False,       # 未实现, 恒为 False
                'two_factor': False,         # 未实现, 恒为 False
                'ip_whitelist': self.chk_ip_whitelist.isChecked(),
                'whitelist_ips': self.edit_whitelist.text().strip(),
            })
            save_settings(settings)

            # 开机自启: 真的去写/删注册表 Run 键 (旧版勾了没有任何作用)
            self._apply_autostart(settings['autostart'])

            QMessageBox.information(
                self, '成功',
                '✅ 设置已保存！\n\n'
                f'监听端口: {settings["port"]}（重启 MyDesk 后生效）\n'
                f'开机自启: {"已开启" if settings["autostart"] else "已关闭"}\n'
                f'默认保存路径: {settings["save_path"]}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'保存设置失败: {e}')

    def _apply_autostart(self, enabled):
        """把"开机自启"落到注册表 (失败只提示, 不影响其它设置)"""
        try:
            from autostart import (is_installed_registry, install_registry,
                                   uninstall_registry)
            if enabled and not is_installed_registry():
                install_registry()
            elif not enabled and is_installed_registry():
                uninstall_registry()
        except Exception as e:
            debug_log(f'[UI] 配置开机自启失败: {e}', 'ui')
            QMessageBox.warning(self, '开机自启',
                                f'开机自启设置失败(其它设置已保存): {e}')

    def _browse_save_path(self):
        path = QFileDialog.getExistingDirectory(self, '选择保存路径')
        if path:
            self.edit_save_path.setText(path)

    # ============ 公共方法 ============
    def get_device_id(self):
        return self._my_device_id

    def get_password(self):
        return self._my_password

    # ============ 历史记录 (吸收自 unified_ui) ============
    def _refresh_history(self):
        self.input_history.blockSignals(True)
        self.input_history.clear()
        history = get_history()
        if history:
            self.input_history.addItem('-- 选择历史记录 --', '')
            for item in history:
                target = item.get('target', '')
                host = item.get('host', '')
                last_used = item.get('last_used', '')
                display = target
                if host:
                    display += f' ({host})'
                if last_used:
                    display += f'  - {last_used}'
                self.input_history.addItem(display, target)
        self.input_history.blockSignals(False)

    def _on_history_selected(self, index):
        if index <= 0:
            return
        target = self.input_history.itemData(index)
        if target:
            self.input_device_id.setText(target)
            history = get_history()
            for item in history:
                if item.get('target') == target:
                    pwd = item.get('password', '')
                    if pwd:
                        self.input_password.setText(pwd)
                    break

    def _clear_history(self):
        ret = QMessageBox.question(
            self, '确认',
            '确定要清空所有历史记录吗？',
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            clear_history()
            self._refresh_history()

    def _run_network_diagnostics(self):
        """网络诊断: 放到后台线程执行, 结果通过信号回到 GUI 线程显示。

        旧实现在 GUI 线程里做 DNS 解析 + 两个 2 秒超时的 socket, 最坏会让
        界面冻结 4-6 秒; 而且 socket 只在成功分支关闭, 失败时泄漏句柄。
        """
        debug_log('[诊断] 开始网络诊断', 'diag')

        def worker():
            results = []
            diag_port = self._port

            # 本机 IP
            try:
                results.append(f'✅ 本机IP: {self._get_local_ip()}')
            except Exception as e:
                results.append(f'❌ 本机IP获取失败: {e}')

            # 被控端监听端口是否在听 (先看自己这台)
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                rc = s.connect_ex(('127.0.0.1', diag_port))
                if rc == 0:
                    results.append(f'✅ TCP 端口 {diag_port} 本地可达 (被控端已在监听)')
                else:
                    results.append(f'⚠️ TCP 端口 {diag_port} 本地不可达 (可能被控端未启动)')
            except Exception as e:
                results.append(f'❌ TCP检测异常: {e}')
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass

            # UDP 广播 (发现端口固定, 与可变 TCP 端口解耦)
            s = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(2)
                s.sendto(b'MyDesk-Diagnostic',
                         ('255.255.255.255', get_discovery_port()))
                results.append(f'✅ UDP 广播已发出 (发现端口 {get_discovery_port()})')
            except PermissionError:
                results.append('❌ UDP广播被拒绝 (权限不足)')
            except OSError as e:
                results.append(f'❌ UDP广播失败: {e}')
            except Exception as e:
                results.append(f'❌ UDP异常: {e}')
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass

            # 已记录设备里在线的情况
            try:
                with self._device_lock:
                    devices = list(self._device_list)
                online = [d.get('id', '?') for d in devices
                          if d.get('id') in self._online_devices]
                if devices:
                    results.append(f'ℹ️ 设备列表: 共 {len(devices)} 台, '
                                   f'在线 {len(online)} 台'
                                   + (f' ({", ".join(online)})' if online else ''))
            except Exception as e:
                results.append(f'⚠️ 设备状态统计失败: {e}')

            text = ('网络诊断结果:\n\n' + '\n'.join(results)
                    + f'\n\n本机设备ID: {self._my_device_id}'
                    + f'\n日志目录: {LOG_DIR}')
            debug_log(f'[诊断] 结果: {text}', 'diag')
            self.diagnostics_ready.emit(text)

        threading.Thread(target=worker, daemon=True).start()

    def _on_diagnostics_ready(self, text):
        """GUI 线程: 显示诊断结果"""
        try:
            QMessageBox.information(self, '网络诊断', text)
        except Exception as e:
            debug_log(f'[诊断] 显示结果失败: {e}', 'diag')
