"""
极简版本 - 直接显示界面，无其他功能
用于排查界面显示问题
"""
import sys
import os
import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("MyDesk 极简测试版")
print("=" * 60)

# 捕获所有异常
import traceback

def exception_hook(exc_type, exc_value, exc_tb):
    print("!!! 程序异常 !!!")
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        with open(os.path.join(os.path.expanduser('~'), 'MyDesk_Logs', 'crash.log'), 'a') as f:
            f.write(f'[{datetime.datetime.now()}] 异常: {exc_value}\n')
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except:
        pass

sys.excepthook = exception_hook

try:
    print("\n[1] 导入 PyQt5...")
    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
    from PyQt5.QtCore import Qt, QTimer
    print("✓ PyQt5 导入成功")
    
    print("\n[2] 创建 QApplication...")
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    print("✓ QApplication 创建成功")
    
    print("\n[3] 创建主窗口...")
    # 使用重构后的 gui.main_window.MainWindow
    from gui.main_window import MainWindow
    window = MainWindow()
    window.resize(1280, 800)
    print("✓ MainWindow 创建成功")
    
    print("\n[4] 创建托盘图标...")
    from gui.icons import make_tray_icon
    icon = make_tray_icon()
    tray = QSystemTrayIcon(icon)
    tray.setToolTip('MyDesk - 双击打开')
    tray.show()
    
    menu = QMenu()
    act_show = QAction('📂 显示窗口', app)
    act_show.triggered.connect(lambda: (window.show(), window.activateWindow(), window.raise_()))
    menu.addAction(act_show)
    
    act_quit = QAction('❌ 退出', app)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: (window.show(), window.activateWindow(), window.raise_()) if reason == QSystemTrayIcon.DoubleClick else None)
    
    tray.showMessage('MyDesk 已启动', '双击托盘图标打开主界面', QSystemTrayIcon.Information, 3000)
    print("✓ 托盘图标创建成功")
    
    print("\n[5] 显示主窗口...")
    window.show()
    window.activateWindow()
    window.raise_()
    print("✓ 主窗口已显示")
    
    # 3秒后检查状态
    def check_status():
        print(f"\n[检查] 窗口可见: {window.isVisible()}")
        print(f"[检查] 托盘可见: {tray.isVisible()}")
        if window.isVisible():
            print("✓✓✓ 界面显示正常！")
        else:
            print("✗✗✗ 窗口未显示，请检查防火墙或权限")
    
    QTimer.singleShot(3000, check_status)
    
    print("\n[6] 启动事件循环...")
    print("   如果窗口没有弹出，请查看控制台错误信息")
    sys.exit(app.exec_())

except Exception as e:
    print(f"\n!!! 启动失败: {e} !!!")
    traceback.print_exc()
    input("按回车键退出...")
