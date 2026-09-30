# -*- coding: utf-8 -*-
"""MyDesk 通信协议层

帧格式: [类型 1B][长度 4B][载荷 N B]  (小端)
所有收发都走 send_msg / recv_msg, 保证 TCP 粘包处理正确。
"""
import struct
import hashlib

# ===== 消息类型 =====
MSG_AUTH = 0x01          # 认证: 主控->被控(密码哈希); 被控->主控(0x01成功/0x00失败)
MSG_SCREEN = 0x02        # 屏幕帧: 被控->主控
MSG_INPUT = 0x03         # 键鼠事件: 主控->被控
MSG_FILE = 0x04          # 文件传输: 双向
MSG_HEARTBEAT = 0x05     # 心跳: 双向
MSG_CONTROL = 0x06       # 控制指令(画质/帧率/暂停等): 主控->被控
MSG_CLIPBOARD = 0x07     # 剪贴板文本同步: 双向 (载荷为 UTF-8 文本, 空载荷表示清空)

# 头部: 1 字节类型 + 4 字节载荷长度
_HEADER = struct.Struct('<BI')


def send_msg(sock, msg_type, payload=b''):
    """发送一条完整协议消息"""
    sock.sendall(_HEADER.pack(msg_type, len(payload)) + payload)


def recv_exact(sock, n):
    """精确读取 n 字节, 连接断开返回 None"""
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def recv_msg(sock):
    """接收一条协议消息, 返回 (msg_type, payload); 连接断开返回 None"""
    header = recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    msg_type, length = _HEADER.unpack(header)
    if length:
        payload = recv_exact(sock, length)
        if payload is None:
            return None
    else:
        payload = b''
    return msg_type, payload


def hash_password(password):
    """密码 SHA256 哈希, 避免明文传输"""
    return hashlib.sha256(password.encode('utf-8')).digest()