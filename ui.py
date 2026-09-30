"""
MyDesk - 主界面
ToDesk 风格 UI
主色调: #1677ff
"""

import sys
import os
import json
import time
import struct
import threading
from datetime import datetime

from config import get_password, set_password, get_device_id

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QIcon, QPixmap, QColor, QFont, QPainter, QBrush, QLinearGradient, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QListWidget, QListWidgetItem, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QCheckBox,
    QSpinBox, QComboBox, QSlider, QGroupBox, QFormLayout, QMenu, QAction,
    QSystemTrayIcon, QMenu as QMenuBar, QToolTip, QScrollArea, QGridLayout,
    QSizePolicy, QGraphicsDropShadowEffect, QFileDialog, QMessageBox
)

# 日志目录和数据目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
DATA_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Data')

def _debug_log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_file = os.path.join(LOG_DIR, 'ui_debug.log')
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f'[{timestamp}] {msg}\n')
    except Exception:
        pass

def _crash_log(msg):
    try:
        crash_path = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs', 'crash.log')
        os.makedirs(os.path.dirname(crash_path), exist_ok=True)
        with open(crash_path, 'a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().strftime("%H:%M:%S.%f")[:-3]}] {msg}\n')
    except:
        pass

# ============ 样式表 ============
TODESK_STYLE = """
QWidget {
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #1a1a1a;
}

/* 主窗口背景 */
QMainWindow, QDialog {
    background-color: #ffffff;
}

/* 侧边栏 */
#Sidebar {
    background-color: #f8f9fa;
    border-right: 1px solid #e8e8e8;
}

#SidebarButton {
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 12px 16px;
    text-align: left;
    color: #666;
    font-size: 14px;
}

#SidebarButton:hover {
    background-color: #e8f0fe;
    color: #1677ff;
}

#SidebarButton[selected="true"] {
    background-color: #1677ff;
    color: #ffffff;
}

/* 顶部栏 */
#TopBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e8e8e8;
}

/* 主按钮 */
#PrimaryBtn {
    background-color: #1677ff;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    color: #ffffff;
    font-size: 14px;
    font-weight: bold;
}

#PrimaryBtn:hover {
    background-color: #4096ff;
}

#PrimaryBtn:pressed {
    background-color: #0958d9;
}

#PrimaryBtn:disabled {
    background-color: #bfbfbf;
    color: #ffffff;
}

/* 次要按钮 */
#SecondaryBtn {
    background-color: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 10px 24px;
    color: #333;
    font-size: 14px;
}

#SecondaryBtn:hover {
    border-color: #1677ff;
    color: #1677ff;
}

/* 危险按钮 */
#DangerBtn {
    background-color: #ff4d4f;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    color: #ffffff;
    font-size: 14px;
}

#DangerBtn:hover {
    background-color: #ff7875;
}

/* 输入框 */
QLineEdit {
    background-color: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 10px 14px;
    color: #333;
    font-size: 14px;
}

QLineEdit:focus {
    border-color: #1677ff;
}

/* 卡片 */
#Card {
    background-color: #ffffff;
    border: 1px solid #e8e8e8;
    border-radius: 12px;
    padding: 20px;
}

/* 状态标签 */
#StatusConnected {
    color: #52c41a;
    font-weight: bold;
}

#StatusDisconnected {
    color: #ff4d4f;
    font-weight: bold;
}

/* Tab样式 */
QTabWidget::pane {
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    background: #ffffff;
}

QTabBar::tab {
    background: #f8f9fa;
    border: 1px solid #e8e8e8;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 20px;
    margin-right: 2px;
    color: #666;
}

QTabBar::tab:selected {
    background: #ffffff;
    color: #1677ff;
    border-bottom: 2px solid #1677ff;
}

/* 设备列表项 */
#DeviceItem {
    background-color: #ffffff;
    border: 1px solid #e8e8e8;
    border-radius: 10px;
    padding: 12px 16px;
    margin: 4px 0;
}

#DeviceItem:hover {
    border-color: #1677ff;
    background-color: #f0f7ff;
}

#DeviceItem[selected="true"] {
    border-color: #1677ff;
    background-color: #e6f4ff;
}

/* 圆形按钮 */
#CircularBtn {
    background-color: #1677ff;
    border: none;
    border-radius: 15px;
    width: 30px;
    height: 30px;
    color: white;
    font-size: 12px;
}

#CircularBtn:hover {
    background-color: #4096ff;
}

/* 设置控件 */
QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #d9d9d9;
    background-color: #ffffff;
}

QCheckBox::indicator:checked {
    background-color: #1677ff;
    border-color: #1677ff;
}

QSpinBox, QComboBox {
    border: 1px solid #d9d9d9;
    border-radius: 6px;
    padding: 6px 10px;
    background-color: #ffffff;
}

QSpinBox:focus, QComboBox:focus {
    border-color: #1677ff;
}

/* 会话悬浮工具栏 */
#SessionBar {
    background-color: rgba(255, 255, 255, 0.95);
    border-radius: 24px;
    padding: 8px 12px;
}

#SessionBtn {
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    color: #333;
    font-size: 13px;
}

#SessionBtn:hover {
    background-color: #e6f4ff;
    color: #1677ff;
}

/* 进度条 */
QProgressBar {
    border: 1px solid #e8e8e8;
    border-radius: 6px;
    text-align: center;
    background-color: #f8f9fa;
}

QProgressBar::chunk {
    background-color: #1677ff;
    border-radius: 5px;
}

/* 滚动条 */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
}

QScrollBar::handle:vertical {
    background: #d9d9d9;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: #bfbfbf;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

# ============ 图标生成器 ============
class IconGenerator:
    """生成简单的图标"""
    
    @staticmethod
    def create_icon(text, color='#1677ff', size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 绘制圆形背景
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, size - 4, size - 4)
        
        # 绘制文字
        painter.setPen(QColor('white'))
        font = QFont('Microsoft YaHei')
        font.setPixelSize(size // 2)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
        
        painter.end()
        return QIcon(pixmap)

# ============ 状态指示器 ============
class StatusIndicator(QWidget):
    """状态指示器（小圆点）"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor('#ff4d4f')
        self.setFixedSize(12, 12)
    
    def set_color(self, color_str):
        self._color = QColor(color_str)
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 8, 8)

