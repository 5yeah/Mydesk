# -*- coding: utf-8 -*-
"""MyDesk 被控端 (服务端)

阶段1: TCP 监听 + 挑战-响应认证 + 心跳(RTT回显) + 认证限流
阶段2: 屏幕流推送 (mss 捕获 + JPEG 传输, 共享广播器)
阶段3: 键鼠事件执行 (Win32 SendInput, 虚拟桌面坐标系)
阶段4: 文件传输 (双向 + 断点续传)
阶段5: 多客户端共享屏幕广播 (多人同时连接同一台设备)

每个客户端连接有独立的 _send_lock, 保证并发写安全。
所有客户端共享 ScreenBroadcaster 单例, 避免重复捕获。
键鼠事件加锁排队执行, 避免冲突。
"""
import os
import hmac as _hmac
import select
import socket
import threading
import time
import datetime
from protocol import (
    send_msg, recv_msg,
    MSG_AUTH, MSG_HEARTBEAT, MSG_SCREEN, MSG_INPUT, MSG_FILE, MSG_CONTROL,
    MSG_CLIPBOARD,
    AUTH_V2_REQ, AUTH_V2_CHALLENGE, AUTH_V2_RESPONSE, AUTH_OK, AUTH_FAIL,
    make_nonce, auth_response,
    CTL_QUALITY, CTL_FPS, CTL_CLIPBOARD, CTL_PAUSE, CTL_RESUME, CTL_DEVICE_ID_QUERY,
    CTL_SCREEN_INFO_REQ,
)
from config import (get_device_id, get_password, get_port, get_discovery_port,
                    CONFIG_FILE, load_config, load_settings)
from screen_broadcaster import ScreenBroadcaster
from input_control import handle_input
from file_transfer import FileTransfer
import clipboard_win

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 认证限流参数 (个人自用场景取"温和"值: 允许几次手误, 之后逐次小幅延迟)
AUTH_MAX_FAILURES = 5        # 前 5 次失败不延迟
AUTH_MAX_DELAY = 5.0         # 单次认证失败后的最大延迟(秒)
AUTH_FAIL_WINDOW = 300.0     # 计数窗口(秒), 窗口内无失败则清零

# socket 发送超时(秒): 防止慢/死客户端把发送线程永久挂住
SEND_TIMEOUT = 15


