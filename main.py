# -*- coding: utf-8 -*-
"""
MyDesk - 主入口 / 应用编排层

只负责: 创建 QApplication、装配 MainWindow + SystemTray、连接信号、
设备发现与连接调度、被控端启动、事件循环。
所有界面细节下沉到 gui 包 (gui.main_window / gui.viewer_window / gui.tray)。
日志统一走 gui.logger (crash_log / debug_log)。

线程模型 (本版重点修正):
  网络连接在后台线程完成, 结果通过 Qt 信号投递回 GUI 线程 ——
  绝不在非 GUI 线程里创建窗口或调用 QTimer.singleShot(没有事件循环, 定时器永不触发,
  这正是旧版"连接失败没有任何提示""第二次连接打不开窗口"的根因)。
"""
import faulthandler
import sys
import os
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
from PyQt5.QtCore import Qt, QTimer, QObject, pyqtSignal

from gui import crash_log, debug_log
from gui.main_window import MainWindow
from gui.viewer_window import ViewerWindow, FileTransferDialog
from gui.tray import SystemTray

from controlled import Controlled
from controller import Controller
from config import get_port, get_device_id, get_discovery_port, get_setting


# ============================================================================
# 崩溃处理
#
# 旧实现在 sys.excepthook / QApplication.notify 里直接弹原生 MessageBoxW。
# 那是一个 input-synchronous 调用, 在 Qt 正在派发事件时弹模态框会重入消息循环,
# faulthandler.log 里已记录到由此引发的 access violation —— 异常处理器本身
# 成了新的崩溃源。现在改为: 只记录日志 + 入队一条待显示消息, 由主线程
# 在事件循环的空档用 QTimer 弹 Qt 自己的对话框。
# ============================================================================
_pending_errors = []
_pending_errors_lock = threading.Lock()
MAX_PENDING_ERRORS = 5


def _queue_error(title, text):
    """线程安全地把一条错误加入待显示队列 (绝不在异常处理器里弹窗)"""
    with _pending_errors_lock:
        if len(_pending_errors) < MAX_PENDING_ERRORS:
            _pending_errors.append((title, text))


def _drain_errors():
    """在主线程里显示待显示的错误"""
    while True:
        with _pending_errors_lock:
            if not _pending_errors:
                return
            title, text = _pending_errors.pop(0)
        try:
            QMessageBox.critical(None, title, text)
        except Exception:
            pass


def _global_excepthook(exc_type, exc_value, exc_tb):
    tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        crash_log(f'[全局未捕获异常]:\n{tb}')
    except Exception:
        pass
    # 只入队, 不在这里弹窗
    _queue_error('MyDesk 运行错误', f'发生未捕获异常(已记录到日志):\n\n{tb[-1500:]}')


sys.excepthook = _global_excepthook


class SafeApplication(QApplication):
    """重写 notify, 拦截 GUI 线程(槽函数/事件)中未捕获的 Python 异常,
    记录日志并延后弹窗, 阻止其导致整个进程静默闪退。"""
    _err_shown = False

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            tb = traceback.format_exc()
            try:
                crash_log(f'[SafeApplication] 拦截到界面异常(程序继续运行):\n{tb}')
            except Exception:
                pass
            if not SafeApplication._err_shown:
                SafeApplication._err_shown = True
                try:
                    # 用定时器把弹窗推迟到本次事件派发结束之后, 避免重入
                    QTimer.singleShot(0, lambda: self._show_error(tb))
                except Exception:
                    pass
            return False

    @staticmethod
    def _show_error(tb):
        SafeApplication._err_shown = False
        try:
            QMessageBox.critical(
                None, 'MyDesk 运行错误',
                f'捕获到一个界面异常, 已记录到日志, 程序继续运行:\n\n{tb[-1500:]}')
        except Exception:
            pass


# 最早阶段日志 (跟踪启动流程, 便于崩溃定位)
try:
    debug_log('main.py 开始导入模块', 'startup')
except Exception:
    pass


# ============================================================================
# 单实例保护
# ============================================================================
_mutex_handle = None


