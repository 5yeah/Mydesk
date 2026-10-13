# -*- coding: utf-8 -*-
"""MyDesk 主控端 (客户端)

阶段1: 连接 + 挑战-响应认证 + 心跳(带 RTT 测量)
阶段3: 键鼠事件发送 (输入事件走"合并+专用线程", 绝不阻塞 GUI)
阶段4: 文件传输 (双向)

线程模型:
  - 接收线程 recv_loop     : 唯一读取 socket 的线程
  - 心跳线程 _heartbeat_loop: 每 5 秒发一次带时间戳的心跳, 用于 RTT
  - 输入线程 _input_loop    : 消费键鼠事件, 鼠标移动做"只保留最新一帧"的合并
  - 文件发送线程            : 由 FileTransfer 自行创建, 直接用 send() 同步写

所有 socket 写操作都通过 self._send_lock 串行化, 保证屏幕/输入/文件/心跳
多线程并发写不交错。
"""
import socket
import struct
import threading
import time
import os
import queue
import datetime
from collections import deque
from protocol import (
    send_msg, recv_msg,
    MSG_AUTH, MSG_HEARTBEAT, MSG_SCREEN, MSG_INPUT, MSG_FILE, MSG_CONTROL,
    MSG_CURSOR, EV_MOUSE_MOVE,
    AUTH_V2_REQ, AUTH_V2_CHALLENGE, AUTH_V2_RESPONSE, AUTH_OK,
    make_nonce, auth_response, rtt_payload, payload_to_rtt,
)
from file_transfer import (
    FileTransfer, FT_OFFER, FT_ACCEPT, FT_DATA, FT_DONE, FT_PULL, FT_ERROR,
)

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

# socket 发送超时(秒): 防止链路拥塞时写操作无限期挂死线程
SEND_TIMEOUT = 15
# 非鼠标移动类输入事件的队列上限(键按下/抬起、滚轮、点击)
INPUT_QUEUE_MAX = 1024
# 心跳间隔(秒); 被控端 30 秒无数据即判定掉线, 5 秒远小于该阈值
HEARTBEAT_INTERVAL = 5

# 首个 handler 注册前最多缓存多少条消息。
# 场景: 认证一完成被控端就开始推流/发显示器信息, 而主控端的会话窗口
# (ScreenReceiver) 要稍后才注册消息回调。若不缓存, 这段时间的帧与
# SCREEN: 信息会被直接丢掉 —— 表现为"窗口标题/分辨率要等下一帧才对"
# 甚至"显示器列表丢失"。这里做有上限的缓存并在注册时补发。
HANDLER_BUFFER_MAX = 64


def _debug_log(msg):
    """写入调试日志文件 (严禁在逐帧路径调用)"""
    try:
        log_file = os.path.join(LOG_DIR, 'viewer_debug.log')
        timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f'[{timestamp}] {msg}\n')
    except Exception:
        pass


