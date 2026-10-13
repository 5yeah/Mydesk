# MyDesk

MyDesk 是一款面向 Windows 的开源远程控制与文件传输桌面工具，界面与交互对标 ToDesk：左侧本地、右侧远程的双栏文件传输、设备 ID / IP 双模式连接、全屏浮动工具栏、系统托盘常驻。

当前版本：**1.2.0**（见 `config.py` 的 `VERSION`）

## 功能特性

- **远程桌面**：实时远程控制，支持「远程控制 / 文件传输 / 仅观看」三种连接模式
- **多显示器**：被控端每块屏幕独立开窗，逐屏采集、逐屏传输
- **文件传输**：左本地 / 右远程双栏浏览，上传、下载、删除，下载默认保存到 `~/Desktop/MyDesk_接收`
- **设备发现**：同一局域网内 UDP 广播自动发现在线设备，也支持按设备 ID 或 IP 手动连接
- **连接历史**：自动记录最近 20 条连接（设备 ID、IP、时间），选中即回填
- **连接密码保护**：密码经 Windows DPAPI 加密后落盘，仅当前用户可解密
- **剪贴板同步**、**开机自启**、**Windows 服务化保活**（掉线自动拉起）
- **高性能抓屏**：优先使用 dxcam（DXGI GPU 直读），自动回退到 mss
- **低延迟链路**：TCP_NODELAY 关闭 Nagle 算法，`timeBeginPeriod(1)` 提升定时精度

## 运行环境

- Windows 10 / 11
- Python 3.8+（含 64 位）

## 快速开始

```powershell
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动主程序（主控端 + 被控端 + 托盘）
python main.py
```

也可以双击 `启动MyDesk.bat` 启动，崩溃日志会写入 `_launch.log`。

> 抓屏加速（可选）：`pip install dxcam`，未安装时自动使用 mss 后端。

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `python main.py --silent` | 纯后台被控端：不创建窗口与托盘（开机自启使用） |
| `python main.py --agent` | 会话代理：由 Windows 服务按当前登录用户拉起 |
| `python main.py --service install\|start\|stop\|remove` | 管理 `MyDeskService` Windows 服务 |

## 打包发布

```powershell
pip install pyinstaller
python build.py             # 构建主程序 + 安装包
python build.py --installer # 主程序已存在时，仅重建安装包
```

产出：

- `dist/MyDesk.exe` — 单文件主程序
- `dist/MyDesk_Setup.exe` — 单文件安装包，分发这个即可

服务化部署细节见 [docs/服务化部署说明.md](docs/服务化部署说明.md)。

## 端口与配置

| 项目 | 默认值 | 说明 |
| --- | --- | --- |
| TCP 控制端口 | `7788` | 远程控制与文件传输通道，被占用时自动顺延 |
| UDP 发现端口 | `7789` | 局域网设备发现（与 TCP 端口解耦） |
| 连接密码 | `123456` | 首次启动的默认密码，请及时修改 |
| 配置目录 | `~/.mydesk/` | `config.json`：设备 ID、密码、连接历史 |
| 数据目录 | `~/MyDesk_Data/` | `settings.json`、`device_list.json` |
| 日志目录 | `~/MyDesk_Logs/` | `crash.log`、`faulthandler.log` |

## 目录结构

```
.
├── main.py                  # 主入口：QApplication、窗口/托盘装配、连接调度
├── config.py                # 配置与历史记录（原子写 + DPAPI 加密）
├── protocol.py              # 通信协议与命令定义
├── network.py               # TCP/UDP 网络基础设施
├── controller.py            # 主控端会话逻辑
├── controlled.py            # 被控端会话逻辑
├── screen_broadcaster.py    # 屏幕采集与广播（dxcam / mss 双后端）
├── file_transfer.py         # 文件传输服务端与协议
├── input_control.py         # 远程输入注入（键鼠）
├── clipboard_win.py         # Windows 剪贴板同步
├── server_ui.py             # 被控端托盘入口
├── autostart.py             # 开机自启（注册表 / 计划任务 / 服务）
├── windows_service.py       # MyDeskService Windows 服务宿主
├── build.py                 # PyInstaller 打包脚本
├── installer.py             # 图形化安装器（配合 install_utils.py）
├── gui/                     # 界面层：主窗口、会话窗口、托盘、主题、组件
└── docs/                    # 部署文档
```

## 已知限制

- 锁屏与 UAC 属于 Windows 安全桌面，当前屏幕捕获无法读取；日志出现 `BitBlt: 拒绝访问` 属于预期现象。
- 仅支持 Windows 平台（依赖 pywin32、DPAPI、DXGI）。

## 许可证

[MIT](LICENSE)
