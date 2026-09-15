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
import base64
import ftplib
import hashlib
import io
import json
import re
import ssl
import sys
import threading
import time
import webbrowser
import zipfile
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


# ── SD 卡:FTPS 上传 + 最小 .3mf 打包 ─────────────────────────────
# 开发者模式开放隐式 FTPS(端口 990);project_file 只认 .3mf,所以把
# gcode 包进社区验证过的最小 .3mf 结构(Metadata/plate_1.gcode + md5)。

class ImplicitFTPS(ftplib.FTP_TLS):
    """ftplib 只支持显式 FTPS;打印机用隐式(连上就 TLS),包一层。"""

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value


def _ftps_session(ip, code, timeout=10):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftps = ImplicitFTPS(context=ctx, timeout=timeout)
    ftps.connect(ip, 990)
    ftps.login("bblp", code)
    ftps.prot_p()
    return ftps


BAMBU_GCODE_HEADER = (
    "; HEADER_BLOCK_START\n"
    "; BambuStudio 02.00.03.54\n"
    "; model printer: Bambu Lab A1 0.4 nozzle\n"
    "; total layer number: 1\n"
    "; HEADER_BLOCK_END\n"
    "\n"
    "; CONFIG_BLOCK_START\n"
    "; printer_model = Bambu Lab A1\n"
    "; nozzle_diameter = 0.4\n"
    "; curr_bed_type = Textured PEI Plate\n"
    "; CONFIG_BLOCK_END\n"
    "\n"
    "; EXECUTABLE_BLOCK_START\n"
)


# 以下静态文件抄自 Bambu Studio 导出的 .gcode.3mf 结构(机型改为 A1/N2S)。
# 打印机屏幕靠这些元数据识别文件、显示缩略图并允许启动打印。
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="png" ContentType="image/png"/>\n'
    ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
    '</Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    ' <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>\n'
    ' <Relationship Target="/Metadata/plate_1.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>\n'
    ' <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>\n'
    '</Relationships>'
)
_MODEL = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<model unit="millimeter" xml:lang="en-US" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
    ' <metadata name="Application">BambuStudio-02.08.02.61</metadata>\n'
    ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
    ' <metadata name="Thumbnail_Middle">/Metadata/plate_1.png</metadata>\n'
    ' <metadata name="Thumbnail_Small">/Metadata/plate_1_small.png</metadata>\n'
    ' <resources>\n </resources>\n <build/>\n</model>'
)
_MODEL_SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<config>\n  <plate>\n'
    '    <metadata key="plater_id" value="1"/>\n'
    '    <metadata key="plater_name" value=""/>\n'
    '    <metadata key="locked" value="false"/>\n'
    '    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>\n'
    '    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>\n'
    '    <metadata key="thumbnail_no_light_file" value="Metadata/plate_no_light_1.png"/>\n'
    '    <metadata key="top_file" value="Metadata/top_1.png"/>\n'
    '    <metadata key="pick_file" value="Metadata/pick_1.png"/>\n'
    '    <metadata key="pattern_bbox_file" value="Metadata/plate_1.json"/>\n'
    '  </plate>\n</config>\n'
)
_MODEL_SETTINGS_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/Metadata/plate_1.gcode" Id="rel-1" Type="http://schemas.bambulab.com/package/2021/gcode"/>\n'
    '</Relationships>'
)
_PLATE_JSON = json.dumps({
    "bbox_all": [0, 0, 256, 256], "bbox_objects": [],
    "bed_type": "textured_plate", "filament_colors": ["#00C257"],
    "filament_ids": [0], "first_extruder": 0, "first_layer_time": 1.0,
    "is_seq_print": False, "nozzle_diameter": 0.4, "version": 2,
})
_FILAMENT_SEQ = '{"plate_1":{"nozzle_sequence":[0],"optimal_assignment":[0],"sequence":[1]}}'
_CUT_INFO = ('<?xml version="1.0" encoding="utf-8"?>\n<objects>\n <object id="1">\n'
             '  <cut_id id="0" check_sum="1" connectors_cnt="0"/>\n </object>\n</objects>\n')
