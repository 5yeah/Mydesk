# -*- coding: utf-8 -*-
"""MyDesk 主控端查看窗口 (ToDesk 风格)

功能:
1. 屏幕帧接收与渲染
2. 鼠标键盘捕获
3. ToDesk风格双栏文件传输界面
4. 全屏悬浮工具栏
5. 多屏幕支持
"""
import sys
import os
import struct
import threading
import json
import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFormLayout, QLineEdit, QFileDialog,
    QToolBar, QAction, QStatusBar, QProgressBar, QGroupBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QSplitter, QMessageBox, QInputDialog,
    QHeaderView, QFileSystemModel, QMenu, QAction, QSizePolicy, QStyle,
    QComboBox,
)
from PyQt5.QtCore import Qt, QObject, QRect, pyqtSignal, QTimer, QSize, QDir, QModelIndex
from PyQt5.QtGui import QPixmap, QPainter, QIcon, QFont, QColor, QCursor

from controller import Controller
from protocol import MSG_SCREEN, MSG_CONTROL, MSG_INPUT, MSG_FILE, MSG_CLIPBOARD
from input_control import (
    EV_MOUSE_MOVE, EV_MOUSE_DOWN, EV_MOUSE_UP, EV_MOUSE_SCROLL,
    EV_KEY_DOWN, EV_KEY_UP,
)
from file_transfer import FT_OFFER, FT_ACCEPT, FT_DATA, FT_DONE, FT_PULL, FT_ERROR

# 默认接收目录
DEFAULT_RECV_DIR = os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 调试日志文件
def _debug_log(msg):
    """写入调试日志文件"""
    try:
        log_file = os.path.join(LOG_DIR, 'viewer_debug.log')
        timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f'[{timestamp}] {msg}\n')
    except Exception:
        pass

# Qt 鼠标按钮 -> MyDesk 按钮编号
_QT_BTN = {Qt.LeftButton: 1, Qt.RightButton: 2, Qt.MidButton: 3}


def format_size(size):
    """格式化文件大小"""
    if size < 1024:
        return f'{size} B'
    elif size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    elif size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    else:
        return f'{size / (1024 * 1024 * 1024):.1f} GB'


class LogBridge(QObject):
    """跨线程日志桥接"""
    log_ready = pyqtSignal(str)


