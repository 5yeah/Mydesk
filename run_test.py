"""
启动脚本 - 用于测试 MyDesk
双击此文件启动测试版本（有控制台输出）
"""
import sys
import os

# 切换到脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("MyDesk 启动测试")
print("=" * 50)

# 导入并运行主程序
from main import main

print("正在启动 MyDesk...")
main()
