# -*- coding: utf-8 -*-
"""MyDesk 共享屏幕广播器 (支持多显示器)

支持多客户端共享同一个屏幕捕获源, 避免每个连接都重复捕获。
支持多显示器: 检测所有显示器并独立捕获, 每个屏幕作为独立流发送。
ScreenBroadcaster:
  - 一个 mss 实例 + 多个捕获线程 (每个显示器一个)
  - 每帧编码后, 同时推送给所有订阅的客户端
  - 客户端断开时自动取消订阅
  - 屏幕帧格式: [屏幕索引 4字节][JPEG数据]
"""
import io
import struct
import threading
import time
import os
import datetime
import mss
from PIL import Image
from protocol import MSG_SCREEN, MSG_CONTROL

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


class ScreenBroadcaster:
    """屏幕广播器 (每个被控端实例独立)

    用法:
      bc = ScreenBroadcaster(on_log)
      bc.start()                      # 启动
      bc.subscribe(client_id, send_fn)  # 客户端订阅
      bc.unsubscribe(client_id)       # 客户端断开
    """

    def __init__(self, on_log=None):
        self.on_log = on_log or print
        self.quality = 65
        self.fps = 30
        self.running = False
        self._sct = None
        self._monitors = []  # 所有显示器列表
        self._threads = []  # 每个显示器的捕获线程
        self._cfg_lock = threading.Lock()
        # 订阅者: {client_id: send_fn}
        self._subscribers = {}
        # 逐客户端发送锁: 用于"丢帧不排队", 防止慢客户端阻塞整个捕获循环
        self._sub_locks = {}
        self._sub_lock = threading.Lock()
        # 屏幕信息
        self._screen_info = []  # [(idx, w, h), ...]

    def start(self):
        if self.running:
            _debug_log(f'[屏幕广播] start: 已在运行')
            return True
        try:
            _debug_log(f'[屏幕广播] start: 开始初始化 mss')
            self._sct = mss.mss()
            # 获取所有显示器
            self._monitors = list(self._sct.monitors[1:])  # 跳过索引0 (全屏幕虚拟)
            _debug_log(f'[屏幕广播] 检测到 {len(self._monitors)} 个显示器')
            
            # 检测主显示器
            primary_idx = 0
            for i, m in enumerate(self._monitors):
                if m.get('is_primary'):
                    primary_idx = i
                    break
            
            # 调整顺序，主显示器放第一个
            if primary_idx > 0:
                primary = self._monitors.pop(primary_idx)
                self._monitors.insert(0, primary)
            
            # 保存屏幕信息
            self._screen_info = [
                (i, m['width'], m['height'])
                for i, m in enumerate(self._monitors)
            ]
            _debug_log(f'[屏幕广播] 屏幕信息: {self._screen_info}')
            
            self.running = True
            _debug_log(f'[屏幕广播] running=True')
            
            # 为每个显示器启动捕获线程
            for i, mon in enumerate(self._monitors):
                t = threading.Thread(
                    target=self._capture_loop,
                    args=(i, mon),
                    daemon=True
                )
                t.start()
                self._threads.append(t)
                _debug_log(f'[屏幕广播] 启动捕获线程: screen_idx={i}')
            
            # 发送屏幕信息给所有客户端
            n_screens = len(self._monitors)
            self.on_log(f'[屏幕] 广播器启动, 检测到 {n_screens} 个显示器')
            for idx, w, h in self._screen_info:
                self.on_log(f'[屏幕]   屏幕{idx+1}: {w}x{h}')
            
            return True
        except Exception as e:
            _debug_log(f'[屏幕广播] start 异常: {e}')
            self.on_log(f'[屏幕] 启动失败: {e}')
            return False

    def stop(self):
        self.running = False
        for t in self._threads:
            t.join(timeout=1)
        self._threads = []
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
        with self._sub_lock:
            self._subscribers.clear()
            self._sub_locks.clear()

    # ---------- 订阅管理 ----------
    def subscribe(self, client_id, send_fn):
        """客户端订阅屏幕流, 立即发送屏幕尺寸和多屏幕信息"""
        _debug_log(f'[屏幕广播] subscribe: client_id={client_id}')
        with self._sub_lock:
            self._subscribers[client_id] = send_fn
            self._sub_locks[client_id] = threading.Lock()
        
        n_screens = len(self._monitors)
        _debug_log(f'[屏幕广播] 订阅者数量: {len(self._subscribers)}, 屏幕数量: {n_screens}')
        
        # 发送每个屏幕的尺寸信息 (格式: "SCREEN:{idx}:{w}x{h}")
        for idx, w, h in self._screen_info:
            try:
                info = f"SCREEN:{idx}:{w}x{h}".encode('utf-8')
                _debug_log(f'[屏幕广播] 发送屏幕信息: {info.decode()}')
                send_fn(MSG_CONTROL, info)
            except Exception as e:
                _debug_log(f'[屏幕广播] 发送屏幕信息失败: {e}')
                pass
        
        # 如果有多个屏幕，发送多屏幕信息
        if n_screens > 1:
            try:
                # 发送屏幕列表: "SCREEN_LIST:0:1920:1080,1:1920:1080"
                screen_list_parts = []
                for idx, w, h in self._screen_info:
                    screen_list_parts.append(f'{idx}:{w}:{h}')
                screen_list_msg = f"SCREEN_LIST:{','.join(screen_list_parts)}"
                _debug_log(f'[屏幕广播] 发送屏幕列表: {screen_list_msg}')
                send_fn(MSG_CONTROL, screen_list_msg.encode('utf-8'))
            except Exception as e:
                _debug_log(f'[屏幕广播] 发送屏幕列表失败: {e}')
                pass
        
        self.on_log(f'[屏幕] +订阅 {client_id} (共 {self._sub_count()} 个)')
        _debug_log(f'[屏幕广播] subscribe 完成')

    def unsubscribe(self, client_id):
        with self._sub_lock:
            if client_id in self._subscribers:
                del self._subscribers[client_id]
            self._sub_locks.pop(client_id, None)
        self.on_log(f'[屏幕] -订阅 {client_id} (剩 {self._sub_count()} 个)')

    def _sub_count(self):
        with self._sub_lock:
            return len(self._subscribers)

    def get_subscribers(self):
        with self._sub_lock:
            return list(self._subscribers.keys())

    def get_screen_count(self):
        """获取显示器数量"""
        return len(self._monitors)

    def get_screen_info(self):
        """获取所有屏幕信息"""
        return list(self._screen_info)

    # ---------- 捕获循环 (每个显示器独立) ----------
    def _capture_loop(self, screen_idx, monitor):
        """单个显示器的捕获循环 (每个线程有自己的 mss 实例)"""
        fail_count = 0
        w = monitor['width']
        h = monitor['height']
        self.on_log(f'[屏幕] 屏幕{screen_idx+1} 捕获线程启动 ({w}x{h})')
        _debug_log(f'[屏幕广播] _capture_loop 启动: screen_idx={screen_idx}, {w}x{h}')
        
        # 每个线程创建自己的 mss 实例 (mss 不是线程安全的)
        try:
            sct = mss.mss()
        except Exception as e:
            _debug_log(f'[屏幕广播] 创建mss实例失败: {e}')
            self.on_log(f'[屏幕] 屏幕{screen_idx+1} 创建mss实例失败: {e}')
            return
        
        frame_count = 0
        while self.running:
            with self._sub_lock:
                if not self._subscribers:
                    time.sleep(0.1)
                    if frame_count % 30 == 0:  # 每30帧输出一次
                        _debug_log(f'[屏幕广播] 无订阅者, 等待中...')
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
                    subs = [(cid, fn, self._sub_locks.get(cid))
                            for cid, fn in self._subscribers.items()]
                
                if frame_count < 3:  # 只记录前3帧
                    _debug_log(f'[屏幕广播] 捕获帧 #{frame_count}: jpeg_size={len(jpeg)}, subscribers={len(subs)}')
                
                dead = []
                for cid, send_fn, lk in subs:
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
                # 清理断开的订阅者
                if dead:
                    with self._sub_lock:
                        for cid in dead:
                            self._subscribers.pop(cid, None)
                            self._sub_locks.pop(cid, None)
                fail_count = 0
                frame_count += 1
            except (BrokenPipeError, ConnectionError, OSError) as e:
                fail_count += 1
                _debug_log(f'[屏幕广播] 捕获错误: {e}, fail_count={fail_count}')
                if fail_count > 5:
                    self.on_log(f'[屏幕] 屏幕{screen_idx+1} 捕获连续失败, 停止')
                    break
            except Exception as e:
                _debug_log(f'[屏幕广播] 捕获异常: {e}')
                self.on_log(f'[屏幕] 屏幕{screen_idx+1} 捕获异常: {e}')
                fail_count += 1
                if fail_count > 10:
                    break

            # 帧率控制
            with self._cfg_lock:
                target_fps = self.fps
            elapsed = time.time() - t0
            interval = 1.0 / target_fps - elapsed
            if interval > 0:
                time.sleep(interval)
        
        # 关闭线程自己的 mss 实例
        try:
            sct.close()
        except Exception:
            pass

    def _is_paused_all(self):
        """所有订阅者都暂停时才暂停"""
        return False  # 简化: 不支持单客户端暂停

    # ---------- 配置 ----------
    def set_quality(self, q):
        with self._cfg_lock:
            self.quality = max(10, min(100, int(q)))
        self.on_log(f'[屏幕] 画质={self.quality}')

    def set_fps(self, fps):
        with self._cfg_lock:
            self.fps = max(1, min(60, int(fps)))
        self.on_log(f'[屏幕] 帧率={self.fps}')