class ScreenWidget(QWidget):
    """等比缩放显示远程画面, 并捕获键鼠事件"""

    def __init__(self, controller, screen_idx=0, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.screen_idx = screen_idx
        self.pm = None
        self.remote_w = 0
        self.remote_h = 0
        self._img_rect = QRect()
        self.setStyleSheet('background:#1a1a2e;')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_pixmap(self, pm):
        self.pm = pm
        self.update()

    def set_remote_size(self, w, h):
        self.remote_w = w
        self.remote_h = h
        self.update()  # 立即重绘

    def paintEvent(self, _event):
        painter = QPainter(self)
        if not self.pm:
            painter.fillRect(self.rect(), Qt.black)
            painter.setPen(Qt.white)
            painter.drawText(self.rect(), Qt.AlignCenter, '等待画面...')
            return
        
        # 如果没有远程尺寸信息，使用画面的原始尺寸
        if self.remote_w == 0 or self.remote_h == 0:
            self.remote_w = self.pm.width()
            self.remote_h = self.pm.height()
        
        scaled = self.pm.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        self._img_rect = QRect(x, y, scaled.width(), scaled.height())
        painter.fillRect(self.rect(), Qt.black)
        painter.drawPixmap(x, y, scaled)

    def _map_pos(self, pos):
        if self.remote_w == 0 or self._img_rect.width() == 0:
            return None
        rx = int((pos.x() - self._img_rect.x()) / self._img_rect.width() * self.remote_w)
        ry = int((pos.y() - self._img_rect.y()) / self._img_rect.height() * self.remote_h)
        return max(0, min(rx, self.remote_w - 1)), max(0, min(ry, self.remote_h - 1))

    def mouseMoveEvent(self, event):
        p = self._map_pos(event.pos())
        if p:
            self.controller.send(MSG_INPUT, struct.pack('<BHH', EV_MOUSE_MOVE, p[0], p[1]))

    def mousePressEvent(self, event):
        self.setFocus()
        self._send_pos(event)
        btn = _QT_BTN.get(event.button())
        if btn:
            self.controller.send(MSG_INPUT, struct.pack('<BB', EV_MOUSE_DOWN, btn))

    def mouseReleaseEvent(self, event):
        self._send_pos(event)
        btn = _QT_BTN.get(event.button())
        if btn:
            self.controller.send(MSG_INPUT, struct.pack('<BB', EV_MOUSE_UP, btn))

    def wheelEvent(self, event):
        delta = event.angleDelta()
        self.controller.send(MSG_INPUT, struct.pack('<Bhh', EV_MOUSE_SCROLL, delta.x(), delta.y()))

    def _send_pos(self, event):
        p = self._map_pos(event.pos())
        if p:
            self.controller.send(MSG_INPUT, struct.pack('<BHH', EV_MOUSE_MOVE, p[0], p[1]))

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        vk = event.nativeVirtualKey()
        if vk:
            self.controller.send(MSG_INPUT, struct.pack('<BH', EV_KEY_DOWN, vk))

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        vk = event.nativeVirtualKey()
        if vk:
            self.controller.send(MSG_INPUT, struct.pack('<BH', EV_KEY_UP, vk))


class ScreenReceiver(QObject):
    frame_ready = pyqtSignal(bytes, int, int)  # jpeg, screen_idx, total_screens
    frame_received = pyqtSignal(bytes)  # 简化信号，仅传递jpeg数据
    info_ready = pyqtSignal(int, int, int, int)  # w, h, screen_idx, total_screens
    disconnected = pyqtSignal()
    screen_list_ready = pyqtSignal(list)  # [(idx, w, h), ...]
    clipboard_ready = pyqtSignal(str)     # 远程剪贴板文本
    
    _debug_frame_count = 0  # 调试用

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.controller.set_handler(self._on_msg)
        self._total_screens = 1
        self._debug_logged = False
        _debug_log(f'[ScreenReceiver] 初始化完成, handlers_count={len(controller._msg_handlers)}')

    def _on_msg(self, mtype, payload):
        # 添加首次消息日志
        if not self._debug_logged:
            self._debug_logged = True
            _debug_log(f'[ScreenReceiver] _on_msg 首次被调用: mtype={mtype}, payload_len={len(payload)}')
        
        if mtype == MSG_SCREEN:
            # 新格式: 前4字节是屏幕索引, 后面是JPEG数据
            if len(payload) > 4:
                screen_idx = struct.unpack('<I', payload[:4])[0]
                jpeg_data = payload[4:]
                
                # 调试日志 (写入文件)
                ScreenReceiver._debug_frame_count += 1
                if ScreenReceiver._debug_frame_count <= 5:
                    _debug_log(f'[屏幕帧] 收到帧: idx={screen_idx}, size={len(jpeg_data)}, count={ScreenReceiver._debug_frame_count}')
                
                self.frame_ready.emit(jpeg_data, screen_idx, self._total_screens)
                self.frame_received.emit(jpeg_data)
            else:
                _debug_log(f'[屏幕帧] 收到短帧: size={len(payload)}')
                self.frame_ready.emit(payload, 0, self._total_screens)
                self.frame_received.emit(payload)
        elif mtype == MSG_CLIPBOARD:
            # 剪贴板文本同步 (载荷为 UTF-8 文本)
            text = payload.decode('utf-8', errors='replace')
            self.clipboard_ready.emit(text)
        elif mtype == MSG_CONTROL:
            try:
                cmd = payload.decode('utf-8')
                if cmd.startswith('SCREEN:'):
                    # 尝试新格式: "SCREEN:{idx}:{w}x{h}"
                    rest = cmd[len('SCREEN:'):]  # "{idx}:{w}x{h}" or "{w}x{h}"
                    screen_idx = 0
                    w = 0
                    h = 0
                    
                    # 先尝试新格式 (包含屏幕索引)
                    parts = rest.split(':', 1)
                    if len(parts) == 2:
                        # 检查第一部分是否是数字 (屏幕索引)
                        try:
                            screen_idx = int(parts[0])
                            size_str = parts[1]
                            size_parts = size_str.split('x')
                            if len(size_parts) == 2:
                                w, h = int(size_parts[0]), int(size_parts[1])
                        except ValueError:
                            # 不是新格式，回退到旧格式
                            size_parts = rest.split('x')
                            if len(size_parts) == 2:
                                w, h = int(size_parts[0]), int(size_parts[1])
                    else:
                        # 旧格式: "SCREEN:{w}x{h}"
                        size_parts = rest.split('x')
                        if len(size_parts) == 2:
                            w, h = int(size_parts[0]), int(size_parts[1])
                    
                    if w > 0 and h > 0:
                        _debug_log(f'[屏幕尺寸] idx={screen_idx}, {w}x{h}')
                        self.info_ready.emit(w, h, screen_idx, self._total_screens)
                        
                elif cmd.startswith('MULTI_SCREEN:'):
                    # 多屏幕信息: "MULTI_SCREEN:2"
                    self._total_screens = int(cmd.split(':')[1])
                    self.info_ready.emit(0, 0, 0, self._total_screens)
                elif cmd.startswith('SCREEN_LIST:'):
                    # 屏幕列表: "SCREEN_LIST:0:1920:1080,1:1920:1080"
                    screens = []
                    for s in cmd.split(':', 1)[1].split(','):
                        parts = s.split(':')
                        if len(parts) == 3:
                            screens.append((int(parts[0]), int(parts[1]), int(parts[2])))
                    if screens:
                        self._total_screens = len(screens)
                        self.screen_list_ready.emit(screens)
            except Exception as e:
                _debug_log(f'[错误] 解析控制消息异常: {e}')

    def start(self):
        """启动接收循环 (现在Controller.connect()会自动启动recv_loop)"""
        pass  # 不需要启动新线程了，Controller已经处理了


# ToDesk 风格样式表
_TODESK_STYLE = """
QMainWindow { background: #f5f5f7; }
QToolBar { 
    background: #ffffff; border: none; border-bottom: 1px solid #e0e0e0;
    padding: 4px 8px; spacing: 4px;
}
QToolBar QPushButton {
    background: transparent; border: none; border-radius: 6px;
    padding: 6px 12px; color: #333; font-size: 13px;
}
QToolBar QPushButton:hover { background: #e8eaf6; }
QToolBar QPushButton:pressed { background: #c5cae9; }
QToolBar QPushButton:checked { background: #e8eaf6; color: #1a73e8; }
QSlider::groove:horizontal {
    border: none; height: 4px; background: #e0e0e0; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #1a73e8; border: none; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #1557b0; }
QLabel { color: #333; font-size: 12px; }
QStatusBar { background: #ffffff; border-top: 1px solid #e0e0e0; color: #666; }
QProgressBar {
    border: none; border-radius: 4px; background: #e0e0e0; height: 6px; text-align: center;
}
QProgressBar::chunk { background: #1a73e8; border-radius: 4px; }
QGroupBox {
    border: 1px solid #e0e0e0; border-radius: 8px;
    margin-top: 12px; padding-top: 16px;
    font-size: 13px; color: #333;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 0 4px;
}
QTreeWidget {
    border: 1px solid #e0e0e0; border-radius: 6px;
    background: white; font-size: 12px;
}
QTreeWidget::item:selected {
    background: #e8eaf6; color: #1a73e8;
}
QTreeWidget::item:hover {
    background: #f5f5f5;
}
QHeaderView::section {
    background: #f8f9fa; padding: 6px; border: none;
    border-right: 1px solid #e0e0e0;
    font-weight: bold; color: #555;
}
QDialog {
    background: #f5f5f7;
}
QPushButton#PrimaryBtn {
    background: #1a73e8; color: white; border: none;
    border-radius: 6px; padding: 8px 20px; font-weight: bold;
}
QPushButton#PrimaryBtn:hover { background: #1557b0; }
QPushButton#SecondaryBtn {
    background: white; color: #1a73e8; border: 1px solid #1a73e8;
    border-radius: 6px; padding: 8px 20px;
}
QPushButton#SecondaryBtn:hover { background: #e8eaf6; }
"""

# 悬浮工具栏样式
_FLOAT_BAR_STYLE = """
QWidget#FloatBar {
    background: rgba(255, 255, 255, 230);
    border-radius: 12px;
    border: 1px solid rgba(0, 0, 0, 30);
}
QWidget#FloatBar QPushButton {
    background: transparent; border: none; border-radius: 8px;
    padding: 8px 12px; color: #333; font-size: 13px;
}
QWidget#FloatBar QPushButton:hover {
    background: #e8eaf6;
}
"""


class FloatToolBar(QWidget):
    """全屏悬浮工具栏 - ToDesk风格"""
    
    quality_changed = pyqtSignal(int)
    fps_changed = pyqtSignal(int)
    fullscreen_exit = pyqtSignal()
    file_transfer_requested = pyqtSignal()
    clipboard_toggled = pyqtSignal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('FloatBar')
        self.setStyleSheet(_FLOAT_BAR_STYLE)
        self.setFixedSize(660, 50)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        
        # 连接状态
        self.lbl_status = QLabel('● 已连接')
        self.lbl_status.setStyleSheet('color:#22c55e; font-weight:bold;')
        layout.addWidget(self.lbl_status)
        
        layout.addSpacing(10)
        
        # 画质
        layout.addWidget(QLabel('画质:'))
        self.sl_quality = QSlider(Qt.Horizontal)
        self.sl_quality.setRange(10, 100)
        self.sl_quality.setValue(65)
        self.sl_quality.setFixedWidth(80)
        self.sl_quality.valueChanged.connect(self.quality_changed.emit)
        layout.addWidget(self.sl_quality)
        
        self.lbl_quality = QLabel('65')
        self.lbl_quality.setFixedWidth(30)
        self.sl_quality.valueChanged.connect(lambda v: self.lbl_quality.setText(str(v)))
        layout.addWidget(self.lbl_quality)
        
        layout.addSpacing(10)
        
        # 帧率
        layout.addWidget(QLabel('帧率:'))
        self.sl_fps = QSlider(Qt.Horizontal)
        self.sl_fps.setRange(5, 60)
        self.sl_fps.setValue(30)
        self.sl_fps.setFixedWidth(60)
        self.sl_fps.valueChanged.connect(self.fps_changed.emit)
        layout.addWidget(self.sl_fps)
        
        self.lbl_fps = QLabel('30')
        self.lbl_fps.setFixedWidth(25)
        self.sl_fps.valueChanged.connect(lambda v: self.lbl_fps.setText(str(v)))
        layout.addWidget(self.lbl_fps)
        
        layout.addSpacing(10)
        
        # 文件传输
        btn_ft = QPushButton('📁 文件传输')
        btn_ft.clicked.connect(self.file_transfer_requested.emit)
        layout.addWidget(btn_ft)

        # 剪贴板同步
        self.btn_clipboard = QPushButton('📋 剪贴板')
        self.btn_clipboard.setCheckable(True)
        self.btn_clipboard.setChecked(True)
        self.btn_clipboard.toggled.connect(self.clipboard_toggled.emit)
        layout.addWidget(self.btn_clipboard)
        
        layout.addSpacing(10)
        
        # 退出全屏
        btn_exit = QPushButton('✕ 退出全屏')
        btn_exit.clicked.connect(self.fullscreen_exit.emit)
        btn_exit.setStyleSheet('color:#ef4444; font-weight:bold;')
        layout.addWidget(btn_exit)
        
        # 拖动支持
        self._drag_pos = None
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.pos()
            
    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            
    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class FileTransferDialog(QDialog):
    """ToDesk 风格文件传输对话框
    
    左侧: 本地文件浏览器
    右侧: 远程文件浏览器
    支持: 上传、下载、删除、拖拽
    """
    
    file_upload_requested = pyqtSignal(str)  # 本地文件路径 -> 上传
    file_download_requested = pyqtSignal(str)  # 远程文件路径 -> 下载
    file_delete_requested = pyqtSignal(str, bool)  # 路径, is_remote
    remote_list_ready = pyqtSignal(dict)  # 远程目录列表信号 (线程安全)
    remove_result_ready = pyqtSignal(dict)  # 删除结果信号 (线程安全)
    
    def __init__(self, controller, recv_dir='.', parent=None):
        super().__init__(parent)
        self.controller = controller
        self.recv_dir = recv_dir
        self.setWindowTitle('MyDesk 文件传输')
        self.resize(900, 600)
        self.setStyleSheet(_TODESK_STYLE)
        
        # 当前路径
        self.local_path = os.path.expanduser('~')
        self.remote_path = '/'  # 远程根路径，先显示所有驱动器
        
        # 连接信号 (用于线程安全的UI更新)
        self.remote_list_ready.connect(self._update_remote_list)
        self.remove_result_ready.connect(self._update_remove_result)
        
        # 设置回调
        self.controller.file_transfer.on_list_result = self._on_remote_list
        self.controller.file_transfer.on_remove_result = self._on_remove_result
        
        _debug_log('[文件传输] 初始化开始')
        
        try:
            self._build_ui()
            _debug_log('[文件传输] UI构建完成')
            self._refresh_local()
            _debug_log('[文件传输] 本地文件已加载')
            # 延迟加载远程文件，等待窗口显示后再加载
            QTimer.singleShot(100, self._safe_refresh_remote)
        except Exception as e:
            import traceback
            traceback.print_exc()
            _debug_log(f'[文件传输] 初始化异常: {e}')
            QMessageBox.critical(self, '错误', f'文件传输界面初始化失败:\n{e}')
            self.close()
    
    def _safe_refresh_remote(self):
        """安全地刷新远程文件列表"""
        try:
            _debug_log('[文件传输] 开始加载远程文件列表')
            self._refresh_remote()
            _debug_log('[文件传输] 远程文件列表请求已发送')
        except Exception as e:
            import traceback
            traceback.print_exc()
            _debug_log(f'[文件传输] 加载远程文件异常: {e}')
            QMessageBox.warning(self, '警告', f'加载远程文件列表失败:\n{e}')
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        
        # 顶部标题
        title_row = QHBoxLayout()
        title = QLabel('文件传输')
        title.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        title_row.addWidget(title)
        title_row.addStretch()
        layout.addLayout(title_row)
        
        # 双栏文件浏览器
        splitter = QSplitter(Qt.Horizontal)
        
        # 左侧: 本地文件
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        left_header = QLabel('📂 本地电脑')
        left_header.setStyleSheet('font-weight: bold; color: #333; padding: 5px;')
        left_layout.addWidget(left_header)
        
        # 本地路径导航
        nav_row = QHBoxLayout()
        self.btn_local_back = QPushButton('⬅')
        self.btn_local_back.setFixedWidth(35)
        self.btn_local_back.clicked.connect(self._local_go_back)
        nav_row.addWidget(self.btn_local_back)
        
        # 本地驱动器选择
        self.cmb_local_drive = QComboBox()
        self.cmb_local_drive.setFixedWidth(80)
        self.cmb_local_drive.currentIndexChanged.connect(self._on_local_drive_changed)
        self._update_local_drives()
        nav_row.addWidget(self.cmb_local_drive)
        
        self.lbl_local_path = QLabel(self.local_path)
        self.lbl_local_path.setStyleSheet('color: #666; padding: 5px;')
        self.lbl_local_path.setMinimumWidth(200)
        nav_row.addWidget(self.lbl_local_path, 1)
        
        self.btn_local_refresh = QPushButton('🔄')
        self.btn_local_refresh.setFixedWidth(35)
        self.btn_local_refresh.clicked.connect(self._refresh_local)
        nav_row.addWidget(self.btn_local_refresh)
        
        left_layout.addLayout(nav_row)
        
        # 本地文件树
        self.local_tree = QTreeWidget()
        self.local_tree.setHeaderLabels(['名称', '大小', '类型', '修改时间'])
        self.local_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.local_tree.setRootIsDecorated(False)
        self.local_tree.setSortingEnabled(False)
        self.local_tree.itemDoubleClicked.connect(self._on_local_double_click)
        self.local_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_tree.customContextMenuRequested.connect(self._local_context_menu)
        left_layout.addWidget(self.local_tree, 1)
        
        # 本地操作按钮
        local_btn_row = QHBoxLayout()
        self.btn_upload = QPushButton('⬆ 上传到远程')
        self.btn_upload.setObjectName('PrimaryBtn')
        self.btn_upload.clicked.connect(self._upload_selected)
        local_btn_row.addWidget(self.btn_upload)
        
        self.btn_local_delete = QPushButton('🗑 删除本地')
        self.btn_local_delete.setObjectName('SecondaryBtn')
        self.btn_local_delete.clicked.connect(self._delete_local_selected)
        local_btn_row.addWidget(self.btn_local_delete)
        
        local_btn_row.addStretch()
        left_layout.addLayout(local_btn_row)
        
        splitter.addWidget(left_widget)
        
        # 右侧: 远程文件
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        right_header = QLabel('🌐 远程电脑')
        right_header.setStyleSheet('font-weight: bold; color: #1a73e8; padding: 5px;')
        right_layout.addWidget(right_header)
        
        # 远程路径导航
        rnav_row = QHBoxLayout()
        self.btn_remote_back = QPushButton('⬅')
        self.btn_remote_back.setFixedWidth(35)
        self.btn_remote_back.clicked.connect(self._remote_go_back)
        rnav_row.addWidget(self.btn_remote_back)
        
        # 远程驱动器选择
        self.cmb_remote_drive = QComboBox()
        self.cmb_remote_drive.setFixedWidth(80)
        self.cmb_remote_drive.currentIndexChanged.connect(self._on_remote_drive_changed)
        self.cmb_remote_drive.addItem('\\', '/')  # 根目录
        rnav_row.addWidget(self.cmb_remote_drive)
        
        self.lbl_remote_path = QLabel(self.remote_path)
        self.lbl_remote_path.setStyleSheet('color: #666; padding: 5px;')
        self.lbl_remote_path.setMinimumWidth(200)
        rnav_row.addWidget(self.lbl_remote_path, 1)
        
        self.btn_remote_refresh = QPushButton('🔄')
        self.btn_remote_refresh.setFixedWidth(35)
        self.btn_remote_refresh.clicked.connect(self._refresh_remote)
        rnav_row.addWidget(self.btn_remote_refresh)
        
        right_layout.addLayout(rnav_row)
        
        # 远程文件树
        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(['名称', '大小', '类型', '修改时间'])
        self.remote_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.remote_tree.setRootIsDecorated(False)
        self.remote_tree.setSortingEnabled(False)
        self.remote_tree.itemDoubleClicked.connect(self._on_remote_double_click)
        self.remote_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_tree.customContextMenuRequested.connect(self._remote_context_menu)
        right_layout.addWidget(self.remote_tree, 1)
        
        # 远程操作按钮
        remote_btn_row = QHBoxLayout()
        self.btn_download = QPushButton('⬇ 下载到本地')
        self.btn_download.setObjectName('PrimaryBtn')
        self.btn_download.clicked.connect(self._download_selected)
        remote_btn_row.addWidget(self.btn_download)
        
        self.btn_remote_delete = QPushButton('🗑 删除远程')
        self.btn_remote_delete.setObjectName('SecondaryBtn')
        self.btn_remote_delete.clicked.connect(self._delete_remote_selected)
        remote_btn_row.addWidget(self.btn_remote_delete)
        
        remote_btn_row.addStretch()
        right_layout.addLayout(remote_btn_row)
        
        splitter.addWidget(right_widget)
        splitter.setSizes([430, 430])
        
        layout.addWidget(splitter, 1)
        
        # 底部进度条
        self.progress = QProgressBar()
        self.progress.setFixedHeight(20)
        self.progress.hide()
        layout.addWidget(self.progress)
        
        # 底部按钮
        bottom_row = QHBoxLayout()
        self.lbl_status = QLabel('就绪')
        self.lbl_status.setStyleSheet('color: #666;')
        bottom_row.addWidget(self.lbl_status, 1)
        
        btn_close = QPushButton('关闭')
        btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(btn_close)
        
        layout.addLayout(bottom_row)
    
    # ============ 本地文件操作 ============
    def _update_local_drives(self):
        """更新本地驱动器列表"""
        self.cmb_local_drive.blockSignals(True)
        self.cmb_local_drive.clear()
        import string
        drives_found = []
        # 使用更安全的方式获取所有驱动器
        try:
            for letter in string.ascii_uppercase:
                drive = f'{letter}:/'
                if os.path.exists(drive):
                    drives_found.append(f'{letter}:')
        except Exception:
            pass
        
        # 如果获取失败，使用回退方式
        if not drives_found:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                bitmask = kernel32.GetLogicalDrives()
                for i, letter in enumerate(string.ascii_uppercase):
                    if bitmask & (1 << i):
                        drives_found.append(f'{letter}:')
            except Exception:
                # 最终回退：添加常见驱动器
                for letter in 'CDEFGH':
                    drives_found.append(f'{letter}:')
        
        for drive in drives_found:
            self.cmb_local_drive.addItem(drive, drive)
        self.cmb_local_drive.blockSignals(False)
    
    def _on_local_drive_changed(self, index):
        """本地驱动器切换"""
        drive = self.cmb_local_drive.itemData(index)
        if drive and os.path.exists(drive):
            self.local_path = drive
            self._refresh_local()
    
    def _on_remote_drive_changed(self, index):
        """远程驱动器切换"""
        drive = self.cmb_remote_drive.itemData(index)
        if drive:
            self.remote_path = drive
            self._refresh_remote()
    
    def _update_remote_drives_from_list(self, items):
        """从远程根目录列表更新驱动器选择"""
        if self.remote_path == '/':
            self.cmb_remote_drive.blockSignals(True)
            self.cmb_remote_drive.clear()
            self.cmb_remote_drive.addItem('\\', '/')
            for item in items:
                if item['is_dir'] and item['name'].endswith(':'):
                    self.cmb_remote_drive.addItem(item['name'], '/' + item['name'])
            self.cmb_remote_drive.blockSignals(False)
    
    def _refresh_local(self):
        self.lbl_local_path.setText(self.local_path)
        self.local_tree.clear()
        style = QApplication.style()
        folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.SP_FileIcon)
        back_icon = style.standardIcon(QStyle.SP_DriveHDIcon)
        try:
            items = self._scan_local_dir(self.local_path)
            for item in items:
                tree_item = QTreeWidgetItem([
                    item['name'],
                    format_size(item['size']) if not item['is_dir'] else '',
                    '文件夹' if item['is_dir'] else '文件',
                    item.get('modified', '')
                ])
                tree_item.setData(0, Qt.UserRole, item)
                if item.get('is_parent'):
                    tree_item.setIcon(0, back_icon)
                    tree_item.setForeground(0, QColor('#1565c0'))
                elif item['is_dir']:
                    tree_item.setIcon(0, folder_icon)
                    tree_item.setForeground(0, QColor('#1565c0'))
                else:
                    tree_item.setIcon(0, file_icon)
                self.local_tree.addTopLevelItem(tree_item)
            self.lbl_status.setText(f'本地: {len(items)} 项')
        except Exception as e:
            self.lbl_status.setText(f'本地读取失败: {e}')
    
    def _scan_local_dir(self, path):
        items = []
        try:
            # 添加返回上级目录项
            parent = os.path.dirname(path)
            if parent != path:
                items.append({
                    'name': '.. (返回上级)',
                    'is_dir': True,
                    'size': 0,
                    'modified': '',
                    'is_parent': True,
                })
            
            for entry in sorted(os.listdir(path)):
                full_path = os.path.join(path, entry)
                is_dir = os.path.isdir(full_path)
                item = {
                    'name': entry,
                    'is_dir': is_dir,
                    'size': 0 if is_dir else os.path.getsize(full_path),
                    'modified': '',
                    'full_path': full_path,
                }
                try:
                    stat = os.stat(full_path)
                    import datetime
                    item['modified'] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass
                items.append(item)
        except Exception as e:
            raise Exception(f'无法访问目录: {e}')
        return items
    
    def _local_go_back(self):
        parent = os.path.dirname(self.local_path)
        if parent != self.local_path:
            self.local_path = parent
            self._refresh_local()
    
    def _on_local_double_click(self, item, column):
        data = item.data(0, Qt.UserRole)
        if data:
            if data.get('is_parent'):
                self._local_go_back()
            elif data['is_dir']:
                self.local_path = data['full_path']
                self._refresh_local()
    
    def _local_context_menu(self, pos):
        item = self.local_tree.itemAt(pos)
        if not item:
            return
        
        menu = QMenu(self)
        act_upload = QAction('⬆ 上传到远程', self)
        act_upload.triggered.connect(self._upload_selected)
        menu.addAction(act_upload)
        
        act_open = QAction('📂 在资源管理器中打开', self)
        act_open.triggered.connect(lambda: os.startfile(item.data(0, Qt.UserRole)['full_path']))
        menu.addAction(act_open)
        
        menu.exec_(self.local_tree.viewport().mapToGlobal(pos))
    
    def _get_selected_local(self):
        items = self.local_tree.selectedItems()
        return items[0] if items else None
    
    def _upload_selected(self):
        item = self._get_selected_local()
        if not item:
            QMessageBox.warning(self, '提示', '请先选择要上传的文件')
            return
        data = item.data(0, Qt.UserRole)
        if data and not data.get('is_parent'):
            full_path = data.get('full_path', '')
            if full_path and os.path.isfile(full_path):
                self.file_upload_requested.emit(full_path)
                self.lbl_status.setText(f'正在上传: {data["name"]}')
                self.progress.show()
                self.progress.setRange(0, 0)  # 不确定进度
            elif full_path and os.path.isdir(full_path):
                QMessageBox.information(self, '提示', '暂不支持上传文件夹，请压缩后再上传')
    
    def _delete_local_selected(self):
        item = self._get_selected_local()
        if not item:
            QMessageBox.warning(self, '提示', '请先选择要删除的文件')
            return
        data = item.data(0, Qt.UserRole)
        if data and not data.get('is_parent'):
            ret = QMessageBox.question(
                self, '确认删除',
                f'确定要删除本地文件/文件夹吗？\n\n{data["name"]}\n\n此操作不可恢复！',
                QMessageBox.Yes | QMessageBox.No
            )
            if ret == QMessageBox.Yes:
                try:
                    full_path = data.get('full_path', '')
                    if os.path.isfile(full_path):
                        os.remove(full_path)
                    elif os.path.isdir(full_path):
                        import shutil
                        shutil.rmtree(full_path)
                    self._refresh_local()
                    self.lbl_status.setText(f'已删除: {data["name"]}')
                except Exception as e:
                    QMessageBox.critical(self, '错误', f'删除失败: {e}')
    
    # ============ 远程文件操作 ============
    def _refresh_remote(self):
        self.lbl_remote_path.setText('加载中...')
        self.controller.file_transfer.list_remote_dir(self.remote_path)
    
    def _on_remote_list(self, resp):
        """接收远程目录列表 (由接收线程调用, 需通过信号线程安全地更新UI)"""
        _debug_log(f'[文件传输] 收到远程列表: path={resp.get("path")}, items={len(resp.get("items", []))}')
        self.remote_list_ready.emit(resp)
    
    def _update_remote_list(self, resp):
        """更新远程目录列表 (主线程执行)"""
        try:
            self.remote_path = resp.get('path', self.remote_path)
            self.lbl_remote_path.setText(self.remote_path)
            self.remote_tree.clear()
            
            style = QApplication.style()
            folder_icon = style.standardIcon(QStyle.SP_DirIcon)
            file_icon = style.standardIcon(QStyle.SP_FileIcon)
            back_icon = style.standardIcon(QStyle.SP_DriveHDIcon)
            
            items = resp.get('items', [])
            
            # 如果是根目录，更新驱动器列表
            if self.remote_path == '/' or self.remote_path.endswith(':\\') or len(self.remote_path) <= 3:
                self._update_remote_drives_from_list(items)
            
            # 添加返回上级目录项
            if self.remote_path != '/' and self.remote_path != os.path.dirname(self.remote_path):
                parent = os.path.dirname(self.remote_path)
                if parent != self.remote_path:
                    tree_item = QTreeWidgetItem(['.. (返回上级)', '', '文件夹', ''])
                    tree_item.setData(0, Qt.UserRole, {'is_dir': True, 'is_parent': True, 'path': parent})
                    tree_item.setIcon(0, back_icon)
                    tree_item.setForeground(0, QColor('#1565c0'))
                    self.remote_tree.addTopLevelItem(tree_item)
            
            for item in items:
                display_name = item['name']
                tree_item = QTreeWidgetItem([
                    display_name,
                    format_size(item['size']) if not item['is_dir'] else '',
                    '文件夹' if item['is_dir'] else '文件',
                    item.get('modified', '')
                ])
                item_data = item.copy()
                item_data['path'] = os.path.join(self.remote_path, display_name)
                tree_item.setData(0, Qt.UserRole, item_data)
                if item['is_dir']:
                    tree_item.setIcon(0, folder_icon)
                    tree_item.setForeground(0, QColor('#1565c0'))
                else:
                    tree_item.setIcon(0, file_icon)
                self.remote_tree.addTopLevelItem(tree_item)
            
            self.lbl_status.setText(f'远程: {len(items)} 项')
        except Exception as e:
            _debug_log(f'[文件传输] 更新远程列表异常: {e}')
            import traceback
            traceback.print_exc()
    
    def _remote_go_back(self):
        if self.remote_path == '/':
            return
        parent = os.path.dirname(self.remote_path)
        if parent != self.remote_path:
            self.remote_path = parent
            self._refresh_remote()
    
    def _on_remote_double_click(self, item, column):
        data = item.data(0, Qt.UserRole)
        if data:
            if data.get('is_parent'):
                self.remote_path = data.get('path', '')
                self._refresh_remote()
            elif data.get('is_dir'):
                self.remote_path = data['path']
                self._refresh_remote()
    
    def _remote_context_menu(self, pos):
        item = self.remote_tree.itemAt(pos)
        if not item:
            return
        
        menu = QMenu(self)
        act_download = QAction('⬇ 下载到本地', self)
        act_download.triggered.connect(self._download_selected)
        menu.addAction(act_download)
        
        act_delete = QAction('🗑 删除远程', self)
        act_delete.triggered.connect(self._delete_remote_selected)
        menu.addAction(act_delete)
        
        menu.exec_(self.remote_tree.viewport().mapToGlobal(pos))
    
    def _get_selected_remote(self):
        items = self.remote_tree.selectedItems()
        return items[0] if items else None
    
    def _download_selected(self):
        item = self._get_selected_remote()
        if not item:
            QMessageBox.warning(self, '提示', '请先选择要下载的文件')
            return
        data = item.data(0, Qt.UserRole)
        if data and not data.get('is_parent') and not data.get('is_dir'):
            file_path = data.get('path', '')
            if file_path:
                self.file_download_requested.emit(file_path)
                self.lbl_status.setText(f'正在下载: {data["name"]}')
                self.progress.show()
                self.progress.setRange(0, 0)
    
    def _delete_remote_selected(self):
        item = self._get_selected_remote()
        if not item:
            QMessageBox.warning(self, '提示', '请先选择要删除的文件')
            return
        data = item.data(0, Qt.UserRole)
        if data and not data.get('is_parent'):
            ret = QMessageBox.question(
                self, '确认删除',
                f'确定要删除远程文件吗？\n\n{data["name"]}\n\n此操作不可恢复！',
                QMessageBox.Yes | QMessageBox.No
            )
            if ret == QMessageBox.Yes:
                file_path = data.get('path', '')
                if file_path:
                    self.controller.file_transfer.remove_remote_file(file_path)
                    self.lbl_status.setText(f'正在删除: {data["name"]}')
    
    def _on_remove_result(self, success):
        """接收删除结果 (由接收线程调用, 需通过信号线程安全地更新UI)"""
        self.remove_result_ready.emit({'success': success})
    
    def _update_remove_result(self, data):
        """更新删除结果 (主线程执行)"""
        try:
            success = data.get('success', False)
            if success:
                self._refresh_remote()
                self.lbl_status.setText('删除成功')
            else:
                self.lbl_status.setText('删除失败')
        except Exception as e:
            _debug_log(f'[文件传输] 更新删除结果异常: {e}')


