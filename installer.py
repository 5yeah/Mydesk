# -*- coding: utf-8 -*-
"""MyDesk 图形化安装器

体验对标 ToDesk/向日葵安装包:
  欢迎页 → 选择安装位置 → 附加选项(快捷方式/开机自启) → 安装进度 → 完成

打包后生成 MyDesk_Setup.exe, 内部嵌入 MyDesk.exe (主程序)。
双击即可安装, 无需任何依赖。
"""
import os
import sys
import shutil
import subprocess
import ctypes

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QProgressBar, QFileDialog, QMessageBox, QDesktopWidget, QFrame,
    QGraphicsDropShadowEffect,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QFont, QPixmap, QPainter, QColor, QIcon, QLinearGradient

from install_utils import (
    create_shortcut, desktop_path, start_menu_path,
    register_uninstall, unregister_uninstall,
    is_admin, run_as_admin,
)


# ============ 样式 ============
INSTALLER_QSS = """
QMainWindow {
    background: #f5f7fa;
}
QLabel#Header {
    font-size: 22px;
    font-weight: bold;
    color: #1a1a2e;
}
QLabel#Sub {
    font-size: 13px;
    color: #666;
}
QLabel#Step {
    font-size: 15px;
    font-weight: bold;
    color: #2563eb;
}
QLabel#Desc {
    font-size: 13px;
    color: #444;
    line-height: 18px;
}
QFrame#Card {
    background: white;
    border-radius: 12px;
    border: 1px solid #e8ecf1;
}
QPushButton#Primary {
    background: #2563eb;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
    font-size: 14px;
    font-weight: bold;
    min-width: 100px;
}
QPushButton#Primary:hover {
    background: #1d4ed8;
}
QPushButton#Primary:disabled {
    background: #9ca3af;
}
QPushButton#Secondary {
    background: white;
    color: #555;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
    min-width: 80px;
}
QPushButton#Secondary:hover {
    background: #f3f4f6;
}
QLineEdit#PathBox {
    background: #f8fafc;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
}
QCheckBox {
    font-size: 13px;
    spacing: 8px;
}
QProgressBar {
    background: #e8ecf1;
    border: none;
    border-radius: 8px;
    text-align: center;
    font-size: 13px;
    color: #2563eb;
    font-weight: bold;
    min-height: 22px;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 8px;
}
"""


def _get_resource_dir():
    """PyInstaller 打包后获取内置资源目录"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_embedded_exe():
    """查找内置的 MyDesk.exe"""
    base = _get_resource_dir()
    # PyInstaller --add-data 后, 文件在 _MEIPASS 根目录
    candidates = [
        os.path.join(base, 'MyDesk.exe'),
        os.path.join(base, 'app', 'MyDesk.exe'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _create_app_icon():
    """生成安装器图标"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor('#2563eb'))
    grad.setColorAt(1, QColor('#7c3aed'))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor('#fff'))
    p.setFont(QFont('Arial', 28, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, 'M')
    p.end()
    return QIcon(pm)


def _add_firewall_rules(port):
    """为被控端监听端口添加 Windows 防火墙入站放行规则。

    没有这一步的后果: 首次监听时系统会弹一次"是否允许", 用户在静默自启/远程
    会话里根本看不到这个提示, 入站被静默拦截 —— 表现为"对方连不上且没有任何报错"。
    """
    results = []
    name_tcp = 'MyDesk (TCP)'
    name_udp = 'MyDesk Discovery (UDP)'
    try:
        from config import get_discovery_port
        disc = get_discovery_port()
    except Exception:
        disc = port + 1
    rules = [
        (name_tcp, 'TCP', port),
        (name_udp, 'UDP', disc),
    ]
    for name, proto, p in rules:
        try:
            # 先删旧规则, 避免重复添加(端口变了也能更新)
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
                 f'name={name}'],
                capture_output=True, text=True)
            r = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 f'name={name}', 'dir=in', 'action=allow', f'protocol={proto}',
                 f'localport={p}', 'profile=any'],
                capture_output=True, text=True)
            results.append((name, r.returncode == 0,
                            (r.stderr or r.stdout or '').strip()))
        except Exception as e:
            results.append((name, False, str(e)))
    return results


def _remove_firewall_rules():
    for name in ('MyDesk (TCP)', 'MyDesk Discovery (UDP)'):
        try:
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
                 f'name={name}'],
                capture_output=True, text=True)
        except Exception:
            pass


