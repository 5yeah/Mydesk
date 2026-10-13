# -*- coding: utf-8 -*-
"""MyDesk 主控端查看窗口 (ToDesk 风格)

功能:
1. 屏幕帧接收与渲染
2. 鼠标键盘捕获
3. 双栏文件传输界面
4. 全屏悬浮工具栏
5. 多屏幕支持

重构自原 viewer.py: 清理调试日志 (改用 gui.logger), 套用统一主题 (gui.theme)。
协议解析与交互逻辑保持不变。
"""
import os
import re
import sys
import struct
import threading

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFormLayout, QLineEdit, QFileDialog,
    QToolBar, QAction, QStatusBar, QProgressBar, QGroupBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QSplitter, QMessageBox, QInputDialog,
    QHeaderView, QFileSystemModel, QMenu, QSizePolicy, QStyle,
    QComboBox,
)
from PyQt5.QtCore import Qt, QObject, QRect, pyqtSignal, QTimer, QSize, QDir, QModelIndex
from PyQt5.QtGui import QPixmap, QPainter, QIcon, QFont, QColor, QCursor, QImage

from controller import Controller
from protocol import (
    MSG_SCREEN, MSG_CONTROL, MSG_INPUT, MSG_FILE, MSG_CLIPBOARD, MSG_CURSOR,
    EV_MOUSE_MOVE, EV_MOUSE_DOWN, EV_MOUSE_UP, EV_MOUSE_SCROLL,
    EV_KEY_DOWN, EV_KEY_UP,
    CTL_QUALITY, CTL_FPS, CTL_CLIPBOARD, CTL_PAUSE, CTL_RESUME,
    CTL_SCREEN_INFO_REQ, CTL_SCREEN_SELECT,
)
from file_transfer import (
    FT_OFFER, FT_ACCEPT, FT_DATA, FT_DONE, FT_PULL, FT_ERROR,
    PART_SUFFIX,
)

from .logger import debug_log
from .theme import VIEWER_STYLE, FLOAT_BAR_STYLE, THEME

# 默认接收目录
DEFAULT_RECV_DIR = os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')

# Qt 鼠标按钮 -> MyDesk 按钮编号
_QT_BTN = {Qt.LeftButton: 1, Qt.RightButton: 2, Qt.MidButton: 3}

# 需要在本地处理、不转发给被控端的快捷键
_SHORTCUT_F11 = 'f11'                 # 切换全屏
_SHORTCUT_ESC = 'esc'                 # 退出全屏
_SHORTCUT_FILE = 'file_transfer'      # Ctrl+Shift+F
_SHORTCUT_PAUSE = 'pause'             # Ctrl+Alt+P