class ViewerWindow(QMainWindow):
    """ToDesk 风格远程桌面窗口"""

    def __init__(self, controller, screen_idx=0, total_screens=1, all_controllers=None):
        super().__init__()
        self.controller = controller
        self.screen_idx = screen_idx
        self.total_screens = total_screens
        self.all_controllers = all_controllers or [controller]
        
        screen_info = f'屏幕 {screen_idx + 1}' if total_screens > 1 else '远程桌面'
        self.setWindowTitle(f'MyDesk {screen_info}')
        self.resize(1280, 760)
        self.setStyleSheet(_TODESK_STYLE)
        self._is_fullscreen = False
        self._recv_dir = DEFAULT_RECV_DIR
        os.makedirs(self._recv_dir, exist_ok=True)
        
        # 悬浮工具栏
        self._float_bar = None
        self._float_timer = QTimer(self)
        self._float_timer.setSingleShot(True)
        self._float_timer.setInterval(3000)
        self._float_timer.timeout.connect(self._hide_float_bar)

        # ---- 画面区 ----
        self.screen = ScreenWidget(controller, screen_idx)
        self.screen.setMinimumSize(320, 240)
        self.setCentralWidget(self.screen)

        # ---- 工具栏 ----
        self.toolbar = self._build_toolbar()
        self.addToolBar(self.toolbar)

        # ---- 状态栏 ----
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel('就绪')
        self.status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.hide()
        self.status.addPermanentWidget(self.progress)

        # 日志桥接
        self.log_bridge = LogBridge()
        self.log_bridge.log_ready.connect(self._on_log)
        self.controller.file_transfer.on_log = self.log_bridge.log_ready.emit

        # 接收器
        self.receiver = ScreenReceiver(controller)
        self.receiver.frame_ready.connect(self._on_frame)
        self.receiver.info_ready.connect(self._on_info)
        self.receiver.disconnected.connect(self._on_disconnect)
        self.receiver.start()
        
        _debug_log(f'[ViewerWindow] 初始化完成: screen_idx={screen_idx}, controller.connected={controller.connected}, controller.running={controller.running}')

        # 文件传输对话框
        self._file_dialog = None

        # ---- 剪贴板同步 ----
        self._clip_enabled = True
        self._last_clip_text = None
        self.receiver.clipboard_ready.connect(self._on_remote_clipboard)
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(500)
        self._clip_timer.timeout.connect(self._poll_clipboard)
        self._clip_timer.start()

        QTimer.singleShot(100, self._focus_screen)

    def _build_toolbar(self):
        tb = QToolBar('工具')
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))

        # 连接信息
        self.lbl_conn = QLabel(' 已连接 ')
        self.lbl_conn.setStyleSheet('color:#1a73e8; font-weight:bold; padding:0 8px;')
        tb.addWidget(self.lbl_conn)

        tb.addSeparator()

        # 暂停/恢复
        self.btn_pause = QPushButton('⏸ 暂停')
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._toggle_pause)
        tb.addWidget(self.btn_pause)

        tb.addSeparator()

        # 画质
        tb.addWidget(QLabel(' 画质'))
        self.sl_quality = QSlider(Qt.Horizontal)
        self.sl_quality.setRange(10, 100)
        self.sl_quality.setValue(65)
        self.sl_quality.setFixedWidth(100)
        self.sl_quality.valueChanged.connect(self._set_quality)
        tb.addWidget(self.sl_quality)
        self.lbl_quality = QLabel('65')
        self.lbl_quality.setFixedWidth(24)
        self.sl_quality.valueChanged.connect(lambda v: self.lbl_quality.setText(str(v)))
        tb.addWidget(self.lbl_quality)

        tb.addSeparator()

        # 帧率
        tb.addWidget(QLabel(' 帧率'))
        self.sl_fps = QSlider(Qt.Horizontal)
        self.sl_fps.setRange(5, 60)
        self.sl_fps.setValue(30)
        self.sl_fps.setFixedWidth(80)
        self.sl_fps.valueChanged.connect(self._set_fps)
        tb.addWidget(self.sl_fps)
        self.lbl_fps = QLabel('30')
        self.lbl_fps.setFixedWidth(24)
        self.sl_fps.valueChanged.connect(lambda v: self.lbl_fps.setText(str(v)))
        tb.addWidget(self.lbl_fps)

        tb.addSeparator()

        # 文件传输
        self.btn_file_transfer = QPushButton('📁 文件传输')
        self.btn_file_transfer.clicked.connect(self._open_file_transfer)
        tb.addWidget(self.btn_file_transfer)

        # 剪贴板同步
        self.btn_clipboard = QPushButton('📋 剪贴板')
        self.btn_clipboard.setCheckable(True)
        self.btn_clipboard.setChecked(True)
        self.btn_clipboard.toggled.connect(self._toggle_clipboard)
        tb.addWidget(self.btn_clipboard)

        # 接收目录设置
        self.btn_recv_dir = QPushButton('📁 接收目录')
        self.btn_recv_dir.clicked.connect(self._set_recv_dir)
        tb.addWidget(self.btn_recv_dir)

        tb.addSeparator()

        # 多屏幕切换
        if self.total_screens > 1:
            self.lbl_screen = QLabel(f' 屏幕 {self.screen_idx + 1}/{self.total_screens}')
            self.lbl_screen.setStyleSheet('font-weight:bold; color:#1a73e8;')
            tb.addWidget(self.lbl_screen)

        tb.addSeparator()

        # 全屏
        self.btn_fullscreen = QPushButton('⛶ 全屏')
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        tb.addWidget(self.btn_fullscreen)

        return tb

    def _create_float_bar(self):
        """创建全屏悬浮工具栏"""
        self._float_bar = FloatToolBar()
        self._float_bar.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self._float_bar.quality_changed.connect(self._set_quality)
        self._float_bar.fps_changed.connect(self._set_fps)
        self._float_bar.fullscreen_exit.connect(self._toggle_fullscreen)
        self._float_bar.file_transfer_requested.connect(self._open_file_transfer)
        self._float_bar.clipboard_toggled.connect(self._toggle_clipboard)
        self._float_bar.show()
        self._position_float_bar()

    def _position_float_bar(self):
        """定位悬浮工具栏到屏幕顶部中央"""
        if self._float_bar:
            screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = geo.center().x() - self._float_bar.width() // 2
                y = geo.top() + 10
                self._float_bar.move(x, y)

    def _show_float_bar(self):
        """显示悬浮工具栏并启动自动隐藏计时"""
        if self._float_bar:
            self._float_bar.show()
            self._float_bar.raise_()
            self._float_timer.start()

    def _hide_float_bar(self):
        """隐藏悬浮工具栏"""
        if self._float_bar:
            self._float_bar.hide()

    # ---- 槽函数 ----
    def _on_log(self, msg):
        self.status_label.setText(msg)
        if '接收完成' in msg or '发送完成' in msg:
            self.progress.hide()
        elif '接收' in msg or '发送' in msg:
            self.progress.show()

    def _on_frame(self, jpeg_bytes, screen_idx, total_screens):
        # 添加调试日志
        if screen_idx == self.screen_idx:
            pm = QPixmap()
            load_ok = pm.loadFromData(jpeg_bytes, 'JPEG')
            if load_ok and pm.width() > 0:
                self.screen.set_pixmap(pm)
                _debug_log(f'[画面] 帧已显示: idx={screen_idx}, size={pm.width()}x{pm.height()}')
            else:
                # 解码失败，尝试用PIL解码后再转QPixmap
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(jpeg_bytes))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    data = img.tobytes('raw', 'RGB')
                    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
                    pm = QPixmap.fromImage(qimg.copy())
                    if pm.width() > 0:
                        self.screen.set_pixmap(pm)
                        _debug_log(f'[画面] 帧已显示(PIL回退): idx={screen_idx}, size={pm.width()}x{pm.height()}')
                    else:
                        _debug_log(f'[错误] PIL解码后QPixmap为空')
                except Exception as e:
                    _debug_log(f'[错误] 画面解码失败: {e}, jpeg_size={len(jpeg_bytes)}')
        # 如果屏幕索引不匹配但当前屏幕还没有画面，也显示（作为fallback）
        elif self.screen.pm is None and screen_idx == 0:
            pm = QPixmap()
            if pm.loadFromData(jpeg_bytes, 'JPEG') and pm.width() > 0:
                self.screen.set_pixmap(pm)
                _debug_log(f'[画面] fallback显示帧: idx={screen_idx}')

    def _on_info(self, w, h, screen_idx, total_screens):
        """处理屏幕尺寸信息, 只处理对应屏幕的尺寸"""
        if screen_idx == self.screen_idx:
            self.screen.set_remote_size(w, h)
            screen_info = f'屏幕 {self.screen_idx + 1}/{total_screens}' if total_screens > 1 else '远程桌面'
            self.setWindowTitle(f'MyDesk {screen_info} ({w}x{h})')
            if w > 0 and h > 0:
                self.resize(min(w + 20, 1920), min(h + 80, 1080))
            self._focus_screen()

    def _focus_screen(self):
        self.screen.setFocus()

    def _on_disconnect(self):
        self.setWindowTitle('MyDesk - 连接已断开')
        self.status_label.setText('连接已断开')
        self.lbl_conn.setText(' 已断开 ')
        self.lbl_conn.setStyleSheet('color:#ef4444; font-weight:bold; padding:0 8px;')

    def _toggle_pause(self, checked):
        self.controller.send(MSG_CONTROL, b'PAUSE' if checked else b'RESUME')
        self.btn_pause.setText('▶ 恢复' if checked else '⏸ 暂停')

    def _set_quality(self, val):
        self.controller.send(MSG_CONTROL, f'QUALITY:{val}'.encode('utf-8'))
        # 同步滑动条
        if self.sender() != self.sl_quality:
            self.sl_quality.blockSignals(True)
            self.sl_quality.setValue(val)
            self.sl_quality.blockSignals(False)
        if self._float_bar and self.sender() != self._float_bar.sl_quality:
            self._float_bar.sl_quality.blockSignals(True)
            self._float_bar.sl_quality.setValue(val)
            self._float_bar.sl_quality.blockSignals(False)

    def _set_fps(self, val):
        self.controller.send(MSG_CONTROL, f'FPS:{val}'.encode('utf-8'))
        if self.sender() != self.sl_fps:
            self.sl_fps.blockSignals(True)
            self.sl_fps.setValue(val)
            self.sl_fps.blockSignals(False)
        if self._float_bar and self.sender() != self._float_bar.sl_fps:
            self._float_bar.sl_fps.blockSignals(True)
            self._float_bar.sl_fps.setValue(val)
            self._float_bar.sl_fps.blockSignals(False)

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.showNormal()
            self.toolbar.show()
            self.statusBar().show()
            self.btn_fullscreen.setText('⛶ 全屏')
            self._is_fullscreen = False
            if self._float_bar:
                self._float_bar.close()
                self._float_bar = None
        else:
            self.showFullScreen()
            self.toolbar.hide()
            self.statusBar().hide()
            self.btn_fullscreen.setText('退出全屏')
            self._is_fullscreen = True
            self._create_float_bar()
        self._focus_screen()

    def _set_recv_dir(self):
        """选择文件接收目录"""
        d = QFileDialog.getExistingDirectory(
            self, '选择文件接收目录', self._recv_dir
        )
        if d:
            self._recv_dir = d
            self.controller.file_transfer.recv_dir = d
            self.status_label.setText(f'接收目录: {d}')

    def _open_file_transfer(self):
        """打开文件传输对话框"""
        try:
            if self._file_dialog is None or not self._file_dialog.isVisible():
                self._file_dialog = FileTransferDialog(
                    self.controller, self._recv_dir, self
                )
                self._file_dialog.file_upload_requested.connect(self._do_upload)
                self._file_dialog.file_download_requested.connect(self._do_download)
                self._file_dialog.show()
            else:
                self._file_dialog.raise_()
                self._file_dialog.activateWindow()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, '错误', f'打开文件传输失败:\n{e}')

    def _do_upload(self, filepath):
        """执行文件上传"""
        self.controller.upload_file(filepath)
        self.status_label.setText(f'正在上传: {os.path.basename(filepath)}')
        self.progress.show()
        self.progress.setRange(0, 0)

    def _do_download(self, remote_path):
        """执行文件下载"""
        self.controller.download_file(remote_path)
        self.status_label.setText(f'正在下载: {os.path.basename(remote_path)}')
        self.progress.show()
        self.progress.setRange(0, 0)

    def keyPressEvent(self, event):
        # ESC 退出全屏
        if event.key() == Qt.Key_Escape and self._is_fullscreen:
            self._toggle_fullscreen()
            return
        # F11 切换全屏
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
            return
        # Ctrl+Shift+F 打开文件传输
        if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier) and event.key() == Qt.Key_F:
            self._open_file_transfer()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        """全屏时鼠标移动显示悬浮工具栏"""
        if self._is_fullscreen:
            self._show_float_bar()
        super().mouseMoveEvent(event)

    def _toggle_clipboard(self, checked):
        """开关剪贴板同步"""
        self._clip_enabled = checked
        self.controller.send(MSG_CONTROL, b'CLIPBOARD:on' if checked else b'CLIPBOARD:off')
        if checked:
            self._last_clip_text = None  # 开启后立即同步当前剪贴板

    def _poll_clipboard(self):
        """轮询本地剪贴板, 变化时推送到被控端"""
        if not self._clip_enabled:
            return
        try:
            text = QApplication.clipboard().text()
        except Exception:
            return
        if text is None:
            text = ''
        if text != self._last_clip_text:
            self._last_clip_text = text
            try:
                self.controller.send(MSG_CLIPBOARD, text.encode('utf-8'))
            except Exception:
                pass

    def _on_remote_clipboard(self, text):
        """接收被控端剪贴板, 写入本地"""
        self._last_clip_text = text
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭窗口时清理处理器, 只在最后一个窗口关闭时断开连接"""
        self._clip_timer.stop()
        self.controller.remove_handler(self.receiver._on_msg)
        # 检查是否还有其他窗口使用同一个 controller
        remaining = len(self.controller._msg_handlers)
        if remaining <= 1:  # 只有自己的处理器了，可以安全断开
            self.controller.disconnect()
        if self._float_bar:
            self._float_bar.close()
        super().closeEvent(event)


class ConnectionDialog(QDialog):
    """连接对话框"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('MyDesk 连接')
        self.setFixedSize(360, 200)
        self.result = None

        form = QFormLayout()
        self.in_host = QLineEdit('127.0.0.1')
        self.in_port = QLineEdit('7788')
        self.in_pwd = QLineEdit('123456')
        self.in_pwd.setEchoMode(QLineEdit.Password)
        form.addRow('主机 IP:', self.in_host)
        form.addRow('端口:', self.in_port)
        form.addRow('密码:', self.in_pwd)

        self.btn = QPushButton('连接')
        self.btn.clicked.connect(self._do_connect)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.btn)

    def _do_connect(self):
        try:
            self.result = (self.in_host.text(), int(self.in_port.text()), self.in_pwd.text())
            self.accept()
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    if len(sys.argv) >= 4:
        host, port, pwd = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    else:
        dlg = ConnectionDialog()
        if dlg.exec_() != QDialog.Accepted or not dlg.result:
            sys.exit(0)
        host, port, pwd = dlg.result
    controller = Controller()
    try:
        controller.connect(host, port, pwd)
    except Exception as e:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(None, '连接失败', f'无法连接 {host}:{port}\n{e}')
        sys.exit(1)
    win = ViewerWindow(controller)
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
