# -*- coding: utf-8 -*-
"""MyDesk 文件传输模块 (双向 + 断点续传 + 文件浏览)

协议 (MSG_FILE 载荷 = [cmd 1B][data...]):
  FT_OFFER  0x01  发送方提议: [名称长 2B][名称][文件大小 8B]
  FT_ACCEPT 0x02  接收方接受: [起始偏移 8B]  (断点续传)
  FT_DATA   0x03  数据块: [数据字节]
  FT_DONE   0x04  传输完成
  FT_PULL   0x05  请求拉取: [名称长 2B][名称]
  FT_ERROR  0x06  错误: [原因 utf8]
  FT_LIST_REQ  0x07  请求目录列表: [路径长 2B][路径]
  FT_LIST_RESP 0x08  目录列表响应: [JSON数据]
  FT_REMOVE  0x09  请求删除: [名称长 2B][名称]
  FT_REMOVE_RESP 0x0A 删除响应: [结果 1B]

流程:
  推送(A->B): A 发 FT_OFFER -> B 发 FT_ACCEPT(offset) -> A 发 FT_DATA... -> A 发 FT_DONE
  拉取(A<-B): A 发 FT_PULL -> B 发 FT_OFFER -> A 发 FT_ACCEPT(offset) -> B 发 FT_DATA... -> B 发 FT_DONE
  浏览: A 发 FT_LIST_REQ -> B 发 FT_LIST_RESP(JSON)

正确性保证 (本版新增):
  1. 接收端先写 "<名字>.part", 只有 FT_DONE 且 "收到字节数 == 声明大小" 才
     os.replace 成正式文件名 —— 杜绝"中途断线留下半个文件冒充完整文件"。
  2. 同一时刻只处理一个接收任务; 若收到重复 OFFER 会回 FT_ERROR busy,
     不会覆盖正在写入的文件句柄。
  3. 发送端有真正的任务队列: 连续 upload()/download() 不再互相覆盖。
  4. 目录列表/删除结果改为"多播监听", 多个对话框不会再互相抢回调。
"""
import os
import struct
import threading
import json
from collections import deque

FT_OFFER = 0x01
FT_ACCEPT = 0x02
FT_DATA = 0x03
FT_DONE = 0x04
FT_PULL = 0x05
FT_ERROR = 0x06
FT_LIST_REQ = 0x07
FT_LIST_RESP = 0x08
FT_REMOVE = 0x09
FT_REMOVE_RESP = 0x0A

CHUNK_SIZE = 65536  # 64KB 每块, 与屏幕帧交错时不致阻塞过久
SEND_QUEUE_MAX = 64  # 待发送任务上限, 防止无限堆积
PART_SUFFIX = '.part'


