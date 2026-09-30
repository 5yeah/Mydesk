# -*- coding: utf-8 -*-
"""MyDesk 被控端 (服务端)

阶段1: TCP 监听 + 密码认证 + 心跳
阶段2: 屏幕流推送 (mss 捕获 + JPEG 传输)
阶段3: 键鼠事件执行 (Win32 SendInput)
阶段4: 文件传输 (双向 + 断点续传)
阶段5: 多客户端共享屏幕广播 (多人同时连接同一台设备)

每个客户端连接有独立的 _send_lock, 保证并发写安全。
所有客户端共享 ScreenBroadcaster 单例, 避免重复捕获。
键鼠事件加锁排队执行, 避免冲突。
"""
import os
import select
import socket
import threading
import time
import datetime
from protocol import (
    send_msg, recv_msg, hash_password,
    MSG_AUTH, MSG_HEARTBEAT, MSG_SCREEN, MSG_INPUT, MSG_FILE, MSG_CONTROL,
    MSG_CLIPBOARD,
)
from config import get_device_id, get_password, get_port, get_discovery_port
from screen_broadcaster import ScreenBroadcaster
from input_control import handle_input
from file_transfer import FileTransfer
import clipboard_win

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

def _debug_log(msg):
    """写入调试日志文件"""
    try:
        log_file = os.path.join(LOG_DIR, 'controlled_debug.log')
        timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f'[{timestamp}] {msg}\n')
    except Exception:
        pass

# 全局输入锁: 多客户端的键鼠事件排队执行, 避免冲突
_input_lock = threading.Lock()