# ============ 安装工作线程 ============
class InstallWorker(QThread):
    progress = pyqtSignal(int, str)   # 进度百分比, 当前操作描述
    finished_ok = pyqtSignal(str)     # 安装目录
    failed = pyqtSignal(str)          # 错误信息

    def __init__(self, install_dir, create_desktop, create_startmenu, autostart):
        super().__init__()
        self.install_dir = install_dir
        self.create_desktop = create_desktop
        self.create_startmenu = create_startmenu
        self.autostart = autostart

    def run(self):
        try:
            # 1. 创建目录
            self.progress.emit(5, '正在创建安装目录...')
            os.makedirs(self.install_dir, exist_ok=True)

            # 2. 复制主程序
            self.progress.emit(15, '正在复制程序文件...')
            src = _get_embedded_exe()
            if not src:
                self.failed.emit('内置程序文件未找到')
                return
            dst_exe = os.path.join(self.install_dir, 'MyDesk.exe')
            shutil.copy2(src, dst_exe)
            self.progress.emit(60, '程序文件复制完成')

            # 3. 创建快捷方式
            if self.create_desktop:
                self.progress.emit(70, '正在创建桌面快捷方式...')
                try:
                    lnk = os.path.join(desktop_path(), 'MyDesk.lnk')
                    create_shortcut(lnk, dst_exe, description='MyDesk 远程桌面',
                                    icon_path=dst_exe)
                except Exception as e:
                    print(f'桌面快捷方式失败: {e}')

            if self.create_startmenu:
                self.progress.emit(80, '正在创建开始菜单...')
                try:
                    sm_dir = start_menu_path()
                    lnk = os.path.join(sm_dir, 'MyDesk.lnk')
                    create_shortcut(lnk, dst_exe, description='MyDesk 远程桌面',
                                    icon_path=dst_exe)
                except Exception as e:
                    print(f'开始菜单失败: {e}')

            # 4. 优先安装 Windows 服务。服务负责跨登录/断线保活，
            # 当前用户登录后由服务拉起会话代理；失败时保留 Run 键兜底。
            service_ok = False
            if self.autostart:
                self.progress.emit(88, '正在安装远程桌面后台服务...')
                try:
                    from autostart import install_service
                    service_ok = install_service(dst_exe)
                    if service_ok:
                        subprocess.run([dst_exe, '--service', 'start'],
                                       capture_output=True, text=True)
                except Exception as e:
                    print(f'后台服务安装失败: {e}')
            if self.autostart and not service_ok:
                try:
                    import winreg
                    cmd = f'"{dst_exe}" --silent'
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                        r'Software\Microsoft\Windows\CurrentVersion\Run',
                                        0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, 'MyDesk', 0, winreg.REG_SZ, cmd)
                except Exception as e:
                    print(f'开机自启失败: {e}')

            # 5. 放行防火墙入站端口 (否则对方连不上且无任何提示)
            self.progress.emit(90, '正在配置防火墙...')
            try:
                from config import get_port
                fw = _add_firewall_rules(get_port())
                for name, okk, detail in fw:
                    if not okk:
                        print(f'防火墙规则 {name} 添加失败: {detail}')
                    else:
                        print(f'防火墙规则 {name} 已添加')
            except Exception as e:
                print(f'防火墙配置失败(需要管理员权限): {e}')

            # 6. 注册卸载信息
            self.progress.emit(94, '正在注册卸载信息...')
            try:
                register_uninstall(self.install_dir, dst_exe)
            except Exception as e:
                print(f'卸载注册失败(可能需要管理员): {e}')

            self.progress.emit(100, '安装完成!')
            self.finished_ok.emit(self.install_dir)

        except PermissionError:
            self.failed.emit('权限不足, 请右键以管理员身份运行安装包')
        except Exception as e:
            self.failed.emit(str(e))