class Controller:
    def __init__(self, recv_dir='.'):
        self.sock = None
        self.running = False
        self.connected = False
        self._msg_handlers = []      # 消息回调列表: [fn(mtype, payload), ...]
        self._disconnect_cbs = []    # 断开回调列表: [fn(), ...]
        self._hb_thread = None
        self._input_thread = None
        self._send_lock = threading.Lock()   # socket 写锁
        self._recv_started = False   # 接收循环是否已启动
        self._gen = 0                # 连接代数: 防止旧连接的线程误报断开
        self.file_transfer = FileTransfer(self.send, recv_dir)

        # ---- 输入事件通道 ----
        # 鼠标移动只保留"最新一条"(合并), 其余输入事件进有界队列保证按键语义不丢
        self._pending_move = None
        self._move_lock = threading.Lock()
        self._input_queue = queue.Queue(maxsize=INPUT_QUEUE_MAX)
        self._input_dropped = 0

        # ---- RTT ----
        self.last_rtt = None         # 最近一次心跳往返(秒)
        self._rtt_samples = deque(maxlen=5)

        # ---- 首个 handler 注册前的消息缓存 ----
        self._buf_lock = threading.Lock()
        self._handler_buffer = []    # [(mtype, payload), ...]
        self._buffering = True       # set_handler 首次调用后置 False

    # ---------- 连接 ----------
    def connect(self, host, port, password, timeout=5):
        """连接到被控端 (挑战-响应认证, 含超时保护)

        Args:
            timeout: 单个网络操作的超时时间(秒)
        """
        _debug_log(f'[Controller] 开始连接: {host}:{port}')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        # 发送超时: 只影响 send, 不影响 recv 的阻塞语义
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO,
                                 struct.pack('<i', int(SEND_TIMEOUT * 1000)))
        except Exception as e:
            _debug_log(f'[Controller] 设置发送超时失败(忽略): {e}')
        # 关闭 Nagle: 键鼠输入是小包, 不关的话会被攒到上一帧确认后才发, 增加操作延迟
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception as e:
            _debug_log(f'[Controller] 设置 TCP_NODELAY 失败(忽略): {e}')

        # Step 1: TCP 连接
        try:
            self.sock.connect((host, port))
            _debug_log(f'[Controller] TCP连接成功: {host}:{port}')
        except socket.timeout:
            self._close_sock()
            _debug_log(f'[Controller] TCP连接超时: {host}:{port}')
            raise Exception(f'连接 {host}:{port} 超时 (TCP握手失败)')
        except ConnectionRefusedError:
            self._close_sock()
            _debug_log(f'[Controller] TCP连接被拒绝: {host}:{port}')
            raise Exception(f'连接被拒绝: {host}:{port} 端口未监听 (请确认对方已启动MyDesk)')
        except OSError as e:
            self._close_sock()
            _debug_log(f'[Controller] TCP连接失败: {host}:{port}, error={e}')
            raise Exception(f'连接失败: {e}')

        try:
            # Step 2: 发送认证请求(带 client_nonce)
            client_nonce = make_nonce()
            send_msg(self.sock, MSG_AUTH, bytes([AUTH_V2_REQ]) + client_nonce)
            _debug_log('[Controller] 已发送认证请求(v2)')

            # Step 3: 收挑战(server_nonce)
            msg = recv_msg(self.sock)
            if msg is None:
                raise Exception('认证失败: 连接被对方关闭')
            mtype, payload = msg
            if mtype != MSG_AUTH or not payload or payload[0] != AUTH_V2_CHALLENGE \
                    or len(payload) < 1 + 16:
                raise Exception('认证失败: 对端不支持新版认证协议 '
                                '(请把两台设备的 MyDesk 都更新到同一版本)')
            server_nonce = payload[1:1 + 16]

            # Step 4: 发送 HMAC 响应
            resp = auth_response(password, client_nonce, server_nonce)
            send_msg(self.sock, MSG_AUTH, bytes([AUTH_V2_RESPONSE]) + resp)

            # Step 5: 收认证结果
            msg = recv_msg(self.sock)
            if msg is None:
                raise Exception('认证失败: 连接被对方关闭')
            if msg[0] != MSG_AUTH or msg[1] != AUTH_OK:
                raise Exception('密码错误或认证失败')
        except socket.timeout:
            self._close_sock()
            _debug_log('[Controller] 认证超时')
            raise Exception(f'认证超时: {host}:{port} 未响应 (可能不是MyDesk服务)')
        except Exception:
            self._close_sock()
            raise

        # 认证成功: 进入消息循环模式
        self.sock.settimeout(None)
        with self._send_lock:
            self._gen += 1
            gen = self._gen
        self.running = True
        self.connected = True
        self._input_dropped = 0
        self.last_rtt = None
        self._rtt_samples.clear()
        with self._move_lock:
            self._pending_move = None
        self._drain_input_queue()
        # 无 handler 时重新开启"注册前缓存", 并清掉上一轮的残留
        with self._buf_lock:
            self._handler_buffer = []
            self._buffering = not self._msg_handlers
        _debug_log('[Controller] 认证成功, 启动接收/心跳/输入线程')

        self._hb_thread = threading.Thread(target=self._heartbeat_loop, args=(gen,), daemon=True)
        self._hb_thread.start()
        self._recv_thread = threading.Thread(target=self._recv_loop_entry, args=(gen,), daemon=True)
        self._recv_thread.start()
        self._input_thread = threading.Thread(target=self._input_loop, args=(gen,), daemon=True)
        self._input_thread.start()
        return True

    def _close_sock(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _recv_loop_entry(self, gen):
        """接收循环入口"""
        _debug_log(f'[Controller] 接收线程已启动(gen={gen}), handlers={len(self._msg_handlers)}')
        try:
            self.recv_loop()
        except Exception as e:
            _debug_log(f'[Controller] 接收循环异常: {e}')
        finally:
            self.connected = False
            self._recv_started = False
            _debug_log('[Controller] 接收线程已退出')
            self._notify_disconnected(gen)

    def _notify_disconnected(self, gen):
        """通知 UI 连接已断开 (幂等: 只有当前代的线程能触发)"""
        with self._send_lock:
            if gen != self._gen:
                return
            self.connected = False
            self.running = False
            self._gen += 1        # 让本代其它线程的回调失效
        # 清理未完成的文件传输状态 (保留 .part 残文件以便续传)
        try:
            self.file_transfer.reset()
        except Exception as e:
            _debug_log(f'[Controller] 重置文件传输状态失败: {e}')
        for fn in list(self._disconnect_cbs):
            try:
                fn()
            except Exception as e:
                _debug_log(f'[Controller] 断开回调异常: {e}')

    # ---------- 回调注册 ----------
    def set_handler(self, fn):
        """注册消息回调 (支持多个): fn(mtype, payload)

        首次注册时会把"注册之前"收到的消息补发一次, 避免丢帧/丢显示器信息。
        """
        if fn in self._msg_handlers:
            return
        self._msg_handlers.append(fn)
        _debug_log(f'[Controller] 注册handler: '
                   f'{fn.__name__ if hasattr(fn, "__name__") else fn}, '
                   f'总handlers={len(self._msg_handlers)}')

        # 补发缓存 (只做一次)
        with self._buf_lock:
            if not self._buffering:
                return
            self._buffering = False
            buffered = self._handler_buffer
            self._handler_buffer = []
        if buffered:
            _debug_log(f'[Controller] 补发 {len(buffered)} 条注册前缓存的消息')
            for mtype, payload in buffered:
                for h in list(self._msg_handlers):
                    try:
                        h(mtype, payload)
                    except Exception as e:
                        _debug_log(f'[Controller] 补发处理器异常: {e}')

    def _buffer_message(self, mtype, payload):
        """缓存注册前的消息; 同类可合并的消息只保留最新一条, 防止积压"""
        with self._buf_lock:
            if not self._buffering:
                return
            # 屏幕帧/光标位置只保留最新一条(旧帧没有价值)
            if mtype in (MSG_SCREEN, MSG_CURSOR):
                self._handler_buffer = [
                    (t, p) for t, p in self._handler_buffer if t != mtype]
            self._handler_buffer.append((mtype, payload))
            if len(self._handler_buffer) > HANDLER_BUFFER_MAX:
                # 超限时丢最旧的(优先丢屏幕帧)
                drop_idx = next((i for i, (t, _p) in enumerate(self._handler_buffer)
                                 if t == MSG_SCREEN), 0)
                self._handler_buffer.pop(drop_idx)

    def remove_handler(self, fn):
        """移除消息回调

        当所有 handler 都被移除(会话窗口关闭)后重新打开"注册前缓存",
        这样下一次连接期间的帧/信息仍会被保留到新窗口注册为止。
        """
        if fn in self._msg_handlers:
            self._msg_handlers.remove(fn)
        if not self._msg_handlers:
            with self._buf_lock:
                self._handler_buffer = []
                self._buffering = True

    def add_disconnect_callback(self, fn):
        """注册断开回调 (在接收线程中被调用, 实现方需自行切回 GUI 线程)"""
        if fn not in self._disconnect_cbs:
            self._disconnect_cbs.append(fn)

    def remove_disconnect_callback(self, fn):
        if fn in self._disconnect_cbs:
            self._disconnect_cbs.remove(fn)

    # ---------- 发送 ----------
    def send(self, mtype, payload=b''):
        """线程安全的消息发送 (同步, 用于文件/心跳/控制指令)

        带 SEND_TIMEOUT 的写超时, 不会无限期挂死调用线程。
        """
        if not self.sock:
            return False
        try:
            with self._send_lock:
                if not self.sock:
                    return False
                send_msg(self.sock, mtype, payload)
            return True
        except Exception as e:
            _debug_log(f'[Controller] 发送失败: {e}')
            self._mark_dead()
            return False

    def send_input(self, payload):
        """键鼠事件专用通道: 绝不阻塞调用线程(GUI 线程)。

        区分两类:
          - 鼠标移动: 只保留最新一条(覆盖), 慢链路时自动丢旧帧
          - 其余(按下/抬起/滚轮/按键): 进有界队列, 保证按键语义不被鼠标移动冲掉
        """
        if not self.connected:
            return
        # 鼠标移动只保留最新一条; 事件类型定义见 protocol.EV_*
        if payload and payload[0] == EV_MOUSE_MOVE:
            with self._move_lock:
                self._pending_move = payload
            return
        try:
            self._input_queue.put_nowait(payload)
        except queue.Full:
            # 队列已满说明链路严重拥塞; 丢最旧的一条以免内存无界增长
            try:
                self._input_queue.get_nowait()
                self._input_queue.put_nowait(payload)
                self._input_dropped += 1
            except (queue.Empty, queue.Full):
                pass

    def _drain_input_queue(self):
        while True:
            try:
                self._input_queue.get_nowait()
            except queue.Empty:
                return

    def _input_loop(self, gen):
        """输入事件发送线程

        每轮最多发 "1 条最新鼠标移动 + 1 条其它事件":
          - 先发移动, 保证"先移动到位、再按下"的顺序
          - 再取一条非移动事件, 保证连续拖动鼠标时点击/按键不会被饿死
        """
        while True:
            with self._send_lock:
                if gen != self._gen:
                    break
            try:
                did_work = False

                with self._move_lock:
                    move = self._pending_move
                    self._pending_move = None
                if move is not None:
                    if not self.send(MSG_INPUT, move):
                        break
                    did_work = True

                try:
                    payload = self._input_queue.get_nowait()
                except queue.Empty:
                    payload = None
                if payload is not None:
                    if not self.send(MSG_INPUT, payload):
                        break
                    did_work = True

                if not did_work:
                    try:
                        payload = self._input_queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if not self.send(MSG_INPUT, payload):
                        break
            except Exception as e:
                _debug_log(f'[Controller] 输入发送线程异常: {e}')
                break
        _debug_log(f'[Controller] 输入发送线程退出(gen={gen})')

    def _heartbeat_loop(self, gen):
        while True:
            with self._send_lock:
                if gen != self._gen:
                    break
            try:
                if not self.send(MSG_HEARTBEAT, rtt_payload()):
                    break
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception:
                break
        _debug_log(f'[Controller] 心跳线程退出(gen={gen})')

    def _mark_dead(self):
        """写失败时立刻判定连接断开(不等接收线程)"""
        with self._send_lock:
            gen = self._gen
            if not self.running and not self.connected:
                return
        self._notify_disconnected(gen)

    def recv_loop(self):
        """接收消息循环, 在工作线程中调用"""
        msg_count = 0
        _debug_log('[Controller] recv_loop 开始运行')
        while self.running:
            try:
                msg = recv_msg(self.sock)
            except Exception as e:
                _debug_log(f'[Controller] 接收异常: {e}')
                break
            if msg is None:
                _debug_log('[Controller] 连接断开: recv_msg 返回 None')
                break
            mtype, payload = msg
            msg_count += 1

            if mtype == MSG_HEARTBEAT:
                # 载荷是本地发出时的时间戳, 被控端原样回显 -> 算真实 RTT
                rtt = payload_to_rtt(payload)
                if rtt is not None:
                    self._rtt_samples.append(rtt)
                    self.last_rtt = sum(self._rtt_samples) / len(self._rtt_samples)
                continue
            if mtype == MSG_FILE:
                try:
                    self.file_transfer.handle_message(payload)
                except Exception as e:
                    _debug_log(f'[Controller] 文件消息处理异常: {e}')
                continue
            # 还没有 handler (会话窗口尚未创建) -> 先缓存, 注册时补发
            if not self._msg_handlers:
                self._buffer_message(mtype, payload)
                continue
            # 调用所有注册的消息处理器
            for handler in list(self._msg_handlers):
                try:
                    handler(mtype, payload)
                except Exception as e:
                    _debug_log(f'[Controller] 处理器异常: {e}')

        _debug_log(f'[Controller] recv_loop 已退出, 共接收{msg_count}条消息')

    # ---------- 文件传输便捷方法 ----------
    def upload_file(self, filepath):
        self.file_transfer.upload(filepath)

    def download_file(self, remote_name):
        self.file_transfer.download(remote_name)

    def disconnect(self):
        """主动断开: 停止所有线程并通知 UI"""
        with self._send_lock:
            gen = self._gen
            was_alive = self.running or self.connected
        self.running = False
        self.connected = False
        self._recv_started = False
        self._close_sock()
        if was_alive:
            self._notify_disconnected(gen)


if __name__ == '__main__':
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7788
    pwd = sys.argv[3] if len(sys.argv) > 3 else '123456'
    c = Controller()
    c.connect(host, port, pwd)
    print('[主控端] 连接成功, 5秒后断开')
    time.sleep(5)
    c.disconnect()
