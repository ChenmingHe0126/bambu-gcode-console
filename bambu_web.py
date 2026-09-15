#!/usr/bin/env python3
"""
bambu_web.py — Bambu Lab A1 网页控制台(本机 HTTP → 打印机 MQTT 桥)。

用法:
    python bambu_web.py --ip 192.168.x.x --serial <序列号> --code <访问码> [--port 8347]

然后浏览器打开 http://127.0.0.1:8347 。
前端页面在同目录的 web_ui.html;协议细节见 bambu_console.py 和 README。
"""

import argparse
import json
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

from bambu_discovery import resolve

STATUS_KEYS = (
    "nozzle_temper", "nozzle_target_temper",
    "bed_temper", "bed_target_temper",
    "gcode_state", "mc_percent", "mc_remaining_time",
    "wifi_signal", "cooling_fan_speed", "subtask_name",
)


class BambuLink:
    """持有一条到打印机的 MQTT 连接,提供 send_gcode(等待受理回执)和状态缓存。"""

    def __init__(self, ip, serial, code):
        self.ip = ip
        self.serial = serial
        self.code = code
        self.status = {}
        self.connected = False
        self.last_report = 0.0
        self._seq = 100
        self._lock = threading.Lock()
        self._ack_events = {}   # seq -> threading.Event
        self._acks = {}         # seq -> (result, reason)

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        c.username_pw_set("bblp", code)
        # 打印机证书由 Bambu 私有 CA 签发,局域网内跳过校验(同 bambu_console.py)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        c.tls_set_context(ctx)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c

    def start(self):
        self.client.connect(self.ip, 8883, keepalive=30)
        self.client.loop_start()

    def _next_seq(self):
        with self._lock:
            self._seq += 1
            return str(self._seq)

    def _on_connect(self, c, userdata, flags, rc, props=None):
        if rc == 0:
            self.connected = True
            c.subscribe(f"device/{self.serial}/report")
            c.publish(
                f"device/{self.serial}/request",
                json.dumps({"pushing": {"sequence_id": self._next_seq(),
                                        "command": "pushall"}}),
            )
        else:
            print(f"[MQTT] 连接失败 rc={rc}", file=sys.stderr)

    def _on_disconnect(self, c, userdata, flags, rc, props=None):
        self.connected = False

    def _on_message(self, c, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        p = data.get("print", {})
        if p.get("command") == "gcode_line":
            seq = str(p.get("sequence_id"))
            self._acks[seq] = (str(p.get("result", "?")), p.get("reason") or "")
            ev = self._ack_events.get(seq)
            if ev:
                ev.set()
        for k in STATUS_KEYS:
            if k in p:
                self.status[k] = p[k]
        self.last_report = time.time()

    def send_gcode(self, gcode, timeout=3.0):
        """发送一行或多行(\n 分隔)G-code,等待受理回执。"""
        seq = self._next_seq()
        ev = threading.Event()
        self._ack_events[seq] = ev
        payload = {"print": {"command": "gcode_line", "sequence_id": seq,
                             "param": gcode.rstrip("\n") + "\n"}}
        self.client.publish(f"device/{self.serial}/request", json.dumps(payload))
        got = ev.wait(timeout)
        self._ack_events.pop(seq, None)
        result, reason = self._acks.pop(seq, ("timeout", ""))
        if not got:
            result, reason = "timeout", "3 秒内未收到回执"
        return {"result": result, "reason": reason}


def make_handler(link: BambuLink, html_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 安静点,课堂投影不刷日志

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                try:
                    body = html_path.read_bytes()
                except OSError:
                    self.send_error(500, "web_ui.html not found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/status":
                stale = (time.time() - link.last_report) > 10
                self._json({
                    "connected": link.connected and not stale,
                    "serial": link.serial,
                    "ip": link.ip,
                    "status": link.status,
                })
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/gcode":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
                gcode = str(data.get("gcode", "")).strip()
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "bad request"}, 400)
                return
            if not gcode:
                self._json({"error": "empty gcode"}, 400)
                return
            self._json(link.send_gcode(gcode))

    return Handler


def main():
    # Windows 控制台默认 cp1252/GBK,中文输出会炸;强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Bambu A1 网页控制台")
    ap.add_argument("--ip", help="打印机局域网 IP(留空则 SSDP 自动发现)")
    ap.add_argument("--serial", help="打印机序列号(留空则 SSDP 自动发现)")
    ap.add_argument("--code", required=True, help="LAN-only 模式访问码")
    ap.add_argument("--port", type=int, default=8347, help="网页端口(默认 8347)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址;设为 0.0.0.0 可让同网段其他设备访问")
    args = ap.parse_args()

    found = resolve(args.ip, args.serial)
    if not found:
        print("没找到打印机:确认打印机开机且和电脑同网段;"
              "或去打印机屏幕 设置→网络 查 IP,用 --ip 传入(会自动记住)", file=sys.stderr)
        sys.exit(1)
    args.ip, args.serial = found["ip"], found["serial"]
    print(f"打印机: {found.get('name', '?')} ({found.get('model', '?')}) "
          f"@ {args.ip}  SN {args.serial}")

    link = BambuLink(args.ip, args.serial, args.code)
    try:
        link.start()
    except OSError as e:
        print(f"[无法连接打印机 {args.ip}:8883] {e}", file=sys.stderr)
        sys.exit(1)

    html_path = Path(__file__).resolve().parent / "web_ui.html"
    server = ThreadingHTTPServer((args.host, args.port), make_handler(link, html_path))
    print(f"网页控制台: http://{args.host}:{args.port}  (Ctrl+C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        link.client.loop_stop()


if __name__ == "__main__":
    main()
