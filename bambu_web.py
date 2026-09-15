#!/usr/bin/env python3
"""
bambu_web.py — Bambu Lab A1 网页控制台(本机 HTTP → 打印机 MQTT 桥)。

日常用法:双击 启动控制台.bat(或直接 python bambu_web.py),浏览器会自动
打开 http://127.0.0.1:8347,在页面上点「连接」即可 —— 访问码、IP、序列号
都记在 ~/.bambu-gcode-console.json 里,首次填一次以后就不用再填。

命令行参数(都可选,填了会存进配置):
    python bambu_web.py [--code 访问码] [--ip x.x.x.x] [--serial SN]
                        [--port 8347] [--host 127.0.0.1] [--no-browser]

前端页面在同目录的 web_ui.html;协议细节见 bambu_console.py 和 README。
"""

import argparse
import json
import ssl
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

from bambu_discovery import load_cache, resolve, save_cache

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
        self.auth_failed = False
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

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def _next_seq(self):
        with self._lock:
            self._seq += 1
            return str(self._seq)

    def _on_connect(self, c, userdata, flags, rc, props=None):
        if rc == 0:
            self.connected = True
            self.auth_failed = False
            c.subscribe(f"device/{self.serial}/report")
            c.publish(
                f"device/{self.serial}/request",
                json.dumps({"pushing": {"sequence_id": self._next_seq(),
                                        "command": "pushall"}}),
            )
        else:
            self.auth_failed = True
            print(f"[MQTT] 连接被拒 rc={rc}", file=sys.stderr)

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


class App:
    """连接状态机:disconnected → connecting → connected / error。"""

    def __init__(self, ip=None, serial=None, code=None):
        seed = {k: v for k, v in (("ip", ip), ("serial", serial), ("code", code)) if v}
        if seed:
            save_cache(seed)
        self.link = None
        self.phase = "disconnected"
        self.error = ""
        self._busy = threading.Lock()

    def start_connect(self, code=None, ip=None):
        upd = {}
        if code:
            upd["code"] = code
        if ip:
            upd["ip"] = ip
        if upd:
            save_cache(upd)
        if not load_cache().get("code"):
            self.phase, self.error = "error", "请输入访问码(打印机 LAN-only 模式页面上那 8 位)"
            return
        if not self._busy.acquire(blocking=False):
            return  # 已经在连了
        self.phase, self.error = "connecting", ""
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        try:
            cfg = load_cache()
            found = resolve(cfg.get("ip"), cfg.get("serial"))
            if not found:
                self.phase = "error"
                self.error = ("没找到打印机:确认开机且和电脑同网段;"
                              "校园网收不到广播时,去打印机 设置→网络 查 IP 填到下面")
                return
            if self.link:
                self.link.stop()
                self.link = None
            link = BambuLink(found["ip"], found["serial"], load_cache().get("code", ""))
            try:
                link.start()
            except OSError as e:
                self.phase, self.error = "error", f"连不上 {found['ip']}:8883 — {e}"
                return
            for _ in range(60):  # 最多等 6 秒
                if link.connected or link.auth_failed:
                    break
                time.sleep(0.1)
            if link.connected:
                self.link = link
                self.phase, self.error = "connected", ""
                print(f"已连接打印机 {found.get('name', '?')} @ {found['ip']}")
            else:
                link.stop()
                self.phase = "error"
                self.error = ("连接被拒或超时:访问码对不对?"
                              "LAN-only 模式和开发者模式都开了吗?")
        finally:
            self._busy.release()


def make_handler(app: App, html_path: Path):
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
                cfg = load_cache()
                link = app.link
                stale = (link is None) or (time.time() - link.last_report > 10)
                self._json({
                    "phase": app.phase,
                    "error": app.error,
                    "connected": bool(link and link.connected and not stale),
                    "ip": cfg.get("ip", ""),
                    "serial": cfg.get("serial", ""),
                    "code": cfg.get("code", ""),
                    "status": link.status if link else {},
                })
            else:
                self.send_error(404)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}

        def do_POST(self):
            if self.path == "/api/connect":
                try:
                    data = self._body()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                app.start_connect(code=str(data.get("code", "")).strip(),
                                  ip=str(data.get("ip", "")).strip())
                self._json({"phase": app.phase, "error": app.error})
            elif self.path == "/api/gcode":
                try:
                    data = self._body()
                    gcode = str(data.get("gcode", "")).strip()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                if not gcode:
                    self._json({"error": "empty gcode"}, 400)
                    return
                link = app.link
                if not (link and link.connected):
                    self._json({"result": "error", "reason": "还没连接打印机"})
                    return
                self._json(link.send_gcode(gcode))
            else:
                self.send_error(404)

    return Handler


def main():
    # Windows 控制台默认 cp1252/GBK,中文输出会炸;强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Bambu A1 网页控制台")
    ap.add_argument("--ip", help="打印机 IP(可选,会记住)")
    ap.add_argument("--serial", help="打印机序列号(可选,会记住)")
    ap.add_argument("--code", help="LAN-only 访问码(可选,会记住)")
    ap.add_argument("--port", type=int, default=8347, help="网页端口(默认 8347)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址;设为 0.0.0.0 可让同网段其他设备访问")
    ap.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = ap.parse_args()

    app = App(args.ip, args.serial, args.code)
    html_path = Path(__file__).resolve().parent / "web_ui.html"
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(app, html_path))
    except OSError as e:
        print(f"[端口 {args.port} 起不来] {e}(已经开了一个控制台?)", file=sys.stderr)
        sys.exit(1)

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"网页控制台: {url}  (Ctrl+C 或直接关窗口退出)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        if app.link:
            app.link.stop()


if __name__ == "__main__":
    main()
