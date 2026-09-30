# -*- coding: utf-8 -*-
"""MyDesk 共享屏幕广播器 (支持多显示器)

支持多客户端共享同一个屏幕捕获源, 避免每个连接都重复捕获。
支持多显示器: 检测所有显示器并独立捕获, 每个屏幕作为独立流发送。
ScreenBroadcaster:
  - 一个 mss 实例 + 多个捕获线程 (每个显示器一个)
  - 每帧编码后, 同时推送给所有订阅的客户端
  - 客户端断开时自动取消订阅
  - 支持按客户端暂停推送 (PAUSE/RESUME)
  - 屏幕帧格式: [屏幕索引 4字节][JPEG数据]
  - 显示器信息格式: SCREEN:{idx}:{w}x{h}:{left}:{top}   (left/top 为虚拟桌面偏移)
"""
import ctypes
import io
import struct
import threading
import time
import os
import datetime
import mss
from PIL import Image
from protocol import MSG_SCREEN, MSG_CONTROL, MSG_CURSOR

# 日志目录
LOG_DIR = os.path.join(os.path.expanduser('~'), 'MyDesk_Logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 无订阅者时的日志节流间隔(秒)
IDLE_LOG_INTERVAL = 60.0
# 光标位置推送间隔(秒)
CURSOR_INTERVAL = 0.04


def _debug_log(msg):
    """写入调试日志文件 (严禁在逐帧路径调用)"""
    try:
        log_file = os.path.join(LOG_DIR, 'controlled_debug.log')
        timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f'[{timestamp}] {msg}\n')
    except Exception:
        pass


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("hCursor", ctypes.c_void_p),
                ("ptScreenPos", ctypes.c_long * 2)]


_CURSOR_SHOWING = 0x00000001


def get_cursor_state():
    """返回 (虚拟桌面x, 虚拟桌面y, 是否可见); 失败返回 None。

    mss 在 Windows 上不抓光标, 所以光标位置必须单独采集并推给主控端绘制。
    """
    try:
        ci = _CURSORINFO()
        ci.cbSize = ctypes.sizeof(_CURSORINFO)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)):
            return None
        return int(ci.ptScreenPos[0]), int(ci.ptScreenPos[1]), \
            bool(ci.flags & _CURSOR_SHOWING)
    except Exception:
        return None


