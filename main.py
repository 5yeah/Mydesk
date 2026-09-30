# -*- coding: utf-8 -*-
"""
MyDesk - 主入口 / 应用编排层

只负责: 创建 QApplication、装配 MainWindow + SystemTray、连接信号、
设备发现与连接调度、被控端启动、事件循环。
所有界面细节下沉到 gui 包 (gui.main_window / gui.viewer_window / gui.tray)。
日志统一走 gui.logger (crash_log / debug_log)。
"""

import faulthandler
import sys
import os
import time
import socket
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _setup_faulthandler():
    """原生崩溃(segfault)堆栈同时写 stderr 与磁盘文件, 即使窗口闪退也不丢失。
    文件位于用户目录 MyDesk_Logs/faulthandler.log, 便于事后排查。"""
    try:
        import datetime
        log_dir = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
        os.makedirs(log_dir, exist_ok=True)
        fh_path = os.path.join(log_dir, 'faulthandler.log')
        # 'a' 追加 + 行缓冲, 崩溃瞬间也能落盘
        fh_file = open(fh_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
        faulthandler.enable(fh_file)
        fh_file.write(
            f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] faulthandler 已启用, 日志文件={fh_path}\n"
        )
    except Exception as e:
        # 兜底: 至少保证 stderr 有输出
        faulthandler.enable()
        sys.stderr.write(f"[MyDesk] faulthandler 文件初始化失败, 回退 stderr: {e}\n")


_setup_faulthandler()

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt, QTimer

from gui import crash_log, debug_log
from gui.main_window import MainWindow
from gui.viewer_window import ViewerWindow, FileTransferDialog
from gui.tray import SystemTray

from controlled import Controlled
from controller import Controller
from config import get_port, get_device_id, get_discovery_port


# ---------- 全局崩溃防护: 任何未捕获异常都弹窗+写日志, 绝不再"闪退" ----------
def _global_excepthook(exc_type, exc_value, exc_tb):
    import traceback
    tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        crash_log(f'[全局未捕获异常]:\n{tb}')
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f'MyDesk 发生未捕获异常(已记录到日志):\n\n{tb[-1500:]}',
            'MyDesk Error', 0x10)
    except Exception:
        pass


sys.excepthook = _global_excepthook


class SafeApplication(QApplication):
    """重写 notify, 拦截 GUI 线程(槽函数/事件)中未捕获的 Python 异常,
    记录日志并弹窗, 阻止其导致整个进程静默闪退。"""
    _err_shown = False

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            try:
                crash_log(f'[SafeApplication] 拦截到界面异常(程序继续运行):\n{tb}')
            except Exception:
                pass
            if not SafeApplication._err_shown:
                SafeApplication._err_shown = True
                try:
                    QMessageBox.critical(
                        None, 'MyDesk 运行错误',
                        f'捕获到一个界面异常, 已记录到日志, 程序继续运行:\n\n{tb[-1500:]}')
                except Exception:
                    pass
                SafeApplication._err_shown = False
            return False


# 最早阶段日志 (跟踪启动流程, 便于崩溃定位)
try:
    crash_log('main.py 开始导入模块')
except Exception:
    pass