def _debug_log(msg):
    """写入调试日志文件 (严禁在逐帧路径调用)"""
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
        self.recv_dir = recv_dir or os.path.join(
            os.path.expanduser('~'), 'Desktop', 'MyDesk_接收')
        os.makedirs(self.recv_dir, exist_ok=True)
        self.on_log = on_log or print
        self.max_clients = max_clients  # 最大并发连接数(含未认证连接)
        self.running = False
        self.server = None
        self._discover_sock = None
        self._clients = []          # 已认证: [(conn, addr, client_id)]
        self._lock = threading.Lock()
        self._client_counter = 0
        self._conn_count = 0        # 活跃处理线程数(含认证中)
        self._broadcaster = None
        # 密码: 构造时显式传入则固定使用(测试/命令行场景);
        # 否则从 config.json 读取, 并按文件 mtime 热加载 —— 改密码无需重启即可生效
        self._explicit_password = password
        self._pwd_cache = password if password is not None else get_password()
        self.password = self._pwd_cache
        self._pwd_mtime = None
        self._pwd_lock = threading.Lock()
        # 认证失败计数: ip -> [次数, 最后失败时间]
        self._auth_failures = {}
        # 剪贴板同步状态 (按客户端开关)
        self._clip_sync_clients = set()
        self._clip_send_fns = {}
        self._clip_lock = threading.Lock()
        self._clip_last_text = None
        self._clip_thread = None

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
            import traceback
            _debug_log(f'[被控端] ScreenBroadcaster.start() 异常: {e}\n'
                       f'{traceback.format_exc()}')

        # 自动检测端口冲突, 选择可用端口
        self.port = self._find_available_port(self.port)

        # TCP 服务
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('0.0.0.0', self.port))
        self.server.listen(max(1, self.max_clients))
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
        self.on_log(f'[被控端] 设备ID={self.device_id} 端口={self.port}')
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
            # 访问控制: 仅局域网 / IP 白名单 (高级设置里真的会生效)
            if not self._peer_allowed(addr[0]):
                self.on_log(f'[被控端] 拒绝 {addr[0]} (不满足局域网/白名单设置)')
                _debug_log(f'[被控端] 拒绝连接: {addr[0]} 未通过访问控制')
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            # 连接数限制: 未认证的连接也占用名额, 防止连接耗尽
            with self._lock:
                current = self._conn_count
            if current >= self.max_clients:
                self.on_log(f'[被控端] 拒绝 {addr[0]} (已达上限 {self.max_clients})')
                try:
                    conn.close()
                except Exception:
                    pass
                continue
            with self._lock:
                self._conn_count += 1
            self.on_log(f'[被控端] 连接来自 {addr[0]}:{addr[1]}')
            t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
            t.start()

    def _discover_loop(self):
        """UDP 设备发现: 收到设备ID广播时回复本机IP和端口"""
        self.on_log(f'[发现] UDP监听端口: {get_discovery_port()}')
        while self.running:
            try:
                data, addr = self._discover_sock.recvfrom(1024)
                received_id = data.decode('utf-8', errors='ignore')
                if received_id.upper() == self.device_id.upper():
                    # 响应格式: "device_id|port"  (主控端需要知道实际TCP端口)
                    response = f"{self.device_id}|{self.port}".encode('utf-8')
                    self._discover_sock.sendto(response, addr)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                _debug_log(f'[发现] 异常: {e}')
                continue

    def stop(self):
        self.running = False
        if self._discover_sock:
            try:
                self._discover_sock.close()
            except Exception:
                pass
            self._discover_sock = None
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
            self.server = None
        # 主动关闭所有客户端连接, 让处理线程及时退出
        with self._lock:
            conns = [c for c, _a, _cid in self._clients]
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
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
        disc = get_discovery_port()
        for port in range(start_port, start_port + 20):
            if port == disc:
                # 跳过固定的 UDP 发现端口, 避免 TCP/UDP 端口号撞车造成排查混乱
                continue
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
        self.on_log(f'[被控端] 警告: 端口 {start_port}-{start_port + 19} 均被占用')
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
        # 发送超时: 客户端不读数据时不要永久阻塞发送线程
        try:
            import struct as _struct
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO,
                            _struct.pack('<i', int(SEND_TIMEOUT * 1000)))
        except Exception as e:
            _debug_log(f'[被控端] 设置发送超时失败: {e}')

    # ---------- 认证限流 ----------
    def _current_password(self):
        """返回当前生效密码。

        - 构造时显式传入密码 -> 固定使用该密码(不再读配置文件)
        - 未显式传入 -> 读 config.json, 且文件变更时自动热加载
        """
        if self._explicit_password is not None:
            with self._pwd_lock:
                return self._pwd_cache
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            with self._pwd_lock:
                return self._pwd_cache
        with self._pwd_lock:
            if mtime != self._pwd_mtime:
                self._pwd_mtime = mtime
                try:
                    self._pwd_cache = get_password()
                    self.password = self._pwd_cache
                except Exception:
                    pass
            return self._pwd_cache

    def set_password(self, pwd):
        """运行时直接改密码 (不写配置文件)"""
        with self._pwd_lock:
            self._pwd_cache = pwd
            self.password = pwd
            self._explicit_password = pwd

    def _auth_delay(self, ip):
        """返回本次认证失败后应延迟的秒数 (指数退避)"""
        now = time.time()
        with self._lock:
            rec = self._auth_failures.get(ip)
            if rec and now - rec[1] > AUTH_FAIL_WINDOW:
                rec = None
            count = (rec[0] if rec else 0) + 1
            self._auth_failures[ip] = [count, now]
            # 防止字典无界增长
            if len(self._auth_failures) > 512:
                for k in [k for k, v in self._auth_failures.items()
                          if now - v[1] > AUTH_FAIL_WINDOW]:
                    self._auth_failures.pop(k, None)
        if count <= AUTH_MAX_FAILURES:
            return 0.0
        return min(AUTH_MAX_DELAY, 0.5 * (2 ** (count - AUTH_MAX_FAILURES - 1)))

    def _auth_ok(self, ip):
        with self._lock:
            self._auth_failures.pop(ip, None)

    def _auth_failure_count(self, ip):
        with self._lock:
            rec = self._auth_failures.get(ip)
            return rec[0] if rec else 0

    @staticmethod
    def _is_private_ip(ip):
        """私有/本机/Tailscale(CGNAT) 地址判断, 用于"仅局域网"模式"""
        try:
            parts = [int(p) for p in ip.split('.')]
        except Exception:
            return False
        if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
            return False
        a, b = parts[0], parts[1]
        if a == 127:                       # 本机回环
            return True
        if a == 10:                        # 10.0.0.0/8
            return True
        if a == 192 and b == 168:          # 192.168.0.0/16
            return True
        if a == 172 and 16 <= b <= 31:     # 172.16.0.0/12
            return True
        if a == 169 and b == 254:          # 169.254.0.0/16 (链路本地)
            return True
        if a == 100 and 64 <= b <= 127:    # 100.64.0.0/10 (Tailscale/CGNAT)
            return True
        return False

    def _peer_allowed(self, ip):
        """按高级设置里的"仅局域网"和"IP 白名单"决定是否接受该来源。

        本机回环永远放行, 否则本机自测会被自己挡住。
        """
        if ip in ('127.0.0.1', '::1'):
            return True
        try:
            st = load_settings()
        except Exception:
            return True
        # 白名单优先: 开启且填了内容时, 只允许匹配前缀/全等的地址
        if st.get('ip_whitelist'):
            patterns = [p.strip() for p in str(st.get('whitelist_ips', '')).split(',')
                        if p.strip()]
            if patterns:
                if not any(ip == p or ip.startswith(p) for p in patterns):
                    return False
        if st.get('lan_only') and not self._is_private_ip(ip):
            return False
        return True

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

        authed = False
        ft = None
        try:
            conn.settimeout(5)  # 认证超时
            msg = recv_msg(conn)
            if msg is None:
                self.on_log(f'[被控端] [{client_id}] 认证阶段: 对端关闭连接')
                return
            mtype, payload = msg

            # 处理设备ID查询 (认证前, 用于局域网 TCP 兜底发现)
            if mtype == MSG_CONTROL and payload == CTL_DEVICE_ID_QUERY.encode('utf-8'):
                try:
                    send_msg(conn, MSG_CONTROL, self.device_id.encode('utf-8'))
                except Exception:
                    pass
                return

            # ---- 挑战-响应认证 (v2) ----
            if mtype != MSG_AUTH or not payload or payload[0] != AUTH_V2_REQ \
                    or len(payload) < 1 + 16:
                try:
                    send_msg(conn, MSG_AUTH, AUTH_FAIL)
                except Exception:
                    pass
                self.on_log(f'[被控端] [{client_id}] {addr[0]} 认证失败 (协议不匹配)')
                return
            client_nonce = payload[1:1 + 16]

            server_nonce = make_nonce()
            try:
                send_msg(conn, MSG_AUTH, bytes([AUTH_V2_CHALLENGE]) + server_nonce)
            except Exception as e:
                self.on_log(f'[被控端] [{client_id}] 发送挑战失败: {e}')
                return

            msg = recv_msg(conn)
            if msg is None:
                return
            mtype, payload = msg
            password = self._current_password()
            expected = auth_response(password, client_nonce, server_nonce)
            got = payload[1:] if (mtype == MSG_AUTH and payload
                                  and payload[0] == AUTH_V2_RESPONSE) else b''

            # 定长比较, 避免时序侧信道
            if not _hmac.compare_digest(got, expected):
                delay = self._auth_delay(addr[0])
                if delay:
                    _debug_log(f'[被控端] [{client_id}] 认证失败延迟 {delay:.1f}s '
                               f'(该IP累计失败 {self._auth_failure_count(addr[0])} 次)')
                    time.sleep(delay)
                try:
                    send_msg(conn, MSG_AUTH, AUTH_FAIL)
                except Exception:
                    pass
                self.on_log(f'[被控端] [{client_id}] {addr[0]} 认证失败 (密码错误)')
                return

            self._auth_ok(addr[0])
            try:
                send_msg(conn, MSG_AUTH, AUTH_OK)
            except Exception as e:
                self.on_log(f'[被控端] [{client_id}] 发送认证响应失败: {e}')
                return
            authed = True
            conn.settimeout(None)
            # 认证通过后开启 TCP keepalive: 客户端崩溃/断网(半开连接)时能尽快
            # 侦测到并回收连接名额, 避免占满 max_clients 导致"后续连不上"。
            self._set_keepalive(conn)
            with self._lock:
                self._clients.append((conn, addr, client_id))
                count = len(self._clients)
            self.on_log(f'[被控端] [{client_id}] {addr[0]} 认证成功 (当前 {count} 人在线)')

            # 文件传输管理器
            ft = FileTransfer(safe_send, self.recv_dir,
                              on_log=lambda m: self.on_log(f'[被控端] [{client_id}] {m}'))
            # 订阅共享屏幕广播
            if self._broadcaster and self._broadcaster.running:
                try:
                    self._broadcaster.subscribe(client_id, safe_send)
                except Exception as e:
                    _debug_log(f'[被控端] subscribe 异常: {e}')
                    self.on_log(f'[被控端] [{client_id}] 屏幕订阅失败: {e}')
            else:
                self.on_log(f'[被控端] [{client_id}] 屏幕广播器未就绪，将在屏幕就绪后自动推送')
            self._message_loop(conn, addr, client_id, ft, safe_send)
        except Exception as e:
            import traceback
            _debug_log(f'[被控端] 异常: {e}\n{traceback.format_exc()}')
            self.on_log(f'[被控端] [{client_id}] 异常: {e}')
        finally:
            with self._lock:
                self._clients = [(c, a, cid) for c, a, cid in self._clients if c is not conn]
                self._conn_count = max(0, self._conn_count - 1)
                count = len(self._clients)
            with self._clip_lock:
                self._clip_sync_clients.discard(client_id)
                self._clip_send_fns.pop(client_id, None)
            if ft is not None:
                # 清理未完成的接收状态 (保留 .part 残文件以便续传)
                try:
                    ft.reset()
                except Exception as e:
                    _debug_log(f'[被控端] 重置文件传输状态失败: {e}')
            if self._broadcaster:
                # 已认证的才订阅过, 未认证的连接没有订阅, 不必触发订阅日志
                if authed:
                    self._broadcaster.unsubscribe(client_id)
            try:
                conn.close()
            except Exception:
                pass
            if authed:
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
                # 原样回显载荷, 让主控端算出真实 RTT
                send_fn(MSG_HEARTBEAT, payload)
            elif mtype == MSG_CONTROL:
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
            # MSG_SCREEN / MSG_CURSOR 被控端不接收, 忽略

    def _handle_control(self, payload, client_id=None, send_fn=None):
        """处理主控端控制指令: 画质/帧率/剪贴板开关/暂停"""
        try:
            cmd = payload.decode('utf-8')
        except Exception:
            _debug_log('[被控端] 控制指令解码失败')
            return
        try:
            if cmd.startswith(CTL_QUALITY):
                self._broadcaster.set_quality(int(cmd.split(':', 1)[1]))
            elif cmd.startswith(CTL_FPS):
                self._broadcaster.set_fps(int(cmd.split(':', 1)[1]))
            elif cmd.startswith(CTL_CLIPBOARD):
                self._set_clipboard_sync(cmd.split(':', 1)[1] == 'on', client_id, send_fn)
            elif cmd == CTL_PAUSE:
                self._broadcaster.set_paused(client_id, True)
                self.on_log(f'[被控端] [{client_id}] 画面推送已暂停')
            elif cmd == CTL_RESUME:
                self._broadcaster.set_paused(client_id, False)
                self.on_log(f'[被控端] [{client_id}] 画面推送已恢复')
            elif cmd == CTL_DEVICE_ID_QUERY:
                if send_fn:
                    send_fn(MSG_CONTROL, self.device_id.encode('utf-8'))
            elif cmd == CTL_SCREEN_INFO_REQ:
                # 主控端窗口就绪后主动索要, 解决"显示器信息先于窗口到达被丢弃"
                if client_id and self._broadcaster:
                    self._broadcaster.resend_screen_info(client_id)
            else:
                _debug_log(f'[被控端] 未知控制指令: {cmd!r}')
        except Exception as e:
            # 以前这里静默 pass, 导致 PAUSE 之类的指令不生效却毫无线索
            _debug_log(f'[被控端] 控制指令处理失败 cmd={cmd!r}: {e}')

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
        if client_id:
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
            print('[被控端] 密码已更新 (正在运行的实例会自动生效)')
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

    app = Controlled(pwd, port, max_clients=load_config().get('max_clients', 5))
    try:
        app.start()
    except KeyboardInterrupt:
        app.stop()
        print('[被控端] 已停止')
