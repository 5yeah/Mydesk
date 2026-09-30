# -*- coding: utf-8 -*-
"""MyDesk 屏幕捕获与传输模块

被控端 ScreenStreamer: mss 捕获桌面 -> JPEG 编码 -> 发送 MSG_SCREEN
主控端 decode_to_qpixmap: JPEG -> QPixmap 直接渲染
"""
import io
import threading
import time
import mss
from PIL import Image
from protocol import MSG_SCREEN, MSG_CONTROL


class ScreenStreamer:
    """被控端屏幕流推送器

    send_fn: 线程安全的发送函数 fn(msg_type, payload)
    参数调节:
      quality: JPEG 画质 10-100, 局域网建议 70-85, 公网建议 30-50
      fps:     目标帧率 1-60, 局域网建议 30, 公网建议 10-15
    """
    def __init__(self, send_fn, quality=65, fps=30):
        self.send_fn = send_fn
        self.quality = quality
        self.fps = fps
        self.running = False
        self.paused = False
        self._sct = None
        self._mon = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        self._sct = mss.MSS()
        # 优先选主显示器, 否则取第一个
        self._mon = self._sct.monitors[1]
        for m in self._sct.monitors[1:]:
            if m.get('is_primary'):
                self._mon = m
                break
        # 先发送屏幕尺寸 (供主控端初始化窗口)
        info = f"SCREEN:{self._mon['width']}x{self._mon['height']}".encode('utf-8')
        try:
            self.send_fn(MSG_CONTROL, info)
        except Exception:
            return False
        self.running = True
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        return True

    def _stream_loop(self):
        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue
            t0 = time.time()
            try:
                raw = self._sct.grab(self._mon)
                # raw.bgra 为 BGRA 字节流, 转为 RGB 再 JPEG 编码
                img = Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
                buf = io.BytesIO()
                with self._lock:
                    q = self.quality
                img.save(buf, format='JPEG', quality=q)
                self.send_fn(MSG_SCREEN, buf.getvalue())
            except (BrokenPipeError, ConnectionError, OSError):
                self.running = False
                break
            except Exception as e:
                print(f'[屏幕] 传输异常: {e}')
                self.running = False
                break
            # 帧率控制: 补足到目标间隔
            with self._lock:
                target_fps = self.fps
            elapsed = time.time() - t0
            interval = 1.0 / target_fps - elapsed
            if interval > 0:
                time.sleep(interval)

    def set_quality(self, q):
        with self._lock:
            self.quality = max(10, min(100, int(q)))

    def set_fps(self, fps):
        with self._lock:
            self.fps = max(1, min(60, int(fps)))

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass


def decode_to_qpixmap(jpeg_bytes):
    """主控端: JPEG 字节 -> QPixmap (可直接 setPixmap 到 QLabel)"""
    img = Image.open(io.BytesIO(jpeg_bytes))
    return img.toqpixmap()
