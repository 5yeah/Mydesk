# -*- coding: utf-8 -*-
"""MyDesk 共享控件"""
from PyQt5.QtCore import (
    Qt, pyqtSignal, QSize, QRectF, QPropertyAnimation, QEasingCurve, pyqtProperty,
)
from PyQt5.QtGui import QColor, QPainter, QFontMetrics
from PyQt5.QtWidgets import QWidget, QPushButton, QAbstractButton

from .icons import IconGenerator

# 开关尺寸规范 (与 theme.py 的控件高度体系一致)
_TOGGLE_TRACK_W = 40
_TOGGLE_TRACK_H = 22
_TOGGLE_KNOB_D = 18
_TOGGLE_PAD = 2


class ToggleSwitch(QAbstractButton):
    """iOS 风格滑动开关 (替代 QCheckBox)

    - 自绘圆角滑槽 + 白色圆形滑块, 选中滑块右移 (带 150ms 缓动动画)
    - 支持文字标签, 用法与 QCheckBox 一致: ToggleSwitch('开机自启动')
    - isChecked()/setChecked()/toggled 信号均与 QCheckBox 兼容
    """

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        if text:
            self.setText(text)
        self._pos = 0.0  # 滑块位置 0.0(关) -> 1.0(开)

        self._anim = QPropertyAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to)

    # ---- 动画属性 ----
    @pyqtProperty(float)
    def anim_pos(self):
        return self._pos

    @anim_pos.setter
    def anim_pos(self, value):
        self._pos = value
        self.update()

    def _animate_to(self, checked):
        end = 1.0 if checked else 0.0
        if not self.isVisible():
            # 未显示时直接跳位, 避免首帧闪错状态
            self._pos = end
            self.update()
            return
        self._anim.stop()
        self._anim.setTargetObject(self)
        self._anim.setPropertyName(b'anim_pos')
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(end)
        self._anim.start()

    # ---- 尺寸 ----
    def sizeHint(self):
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        w = _TOGGLE_TRACK_W + (10 + text_w if text_w else 0)
        h = max(_TOGGLE_TRACK_H, fm.height() + 2)
        return QSize(w, h)

    # ---- 绘制 ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)

        h = self.height()
        track_y = (h - _TOGGLE_TRACK_H) / 2

        # 滑槽颜色
        if not self.isEnabled():
            track = QColor('#e4e6eb')
        elif self.isChecked():
            track = QColor('#1677ff' if not self.underMouse() else '#4096ff')
        else:
            track = QColor('#d9d9d9' if not self.underMouse() else '#bfbfbf')
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, track_y, _TOGGLE_TRACK_W, _TOGGLE_TRACK_H),
                          _TOGGLE_TRACK_H / 2, _TOGGLE_TRACK_H / 2)

        # 滑块 (白圆, 带浅色描边增加立体感)
        knob_x = _TOGGLE_PAD + self._pos * (_TOGGLE_TRACK_W - _TOGGLE_KNOB_D - 2 * _TOGGLE_PAD)
        knob_y = track_y + (_TOGGLE_TRACK_H - _TOGGLE_KNOB_D) / 2
        p.setBrush(QColor('#ffffff'))
        p.drawEllipse(QRectF(knob_x, knob_y, _TOGGLE_KNOB_D, _TOGGLE_KNOB_D))

        # 文字标签
        if self.text():
            p.setPen(QColor('#1a1a1a') if self.isEnabled() else QColor('#999999'))
            fm = QFontMetrics(self.font())
            text_y = (h + fm.ascent() - fm.descent()) / 2
            p.drawText(_TOGGLE_TRACK_W + 10, int(text_y), self.text())


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


class SidebarButton(QPushButton):
    """侧边栏导航按钮"""

    nav_clicked = pyqtSignal(str)  # 独立信号名, 避免覆盖 QPushButton.clicked

    def __init__(self, icon_text, text, page_id, parent=None):
        super().__init__(parent)
        self._page_id = page_id
        self.setText(text)
        self.setObjectName('SidebarButton')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

        icon = IconGenerator.create_icon(icon_text)
        self.setIcon(icon)
        self.setIconSize(QSize(24, 24))

        super().clicked.connect(lambda: self.nav_clicked.emit(self._page_id))
