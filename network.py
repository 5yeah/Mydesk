# -*- coding: utf-8 -*-
"""MyDesk 网络工具: 本机 IP 检测、Tailscale IP 识别、连接信息展示

内网穿透方案:
  1. 局域网: 直接用本机 IP (get_lan_ip) 连接
  2. 外网: 安装 Tailscale (https://tailscale.com), 两台设备登录同一账号后
     自动获得 100.x.x.x 虚拟 IP, 任意网络均可互连, 无需自建服务器
  3. 备选: frp (需自有公网 VPS)

用法:
  python network.py          # 显示本机所有 IP 和连接信息
"""
import socket


def get_lan_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def get_all_ips():
    """获取本机所有 IPv4 地址"""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith('127.'):
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        ips.append(get_lan_ip())
    return ips


def get_tailscale_ip():
    """检测 Tailscale 虚拟 IP (100.64.0.0/10 网段)"""
    for ip in get_all_ips():
        parts = ip.split('.')
        if len(parts) == 4 and parts[0] == '100' and 64 <= int(parts[1]) <= 127:
            return ip
    return None


def print_connection_info(port=7788):
    """打印连接信息"""
    print('=' * 50)
    print('  MyDesk 连接信息')
    print('=' * 50)
    lan = get_lan_ip()
    ts = get_tailscale_ip()
    print(f'  局域网 IP:  {lan}:{port}  (同网段连接用此)')
    if ts:
        print(f'  Tailscale:  {ts}:{port}  (外网任意位置连接用此)')
    else:
        print('  Tailscale:  未检测到')
        print('  外网连接请安装 Tailscale: https://tailscale.com')
    print('=' * 50)


if __name__ == '__main__':
    print_connection_info()
