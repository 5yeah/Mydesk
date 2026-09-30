# -*- coding: utf-8 -*-
"""MyDesk 配置管理: 设备ID、连接密码、端口、历史记录、用户设置

设计要点:
  - 所有 JSON 写入都是"临时文件 + os.replace"原子替换, 且带锁。
    旧实现直接 open(...,'w') 截断再写, 一旦与 GUI 线程并发就会写出半截 JSON,
    下次读取 json.load 失败后静默返回空 —— 表现为"设备列表凭空清空"。
  - 密码/对端密码用 Windows DPAPI (CryptProtectData) 加密后落盘,
    只有当前用户能解密; 机器不支持 DPAPI 时自动退回明文(保证可用)。
"""
import os
import json
import uuid
import base64
import datetime
import threading

DEFAULT_PORT = 7788
DISCOVERY_PORT = DEFAULT_PORT + 1  # UDP 设备发现固定端口 (与可变的 TCP 端口解耦)
MAX_HISTORY = 20  # 最多保存20条历史记录

CONFIG_DIR = os.path.join(os.path.expanduser('~'), '.mydesk')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

# 用户设置 (高级设置页面写入, 现在真的有代码消费它了)
DATA_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
DEVICE_LIST_FILE = os.path.join(DATA_DIR, 'device_list.json')

_file_lock = threading.RLock()
_settings_cache = None
_settings_mtime = None
_config_cache = None
_config_mtime = None

# ============ 原子读写 ============

def _atomic_write_json(path, obj):
    """原子写 JSON: 先写临时文件再 os.replace, 避免读到半截文件"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _read_json(path):
    """读 JSON; 文件损坏时备份并返回 None"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        # 保留坏文件供排查, 不要直接丢掉用户数据
        try:
            os.replace(path, path + '.corrupt')
        except OSError:
            pass
        return None


# 对外暴露的公共名字 (GUI 层写自己的数据文件时复用同一套原子读写)
atomic_write_json = _atomic_write_json
read_json = _read_json


# ============ 敏感字段加解密 (DPAPI) ============

_DPAPI_PREFIX = 'dpapi:'


def _dpapi_available():
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:
        return False


def protect_secret(text):
    """把明文口令加密成可存盘字符串; 无法加密时原样返回"""
    if not text:
        return text
    if not _dpapi_available():
        return text
    try:
        import win32crypt
        blob = win32crypt.CryptProtectData(
            text.encode('utf-8'), 'MyDesk', None, None, None, 0)
        return _DPAPI_PREFIX + base64.b64encode(blob).decode('ascii')
    except Exception:
        return text


def unprotect_secret(stored):
    """还原 protect_secret 的结果; 旧版明文数据原样返回"""
    if not stored:
        return stored
    if not isinstance(stored, str) or not stored.startswith(_DPAPI_PREFIX):
        return stored  # 旧版明文, 兼容
    try:
        import win32crypt
        blob = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode('utf-8')
    except Exception:
        return ''


# ============ config.json ============

def load_config():
    """读取 config.json (带 mtime 缓存)"""
    global _config_cache, _config_mtime
    with _file_lock:
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            return {}
        if _config_cache is not None and mtime == _config_mtime:
            return dict(_config_cache)
        cfg = _read_json(CONFIG_FILE) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        _config_cache = cfg
        _config_mtime = mtime
        return dict(cfg)


def save_config(cfg):
    """写入 config.json (原子 + 加锁)"""
    global _config_cache, _config_mtime
    with _file_lock:
        _atomic_write_json(CONFIG_FILE, cfg)
        _config_cache = dict(cfg)
        try:
            _config_mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            _config_mtime = None


def get_device_id():
    """获取/生成设备ID(9位数字)"""
    cfg = load_config()
    if 'device_id' not in cfg or not cfg['device_id']:
        import hashlib
        import platform
        mac = hex(uuid.getnode())
        hostname = platform.node()
        raw = f'{mac}_{hostname}'
        hash_val = hashlib.md5(raw.encode()).hexdigest()
        digits = ''.join([c for c in hash_val if c.isdigit()])[:9]
        if len(digits) < 9:
            digits = digits.ljust(9, '0')
        cfg['device_id'] = digits
        save_config(cfg)
    return cfg['device_id']


def get_password():
    """读取设备连接密码, 默认 123456"""
    cfg = load_config()
    if 'password_dpapi' in cfg:
        pwd = unprotect_secret(cfg['password_dpapi'])
        if pwd:
            return pwd
    return cfg.get('password', '123456')


