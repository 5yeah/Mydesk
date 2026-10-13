# -*- coding: utf-8 -*-
"""MyDesk Windows 服务宿主。

服务只负责生命周期和会话代理的拉起，不直接创建 Qt 窗口。
这样主界面退出、用户重新登录、网络抖动或被控端进程崩溃时，
远程桌面服务仍可自动恢复。

注意：Windows 锁屏/安全桌面仍需在真实机器上验证。服务化是必要条件，
但不能把普通桌面捕获 API 自动变成 Winlogon 桌面 API。
"""
import os
import sys
import time

import win32con
import win32event
import win32process
import win32profile
import win32service
import win32serviceutil
import win32security
import win32ts


SERVICE_NAME = 'MyDeskService'
SERVICE_DISPLAY_NAME = 'MyDesk Remote Desktop Service'
SERVICE_DESCRIPTION = 'MyDesk remote desktop session broker and watchdog'


def _active_session_id():
    """返回当前控制台会话；无交互用户时返回 None。"""
    try:
        sid = win32ts.WTSGetActiveConsoleSessionId()
        return None if sid in (0xFFFFFFFF, None) else int(sid)
    except Exception:
        return None


def _agent_command():
    """构造当前安装形态下的会话代理命令。"""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --agent'
    entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')
    return f'"{sys.executable}" "{entry}" --agent'


class MyDeskService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None
        self.session_id = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self._stop_agent()

    def SvcDoRun(self):
        self._run_watchdog()

    def _start_agent(self, session_id):
        token = win32security.WTSQueryUserToken(session_id)
        try:
            env = win32profile.CreateEnvironmentBlock(token, False)
            startup = win32process.STARTUPINFO()
            startup.lpDesktop = 'winsta0\\default'
            cmd = _agent_command()
            flags = win32process.CREATE_UNICODE_ENVIRONMENT
            self.process = win32process.CreateProcessAsUser(
                token, None, cmd, None, None, False, flags, env, None, startup)
            self.session_id = session_id
        finally:
            try:
                win32profile.DestroyEnvironmentBlock(env)
            except Exception:
                pass
            try:
                token.Close()
            except Exception:
                pass

    def _stop_agent(self):
        if not self.process:
            return
        try:
            win32process.TerminateProcess(self.process[0], 0)
        except Exception:
            pass
        self.process = None
        self.session_id = None

    def _agent_alive(self):
        if not self.process:
            return False
        try:
            return win32process.GetExitCodeProcess(self.process[0]) == win32con.STILL_ACTIVE
        except Exception:
            return False

    def _run_watchdog(self):
        while True:
            result = win32event.WaitForSingleObject(self.stop_event, 1000)
            if result == win32event.WAIT_OBJECT_0:
                break
            sid = _active_session_id()
            if sid is None:
                self._stop_agent()
                continue
            if self.session_id != sid or not self._agent_alive():
                self._stop_agent()
                try:
                    self._start_agent(sid)
                except Exception:
                    # 用户刚登录/切换会话时令牌可能暂时不可用，下一轮重试。
                    self.process = None
                    self.session_id = None
                    time.sleep(2)
        self._stop_agent()


def service_cli():
    """供 MyDesk.exe --service install/start/stop/remove 调用。"""
    # pywin32 的命令行解析读取 sys.argv，因此这里保留 install 等参数。
    win32serviceutil.HandleCommandLine(MyDeskService)


if __name__ == '__main__':
    service_cli()
