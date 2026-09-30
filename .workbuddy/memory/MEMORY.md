# MyDesk 项目长期备忘

## 运行环境 (关键, 已验证)
- **正确 conda 环境**: `C:\Users\Administrator\.conda\envs\mydesk\python.exe`
  - PyQt5 5.15.11 + mss 10.2.0 + Pillow 12.3.0 + pywin32 312 + numpy 2.2.6
  - ⚠️ 不要用 `E:\app\minconda\envs\py38`（该路径不存在或缺依赖, 会闪退）。
- 依赖清单见 `requirements.txt`（已按真实 import 校正: 需要 mss 与 pywin32）。
- 无头验证: `QT_QPA_PLATFORM=offscreen MYDESK_NO_TRAY=1 <python> main.py`

## 版本控制 (本次新增)
- 已 `git init`，`a6f874d` 是**修复前的完整快照**（含《代码审查报告.md》）。
- 想回到修复前: `git checkout a6f874d`；想看改了什么: `git diff a6f874d -- <file>`。
- `.gitignore` 已排除 `build/ dist/ __pycache__/ *.log _launch.log .acl-recovery/`。

## 项目结构 (当前)
- 入口 `main.py`：QApplication + MainWindow + SystemTray + 被控端启动 + 连接调度。
  - `--silent` = 只跑被控端（无窗口/托盘），开机自启用它。
  - 单实例互斥锁；后台线程一律通过 `_AppBridge` 的 Qt 信号回到 GUI 线程。
- 界面层 `gui/`：`main_window.py` / `viewer_window.py` / `tray.py` / `theme.py`
  / `logger.py`（2MB 轮转） / `icons.py` / `widgets.py`。
- 协议层 `protocol.py`：帧格式 + 消息类型 + 事件/指令常量 + **v2 挑战-响应认证**。
- 被控端 `controlled.py`（认证/限流/广播订阅/PAUSE/剪贴板/LAN-only/白名单）
  + `screen_broadcaster.py`（多显示器、按客户端暂停、光标推送）
  + `input_control.py`（虚拟桌面坐标注入）+ `clipboard_win.py`。
- 主控端 `controller.py`（接收/心跳RTT/输入发送线程/断开回调/注册前缓存）
  + `file_transfer.py`（`.part` + 完成校验 + 发送队列 + 多播监听）。
- 配置 `config.py`：原子写 JSON、设置项、DPAPI 加密口令、历史记录。
- 安装: `installer.py` + `install_utils.py`（含防火墙规则）; 打包 `build.py`。
- 已删除死代码 `ui.py` / `viewer.py` / `screen.py`（在 git 历史里）。

## 验证工具 (在 .acl-recovery/, 用 git 忽略)
- `probe_protocol.py` 24 项、`probe_filetransfer.py` 28 项、
  `probe_gui.py` 61 项、`probe_smoke.py` 16 项 —— **合计 129 项, 全绿**。
- 改动后请至少跑 `probe_smoke.py`（真实入口 + 两轮会话 + 日志增量回归）。

## 已知未做（下一步的架构级任务）
1. H.264/硬件编码 + 脏矩形（目前整帧 JPEG, 2560x1440 ≈ 330KB/帧）。
2. Windows 服务化（才能过 UAC/锁屏做真无人值守）。
3. 流加密 + 自建中继（当前：认证已防重放，但数据明文；建议只在局域网/Tailscale 用）。