def set_password(pwd):
    """设置连接密码 (DPAPI 加密落盘, 不再保存明文)"""
    cfg = load_config()
    protected = protect_secret(pwd)
    if protected and protected.startswith(_DPAPI_PREFIX):
        cfg['password_dpapi'] = protected
        cfg.pop('password', None)
    else:
        cfg['password'] = pwd          # DPAPI 不可用时退回明文
        cfg.pop('password_dpapi', None)
    save_config(cfg)


def get_port():
    """监听端口: 高级设置 > config.json > 默认值"""
    port = get_setting('port')
    if isinstance(port, int) and 1024 <= port <= 65535:
        return port
    return load_config().get('port', DEFAULT_PORT)


def get_discovery_port():
    """UDP 设备发现端口: 固定值, 与可变的 TCP 端口解耦。
    这样即使 TCP 端口因被占用而自动切换, 主控端仍能通过发现拿到真实端口,
    避免"端口漂移导致连不上"。"""
    return DISCOVERY_PORT


# ============ 用户设置 (settings.json) ============

DEFAULT_SETTINGS = {
    'autostart': False,
    'prevent_sleep': True,
    'show_tray': True,
    'save_path': os.path.join(os.path.expanduser('~'), 'Desktop', 'MyDesk_接收'),
    'port': DEFAULT_PORT,
    'lan_only': False,
    'encryption': 0,
    'quality': 65,          # 直接存 JPEG 画质数值(与滑杆一致)
    'fps': 30,
    'auto_quality': True,
    'access_password': '',
    'two_factor': False,
    'ip_whitelist': False,
    'whitelist_ips': '',
}


def load_settings():
    """读取用户设置 (带 mtime 缓存, 缺失项用默认值补齐)"""
    global _settings_cache, _settings_mtime
    with _file_lock:
        try:
            mtime = os.path.getmtime(SETTINGS_FILE)
        except OSError:
            mtime = None
        if _settings_cache is not None and mtime == _settings_mtime:
            return dict(_settings_cache)
        data = _read_json(SETTINGS_FILE) or {}
        if not isinstance(data, dict):
            data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        _settings_cache = merged
        _settings_mtime = mtime
        return dict(merged)


def save_settings(settings):
    """写入用户设置 (原子 + 加锁)"""
    global _settings_cache, _settings_mtime
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings or {})
    with _file_lock:
        _atomic_write_json(SETTINGS_FILE, merged)
        _settings_cache = merged
        try:
            _settings_mtime = os.path.getmtime(SETTINGS_FILE)
        except OSError:
            _settings_mtime = None


def get_setting(key, default=None):
    val = load_settings().get(key, default)
    return default if val is None else val


# ============ 历史记录功能 ============

def get_history():
    """获取连接历史记录列表 (密码字段自动解密后返回)"""
    out = []
    for item in load_config().get('history', []):
        it = dict(item)
        it['password'] = unprotect_secret(it.get('password', ''))
        out.append(it)
    return out


def add_history(target, password=None, host=None):
    """添加一条历史记录

    Args:
        target: 设备ID或IP地址
        password: 连接密码
        host: 实际连接的主机地址 (可选)
    """
    cfg = load_config()
    history = cfg.get('history', [])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prot = protect_secret(password) if password else ''

    for i, item in enumerate(history):
        if item.get('target') == target:
            if password:
                item['password'] = prot
            if host:
                item['host'] = host
            item['last_used'] = now
            # 移到最前面: 先 pop 再 insert 同一对象。
            # (原实现 pop(i) 后又取 history[i], pop 后索引已错位会取到别的元素)
            history.pop(i)
            history.insert(0, item)
            break
    else:
        history.insert(0, {
            'target': target,
            'password': prot,
            'host': host or '',
            'last_used': now,
        })

    if len(history) > MAX_HISTORY:
        history = history[:MAX_HISTORY]

    cfg['history'] = history
    save_config(cfg)
    return history


def remove_history(target):
    """删除一条历史记录"""
    cfg = load_config()
    history = [item for item in cfg.get('history', [])
               if item.get('target') != target]
    cfg['history'] = history
    save_config(cfg)
    return history


def clear_history():
    """清空所有历史记录"""
    cfg = load_config()
    cfg['history'] = []
    save_config(cfg)
