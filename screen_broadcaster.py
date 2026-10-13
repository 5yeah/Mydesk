# -*- coding: utf-8 -*-
"""MyDesk 共享屏幕广播器 (支持多显示器)

支持多客户端共享同一个屏幕捕获源, 避免每个连接都重复捕获。
支持多显示器: 检测所有显示器并独立捕获, 每个屏幕作为独立流发送。
抓屏后端 (性能关键):
  - 首选 dxcam (DXGI Desktop Duplication, 显卡直读): 实测单帧 <1ms
  - 回退 mss (GDI BitBlt): 实测单帧约 33ms, 是卡顿的最大瓶颈
  - 两套后端自动按坐标匹配同一块显示器, dxcam 任何环节失败自动回退 mss
ScreenBroadcaster:
  - 每个显示器一个捕获线程
  - 每帧编码后, 同时推送给所有订阅的客户端
  - 客户端断开时自动取消订阅
  - 支持按客户端暂停推送 (PAUSE/RESUME)
  - 只抓/只编"客户端正在看的那块屏"(SCREEN_SELECT), 双屏机器省一半开销
  - 画面完全没变时不编码不发送; 长时间空闲自动降频
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
# 画面长时间无变化时降到的帧率 (省 CPU/带宽; 任一端一有动作立刻恢复)
IDLE_AFTER = 5.0
IDLE_FPS = 3


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def system_idle_seconds():
    """本机"无真实键鼠输入"的秒数。

    注意: GetLastInputInfo 只反映**真实硬件输入**, 不反映 SendInput 注入的输入,
    所以调用方必须再结合"最近收到主控端输入的时间"一起判断, 见 note_activity()。
    """
    try:
        li = _LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount64()
        diff = int(tick) - int(li.dwTime)
        return max(0.0, diff / 1000.0)
    except Exception:
        return 0.0


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

# Windows 默认定时器精度 15.6ms, time.sleep(17ms) 实际会睡 16~31ms,
# 帧率控制会被拖到 20fps 左右。广播期间把精度提到 1ms (start/stop 成对)。
_timer_period_on = False


def _set_timer_precision(on):
    global _timer_period_on
    try:
        if on and not _timer_period_on:
            ctypes.windll.winmm.timeBeginPeriod(1)
            _timer_period_on = True
        elif not on and _timer_period_on:
            ctypes.windll.winmm.timeEndPeriod(1)
            _timer_period_on = False
    except Exception:
        pass


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
        self._dxcam = None  # dxcam 模块引用 (不可用时为 None)
        # dxcam 输出枚举结果: [(device_idx, output_idx, left, top), ...]
        self._dx_outputs = []
        self._dx_map = {}   # {screen_idx: (device_idx, output_idx)}
        self._cams = {}     # {screen_idx: dxcam 相机实例}
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
        # 每个客户端想看哪块屏 (idx; -1 = 不看画面)。无人指定 -> 全部屏幕都编(兼容旧客户端)
        self._client_screen = {}
        # 需要强制重发一帧的屏幕 (新订阅/切换屏幕时)
        self._frame_dirty = {}
        # 上一帧原始像素, 用于"画面没变就不编码"
        self._prev_bgra = {}
        # 最近一次"有活动"的时间: 真实输入或收到主控端输入都算
        self._last_activity = time.time()
        # 诊断计数: 每块屏实际编码了多少帧 / 因画面未变跳过了多少次
        self._encoded_counts = {}
        self._skipped_counts = {}

    def start(self):
        if self.running:
            _debug_log('[屏幕广播] start: 已在运行')
            return True
        try:
            _debug_log('[屏幕广播] start: 开始初始化抓屏后端')
            # ---- 先枚举 dxcam 输出 (必须早于 mss 枚举) ----
            # dxcam 初始化会设置进程 DPI 感知, 提前 import 保证
            # DXGI 坐标系与 mss 坐标系一致, 后续才能按 left/top 正确配对。
            self._dx_outputs = []
            self._dxcam = None
            try:
                import dxcam
                self._dxcam = dxcam
                # 注意: 类内直接写 dxcam.__factory 会被名称改写,
                # 必须用 getattr 拿到模块级的工厂实例
                factory = getattr(dxcam, '__factory')
                for dev_idx, outputs in enumerate(factory.outputs):
                    for out_idx, out in enumerate(outputs):
                        try:
                            rect = out.desc.DesktopCoordinates
                            if not out.attached_to_desktop:
                                continue
                            self._dx_outputs.append(
                                (dev_idx, out_idx, rect.left, rect.top))
                        except Exception:
                            continue
                _debug_log(f'[屏幕广播] dxcam 输出: {self._dx_outputs}')
            except Exception as e:
                _debug_log(f'[屏幕广播] dxcam 不可用, 全部回退 mss: {e}')
                self._dxcam = None
                self._dx_outputs = []

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

            # ---- 把 dxcam 输出按虚拟桌面坐标配对到 mss 显示器 ----
            self._dx_map = {}
            self._cams = {}
            if self._dxcam is not None:
                for i, m in enumerate(self._monitors):
                    left = m.get('left', 0)
                    top = m.get('top', 0)
                    for dev_idx, out_idx, dl, dt in self._dx_outputs:
                        if dl == left and dt == top:
                            self._dx_map[i] = (dev_idx, out_idx)
                            break
                # 每块屏创建一个 dxcam 相机并启动后台采集线程;
                # 任何一块失败就该屏回退 mss, 不影响其它屏。
                for i, (dev_idx, out_idx) in list(self._dx_map.items()):
                    try:
                        cam = self._dxcam.create(
                            device_idx=dev_idx,
                            output_idx=out_idx,
                            output_color='RGB',
                            # numpy 后端走编译好的 Cython 内核, 颜色已实测
                            # 与 mss 逐通道一致; 且不依赖 OpenCV(省 ~100MB 体积)
                            processor_backend='numpy',
                        )
                        if not getattr(cam, 'is_capturing', False):
                            cam.start(video_mode=True)  # target_fps 默认 60
                        self._cams[i] = cam
                    except Exception as e:
                        _debug_log(f'[屏幕广播] 屏幕{i + 1} dxcam 创建失败, '
                                   f'回退 mss: {e}')
                        self._dx_map.pop(i, None)
            n_dx = len(self._cams)
            self.on_log(f'[屏幕] 抓屏后端: {"dxcam(DXGI) x" + str(n_dx) if n_dx else ""}'
                        f'{" + " if n_dx else ""}mss x{len(self._monitors) - n_dx}')

            self.running = True
            _set_timer_precision(True)

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
        # 释放 dxcam 相机 (停止其后台采集线程, 下次 start 重建)
        for i, cam in list(self._cams.items()):
            try:
                cam.stop()
            except Exception:
                pass
            try:
                cam.release()
            except Exception:
                pass
        self._cams = {}
        self._dx_map = {}
        _set_timer_precision(False)
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
            self._client_screen.pop(client_id, None)
            # 强制光标线程立刻给这个新客户端推一次当前位置
            self._cursor_dirty = True
            # 所有屏幕都强制重发一帧, 保证新客户端立刻看到画面
            for idx, _w, _h, _l, _t in self._screen_info:
                self._frame_dirty[idx] = True

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
            self._client_screen.pop(client_id, None)
        self.on_log(f'[屏幕] -订阅 {client_id} (剩 {self._sub_count()} 个)')

    def set_client_screen(self, client_id, screen_idx):
        """客户端声明"我只看第 screen_idx 块屏"。

        这是省 CPU 的关键: 双屏机器上, 客户端只看一块屏时,
        另一块的抓屏+编码(实测约 50ms/帧)完全是白烧的。
        screen_idx = -1 表示不看画面(例如只做文件传输)。
        """
        with self._sub_lock:
            if client_id not in self._subscribers:
                return False
            self._client_screen[client_id] = int(screen_idx)
            if int(screen_idx) >= 0:
                self._frame_dirty[int(screen_idx)] = True
            self._cursor_dirty = True
        _debug_log(f'[屏幕广播] {client_id} 选择屏幕 {screen_idx}')
        return True

    def note_activity(self):
        """标记"有人在对这台机器操作"(主控端注入的输入也算), 用于解除降频"""
        self._last_activity = time.time()

    def _idle_seconds(self):
        """综合本机真实输入与主控端输入的空闲时长"""
        return min(system_idle_seconds(), time.time() - self._last_activity)

    def _wanted_screens(self):
        """返回"需要编码的屏幕集合"。

        - 返回 None  : 没有任何客户端声明过 -> 全部屏幕都编 (兼容旧客户端)
        - 返回 set() : 声明过但都选了 -1 -> 一块都不编 (例如只做文件传输)
        - 返回 {0}   : 只编第 0 块
        """
        # 调用方在读取订阅状态时已经持有 _sub_lock；这里不能再次加锁，
        # 否则 threading.Lock 会把捕获线程永久卡死，表现为连接成功但黑屏。
        declared = {cid: i for cid, i in self._client_screen.items()
                    if cid in self._subscribers}
        if not declared:
            return None
        return {i for i in declared.values() if i >= 0}

    def get_encode_stats(self):
        """诊断用: (每屏已编码帧数, 每屏因"画面未变"跳过的次数)"""
        with self._sub_lock:
            return dict(self._encoded_counts), dict(self._skipped_counts)

    def set_paused(self, client_id, paused):
        """暂停/恢复向某个客户端推送画面 (该客户端仍保持订阅, 不影响其他人)"""
        with self._sub_lock:
            if paused:
                self._paused.add(client_id)
            else:
                self._paused.discard(client_id)
                # 恢复后即使画面像素没变化，也必须补发一帧；否则优化路径
                # 会把恢复后的客户端永久留在“最后一帧/黑屏”状态。
                for idx, _w, _h, _l, _t in self._screen_info:
                    self._frame_dirty[idx] = True
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
        skipped_same = 0
        last_idle_log = 0.0
        # 诊断: 两种后端各出了多少帧 (退出时写日志)
        dx_frames = 0
        mss_frames = 0
        # dxcam 相机 (start() 已按屏幕配对好; None 表示该屏用 mss)
        cam = self._cams.get(screen_idx)
        # 上一帧的 DXGI 帧号: 帧号没变 = 显卡没有新画面 = 不用编码
        prev_ticks = None
        peek_fails = 0
        try:
            while self.running:
                with self._sub_lock:
                    active = len(self._subscribers)
                    paused_all = active > 0 and len(self._paused) >= active
                    idle = active == 0
                    wanted = self._wanted_screens()
                    dirty = self._frame_dirty.get(screen_idx, False)
                if idle or paused_all:
                    # 无人订阅 / 所有人都暂停: 不抓屏不编码, 节省 CPU
                    if idle and time.time() - last_idle_log >= IDLE_LOG_INTERVAL:
                        last_idle_log = time.time()
                        _debug_log(f'[屏幕广播] 屏幕{screen_idx + 1} 无订阅者, 待机中')
                    self._prev_bgra.pop(screen_idx, None)
                    prev_ticks = None
                    time.sleep(0.05)
                    continue

                # 没有任何客户端在看这块屏 -> 完全不抓不编
                # (双屏机器上这能直接省掉一半的抓屏+编码开销, 实测约 50ms/帧)
                # wanted is None 表示"没人声明过", 此时保持旧行为(全部编)
                if wanted is not None and screen_idx not in wanted:
                    time.sleep(0.1)
                    continue

                # 空闲降频: 本机没人动 + 主控端也没在操作 -> 降到 IDLE_FPS
                with self._cfg_lock:
                    target_fps = max(1, self.fps)
                if self._idle_seconds() > IDLE_AFTER:
                    target_fps = min(target_fps, IDLE_FPS)

                t0 = time.time()
                try:
                    img = None
                    dxf = None  # dxcam 取到的 (frame, ticks)
                    if cam is not None:
                        # ---- dxcam 路径 (DXGI 显卡直读) ----
                        # 关键: 桌面自进程启动以来从未变化时, DXGI 不会推送
                        # 任何帧(连初始帧都没有), get_latest_frame 会永久阻塞。
                        # 所以先非阻塞探测, 没有帧就先走 mss 兜底抓一次。
                        try:
                            peek = cam.grab(copy=False, new_frame_only=False)
                            peek_fails = 0
                        except Exception:
                            # 停机竞态等罕见情况: 连续失败才判死
                            peek = None
                            peek_fails += 1
                        if peek_fails > 3:
                            dxf = 'dead'
                        elif peek is None:
                            # dxcam 尚无帧 -> 下面走 mss;
                            # 但如果采集线程已死(buffer 被清空)则按故障处理
                            if not cam.is_capturing:
                                dxf = 'dead'
                        elif not cam.is_capturing:
                            dxf = 'dead'  # 采集线程已退出
                        else:
                            try:
                                dxf = cam.get_latest_frame(
                                    copy=True, with_timestamp=True)
                            except Exception as e:
                                _debug_log(f'[屏幕广播] dxcam 取帧异常: {e}')
                                dxf = None
                            if dxf is None:
                                # peek 刚拿到过帧, 这里却返回 None
                                # = 采集线程刚刚异常退出 (buffer 被清空)
                                dxf = 'dead'
                        if dxf == 'dead':
                            self.on_log(f'[屏幕] 屏幕{screen_idx + 1} dxcam 故障, '
                                        f'切换 mss 抓屏')
                            for fn in (cam.stop, cam.release):
                                try:
                                    fn()
                                except Exception:
                                    pass
                            self._cams.pop(screen_idx, None)
                            cam = None
                            prev_ticks = None
                    if dxf is not None and dxf != 'dead':
                        frame, ticks = dxf
                        # 帧号没变 = 显卡没有新画面 -> 不编码不发送
                        if dirty or ticks != prev_ticks:
                            prev_ticks = ticks
                            # 释放 mss 兜底期间缓存的上一帧 (省 ~11MB 内存)
                            self._prev_bgra.pop(screen_idx, None)
                            # dxcam output_color='RGB' -> 直接就是 RGB 像素
                            img = Image.fromarray(frame)
                            dx_frames += 1
                    else:
                        # ---- mss 兜底路径 (dxcam 尚无帧 / dxcam 故障回退) ----
                        raw = sct.grab(monitor)
                        bgra = raw.bgra
                        # 画面完全没变 -> 不编码不发送 (省编码 + 一帧带宽)
                        if dirty or self._prev_bgra.get(screen_idx) != bgra:
                            self._prev_bgra[screen_idx] = bgra
                            img = Image.frombytes('RGB', raw.size, bgra,
                                                  'raw', 'BGRX')
                            mss_frames += 1

                    if img is None:
                        # 画面没变 -> 不编码不发送 (14MB 字节比较实测仅 ~1.2ms)
                        skipped_same += 1
                        with self._sub_lock:
                            self._skipped_counts[screen_idx] = \
                                self._skipped_counts.get(screen_idx, 0) + 1
                        elapsed = time.time() - t0
                        interval = 1.0 / target_fps - elapsed
                        if interval > 0:
                            time.sleep(interval)
                        continue
                    with self._sub_lock:
                        self._frame_dirty[screen_idx] = False
                        self._encoded_counts[screen_idx] = \
                            self._encoded_counts.get(screen_idx, 0) + 1

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
                                self._client_screen.pop(cid, None)
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

                # 帧率控制 (target_fps 已按"是否空闲"算好)
                elapsed = time.time() - t0
                interval = 1.0 / target_fps - elapsed
                if interval > 0:
                    time.sleep(interval)
        finally:
            try:
                sct.close()
            except Exception:
                pass
            self._prev_bgra.pop(screen_idx, None)
            _debug_log(f'[屏幕广播] 屏幕{screen_idx + 1} 捕获线程退出 '
                       f'(编码 {frame_count} 帧: dxcam {dx_frames} / '
                       f'mss {mss_frames}, 跳过未变化 {skipped_same} 次)')

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