class Controlled:
    def __init__(self, password=None, port=None, recv_dir=None, on_log=None,
                 max_clients=5):
        self.password = password or get_password()
        self.port = port or get_port()
        self.device_id = get_device_id()
        self.recv_dir = recv_dir or os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')
        os.makedirs(self.recv_dir, exist_ok=True)
        self.on_log = on_log or print
        self.max_clients = max_clients  # 最大并发连接数
        self.running = False
        self.server = None
        self._discover_sock = None
        self._clients = []          # [(conn, addr, client_id)]
        self._lock = threading.Lock()
        self._client_counter = 0
        self._broadcaster = None
        # 剪贴板同步状态 (按客户端开关)
        self._clip_sync_clients = set()   # 已开启剪贴板同步的 client_id
        self._clip_send_fns = {}          # client_id -> safe_send
        self._clip_lock = threading.Lock()
        self._clip_last_text = None       # 最近一次观察/写入的剪贴板文本
        self._clip_thread = None          # 剪贴板轮询线程

    # ---------- 生命周期 ----------
    def start(self):
        self.running = True
        # 启动屏幕广播器 (每个实例独立)
        self._broadcaster = ScreenBroadcaster(self.on_log)
        try:
            result = self._broadcaster.start()
            if not result:
                self.on_log('[被控端] 屏幕广播器启动失败，但继续运行（无屏幕模式）')
                _debug_log('[被控端] ScreenBroadcaster.start() 返回 False')
        except Exception as e:
            self.on_log(f'[被控端] 屏幕广播器启动异常: {e}')
            _debug_log(f'[被控端] ScreenBroadcaster.start() 异常: {e}')
            import traceback
            _debug_log(traceback.format_exc())

        # 自动检测端口冲突, 选择可用端口
        self.port = self._find_available_port(self.port)

        # TCP 服务
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('0.0.0.0', self.port))
        self.server.listen(self.max_clients)
        # UDP 设备发现: 绑定到固定的发现端口(与可变 TCP 端口解耦),
        # 这样即使 TCP 端口漂移, 主控端仍能发现并拿到真实端口。
        self._discover_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._discover_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._discover_sock.bind(('0.0.0.0', get_discovery_port()))
        except OSError as e:
            _debug_log(f'[被控端] UDP 发现端口 {get_discovery_port()} 绑定失败(忽略): {e}')
        self._discover_sock.settimeout(1.0)
        disc_thread = threading.Thread(target=self._discover_loop, daemon=True)
        disc_thread.start()
        self.on_log(f'[被控端] 设备ID={self.device_id} 密码={self.password} 端口={self.port}')
        self.on_log(f'[被控端] 最大连接数={self.max_clients} 接收目录={self.recv_dir}')
        self.on_log('[被控端] 等待连接...')
        while self.running:
            try:
                conn, addr = self.server.accept()
            except OSError as e:
                # Windows 下客户端在握手阶段异常断开时, accept() 会抛瞬时 OSError
                # (如 WinError 10053/10054)。绝不能因此 break 掉监听循环,
                # 否则服务端会永久停止接受新连接 —— 表现为"之后再也连不上"。
                if not self.running:
                    break
                _debug_log(f'[被控端] accept 瞬时异常(忽略并继续监听): {e}')
                time.sleep(0.05)
                continue
            # 检查连接数限制
            with self._lock:
                current = len(self._clients)
            if current >= self.max_clients:
                self.on_log(f'[被控端] 拒绝 {addr[0]} (已达上限 {self.max_clients})')
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            self.on_log(f'[被控端] 连接来自 {addr[0]}:{addr[1]}')
            t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
            t.start()

    def _discover_loop(self):
        """UDP 设备发现: 收到设备ID广播时回复本机IP和端口"""
        self.on_log(f'[发现] UDP监听端口: {get_discovery_port()}')
        while self.running:
            try:
                data, addr = self._discover_sock.recvfrom(1024)
                received_id = data.decode('utf-8')
                if received_id.upper() == self.device_id.upper():
                    # 响应格式: "device_id|port"  (主控端需要知道实际TCP端口)
                    response = f"{self.device_id}|{self.port}".encode('utf-8')
                    self._discover_sock.sendto(response, addr)
                    self.on_log(f'[发现] 响应来自 {addr[0]} 的设备查询 (ID={received_id})')
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                self.on_log(f'[发现] 异常: {e}')
                continue

    def stop(self):
        self.running = False
        if self._discover_sock:
            try:
                self._discover_sock.close()
            except Exception:
                pass
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        if self._broadcaster:
            self._broadcaster.stop()

    def get_client_count(self):
        with self._lock:
            return len(self._clients)

    def get_client_list(self):
        with self._lock:
            return [(addr[0], cid) for _, addr, cid in self._clients]

    def _find_available_port(self, start_port):
        """从 start_port 开始, 找一个可用的 TCP 端口 (UDP 发现端口已固定, 单独处理)"""
        for port in range(start_port, start_port + 20):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # 不使用 SO_REUSEADDR, 确保能真实检测到端口占用
                s.bind(('0.0.0.0', port))
                s.close()
            except OSError:
                continue
            if port != start_port:
                self.on_log(f'[被控端] 端口 {start_port} 已被占用, 使用 {port}')
            return port
        self.on_log(f'[被控端] 警告: 端口 {start_port}-{start_port+19} 均被占用')
        return start_port

    @staticmethod
    def _set_keepalive(conn):
        """开启 TCP keepalive, 并尽量缩短探测间隔, 以便快速发现掉线客户端。"""
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception as e:
            _debug_log(f'[被控端] 开启 SO_KEEPALIVE 失败: {e}')
        # Windows: ioctl(SIO_KEEPALIVE_VALS, (启用, 空闲毫秒, 间隔毫秒))
        try:
            if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
                conn.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        except Exception as e:
            _debug_log(f'[被控端] 设置 keepalive 参数失败: {e}')

    # ---------- 客户端处理 ----------
    def _handle_client(self, conn, addr):
        send_lock = threading.Lock()

        def safe_send(mtype, payload=b''):
            # 注意: 本函数每一帧都会被调用, 严禁在此写逐条调试日志,
            # 否则高频文件 IO 会严重拖慢屏幕流(表现为卡顿)。
            with send_lock:
                try:
                    send_msg(conn, mtype, payload)
                except Exception as e:
                    _debug_log(f'[被控端] safe_send 失败: mtype={mtype}, error={e}')

        # 分配客户端ID
        with self._lock:
            self._client_counter += 1
            client_id = f'{addr[0]}#{self._client_counter}'

        _debug_log(f'[被控端] 新连接: {addr[0]}:{addr[1]}, client_id={client_id}')
        self.on_log(f'[被控端] 新连接: {addr[0]}:{addr[1]}, client_id={client_id}')

        try:
            conn.settimeout(5)  # 认证超时 (缩短到5秒)
            msg = recv_msg(conn)
            if msg is None:
                self.on_log(f'[被控端] [{client_id}] 认证阶段: 对端关闭连接')
                return
            
            mtype, payload = msg
            _debug_log(f'[被控端] 收到认证请求: mtype={mtype}')
            
            # 处理设备ID查询 (认证前, 用于TCP发现)
            if mtype == MSG_CONTROL and payload == b'DEVICE_ID_QUERY':
                try:
                    send_msg(conn, MSG_CONTROL, self.device_id.encode('utf-8'))
                    self.on_log(f'[被控端] [{client_id}] 响应设备ID查询: {self.device_id}')
                except Exception:
                    pass
                conn.close()
                return
            
            if mtype != MSG_AUTH or payload != hash_password(self.password):
                try:
                    send_msg(conn, MSG_AUTH, b'\x00')
                except Exception:
                    pass
                self.on_log(f'[被控端] [{client_id}] {addr[0]} 认证失败 (密码错误)')
                return
            try:
                send_msg(conn, MSG_AUTH, b'\x01')
            except Exception as e:
                self.on_log(f'[被控端] [{client_id}] 发送认证响应失败: {e}')
                return
            conn.settimeout(None)
            # 认证通过后开启 TCP keepalive: 客户端崩溃/断网(半开连接)时能尽快
            # 侦测到并回收连接名额, 避免占满 max_clients 导致"后续连不上"。
            self._set_keepalive(conn)
            with self._lock:
                self._clients.append((conn, addr, client_id))
            count = len(self._clients)
            self.on_log(f'[被控端] [{client_id}] {addr[0]} 认证成功 (当前 {count} 人在线)')
            _debug_log(f'[被控端] 认证成功, 准备订阅屏幕广播')

            # 文件传输管理器
            ft = FileTransfer(safe_send, self.recv_dir,
                              on_log=lambda m: self.on_log(f'[被控端] [{client_id}] {m}'))
            # 订阅共享屏幕广播
            _debug_log(f'[被控端] 调用 subscribe, broadcaster存在={self._broadcaster is not None}')
            if self._broadcaster and self._broadcaster.running:
                try:
                    self._broadcaster.subscribe(client_id, safe_send)
                    _debug_log(f'[被控端] subscribe 完成')
                except Exception as e:
                    _debug_log(f'[被控端] subscribe 异常: {e}')
                    self.on_log(f'[被控端] [{client_id}] 屏幕订阅失败: {e}')
            else:
                _debug_log(f'[被控端] 屏幕广播器未运行，跳过订阅')
                self.on_log(f'[被控端] [{client_id}] 屏幕广播器未就绪，将在屏幕就绪后自动推送')
            self._message_loop(conn, addr, client_id, ft, safe_send)
        except Exception as e:
            _debug_log(f'[被控端] 异常: {e}')
            self.on_log(f'[被控端] [{client_id}] 异常: {e}')
        finally:
            with self._lock:
                self._clients = [(c, a, cid) for c, a, cid in self._clients if c is not conn]
            # 取消剪贴板同步注册
            with self._clip_lock:
                self._clip_sync_clients.discard(client_id)
                self._clip_send_fns.pop(client_id, None)
            # 取消屏幕订阅
            if self._broadcaster:
                self._broadcaster.unsubscribe(client_id)
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                count = len(self._clients)
            self.on_log(f'[被控端] [{client_id}] 断开 (剩余 {count} 人在线)')

    def _message_loop(self, conn, addr, client_id, ft, send_fn):
        """认证后消息分发循环

        send_fn: 该客户端的线程安全发送函数 (带 per-client 发送锁)。
        所有回复(心跳/控制/文件)必须走 send_fn, 绝不能绕过锁直接 send_msg,
        否则会与屏幕帧/文件数据交错, 损坏 TCP 流。
        """
        IDLE_TIMEOUT = 30  # 主控端每 5 秒心跳, 30 秒无任何数据即判定掉线
        while self.running:
            # 用 select 做空闲检测: 超时说明客户端已死/断网, 主动回收连接名额,
            # 避免半开连接永久占用导致 "后续无法连接"。(不设 socket 超时,
            # 以免 sendall 因发送缓冲暂时满而超时, 破坏 TCP 帧完整性)
            try:
                ready, _, _ = select.select([conn], [], [], IDLE_TIMEOUT)
            except (OSError, ValueError):
                break
            if not ready:
                _debug_log(f'[被控端] [{client_id}] {IDLE_TIMEOUT}s 无数据(心跳超时), 回收连接')
                break
            try:
                msg = recv_msg(conn)
            except Exception as e:
                _debug_log(f'[被控端] [{client_id}] 接收异常: {e}')
                break
            if msg is None:
                break
            mtype, payload = msg
            if mtype == MSG_HEARTBEAT:
                try:
                    send_fn(MSG_HEARTBEAT)
                except Exception:
                    break
            elif mtype == MSG_CONTROL:
                # 设备ID查询 (用于TCP发现, 认证前也能响应)
                try:
                    cmd = payload.decode('utf-8')
                    if cmd == 'DEVICE_ID_QUERY':
                        send_fn(MSG_CONTROL, self.device_id.encode('utf-8'))
                        self.on_log(f'[被控端] [{client_id}] 响应设备ID查询')
                        continue
                except Exception:
                    pass
                self._handle_control(payload, client_id, send_fn)
            elif mtype == MSG_INPUT:
                # 加锁排队执行, 避免多客户端同时操作冲突
                with _input_lock:
                    try:
                        handle_input(payload)
                    except Exception as e:
                        _debug_log(f'[被控端] [{client_id}] 输入执行异常: {e}')
            elif mtype == MSG_FILE:
                try:
                    ft.handle_message(payload)
                except Exception as e:
                    _debug_log(f'[被控端] [{client_id}] 文件消息处理异常: {e}')
                    try:
                        send_fn(MSG_FILE, b'\x06' + f'协议处理失败: {e}'.encode('utf-8')[:2000])
                    except Exception:
                        pass
            elif mtype == MSG_CLIPBOARD:
                self._on_clipboard_receive(client_id, payload)
            # MSG_SCREEN 被控端不接收, 忽略

    def _handle_control(self, payload, client_id=None, send_fn=None):
        """处理主控端控制指令: 画质/帧率/剪贴板开关"""
        try:
            cmd = payload.decode('utf-8')
            if cmd.startswith('QUALITY:'):
                self._broadcaster.set_quality(int(cmd.split(':')[1]))
            elif cmd.startswith('FPS:'):
                self._broadcaster.set_fps(int(cmd.split(':')[1]))
            elif cmd.startswith('CLIPBOARD:'):
                self._set_clipboard_sync(cmd.split(':')[1] == 'on', client_id, send_fn)
        except Exception:
            pass

    # ---------- 剪贴板同步 ----------
    def _set_clipboard_sync(self, enabled, client_id, send_fn):
        """开启/关闭某个客户端的剪贴板同步"""
        with self._clip_lock:
            if enabled and client_id:
                self._clip_sync_clients.add(client_id)
                self._clip_send_fns[client_id] = send_fn
                self._clip_last_text = None  # 强制下一次轮询立即推送当前剪贴板
            elif client_id:
                self._clip_sync_clients.discard(client_id)
                self._clip_send_fns.pop(client_id, None)
        if self._clip_thread is None or not self._clip_thread.is_alive():
            self._clip_thread = threading.Thread(
                target=self._clipboard_monitor, daemon=True)
            self._clip_thread.start()
        self.on_log(f'[被控端] [{client_id}] 剪贴板同步={"开" if enabled else "关"}')

    def _clipboard_monitor(self):
        """轮询被控端本机剪贴板, 变化时推送给已开启同步的客户端"""
        while self.running:
            with self._clip_lock:
                active = list(self._clip_sync_clients)
            if not active:
                time.sleep(0.5)
                continue
            try:
                text = clipboard_win.get_text()
            except Exception:
                time.sleep(0.5)
                continue
            with self._clip_lock:
                last = self._clip_last_text
            if text != last:
                with self._clip_lock:
                    self._clip_last_text = text
                    fns = [(cid, self._clip_send_fns.get(cid)) for cid in active]
                for cid, fn in fns:
                    if fn is None:
                        continue
                    try:
                        fn(MSG_CLIPBOARD, text.encode('utf-8'))
                    except Exception as e:
                        _debug_log(f'[剪贴板] 推送失败 {cid}: {e}')
            time.sleep(0.5)

    def _on_clipboard_receive(self, client_id, payload):
        """收到主控端推送的剪贴板文本, 写入本机剪贴板"""
        text = payload.decode('utf-8', errors='replace')
        try:
            clipboard_win.set_text(text)
            with self._clip_lock:
                self._clip_last_text = text  # 防止轮询线程回环推送
            self.on_log(f'[被控端] [{client_id}] 收到剪贴板 ({len(text)} 字符)')
        except Exception as e:
            _debug_log(f'[剪贴板] 设置本机剪贴板失败: {e}')