# 从进度日志里抽百分比, 例如 "[文件] 接收中 x.bin: 30% (300/1000)"
_PCT_RE = re.compile(r':\s*(\d{1,3})%')
# 判断一条日志是否意味着"本次传输结束"(成功或失败)
_DONE_WORDS = ('接收完成', '发送完成', '无需传输', '已存在', '不完整', '失败', '错误')


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
    """等比缩放显示远程画面, 并捕获键鼠事件

    坐标说明:
      被控端下发 `SCREEN:{idx}:{w}x{h}:{left}:{top}`, left/top 是该显示器在
      **虚拟桌面**中的原点。本控件把鼠标位置映射成该屏内的局部坐标后,
      再加上 (left, top) 变成虚拟桌面坐标发给被控端 —— 被控端按整个虚拟桌面
      归一化注入, 这样副屏上的点击才会落在副屏上。

    只读模式:
      mode='view' 时完全不发送 MSG_INPUT, 保证"仅观看"是真实语义。
    """

    local_shortcut = pyqtSignal(str)   # 需要本地处理的快捷键

    def __init__(self, controller, screen_idx=0, parent=None, readonly=False):
        super().__init__(parent)
        self.controller = controller
        self.screen_idx = screen_idx
        self.readonly = readonly
        self.pm = None
        self.remote_w = 0
        self.remote_h = 0
        self.origin_x = 0     # 该显示器在虚拟桌面中的原点
        self.origin_y = 0
        self._img_rect = QRect()
        self._scaled_cache = None      # (源QPixmap, size) -> 缩放结果, 避免每次重绘都缩放
        self._cursor_pos = None        # 远端光标在本屏内的位置(局部坐标)
        self._cursor_visible = False
        self._pressed_vks = set()      # 已按下但未抬起的键, 失焦时补发抬起
        self.setStyleSheet('background:#1a1a2e;')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        if readonly:
            self.setCursor(Qt.ArrowCursor)

    # ---- 供 ViewerWindow 更新 ----
    def set_pixmap(self, pm):
        self.pm = pm
        self._scaled_cache = None
        self.update()

    def set_remote_size(self, w, h):
        if w > 0 and h > 0:
            self.remote_w = w
            self.remote_h = h
            self._scaled_cache = None
            self.update()

    def set_origin(self, x, y):
        self.origin_x = int(x)
        self.origin_y = int(y)

    def set_cursor_pos(self, x, y, visible):
        """x/y 为虚拟桌面坐标; 只画属于本屏的光标"""
        if not visible:
            self._cursor_visible = False
        else:
            lx = int(x) - self.origin_x
            ly = int(y) - self.origin_y
            if 0 <= lx < max(1, self.remote_w) and 0 <= ly < max(1, self.remote_h):
                self._cursor_pos = (lx, ly)
                self._cursor_visible = True
            else:
                self._cursor_visible = False
        self.update()

    def clear_cursor(self):
        self._cursor_visible = False
        self._cursor_pos = None
        self.update()

    # ---- 绘制 ----
    def _calc_img_rect(self):
        """按当前控件尺寸与远端分辨率算出"画面在控件内的矩形"(含黑边)"""
        if self.pm is None or self.width() <= 0 or self.height() <= 0:
            return QRect()
        key = (self.pm.cacheKey(), self.width(), self.height())
        cached = self._scaled_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        # 用快速缩放而非平滑缩放: 远程镜像场景无需高质量插值,
        # 且平滑缩放在超大帧(如锁屏高分辨率)下更易触发原生绘图崩溃
        scaled = self.pm.scaled(self.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        rect = QRect(x, y, scaled.width(), scaled.height())
        self._scaled_cache = (key, rect, scaled)
        return rect

    def paintEvent(self, _event):
        if self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        if not self.pm:
            painter.fillRect(self.rect(), Qt.black)
            painter.setPen(Qt.white)
            painter.drawText(self.rect(), Qt.AlignCenter, '等待画面...')
            return

        if self.remote_w == 0 or self.remote_h == 0:
            self.remote_w = self.pm.width()
            self.remote_h = self.pm.height()

        rect = self._calc_img_rect()
        self._img_rect = rect
        painter.fillRect(self.rect(), Qt.black)
        scaled = self._scaled_cache[2] if self._scaled_cache else self.pm
        painter.drawPixmap(rect.x(), rect.y(), scaled)

        # 远端光标 (mss 在 Windows 上不抓光标, 由被控端单独推送位置)
        if self._cursor_visible and self._cursor_pos is not None:
            self._draw_remote_cursor(painter, rect)

    def _draw_remote_cursor(self, painter, rect):
        """在画面上绘制远端光标 (带白色描边的箭头, 任何背景都可见)"""
        try:
            if self.remote_w <= 0 or rect.width() <= 0 or self._cursor_pos is None:
                return
            sx = rect.width() / float(self.remote_w)
            sy = rect.height() / float(self.remote_h)
            cx = rect.x() + int(self._cursor_pos[0] * sx)
            cy = rect.y() + int(self._cursor_pos[1] * sy)
            from PyQt5.QtCore import QPoint
            from PyQt5.QtGui import QPolygon
            pts = [(0, 0), (0, 16), (4, 12), (7, 18), (10, 16), (7, 10), (12, 10)]
            poly = QPolygon([QPoint(cx + dx, cy + dy) for dx, dy in pts])
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QColor(255, 255, 255, 230))
            painter.setBrush(QColor(20, 20, 20, 235))
            painter.drawPolygon(poly)
            painter.restore()
        except Exception:
            pass

    # ---- 坐标映射 ----
    def _map_pos(self, pos, allow_outside=False):
        """控件坐标 -> 虚拟桌面坐标; 点在黑边上时返回 None(避免误点远端边缘)"""
        rect = self._img_rect
        if rect.width() <= 0 or rect.height() <= 0:
            rect = self._calc_img_rect()
            self._img_rect = rect
        if self.remote_w <= 0 or self.remote_h <= 0 or rect.width() <= 0:
            return None
        if not allow_outside and not rect.contains(pos):
            return None
        rx = int((pos.x() - rect.x()) / rect.width() * self.remote_w)
        ry = int((pos.y() - rect.y()) / rect.height() * self.remote_h)
        rx = max(0, min(rx, self.remote_w - 1))
        ry = max(0, min(ry, self.remote_h - 1))
        return self.origin_x + rx, self.origin_y + ry

    def _send_input(self, payload):
        if self.readonly or not self.controller.connected:
            return
        self.controller.send_input(payload)

    # ---- 鼠标 ----
    def mouseMoveEvent(self, event):
        p = self._map_pos(event.pos(), allow_outside=True)
        if p:
            self._send_input(struct.pack('<BHH', EV_MOUSE_MOVE, p[0] & 0xFFFF, p[1] & 0xFFFF))
        # 未消费的事件不 accept 会阻止冒泡给父窗口, 这里显式调用父类
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        if not self.readonly:
            p = self._map_pos(event.pos())
            if p is None:
                super().mousePressEvent(event)
                return
            self._send_input(struct.pack('<BHH', EV_MOUSE_MOVE, p[0] & 0xFFFF, p[1] & 0xFFFF))
            btn = _QT_BTN.get(event.button())
            if btn:
                self._send_input(struct.pack('<BB', EV_MOUSE_DOWN, btn))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.readonly:
            p = self._map_pos(event.pos(), allow_outside=True)
            if p is not None:
                self._send_input(struct.pack('<BHH', EV_MOUSE_MOVE, p[0] & 0xFFFF, p[1] & 0xFFFF))
            btn = _QT_BTN.get(event.button())
            if btn:
                self._send_input(struct.pack('<BB', EV_MOUSE_UP, btn))
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if not self.readonly and self._map_pos(event.pos()) is not None:
            delta = event.angleDelta()
            self._send_input(struct.pack('<Bhh', EV_MOUSE_SCROLL,
                                         max(-32768, min(32767, delta.x())),
                                         max(-32768, min(32767, delta.y()))))
        super().wheelEvent(event)

    # ---- 键盘 ----
    def keyPressEvent(self, event):
        # 本地快捷键优先: 处理完必须 return, 否则 F11/Esc 会被注入到对方电脑
        if self._handle_local_shortcut(event):
            event.accept()
            return
        if event.isAutoRepeat():
            event.accept()
            return
        vk = event.nativeVirtualKey()
        if vk:
            self._pressed_vks.add(int(vk))
            self._send_input(struct.pack('<BH', EV_KEY_DOWN, vk & 0xFFFF))
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            event.accept()
            return
        vk = event.nativeVirtualKey()
        if vk:
            self._pressed_vks.discard(int(vk))
            self._send_input(struct.pack('<BH', EV_KEY_UP, vk & 0xFFFF))
        event.accept()

    def _handle_local_shortcut(self, event):
        """返回 True 表示该按键已被本地消费, 不再转发给被控端"""
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_F11:
            self.local_shortcut.emit(_SHORTCUT_F11)
            return True
        if key == Qt.Key_Escape:
            self.local_shortcut.emit(_SHORTCUT_ESC)
            return True
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and key == Qt.Key_F:
            self.local_shortcut.emit(_SHORTCUT_FILE)
            return True
        if mods == (Qt.ControlModifier | Qt.AltModifier) and key == Qt.Key_P:
            self.local_shortcut.emit(_SHORTCUT_PAUSE)
            return True
        return False

    def focusOutEvent(self, event):
        """失焦时补发所有按键抬起。

        不这么做的话: 用户按住 Ctrl 时切走窗口, Qt 不会补发 release,
        被控端的 Ctrl 会永远保持按下 —— 之后所有输入都变成 Ctrl 组合键。
        """
        self._release_all_keys()
        super().focusOutEvent(event)

    def _release_all_keys(self):
        vks = list(self._pressed_vks)
        self._pressed_vks.clear()
        for vk in vks:
            self._send_input(struct.pack('<BH', EV_KEY_UP, vk & 0xFFFF))

    def set_readonly(self, readonly):
        was = self.readonly
        self.readonly = bool(readonly)
        if self.readonly and not was:
            # 切到只读前先把可能按下的键放开
            self._release_all_keys()