# 1x1 深灰 PNG,前端没给缩略图时兜底
_FALLBACK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _slice_info(job_name):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<config>\n  <header>\n'
        '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
        '    <header_item key="X-BBL-Client-Version" value="02.08.02.61"/>\n'
        '  </header>\n  <plate>\n'
        '    <metadata key="index" value="1"/>\n'
        '    <metadata key="extruder_type" value="0"/>\n'
        '    <metadata key="nozzle_volume_type" value="0"/>\n'
        '    <metadata key="printer_model_id" value="N2S"/>\n'
        '    <metadata key="nozzle_diameters" value="0.4"/>\n'
        '    <metadata key="timelapse_type" value="0"/>\n'
        '    <metadata key="prediction" value="60"/>\n'
        '    <metadata key="weight" value="0.10"/>\n'
        '    <metadata key="pause_count" value="0"/>\n'
        '    <metadata key="first_layer_time" value="1.0"/>\n'
        '    <metadata key="outside" value="false"/>\n'
        '    <metadata key="support_used" value="false"/>\n'
        '    <metadata key="label_object_enabled" value="false"/>\n'
        f'    <object identify_id="1" name="{job_name}" skipped="false" />\n'
        '    <filament id="1" tray_info_idx="GFA00" type="PLA" color="#00C257" '
        'used_m="0.01" used_g="0.10" nozzle_diameter="0.40"/>\n'
        '  </plate>\n</config>\n'
    )


def make_3mf(gcode_text, job_name="demo", thumb_png=None):
    """把 gcode 包成 Bambu Studio 风格的完整 .gcode.3mf。

    结构照抄 Studio 导出文件(机型 A1/N2S、耗材标 PLA):打印机屏幕能
    列出、显示缩略图并启动。gcode 需要 HEADER/CONFIG/EXECUTABLE 三段
    护栏注释,没有就自动补;md5 附属文件为大写十六进制、无换行。
    """
    if "HEADER_BLOCK_START" not in gcode_text:
        gcode_text = (BAMBU_GCODE_HEADER + gcode_text.rstrip("\n")
                      + "\n; EXECUTABLE_BLOCK_END\n")
    gcode_bytes = gcode_text.encode("utf-8")
    md5 = hashlib.md5(gcode_bytes).hexdigest().upper()
    png = thumb_png or _FALLBACK_PNG
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", _MODEL)
        z.writestr("Metadata/plate_1.png", png)
        z.writestr("Metadata/plate_1_small.png", png)
        z.writestr("Metadata/plate_no_light_1.png", png)
        z.writestr("Metadata/top_1.png", png)
        z.writestr("Metadata/pick_1.png", png)
        z.writestr("Metadata/plate_1.json", _PLATE_JSON)
        z.writestr("Metadata/model_settings.config", _MODEL_SETTINGS)
        z.writestr("Metadata/_rels/model_settings.config.rels", _MODEL_SETTINGS_RELS)
        z.writestr("Metadata/slice_info.config", _slice_info(job_name))
        z.writestr("Metadata/filament_sequence.json", _FILAMENT_SEQ)
        z.writestr("Metadata/cut_information.xml", _CUT_INFO)
        z.writestr("Metadata/plate_1.gcode", gcode_bytes)
        z.writestr("Metadata/plate_1.gcode.md5", md5)
    return buf.getvalue()


def sd_upload(ip, code, name, data):
    """上传 .3mf。个别固件在数据传完后 226 响应超时——重连核对大小兜底。"""
    try:
        ftps = _ftps_session(ip, code, timeout=20)
        try:
            ftps.storbinary(f"STOR {name}", io.BytesIO(data))
            return
        finally:
            try:
                ftps.quit()
            except Exception:
                ftps.close()
    except (OSError, ftplib.error_temp):
        chk = _ftps_session(ip, code, timeout=10)
        try:
            chk.voidcmd("TYPE I")
            if chk.size(name) == len(data):
                return  # 实际已经传上去了
        finally:
            try:
                chk.quit()
            except Exception:
                chk.close()
        raise