if __name__ == '__main__':
    import sys
    # 独立运行(无 Qt)时手动设为 DPI 感知, 保证注入坐标与屏幕捕获物理分辨率一致
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    args = sys.argv[1:]

    # ---- 命令行子命令 ----
    if '--install' in args:
        from autostart import install
        install()
        sys.exit(0)
    if '--install-task' in args:
        from autostart import install_task_scheduler
        install_task_scheduler()
        sys.exit(0)
    if '--uninstall' in args:
        from autostart import uninstall
        uninstall()
        sys.exit(0)
    if '--set-password' in args:
        idx = args.index('--set-password')
        if idx + 1 < len(args):
            from config import set_password
            set_password(args[idx + 1])
            print(f'[被控端] 密码已设置为 {args[idx + 1]}')
        sys.exit(0)
    if '--set-max-clients' in args:
        idx = args.index('--set-max-clients')
        if idx + 1 < len(args):
            from config import save_config, load_config
            cfg = load_config()
            cfg['max_clients'] = int(args[idx + 1])
            save_config(cfg)
            print(f'[被控端] 最大连接数已设置为 {args[idx + 1]}')
        sys.exit(0)

    # 静默模式: 隐藏控制台
    if '--silent' in args:
        from autostart import hide_console
        hide_console()

    # 普通位置参数: [password] [port]
    pwd = None
    port = None
    for a in args:
        if not a.startswith('--'):
            if pwd is None:
                pwd = a
            elif port is None:
                try:
                    port = int(a)
                except ValueError:
                    pass

    # 读取最大连接数配置
    from config import load_config
    max_clients = load_config().get('max_clients', 5)

    app = Controlled(pwd, port, max_clients=max_clients)
    try:
        app.start()
    except KeyboardInterrupt:
        app.stop()
        print('[被控端] 已停止')