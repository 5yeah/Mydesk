# -*- coding: utf-8 -*-
"""MyDesk 统一主题

全应用唯一配色来源，消除原先 ui.py(#1677ff) / unified_ui.py(#2563eb)
/ server_ui.py(#4CAF50) / viewer.py(#1a73e8) 四套混用的问题。
"""

# ===== 统一调色板 (Ant Design 蓝) =====
THEME = {
    'primary': '#1677ff',
    'primary_hover': '#4096ff',
    'primary_active': '#0958d9',
    'primary_bg': '#e6f4ff',     # 浅蓝底 (选中/悬浮)
    'success': '#52c41a',
    'success_bg': '#f6ffed',
    'danger': '#ff4d4f',
    'danger_hover': '#ff7875',
    'warning': '#faad14',
    'warning_bg': '#fffbe6',
    'text': '#1a1a1a',
    'text_secondary': '#666666',
    'text_placeholder': '#999999',
    'border': '#e8e8e8',
    'border_light': '#f0f0f0',
    'bg': '#ffffff',
    'bg_gray': '#f5f6fa',
    'bg_sidebar': '#f8f9fa',
}


# ===== 主窗口样式 (原 ui.py TODESK_STYLE, 已是 #1677ff) =====
MAIN_STYLE = """
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

/* 输入框 (统一高度 36px: 8px上下内边距 + 18px内容 + 2px边框) */
QLineEdit {
    background-color: #ffffff;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 8px 12px;
    color: #333;
    font-size: 14px;
}

QLineEdit:focus {
    border-color: #1677ff;
}

/* 下拉框/数字框 (与输入框同高同字号, 保证同一行内协调) */
QSpinBox, QComboBox {
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    padding: 7px 12px;
    min-height: 18px;
    background-color: #ffffff;
    color: #333;
    font-size: 14px;
}

QSpinBox:focus, QComboBox:focus {
    border-color: #1677ff;
}

QComboBox::drop-down {
    border: none;
    width: 28px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #999999;
    margin-right: 10px;
}

QComboBox QAbstractItemView {
    border: 1px solid #e8e8e8;
    border-radius: 6px;
    background: #ffffff;
    selection-background-color: #e6f4ff;
    selection-color: #1a1a1a;
    outline: none;
}

/* 分组框 */
QGroupBox {
    background: #ffffff;
    border: 1px solid #e8e8e8;
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-size: 13px;
    color: #333;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 4px;
    color: #333;
    font-weight: bold;
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

/* Tab样式 (扁平下划线风格, 与卡片化页面协调) */
QTabWidget::pane {
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    background: #ffffff;
    top: -1px;
}

QTabBar::tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 18px;
    margin-right: 4px;
    color: #666666;
    font-size: 13px;
}

QTabBar::tab:selected {
    background: transparent;
    color: #1677ff;
    border-bottom: 2px solid #1677ff;
}

QTabBar::tab:hover {
    color: #1677ff;
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

/* 开关控件: ToggleSwitch (gui/widgets.py) 为自绘控件, 不走 QSS。
   如仍有零散 QCheckBox, 保留基础样式兜底。 */
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


# ===== 远程会话窗口样式 (原 viewer.py _TODESK_STYLE, 统一为 #1677ff) =====
_VIEWER_STYLE_RAW = """
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

_FLOAT_BAR_STYLE_RAW = """
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


def _unify(qss):
    """把原 viewer 的旧蓝 (#1a73e8 / #1557b0 / #e8eaf6) 统一为 THEME 配色"""
    return (qss
            .replace('#1a73e8', THEME['primary'])
            .replace('#1557b0', THEME['primary_active'])
            .replace('#e8eaf6', THEME['primary_bg']))


VIEWER_STYLE = _unify(_VIEWER_STYLE_RAW)
FLOAT_BAR_STYLE = _FLOAT_BAR_STYLE_RAW
