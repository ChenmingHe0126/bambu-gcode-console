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
            print(f"[已连接 {args.ip}] 订阅状态推送…")
            c.subscribe(f"device/{args.serial}/report")
            # 请求一次全量状态,拿到初始温度等
            c.publish(
                f"device/{args.serial}/request",
                json.dumps({"pushing": {"sequence_id": next_seq(), "command": "pushall"}}),
            )
        else:
            print(f"[连接失败] rc={rc}(检查 IP/访问码,以及开发者模式是否已开启)")

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
        print("  (还没收到状态推送,稍等 1-2 秒再试)")
        return
    n = last_status.get("nozzle_temper", "?")
    nt = last_status.get("nozzle_target_temper", "?")
    b = last_status.get("bed_temper", "?")
    bt = last_status.get("bed_target_temper", "?")
    st = last_status.get("gcode_state", "?")
    print(f"  喷嘴 {n}/{nt}°C  热床 {b}/{bt}°C  状态 {st}")


HELP = """命令:
  <任意 G-code>   逐行发送执行,如 G28 / G90 / G1 X128 Y128 F6000 / M104 S150
  status          查看喷嘴/热床温度和打印机状态
  help            显示本帮助
  exit / quit     退出
注意:gcode_line 只回"已接受",不回位置/参数(M114 之类不会有输出);
      移动前先 G28 归零,课堂演示建议温度不超过 M104 S150。"""


def main():
    # Windows 控制台默认 cp1252/GBK,中文输出会炸;强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Bambu A1 逐行 G-code 控制台")
    ap.add_argument("--ip", help="打印机局域网 IP(留空则 SSDP 自动发现)")
    ap.add_argument("--serial", help="打印机序列号(留空则 SSDP 自动发现)")
    ap.add_argument("--code", help="LAN-only 模式访问码(填过一次会记住)")
    args = ap.parse_args()

    if not args.code:
        args.code = load_cache().get("code", "")
    if not args.code:
        try:
            args.code = input("访问码 Access Code(LAN-only 模式页面上那 8 位): ").strip()
        except (EOFError, KeyboardInterrupt):
            args.code = ""
    if not args.code:
        print("没有访问码,退出")
        sys.exit(1)
    save_cache({"code": args.code})

    found = resolve(args.ip, args.serial)
    if not found:
        print("没找到打印机:确认打印机开机且和电脑同网段;"
              "或去打印机屏幕 设置→网络 查 IP,用 --ip 传入(会自动记住)")
        sys.exit(1)
    args.ip, args.serial = found["ip"], found["serial"]
    print(f"打印机: {found.get('name', '?')} ({found.get('model', '?')}) "
          f"@ {args.ip}  SN {args.serial}")

    client = make_client(args)
    try:
        client.connect(args.ip, 8883, keepalive=30)
    except OSError as e:
        print(f"[无法连接 {args.ip}:8883] {e}")
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
    print("再见")


if __name__ == "__main__":
    main()