def acquire_single_instance():
    """确保只有一个 MyDesk 实例在运行。

    多开的后果: 两个实例抢同一批端口(TCP 与 UDP 发现端口还会撞车)、共享同一份
    config.json 得到相同设备ID, 导致 UDP 发现应答来源不确定。
    """
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        ERROR_ALREADY_EXISTS = 183
        _mutex_handle = kernel32.CreateMutexW(None, False, 'Global\\MyDesk_SingleInstance')
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True  # 无法判断时不阻止启动


def notify_already_running():
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, 'MyDesk 已经在运行了。\n\n请在系统托盘中找到它的图标（双击打开主界面）。',
            'MyDesk', 0x40)
    except Exception:
        pass


class _AppBridge(QObject):
    """后台线程 -> GUI 线程的投递通道

    用信号而不是 QTimer.singleShot: 信号走队列连接由 Qt 自动切回 GUI 线程,
    而 QTimer 在普通 threading.Thread 里创建后永远不会触发。
    """
    session_ready = pyqtSignal(object, str, int)  # controller, mode, 连接代
    connect_failed = pyqtSignal(int, str, str)    # 连接代, 标题, 消息
    status_message = pyqtSignal(str)
    # 连接断开(可能来自接收线程); 携带断开的 controller 用于会话隔离
    session_closed = pyqtSignal(object)


