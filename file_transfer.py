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
"""
import os
import struct
import threading
import json

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


class FileTransfer:
    """文件传输管理器 (双向, 单任务)

    send_fn: 线程安全的发送函数 fn(msg_type, payload)
    recv_dir: 接收文件保存目录
    on_log: 状态日志回调 fn(str)
    """

    def __init__(self, send_fn, recv_dir='.', on_log=None):
        self.send_fn = send_fn
        self.recv_dir = recv_dir
        self.on_log = on_log or print
        self._recv = None          # 接收状态 dict
        self._pending_send = None  # 待发送 dict
        self._lock = threading.Lock()

    def _log(self, msg):
        self.on_log(msg)

    def _send(self, cmd, data=b''):
        from protocol import MSG_FILE
        self.send_fn(MSG_FILE, bytes([cmd]) + data)

    # ========== 主动发起 ==========
    def upload(self, filepath, remote_name=None):
        """推送本地文件到对端"""
        remote_name = remote_name or os.path.basename(filepath)
        if not os.path.exists(filepath):
            self._log(f'[文件] 文件不存在: {filepath}')
            return
        filesize = os.path.getsize(filepath)
        nb = remote_name.encode('utf-8')
        with self._lock:
            self._pending_send = {'filepath': filepath, 'filesize': filesize}
        self._send(FT_OFFER, struct.pack('<H', len(nb)) + nb + struct.pack('<Q', filesize))
        self._log(f'[文件] 提议发送 {remote_name} ({filesize} B)')

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
                self._log(f'[文件] 对端错误: {data.decode("utf-8", errors="replace")}')
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

    # ---------- 接收逻辑 ----------
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
        local_path = os.path.join(self.recv_dir, os.path.basename(name))
        # 确保接收目录存在
        try:
            os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
        except Exception as e:
            self._log(f'[文件] 创建接收目录失败: {e}')
            self._send(FT_ERROR, f'创建接收目录失败: {e}'.encode('utf-8')[:2000])
            return
        # 断点续传: 若已有部分文件, 从当前大小继续
        offset = 0
        if os.path.exists(local_path):
            try:
                existing = os.path.getsize(local_path)
                if 0 < existing < filesize:
                    offset = existing
            except OSError:
                offset = 0
        try:
            fh = open(local_path, 'ab' if offset else 'wb')
        except OSError as e:
            self._log(f'[文件] 打开接收文件失败: {e}')
            self._recv = None
            self._send(FT_ERROR, f'打开接收文件失败: {e}'.encode('utf-8')[:2000])
            return
        self._recv = {
            'name': name, 'path': local_path, 'filesize': filesize,
            'received': offset,
            'fh': fh,
        }
        self._log(f'[文件] 接收 {name} ({filesize} B) 从 offset={offset}')
        self._send(FT_ACCEPT, struct.pack('<Q', offset))

    def _on_accept(self, data):
        if len(data) < 8:
            return
        offset = struct.unpack('<Q', data[:8])[0]
        info = self._pending_send
        if not info:
            return
        threading.Thread(target=self._send_file, args=(info, offset), daemon=True).start()

    def _send_file(self, info, offset):
        filepath = info['filepath']
        filesize = info['filesize']
        try:
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
                self._log(f'[文件] 发送完成 {os.path.basename(filepath)}')
        except Exception as e:
            self._log(f'[文件] 发送失败: {e}')
            self._send(FT_ERROR, str(e).encode('utf-8'))
        finally:
            with self._lock:
                self._pending_send = None

    def _on_data(self, data):
        if not self._recv:
            return
        try:
            self._recv['fh'].write(data)
            self._recv['received'] += len(data)
        except OSError as e:
            self._log(f'[文件] 写入接收文件失败: {e}')
            try:
                self._recv['fh'].close()
            except Exception:
                pass
            self._recv = None
            self._send(FT_ERROR, f'写入文件失败: {e}'.encode('utf-8')[:2000])
            return
        # 进度日志
        total = self._recv['filesize']
        received = self._recv['received']
        if total > 0:
            pct = received * 100 // total
            if pct % 10 == 0 and pct > 0:
                self._log(f'[文件] 接收中 {self._recv["name"]}: {pct}% ({received}/{total})')

    def _on_done(self):
        if not self._recv:
            return
        try:
            self._recv['fh'].close()
        except Exception:
            pass
        save_path = self._recv['path']
        self._log(f'[文件] 接收完成: {self._recv["name"]} 已保存到 {save_path}')
        self._recv = None

    def _on_pull(self, data):
        if len(data) < 2:
            return
        name_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + name_len:
            return
        name = data[2:2 + name_len].decode('utf-8', errors='replace')
        path = name if os.path.isabs(name) else os.path.join(self.recv_dir, name)
        if not os.path.isfile(path):
            self._send(FT_ERROR, f'文件不存在: {name}'.encode('utf-8'))
            return
        filesize = os.path.getsize(path)
        nb = os.path.basename(name).encode('utf-8')
        with self._lock:
            self._pending_send = {'filepath': path, 'filesize': filesize}
        self._send(FT_OFFER, struct.pack('<H', len(nb)) + nb + struct.pack('<Q', filesize))
        self._log(f'[文件] 对端拉取 {name} ({filesize} B)')

    # ---------- 浏览逻辑 (被控端响应) ----------
    def _on_list_req(self, data):
        """被控端: 处理目录列表请求"""
        if len(data) < 2:
            return
        path_len = struct.unpack('<H', data[:2])[0]
        if len(data) < 2 + path_len:
            return
        path = data[2:2 + path_len].decode('utf-8', errors='replace')
        # 安全检查: 防止路径遍历攻击
        try:
            # Windows 根路径特殊处理: 显示所有驱动器
            if path in ('/', '\\', ''):
                drives = self._get_all_drives()
                resp_data = json.dumps({'path': '/', 'items': drives}, ensure_ascii=False)
                self._send(FT_LIST_RESP, resp_data.encode('utf-8'))
                return
            
            # 转换路径格式 (Windows)
            if os.name == 'nt' and path.startswith('/'):
                path = path[1:]  # 移除开头的 /
            
            if not os.path.isabs(path):
                path = os.path.expanduser(path)
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)
            
            abs_path = os.path.abspath(path)
            
            # 如果路径是驱动器根目录 (如 C:), 添加反斜杠
            if abs_path.endswith(':'):
                abs_path += '\\'
                
            if not os.path.exists(abs_path):
                self._send(FT_ERROR, f'路径不存在: {abs_path}'.encode('utf-8'))
                return
                
            items = self._scan_directory(abs_path)
            resp_data = json.dumps({'path': abs_path, 'items': items}, ensure_ascii=False)
            self._send(FT_LIST_RESP, resp_data.encode('utf-8'))
        except Exception as e:
            import traceback
            self._log(f'[文件] 处理目录列表异常: {e}')
            self._log(traceback.format_exc())
            try:
                self._send(FT_ERROR, str(e).encode('utf-8'))
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
                if os.path.exists(drive):
                    items.append({
                        'name': f'{letter}:\\',
                        'is_dir': True,
                        'size': 0,
                        'modified': '',
                    })
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
                self._send(FT_REMOVE_RESP, b'\x01')  # 成功
                self._log(f'[文件] 对端删除文件: {abs_path}')
            elif os.path.isdir(abs_path):
                import shutil
                shutil.rmtree(abs_path)
                self._send(FT_REMOVE_RESP, b'\x01')
                self._log(f'[文件] 对端删除目录: {abs_path}')
            else:
                self._send(FT_REMOVE_RESP, b'\x00')  # 失败
        except Exception as e:
            self._send(FT_ERROR, str(e).encode('utf-8'))

    # ---------- 浏览逻辑 (主控端接收响应) ----------
    def _on_list_resp(self, data):
        """主控端: 接收目录列表响应"""
        try:
            resp = json.loads(data.decode('utf-8'))
            if hasattr(self, 'on_list_result'):
                self.on_list_result(resp)
            self._log(f'[文件] 收到目录列表: {resp.get("path", "")}')
        except Exception as e:
            self._log(f'[文件] 解析目录列表失败: {e}')

    def _on_remove_resp(self, data):
        """主控端: 接收删除响应"""
        success = data[0] == 1 if data else False
        if hasattr(self, 'on_remove_result'):
            self.on_remove_result(success)
        self._log(f'[文件] 删除{"成功" if success else "失败"}')

    # ---------- 工具方法 ----------
    @staticmethod
    def _scan_directory(path):
        """扫描目录并返回文件列表"""
        items = []
        try:
            for entry in os.listdir(path):
                full_path = os.path.join(path, entry)
                is_dir = os.path.isdir(full_path)
                item = {
                    'name': entry,
                    'is_dir': is_dir,
                    'size': 0 if is_dir else os.path.getsize(full_path),
                }
                try:
                    stat = os.stat(full_path)
                    import datetime
                    item['modified'] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    item['modified'] = ''
                items.append(item)
        except Exception as e:
            raise Exception(f'无法访问目录: {e}')
        return items