# ============ 侧边栏按钮 ============
class SidebarButton(QPushButton):
    """侧边栏导航按钮"""
    
    nav_clicked = pyqtSignal(str)  # 使用独立信号名，避免覆盖QPushButton.clicked
    
    def __init__(self, icon_text, text, page_id, parent=None):
        super().__init__(parent)
        self._page_id = page_id
        self.setText(text)
        self.setObjectName('SidebarButton')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        
        # 设置图标
        icon = IconGenerator.create_icon(icon_text)
        self.setIcon(icon)
        self.setIconSize(QSize(24, 24))
        
        # 连接基类clicked信号(不带参数)到自定义信号
        super().clicked.connect(lambda: self.nav_clicked.emit(self._page_id))

# ============ 主窗口 ============
class MainWindow(QMainWindow):
    """MyDesk 主窗口"""
    
    # 信号
    connect_requested = pyqtSignal(str, str, str)  # 设备ID, 模式, 密码
    file_transfer_requested = pyqtSignal(str)  # 设备ID
    terminal_requested = pyqtSignal(str)  # 设备ID
    device_list_changed = pyqtSignal()
    
    def __init__(self):
        try:
            _crash_log('MainWindow.__init__ 开始')
            super().__init__()
            _crash_log('MainWindow: super().__init__ 完成')
            self.setWindowTitle('MyDesk - 个人远程桌面')
            self.setMinimumSize(1100, 700)
            self.resize(1280, 800)
            _crash_log('MainWindow: 设置样式表')
            self.setStyleSheet(TODESK_STYLE)
            
            # 设备数据
            _crash_log('MainWindow: 获取设备ID')
            self._my_device_id = get_device_id()
            _crash_log('MainWindow: 加载密码')
            self._my_password = get_password()
            _crash_log('MainWindow: 加载设备列表')
            self._device_list = self._load_device_list()
            self._online_devices = set()
            
            # 延迟显示
            self._latency = 0
            self._latency_timer = QTimer(self)
            self._latency_timer.timeout.connect(self._update_latency)
            
            # 构建UI
            _crash_log('MainWindow: 开始 _build_ui')
            self._build_ui()
            _crash_log('MainWindow: _build_ui 完成')
            
            # 启动设备发现
            _crash_log('MainWindow: 开始 _start_discovery')
            self._start_discovery()
            _crash_log('MainWindow: _start_discovery 完成')
            
            _debug_log(f'[UI] 主窗口初始化完成, 设备ID: {self._my_device_id}')
            _crash_log('MainWindow.__init__ 完成')
        except Exception as e:
            import traceback
            _crash_log(f'MainWindow.__init__ CRASH: {e}\n{traceback.format_exc()}')
            raise
    
    def _generate_password(self):
        """生成随机访问密码"""
        import random
        import string
        chars = string.ascii_lowercase + string.digits
        return ''.join(random.choices(chars, k=6))
    
    def _load_device_list(self):
        """加载设备列表"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            file_path = os.path.join(DATA_DIR, 'device_list.json')
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            _debug_log(f'[UI] 加载设备列表失败: {e}')
        return []
    
    def _save_device_list(self):
        """保存设备列表"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            file_path = os.path.join(DATA_DIR, 'device_list.json')
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self._device_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _debug_log(f'[UI] 保存设备列表失败: {e}')
    
    def _build_ui(self):
        """构建主界面"""
        _crash_log('_build_ui: 开始')
        central = QWidget()
        _crash_log('_build_ui: QWidget 创建完成')
        self.setCentralWidget(central)
        _crash_log('_build_ui: setCentralWidget 完成')
        
        # 主布局
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ============ 左侧边栏 ============
        _crash_log('_build_ui: 创建 Sidebar')
        sidebar = QFrame()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 20, 12, 20)
        sidebar_layout.setSpacing(8)
        
        # Logo
        logo_label = QLabel('MyDesk')
        logo_label.setStyleSheet('font-size: 24px; font-weight: bold; color: #1677ff; padding: 10px 0 20px 0;')
        logo_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(logo_label)
        
        # 版本
        ver_label = QLabel('v1.0.0')
        ver_label.setStyleSheet('color: #999; font-size: 12px; text-align: center;')
        ver_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(ver_label)
        
        sidebar_layout.addSpacing(20)
        
        # 导航按钮
        _crash_log('_build_ui: 创建导航按钮')
        self._nav_buttons = {}
        nav_items = [
            ('🏠', '远程控制', 'home'),
            ('📋', '设备列表', 'devices'),
            ('⚙️', '高级设置', 'settings'),
        ]
        
        for icon, text, page_id in nav_items:
            _crash_log(f'_build_ui: 创建按钮 {page_id}')
            btn = SidebarButton(icon, text, page_id)
            btn.nav_clicked.connect(self._on_nav_clicked)
            self._nav_buttons[page_id] = btn
            sidebar_layout.addWidget(btn)
        
        sidebar_layout.addStretch()
        
        # 底部版权
        copyright_label = QLabel('仅供个人使用')
        copyright_label.setStyleSheet('color: #ccc; font-size: 11px;')
        copyright_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(copyright_label)
        
        main_layout.addWidget(sidebar)
        
        # ============ 右侧内容区 ============
        _crash_log('_build_ui: 创建右侧内容区')
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # 顶部栏
        topbar = QFrame()
        topbar.setObjectName('TopBar')
        topbar.setFixedHeight(50)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(20, 0, 20, 0)
        
        self.lbl_page_title = QLabel('远程控制')
        self.lbl_page_title.setStyleSheet('font-size: 18px; font-weight: bold; color: #333;')
        topbar_layout.addWidget(self.lbl_page_title)
        
        topbar_layout.addStretch()
        
        # 状态指示器
        _crash_log('_build_ui: 创建 StatusIndicator')
        self.status_indicator = StatusIndicator()
        self.status_indicator.set_color('#52c41a')
        topbar_layout.addWidget(self.status_indicator)
        
        self.lbl_status_text = QLabel('在线')
        self.lbl_status_text.setStyleSheet('color: #52c41a; font-size: 13px; margin-right: 15px;')
        topbar_layout.addWidget(self.lbl_status_text)
        
        # 延迟显示
        self.lbl_latency = QLabel('延迟: --ms')
        self.lbl_latency.setStyleSheet('color: #666; font-size: 13px; margin-right: 15px;')
        topbar_layout.addWidget(self.lbl_latency)
        
        # 刷新按钮
        btn_refresh = QPushButton('🔄')
        btn_refresh.setFixedSize(32, 32)
        btn_refresh.setStyleSheet('background: transparent; border: none; border-radius: 6px; font-size: 16px;')
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self._refresh_current_page)
        topbar_layout.addWidget(btn_refresh)
        
        content_layout.addWidget(topbar)
        
        # 页面堆栈
        _crash_log('_build_ui: 构建页面堆栈')
        self._stack = QStackedWidget()
        _crash_log('_build_ui: 构建首页')
        self._stack.addWidget(self._build_home_page())
        _crash_log('_build_ui: 构建设备列表页')
        self._stack.addWidget(self._build_devices_page())
        _crash_log('_build_ui: 构建设置页')
        self._stack.addWidget(self._build_settings_page())
        content_layout.addWidget(self._stack)
        
        main_layout.addWidget(content, 1)
        
        # 默认选中首页
        self._nav_buttons['home'].setChecked(True)
        _crash_log('_build_ui: 完成')
    
    def _on_nav_clicked(self, page_id):
        """导航切换"""
        page_map = {'home': 0, 'devices': 1, 'settings': 2}
        title_map = {'home': '远程控制', 'devices': '设备列表', 'settings': '高级设置'}
        
        for pid, btn in self._nav_buttons.items():
            btn.setChecked(pid == page_id)
        
        if page_id in page_map:
            self._stack.setCurrentIndex(page_map[page_id])
            self.lbl_page_title.setText(title_map[page_id])
    
    def _build_home_page(self):
        """构建远程控制主页"""
        _crash_log('_build_home_page: 开始')
        # 外层容器
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        
        # 滚动区域
        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_scroll.setStyleSheet('QScrollArea { border: none; background: #f5f6fa; }')
        
        page = QWidget()
        page.setStyleSheet('background: #f5f6fa;')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        
        # ============ 顶部卡片: 本机信息 ============
        _crash_log('_build_home_page: 创建本机信息卡片')
        self_my_card = QFrame()
        _crash_log('_build_home_page: QFrame 创建完成')
        self_my_card.setObjectName('Card')
        card_layout = QVBoxLayout(self_my_card)
        card_layout.setSpacing(10)
        card_layout.setContentsMargins(20, 15, 20, 15)
        
        title = QLabel('📱 本机设备信息')
        title.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        card_layout.addWidget(title)
        
        # 设备ID
        _crash_log('_build_home_page: 创建设备ID行')
        id_row = QHBoxLayout()
        id_label = QLabel('设备代码:')
        id_label.setFixedWidth(70)
        id_label.setStyleSheet('color: #666; font-size: 13px;')
        id_row.addWidget(id_label)
        
        _crash_log('_build_home_page: 调用 _format_id')
        self.lbl_my_id = QLabel(self._format_id(self._my_device_id))
        _crash_log('_build_home_page: QLabel(id) 创建完成')
        self.lbl_my_id.setStyleSheet('font-size: 20px; font-weight: bold; color: #1677ff;')
        _crash_log('_build_home_page: QLabel(id) 样式设置完成')
        id_row.addWidget(self.lbl_my_id)
        _crash_log('_build_home_page: id_row.addWidget 完成')
        
        btn_copy_id = QPushButton('📋')
        _crash_log('_build_home_page: btn_copy_id 创建完成')
        btn_copy_id.setFixedSize(28, 28)
        btn_copy_id.setToolTip('复制设备ID')
        btn_copy_id.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_id.setCursor(Qt.PointingHandCursor)
        btn_copy_id.clicked.connect(lambda: self._copy_to_clipboard(self._my_device_id))
        id_row.addWidget(btn_copy_id)
        _crash_log('_build_home_page: btn_copy_id 添加完成')
        
        id_row.addStretch()
        card_layout.addLayout(id_row)
        _crash_log('_build_home_page: id_row 添加到 card_layout')
        
        # 密码
        pwd_row = QHBoxLayout()
        pwd_label = QLabel('访问密码:')
        pwd_label.setFixedWidth(70)
        pwd_label.setStyleSheet('color: #666; font-size: 13px;')
        pwd_row.addWidget(pwd_label)
        _crash_log('_build_home_page: 密码行创建')
        
        self.lbl_my_password = QLabel(self._my_password)
        self.lbl_my_password.setStyleSheet('font-size: 16px; font-weight: bold; color: #ff6b00;')
        pwd_row.addWidget(self.lbl_my_password)
        _crash_log('_build_home_page: 密码Label创建')
        
        btn_copy_pwd = QPushButton('📋')
        btn_copy_pwd.setFixedSize(28, 28)
        btn_copy_pwd.setToolTip('复制密码')
        btn_copy_pwd.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_pwd.setCursor(Qt.PointingHandCursor)
        btn_copy_pwd.clicked.connect(lambda: self._copy_to_clipboard(self._my_password))
        pwd_row.addWidget(btn_copy_pwd)
        _crash_log('_build_home_page: 复制密码按钮创建')
        
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
        _crash_log('_build_home_page: 刷新密码按钮创建')
        
        pwd_row.addStretch()
        card_layout.addLayout(pwd_row)
        _crash_log('_build_home_page: 密码行添加完成')
        
        # IP地址
        ip_row = QHBoxLayout()
        ip_label = QLabel('IP 地址:')
        ip_label.setFixedWidth(70)
        ip_label.setStyleSheet('color: #666; font-size: 13px;')
        ip_row.addWidget(ip_label)
        _crash_log('_build_home_page: IP行创建, 准备获取IP')
        
        self.lbl_my_ip = QLabel(self._get_local_ip())
        _crash_log('_build_home_page: IP获取完成')
        self.lbl_my_ip.setStyleSheet('font-size: 13px; color: #333;')
        ip_row.addWidget(self.lbl_my_ip)
        _crash_log('_build_home_page: IP Label添加完成')
        
        btn_copy_ip = QPushButton('📋')
        btn_copy_ip.setFixedSize(28, 28)
        btn_copy_ip.setToolTip('复制IP地址')
        btn_copy_ip.setStyleSheet('background: transparent; border: none; font-size: 14px;')
        btn_copy_ip.setCursor(Qt.PointingHandCursor)
        btn_copy_ip.clicked.connect(lambda: self._copy_to_clipboard(self.lbl_my_ip.text()))
        ip_row.addWidget(btn_copy_ip)
        _crash_log('_build_home_page: btn_copy_ip 创建完成')
        
        ip_row.addStretch()
        card_layout.addLayout(ip_row)
        _crash_log('_build_home_page: ip_row 添加完成')
        
        # 安全提示
        security_tip = QLabel('🔒 端到端加密已启用，仅本人设备可连接')
        security_tip.setStyleSheet('color: #52c41a; font-size: 12px; padding: 8px; background-color: #f6ffed; border-radius: 6px;')
        card_layout.addWidget(security_tip)
        _crash_log('_build_home_page: security_tip 创建完成')
        
        layout.addWidget(self_my_card)
        _crash_log('_build_home_page: self_my_card 添加到 layout')
        
        # ============ 连接卡片 ============
        connect_card = QFrame()
        connect_card.setObjectName('Card')
        card_layout2 = QVBoxLayout(connect_card)
        card_layout2.setSpacing(10)
        card_layout2.setContentsMargins(20, 15, 20, 15)
        _crash_log('_build_home_page: connect_card 创建完成')
        
        title2 = QLabel('🔗 连接远程设备')
        title2.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        card_layout2.addWidget(title2)
        _crash_log('_build_home_page: title2 创建完成')
        
        # 连接方式切换 (设备ID / IP地址) - 紧凑模式
        _crash_log('_build_home_page: 开始 mode_switch_row')
        mode_switch_row = QHBoxLayout()
        mode_switch_row.setSpacing(6)
        self.btn_mode_id = QPushButton('设备ID')
        self.btn_mode_id.setCheckable(True)
        self.btn_mode_id.setChecked(True)
        self.btn_mode_id.setFixedSize(80, 28)
        self.btn_mode_id.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 4px; font-size: 12px;')
        self.btn_mode_id.clicked.connect(lambda: self._set_conn_mode('id'))
        mode_switch_row.addWidget(self.btn_mode_id)
        _crash_log('_build_home_page: btn_mode_id 创建完成')
        
        self.btn_mode_ip = QPushButton('IP地址')
        self.btn_mode_ip.setCheckable(True)
        self.btn_mode_ip.setFixedSize(80, 28)
        self.btn_mode_ip.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 12px;')
        self.btn_mode_ip.clicked.connect(lambda: self._set_conn_mode('ip'))
        mode_switch_row.addWidget(self.btn_mode_ip)
        mode_switch_row.addStretch()
        card_layout2.addLayout(mode_switch_row)
        _crash_log('_build_home_page: mode_switch_row 完成')
        
        # 设备ID/IP输入
        _crash_log('_build_home_page: 开始 id_input_row')
        id_input_row = QHBoxLayout()
        self.id_input_label = QLabel('设备代码:')
        self.id_input_label.setFixedWidth(70)
        self.id_input_label.setStyleSheet('color: #666; font-size: 13px;')
        id_input_row.addWidget(self.id_input_label)
        
        self.input_device_id = QLineEdit()
        self.input_device_id.setPlaceholderText('请输入9位设备ID 或 IP地址')
        self.input_device_id.setFixedHeight(36)
        self.input_device_id.setStyleSheet('font-size: 16px;')
        id_input_row.addWidget(self.input_device_id, 1)
        card_layout2.addLayout(id_input_row)
        _crash_log('_build_home_page: id_input_row 完成')
        
        # 密码输入
        _crash_log('_build_home_page: 开始 pwd_input_row')
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
        _crash_log('_build_home_page: pwd_input_row 完成')
        
        # 连接模式
        _crash_log('_build_home_page: 开始 mode_row')
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_label = QLabel('连接模式:')
        mode_label.setFixedWidth(70)
        mode_label.setStyleSheet('color: #666; font-size: 13px;')
        mode_row.addWidget(mode_label)
        
        self.mode_control = QPushButton('远程控制')
        self.mode_control.setCheckable(True)
        self.mode_control.setChecked(True)
        self.mode_control.setFixedHeight(34)
        self.mode_control.setStyleSheet('background-color: #1677ff; color: white; border: none; border-radius: 6px; padding: 6px 14px; font-size: 13px;')
        self.mode_control.clicked.connect(lambda: self._set_mode('control'))
        mode_row.addWidget(self.mode_control)
        _crash_log('_build_home_page: mode_control 完成')
        
        self.mode_file = QPushButton('文件传输')
        self.mode_file.setCheckable(True)
        self.mode_file.setFixedHeight(34)
        self.mode_file.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; padding: 6px 14px; font-size: 13px;')
        self.mode_file.clicked.connect(lambda: self._set_mode('file'))
        mode_row.addWidget(self.mode_file)
        _crash_log('_build_home_page: mode_file 完成')
        
        self.mode_view = QPushButton('仅观看')
        self.mode_view.setCheckable(True)
        self.mode_view.setFixedHeight(34)
        self.mode_view.setStyleSheet('background-color: white; color: #666; border: 1px solid #d9d9d9; border-radius: 6px; padding: 6px 14px; font-size: 13px;')
        self.mode_view.clicked.connect(lambda: self._set_mode('view'))
        mode_row.addWidget(self.mode_view)
        _crash_log('_build_home_page: mode_view 完成')
        
        mode_row.addStretch()
        card_layout2.addLayout(mode_row)
        _crash_log('_build_home_page: mode_row 完成')
        
        # 连接按钮
        _crash_log('_build_home_page: 开始 btn_connect')
        self.btn_connect = QPushButton('连接')
        self.btn_connect.setObjectName('PrimaryBtn')
        self.btn_connect.setFixedHeight(42)
        self.btn_connect.setStyleSheet('background-color: #1677ff; border: none; border-radius: 8px; padding: 10px 48px; color: white; font-size: 15px; font-weight: bold;')
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        card_layout2.addWidget(self.btn_connect)
        _crash_log('_build_home_page: btn_connect 完成')
        
        # 状态提示
        self.lbl_connect_status = QLabel('')
        self.lbl_connect_status.setStyleSheet('color: #666; font-size: 12px;')
        self.lbl_connect_status.setAlignment(Qt.AlignCenter)
        card_layout2.addWidget(self.lbl_connect_status)
        _crash_log('_build_home_page: lbl_connect_status 完成')
        
        layout.addWidget(connect_card)
        _crash_log('_build_home_page: connect_card 添加到 layout')
        
        # ============ 最近连接 ============
        _crash_log(f'_build_home_page: 检查 device_list, len={len(self._device_list) if self._device_list else 0}')
        if self._device_list:
            recent_card = QFrame()
            recent_card.setObjectName('Card')
            recent_layout = QVBoxLayout(recent_card)
            recent_layout.setSpacing(8)
            recent_layout.setContentsMargins(20, 15, 20, 15)
            
            title3 = QLabel('🕐 最近连接')
            title3.setStyleSheet('font-size: 14px; font-weight: bold; color: #333;')
            recent_layout.addWidget(title3)
            
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setMaximumHeight(200)
            scroll.setStyleSheet('QScrollArea { border: none; }')
            
            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setSpacing(6)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            
            for i, device in enumerate(self._device_list[:5]):
                item = self._create_recent_device_item(device)
                scroll_layout.addWidget(item)
            _crash_log('_build_home_page: recent items 创建完成')
            
            scroll_layout.addStretch()
            scroll.setWidget(scroll_content)
            recent_layout.addWidget(scroll)
            
            layout.addWidget(recent_card)
            _crash_log('_build_home_page: recent_card 添加完成')
        
        layout.addStretch()
        _crash_log('_build_home_page: layout.addStretch 完成')
        
        # 设置滚动区域
        main_scroll.setWidget(page)
        outer_layout.addWidget(main_scroll)
        _crash_log('_build_home_page: scroll.setWidget 完成')
        
        _crash_log('_build_home_page: 返回 outer')
        return outer
    
    def _create_recent_device_item(self, device):
        """创建最近连接设备项"""
        item_widget = QFrame()
        item_widget.setObjectName('DeviceItem')
        item_layout = QHBoxLayout(item_widget)
        item_layout.setSpacing(12)
        
        # 状态指示器
        status = StatusIndicator()
        status.set_color('#52c41a' if device.get('online', False) else '#d9d9d9')
        item_layout.addWidget(status)
        
        # 设备信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        name_label = QLabel(device.get('name', '未知设备'))
        name_label.setStyleSheet('font-weight: bold; color: #333;')
        info_layout.addWidget(name_label)
        
        id_label = QLabel(f"ID: {device.get('id', '-----')} | {device.get('ip', '-----')}")
        id_label.setStyleSheet('color: #999; font-size: 12px;')
        info_layout.addWidget(id_label)
        
        item_layout.addLayout(info_layout, 1)
        
        # 操作按钮
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
        """设置连接模式"""
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
        """切换连接方式 (设备ID / IP地址)"""
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
        """连接按钮点击"""
        device_id = self.input_device_id.text().strip()
        password = self.input_password.text().strip()
        
        if not device_id:
            self.lbl_connect_status.setText('⚠️ 请输入设备ID或IP地址')
            self.lbl_connect_status.setStyleSheet('color: #ff4d4f;')
            return
        
        if not password:
            self.lbl_connect_status.setText('⚠️ 请输入访问密码')
            self.lbl_connect_status.setStyleSheet('color: #ff4d4f;')
            return
        
        self.lbl_connect_status.setText('⏳ 正在连接...')
        self.lbl_connect_status.setStyleSheet('color: #1677ff;')
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText('连接中...')
        
        # 延迟发送连接请求（模拟异步）
        QTimer.singleShot(100, lambda: self._do_connect(device_id, password))
    
    def _do_connect(self, device_id, password):
        """执行连接"""
        # 判断是否是IP地址
        is_ip = '.' in device_id and device_id.replace('.', '').replace(':', '').isdigit()
        if is_ip:
            ip_addr = device_id.split(':')[0] if ':' in device_id else device_id
            self._add_to_device_list(device_id, ip=ip_addr)
        else:
            self._add_to_device_list(device_id)
        
        # 刷新设备列表
        self._refresh_devices_page()
        
        # 发送连接信号给主控制器
        mode = getattr(self, '_current_mode', 'control')
        self.connect_requested.emit(device_id, mode, password)
        
        # 延迟重置按钮状态（连接是异步的，结果由QMessageBox反馈）
        QTimer.singleShot(3000, lambda: self._reset_connect_button())
    
    def _reset_connect_button(self):
        """重置连接按钮"""
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText('🔌 连接')
        self.lbl_connect_status.setText('')
    
    def _quick_connect(self, device):
        """快速连接"""
        self.input_device_id.setText(device.get('id', ''))
        self.input_password.setText(device.get('password', ''))
        self._on_connect_clicked()
    
    def _add_to_device_list(self, device_id, ip=None):
        """添加到设备列表，自动去重（同ID或同IP视为同一设备）"""
        is_ip = '.' in device_id and device_id.replace('.', '').isdigit()
        
        for device in self._device_list:
            # 匹配条件：ID相同，或者IP相同且IP不为空
            id_match = device.get('id') == device_id
            ip_match = ip and device.get('ip') == ip and ip != '局域网'
            # 如果传入的是IP，也检查是否有设备的ID就是这个IP
            if is_ip and device.get('ip') == device_id:
                ip_match = True
            
            if id_match or ip_match:
                device['last_connected'] = datetime.now().isoformat()
                if ip and ip != '局域网':
                    device['ip'] = ip
                if not is_ip:
                    device['id'] = device_id
                device['online'] = True
                self._save_device_list()
                return
        
        self._device_list.append({
            'id': device_id,
            'name': f'设备 {device_id}',
            'ip': ip or '局域网',
            'online': True,
            'favorite': False,
            'last_connected': datetime.now().isoformat(),
            'password': self.input_password.text(),
        })
        self._save_device_list()

    def _update_device_ip(self, device_id, ip):
        """连接成功后更新设备的IP地址"""
        for device in self._device_list:
            if device.get('id') == device_id:
                device['ip'] = ip
                device['online'] = True
                self._save_device_list()
                return

    def _check_devices_online(self):
        """后台线程：通过TCP检测设备在线状态"""
        import socket
        for device in self._device_list:
            ip = device.get('ip', '')
            if not ip or ip == '局域网':
                # 没有IP的设备无法检测，保持原状态
                continue
            # 提取IP（去掉端口）
            host = ip.split(':')[0] if ':' in ip else ip
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((host, 7788))
                sock.close()
                if result == 0:
                    self._online_devices.add(device.get('id'))
                else:
                    self._online_devices.discard(device.get('id'))
            except Exception:
                self._online_devices.discard(device.get('id'))
    
    def _refresh_password(self):
        """刷新密码"""
        self._my_password = self._generate_password()
        set_password(self._my_password)
        self.lbl_my_password.setText(self._my_password)
        self._copy_to_clipboard(self._my_password)
    
    def _edit_password(self):
        """自定义密码"""
        from PyQt5.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, '设置访问密码', '请输入新的访问密码:', text=self._my_password)
        if ok and text.strip():
            self._my_password = text.strip()
            set_password(self._my_password)
            self.lbl_my_password.setText(self._my_password)
            QMessageBox.information(self, '设置成功', f'访问密码已更新为: {self._my_password}')
    
    def _format_id(self, id_str):
        """格式化设备ID"""
        if len(id_str) >= 9:
            return f'{id_str[:3]} {id_str[3:6]} {id_str[6:]}'
        return id_str
    
    def _get_local_ip(self):
        """获取本机局域网IP"""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
    
    def _copy_to_clipboard(self, text):
        """复制到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
    
    def _start_discovery(self):
        """启动设备发现"""
        self._discover_timer = QTimer(self)
        self._discover_timer.timeout.connect(self._discover_devices)
        self._discover_timer.start(3000)
    
    def _discover_devices(self):
        """发现设备 - 后台检测在线状态"""
        import threading
        def _do_check():
            self._check_devices_online()
            # 回到主线程刷新UI
            for device in self._device_list:
                device['online'] = device.get('id') in self._online_devices
            QTimer.singleShot(0, self._refresh_devices_page)
        
        threading.Thread(target=_do_check, daemon=True).start()
    
    def _update_latency(self):
        """更新延迟显示"""
        # 模拟延迟
        self._latency = 10 + (hash(str(time.time())) % 20)
        self.lbl_latency.setText(f'延迟: {self._latency}ms')
        
        if self._latency < 50:
            self.lbl_latency.setStyleSheet('color: #52c41a; font-size: 13px; margin-right: 15px;')
        elif self._latency < 100:
            self.lbl_latency.setStyleSheet('color: #faad14; font-size: 13px; margin-right: 15px;')
        else:
            self.lbl_latency.setStyleSheet('color: #ff4d4f; font-size: 13px; margin-right: 15px;')
    
    def start_latency_monitor(self):
        """启动延迟监控"""
        self._latency_timer.start(1000)
    
    def stop_latency_monitor(self):
        """停止延迟监控"""
        self._latency_timer.stop()
        self.lbl_latency.setText('延迟: --ms')
    
    def _refresh_current_page(self):
        """刷新当前页面"""
        idx = self._stack.currentIndex()
        if idx == 0:
            self._stack.setCurrentIndex(1)
            self._refresh_devices_page()
            self._stack.setCurrentIndex(0)
        elif idx == 1:
            self._refresh_devices_page()
    
    # ============ 设备列表页面 ============
    def _build_devices_page(self):
        """构建设备列表页面"""
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
        
        # 搜索栏
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
        
        # 设备列表
        self.devices_scroll = QScrollArea()
        self.devices_scroll.setWidgetResizable(True)
        self.devices_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self.devices_content = QWidget()
        self.devices_layout = QVBoxLayout(self.devices_content)
        self.devices_layout.setSpacing(10)
        
        self.devices_scroll.setWidget(self.devices_content)
        layout.addWidget(self.devices_scroll, 1)
        
        # 详情面板 - 紧凑双行布局
        self.detail_card = QFrame()
        self.detail_card.setObjectName('Card')
        self.detail_card.setFixedHeight(120)
        self.detail_layout = QVBoxLayout(self.detail_card)
        self.detail_layout.setSpacing(6)
        self.detail_layout.setContentsMargins(20, 12, 20, 12)
        
        # 第一行: 设备名称 + 状态
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
        
        # 第二行: 操作按钮 (水平排列)
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
        
        self.btn_action_terminal = QPushButton('终端')
        self.btn_action_terminal.setFixedHeight(32)
        self.btn_action_terminal.setStyleSheet('background-color: white; color: #333; border: 1px solid #d9d9d9; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_terminal.setCursor(Qt.PointingHandCursor)
        self.btn_action_terminal.setEnabled(False)
        self.btn_action_terminal.clicked.connect(lambda: self._connect_detail_device('terminal'))
        btn_row.addWidget(self.btn_action_terminal)
        
        self.btn_action_restart = QPushButton('重启设备')
        self.btn_action_restart.setFixedHeight(32)
        self.btn_action_restart.setStyleSheet('background-color: white; color: #faad14; border: 1px solid #ffe58f; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_restart.setCursor(Qt.PointingHandCursor)
        self.btn_action_restart.setEnabled(False)
        self.detail_actions_restart = self.btn_action_restart
        btn_row.addWidget(self.btn_action_restart)
        
        self.btn_action_shutdown = QPushButton('关机设备')
        self.btn_action_shutdown.setFixedHeight(32)
        self.btn_action_shutdown.setStyleSheet('background-color: white; color: #ff4d4f; border: 1px solid #ffa39e; border-radius: 6px; padding: 4px 16px; font-size: 12px;')
        self.btn_action_shutdown.setCursor(Qt.PointingHandCursor)
        self.btn_action_shutdown.setEnabled(False)
        btn_row.addWidget(self.btn_action_shutdown)
        
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
        """刷新设备列表页面"""
        # 清空列表
        while self.devices_layout.count():
            item = self.devices_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        search_text = self.input_search.text().strip() if hasattr(self, 'input_search') else ''
        filtered = [d for d in self._device_list 
                    if not search_text or search_text in d.get('id', '') or search_text in d.get('name', '')]
        
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
        """创建设备列表项"""
        item_widget = QFrame()
        item_widget.setObjectName('DeviceItem')
        item_widget.setMinimumHeight(60)
        item_widget.setMaximumHeight(80)
        item_layout = QHBoxLayout(item_widget)
        item_layout.setSpacing(15)
        item_layout.setContentsMargins(15, 8, 15, 8)
        
        # 状态指示器
        status = StatusIndicator()
        is_online = device.get('online', False)
        status.set_color('#52c41a' if is_online else '#d9d9d9')
        item_layout.addWidget(status)
        
        # 设备图标
        icon_label = QLabel('💻')
        icon_label.setStyleSheet('font-size: 28px;')
        item_layout.addWidget(icon_label)
        
        # 设备信息
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
        
        # 操作按钮
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
        
        # 删除按钮
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
        """选择设备"""
        self._current_detail_device = device
        self.lbl_detail_name.setText(device.get('name', '未知设备'))
        self.lbl_detail_id.setText(f"ID: {device.get('id', '-----')}")
        self.lbl_detail_ip.setText(f"IP: {device.get('ip', '-----')}")
        
        is_online = device.get('online', False)
        self.lbl_detail_status.setText('在线' if is_online else '离线')
        self.lbl_detail_status.setStyleSheet('font-size: 12px; color: #52c41a;' if is_online else 'font-size: 12px; color: #999;')
        
        # 启用操作按钮
        for btn in [self.btn_action_control, self.btn_action_file,
                    self.btn_action_terminal, self.btn_action_restart,
                    self.btn_action_shutdown, self.btn_action_delete]:
            btn.setEnabled(True)
    
    def _toggle_favorite(self, device):
        """切换收藏"""
        device['favorite'] = not device.get('favorite', False)
        self._save_device_list()
        self._refresh_devices_page()
    
    def _confirm_remove_device(self, device):
        """确认删除单个设备"""
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
        """从设备列表删除单个设备"""
        if device in self._device_list:
            self._device_list.remove(device)
        self._save_device_list()
        # 如果详情面板正显示该设备, 清空并禁用按钮
        if self._current_detail_device is device:
            self._current_detail_device = None
            self.lbl_detail_name.setText('请选择设备')
            self.lbl_detail_id.setText('')
            self.lbl_detail_ip.setText('')
            self.lbl_detail_status.setText('')
            for btn in [self.btn_action_control, self.btn_action_file,
                        self.btn_action_terminal, self.btn_action_restart,
                        self.btn_action_shutdown, self.btn_action_delete]:
                btn.setEnabled(False)
        self._refresh_devices_page()
        self._refresh_home_page()
    
    def _clear_all_devices(self):
        """清空全部设备记录"""
        if not self._device_list:
            return
        ret = QMessageBox.question(
            self, '清空设备列表',
            f'确定要清空全部 {len(self._device_list)} 条设备记录吗？\n此操作不可恢复！',
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            self._device_list.clear()
            self._save_device_list()
            self._current_detail_device = None
            self.lbl_detail_name.setText('请选择设备')
            self.lbl_detail_id.setText('')
            self.lbl_detail_ip.setText('')
            self.lbl_detail_status.setText('')
            for btn in [self.btn_action_control, self.btn_action_file,
                        self.btn_action_terminal, self.btn_action_restart,
                        self.btn_action_shutdown, self.btn_action_delete]:
                btn.setEnabled(False)
            self._refresh_devices_page()
            self._refresh_home_page()
    
    def _refresh_home_page(self):
        """重建主页 (最近连接列表随删除/添加变化)"""
        old = self._stack.widget(0)
        current = self._stack.currentWidget()
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.insertWidget(0, self._build_home_page())
        if current is old or current is None:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentWidget(current)
    
    def _connect_detail_device(self, mode):
        """从详情面板连接设备"""
        if not self._current_detail_device:
            return
        
        device_id = self._current_detail_device.get('id', '')
        password = self._current_detail_device.get('password', '')
        
        if not password:
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(self, '需要密码', '该设备需要访问密码，请在主页输入密码后连接。', 
                                        QMessageBox.Ok)
            return
        
        self.connect_requested.emit(device_id, mode, password)
    
    # ============ 高级设置页面 ============
    def _build_settings_page(self):
        """构建高级设置页面"""
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
        tabs.setDocumentMode(True)
        
        # 基本设置
        basic_tab = QWidget()
        basic_layout = QVBoxLayout(basic_tab)
        basic_layout.setSpacing(15)
        
        group1 = QGroupBox('常规设置')
        form1 = QFormLayout()
        
        self.chk_autostart = QCheckBox('开机自启动 MyDesk')
        self.chk_autostart.setChecked(self._get_setting('autostart', False))
        form1.addRow(self.chk_autostart)
        
        self.chk_prevent_sleep = QCheckBox('防止系统休眠（远程会话中）')
        self.chk_prevent_sleep.setChecked(self._get_setting('prevent_sleep', True))
        form1.addRow(self.chk_prevent_sleep)
        
        self.chk_show_tray = QCheckBox('最小化到系统托盘')
        self.chk_show_tray.setChecked(self._get_setting('show_tray', True))
        form1.addRow(self.chk_show_tray)
        
        group1.setLayout(form1)
        basic_layout.addWidget(group1)
        
        group2 = QGroupBox('文件传输')
        form2 = QFormLayout()
        
        self.edit_save_path = QLineEdit(self._get_setting('save_path', os.path.expanduser('~')))
        self.edit_save_path.setFixedHeight(36)
        form2.addRow('默认保存路径:', self.edit_save_path)
        
        self.btn_browse = QPushButton('浏览...')
        self.btn_browse.setFixedHeight(36)
        self.btn_browse.clicked.connect(self._browse_save_path)
        form2.addRow('', self.btn_browse)
        
        group2.setLayout(form2)
        basic_layout.addWidget(group2)
        
        basic_layout.addStretch()
        tabs.addTab(basic_tab, '基本设置')
        
        # 连接设置
        connect_tab = QWidget()
        connect_layout = QVBoxLayout(connect_tab)
        connect_layout.setSpacing(15)
        
        group3 = QGroupBox('连接设置')
        form3 = QFormLayout()
        
        self.spn_port = QSpinBox()
        self.spn_port.setRange(1024, 65535)
        self.spn_port.setValue(self._get_setting('port', 7788))
        form3.addRow('监听端口:', self.spn_port)
        
        self.chk_lan_only = QCheckBox('仅局域网模式')
        self.chk_lan_only.setChecked(self._get_setting('lan_only', False))
        form3.addRow(self.chk_lan_only)
        
        self.cmb_encryption = QComboBox()
        self.cmb_encryption.addItems(['AES-256', 'AES-128', '无加密'])
        self.cmb_encryption.setCurrentIndex(self._get_setting('encryption', 0))
        form3.addRow('加密方式:', self.cmb_encryption)
        
        group3.setLayout(form3)
        connect_layout.addWidget(group3)
        
        connect_layout.addStretch()
        tabs.addTab(connect_tab, '连接设置')
        
        # 显示设置
        display_tab = QWidget()
        display_layout = QVBoxLayout(display_tab)
        display_layout.setSpacing(15)
        
        group4 = QGroupBox('画质设置')
        form4 = QFormLayout()
        
        self.cmb_quality = QComboBox()
        self.cmb_quality.addItems(['流畅 (低)', '清晰 (中)', '高清 (高)', '原画 (极致)'])
        self.cmb_quality.setCurrentIndex(self._get_setting('quality', 2))
        form4.addRow('默认画质:', self.cmb_quality)
        
        self.cmb_fps = QComboBox()
        self.cmb_fps.addItems([str(x) for x in [15, 24, 30, 45, 60]])
        self.cmb_fps.setCurrentText(str(self._get_setting('fps', 30)))
        form4.addRow('帧率上限:', self.cmb_fps)
        
        self.chk_auto_quality = QCheckBox('根据网络自动调整画质')
        self.chk_auto_quality.setChecked(self._get_setting('auto_quality', True))
        form4.addRow(self.chk_auto_quality)
        
        group4.setLayout(form4)
        display_layout.addWidget(group4)
        
        display_layout.addStretch()
        tabs.addTab(display_tab, '显示设置')
        
        # 安全设置
        security_tab = QWidget()
        security_layout = QVBoxLayout(security_tab)
        security_layout.setSpacing(15)
        
        group5 = QGroupBox('安全设置')
        form5 = QFormLayout()
        
        self.edit_access_password = QLineEdit(self._get_setting('access_password', ''))
        self.edit_access_password.setPlaceholderText('设置访问密码')
        self.edit_access_password.setEchoMode(QLineEdit.Password)
        self.edit_access_password.setFixedHeight(36)
        form5.addRow('访问密码:', self.edit_access_password)
        
        self.chk_two_factor = QCheckBox('启用二次验证（连接时需确认）')
        self.chk_two_factor.setChecked(self._get_setting('two_factor', False))
        form5.addRow(self.chk_two_factor)
        
        self.chk_ip_whitelist = QCheckBox('仅允许白名单IP连接')
        self.chk_ip_whitelist.setChecked(self._get_setting('ip_whitelist', False))
        form5.addRow(self.chk_ip_whitelist)
        
        self.edit_whitelist = QLineEdit(self._get_setting('whitelist_ips', ''))
        self.edit_whitelist.setPlaceholderText('多个IP用逗号分隔')
        self.edit_whitelist.setFixedHeight(36)
        form5.addRow('白名单IP:', self.edit_whitelist)
        
        group5.setLayout(form5)
        security_layout.addWidget(group5)
        
        security_layout.addStretch()
        tabs.addTab(security_tab, '安全设置')
        
        # 保存按钮
        btn_save = QPushButton('💾 保存设置')
        btn_save.setObjectName('PrimaryBtn')
        btn_save.setFixedHeight(44)
        btn_save.clicked.connect(self._save_settings)
        layout.addWidget(btn_save)
        
        layout.addWidget(tabs)
        
        scroll.setWidget(page)
        outer_layout.addWidget(scroll)
        return outer
    
    def _get_setting(self, key, default=None):
        """获取设置值"""
        try:
            file_path = os.path.join(DATA_DIR, 'settings.json')
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                return settings.get(key, default)
        except Exception:
            pass
        return default
    
    def _save_settings(self):
        """保存设置"""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            file_path = os.path.join(DATA_DIR, 'settings.json')
            settings = {
                'autostart': self.chk_autostart.isChecked(),
                'prevent_sleep': self.chk_prevent_sleep.isChecked(),
                'show_tray': self.chk_show_tray.isChecked(),
                'save_path': self.edit_save_path.text(),
                'port': self.spn_port.value(),
                'lan_only': self.chk_lan_only.isChecked(),
                'encryption': self.cmb_encryption.currentIndex(),
                'quality': self.cmb_quality.currentIndex(),
                'fps': int(self.cmb_fps.currentText()),
                'auto_quality': self.chk_auto_quality.isChecked(),
                'access_password': self.edit_access_password.text(),
                'two_factor': self.chk_two_factor.isChecked(),
                'ip_whitelist': self.chk_ip_whitelist.isChecked(),
                'whitelist_ips': self.edit_whitelist.text(),
            }
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, '成功', '✅ 设置已保存！')
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, '错误', f'保存设置失败: {e}')
    
    def _browse_save_path(self):
        """浏览保存路径"""
        from PyQt5.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, '选择保存路径')
        if path:
            self.edit_save_path.setText(path)
    
    # ============ 公共方法 ============
    def get_device_id(self):
        return self._my_device_id
    
    def get_password(self):
        return self._my_password