class MyDeskApplication:
    def __init__(self):
        try:
            debug_log('MyDeskApplication.__init__ 开始', 'startup')
            # 高 DPI 缩放: 让 Qt 把本进程设为 Per-Monitor DPI 感知。
            # 这样远程鼠标坐标基于物理像素, 与 mss 捕获的物理分辨率一致,
            # 避免高 DPI(缩放)系统下远程鼠标错位/点不中("无法操作")。
            try:
                QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
                QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
            except Exception:
                pass
            self.app = SafeApplication(sys.argv)
            self.app.setStyle('Fusion')
            # 关闭最后一个窗口是否退出: 有托盘时保持后台(False), 无托盘时直接退出(True)
            self.app.setQuitOnLastWindowClosed(False)

            self.main_window = MainWindow()

            self.controlled = None
            self.current_session = None
            self.current_controller = None
            self.current_file_transfer = None
            self.tray = None
            self._closing = False
            # 连接互斥与代际: 同一时刻只允许一个连接在跑,
            # 过期连接的结果不得覆盖/杀死新连接 (二次连接卡"连接中"的根因之一)
            self._connecting = False
            self._connect_gen = 0

            # 后台线程 -> GUI 线程 的信号桥
            self._bridge = _AppBridge()
            self._bridge.session_ready.connect(self._on_session_ready)
            self._bridge.connect_failed.connect(self._on_connect_failed)
            self._bridge.status_message.connect(self._on_status_message)
            self._bridge.session_closed.connect(self._cleanup_session)

            self._resolved_port = get_port()  # 解析出的端口号

            self.tray = SystemTray(
                self.app,
                self.main_window,
                # 托盘快速连接也必须走 on_connect_requested:
                # 旧版直接调 connect_to_device 会在 GUI 线程跑整个扫描+连接, 冻结界面
                on_quick_connect=lambda d, p, m='control': self.on_connect_requested(d, m, p),
                on_quit=self.quit,
            )
            # 无托盘会话(远程桌面/服务器): 关闭窗口即退出, 避免无窗口无托盘的"僵尸"进程
            self._tray_available = getattr(self.tray, '_tray_ok', False)
            self.app.setQuitOnLastWindowClosed(not self._tray_available)

            self.main_window.connect_requested.connect(self.on_connect_requested)

            # 让主窗口能拿到真实 RTT (由 Controller 心跳测量)
            self.main_window.set_rtt_source(self._current_rtt)

            # 延迟显示待显示的错误 (每 500ms 检查一次, 只在主线程操作)
            self._error_timer = QTimer(self.app)
            self._error_timer.setInterval(500)
            self._error_timer.timeout.connect(self._poll_errors)
            self._error_timer.start()

            debug_log('[MyDesk] 应用初始化完成')
            debug_log('MyDeskApplication.__init__ 完成', 'startup')
        except Exception as e:
            crash_log(f'MyDeskApplication.__init__ CRASH: {e}\n{traceback.format_exc()}')
            raise

    def _poll_errors(self):
        _drain_errors()

    def _current_rtt(self):
        """供主窗口读取当前会话的真实 RTT (毫秒); 无会话返回 None"""
        ctrl = self.current_controller
        if ctrl is not None and ctrl.connected and ctrl.last_rtt is not None:
            return ctrl.last_rtt * 1000.0
        return None

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
        # 连接单飞: 已有连接在跑时忽略重复请求
        # (主页按钮禁用挡不住设备详情页/历史记录/托盘这些旁路入口)
        if self._connecting:
            debug_log(f'[MyDesk] 已有连接进行中, 忽略重复请求: {device_id}')
            return
        self._connecting = True
        self._connect_gen += 1
        gen = self._connect_gen
        debug_log(f'[MyDesk] 连接请求 (gen={gen}): {device_id}, mode={mode}')

        # 连接看门狗: 无论卡在设备发现/认证/信号投递哪一步,
        # 到点强制作废本次连接并复位按钮 —— 按钮绝不会永久停在"连接中"
        QTimer.singleShot(45000, lambda g=gen: self._on_connect_timeout(g))

        # 在后台线程中连接，避免阻塞UI
        def connect_worker():
            try:
                self.connect_to_device(device_id, password, mode, gen)
            except Exception as e:
                debug_log(f'[MyDesk] 连接工作线程异常: {e}\n{traceback.format_exc()}')
                self._bridge.connect_failed.emit(gen, '连接失败', f'连接出错: {e}')
        threading.Thread(target=connect_worker, daemon=True).start()

    def _on_connect_timeout(self, gen):
        """看门狗到点: 本次连接仍无结果就作废它并复位界面 (GUI 线程)"""
        if gen != self._connect_gen or not self._connecting:
            return
        debug_log(f'[MyDesk] 连接看门狗超时 (gen={gen}), 作废本次连接')
        self._connect_gen += 1        # 让仍在跑的 worker 结果全部失效
        self._connecting = False
        try:
            self.main_window.reset_connect_button()
            self.main_window.set_connect_status('⚠️ 连接超时', error=True)
        except Exception:
            pass
        QMessageBox.critical(
            None, '连接超时',
            '连接超时: 未能在限定时间内建立连接。\n\n'
            '请检查网络, 或确认对方设备已启动并在线后重试。')

    def _on_status_message(self, text):
        try:
            self.main_window.set_connect_status(text)
        except Exception:
            pass

    def _on_connect_failed(self, gen, title, message):
        """在 GUI 线程弹出连接失败提示"""
        if gen != self._connect_gen:
            debug_log(f'[MyDesk] 丢弃过期连接失败结果 (gen={gen})')
            return
        self._connecting = False
        debug_log(f'[MyDesk] 连接失败 (gen={gen}): {title} - {message[:300]}')
        try:
            self.main_window.reset_connect_button()
        except Exception:
            pass
        QMessageBox.critical(None, title, message)

    def _on_session_ready(self, controller, mode, gen):
        """在 GUI 线程创建会话窗口 (唯一允许创建窗口的地方)"""
        if gen != self._connect_gen:
            # 过期连接(已被超时/新请求作废): 主动断开, 不许它打开窗口
            debug_log(f'[MyDesk] 丢弃过期会话 (gen={gen}), 已断开')
            try:
                controller.disconnect()
            except Exception:
                pass
            return
        self._connecting = False
        debug_log(f'[MyDesk] 连接成功 (gen={gen}), mode={mode}')
        try:
            self.main_window.reset_connect_button()
            if mode == 'file':
                self._open_file_transfer(controller)
            else:
                self._open_session(controller, mode)
        except Exception as e:
            debug_log(f'[MyDesk] 打开会话异常: {e}\n{traceback.format_exc()}')
            QMessageBox.critical(None, '错误', f'打开会话失败: {e}')

    def connect_to_device(self, device_id, password, mode='control', gen=None):
        """在后台线程执行: 解析地址 -> 建立连接 -> 把结果 emit 回 GUI 线程"""
        if gen is None:
            gen = self._connect_gen

        def emit_failed(title, msg):
            self._bridge.connect_failed.emit(gen, title, msg)

        def emit_status(text):
            # 过期连接不许再改界面状态
            if gen == self._connect_gen:
                self._bridge.status_message.emit(text)

        try:
            direct_ip = None
            target_port = get_port()

            # 判断输入是 IP 还是设备ID
            ip_parts = device_id.split('.')
            if len(ip_parts) == 4:
                try:
                    for p in ip_parts:
                        int(p)
                    direct_ip = device_id
                    debug_log(f'[MyDesk] 直接使用IP连接: {direct_ip}:{target_port}')
                except ValueError:
                    pass

            # 处理 IP:端口 格式
            if not direct_ip and ':' in device_id:
                parts = device_id.split(':', 1)
                try:
                    ip_test = parts[0].split('.')
                    if len(ip_test) == 4 and all(p.isdigit() for p in ip_test):
                        direct_ip = parts[0]
                        target_port = int(parts[1])
                        debug_log(f'[MyDesk] 使用IP:端口连接: {direct_ip}:{target_port}')
                except (ValueError, IndexError):
                    pass

            # 构建候选地址列表:
            #   IP输入 -> 只连该地址
            #   设备ID -> 先试"上次连接成功的缓存地址"(秒连, 跳过发现),
            #             缓存不通才跑 UDP广播+TCP子网扫描(3-10秒)
            attempts = []
            if direct_ip:
                attempts.append((direct_ip, target_port, 'IP直连'))
            else:
                cached = None
                try:
                    cached = self.main_window.get_device_cached_addr(device_id)
                except Exception:
                    cached = None
                if cached:
                    try:
                        c_ip, c_port = cached.rsplit(':', 1)
                        attempts.append((c_ip, int(c_port), '缓存地址'))
                        debug_log(f'[MyDesk] 使用缓存地址快速重连: {cached}')
                    except ValueError:
                        pass
                attempts.append(None)   # 占位: 缓存失败后才执行设备发现

            controller = None
            target_ip = None
            last_err = None
            for item in attempts:
                if item is None:
                    emit_status('正在查找设备...')
                    found_ip = self._find_device_ip(device_id)
                    if not found_ip:
                        continue
                    item = (found_ip, self._resolved_port or target_port, '设备发现')
                ip, port, src = item
                debug_log(f'[MyDesk] 连接到 {ip}:{port} (来源: {src})')
                emit_status(f'正在连接 {ip}:{port} ...')
                c = Controller()
                try:
                    c.connect(ip, port, password)
                    controller = c
                    target_ip, target_port = ip, port
                    break
                except Exception as e:
                    last_err = e
                    debug_log(f'[MyDesk] 连接 {ip}:{port} 失败({src}): {e}')
                    try:
                        c.disconnect()
                    except Exception:
                        pass

            if controller is None:
                if last_err is None:
                    emit_failed(
                        '连接失败',
                        f'无法找到设备: {device_id}\n\n请检查:\n'
                        f'1. 设备ID是否正确\n'
                        f'2. 对方设备是否已启动MyDesk\n'
                        f'3. 两台设备是否在同一局域网\n'
                        f'4. 尝试使用IP地址直接连接')
                else:
                    emit_failed('连接失败', f'连接出错: {last_err}')
                return

            # 连接成功后更新设备IP (仅内存/文件操作, 不碰 Qt 控件)
            try:
                self.main_window.update_device_ip(device_id, f'{target_ip}:{target_port}')
            except Exception as e:
                debug_log(f'[MyDesk] 更新设备IP失败: {e}')
            try:
                self.main_window.add_history_entry(device_id, password,
                                                   f'{target_ip}:{target_port}')
            except Exception as e:
                debug_log(f'[MyDesk] 写入历史记录失败: {e}')

            # 把结果投递回 GUI 线程创建窗口
            self._bridge.session_ready.emit(controller, mode, gen)
        except Exception as e:
            debug_log(f'[MyDesk] 连接异常: {e}\n{traceback.format_exc()}')
            emit_failed('连接失败', f'连接出错: {e}')

    def _find_device_ip(self, device_id):
        """通过设备ID查找IP: UDP广播(快速) + TCP子网扫描(轻量回退)"""
        # 策略1: UDP广播 (单端口, 快速)
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            # 广播到固定的发现端口(与被控端 TCP 端口解耦, 端口漂移也能发现)
            sock.sendto(device_id.encode('utf-8'),
                        ('255.255.255.255', get_discovery_port()))
            try:
                data, addr = sock.recvfrom(1024)
                resp = data.decode('utf-8', errors='ignore')
                if '|' in resp:
                    resp_id, resp_port = resp.split('|', 1)
                    if resp_id.upper() == device_id.upper():
                        sock.close()
                        try:
                            self._resolved_port = int(resp_port)
                        except ValueError:
                            pass
                        debug_log(f'[MyDesk] UDP广播找到设备 {device_id} at '
                                  f'{addr[0]}:{self._resolved_port}')
                        return addr[0]
                elif resp.strip().upper() == device_id.upper():
                    sock.close()
                    debug_log(f'[MyDesk] UDP广播找到设备 {device_id} at {addr[0]}')
                    return addr[0]
            except socket.timeout:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception as e:
            debug_log(f'[MyDesk] UDP广播异常: {e}')

        # 策略2: TCP子网扫描(轻量, 最多10并发)
        return self._tcp_scan_find(device_id)

    def _tcp_scan_find(self, device_id):
        """TCP子网扫描: 最多10并发, 总共10秒超时"""
        try:
            import socket
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
            s = None
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
            except Exception:
                pass
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass

        # 最多10个并发线程, 分批扫描
        ip_list = [f'{base}.{i}' for i in range(1, 255)
                   if f'{base}.{i}' != local_ip]
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

    def _close_existing_session(self):
        """打开新会话前先释放旧会话/旧控制器。

        不先关旧窗口的话, 旧会话的断开回调会经 _cleanup_session
        把刚建好的新会话杀掉 (日志实证: 新会话 3 秒后被旧回调断开)。
        """
        old_session = self.current_session
        old_ft = self.current_file_transfer
        old_ctrl = self.current_controller
        # 先清空引用: 旧窗口 close() 期间触发的断开回调走 _cleanup_session 时
        # 会因"身份不匹配"被直接忽略
        self.current_session = None
        self.current_file_transfer = None
        self.current_controller = None
        for w in (old_session, old_ft):
            if w is not None:
                try:
                    w.close()
                except Exception as e:
                    debug_log(f'[MyDesk] 关闭旧会话窗口失败: {e}')
        if old_ctrl is not None:
            try:
                old_ctrl.disconnect()
            except Exception:
                pass

    def _open_session(self, controller, mode='control'):
        try:
            debug_log('[MyDesk] 开始创建 ViewerWindow')
            self._close_existing_session()
            self.current_controller = controller
            self.current_session = ViewerWindow(controller, mode=mode)
            self.current_session.setWindowTitle('MyDesk - 远程会话')
            self.current_session.resize(1280, 800)
            self.current_session.show()
            debug_log('[MyDesk] ViewerWindow.show() 完成')

            # 断线回调在接收线程触发 -> 用信号切回 GUI 线程
            # (绝不能用 QTimer.singleShot: 非 GUI 线程里创建的定时器永远不会触发)
            # 绑定具体 controller: 过期会话的回调不得清理新会话
            controller.add_disconnect_callback(
                lambda c=controller: self._bridge.session_closed.emit(c))
            self.main_window.start_latency_monitor()
            debug_log('[MyDesk] 会话已打开')
        except Exception as e:
            debug_log(f'[MyDesk] 打开会话异常: {e}\n{traceback.format_exc()}')
            QMessageBox.critical(None, '错误', f'打开会话失败: {e}')

    def _open_file_transfer(self, controller):
        try:
            # 先释放旧会话, 防止旧窗口回调杀掉本次文件传输会话
            self._close_existing_session()
            # 纯文件传输模式: 完全不需要画面, 让被控端别抓屏 (省掉全部画面开销)
            try:
                from protocol import MSG_CONTROL
                controller.send(MSG_CONTROL, b'SCREEN_SELECT:-1')
            except Exception:
                pass
            recv_dir = get_setting('save_path')
            if not recv_dir:
                recv_dir = os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')
            os.makedirs(recv_dir, exist_ok=True)
            # 同步controller的接收目录
            controller.file_transfer.recv_dir = recv_dir
            self.current_controller = controller
            ft = FileTransferDialog(controller, recv_dir)
            # 上传/下载的发起包含 socket 写操作, 放到后台线程避免阻塞 GUI
            ft.file_upload_requested.connect(
                lambda p: threading.Thread(target=controller.upload_file,
                                           args=(p,), daemon=True).start())
            ft.file_download_requested.connect(
                lambda n: threading.Thread(target=controller.download_file,
                                           args=(n,), daemon=True).start())
            ft.finished.connect(lambda _code=0, d=ft: self._on_file_transfer_closed(d))
            ft.resize(900, 600)
            self.current_file_transfer = ft
            ft.show()
            debug_log('[MyDesk] 文件传输窗口已显示')
        except Exception as e:
            debug_log(f'[MyDesk] 文件传输异常: {e}\n{traceback.format_exc()}')
            QMessageBox.critical(None, '错误', f'文件传输失败: {e}')

    def _on_file_transfer_closed(self, dlg=None):
        # 会话隔离: 只有"当前"文件传输窗口关闭才允许清理当前会话,
        # 否则被替换掉的旧窗口关闭时会误杀新会话
        if dlg is not None and dlg is not self.current_file_transfer:
            debug_log('[MyDesk] 忽略过期文件传输窗口关闭事件')
            return
        if self.current_controller:
            try:
                self.current_controller.disconnect()
            except Exception:
                pass
            self.current_controller = None
        self.current_file_transfer = None

    def _cleanup_session(self, controller=None):
        # 会话隔离: 只允许清理"当前"会话。
        # 旧会话(可能已关闭/已被替换)的断开回调若不拦下, 会把新会话一起杀掉。
        if controller is not None and controller is not self.current_controller:
            debug_log('[MyDesk] 忽略过期会话断开回调 (会话隔离)')
            return
        debug_log('[MyDesk] 检测到会话断开, 清理会话')
        self.main_window.stop_latency_monitor()
        if self.current_controller:
            try:
                self.current_controller.disconnect()
            except Exception:
                pass
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
            debug_log(f'[MyDesk] 被控端启动失败: {e}\n{traceback.format_exc()}')
            self._bridge.status_message.emit(f'被控端启动失败: {e}')

    def run(self):
        try:
            self.start_controlled()
            self.show_main_window()
            return self.app.exec_()
        except Exception as e:
            crash_log(f'run() 异常: {e}\n{traceback.format_exc()}')
            _queue_error('MyDesk 运行异常', str(e))
            return 1

    def quit(self):
        self._closing = True
        if self.controlled:
            try:
                self.controlled.stop()
            except Exception:
                pass
        for ctrl in (self.current_controller,):
            if ctrl:
                try:
                    ctrl.disconnect()
                except Exception:
                    pass
        self.app.quit()


