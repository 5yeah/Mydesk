# -*- coding: utf-8 -*-
"""MyDesk 通信协议层

帧格式: [类型 1B][长度 4B][载荷 N B]  (小端)
所有收发都走 send_msg / recv_msg, 保证 TCP 粘包处理正确。

认证 (v2, 挑战-响应, 防重放):
  1. 主控 -> 被控: MSG_AUTH [0x02][client_nonce 16B]
  2. 被控 -> 主控: MSG_AUTH [0x02][server_nonce 16B]
  3. 主控 -> 被控: MSG_AUTH [0x03][hmac 32B]
        hmac = HMAC_SHA256(key=sha256(password), msg=AUTH_CONTEXT + client_nonce + server_nonce)
  4. 被控 -> 主控: MSG_AUTH [0x01] 成功 / [0x00] 失败

  旧版直接把 sha256(password) 当凭据发送, 抓包即可重放; v2 每次连接的两个 nonce 都不同,
  重放旧响应无法通过校验。
"""
import struct
import hashlib
import hmac as _hmac
import os

# ===== 消息类型 =====
MSG_AUTH = 0x01          # 认证 (见上方 v2 流程)
MSG_SCREEN = 0x02        # 屏幕帧: 被控->主控  [屏号 4B][JPEG...]
MSG_INPUT = 0x03         # 键鼠事件: 主控->被控
MSG_FILE = 0x04          # 文件传输: 双向
MSG_HEARTBEAT = 0x05     # 心跳: 双向 (载荷为 8B double 发送时刻, 被控端原样回显用于算 RTT)
MSG_CONTROL = 0x06       # 控制指令(画质/帧率/剪贴板/暂停等): 主控->被控
MSG_CLIPBOARD = 0x07     # 剪贴板文本同步: 双向 (载荷为 UTF-8 文本, 空载荷表示清空)
MSG_CURSOR = 0x08        # 远端光标位置: 被控->主控  [x 4B][y 4B][visible 1B]

# ===== MSG_INPUT 事件子类型 (载荷第 0 字节) =====
# 放在协议层定义, 供 input_control(执行) 与 controller/viewer(发送) 共用, 避免魔法数字漂移
EV_MOUSE_MOVE = 0x01     # [x 2B][y 2B]      绝对坐标(虚拟桌面坐标系)
EV_MOUSE_DOWN = 0x02     # [button 1B]       1左 2右 3中
EV_MOUSE_UP = 0x03       # [button 1B]
EV_MOUSE_SCROLL = 0x04   # [dx 2B][dy 2B]    signed, 通常 dy=±120
EV_KEY_DOWN = 0x05       # [vk 2B]
EV_KEY_UP = 0x06         # [vk 2B]

# ===== MSG_CONTROL 指令前缀 (文本) =====
CTL_QUALITY = 'QUALITY:'      # QUALITY:<10-100>
CTL_FPS = 'FPS:'              # FPS:<1-60>
CTL_CLIPBOARD = 'CLIPBOARD:'  # CLIPBOARD:on / CLIPBOARD:off
CTL_PAUSE = 'PAUSE'           # 暂停向本客户端推送画面
CTL_RESUME = 'RESUME'         # 恢复
CTL_DEVICE_ID_QUERY = 'DEVICE_ID_QUERY'  # 认证前查询设备ID(局域网 TCP 兜底发现)
CTL_SCREEN_INFO_REQ = 'SCREEN_INFO_REQ'  # 主控端就绪后主动索要显示器信息(消除时序竞争)

# 头部: 1 字节类型 + 4 字节载荷长度
_HEADER = struct.Struct('<BI')

# ===== 认证子类型 =====
AUTH_V2_REQ = 0x02       # 主控->被控: 请求认证, 附 client_nonce
AUTH_V2_CHALLENGE = 0x02  # 被控->主控: 附 server_nonce
AUTH_V2_RESPONSE = 0x03  # 主控->被控: 附 hmac
AUTH_OK = b'\x01'
AUTH_FAIL = b'\x00'

AUTH_CONTEXT = b'MyDesk-Auth-v2'
NONCE_SIZE = 16
HMAC_SIZE = 32

# 单帧载荷长度上限 (防御性: 防止对端/坏包构造超长长度导致内存爆炸)
MAX_PAYLOAD = 64 * 1024 * 1024


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
    """接收一条协议消息, 返回 (msg_type, payload); 连接断开/长度非法返回 None"""
    header = recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    msg_type, length = _HEADER.unpack(header)
    if length > MAX_PAYLOAD:
        return None
    if length:
        payload = recv_exact(sock, length)
        if payload is None:
            return None
    else:
        payload = b''
    return msg_type, payload


# ===== 认证辅助 =====

def make_nonce():
    """生成 16 字节随机 nonce"""
    return os.urandom(NONCE_SIZE)


def auth_key(password):
    """由密码派生 HMAC 密钥 (仍不传输, 仅作 HMAC key)"""
    return hashlib.sha256(password.encode('utf-8')).digest()


def auth_response(password, client_nonce, server_nonce):
    """计算认证响应: HMAC_SHA256(sha256(password), 上下文+双 nonce)"""
    return _hmac.new(
        auth_key(password),
        AUTH_CONTEXT + bytes(client_nonce) + bytes(server_nonce),
        hashlib.sha256,
    ).digest()


def hash_password(password):
    """[已废弃] 旧版静态凭据, 保留仅为兼容外部脚本; 认证流程请用 auth_response()。

    静态哈希无 nonce, 可被抓包重放 —— 不要再用于认证。
    """
    return hashlib.sha256(password.encode('utf-8')).digest()


def rtt_payload():
    """心跳载荷: 当前时间戳 (double), 被控端原样回显"""
    import time
    return struct.pack('<d', time.time())


def payload_to_rtt(payload):
    """由回显的时间戳算出 RTT(秒); 载荷非法返回 None"""
    if not payload or len(payload) < 8:
        return None
    try:
        sent = struct.unpack('<d', payload[:8])[0]
    except struct.error:
        return None
    import time
    rtt = time.time() - sent
    # 服务器与客户端时钟无关(同一载荷往返), 但异常值仍做保护
    if rtt < 0 or rtt > 60:
        return None
    return rtt