class MyDeskApplication:
    def __init__(self):
        try:
            crash_log('MyDeskApplication.__init__ 开始')
            crash_log('  Step 1: 创建 QApplication')
            # 高 DPI 缩放: 让 Qt 把本进程设为 Per-Monitor DPI 感知。
            # 这样远程鼠标坐标基于物理像素, 与 mss 捕获的物理分辨率一致,
            # 避免高 DPI(缩放)系统下远程鼠标错位/点不中("无法操作")。
            try:
                QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
                QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
            except Exception:
                pass
            self.app = SafeApplication(sys.argv)
            crash_log('  Step 2: 设置样式')
            self.app.setStyle('Fusion')
            # 关闭最后一个窗口是否退出: 有托盘时保持后台(False), 无托盘时直接退出(True)
            self.app.setQuitOnLastWindowClosed(False)

            crash_log('  Step 3: 创建 MainWindow')
            self.main_window = MainWindow()
            crash_log('  Step 3.1: MainWindow 创建完成')

            self.controlled = None
            self.current_session = None
            self.current_controller = None
            self.current_file_transfer = None
            self.tray = None

            crash_log('  Step 4: 获取端口配置')
            self._resolved_port = get_port()  # 解析出的端口号

            crash_log('  Step 5: 设置系统托盘')
            self.tray = SystemTray(
                self.app,
                self.main_window,
                on_quick_connect=self.connect_to_device,
                on_quit=self.quit,
            )
            # 无托盘会话(远程桌面/服务器): 关闭窗口即退出, 避免无窗口无托盘的"僵尸"进程
            self._tray_available = getattr(self.tray, '_tray_ok', False)
            self.app.setQuitOnLastWindowClosed(not self._tray_available)

            # 连接信号
            crash_log('  Step 6: 连接信号')
            self.main_window.connect_requested.connect(self.on_connect_requested)

            # 用于跨线程创建窗口
            self._pending_controller = None
            self._pending_mode = None
            self._poll_timer = QTimer(self.app)  # 必须指定父对象，否则跨线程GC会触发Qt警告
            self._poll_timer.setInterval(100)
            self._poll_timer.timeout.connect(self._poll_pending_session)
            self._poll_timer.start()

            debug_log('[MyDesk] 应用初始化完成')
            crash_log('MyDeskApplication.__init__ 完成')
        except Exception as e:
            crash_log(f'MyDeskApplication.__init__ CRASH: {e}\n{traceback.format_exc()}')
            raise

    # ---- 窗口显隐 (委托给托盘, 保持单一入口) ----
    def show_main_window(self):
        if self.tray:
            self.tray.show_main_window()
        else:
            self.main_window.show()
            self.main_window.activateWindow()
            self.main_window.raise_()

    def hide_main_window(self):
        if self.tray:
            self.tray.hide_main_window()
        else:
            self.main_window.hide()

    # ---- 连接调度 ----
    def on_connect_requested(self, device_id, mode, password):
        debug_log(f'[MyDesk] 连接请求: {device_id}, mode={mode}')
        # 在后台线程中连接，避免阻塞UI
        def connect_worker():
            try:
                self.connect_to_device(device_id, password, mode)
            except Exception as e:
                debug_log(f'[MyDesk] 连接工作线程异常: {e}')
        t = threading.Thread(target=connect_worker, daemon=True)
        t.start()

    def connect_to_device(self, device_id, password, mode='control'):
        try:
            target_ip = None
            target_port = get_port()

            # 判断输入是 IP 还是设备ID
            ip_parts = device_id.split('.')
            if len(ip_parts) == 4:
                try:
                    for p in ip_parts:
                        int(p)
                    # 是纯IP地址
                    target_ip = device_id
                    debug_log(f'[MyDesk] 直接使用IP连接: {target_ip}:{target_port}')
                except ValueError:
                    pass

            # 处理 IP:端口 格式
            if not target_ip and ':' in device_id:
                parts = device_id.split(':', 1)
                try:
                    ip_test = parts[0].split('.')
                    if len(ip_test) == 4 and all(p.isdigit() for p in ip_test):
                        target_ip = parts[0]
                        target_port = int(parts[1])
                        debug_log(f'[MyDesk] 使用IP:端口连接: {target_ip}:{target_port}')
                except (ValueError, IndexError):
                    pass

            # 如果不是IP，则通过设备ID查找
            if not target_ip:
                target_ip = self._find_device_ip(device_id)
                if self._resolved_port:
                    target_port = self._resolved_port

            if not target_ip:
                QTimer.singleShot(0, lambda: QMessageBox.warning(None, '连接失败',
                    f'无法找到设备: {device_id}\n\n请检查:\n'
                    f'1. 设备ID是否正确\n'
                    f'2. 对方设备是否已启动MyDesk\n'
                    f'3. 两台设备是否在同一局域网\n'
                    f'4. 尝试使用IP地址直接连接'))
                return

            debug_log(f'[MyDesk] 连接到 {target_ip}:{target_port}')

            controller = Controller()
            try:
                if not controller.connect(target_ip, target_port, password):
                    QTimer.singleShot(0, lambda: QMessageBox.critical(None, '连接失败', '无法连接到远程设备'))
                    return
            except Exception as e:
                err_msg = str(e)
                QTimer.singleShot(0, lambda: QMessageBox.critical(None, '连接失败', f'连接出错: {err_msg}'))
                debug_log(f'[MyDesk] 连接异常: {e}')
                return

            self.current_controller = controller

            # 连接成功后更新设备IP
            self.main_window._update_device_ip(device_id, f'{target_ip}:{target_port}')

            # 不在后台线程创建Qt窗口，存起来让主线程定时器处理
            self._pending_controller = controller
            self._pending_mode = mode

        except Exception as e:
            debug_log(f'[MyDesk] 连接异常: {e}')
            import traceback as _tb
            _tb.print_exc()
            err_msg = str(e)
            QTimer.singleShot(0, lambda: QMessageBox.critical(None, '连接失败', f'连接出错: {err_msg}'))

    def _find_device_ip(self, device_id):
        """通过设备ID查找IP: UDP广播(快速) + TCP子网扫描(轻量回退)"""
        port = get_port()

        # 策略1: UDP广播 (单端口, 快速)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            # 广播到固定的发现端口(与被控端 TCP 端口解耦, 端口漂移也能发现)
            sock.sendto(device_id.encode('utf-8'), ('255.255.255.255', get_discovery_port()))
            try:
                data, addr = sock.recvfrom(1024)
                resp = data.decode('utf-8', errors='ignore')
                if '|' in resp:
                    resp_id, resp_port = resp.split('|', 1)
                    if resp_id.upper() == device_id.upper():
                        sock.close()
                        self._resolved_port = int(resp_port)
                        debug_log(f'[MyDesk] UDP广播找到设备 {device_id} at {addr[0]}:{self._resolved_port}')
                        return addr[0]
                elif resp.strip().upper() == device_id.upper():
                    sock.close()
                    debug_log(f'[MyDesk] UDP广播找到设备 {device_id} at {addr[0]}')
                    return addr[0]
            except socket.timeout:
                pass
            sock.close()
        except Exception as e:
            debug_log(f'[MyDesk] UDP广播异常: {e}')

        # 策略2: TCP子网扫描(轻量, 最多10并发)
        return self._tcp_scan_find(device_id)

    def _tcp_scan_find(self, device_id):
        """TCP子网扫描: 最多10并发, 总共10秒超时"""
        try:
            from network import get_lan_ip
        except ImportError:
            debug_log('[MyDesk] network.get_lan_ip 不可用')
            return None

        local_ip = get_lan_ip()
        parts = local_ip.split('.')
        if len(parts) != 4:
            return None

        base = f'{parts[0]}.{parts[1]}.{parts[2]}'
        debug_log(f'[MyDesk] TCP轻量扫描子网: {base}.1-254')

        port = get_port()
        found = {'ip': None, 'port': None}
        found_lock = threading.Lock()
        done_event = threading.Event()

        def try_query(ip):
            if done_event.is_set():
                return
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.3)
                s.connect((ip, port))
                from protocol import send_msg, recv_msg, MSG_CONTROL
                send_msg(s, MSG_CONTROL, b'DEVICE_ID_QUERY')
                s.settimeout(0.5)
                try:
                    msg = recv_msg(s)
                    if msg and msg[0] == MSG_CONTROL:
                        resp_id = msg[1].decode('utf-8', errors='ignore')
                        if resp_id.upper() == device_id.upper():
                            with found_lock:
                                found['ip'] = ip
                                found['port'] = port
                            done_event.set()
                except socket.timeout:
                    pass
                s.close()
            except Exception:
                pass

        # 最多10个并发线程, 分批扫描
        ip_list = [f'{base}.{i}' for i in range(1, 255) if f'{base}.{i}' != local_ip]
        batch_size = 10

        for i in range(0, len(ip_list), batch_size):
            if done_event.is_set():
                break
            batch = ip_list[i:i + batch_size]
            threads = []
            for ip in batch:
                t = threading.Thread(target=try_query, args=(ip,), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=2.0)

        if found['ip']:
            self._resolved_port = found['port']
            debug_log(f'[MyDesk] TCP扫描找到设备 at {found["ip"]}:{found["port"]}')
            return found['ip']

        debug_log('[MyDesk] TCP扫描未找到设备')
        return None

    def _open_session(self, controller):
        try:
            debug_log('[MyDesk] 开始创建 ViewerWindow')
            self.current_session = ViewerWindow(controller)
            self.current_session.setWindowTitle('MyDesk - 远程会话')
            self.current_session.resize(1280, 800)
            self.current_session.show()
            debug_log('[MyDesk] ViewerWindow.show() 完成')

            self.current_session.receiver.disconnected.connect(self._on_session_disconnected)
            self.main_window.start_latency_monitor()
            debug_log('[MyDesk] 会话已打开')
        except Exception as e:
            debug_log(f'[MyDesk] 打开会话异常: {e}')
            import traceback as _tb
            _tb.print_exc()
            QMessageBox.critical(None, '错误', f'打开会话失败: {e}')

    def _poll_pending_session(self):
        """主线程定时检查是否有待创建的会话"""
        if self._pending_controller is not None:
            controller = self._pending_controller
            mode = self._pending_mode
            self._pending_controller = None
            self._pending_mode = None
            self._poll_timer.stop()

            debug_log(f'[MyDesk] 主线程创建会话, mode={mode}')
            if mode == 'file':
                self._open_file_transfer(controller)
            else:
                self._open_session(controller)

    def _open_file_transfer(self, controller):
        try:
            recv_dir = os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')
            os.makedirs(recv_dir, exist_ok=True)
            # 同步controller的接收目录
            controller.file_transfer.recv_dir = recv_dir
            self.current_file_transfer = FileTransferDialog(controller, recv_dir)
            # 上传/下载的发起包含 socket 写操作, 放到后台线程避免阻塞 GUI
            self.current_file_transfer.file_upload_requested.connect(
                lambda p: threading.Thread(target=controller.upload_file, args=(p,), daemon=True).start())
            self.current_file_transfer.file_download_requested.connect(
                lambda n: threading.Thread(target=controller.download_file, args=(n,), daemon=True).start())
            self.current_file_transfer.resize(900, 600)
            self.current_file_transfer.show()
            debug_log('[MyDesk] 文件传输窗口已显示')
        except Exception as e:
            debug_log(f'[MyDesk] 文件传输异常: {e}')
            import traceback as _tb
            _tb.print_exc()
            QMessageBox.critical(None, '错误', f'文件传输失败: {e}')

    def _on_session_disconnected(self):
        debug_log('[MyDesk] 会话已断开')
        QTimer.singleShot(0, self._cleanup_session)

    def _cleanup_session(self):
        self.main_window.stop_latency_monitor()
        if self.current_controller:
            self.current_controller.disconnect()
            self.current_controller = None
        self.current_session = None

    def start_controlled(self):
        try:
            self.controlled = Controlled()
            # controlled.start() 内部有 while True 阻塞循环 (server.accept()),
            # 必须放在后台线程中运行, 否则会阻塞主线程导致窗口无法显示
            t = threading.Thread(target=self.controlled.start, daemon=True)
            t.start()
            debug_log(f'[MyDesk] 被控端已启动, 设备ID: {get_device_id()}')
        except Exception as e:
            debug_log(f'[MyDesk] 被控端启动失败: {e}')
            import traceback as _tb
            _tb.print_exc()

    def run(self):
        try:
            self.start_controlled()
            self.show_main_window()
            return self.app.exec_()
        except Exception as e:
            crash_log(f'run() 异常: {e}\n{traceback.format_exc()}')
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f'MyDesk 运行异常:\n\n{e}', 'MyDesk Error', 0x10)
            except Exception:
                pass
            return 1

    def quit(self):
        if self.controlled:
            try:
                self.controlled.stop()
            except Exception:
                pass
        if self.current_controller:
            try:
                self.current_controller.disconnect()
            except Exception:
                pass
        self.app.quit()


def main():
    try:
        crash_log('main() 开始运行')
        app = MyDeskApplication()
        crash_log('MyDeskApplication 创建完成, 调用 run()')
        app.run()
    except Exception as e:
        crash_log(f'CRASH: {e}\n{traceback.format_exc()}')
        # 尝试弹窗显示错误
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f'MyDesk 启动失败:\n\n{e}', 'MyDesk Error', 0x10)
        except Exception:
            pass
    sys.exit(0)


if __name__ == '__main__':
    main()