# ============ 卸载工作线程 ============
class UninstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, install_dir):
        super().__init__()
        self.install_dir = install_dir

    def run(self):
        try:
            # 1. 关闭运行中的 MyDesk
            self.progress.emit(10, '正在关闭 MyDesk...')
            try:
                from autostart import uninstall_service
                uninstall_service(os.path.join(self.install_dir, 'MyDesk.exe'))
            except Exception as e:
                print(f'后台服务卸载失败: {e}')
            subprocess.run(['taskkill', '/F', '/IM', 'MyDesk.exe'],
                          capture_output=True, text=True)
            self.progress.emit(30, 'MyDesk 已关闭')

            # 2. 移除快捷方式
            self.progress.emit(40, '正在移除快捷方式...')
            try:
                lnk = os.path.join(desktop_path(), 'MyDesk.lnk')
                if os.path.exists(lnk):
                    os.remove(lnk)
            except Exception:
                pass
            try:
                sm_dir = start_menu_path()
                if os.path.isdir(sm_dir):
                    shutil.rmtree(sm_dir, ignore_errors=True)
            except Exception:
                pass
            self.progress.emit(60, '快捷方式已移除')

            # 3. 移除开机自启
            self.progress.emit(70, '正在移除开机自启...')
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r'Software\Microsoft\Windows\CurrentVersion\Run',
                                    0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, 'MyDesk')
            except Exception:
                pass

            # 4. 移除防火墙规则
            self.progress.emit(75, '正在移除防火墙规则...')
            _remove_firewall_rules()

            # 5. 移除卸载注册
            self.progress.emit(80, '正在清理注册表...')
            unregister_uninstall()

            # 6. 删除安装目录
            self.progress.emit(90, '正在删除程序文件...')
            if os.path.isdir(self.install_dir):
                shutil.rmtree(self.install_dir, ignore_errors=True)

            self.progress.emit(100, '卸载完成!')
            self.finished_ok.emit()

        except Exception as e:
            self.failed.emit(str(e))