def run_silent():
    """--silent: 只做被控端, 不创建任何窗口/托盘 (开机自启用)。

    旧版安装器把自启写成 "MyDesk.exe --silent", 但 main.py 根本不认识这个参数,
    于是每次登录都会弹出完整主窗口 —— 与"后台静默运行"的勾选项不符。
    """
    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication(sys.argv)
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    server = Controlled(on_log=lambda m: debug_log(f'[静默被控端] {m}'))
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    try:
        return app.exec_()
    finally:
        server.stop()


def run_agent():
    """在交互用户会话中运行被控端代理。

    由 windows_service.py 以当前活动会话用户身份拉起；不创建主界面，
    只保留屏幕、输入、文件和网络服务。"""
    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication(sys.argv)
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    server = Controlled(on_log=lambda m: debug_log(f'[会话代理] {m}'))
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    try:
        return app.exec_()
    finally:
        server.stop()


def main():
    try:
        debug_log('main() 开始运行', 'startup')

        args = sys.argv[1:]
        # pywin32 启动已安装服务时可能传入 -k ServiceName；安装/管理命令
        # 则使用 --service。两种入口都必须绕过 Qt GUI。
        if '--service' in args or '-k' in args:
            # 服务命令由 pywin32 处理，避免服务进程创建 Qt 窗口。
            from windows_service import service_cli
            sys.argv = [sys.argv[0]] + [a for a in args if a != '--service']
            service_cli()
            return 0
        if '--agent' in args:
            return run_agent()
        silent = '--silent' in args

        if not acquire_single_instance():
            if not silent:
                notify_already_running()
            return 0

        if silent:
            debug_log('以静默模式启动被控端', 'startup')
            return run_silent()

        app = MyDeskApplication()
        debug_log('MyDeskApplication 创建完成, 调用 run()', 'startup')
        app.run()
    except Exception as e:
        crash_log(f'CRASH: {e}\n{traceback.format_exc()}')
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f'MyDesk 启动失败:\n\n{e}', 'MyDesk Error', 0x10)
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
