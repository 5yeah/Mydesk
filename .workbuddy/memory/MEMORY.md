# MyDesk 项目长期备忘

## 运行环境 (关键, 已验证)
- **正确 conda 环境**: `C:\Users\Administrator\.conda\envs\mydesk\python.exe`
  - 含 PyQt5 5.15.11 + mss + pynput + win32api + numpy, 运行依赖齐全。
- ⚠️ 不要再用 `E:\app\minconda\envs\py38`：该机此路径不存在或缺运行依赖, 用它会闪退。
- 所有启动脚本 (.bat) 的 PYTHON 均指向 mydesk 环境。
- 无头验证命令: `QT_QPA_PLATFORM=offscreen C:\Users\Administrator\.conda\envs\mydesk\python.exe main.py`
  (卡在事件循环=启动正常; 不退出即无崩溃)

## 项目结构 (重构后)
- 界面层已包化为 `gui/`: `main_window.py`(MainWindow) / `viewer_window.py`(ViewerWindow+FileTransferDialog)
  / `tray.py`(SystemTray) / `theme.py`(统一 THEME) / `logger.py`(集中日志) / `icons.py` / `widgets.py`。
- `main.py` 为入口+应用编排, 全部 import 来自 gui。
- 一键启动: `启动MyDesk.bat` → `main.py`。测试: `启动测试.bat`(run_test.py) / `运行测试.bat` / `测试界面.bat`(test_minimal.py)。
- 旧 `ui.py`/`viewer.py` 仍保留作无 git 回退, 待实机验证后可删。
