"""
简单测试 - 验证托盘和窗口功能
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QWidget, QLabel, QVBoxLayout, QPushButton
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt5.QtCore import Qt, QTimer

print("创建 QApplication...")
app = QApplication(sys.argv)
app.setStyle('Fusion')

print("创建主窗口...")
window = QWidget()
window.setWindowTitle('MyDesk 测试')
window.resize(400, 300)

layout = QVBoxLayout(window)
label = QLabel('如果看到这个窗口，说明界面正常！')
label.setAlignment(Qt.AlignCenter)
label.setStyleSheet('font-size: 18px; color: #1677ff;')
layout.addWidget(label)

btn = QPushButton('测试按钮')
layout.addWidget(btn)

print("创建托盘图标...")
pixmap = QPixmap(32, 32)
pixmap.fill(Qt.transparent)
painter = QPainter(pixmap)
painter.setRenderHint(QPainter.Antialiasing)
painter.setBrush(QColor('#1677ff'))
painter.setPen(Qt.NoPen)
painter.drawEllipse(4, 4, 24, 24)
painter.setPen(QColor('white'))
font = painter.font()
font.setPixelSize(14)
font.setBold(True)
painter.setFont(font)
painter.drawText(pixmap.rect(), Qt.AlignCenter, 'M')
painter.end()

icon = QIcon(pixmap)

print("设置托盘...")
tray = QSystemTrayIcon(icon)
tray.setToolTip('MyDesk - 测试')

tray_menu = QMenu()
action_show = QAction('显示窗口', app)
action_show.triggered.connect(lambda: (window.show(), window.activateWindow(), window.raise_()))
tray_menu.addAction(action_show)

tray_menu.addSeparator()

action_quit = QAction('退出', app)
action_quit.triggered.connect(app.quit)
tray_menu.addAction(action_quit)

tray.setContextMenu(tray_menu)
tray.activated.connect(lambda reason: (window.show(), window.activateWindow(), window.raise_()) if reason == QSystemTrayIcon.DoubleClick else None)
tray.show()

print("显示窗口...")
window.show()
print("窗口已显示")

# 3秒后检查
def check_window():
    print(f"窗口可见: {window.isVisible()}")
    print(f"托盘可见: {tray.isVisible()}")
    print("测试完成！窗口应该已经显示了")

QTimer.singleShot(3000, check_window)

print("启动事件循环...")
sys.exit(app.exec_())
