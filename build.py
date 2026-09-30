# -*- coding: utf-8 -*-
"""MyDesk 打包脚本: 生成安装包 + 主程序

生成:
  dist/MyDesk.exe         - 主程序 (被控端+主控端+托盘)
  dist/MyDesk_Setup.exe   - 安装包 (内嵌 MyDesk.exe)

使用顺序:
  1. 先运行本脚本生成两个 EXE
  2. 用户只需分发 MyDesk_Setup.exe (单文件安装包)
  3. 其他电脑双击 MyDesk_Setup.exe 即可安装

用法:
  python build.py            # 构建主程序 + 安装包
  python build.py --installer # 仅构建安装包 (主程序已存在)
"""
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# 使用 python -m PyInstaller 方式调用，确保在conda环境下也能找到
PYI = [sys.executable, '-m', 'PyInstaller']

HIDDEN = ['mss', 'PIL', 'pywin32', 'win32com', 'win32com.shell',
          'win32com.shell.shellcon', 'pythoncom', 'win32crypt']

# conda 环境下 _ctypes 依赖 ffi DLL
_FFI_DIR = os.path.join(os.path.dirname(sys.executable), 'Library', 'bin')
_BINARIES = []
for ffi in ['ffi-7.dll', 'ffi-8.dll', 'ffi.dll']:
    p = os.path.join(_FFI_DIR, ffi)
    if os.path.exists(p):
        _BINARIES.append(f'{p};.')


def build_main():
    """打包主程序 MyDesk.exe"""
    args = PYI + ['--onefile', '--name', 'MyDesk',
            '--distpath', os.path.join(HERE, 'dist'),
            '--workpath', os.path.join(HERE, 'build'),
            '--specpath', os.path.join(HERE, 'build'),
            '--noconsole']
    for h in HIDDEN:
        args += ['--hidden-import', h]
    for b in _BINARIES:
        args += ['--add-binary', b]
    # 仅作存在性核对 (PyInstaller 会从 main.py 自动收集 gui 包及依赖)
    for src in ['main.py', 'controller.py', 'controlled.py', 'screen_broadcaster.py',
                'file_transfer.py', 'config.py', 'network.py', 'protocol.py',
                'input_control.py', 'autostart.py', 'clipboard_win.py', 'server_ui.py']:
        if not os.path.exists(os.path.join(HERE, src)):
            print(f'[警告] 源文件缺失: {src}')
    args.append(os.path.join(HERE, 'main.py'))
    print('=== 打包 MyDesk 主程序 ===')
    print(' '.join(args))
    return subprocess.run(args, cwd=HERE).returncode == 0


def build_installer():
    """打包安装包 MyDesk_Setup.exe (嵌入 MyDesk.exe)"""
    main_exe = os.path.join(HERE, 'dist', 'MyDesk.exe')
    if not os.path.exists(main_exe):
        print('错误: 请先构建主程序 MyDesk.exe')
        return False

    args = PYI + ['--onefile', '--name', 'MyDesk_Setup',
            '--distpath', os.path.join(HERE, 'dist'),
            '--workpath', os.path.join(HERE, 'build'),
            '--specpath', os.path.join(HERE, 'build'),
            '--noconsole',
            # 嵌入 MyDesk.exe (PyInstaller --add-data src;dest)
            '--add-data', f'{main_exe};.',
            ]
    for h in HIDDEN:
        args += ['--hidden-import', h]
    for b in _BINARIES:
        args += ['--add-binary', b]
    args.append(os.path.join(HERE, 'installer.py'))
    print('=== 打包 MyDesk_Setup 安装包 ===')
    print(' '.join(args))
    return subprocess.run(args, cwd=HERE).returncode == 0


if __name__ == '__main__':
    only_installer = '--installer' in sys.argv
    ok = True
    if not only_installer:
        ok = build_main()
    if ok:
        ok = build_installer()
    if ok:
        print('\n=== 打包完成! ===')
        print(f'主程序:    {os.path.join(HERE, "dist", "MyDesk.exe")}')
        print(f'安装包:    {os.path.join(HERE, "dist", "MyDesk_Setup.exe")}')
        print('\n分发说明:')
        print('  其他电脑只需拷贝 MyDesk_Setup.exe, 双击安装即可')
    else:
        print('\n打包失败, 请检查错误信息')
        sys.exit(1)