class FileTransfer:
    """文件传输管理器 (双向, 带发送队列)

    send_fn: 线程安全的发送函数 fn(msg_type, payload)
    recv_dir: 接收文件保存目录
    on_log: 状态日志回调 fn(str)
    """

    def __init__(self, send_fn, recv_dir='.', on_log=None):
        self.send_fn = send_fn
        self.recv_dir = recv_dir
        self.on_log = on_log or print
        self._recv = None          # 当前接收状态 dict (同一时刻最多一个)
        self._pending_send = None  # 正在协商/发送的任务
        self._active_send = False  # 是否已有发送线程在跑
        self._send_queue = deque()  # 待发送任务队列
        self._lock = threading.Lock()
        # 目录列表 / 删除结果的监听者 (多播, 替代原来"单属性互相覆盖"的写法)
        self._list_listeners = []
        self._remove_listeners = []
        self._log_listeners = []

    def _log(self, msg):
        # 多播: 状态栏 / 对话框进度条等可以有多个消费者
        for fn in list(self._log_listeners):
            try:
                fn(msg)
            except Exception:
                pass
        try:
            self.on_log(msg)
        except Exception:
            pass

    def _send(self, cmd, data=b''):
        from protocol import MSG_FILE
        self.send_fn(MSG_FILE, bytes([cmd]) + data)

    # ========== 监听者注册 ==========
    def add_list_listener(self, fn):
        if fn not in self._list_listeners:
            self._list_listeners.append(fn)

    def remove_list_listener(self, fn):
        if fn in self._list_listeners:
            self._list_listeners.remove(fn)

    def add_remove_listener(self, fn):
        if fn not in self._remove_listeners:
            self._remove_listeners.append(fn)

    def remove_remove_listener(self, fn):
        if fn in self._remove_listeners:
            self._remove_listeners.remove(fn)

    def add_log_listener(self, fn):
        if fn not in self._log_listeners:
            self._log_listeners.append(fn)

    def remove_log_listener(self, fn):
        if fn in self._log_listeners:
            self._log_listeners.remove(fn)

    # ========== 主动发起 ==========
    def upload(self, filepath, remote_name=None):
        """推送本地文件到对端 (进入发送队列)"""
        remote_name = remote_name or os.path.basename(filepath)
        if not os.path.exists(filepath):
            self._log(f'[文件] 文件不存在: {filepath}')
            return False
        if not os.path.isfile(filepath):
            self._log(f'[文件] 不是文件(暂不支持目录): {filepath}')
            return False
        filesize = os.path.getsize(filepath)
        self._enqueue_send({'filepath': filepath, 'filesize': filesize,
                            'name': remote_name})
        return True

    def download(self, remote_name):
        """从对端拉取文件"""
        nb = remote_name.encode('utf-8')
        self._send(FT_PULL, struct.pack('<H', len(nb)) + nb)
        self._log(f'[文件] 请求拉取 {remote_name}')

    def list_remote_dir(self, path):
        """请求远程目录列表"""
        nb = path.encode('utf-8')
        self._send(FT_LIST_REQ, struct.pack('<H', len(nb)) + nb)

    def remove_remote_file(self, path):
        """请求删除远程文件"""
        nb = path.encode('utf-8')
        self._send(FT_REMOVE, struct.pack('<H', len(nb)) + nb)

    # ---------- 发送队列 ----------
    def _enqueue_send(self, info):
        with self._lock:
            if len(self._send_queue) >= SEND_QUEUE_MAX:
                self._log('[文件] 发送队列已满, 丢弃本次任务')
                return
            self._send_queue.append(info)
        self._pump_send_queue()

    def _pump_send_queue(self):
        """在没有进行中任务时取出下一个任务并发出 FT_OFFER"""
        with self._lock:
            if self._pending_send is not None or self._active_send:
                return
            if not self._send_queue:
                return
            info = self._send_queue.popleft()
            self._pending_send = info
        nb = info['name'].encode('utf-8')
        try:
            self._send(FT_OFFER,
                       struct.pack('<H', len(nb)) + nb
                       + struct.pack('<Q', info['filesize']))
            self._log(f'[文件] 提议发送 {info["name"]} ({info["filesize"]} B)')
        except Exception as e:
            self._log(f'[文件] 发送提议失败: {e}')
            with self._lock:
                self._pending_send = None
            self._pump_send_queue()

    def _finish_send(self):
        with self._lock:
            self._pending_send = None
            self._active_send = False
        self._pump_send_queue()

    # ========== 消息处理 (在接收线程调用) ==========
    def handle_message(self, payload):
        if not payload:
            return
        cmd = payload[0]
        data = payload[1:]
        try:
            if cmd == FT_OFFER:
                self._on_offer(data)
            elif cmd == FT_ACCEPT:
                self._on_accept(data)
            elif cmd == FT_DATA:
                self._on_data(data)
            elif cmd == FT_DONE:
                self._on_done()
            elif cmd == FT_PULL:
                self._on_pull(data)
            elif cmd == FT_ERROR:
                reason = data.decode('utf-8', errors='replace')
                self._log(f'[文件] 对端错误: {reason}')
                self._on_remote_error()
            elif cmd == FT_LIST_RESP:
                self._on_list_resp(data)
            elif cmd == FT_REMOVE_RESP:
                self._on_remove_resp(data)
            elif cmd == FT_LIST_REQ:
                self._on_list_req(data)
            elif cmd == FT_REMOVE:
                self._on_remove(data)
        except Exception as e:
            # 单条坏包只记录日志, 绝不中断接收循环/连接
            self._log(f'[文件] 处理消息异常 cmd={cmd:#x}: {e}')
            try:
                self._send(FT_ERROR, f'协议处理失败: {e}'.encode('utf-8')[:2000])
            except Exception:
                pass

    def _on_remote_error(self):
        """对端报错: 释放本端"协商中"的任务, 让队列继续"""
        with self._lock:
            waiting = self._pending_send is not None and not self._active_send
            if waiting:
                self._pending_send = None
        if waiting:
            self._pump_send_queue()

    # ---------- 接收逻辑 ----------
    def _part_path(self, final_path):
        return final_path + PART_SUFFIX

    def _on_offer(self, data):
        # 长度校验: 至少 2 字节名称长度 + 名称 + 8 字节文件大小
        if len(data) < 2:
            return
        name_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + name_len + 8:
            self._log(f'[文件] 收到截断的文件提议 (name_len={name_len}, data_len={len(data)})')
            return
        name = data[2:2 + name_len].decode('utf-8', errors='replace')
        filesize = struct.unpack('<Q', data[2 + name_len:10 + name_len])[0]

        # 同一时刻只允许一个接收任务, 否则会覆盖正在写入的文件句柄
        if self._recv is not None:
            self._log(f'[文件] 已有接收任务进行中, 拒绝并发提议 {name}')
            self._send(FT_ERROR, '接收端忙: 请等待当前传输完成'.encode('utf-8'))
            return

        safe_name = os.path.basename(name) or 'unnamed'
        local_path = os.path.join(self.recv_dir, safe_name)
        part_path = self._part_path(local_path)
        try:
            os.makedirs(self.recv_dir or '.', exist_ok=True)
        except Exception as e:
            self._log(f'[文件] 创建接收目录失败: {e}')
            self._send(FT_ERROR, f'创建接收目录失败: {e}'.encode('utf-8')[:2000])
            return

        # 情况1: 正式文件已存在且大小一致 -> 视为已完整, 让对端直接 DONE
        try:
            if os.path.isfile(local_path) and os.path.getsize(local_path) == filesize:
                self._recv = {'name': name, 'path': local_path, 'part': part_path,
                              'filesize': filesize, 'received': filesize,
                              'fh': None, 'complete': True, 'logged_pct': 100}
                self._log(f'[文件] {safe_name} 已存在且大小一致, 跳过传输')
                self._send(FT_ACCEPT, struct.pack('<Q', filesize))
                return
        except OSError:
            pass

        # 情况2: 断点续传 —— 依据 .part 残文件大小
        offset = 0
        try:
            if os.path.isfile(part_path):
                existing = os.path.getsize(part_path)
                if 0 < existing < filesize:
                    offset = existing
        except OSError:
            offset = 0

        try:
            # 从 0 开始时截断, 避免旧内容残留
            fh = open(part_path, 'ab' if offset else 'wb')
        except OSError as e:
            self._log(f'[文件] 打开接收文件失败: {e}')
            self._recv = None
            self._send(FT_ERROR, f'打开接收文件失败: {e}'.encode('utf-8')[:2000])
            return

        self._recv = {'name': name, 'path': local_path, 'part': part_path,
                      'filesize': filesize, 'received': offset, 'fh': fh,
                      'complete': False, 'logged_pct': (offset * 100 // filesize)
                      if filesize else 0}
        self._log(f'[文件] 接收 {safe_name} ({filesize} B) 从 offset={offset}')
        self._send(FT_ACCEPT, struct.pack('<Q', offset))

    def _on_accept(self, data):
        if len(data) < 8:
            return
        offset = struct.unpack('<Q', data[:8])[0]
        with self._lock:
            info = self._pending_send
            if not info:
                return
            self._active_send = True
        threading.Thread(target=self._send_file, args=(info, offset), daemon=True).start()

    def _send_file(self, info, offset):
        filepath = info['filepath']
        filesize = info['filesize']
        try:
            if offset > filesize:
                offset = 0
            if offset == filesize:
                self._send(FT_DONE)
                self._log(f'[文件] 对端已完整拥有 {info["name"]}, 无需传输')
                return
            with open(filepath, 'rb') as f:
                f.seek(offset)
                sent = offset
                while sent < filesize:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self._send(FT_DATA, chunk)
                    sent += len(chunk)
                self._send(FT_DONE)
                self._log(f'[文件] 发送完成 {os.path.basename(filepath)} ({sent} B)')
        except Exception as e:
            self._log(f'[文件] 发送失败: {e}')
            try:
                self._send(FT_ERROR, str(e).encode('utf-8')[:2000])
            except Exception:
                pass
        finally:
            self._finish_send()

    def _on_data(self, data):
        recv = self._recv
        if not recv:
            return
        if recv.get('complete'):
            return
        fh = recv.get('fh')
        if fh is None:
            return
        try:
            fh.write(data)
            recv['received'] += len(data)
        except OSError as e:
            self._log(f'[文件] 写入接收文件失败: {e}')
            self._abort_recv()
            self._send(FT_ERROR, f'写入文件失败: {e}'.encode('utf-8')[:2000])
            return
        total = recv['filesize']
        received = recv['received']
        if total > 0:
            pct = received * 100 // total
            # 只在跨越 10% 台阶时打一行, 避免同一区间反复刷日志
            if pct // 10 > recv.get('logged_pct', 0) // 10:
                recv['logged_pct'] = pct
                self._log(f'[文件] 接收中 {recv["name"]}: {pct}% ({received}/{total})')

    def _abort_recv(self):
        """放弃当前接收任务 (保留 .part 以便下次续传)"""
        recv = self._recv
        self._recv = None
        if not recv:
            return
        fh = recv.get('fh')
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass

    def _on_done(self):
        recv = self._recv
        if not recv:
            return
        self._recv = None
        name = recv['name']
        filesize = recv['filesize']
        received = recv['received']

        if recv.get('complete'):
            self._log(f'[文件] 接收完成: {name} 已存在')
            return

        fh = recv.get('fh')
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass

        # 关键校验: 字节数必须与声明一致, 否则绝不改名
        if received != filesize:
            self._log(f'[文件] 传输不完整: {name} 收到 {received}/{filesize} B, '
                      f'已保留 {os.path.basename(recv["part"])} 供续传')
            try:
                self._send(FT_ERROR,
                           f'传输不完整: {received}/{filesize}'.encode('utf-8'))
            except Exception:
                pass
            return

        try:
            os.replace(recv['part'], recv['path'])
            self._log(f'[文件] 接收完成: {name} ({received} B) 已保存到 {recv["path"]}')
        except OSError as e:
            self._log(f'[文件] 重命名失败: {e} (数据保存在 {recv["part"]})')

    def reset(self):
        """连接断开时清理未完成的传输状态 (保留 .part 残文件以便续传)"""
        self._abort_recv()
        with self._lock:
            self._pending_send = None
            self._send_queue.clear()
            self._active_send = False

    def _on_pull(self, data):
        if len(data) < 2:
            return
        name_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + name_len:
            return
        name = data[2:2 + name_len].decode('utf-8', errors='replace')
        path = name if os.path.isabs(name) else os.path.join(self.recv_dir, name)
        if not os.path.isfile(path):
            self._send(FT_ERROR, f'文件不存在: {name}'.encode('utf-8')[:2000])
            return
        try:
            filesize = os.path.getsize(path)
        except OSError as e:
            self._send(FT_ERROR, f'读取文件失败: {e}'.encode('utf-8')[:2000])
            return
        # 走统一发送队列: 多个拉取请求会排队, 不再互相覆盖
        self._enqueue_send({'filepath': path, 'filesize': filesize,
                            'name': os.path.basename(name)})

    # ---------- 浏览逻辑 (被控端响应) ----------
    def _on_list_req(self, data):
        """被控端: 处理目录列表请求"""
        if len(data) < 2:
            return
        path_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + path_len:
            return
        path = data[2:2 + path_len].decode('utf-8', errors='replace')
        try:
            # Windows 根路径特殊处理: 显示所有驱动器
            if path in ('/', '\\', ''):
                drives = self._get_all_drives()
                resp_data = json.dumps({'path': '/', 'items': drives}, ensure_ascii=False)
                self._send(FT_LIST_RESP, resp_data.encode('utf-8'))
                return

            # 转换路径格式 (Windows)
            if os.name == 'nt' and path.startswith('/'):
                path = path[1:]

            if not os.path.isabs(path):
                path = os.path.expanduser(path)
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)

            abs_path = os.path.abspath(path)

            # 如果路径是驱动器根目录 (如 C:), 添加反斜杠
            if abs_path.endswith(':'):
                abs_path += '\\'

            if not os.path.exists(abs_path):
                self._send(FT_ERROR, f'路径不存在: {abs_path}'.encode('utf-8')[:2000])
                return

            items = self._scan_directory(abs_path)
            resp_data = json.dumps({'path': abs_path, 'items': items}, ensure_ascii=False)
            self._send(FT_LIST_RESP, resp_data.encode('utf-8'))
        except Exception as e:
            import traceback
            self._log(f'[文件] 处理目录列表异常: {e}')
            self._log(traceback.format_exc())
            try:
                self._send(FT_ERROR, str(e).encode('utf-8')[:2000])
            except Exception:
                pass

    @staticmethod
    def _get_all_drives():
        """获取Windows上所有可用驱动器"""
        items = []
        try:
            import string
            for letter in string.ascii_uppercase:
                drive = f'{letter}:/'
                try:
                    if os.path.exists(drive):
                        items.append({
                            'name': f'{letter}:\\',
                            'is_dir': True,
                            'size': 0,
                            'modified': '',
                        })
                except OSError:
                    continue
        except Exception:
            pass
        return items

    def _on_remove(self, data):
        """被控端: 处理文件删除请求"""
        if len(data) < 2:
            return
        path_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + path_len:
            return
        path = data[2:2 + path_len].decode('utf-8', errors='replace')
        try:
            abs_path = os.path.abspath(path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)
                self._send(FT_REMOVE_RESP, b'\x01')
                self._log(f'[文件] 对端删除文件: {abs_path}')
            elif os.path.isdir(abs_path):
                import shutil
                shutil.rmtree(abs_path)
                self._send(FT_REMOVE_RESP, b'\x01')
                self._log(f'[文件] 对端删除目录: {abs_path}')
            else:
                self._send(FT_REMOVE_RESP, b'\x00')
        except Exception as e:
            self._log(f'[文件] 删除失败: {e}')
            try:
                self._send(FT_ERROR, str(e).encode('utf-8')[:2000])
            except Exception:
                pass

    # ---------- 浏览逻辑 (主控端接收响应) ----------
    def _on_list_resp(self, data):
        """主控端: 接收目录列表响应 -> 多播给所有监听者"""
        try:
            resp = json.loads(data.decode('utf-8'))
        except Exception as e:
            self._log(f'[文件] 解析目录列表失败: {e}')
            return
        for fn in list(self._list_listeners):
            try:
                fn(resp)
            except Exception as e:
                self._log(f'[文件] 目录列表回调异常: {e}')

    def _on_remove_resp(self, data):
        """主控端: 接收删除响应 -> 多播给所有监听者"""
        success = bool(data) and data[0] == 1
        for fn in list(self._remove_listeners):
            try:
                fn(success)
            except Exception as e:
                self._log(f'[文件] 删除结果回调异常: {e}')

    # ---------- 工具方法 ----------
    @staticmethod
    def _scan_directory(path):
        """扫描目录并返回文件列表"""
        items = []
        for entry in os.listdir(path):
            full_path = os.path.join(path, entry)
            try:
                is_dir = os.path.isdir(full_path)
            except OSError:
                is_dir = False
            item = {
                'name': entry,
                'is_dir': is_dir,
                'size': 0,
                'modified': '',
            }
            try:
                if not is_dir:
                    item['size'] = os.path.getsize(full_path)
            except OSError:
                pass
            try:
                stat = os.stat(full_path)
                import datetime
                item['modified'] = datetime.datetime.fromtimestamp(
                    stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            except Exception:
                pass
            items.append(item)
        return items