class ScreenBroadcaster:
    """屏幕广播器 (每个被控端实例独立)

    用法:
      bc = ScreenBroadcaster(on_log)
      bc.start()                        # 启动
      bc.subscribe(client_id, send_fn)  # 客户端订阅
      bc.unsubscribe(client_id)         # 客户端断开
      bc.set_paused(client_id, True)    # 暂停向该客户端推送
    """

    def __init__(self, on_log=None):
        self.on_log = on_log or print
        self.quality = 65
        self.fps = 30
        self.running = False
        self._sct = None
        self._monitors = []  # 所有显示器列表 (主显示器在首位)
        self._threads = []  # 每个显示器的捕获线程
        self._cfg_lock = threading.Lock()
        # 订阅者: {client_id: send_fn}
        self._subscribers = {}
        # 已暂停推送的客户端
        self._paused = set()
        # 逐客户端发送锁: 用于"丢帧不排队", 防止慢客户端阻塞整个捕获循环
        self._sub_locks = {}
        self._sub_lock = threading.Lock()
        # 屏幕信息: [(idx, w, h, left, top), ...]
        self._screen_info = []
        self._cursor_thread = None
        # 新客户端订阅后需要"强制再发一次"光标位置: 光标线程只在位置变化时才推,
        # 若用户没动鼠标, 后连接的客户端会一直看不到远端光标。
        self._cursor_dirty = True

    def start(self):
        if self.running:
            _debug_log('[屏幕广播] start: 已在运行')
            return True
        try:
            _debug_log('[屏幕广播] start: 开始初始化 mss')
            self._sct = mss.MSS()
            # 获取所有显示器 (跳过索引0的"全屏虚拟桌面")
            self._monitors = list(self._sct.monitors[1:])
            _debug_log(f'[屏幕广播] 检测到 {len(self._monitors)} 个显示器')
            if not self._monitors:
                raise RuntimeError('未检测到任何显示器')

            # 主显示器排到首位, 让 idx=0 与"主屏"语义一致
            primary_idx = 0
            for i, m in enumerate(self._monitors):
                if m.get('is_primary'):
                    primary_idx = i
                    break
            if primary_idx > 0:
                primary = self._monitors.pop(primary_idx)
                self._monitors.insert(0, primary)

            # 保存屏幕信息 (含虚拟桌面偏移, 供主控端做坐标换算)
            self._screen_info = [
                (i, m['width'], m['height'], m.get('left', 0), m.get('top', 0))
                for i, m in enumerate(self._monitors)
            ]
            _debug_log(f'[屏幕广播] 屏幕信息: {self._screen_info}')

            self.running = True

            # 为每个显示器启动捕获线程
            for i, mon in enumerate(self._monitors):
                t = threading.Thread(target=self._capture_loop, args=(i, mon), daemon=True)
                t.start()
                self._threads.append(t)

            # 光标位置推送线程 (mss 不抓光标, 需单独发送)
            self._cursor_thread = threading.Thread(target=self._cursor_loop, daemon=True)
            self._cursor_thread.start()

            n_screens = len(self._monitors)
            self.on_log(f'[屏幕] 广播器启动, 检测到 {n_screens} 个显示器')
            for idx, w, h, left, top in self._screen_info:
                self.on_log(f'[屏幕]   屏幕{idx + 1}: {w}x{h} (虚拟偏移 {left},{top})')
            return True
        except Exception as e:
            _debug_log(f'[屏幕广播] start 异常: {e}')
            self.on_log(f'[屏幕] 启动失败: {e}')
            self.running = False
            return False

    def stop(self):
        self.running = False
        for t in self._threads:
            t.join(timeout=1)
        self._threads = []
        if self._cursor_thread:
            self._cursor_thread.join(timeout=0.5)
            self._cursor_thread = None
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        with self._sub_lock:
            self._subscribers.clear()
            self._sub_locks.clear()
            self._paused.clear()

    # ---------- 订阅管理 ----------
    def subscribe(self, client_id, send_fn):
        """客户端订阅屏幕流, 立即发送屏幕尺寸和多屏幕信息"""
        _debug_log(f'[屏幕广播] subscribe: client_id={client_id}')
        with self._sub_lock:
            self._subscribers[client_id] = send_fn
            self._sub_locks[client_id] = threading.Lock()
            self._paused.discard(client_id)
            # 强制光标线程立刻给这个新客户端推一次当前位置
            self._cursor_dirty = True

        self._send_screen_info(send_fn)
        self.on_log(f'[屏幕] +订阅 {client_id} (共 {self._sub_count()} 个)')

    def resend_screen_info(self, client_id):
        """按需重发显示器信息 (主控端窗口就绪后调用, 消除"信息早于窗口"的竞争)"""
        with self._sub_lock:
            send_fn = self._subscribers.get(client_id)
        if send_fn is None:
            return False
        self._send_screen_info(send_fn)
        return True

    def _send_screen_info(self, send_fn):
        """发送每个屏幕的尺寸+虚拟桌面偏移; 多屏时额外发送屏幕列表"""
        for idx, w, h, left, top in self._screen_info:
            try:
                # "SCREEN:{idx}:{w}x{h}:{left}:{top}"
                send_fn(MSG_CONTROL,
                        f'SCREEN:{idx}:{w}x{h}:{left}:{top}'.encode('utf-8'))
            except Exception as e:
                _debug_log(f'[屏幕广播] 发送屏幕信息失败: {e}')

        if len(self._screen_info) > 1:
            try:
                parts = [f'{idx}:{w}:{h}:{left}:{top}'
                         for idx, w, h, left, top in self._screen_info]
                send_fn(MSG_CONTROL, f"SCREEN_LIST:{','.join(parts)}".encode('utf-8'))
            except Exception as e:
                _debug_log(f'[屏幕广播] 发送屏幕列表失败: {e}')

    def unsubscribe(self, client_id):
        with self._sub_lock:
            self._subscribers.pop(client_id, None)
            self._sub_locks.pop(client_id, None)
            self._paused.discard(client_id)
        self.on_log(f'[屏幕] -订阅 {client_id} (剩 {self._sub_count()} 个)')

    def set_paused(self, client_id, paused):
        """暂停/恢复向某个客户端推送画面 (该客户端仍保持订阅, 不影响其他人)"""
        with self._sub_lock:
            if paused:
                self._paused.add(client_id)
            else:
                self._paused.discard(client_id)
        _debug_log(f'[屏幕广播] set_paused {client_id} -> {paused}')

    def _sub_count(self):
        with self._sub_lock:
            return len(self._subscribers)

    def get_subscribers(self):
        with self._sub_lock:
            return list(self._subscribers.keys())

    def get_screen_count(self):
        return len(self._monitors)

    def get_screen_info(self):
        return list(self._screen_info)

    # ---------- 捕获循环 (每个显示器独立) ----------
    def _capture_loop(self, screen_idx, monitor):
        """单个显示器的捕获循环 (每个线程有自己的 mss 实例)"""
        fail_count = 0
        w = monitor['width']
        h = monitor['height']
        self.on_log(f'[屏幕] 屏幕{screen_idx + 1} 捕获线程启动 ({w}x{h})')

        # 每个线程创建自己的 mss 实例 (mss 不是线程安全的)
        try:
            sct = mss.MSS()
        except Exception as e:
            _debug_log(f'[屏幕广播] 创建mss实例失败: {e}')
            self.on_log(f'[屏幕] 屏幕{screen_idx + 1} 创建mss实例失败: {e}')
            return

        frame_count = 0
        last_idle_log = 0.0
        try:
            while self.running:
                with self._sub_lock:
                    active = len(self._subscribers)
                    paused_all = active > 0 and len(self._paused) >= active
                    idle = active == 0
                if idle or paused_all:
                    # 无人订阅 / 所有人都暂停: 不抓屏不编码, 节省 CPU
                    if idle and time.time() - last_idle_log >= IDLE_LOG_INTERVAL:
                        last_idle_log = time.time()
                        _debug_log(f'[屏幕广播] 屏幕{screen_idx + 1} 无订阅者, 待机中')
                    time.sleep(0.05)
                    continue

                t0 = time.time()
                try:
                    raw = sct.grab(monitor)
                    img = Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
                    buf = io.BytesIO()
                    with self._cfg_lock:
                        q = self.quality
                    img.save(buf, format='JPEG', quality=q)
                    jpeg = buf.getvalue()

                    # 构造帧数据: [屏幕索引 4字节][JPEG数据]
                    frame_data = struct.pack('<I', screen_idx) + jpeg

                    # 广播给所有订阅者
                    with self._sub_lock:
                        subs = [(cid, fn, self._sub_locks.get(cid), cid in self._paused)
                                for cid, fn in self._subscribers.items()]

                    dead = []
                    for cid, send_fn, lk, paused in subs:
                        if paused:
                            continue
                        # 非阻塞获取该客户端的帧锁: 若上一帧尚未发送完成(客户端慢/卡),
                        # 直接丢弃本帧, 绝不排队等待 —— 否则一个慢客户端会拖垮所有客户端。
                        if lk is not None and not lk.acquire(blocking=False):
                            continue
                        try:
                            send_fn(MSG_SCREEN, frame_data)
                        except Exception as e:
                            _debug_log(f'[屏幕广播] 发送帧失败: client={cid}, error={e}')
                            dead.append(cid)
                        finally:
                            if lk is not None:
                                lk.release()
                    if dead:
                        with self._sub_lock:
                            for cid in dead:
                                self._subscribers.pop(cid, None)
                                self._sub_locks.pop(cid, None)
                                self._paused.discard(cid)
                    fail_count = 0
                    frame_count += 1
                except (BrokenPipeError, ConnectionError, OSError) as e:
                    fail_count += 1
                    _debug_log(f'[屏幕广播] 捕获错误: {e}, fail_count={fail_count}')
                    if fail_count > 5:
                        self.on_log(f'[屏幕] 屏幕{screen_idx + 1} 捕获连续失败, 停止')
                        break
                except Exception as e:
                    _debug_log(f'[屏幕广播] 捕获异常: {e}')
                    self.on_log(f'[屏幕] 屏幕{screen_idx + 1} 捕获异常: {e}')
                    fail_count += 1
                    if fail_count > 10:
                        break

                # 帧率控制
                with self._cfg_lock:
                    target_fps = max(1, self.fps)
                elapsed = time.time() - t0
                interval = 1.0 / target_fps - elapsed
                if interval > 0:
                    time.sleep(interval)
        finally:
            try:
                sct.close()
            except Exception:
                pass
            _debug_log(f'[屏幕广播] 屏幕{screen_idx + 1} 捕获线程退出')

    # ---------- 光标位置 ----------
    def _cursor_loop(self):
        """轮询本机光标位置并推送给所有订阅者 (mss 不抓光标, 必须单独发)"""
        last = None
        while self.running:
            with self._sub_lock:
                fns = [] if not self._subscribers else \
                    [(cid, fn) for cid, fn in self._subscribers.items()
                     if cid not in self._paused]
                dirty = self._cursor_dirty
            if not fns:
                time.sleep(0.2)
                continue
            state = get_cursor_state()
            if state is None:
                time.sleep(0.2)
                continue
            # dirty: 有新客户端刚订阅 -> 即使光标没动也要推一次,
            # 否则后连接的客户端在用户不动鼠标前看不到远端光标
            if state != last or dirty:
                last = state
                with self._sub_lock:
                    self._cursor_dirty = False
                x, y, visible = state
                payload = struct.pack('<IIB', x & 0xFFFFFFFF, y & 0xFFFFFFFF,
                                      1 if visible else 0)
                for cid, fn in fns:
                    try:
                        fn(MSG_CURSOR, payload)
                    except Exception:
                        pass
            time.sleep(CURSOR_INTERVAL)

    # ---------- 配置 ----------
    def set_quality(self, q):
        with self._cfg_lock:
            self.quality = max(10, min(100, int(q)))
        self.on_log(f'[屏幕] 画质={self.quality}')

    def set_fps(self, fps):
        with self._cfg_lock:
            self.fps = max(1, min(60, int(fps)))
        self.on_log(f'[屏幕] 帧率={self.fps}')
