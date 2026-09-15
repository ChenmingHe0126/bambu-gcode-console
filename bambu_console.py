#!/usr/bin/env python3
"""
bambu_console.py — 在 Bambu Lab A1 上逐行发送 G-code 的迷你控制台(Pronterface 风格)。

用法:
    pip install "paho-mqtt>=2.0"
    python bambu_console.py --ip 192.168.x.x --serial <打印机序列号> --code <访问码>

前提(在打印机屏幕上设置,详见 README):
  1. 设置 -> 网络:开启「仅局域网模式 (LAN-only Mode)」,首次开启后重启打印机
  2. 同一菜单里开启「开发者模式 (Developer Mode)」——固件 01.05.00.00+ 必须开,
     否则打印机会拒绝一切控制指令(只读状态不受影响)
  3. 访问码显示在 LAN-only 模式页面上;序列号在 设置 -> 设备信息 或机身贴纸上

协议(见 OpenBambuAPI 文档):MQTT over TLS, 端口 8883, 用户名 bblp, 密码 = 访问码。
  发送: device/{serial}/request  {"print": {"command": "gcode_line", "sequence_id": "N", "param": "G28\n"}}
  接收: device/{serial}/report   打印机会回一条带同样 sequence_id 的 result(表示"已接受",
        不代表动作执行完毕),并周期性推送温度/状态 JSON。
"""

import argparse
import json
import ssl
import sys
import time

import paho.mqtt.client as mqtt

from bambu_discovery import load_cache, resolve, save_cache

seq = 0
last_status = {}  # 最近一次 report 里关心的字段缓存


def next_seq():
    global seq
    seq += 1
    return str(seq)


STATUS_KEYS = (
    "nozzle_temper", "nozzle_target_temper",
    "bed_temper", "bed_target_temper",
    "gcode_state", "mc_print_stage", "wifi_signal",
)


def make_client(args):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    client.username_pw_set("bblp", args.code)
    # 打印机证书由 Bambu 私有 CA 签发,公网 CA 无法验证。
    # 课堂局域网环境下直接跳过校验;如需严格校验,可改为固定信任
    # OpenBambuAPI 仓库里的 ca_cert.pem(见 README)。
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ctx)

    def on_connect(c, userdata, flags, rc, props=None):
        if rc == 0:
            print(f"[connected to {args.ip}] subscribing to status reports…")
            c.subscribe(f"device/{args.serial}/report")
            # 请求一次全量状态,拿到初始温度等
            c.publish(
                f"device/{args.serial}/request",
                json.dumps({"pushing": {"sequence_id": next_seq(), "command": "pushall"}}),
            )
        else:
            print(f"[connection refused] rc={rc} (check IP/access code, and that Developer Mode is on)")

    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        p = data.get("print", {})
        # G-code 指令的受理回执(类似串口的 "ok" / "error")
        if p.get("command") == "gcode_line":
            result = str(p.get("result", "?")).lower()
            reason = p.get("reason") or ""
            tag = "ok" if result == "success" else f"FAILED {reason}".strip()
            print(f"  << [{p.get('sequence_id')}] {tag}")
        # 缓存状态字段,供 status 命令查看
        for k in STATUS_KEYS:
            if k in p:
                last_status[k] = p[k]

    client.on_connect = on_connect
    client.on_message = on_message
    return client


def send_gcode(client, serial, line):
    payload = {
        "print": {
            "command": "gcode_line",
            "sequence_id": next_seq(),
            "param": line + "\n",
        }
    }
    client.publish(f"device/{serial}/request", json.dumps(payload))
    print(f"  >> {line}")


def print_status():
    if not last_status:
        print("  (no status report yet — wait a second and try again)")
        return
    n = last_status.get("nozzle_temper", "?")
    nt = last_status.get("nozzle_target_temper", "?")
    b = last_status.get("bed_temper", "?")
    bt = last_status.get("bed_target_temper", "?")
    st = last_status.get("gcode_state", "?")
    print(f"  nozzle {n}/{nt}°C  bed {b}/{bt}°C  state {st}")


HELP = """Commands:
  <any G-code>    executed line by line, e.g. G28 / G90 / G1 X128 Y128 F6000 / M104 S150
  status          show nozzle/bed temperatures and printer state
  help            show this help
  exit / quit     leave the console
Note: gcode_line only acks acceptance — query commands like M114 return no
      output. Home first (G28) before moving; keep demo temps at or below
      M104 S150."""


def main():
    # Windows 控制台默认 cp1252/GBK,中文输出会炸;强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Line-by-line G-code console for Bambu Lab A1")
    ap.add_argument("--ip", help="printer LAN IP (omit to auto-discover)")
    ap.add_argument("--serial", help="printer serial number (omit to auto-discover)")
    ap.add_argument("--code", help="LAN-only Mode access code (remembered after first use)")
    args = ap.parse_args()

    if not args.code:
        args.code = load_cache().get("code", "")
    if not args.code:
        try:
            args.code = input("Access Code (8 characters on the LAN-only Mode screen): ").strip()
        except (EOFError, KeyboardInterrupt):
            args.code = ""
    if not args.code:
        print("No access code, exiting")
        sys.exit(1)
    save_cache({"code": args.code})

    found = resolve(args.ip, args.serial)
    if not found:
        print("Printer not found. Check it is on and on the same network, or read the IP "
              "from the printer's Settings > Network screen and pass --ip (remembered).")
        sys.exit(1)
    args.ip, args.serial = found["ip"], found["serial"]
    print(f"Printer: {found.get('name', '?')} ({found.get('model', '?')}) "
          f"@ {args.ip}  SN {args.serial}")

    client = make_client(args)
    try:
        client.connect(args.ip, 8883, keepalive=30)
    except OSError as e:
        print(f"[cannot reach {args.ip}:8883] {e}")
        sys.exit(1)
    client.loop_start()
    time.sleep(1.5)

    print(HELP)
    while True:
        try:
            line = input("gcode> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        low = line.lower()
        if low in ("exit", "quit"):
            break
        if low == "status":
            print_status()
            continue
        if low == "help":
            print(HELP)
            continue
        send_gcode(client, args.serial, line)

    client.loop_stop()
    client.disconnect()
    print("Bye")


if __name__ == "__main__":
    main()