class ScreenReceiver(QObject):
    """把 controller 的消息转成 Qt 信号。

    丢帧策略: 屏幕帧只保留"最新一帧", 并且在上一次派发还没被 GUI 线程取走时
    不再重复 emit —— 这样 GUI 事件队列里最多只有一帧待处理, 不会因为解码跟不上
    而无限堆积(旧实现每帧都 emit, 表现为延迟越用越大、内存持续上涨)。
    """

    frame_ready = pyqtSignal(bytes, int, int)  # jpeg, screen_idx, total_screens
    info_ready = pyqtSignal(int, int, int, int)  # w, h, screen_idx, total_screens
    disconnected = pyqtSignal()
    screen_list_ready = pyqtSignal(list)  # [(idx, w, h, left, top), ...]
    clipboard_ready = pyqtSignal(str)     # 远程剪贴板文本
    cursor_ready = pyqtSignal(int, int, bool)  # 虚拟桌面 x, y, 可见

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._total_screens = 1
        self._started = False
        # 显示器信息缓存: idx -> (w, h, left, top), 供切换屏幕时立即使用
        self.screen_sizes = {}
        # 帧合并
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._frame_pending = False

    def start(self):
        """注册消息回调并索要显示器信息。

        必须在 ViewerWindow 连好各信号之后再调用 —— 否则首批帧/显示器信息
        会 emit 给一个还没有接收者的信号而被丢弃。
        """
        if self._started:
            return
        self._started = True
        self.controller.set_handler(self._on_msg)
        self.controller.add_disconnect_callback(self._on_conn_lost)
        # 主动索要显示器信息(被控端在订阅时也发过一次, 那次可能早于窗口创建)
        self.controller.send(MSG_CONTROL, CTL_SCREEN_INFO_REQ.encode('utf-8'))

    def stop(self):
        self.controller.remove_handler(self._on_msg)
        self.controller.remove_disconnect_callback(self._on_conn_lost)
        self._started = False

    # ---- 断开 ----
    def _on_conn_lost(self):
        """在接收线程被调用 -> 用信号切回 GUI 线程"""
        try:
            self.disconnected.emit()
        except Exception:
            pass

    # ---- 收帧 ----
    def _on_msg(self, mtype, payload):
        if mtype == MSG_SCREEN:
            if len(payload) > 4:
                screen_idx = struct.unpack('<I', payload[:4])[0]
                jpeg_data = payload[4:]
            else:
                screen_idx = 0
                jpeg_data = payload
            self._offer_frame(jpeg_data, screen_idx)
        elif mtype == MSG_CURSOR:
            if len(payload) >= 9:
                try:
                    x, y, vis = struct.unpack('<IIB', payload[:9])
                    self.cursor_ready.emit(int(x), int(y), bool(vis))
                except struct.error:
                    pass
        elif mtype == MSG_CLIPBOARD:
            self.clipboard_ready.emit(payload.decode('utf-8', errors='replace'))
        elif mtype == MSG_CONTROL:
            try:
                cmd = payload.decode('utf-8')
            except Exception:
                return
            try:
                if cmd.startswith('SCREEN:'):
                    self._parse_screen(cmd)
                elif cmd.startswith('SCREEN_LIST:'):
                    self._parse_screen_list(cmd)
                elif cmd.startswith('MULTI_SCREEN:'):
                    self._total_screens = int(cmd.split(':', 1)[1])
            except Exception as e:
                debug_log(f'[错误] 解析控制消息异常: {e}', 'viewer')

    def _parse_screen(self, cmd):
        """解析 SCREEN:{idx}:{w}x{h}[:{left}:{top}] (后两项为多屏偏移, 可缺省)"""
        rest = cmd[len('SCREEN:'):]
        parts = rest.split(':')
        if len(parts) < 2:
            return
        try:
            screen_idx = int(parts[0])
        except ValueError:
            return
        try:
            w_s, h_s = parts[1].split('x')
            w, h = int(w_s), int(h_s)
        except ValueError:
            return
        left = int(parts[2]) if len(parts) >= 4 and parts[2].lstrip('-').isdigit() else 0
        top = int(parts[3]) if len(parts) >= 4 and parts[3].lstrip('-').isdigit() else 0
        if w > 0 and h > 0:
            self.screen_sizes[screen_idx] = (w, h, left, top)
            self.info_ready.emit(w, h, screen_idx, self._total_screens)

    def _parse_screen_list(self, cmd):
        screens = []
        for s in cmd.split(':', 1)[1].split(','):
            parts = s.split(':')
            if len(parts) >= 3:
                try:
                    idx = int(parts[0])
                    w = int(parts[1])
                    h = int(parts[2])
                except ValueError:
                    continue
                left = int(parts[3]) if len(parts) >= 5 and parts[3].lstrip('-').isdigit() else 0
                top = int(parts[4]) if len(parts) >= 5 and parts[4].lstrip('-').isdigit() else 0
                screens.append((idx, w, h, left, top))
                self.screen_sizes[idx] = (w, h, left, top)
        if screens:
            self._total_screens = len(screens)
            self.screen_list_ready.emit(screens)

    def _offer_frame(self, jpeg_data, screen_idx):
        """合并帧: 只留最新一帧, 且同一时刻最多一个待派发事件"""
        if not jpeg_data or len(jpeg_data) < 2:
            return
        with self._frame_lock:
            self._latest_frame = (jpeg_data, screen_idx, self._total_screens)
            if self._frame_pending:
                return
            self._frame_pending = True
        self.frame_ready.emit(jpeg_data, screen_idx, self._total_screens)

    def take_latest_frame(self):
        """GUI 线程取走最新一帧并清掉 pending 标记"""
        with self._frame_lock:
            self._frame_pending = False
            data = self._latest_frame
            self._latest_frame = None
        return data


