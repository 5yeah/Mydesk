# -*- coding: utf-8 -*-
"""MyDesk 配置管理: 设备ID、连接密码、端口、历史记录等"""
import os
import json
import uuid
import datetime

DEFAULT_PORT = 7788
DISCOVERY_PORT = DEFAULT_PORT + 1  # UDP 设备发现固定端口 (与可变的 TCP 端口解耦)
MAX_HISTORY = 20  # 最多保存20条历史记录

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.mydesk')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_device_id():
    """获取/生成设备ID(9位数字)"""
    cfg = load_config()
    if 'device_id' not in cfg:
        # 生成9位数字ID
        import hashlib
        import platform
        mac = hex(uuid.getnode())
        hostname = platform.node()
        raw = f'{mac}_{hostname}'
        hash_val = hashlib.md5(raw.encode()).hexdigest()
        # 取数字部分，补零到9位
        digits = ''.join([c for c in hash_val if c.isdigit()])[:9]
        if len(digits) < 9:
            digits = digits.ljust(9, '0')
        cfg['device_id'] = digits
        save_config(cfg)
    return cfg['device_id']


def get_password():
    """读取设备连接密码, 默认 123456"""
    return load_config().get('password', '123456')


def set_password(pwd):
    cfg = load_config()
    cfg['password'] = pwd
    save_config(cfg)


def get_port():
    return load_config().get('port', DEFAULT_PORT)


def get_discovery_port():
    """UDP 设备发现端口: 固定值, 与可变的 TCP 端口解耦。
    这样即使 TCP 端口因被占用而自动切换, 主控端仍能通过发现拿到真实端口,
    避免"端口漂移导致连不上"。"""
    return DISCOVERY_PORT


# ============ 历史记录功能 ============

def get_history():
    """获取连接历史记录列表"""
    cfg = load_config()
    return cfg.get('history', [])


def add_history(target, password=None, host=None):
    """添加一条历史记录
    
    Args:
        target: 设备ID或IP地址
        password: 连接密码
        host: 实际连接的主机地址 (可选)
    """
    cfg = load_config()
    history = cfg.get('history', [])
    
    # 检查是否已存在 (相同的target)
    for i, item in enumerate(history):
        if item.get('target') == target:
            # 更新现有记录
            item['password'] = password or item.get('password', '')
            item['host'] = host or item.get('host', '')
            item['last_used'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # 移到最前面: 先 pop 再 insert 同一对象。
            # (原实现 pop(i) 后又取 history[i], pop 后索引已错位会取到别的元素)
            history.pop(i)
            history.insert(0, item)
            break
    else:
        # 添加新记录
        record = {
            'target': target,
            'password': password or '',
            'host': host or '',
            'last_used': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        history.insert(0, record)
    
    # 限制历史记录数量
    if len(history) > MAX_HISTORY:
        history = history[:MAX_HISTORY]
    
    cfg['history'] = history
    save_config(cfg)
    return history


def remove_history(target):
    """删除一条历史记录"""
    cfg = load_config()
    history = cfg.get('history', [])
    history = [item for item in history if item.get('target') != target]
    cfg['history'] = history
    save_config(cfg)
    return history


def clear_history():
    """清空所有历史记录"""
    cfg = load_config()
    cfg['history'] = []
    save_config(cfg)
