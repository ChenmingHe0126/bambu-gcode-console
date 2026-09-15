#!/usr/bin/env python3
"""
bambu_discovery.py — 自动定位局域网里的 Bambu 打印机。

三级回退链(resolve):
  1. 显式传入的 --ip:先 TCP 探测 8883 端口,通了直接用
  2. 上次成功连接的缓存地址(~/.bambu-gcode-console.json)
  3. SSDP 被动监听:打印机每隔几秒向 UDP 1990/2021 广播一条 NOTIFY,
     带 IP(Location)、序列号(USN)、机型(DevModel.bambu.com)和设备名

注意:企业/校园 WiFi 常过滤客户端间广播,Windows 防火墙在 Public 网络下也会
拦入站 UDP——所以 SSDP 放在最后,平时靠缓存就够了。
"""

import json
import select
import socket
import time
from pathlib import Path

CACHE_FILE = Path.home() / ".bambu-gcode-console.json"
SSDP_PORTS = (2021, 1990)


def probe(ip, port=8883, timeout=1.5):
    """TCP 探测打印机的 MQTT 端口是否可达。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(info):
    """合并写入(文件同时存着访问码等其他字段,别整个覆盖)。"""
    try:
        merged = load_cache()
        merged.update({k: v for k, v in info.items() if v})
        CACHE_FILE.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 缓存写不进去不致命


def discover(timeout=20.0):
    """被动监听 SSDP 广播;返回 {"ip","serial","model","name"} 或 None。"""
    socks = []
    for port in SSDP_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            s.setblocking(False)
            socks.append(s)
        except OSError:
            continue  # 被 Bambu Studio/OrcaSlicer 占用就跳过这个端口
    if not socks:
        print("[SSDP] cannot listen on ports 1990/2021 (Bambu Studio/OrcaSlicer running?)")
        return None
    end = time.time() + timeout
    try:
        while time.time() < end:
            readable, _, _ = select.select(socks, [], [], 1.0)
            for s in readable:
                try:
                    data, addr = s.recvfrom(4096)
                except OSError:
                    continue
                info = _parse(data.decode(errors="replace"), addr[0])
                if info:
                    return info
    finally:
        for s in socks:
            s.close()
    return None


def _parse(txt, sender_ip):
    headers = {}
    for line in txt.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    if "bambulab" not in headers.get("nt", "") and "devmodel.bambu.com" not in headers:
        return None
    return {
        "ip": headers.get("location") or sender_ip,
        "serial": headers.get("usn", ""),
        "model": headers.get("devmodel.bambu.com", "?"),
        "name": headers.get("devname.bambu.com", "?"),
    }


def resolve(ip=None, serial=None):
    """按 显式参数 → 缓存 → SSDP 的顺序确定打印机;返回 info dict 或 None。"""
    cache = load_cache()
    serial = serial or cache.get("serial", "")

    if ip:
        if probe(ip):
            if serial:
                info = {"ip": ip, "serial": serial,
                        "model": cache.get("model", "?"), "name": cache.get("name", "?")}
                save_cache(info)
                return info
            print(f"[{ip}] reachable but serial unknown, trying auto-discovery…")
        else:
            print(f"[{ip}] port 8883 unreachable (printer IP changed?), trying fallbacks…")

    cached_ip = cache.get("ip")
    if cached_ip and cached_ip != ip and serial and probe(cached_ip):
        print(f"Using last known address {cached_ip}")
        info = dict(cache, ip=cached_ip, serial=serial)
        save_cache(info)
        return info

    print("Listening for printer SSDP broadcast (up to 20 s)…")
    found = discover()
    if found:
        save_cache(found)
        return found
    return None


if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    found = resolve()
    print(found if found else
          "Printer not found. Check it is on and on the same network; if broadcasts are "
          "filtered (campus Wi-Fi), read the IP from the printer's Settings > Network "
          "screen and pass it via --ip (it will be remembered).")
