# -*- coding: utf-8 -*-
"""MyDesk 主控端 (客户端)

阶段1: 连接 + 密码认证 + 心跳
阶段3: 键鼠事件发送
阶段4: 文件传输 (双向)

所有 socket 写操作通过 self.send() 加锁, 保证屏幕/输入/文件/心跳
多线程并发写不交错。
"""
import socket
import threading
import time
import os
import datetime
from protocol import (
    send_msg, recv_msg, hash_password,
    MSG_AUTH, MSG_HEARTBEAT, MSG_SCREEN, MSG_INPUT, MSG_FILE, MSG_CONTROL,
)
from file_transfer import FileTransfer, FT_OFFER, FT_ACCEPT, FT_DATA, FT_DONE, FT_PULL, FT_ERROR

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

def _debug_log(msg):
    """写入调试日志文件"""
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
        self._hb_thread = None
        self._send_lock = threading.Lock()   # socket 写锁
        self._recv_started = False   # 接收循环是否已启动
        self.file_transfer = FileTransfer(self.send, recv_dir)

    # ---------- 连接 ----------
    def connect(self, host, port, password, timeout=5):
        """连接到被控端 (带认证超时)
        
        Args:
            timeout: 单个网络操作的超时时间(秒)
        """
        _debug_log(f'[Controller] 开始连接: {host}:{port}')
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Step 1: TCP连接 (超时保护)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect((host, port))
            _debug_log(f'[Controller] TCP连接成功: {host}:{port}')
        except socket.timeout:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] TCP连接超时: {host}:{port}')
            raise Exception(f'连接 {host}:{port} 超时 (TCP握手失败)')
        except ConnectionRefusedError:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] TCP连接被拒绝: {host}:{port}')
            raise Exception(f'连接被拒绝: {host}:{port} 端口未监听 (请确认对方已启动MyDesk)')
        except OSError as e:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] TCP连接失败: {host}:{port}, error={e}')
            raise Exception(f'连接失败: {e}')

        # Step 2: 发送认证 (保持超时)
        try:
            send_msg(self.sock, MSG_AUTH, hash_password(password))
            _debug_log(f'[Controller] 已发送认证请求')
        except Exception as e:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] 发送认证失败: {e}')
            raise Exception(f'发送认证失败: {e}')

        # Step 3: 等待认证响应 (保持超时, 关键修复！)
        try:
            msg = recv_msg(self.sock)
            _debug_log(f'[Controller] 收到认证响应: msg={msg[0] if msg else None}')
        except socket.timeout:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] 认证超时')
            raise Exception(f'认证超时: {host}:{port} 未响应 (可能不是MyDesk服务)')

        if msg is None:
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] 认证失败: 连接被对方关闭')
            raise Exception('认证失败: 连接被对方关闭')
        if msg[0] != MSG_AUTH or msg[1] != b'\x01':
            self.sock.close()
            self.sock = None
            _debug_log(f'[Controller] 认证失败: 密码错误')
            raise Exception('密码错误或认证失败')

        # 认证成功后才取消超时，进入消息循环模式
        self.sock.settimeout(None)
        self.running = True
        self.connected = True
        _debug_log(f'[Controller] 认证成功, 启动接收线程')
        # 启动心跳保活
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        # 启动接收线程 (只有一个)
        self._recv_thread = threading.Thread(target=self._recv_loop_entry, daemon=True)
        self._recv_thread.start()
        return True
    
    def _recv_loop_entry(self):
        """接收循环入口"""
        _debug_log(f'[Controller] 接收线程已启动, handlers数量={len(self._msg_handlers)}')
        try:
            self.recv_loop()
        except Exception as e:
            _debug_log(f'[Controller] 接收循环异常: {e}')
        finally:
            self.connected = False
            self._recv_started = False
            _debug_log(f'[Controller] 接收线程已退出')

    def set_handler(self, fn):
        """注册消息回调 (支持多个): fn(mtype, payload)"""
        if fn not in self._msg_handlers:
            self._msg_handlers.append(fn)
            _debug_log(f'[Controller] 注册handler: {fn.__name__ if hasattr(fn, "__name__") else fn}, 总handlers={len(self._msg_handlers)}')
    
    def remove_handler(self, fn):
        """移除消息回调"""
        if fn in self._msg_handlers:
            self._msg_handlers.remove(fn)

    def send(self, mtype, payload=b''):
        """线程安全的消息发送"""
        if not self.sock:
            return
        try:
            with self._send_lock:
                send_msg(self.sock, mtype, payload)
        except Exception:
            self.connected = False

    def _heartbeat_loop(self):
        while self.running:
            try:
                self.send(MSG_HEARTBEAT)
                time.sleep(5)
            except Exception:
                self.connected = False
                print('[主控端] 连接断开')
                break

    def recv_loop(self):
        """接收消息循环, 通常在工作线程中调用"""
        msg_count = 0
        _debug_log(f'[Controller] recv_loop 开始运行')
        while self.running:
            msg = recv_msg(self.sock)
            if msg is None:
                self.connected = False
                _debug_log(f'[Controller] 连接断开: recv_msg返回None')
                break
            mtype, payload = msg
            msg_count += 1
            
            # 记录前10条消息和所有屏幕/控制消息
            if msg_count <= 10 or mtype in (MSG_SCREEN, MSG_CONTROL):
                _debug_log(f'[Controller] 收到消息 #{msg_count}: mtype={mtype}, payload_len={len(payload)}')
            
            if mtype == MSG_HEARTBEAT:
                continue
            if mtype == MSG_FILE:
                try:
                    self.file_transfer.handle_message(payload)
                except Exception as e:
                    _debug_log(f'[Controller] 文件消息处理异常: {e}')
                continue
            # 调用所有注册的消息处理器
            for handler in self._msg_handlers:
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
        self.running = False
        self.connected = False
        self._recv_started = False  # 重置接收循环标记，允许重新连接
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


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