def sd_delete(ip, code, name):
    ftps = _ftps_session(ip, code)
    try:
        ftps.delete(name)
    finally:
        try:
            ftps.quit()
        except Exception:
            ftps.close()


def sd_list(ip, code):
    ftps = _ftps_session(ip, code)
    try:
        names = ftps.nlst()
    finally:
        try:
            ftps.quit()
        except Exception:
            ftps.close()
    return sorted(n.lstrip("/") for n in names if n.lower().endswith(".3mf"))


def safe_3mf_name(filename):
    stem = Path(filename).stem
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "demo"
    return stem[:60] + ".gcode.3mf"


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
        # A 系列固件对旧 sequence_id 会静默忽略命令,用时间戳起步保证新鲜
        self._seq = int(time.time())
        self._lock = threading.Lock()
        self._ack_events = {}   # seq -> threading.Event
        self._ack_expect = {}   # seq -> command name we are waiting on
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
            print(f"[MQTT] connection refused rc={rc}", file=sys.stderr)

    def _on_disconnect(self, c, userdata, flags, rc, props=None):
        self.connected = False

    def _on_message(self, c, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        p = data.get("print", {})
        seq = str(p.get("sequence_id", ""))
        # 只有 command 和我们发出的那条对得上才算回执(状态推送也带 sequence_id)
        if seq in self._ack_events and p.get("command") == self._ack_expect.get(seq):
            self._acks[seq] = (str(p.get("result", "?")), p.get("reason") or "")
            self._ack_events[seq].set()
        for k in STATUS_KEYS:
            if k in p:
                self.status[k] = p[k]
        self.last_report = time.time()

    def send_command(self, print_payload, timeout=5.0):
        """发送一条 print 命令并等待打印机的受理回执。"""
        seq = self._next_seq()
        ev = threading.Event()
        self._ack_events[seq] = ev
        self._ack_expect[seq] = print_payload["command"]
        payload = {"print": dict(print_payload, sequence_id=seq)}
        self.client.publish(f"device/{self.serial}/request", json.dumps(payload))
        got = ev.wait(timeout)
        self._ack_events.pop(seq, None)
        self._ack_expect.pop(seq, None)
        result, reason = self._acks.pop(seq, ("timeout", ""))
        if not got:
            result, reason = "timeout", f"no ack within {timeout:g} s"
        return {"result": result, "reason": reason}

    def send_gcode(self, gcode, timeout=3.0):
        """发送一行或多行(\n 分隔)G-code,等待受理回执。"""
        return self.send_command(
            {"command": "gcode_line", "param": gcode.rstrip("\n") + "\n"}, timeout)

    def start_sd_print(self, name):
        """启动 SD 卡上的 .3mf(param 指向包内的 gcode)。"""
        subtask = name
        for ext in (".3mf", ".gcode"):
            if subtask.lower().endswith(ext):
                subtask = subtask[: -len(ext)]
        return self.send_command({
            "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "url": f"file:///sdcard/{name}",
            "file": name,
            "md5": "",
            "subtask_name": subtask,
            "subtask_id": "0",
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "bed_type": "auto",
            "timelapse": False,
            "bed_leveling": False,
            "flow_cali": False,
            "vibration_cali": False,
            "layer_inspect": False,
            "use_ams": False,
            "ams_mapping": "",
        }, timeout=8.0)


class JobRunner:
    """把加载的 G-code 文件逐行喂给打印机(Printrun 式),每行等受理回执。"""

    def __init__(self, app):
        self.app = app
        self.lines = []
        self.filename = ""
        self.total = 0
        self.current = 0
        self.state = "idle"   # idle | running | paused | done | stopped | error
        self.error = ""
        self._stop = threading.Event()
        self._pause = threading.Event()

    def snapshot(self):
        return {"state": self.state, "current": self.current, "total": self.total,
                "filename": self.filename, "error": self.error}

    def start(self, filename, text):
        if self.state in ("running", "paused"):
            return False, "a job is already running"
        link = self.app.link
        if not (link and link.connected):
            return False, "printer not connected"
        self.lines = text.splitlines()
        self.total = len(self.lines)
        if not self.total:
            return False, "file is empty"
        self.filename = filename
        self.current = 0
        self.error = ""
        self._stop.clear()
        self._pause.clear()
        self.state = "running"
        threading.Thread(target=self._run, daemon=True).start()
        return True, ""

    def _run(self):
        for i, line in enumerate(self.lines):
            while self._pause.is_set() and not self._stop.is_set():
                time.sleep(0.1)
            if self._stop.is_set():
                self.state = "stopped"
                return
            self.current = i
            cmd = line.split(";", 1)[0].strip()
            if not cmd:
                continue
            link = self.app.link
            if not (link and link.connected):
                self.state, self.error = "error", "printer connection lost"
                return
            r = link.send_gcode(cmd)
            if r["result"] != "success":
                self.state = "error"
                self.error = f"line {i + 1} ({cmd}): {r['result']} {r.get('reason', '')}".strip()
                return
        self.current = self.total
        self.state = "done"

    def pause(self):
        if self.state == "running":
            self._pause.set()
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self._pause.clear()
            self.state = "running"

    def stop(self):
        if self.state in ("running", "paused"):
            self._stop.set()
            self._pause.clear()


class App:
    """连接状态机:disconnected → connecting → connected / error。"""

    def __init__(self, ip=None, serial=None, code=None):
        seed = {k: v for k, v in (("ip", ip), ("serial", serial), ("code", code)) if v}
        if seed:
            save_cache(seed)
        self.link = None
        self.phase = "disconnected"
        self.error = ""
        self.job = JobRunner(self)
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
            self.phase = "error"
            self.error = "Access code required (the 8 characters on the printer's LAN-only Mode screen)"
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
                self.error = ("Printer not found. Check it is on and on the same network; "
                              "if broadcasts are filtered (campus Wi-Fi), read the IP from "
                              "the printer's Settings → Network screen and enter it below.")
                return
            if self.link:
                self.link.stop()
                self.link = None
            link = BambuLink(found["ip"], found["serial"], load_cache().get("code", ""))
            try:
                link.start()
            except OSError as e:
                self.phase, self.error = "error", f"Cannot reach {found['ip']}:8883 — {e}"
                return
            for _ in range(60):  # 最多等 6 秒
                if link.connected or link.auth_failed:
                    break
                time.sleep(0.1)
            if link.connected:
                self.link = link
                self.phase, self.error = "connected", ""
                print(f"Connected to printer {found.get('name', '?')} @ {found['ip']}")
            else:
                link.stop()
                self.phase = "error"
                self.error = ("Connection refused or timed out. Is the access code right? "
                              "Are LAN-only Mode and Developer Mode both enabled?")
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
            elif self.path == "/api/sd/list":
                cfg = load_cache()
                try:
                    files = sd_list(cfg.get("ip", ""), cfg.get("code", ""))
                    self._json({"ok": True, "files": files})
                except Exception as e:
                    self._json({"ok": False, "error": f"FTP failed: {e}", "files": []})
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
                    "job": app.job.snapshot(),
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
                    self._json({"result": "error", "reason": "printer not connected — click Connect first"})
                    return
                self._json(link.send_gcode(gcode))
            elif self.path == "/api/job/start":
                try:
                    data = self._body()
                    text = str(data.get("gcode", ""))
                    filename = str(data.get("filename", "untitled.gcode"))[:120]
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                if len(text) > 2_000_000:
                    self._json({"ok": False, "error": "file too large for line-by-line streaming (2 MB max)"})
                    return
                ok, err = app.job.start(filename, text)
                self._json({"ok": ok, "error": err})
            elif self.path == "/api/job/pause":
                app.job.pause()
                self._json({"ok": True})
            elif self.path == "/api/job/resume":
                app.job.resume()
                self._json({"ok": True})
            elif self.path == "/api/job/stop":
                app.job.stop()
                self._json({"ok": True})
            elif self.path == "/api/sd/upload":
                try:
                    data = self._body()
                    text = str(data.get("gcode", ""))
                    filename = str(data.get("filename", "demo.gcode"))
                    thumb_b64 = str(data.get("thumbnail", ""))
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                if not text.strip():
                    self._json({"ok": False, "error": "file is empty"})
                    return
                thumb = None
                if thumb_b64:
                    try:
                        thumb = base64.b64decode(thumb_b64.split(",", 1)[-1])
                        if len(thumb) > 2_000_000:
                            thumb = None
                    except (ValueError, base64.binascii.Error):
                        thumb = None
                cfg = load_cache()
                name = safe_3mf_name(filename)
                job_name = name[:-len(".gcode.3mf")] if name.endswith(".gcode.3mf") else name
                try:
                    sd_upload(cfg.get("ip", ""), cfg.get("code", ""), name,
                              make_3mf(text, job_name=job_name, thumb_png=thumb))
                    self._json({"ok": True, "name": name})
                except Exception as e:
                    self._json({"ok": False, "error": f"FTP upload failed: {e}"})
            elif self.path == "/api/sd/print":
                try:
                    name = str(self._body().get("name", "")).strip()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                link = app.link
                if not (link and link.connected):
                    self._json({"ok": False, "error": "printer not connected"})
                    return
                if not name.lower().endswith(".3mf"):
                    self._json({"ok": False, "error": "not a .3mf file"})
                    return
                r = link.start_sd_print(name)
                ok = r["result"] == "success"
                self._json({"ok": ok,
                            "error": "" if ok else f"{r['result']} {r.get('reason', '')}".strip()})
            elif self.path == "/api/sd/delete":
                try:
                    name = str(self._body().get("name", "")).strip()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                if not name.lower().endswith(".3mf") or "/" in name or "\\" in name:
                    self._json({"ok": False, "error": "not a .3mf file"})
                    return
                cfg = load_cache()
                try:
                    sd_delete(cfg.get("ip", ""), cfg.get("code", ""), name)
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"ok": False, "error": f"FTP delete failed: {e}"})
            elif self.path in ("/api/print/pause", "/api/print/resume", "/api/print/stop"):
                link = app.link
                if not (link and link.connected):
                    self._json({"ok": False, "error": "printer not connected"})
                    return
                cmd = self.path.rsplit("/", 1)[1]
                r = link.send_command({"command": cmd, "param": ""})
                ok = r["result"] == "success"
                self._json({"ok": ok,
                            "error": "" if ok else f"{r['result']} {r.get('reason', '')}".strip()})
            else:
                self.send_error(404)

    return Handler


def main():
    # Windows 控制台默认 cp1252/GBK,中文输出会炸;强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Bambu A1 web console")
    ap.add_argument("--ip", help="printer IP (optional, remembered)")
    ap.add_argument("--serial", help="printer serial number (optional, remembered)")
    ap.add_argument("--code", help="LAN-only Mode access code (optional, remembered)")
    ap.add_argument("--port", type=int, default=8347, help="web port (default 8347)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; 0.0.0.0 lets other devices on the LAN access it")
    ap.add_argument("--no-browser", action="store_true", help="do not auto-open the browser")
    args = ap.parse_args()

    app = App(args.ip, args.serial, args.code)
    html_path = Path(__file__).resolve().parent / "web_ui.html"
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(app, html_path))
    except OSError as e:
        print(f"[port {args.port} unavailable] {e} (is another console already running?)",
              file=sys.stderr)
        sys.exit(1)

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"Web console: {url}  (Ctrl+C or close this window to quit)")
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
