# -*- coding: utf-8 -*-
"""MyDesk 图标与托盘图标生成 (纯代码绘制, 无外部资源依赖)"""
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtCore import Qt

from .theme import THEME


class IconGenerator:
    """生成简单的圆形字母图标"""

    @staticmethod
    def create_icon(text, color=THEME['primary'], size=24):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, size - 4, size - 4)

        painter.setPen(QColor('white'))
        font = QFont('Microsoft YaHei')
        font.setPixelSize(size // 2)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)

        painter.end()
        return QIcon(pixmap)


def make_tray_icon(color_hex=THEME['success']):
    """生成圆形状态托盘图标 (中心字母 M)"""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color_hex))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 24, 24)
    p.setPen(QColor('#fff'))
    p.setFont(QFont('Arial', 10, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, 'M')
    p.end()
    return QIcon(pm)


def make_app_icon():
    """生成应用主图标 (蓝紫渐变圆 + M)"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    from PyQt5.QtGui import QLinearGradient
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor(THEME['primary']))
    grad.setColorAt(1, QColor('#7c3aed'))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor('#fff'))
    p.setFont(QFont('Arial', 28, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, 'M')
    p.end()
    return QIcon(pm)