# ============ 主窗口 ============
class InstallerWindow(QMainWindow):
    def __init__(self, is_uninstall=False, install_dir=None):
        super().__init__()
        self.is_uninstall = is_uninstall
        self.install_dir = install_dir or r'C:\Program Files\MyDesk'
        self.worker = None
        self.setWindowTitle('MyDesk 安装程序' if not is_uninstall else 'MyDesk 卸载程序')
        self.setWindowIcon(_create_app_icon())
        self.resize(560, 420)
        self._center()

        central = QWidget()
        self.setCentralWidget(central)
        self.root = QVBoxLayout(central)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)

        # 顶部横幅
        self._build_banner()

        # 内容区 (分页栈)
        self.stack = QStackedWidget()
        self.root.addWidget(self.stack, 1)

        if is_uninstall:
            self._build_uninstall_page()
        else:
            self._build_welcome_page()
            self._build_location_page()
            self._build_install_page()
            self._build_finish_page()

        self.stack.setCurrentIndex(0)

    def _center(self):
        geo = self.frameGeometry()
        center = QDesktopWidget().availableGeometry().center()
        geo.moveCenter(center)
        self.move(geo.topLeft())

    def _build_banner(self):
        banner = QWidget()
        banner.setFixedHeight(100)
        banner.setStyleSheet('''
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2563eb, stop:1 #7c3aed);
            }
        ''')
        h = QHBoxLayout(banner)
        h.setContentsMargins(28, 20, 28, 20)

        # 左侧文字
        title = QLabel('MyDesk')
        title.setStyleSheet('color:white; font-size:24px; font-weight:bold;')
        subtitle = QLabel('远程桌面控制  ·  个人自用版')
        subtitle.setStyleSheet('color:rgba(255,255,255,0.85); font-size:13px;')
        v = QVBoxLayout()
        v.setSpacing(2)
        v.addWidget(title)
        v.addWidget(subtitle)
        v.addStretch()
        h.addLayout(v)
        h.addStretch()

        # 右侧图标
        icon_label = QLabel()
        icon_pm = QPixmap(48, 48)
        icon_pm.fill(Qt.transparent)
        p = QPainter(icon_pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor('white'))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(4, 4, 40, 40, 10, 10)
        p.setBrush(QColor('#2563eb'))
        p.drawRoundedRect(10, 10, 28, 28, 6, 6)
        p.setPen(QColor('white'))
        p.setFont(QFont('Arial', 18, QFont.Bold))
        p.drawText(icon_pm.rect(), Qt.AlignCenter, 'M')
        p.end()
        icon_label.setPixmap(icon_pm)
        h.addWidget(icon_label)

        self.root.addWidget(banner)

    # ---------- 内容容器 ----------
    def _content(self, body_widget):
        """包装内容页: 白色卡片 + 内边距"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 24)

        card = QFrame()
        card.setStyleSheet('QFrame { background:white; border-radius:12px; }')
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 15))
        card.setGraphicsEffect(shadow)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 24, 24, 24)
        inner.addWidget(body_widget)

        outer.addWidget(card)
        return page

    # ---------- 页面1: 欢迎 ----------
    def _build_welcome_page(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(14)

        title = QLabel('欢迎使用 MyDesk 安装向导')
        title.setObjectName('Header')
        v.addWidget(title)

        desc = QLabel(
            '本向导将引导你完成 MyDesk 远程桌面软件的安装。\n\n'
            'MyDesk 是一款个人自用的 Windows 远程桌面控制软件，'
            '支持远程查看、键鼠控制、文件传输，\n'
            '基于 Tailscale 可实现外网任意位置远程连接。\n\n'
            '点击「下一步」继续。'
        )
        desc.setObjectName('Desc')
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton('取消')
        btn_cancel.setObjectName('Secondary')
        btn_cancel.clicked.connect(self.close)
        btn_next = QPushButton('下一步')
        btn_next.setObjectName('Primary')
        btn_next.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(10)
        btn_row.addWidget(btn_next)
        v.addLayout(btn_row)

        self.stack.addWidget(self._content(body))

    # ---------- 页面2: 选择安装位置 ----------
    def _build_location_page(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(14)

        title = QLabel('选择安装位置')
        title.setObjectName('Step')
        v.addWidget(title)

        desc = QLabel('选择 MyDesk 的安装目录 (建议保留默认):')
        desc.setObjectName('Desc')
        v.addWidget(desc)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit(self.install_dir)
        self.path_input.setObjectName('PathBox')
        path_row.addWidget(self.path_input)
        btn_browse = QPushButton('浏览...')
        btn_browse.setObjectName('Secondary')
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(btn_browse)
        v.addLayout(path_row)

        v.addSpacing(10)

        # 附加选项
        options_title = QLabel('附加选项:')
        options_title.setObjectName('Step')
        v.addWidget(options_title)

        self.chk_desktop = QCheckBox('创建桌面快捷方式')
        self.chk_desktop.setChecked(True)
        v.addWidget(self.chk_desktop)

        self.chk_startmenu = QCheckBox('创建开始菜单快捷方式')
        self.chk_startmenu.setChecked(True)
        v.addWidget(self.chk_startmenu)

        self.chk_autostart = QCheckBox('开机自动启动 MyDesk (后台静默运行)')
        self.chk_autostart.setChecked(True)
        v.addWidget(self.chk_autostart)

        v.addStretch()

        btn_row = QHBoxLayout()
        btn_back = QPushButton('上一步')
        btn_back.setObjectName('Secondary')
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        btn_row.addWidget(btn_back)
        btn_row.addStretch()
        btn_install = QPushButton('安装')
        btn_install.setObjectName('Primary')
        btn_install.clicked.connect(self._start_install)
        btn_row.addWidget(btn_install)
        v.addLayout(btn_row)

        self.stack.addWidget(self._content(body))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, '选择安装目录', self.path_input.text())
        if d:
            self.path_input.setText(d)

    # ---------- 页面3: 安装进度 ----------
    def _build_install_page(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(20)

        title = QLabel('正在安装...')
        title.setObjectName('Step')
        v.addWidget(title)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        v.addWidget(self.progress_bar)

        self.status_label = QLabel('准备安装...')
        self.status_label.setObjectName('Desc')
        v.addWidget(self.status_label)

        v.addStretch()

        # 隐藏的取消按钮 (安装中不可点)
        self.btn_cancel_install = QPushButton('取消')
        self.btn_cancel_install.setObjectName('Secondary')
        self.btn_cancel_install.setEnabled(False)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel_install)
        v.addLayout(btn_row)

        self.stack.addWidget(self._content(body))

    # ---------- 页面4: 完成 ----------
    def _build_finish_page(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(14)

        title = QLabel('安装完成!')
        title.setObjectName('Header')
        v.addWidget(title)

        self.finish_label = QLabel(
            'MyDesk 已成功安装到你的电脑。\n\n'
            '你可以通过桌面快捷方式或开始菜单启动 MyDesk。\n'
            '关闭窗口后, 安装程序将退出。\n\n'
            '💡 提示: 首次使用请查看主窗口右侧的 Tailscale 提示\n'
            '    以启用外网远程连接功能。'
        )
        self.finish_label.setObjectName('Desc')
        self.finish_label.setWordWrap(True)
        v.addWidget(self.finish_label)
        v.addStretch()

        self.chk_launch = QCheckBox('立即启动 MyDesk')
        self.chk_launch.setChecked(True)
        v.addWidget(self.chk_launch)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_finish = QPushButton('完成')
        btn_finish.setObjectName('Primary')
        btn_finish.clicked.connect(self._finish)
        btn_row.addWidget(btn_finish)
        v.addLayout(btn_row)

        self.stack.addWidget(self._content(body))

    # ---------- 卸载页面 ----------
    def _build_uninstall_page(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(14)

        title = QLabel('卸载 MyDesk')
        title.setObjectName('Header')
        v.addWidget(title)

        desc = QLabel(
            f'即将从你的电脑卸载 MyDesk 远程桌面软件。\n\n'
            f'卸载将删除:\n'
            f'  • 程序文件\n'
            f'  • 桌面和开始菜单快捷方式\n'
            f'  • 开机自启项\n'
            f'  • 卸载注册信息\n\n'
            f'注意: 你的配置文件 (~/.mydesk) 将保留。\n\n'
            f'点击「卸载」开始。'
        )
        desc.setObjectName('Desc')
        desc.setWordWrap(True)
        v.addWidget(desc)
        v.addStretch()

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton('取消')
        btn_cancel.setObjectName('Secondary')
        btn_cancel.clicked.connect(self.close)
        btn_uninstall = QPushButton('卸载')
        btn_uninstall.setObjectName('Primary')
        btn_uninstall.setStyleSheet('background:#ef4444;')
        btn_uninstall.clicked.connect(self._start_uninstall)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(10)
        btn_row.addWidget(btn_uninstall)
        v.addLayout(btn_row)

        # 进度页
        self.uninstall_body = QWidget()
        uv = QVBoxLayout(self.uninstall_body)
        uv.setSpacing(20)
        uv.addWidget(QLabel('正在卸载...'))

        self.uninstall_progress = QProgressBar()
        self.uninstall_progress.setValue(0)
        uv.addWidget(self.uninstall_progress)

        self.uninstall_status = QLabel('准备卸载...')
        self.uninstall_status.setObjectName('Desc')
        uv.addWidget(self.uninstall_status)
        uv.addStretch()

        self.stack.addWidget(self._content(body))
        self.stack.addWidget(self._content(self.uninstall_body))

    # ---------- 操作 ----------
    def _start_install(self):
        self.install_dir = self.path_input.text().strip()
        if not self.install_dir:
            QMessageBox.warning(self, '提示', '请输入安装目录')
            return
        # 切到安装页
        self.stack.setCurrentIndex(2)
        # 启动工作线程
        self.worker = InstallWorker(
            self.install_dir,
            self.chk_desktop.isChecked(),
            self.chk_startmenu.isChecked(),
            self.chk_autostart.isChecked(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_install_done)
        self.worker.failed.connect(self._on_install_failed)
        self.worker.start()

    def _on_progress(self, percent, text):
        self.progress_bar.setValue(percent)
        self.status_label.setText(text)

    def _on_install_done(self, install_dir):
        self.install_dir = install_dir
        self.stack.setCurrentIndex(3)

    def _on_install_failed(self, err):
        QMessageBox.critical(self, '安装失败', err)
        self.stack.setCurrentIndex(1)

    def _finish(self):
        if self.chk_launch.isChecked():
            exe = os.path.join(self.install_dir, 'MyDesk.exe')
            if os.path.exists(exe):
                subprocess.Popen([exe])
        self.close()

    # ---------- 卸载 ----------
    def _start_uninstall(self):
        self.stack.setCurrentIndex(1)
        self.worker = UninstallWorker(self.install_dir)
        self.worker.progress.connect(self._on_uninstall_progress)
        self.worker.finished_ok.connect(self._on_uninstall_done)
        self.worker.failed.connect(self._on_uninstall_failed)
        self.worker.start()

    def _on_uninstall_progress(self, percent, text):
        self.uninstall_progress.setValue(percent)
        self.uninstall_status.setText(text)

    def _on_uninstall_done(self):
        QMessageBox.information(self, '卸载完成',
                                'MyDesk 已成功卸载。')
        self.close()

    def _on_uninstall_failed(self, err):
        QMessageBox.critical(self, '卸载失败', err)


def main():
    args = sys.argv[1:]

    # 卸载模式
    if '--uninstall' in args:
        # 从注册表读取安装路径
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'Software\Microsoft\Windows\CurrentVersion\Uninstall\MyDesk',
                                0, winreg.KEY_READ) as k:
                install_loc, _ = winreg.QueryValueEx(k, 'InstallLocation')
        except Exception:
            install_loc = r'C:\Program Files\MyDesk'
        app = QApplication(sys.argv)
        app.setStyleSheet(INSTALLER_QSS)
        win = InstallerWindow(is_uninstall=True, install_dir=install_loc)
        win.show()
        sys.exit(app.exec_())

    # 安装模式 (默认)
    app = QApplication(sys.argv)
    app.setStyleSheet(INSTALLER_QSS)
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