class FloatToolBar(QWidget):
    """全屏悬浮工具栏 - ToDesk风格"""

    quality_changed = pyqtSignal(int)
    fps_changed = pyqtSignal(int)
    fullscreen_exit = pyqtSignal()
    file_transfer_requested = pyqtSignal()
    clipboard_toggled = pyqtSignal(bool)
    pause_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('FloatBar')
        self.setStyleSheet(FLOAT_BAR_STYLE)
        self.setFixedSize(720, 50)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        self.lbl_status = QLabel('● 已连接')
        self.lbl_status.setStyleSheet('color:#22c55e; font-weight:bold;')
        layout.addWidget(self.lbl_status)

        layout.addSpacing(10)

        self.btn_pause = QPushButton('⏸')
        self.btn_pause.setCheckable(True)
        self.btn_pause.setFixedWidth(40)
        self.btn_pause.setToolTip('暂停/恢复画面推送')
        self.btn_pause.toggled.connect(self.pause_toggled.emit)
        layout.addWidget(self.btn_pause)

        layout.addSpacing(10)

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

        btn_ft = QPushButton('📁 文件传输')
        btn_ft.clicked.connect(self.file_transfer_requested.emit)
        layout.addWidget(btn_ft)

        self.btn_clipboard = QPushButton('📋 剪贴板')
        self.btn_clipboard.setCheckable(True)
        self.btn_clipboard.setChecked(True)
        self.btn_clipboard.toggled.connect(self.clipboard_toggled.emit)
        layout.addWidget(self.btn_clipboard)

        layout.addSpacing(10)

        btn_exit = QPushButton('✕ 退出全屏')
        btn_exit.clicked.connect(self.fullscreen_exit.emit)
        btn_exit.setStyleSheet('color:#ef4444; font-weight:bold;')
        layout.addWidget(btn_exit)

        self._drag_pos = None

    def set_state(self, quality, fps, clipboard_on, paused):
        """用主工具栏的当前值回填, 避免"打开悬浮栏就悄悄改设置\""""
        for w in (self.sl_quality, self.sl_fps, self.btn_clipboard, self.btn_pause):
            w.blockSignals(True)
        try:
            self.sl_quality.setValue(int(quality))
            self.lbl_quality.setText(str(int(quality)))
            self.sl_fps.setValue(int(fps))
            self.lbl_fps.setText(str(int(fps)))
            self.btn_clipboard.setChecked(bool(clipboard_on))
            self.btn_pause.setChecked(bool(paused))
        finally:
            for w in (self.sl_quality, self.sl_fps, self.btn_clipboard, self.btn_pause):
                w.blockSignals(False)

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
    支持: 上传、下载、删除
    """

    file_upload_requested = pyqtSignal(str)  # 本地文件路径 -> 上传
    file_download_requested = pyqtSignal(str)  # 远程文件路径 -> 下载
    file_delete_requested = pyqtSignal(str, bool)  # 路径, is_remote
    remote_list_ready = pyqtSignal(dict)  # 远程目录列表信号 (线程安全)
    remove_result_ready = pyqtSignal(dict)  # 删除结果信号 (线程安全)
    transfer_log_ready = pyqtSignal(str)  # 传输日志信号 (线程安全)

    def __init__(self, controller, recv_dir='.', parent=None):
        super().__init__(parent)
        self.controller = controller
        self.recv_dir = recv_dir
        self.setWindowTitle('MyDesk 文件传输')
        self.resize(900, 600)
        self.setStyleSheet(VIEWER_STYLE)
        # 关闭即销毁, 否则反复开关会一直累积对话框对象(旧实现只 close 不释放)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        # 即使初始化抛异常也要能安全解绑
        self._listeners_registered = False

        self.local_path = os.path.expanduser('~')
        self.remote_path = '/'

        self.remote_list_ready.connect(self._update_remote_list)
        self.remove_result_ready.connect(self._update_remove_result)
        self.transfer_log_ready.connect(self._on_transfer_log)

        ft = self.controller.file_transfer
        ft.add_list_listener(self._on_remote_list)
        ft.add_remove_listener(self._on_remove_result)
        ft.add_log_listener(self._on_transfer_log_signal)
        self._listeners_registered = True

        debug_log('[文件传输] 初始化开始', 'viewer')

        try:
            self._build_ui()
            debug_log('[文件传输] UI构建完成', 'viewer')
            self._refresh_local()
            debug_log('[文件传输] 本地文件已加载', 'viewer')
            QTimer.singleShot(100, self._safe_refresh_remote)
        except Exception as e:
            import traceback
            traceback.print_exc()
            debug_log(f'[文件传输] 初始化异常: {e}', 'viewer')
            QMessageBox.critical(self, '错误', f'文件传输界面初始化失败:\n{e}')
            self.close()

    # ---- 传输日志 -> 进度条 ----
    def _on_transfer_log_signal(self, msg):
        """在接收线程被调用 -> 只发信号"""
        try:
            self.transfer_log_ready.emit(msg)
        except Exception:
            pass

    def _on_transfer_log(self, msg):
        """GUI 线程: 用真实百分比驱动进度条, 结束后隐藏"""
        try:
            self.lbl_status.setText(msg)
            m = _PCT_RE.search(msg)
            if m:
                try:
                    pct = max(0, min(100, int(m.group(1))))
                except ValueError:
                    pct = None
                if pct is not None:
                    self.progress.setRange(0, 100)
                    self.progress.setValue(pct)
                    self.progress.show()
            if any(w in msg for w in _DONE_WORDS):
                self.progress.hide()
                if '不完整' in msg or '失败' in msg or '错误' in msg:
                    self.lbl_status.setStyleSheet('color: #ff4d4f;')
                else:
                    self.lbl_status.setStyleSheet('color: #52c41a;')
        except Exception as e:
            debug_log(f'[文件传输] 更新进度失败: {e}', 'viewer')

    def closeEvent(self, event):
        """解绑所有回调, 避免接收线程回调到已销毁的窗口"""
        try:
            ft = self.controller.file_transfer
            if self._listeners_registered:
                ft.remove_list_listener(self._on_remote_list)
                ft.remove_remove_listener(self._on_remove_result)
                ft.remove_log_listener(self._on_transfer_log_signal)
                self._listeners_registered = False
        except Exception:
            pass
        super().closeEvent(event)

    def _open_recv_dir(self):
        """打开下载保存目录"""
        try:
            os.makedirs(self.recv_dir, exist_ok=True)
            os.startfile(self.recv_dir)
        except Exception as e:
            QMessageBox.warning(self, '打开失败', f'{e}')

    def _safe_refresh_remote(self):
        try:
            self._refresh_remote()
            debug_log('[文件传输] 远程文件列表请求已发送', 'viewer')
        except Exception as e:
            import traceback
            traceback.print_exc()
            debug_log(f'[文件传输] 加载远程文件异常: {e}', 'viewer')
            QMessageBox.warning(self, '警告', f'加载远程文件列表失败:\n{e}')

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel('文件传输')
        title.setStyleSheet('font-size: 16px; font-weight: bold; color: #333;')
        title_row.addWidget(title)
        title_row.addStretch()

        # 明确显示"下载保存到哪", 并提供一键打开 —— 旧版没有任何地方显示落盘位置
        self.lbl_recv_dir = QLabel(f'下载保存到: {self.recv_dir}')
        self.lbl_recv_dir.setStyleSheet('color: #999; font-size: 12px;')
        self.lbl_recv_dir.setToolTip(self.recv_dir)
        title_row.addWidget(self.lbl_recv_dir)

        btn_open_dir = QPushButton('打开目录')
        btn_open_dir.setObjectName('SecondaryBtn')
        btn_open_dir.clicked.connect(self._open_recv_dir)
        title_row.addWidget(btn_open_dir)

        layout.addLayout(title_row)

        splitter = QSplitter(Qt.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_header = QLabel('📂 本地电脑')
        left_header.setStyleSheet('font-weight: bold; color: #333; padding: 5px;')
        left_layout.addWidget(left_header)

        nav_row = QHBoxLayout()
        self.btn_local_back = QPushButton('⬅')
        self.btn_local_back.setFixedWidth(35)
        self.btn_local_back.clicked.connect(self._local_go_back)
        nav_row.addWidget(self.btn_local_back)

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

        self.local_tree = QTreeWidget()
        self.local_tree.setHeaderLabels(['名称', '大小', '类型', '修改时间'])
        self.local_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.local_tree.setRootIsDecorated(False)
        self.local_tree.setSortingEnabled(False)
        self.local_tree.itemDoubleClicked.connect(self._on_local_double_click)
        self.local_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_tree.customContextMenuRequested.connect(self._local_context_menu)
        left_layout.addWidget(self.local_tree, 1)

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

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_header = QLabel('🌐 远程电脑')
        right_header.setStyleSheet(f'font-weight: bold; color: {THEME["primary"]}; padding: 5px;')
        right_layout.addWidget(right_header)

        rnav_row = QHBoxLayout()
        self.btn_remote_back = QPushButton('⬅')
        self.btn_remote_back.setFixedWidth(35)
        self.btn_remote_back.clicked.connect(self._remote_go_back)
        rnav_row.addWidget(self.btn_remote_back)

        self.cmb_remote_drive = QComboBox()
        self.cmb_remote_drive.setFixedWidth(80)
        # 顺序不能反: 先 addItem 再 connect。
        # addItem 会立即触发 currentIndexChanged -> _on_remote_drive_changed ->
        # _refresh_remote -> lbl_remote_path 尚未创建 -> AttributeError
        # (PyQt5 槽函数异常默认直接 abort 进程 = 打开文件传输必闪退)
        self.cmb_remote_drive.addItem('\\', '/')
        self.cmb_remote_drive.currentIndexChanged.connect(self._on_remote_drive_changed)
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

        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(['名称', '大小', '类型', '修改时间'])
        self.remote_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.remote_tree.setRootIsDecorated(False)
        self.remote_tree.setSortingEnabled(False)
        self.remote_tree.itemDoubleClicked.connect(self._on_remote_double_click)
        self.remote_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_tree.customContextMenuRequested.connect(self._remote_context_menu)
        right_layout.addWidget(self.remote_tree, 1)

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

        self.progress = QProgressBar()
        self.progress.setFixedHeight(20)
        self.progress.hide()
        layout.addWidget(self.progress)

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
        self.cmb_local_drive.blockSignals(True)
        self.cmb_local_drive.clear()
        import string
        drives_found = []
        try:
            for letter in string.ascii_uppercase:
                drive = f'{letter}:/'
                if os.path.exists(drive):
                    drives_found.append(f'{letter}:')
        except Exception:
            pass

        if not drives_found:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                bitmask = kernel32.GetLogicalDrives()
                for i, letter in enumerate(string.ascii_uppercase):
                    if bitmask & (1 << i):
                        drives_found.append(f'{letter}:')
            except Exception:
                for letter in 'CDEFGH':
                    drives_found.append(f'{letter}:')

        for drive in drives_found:
            self.cmb_local_drive.addItem(drive, drive)
        self.cmb_local_drive.blockSignals(False)

    def _on_local_drive_changed(self, index):
        drive = self.cmb_local_drive.itemData(index)
        if drive and os.path.exists(drive):
            self.local_path = drive
            self._refresh_local()

    def _on_remote_drive_changed(self, index):
        drive = self.cmb_remote_drive.itemData(index)
        if drive:
            self.remote_path = drive
            self._refresh_remote()

    def _update_remote_drives_from_list(self, items):
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
        # 右键就把该行选中, 避免"右键 A 却操作了之前选中的 B"
        self.local_tree.setCurrentItem(item)

        menu = QMenu(self)
        act_upload = QAction('⬆ 上传到远程', self)
        act_upload.triggered.connect(self._upload_selected)
        menu.addAction(act_upload)

        data = item.data(0, Qt.UserRole) or {}
        act_open = QAction('📂 在资源管理器中打开', self)
        act_open.setEnabled(bool(data.get('full_path')) and not data.get('is_parent'))
        act_open.triggered.connect(lambda: self._open_in_explorer(item))
        menu.addAction(act_open)

        menu.exec_(self.local_tree.viewport().mapToGlobal(pos))

    def _open_in_explorer(self, item):
        """在资源管理器中打开。

        注意: ".. (返回上级)" 这类列表项的字典里没有 full_path,
        旧实现直接下标取值 -> 槽函数抛 KeyError -> 进程被打死。
        """
        data = item.data(0, Qt.UserRole) or {}
        path = data.get('full_path')
        if not path:
            self.lbl_status.setText('该项不支持在资源管理器中打开')
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, '提示', f'路径已不存在:\n{path}')
            self._refresh_local()
            return
        try:
            os.startfile(path)
        except Exception as e:
            QMessageBox.warning(self, '打开失败', f'{e}')

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
                self.progress.setRange(0, 0)
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
        if not hasattr(self, 'lbl_remote_path'):
            return  # UI 尚未构建完成, 防御性守卫
        self.lbl_remote_path.setText('加载中...')
        self.controller.file_transfer.list_remote_dir(self.remote_path)

    def _on_remote_list(self, resp):
        debug_log(f'[文件传输] 收到远程列表: path={resp.get("path")}, items={len(resp.get("items", []))}', 'viewer')
        self.remote_list_ready.emit(resp)

    def _update_remote_list(self, resp):
        try:
            self.remote_path = resp.get('path', self.remote_path)
            self.lbl_remote_path.setText(self.remote_path)
            self.remote_tree.clear()

            style = QApplication.style()
            folder_icon = style.standardIcon(QStyle.SP_DirIcon)
            file_icon = style.standardIcon(QStyle.SP_FileIcon)
            back_icon = style.standardIcon(QStyle.SP_DriveHDIcon)

            items = resp.get('items', [])

            if self.remote_path == '/' or self.remote_path.endswith(':\\') or len(self.remote_path) <= 3:
                self._update_remote_drives_from_list(items)

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
            debug_log(f'[文件传输] 更新远程列表异常: {e}', 'viewer')
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
        # 同样先选中右键的那一行
        self.remote_tree.setCurrentItem(item)

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
        self.remove_result_ready.emit({'success': success})

    def _update_remove_result(self, data):
        try:
            success = data.get('success', False)
            if success:
                self._refresh_remote()
                self.lbl_status.setText('删除成功')
            else:
                self.lbl_status.setText('删除失败')
        except Exception as e:
            debug_log(f'[文件传输] 更新删除结果异常: {e}', 'viewer')


class ViewerWindow(QMainWindow):
    """ToDesk 风格远程桌面窗口

    mode:
      'control' -> 正常远程控制
      'view'    -> 仅观看: ScreenWidget 完全不发送键鼠事件
    """

    def __init__(self, controller, screen_idx=0, total_screens=1, all_controllers=None,
                 mode='control'):
        super().__init__()
        self.controller = controller
        self.mode = mode
        self.readonly = (mode == 'view')
        self.screen_idx = screen_idx
        self.total_screens = total_screens
        self.all_controllers = all_controllers or [controller]

        screen_info = f'屏幕 {screen_idx + 1}' if total_screens > 1 else '远程桌面'
        self.setWindowTitle(f'MyDesk {screen_info}')
        self.resize(1280, 760)
        self.setStyleSheet(VIEWER_STYLE)
        self._is_fullscreen = False
        self._size_autoset = False        # 只在首次收到分辨率时自动调整窗口

        # 接收目录: 立刻同步给 FileTransfer, 否则下载会落到进程 CWD
        from config import get_setting
        try:
            self._recv_dir = get_setting('save_path') or DEFAULT_RECV_DIR
        except Exception:
            self._recv_dir = DEFAULT_RECV_DIR
        try:
            os.makedirs(self._recv_dir, exist_ok=True)
        except Exception:
            self._recv_dir = DEFAULT_RECV_DIR
            os.makedirs(self._recv_dir, exist_ok=True)
        self.controller.file_transfer.recv_dir = self._recv_dir

        self._float_bar = None
        self._float_timer = QTimer(self)
        self._float_timer.setSingleShot(True)
        self._float_timer.setInterval(3000)
        self._float_timer.timeout.connect(self._hide_float_bar)

        self.screen = ScreenWidget(controller, screen_idx, readonly=self.readonly)
        self.screen.setMinimumSize(320, 240)
        self.setCentralWidget(self.screen)
        self.screen.local_shortcut.connect(self._on_local_shortcut)

        self.toolbar = self._build_toolbar()
        self.addToolBar(self.toolbar)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel('就绪')
        self.status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.hide()
        self.status.addPermanentWidget(self.progress)

        self.log_bridge = LogBridge()
        self.log_bridge.log_ready.connect(self._on_log)
        self.controller.file_transfer.on_log = self.log_bridge.log_ready.emit

        # 先连信号, 再 start() 注册消息回调并索要显示器信息 ——
        # 顺序反了会让认证后立刻到达的首批帧/分辨率信息丢给"没有接收者的信号"
        self.receiver = ScreenReceiver(controller)
        self.receiver.frame_ready.connect(self._on_frame)
        self.receiver.info_ready.connect(self._on_info)
        self.receiver.disconnected.connect(self._on_disconnect)
        self.receiver.cursor_ready.connect(self._on_cursor)
        self.receiver.screen_list_ready.connect(self._on_screen_list)
        self.receiver.start()

        debug_log(f'[ViewerWindow] 初始化完成: screen_idx={screen_idx}, '
                  f'mode={mode}, controller.connected={controller.connected}', 'viewer')

        self._file_dialog = None
        if self.readonly:
            self._file_dialog = None
            self.btn_pause.setEnabled(False)

        # 剪贴板: 默认开启, 并且必须主动告诉被控端 "CLIPBOARD:on",
        # 否则被控端不会把它的剪贴板推过来(旧版按钮显示已开启, 实际只有单向可用)
        self._clip_enabled = True
        self._last_clip_text = None
        self.receiver.clipboard_ready.connect(self._on_remote_clipboard)
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(500)
        self._clip_timer.timeout.connect(self._poll_clipboard)
        self._clip_timer.start()
        try:
            self.controller.send(MSG_CONTROL, f'{CTL_CLIPBOARD}on'.encode('utf-8'))
        except Exception:
            pass

        # 告诉被控端"我只看第 screen_idx 块屏"。
        # 这是最有效的一步省 CPU: 双屏机器上被控端不必再抓/编另一块屏(实测 ~50ms/帧)。
        self._send_screen_select()

        # 会话期间阻止系统休眠 (可通过设置关闭)
        self._sleep_prevented = False
        try:
            if get_setting('prevent_sleep', True):
                self._set_prevent_sleep(True)
        except Exception:
            pass

        QTimer.singleShot(100, self._focus_screen)

    # ---- 休眠抑制 ----
    def _set_prevent_sleep(self, enabled):
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            flags = (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                     if enabled else ES_CONTINUOUS)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self._sleep_prevented = bool(enabled)
        except Exception as e:
            debug_log(f'[ViewerWindow] 设置休眠抑制失败: {e}', 'viewer')

    def _on_local_shortcut(self, name):
        """ScreenWidget 里被本地消费的快捷键"""
        if name == _SHORTCUT_F11:
            self._toggle_fullscreen()
        elif name == _SHORTCUT_ESC:
            # Esc 只在全屏时退出, 不全屏时不做任何事(否则会误触发进入全屏)
            if self._is_fullscreen:
                self._toggle_fullscreen()
        elif name == _SHORTCUT_FILE:
            self._open_file_transfer()
        elif name == _SHORTCUT_PAUSE:
            self.btn_pause.setChecked(not self.btn_pause.isChecked())

    def _send_screen_select(self):
        """把"当前在看哪块屏"告诉被控端, 让它只抓/只编那一块"""
        try:
            self.controller.send(
                MSG_CONTROL,
                f'{CTL_SCREEN_SELECT}{self.screen_idx}'.encode('utf-8'))
        except Exception:
            pass

    def _on_cursor(self, x, y, visible):
        try:
            self.screen.set_cursor_pos(x, y, visible)
        except Exception:
            pass

    def _on_screen_list(self, screens):
        """被控端报告了多显示器列表 -> 在工具栏提供切换"""
        try:
            self._screens = list(screens)
            combo = getattr(self, 'cmb_screen', None)
            if combo is None:
                return
            combo.blockSignals(True)
            combo.clear()
            for idx, w, h, left, top in screens:
                combo.addItem(f'屏幕 {idx + 1} ({w}x{h})', idx)
            # 选中当前屏幕
            for i in range(combo.count()):
                if combo.itemData(i) == self.screen_idx:
                    combo.setCurrentIndex(i)
                    break
            combo.blockSignals(False)
            combo.setVisible(len(screens) > 1)
        except Exception as e:
            debug_log(f'[ViewerWindow] 填充显示器列表失败: {e}', 'viewer')

    def _on_screen_changed(self, index):
        """切换显示哪个显示器"""
        try:
            combo = self.cmb_screen
            idx = combo.itemData(index)
            if idx is None or idx == self.screen_idx:
                return
            self.screen_idx = int(idx)
            self.screen.screen_idx = int(idx)
            self.screen.pm = None
            self.screen._scaled_cache = None
            self.screen.clear_cursor()
            info = self.receiver.screen_sizes.get(int(idx))
            if info:
                w, h, left, top = info
                self.screen.set_remote_size(w, h)
                self.screen.set_origin(left, top)
                self.setWindowTitle(f'MyDesk 屏幕 {int(idx) + 1} ({w}x{h})')
            else:
                self.screen.set_remote_size(0, 0)
                self.setWindowTitle(f'MyDesk 屏幕 {int(idx) + 1}')
            self.screen.update()
            # 通知被控端切换抓屏目标 (旧屏幕停止编码, 新屏幕开始)
            self._send_screen_select()
            self._focus_screen()
            self.status_label.setText(f'已切换到屏幕 {int(idx) + 1}')
        except Exception as e:
            debug_log(f'[ViewerWindow] 切换显示器失败: {e}', 'viewer')

    def _build_toolbar(self):
        tb = QToolBar('工具')
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))

        self.lbl_conn = QLabel(' 已连接 ')
        self.lbl_conn.setStyleSheet(f'color:{THEME["primary"]}; font-weight:bold; padding:0 8px;')
        tb.addWidget(self.lbl_conn)

        if self.readonly:
            self.lbl_mode = QLabel(' 👁 仅观看(不发送操作) ')
            self.lbl_mode.setStyleSheet('color:#d46b08; font-weight:bold; padding:0 8px;')
            tb.addWidget(self.lbl_mode)

        tb.addSeparator()

        self.btn_pause = QPushButton('⏸ 暂停')
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip('暂停向本机推送画面 (Ctrl+Alt+P)')
        self.btn_pause.toggled.connect(self._toggle_pause)
        tb.addWidget(self.btn_pause)

        tb.addSeparator()

        # 默认画质/帧率来自设置(以前是写死的 65/30, 设置页无效)
        from config import get_setting
        try:
            init_q = int(get_setting('quality', 65))
            init_fps = int(get_setting('fps', 30))
        except Exception:
            init_q, init_fps = 65, 30
        init_q = max(10, min(100, init_q))
        init_fps = max(5, min(60, init_fps))

        tb.addWidget(QLabel(' 画质'))
        self.sl_quality = QSlider(Qt.Horizontal)
        self.sl_quality.setRange(10, 100)
        self.sl_quality.setValue(init_q)
        self.sl_quality.setFixedWidth(100)
        self.sl_quality.valueChanged.connect(self._set_quality)
        tb.addWidget(self.sl_quality)
        self.lbl_quality = QLabel(str(init_q))
        self.lbl_quality.setFixedWidth(24)
        self.sl_quality.valueChanged.connect(lambda v: self.lbl_quality.setText(str(v)))
        tb.addWidget(self.lbl_quality)

        tb.addSeparator()

        tb.addWidget(QLabel(' 帧率'))
        self.sl_fps = QSlider(Qt.Horizontal)
        self.sl_fps.setRange(5, 60)
        self.sl_fps.setValue(init_fps)
        self.sl_fps.setFixedWidth(80)
        self.sl_fps.valueChanged.connect(self._set_fps)
        tb.addWidget(self.sl_fps)
        self.lbl_fps = QLabel(str(init_fps))
        self.lbl_fps.setFixedWidth(24)
        self.sl_fps.valueChanged.connect(lambda v: self.lbl_fps.setText(str(v)))
        tb.addWidget(self.lbl_fps)

        tb.addSeparator()

        self.btn_file_transfer = QPushButton('📁 文件传输')
        self.btn_file_transfer.setToolTip('Ctrl+Shift+F')
        self.btn_file_transfer.clicked.connect(self._open_file_transfer)
        tb.addWidget(self.btn_file_transfer)

        self.btn_clipboard = QPushButton('📋 剪贴板')
        self.btn_clipboard.setCheckable(True)
        self.btn_clipboard.setChecked(True)
        # 先 setChecked 再 connect: 顺序反了会立刻触发一次 toggled,
        # 在 CLIPBOARD:on 还没发出去之前又把它切成 off
        self.btn_clipboard.toggled.connect(self._toggle_clipboard)
        tb.addWidget(self.btn_clipboard)

        self.btn_recv_dir = QPushButton('📁 接收目录')
        self.btn_recv_dir.setToolTip(self._recv_dir)
        self.btn_recv_dir.clicked.connect(self._set_recv_dir)
        tb.addWidget(self.btn_recv_dir)

        tb.addSeparator()

        # 多显示器切换: 被控端下发 SCREEN_LIST 后由 _on_screen_list 填充并显示
        self.cmb_screen = QComboBox()
        self.cmb_screen.setFixedWidth(130)
        self.cmb_screen.setVisible(False)
        self.cmb_screen.currentIndexChanged.connect(self._on_screen_changed)
        tb.addWidget(self.cmb_screen)

        tb.addSeparator()

        self.btn_fullscreen = QPushButton('⛶ 全屏')
        self.btn_fullscreen.setToolTip('F11 切换, Esc 退出')
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        tb.addWidget(self.btn_fullscreen)

        return tb

    def _create_float_bar(self):
        self._float_bar = FloatToolBar()
        self._float_bar.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self._float_bar.quality_changed.connect(self._set_quality)
        self._float_bar.fps_changed.connect(self._set_fps)
        self._float_bar.fullscreen_exit.connect(self._toggle_fullscreen)
        self._float_bar.file_transfer_requested.connect(self._open_file_transfer)
        self._float_bar.clipboard_toggled.connect(self._toggle_clipboard)
        self._float_bar.pause_toggled.connect(self._toggle_pause)
        # 回填当前状态, 否则用户在全屏里轻拖一下滑杆就会把画质从 90 悄悄改成 66
        try:
            self._float_bar.set_state(self.sl_quality.value(), self.sl_fps.value(),
                                      self._clip_enabled, self.btn_pause.isChecked())
        except Exception:
            pass
        # 显示并启动 3 秒自动隐藏 (旧版只 show 不启动计时器 -> 永久遮挡画面)
        self._show_float_bar()

    def _position_float_bar(self):
        if self._float_bar:
            screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = geo.center().x() - self._float_bar.width() // 2
                y = geo.top() + 10
                self._float_bar.move(x, y)

    def _show_float_bar(self):
        if self._float_bar:
            self._float_bar.show()
            self._float_bar.raise_()
            self._float_timer.start()

    def _hide_float_bar(self):
        if self._float_bar:
            self._float_bar.hide()

    def _on_log(self, msg):
        self.status_label.setText(msg)
        m = _PCT_RE.search(msg)
        if m:
            try:
                self.progress.setRange(0, 100)
                self.progress.setValue(max(0, min(100, int(m.group(1)))))
                self.progress.show()
            except ValueError:
                pass
            return
        if any(w in msg for w in _DONE_WORDS):
            self.progress.hide()

    def _on_frame(self, jpeg_bytes, screen_idx, total_screens):
        """GUI 线程: 渲染一帧。

        取的是 receiver 里"最新一帧"而不是信号参数, 这样在解码跟不上时
        掉队的事件会复用最新画面, 不会逐帧排队造成延迟累积。
        """
        try:
            latest = self.receiver.take_latest_frame()
            if latest is not None:
                jpeg_bytes, screen_idx, total_screens = latest
            if not jpeg_bytes or len(jpeg_bytes) < 2:
                return
            if screen_idx != self.screen_idx:
                return
            pm = QPixmap()
            load_ok = False
            try:
                load_ok = pm.loadFromData(jpeg_bytes, 'JPEG')
            except Exception as e:
                debug_log(f'[错误] loadFromData 异常: {e}, jpeg_size={len(jpeg_bytes)}', 'viewer')
            if not (load_ok and pm.width() > 0):
                # PIL 回退
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(jpeg_bytes))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    data = img.tobytes('raw', 'RGB')
                    qimg = QImage(data, img.width, img.height, img.width * 3,
                                  QImage.Format_RGB888)
                    pm = QPixmap.fromImage(qimg.copy())
                    load_ok = pm.width() > 0
                except Exception as e:
                    debug_log(f'[错误] 画面解码失败: {e}, jpeg_size={len(jpeg_bytes)}', 'viewer')
                    return
            if load_ok:
                self.screen.set_pixmap(pm)
        except Exception as e:
            debug_log(f'[错误] _on_frame 未捕获异常(已拦截): {e}', 'viewer')

    def _on_info(self, w, h, screen_idx, total_screens):
        if screen_idx != self.screen_idx:
            return
        self.screen.set_remote_size(w, h)
        info = self.receiver.screen_sizes.get(screen_idx)
        if info:
            self.screen.set_origin(info[2], info[3])
        if w <= 0 or h <= 0:
            return
        self.total_screens = max(self.total_screens, total_screens)
        screen_info = (f'屏幕 {self.screen_idx + 1}/{total_screens}'
                       if total_screens > 1 else '远程桌面')
        self.setWindowTitle(f'MyDesk {screen_info} ({w}x{h})')
        # 只在首次收到分辨率时自动调整窗口尺寸, 之后不再覆盖用户手动调整
        if not self._size_autoset:
            self._size_autoset = True
            try:
                from PyQt5.QtWidgets import QApplication as _App
                scr = _App.primaryScreen()
                avail = scr.availableGeometry() if scr else None
                max_w = avail.width() - 40 if avail else 1920
                max_h = avail.height() - 80 if avail else 1080
                self.resize(min(w + 20, max_w), min(h + 80, max_h))
            except Exception:
                pass
        self._focus_screen()

    def _focus_screen(self):
        self.screen.setFocus()

    def _on_disconnect(self):
        self.setWindowTitle('MyDesk - 连接已断开')
        self.status_label.setText('连接已断开')
        self.status_label.setStyleSheet('color: #ff4d4f;')
        self.lbl_conn.setText(' 已断开 ')
        self.lbl_conn.setStyleSheet('color:#ef4444; font-weight:bold; padding:0 8px;')
        # 输入已无效, 明确告知用户(并放开可能按住的键)
        try:
            self.screen._release_all_keys()
        except Exception:
            pass
        self.screen.set_readonly(True)

    def _toggle_pause(self, checked):
        try:
            self.controller.send(MSG_CONTROL,
                                 (CTL_PAUSE if checked else CTL_RESUME).encode('utf-8'))
        except Exception:
            pass
        self.btn_pause.setText('▶ 恢复' if checked else '⏸ 暂停')
        self.status_label.setText('画面推送已暂停' if checked else '画面推送已恢复')
        if self._float_bar:
            self._float_bar.btn_pause.setChecked(checked)

    def _set_quality(self, val):
        try:
            self.controller.send(MSG_CONTROL, f'{CTL_QUALITY}{val}'.encode('utf-8'))
        except Exception:
            pass
        if self.sender() is not self.sl_quality:
            self.sl_quality.blockSignals(True)
            self.sl_quality.setValue(val)
            self.sl_quality.blockSignals(False)
        if self._float_bar and self.sender() is not self._float_bar.sl_quality:
            self._float_bar.sl_quality.blockSignals(True)
            self._float_bar.sl_quality.setValue(val)
            self._float_bar.sl_quality.blockSignals(False)

    def _set_fps(self, val):
        try:
            self.controller.send(MSG_CONTROL, f'{CTL_FPS}{val}'.encode('utf-8'))
        except Exception:
            pass
        if self.sender() is not self.sl_fps:
            self.sl_fps.blockSignals(True)
            self.sl_fps.setValue(val)
            self.sl_fps.blockSignals(False)
        if self._float_bar and self.sender() is not self._float_bar.sl_fps:
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
                try:
                    self._float_bar.close()
                except Exception:
                    pass
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
        d = QFileDialog.getExistingDirectory(
            self, '选择文件接收目录', self._recv_dir
        )
        if d:
            self._recv_dir = d
            self.controller.file_transfer.recv_dir = d
            self.btn_recv_dir.setToolTip(d)
            self.status_label.setText(f'接收目录: {d}')

    def _open_file_transfer(self):
        try:
            # WA_DeleteOnClose 的对话框关闭后会自行销毁, 这里用 destroyed 清引用
            if self._file_dialog is not None:
                try:
                    self._file_dialog.close()
                except Exception:
                    pass
                self._file_dialog = None
            self._file_dialog = FileTransferDialog(
                self.controller, self._recv_dir, self
            )
            self._file_dialog.file_upload_requested.connect(self._do_upload)
            self._file_dialog.file_download_requested.connect(self._do_download)
            self._file_dialog.destroyed.connect(self._on_file_dialog_destroyed)
            self._file_dialog.show()
        except Exception as e:
            debug_log(f'[ViewerWindow] 打开文件传输失败: {e}', 'viewer')
            QMessageBox.critical(self, '错误', f'打开文件传输失败:\n{e}')

    def _on_file_dialog_destroyed(self, _obj=None):
        self._file_dialog = None

    def _do_upload(self, filepath):
        self.controller.upload_file(filepath)
        self.status_label.setText(f'正在上传: {os.path.basename(filepath)}')
        self.progress.setRange(0, 0)
        self.progress.show()

    def _do_download(self, remote_path):
        self.controller.download_file(remote_path)
        self.status_label.setText(f'正在下载: {os.path.basename(remote_path)}')
        self.progress.setRange(0, 0)
        self.progress.show()

    def keyPressEvent(self, event):
        """兜底: 焦点在工具栏/状态栏时也能用快捷键。

        正常情况下键盘焦点在 ScreenWidget, 它已经拦截了这些组合;
        这里保留一份是为了焦点跑到工具栏按钮上时依旧可用。
        """
        if event.key() == Qt.Key_Escape and self._is_fullscreen:
            self._toggle_fullscreen()
            return
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
            return
        if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier) and event.key() == Qt.Key_F:
            self._open_file_transfer()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_fullscreen:
            self._show_float_bar()
        super().mouseMoveEvent(event)

    def _toggle_clipboard(self, checked):
        self._clip_enabled = checked
        try:
            self.controller.send(
                MSG_CONTROL,
                f'{CTL_CLIPBOARD}{"on" if checked else "off"}'.encode('utf-8'))
        except Exception:
            pass
        if checked:
            self._last_clip_text = None

    def _poll_clipboard(self):
        if not self._clip_enabled or not self.controller.connected:
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
        self._last_clip_text = text
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            pass

    def closeEvent(self, event):
        """彻底清理: 定时器、消息回调、文件对话框回调、休眠抑制、连接"""
        try:
            self._clip_timer.stop()
        except Exception:
            pass
        try:
            self._float_timer.stop()
        except Exception:
            pass
        # 先放开可能按住的键, 避免被控端修饰键卡死
        try:
            self.screen._release_all_keys()
        except Exception:
            pass
        # 解绑文件对话框的回调, 否则接收线程会回调到已销毁的对象
        try:
            if self._file_dialog is not None:
                dlg = self._file_dialog
                self._file_dialog = None
                try:
                    dlg.close()
                except Exception:
                    pass
            ft = self.controller.file_transfer
            try:
                ft.on_log = lambda _m: None
            except Exception:
                pass
        except Exception:
            pass
        # 每个会话独占一个 Controller, 窗口关闭就断开连接
        try:
            self.receiver.stop()
        except Exception:
            pass
        try:
            self.controller.disconnect()
        except Exception:
            pass
        if self._sleep_prevented:
            self._set_prevent_sleep(False)
        if self._float_bar:
            try:
                self._float_bar.close()
            except Exception:
                pass
            self._float_bar = None
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
        QMessageBox.critical(None, '连接失败', f'无法连接 {host}:{port}\n{e}')
        sys.exit(1)
    win = ViewerWindow(controller)
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    import sys
    main()
