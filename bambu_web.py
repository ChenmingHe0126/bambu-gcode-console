#!/usr/bin/env python3
"""
bambu_web.py - web console for Bambu Lab printers (a local HTTP server that bridges to
the printer's MQTT and FTPS interfaces).

Everyday use: double-click start_console.bat / start_console.sh (or run python bambu_web.py).
The browser opens http://127.0.0.1:8347; press Connect on the page. The access code, IP and
serial are remembered in ~/.bambu-gcode-console.json, so they are typed only once.

Command-line options (all optional; given values are saved to the config):
    python bambu_web.py [--code ACCESS_CODE] [--ip x.x.x.x] [--serial SN]
                        [--port 8347] [--host 127.0.0.1] [--no-browser]

The page lives in web_ui.html next to this file; protocol notes are in README.md.
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

try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion  # only exists in paho-mqtt 2.x
except ImportError:
    sys.exit('This tool needs paho-mqtt 2.x. Install it with:  python -m pip install "paho-mqtt>=2.0"')

from bambu_discovery import load_cache, resolve, save_cache

STATUS_KEYS = (
    "nozzle_temper", "nozzle_target_temper",
    "bed_temper", "bed_target_temper",
    "gcode_state", "mc_percent", "mc_remaining_time",
    "wifi_signal", "cooling_fan_speed", "subtask_name",
)


# -- SD card: FTPS upload + 3mf wrapping -------------------------------------
# Developer Mode exposes implicit FTPS on port 990. The firmware's project_file
# command only executes Bambu-Studio-structured .gcode.3mf files, so raw gcode is
# injected into a genuine Studio skeleton (see wrap_gcode_in_skeleton).

class ImplicitFTPS(ftplib.FTP_TLS):
    """ftplib only speaks explicit FTPS; the printer uses implicit FTPS (TLS from the first byte)."""

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


def sd_upload(ip, code, name, data):
    """Upload a file unchanged to the SD card root. Some firmware times out on the final 226 reply; reconnect and compare sizes as a fallback."""
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
                return  # the transfer did complete
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


def sd_download(ip, code, name):
    ftps = _ftps_session(ip, code, timeout=30)
    buf = io.BytesIO()
    try:
        ftps.retrbinary(f"RETR {name}", buf.write)
    finally:
        try:
            ftps.quit()
        except Exception:
            ftps.close()
    return buf.getvalue()


def wrapped_3mf_name(name):
    return re.sub(r"\.(gcode|gco|g|nc|txt)$", "", name, flags=re.I) + ".gcode.3mf"


def sd_list(ip, code):
    ftps = _ftps_session(ip, code)
    try:
        names = ftps.nlst()
    finally:
        try:
            ftps.quit()
        except Exception:
            ftps.close()
    return sorted(n.lstrip("/") for n in names
                  if n.lower().endswith((".3mf", ".gcode", ".gco")) and not n.startswith("._"))


def safe_sd_name(filename):
    """Keep the original file name (with extension); strip path separators and odd characters."""
    name = re.sub(r"[^A-Za-z0-9_.\-]+", "_", Path(filename).name).strip("._") or "file.gcode"
    return name[:100]


SKELETON_3MF = Path(__file__).resolve().parent / "a1_skeleton.gcode.3mf"


def wrap_gcode_in_skeleton(gcode_text):
    """Inject raw gcode into a genuine Bambu Studio .gcode.3mf skeleton ("dummy 3mf" method).

    The firmware's project_file only executes Studio-structured 3mf files; hand-built
    skeletons are ignored or hang in "preparing". So the skeleton is a real file sliced
    with the Studio CLI (a 10 mm cube). Its HEADER/CONFIG comment blocks are kept, the
    EXECUTABLE block is replaced by the user's gcode, and the md5 sidecar is recomputed.
    Gcode that already carries a HEADER_BLOCK (a Studio export) is used as-is.
    """
    skel = zipfile.ZipFile(SKELETON_3MF)
    if "HEADER_BLOCK_START" in gcode_text:
        new_gcode = gcode_text
    else:
        g = skel.read("Metadata/plate_1.gcode").decode("utf-8")
        cut = g.index("\n", g.index("; EXECUTABLE_BLOCK_START")) + 1
        new_gcode = g[:cut] + gcode_text.rstrip("\r\n") + "\n; EXECUTABLE_BLOCK_END\n"
    nb = new_gcode.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in skel.infolist():
            n = info.filename
            if n == "Metadata/plate_1.gcode":
                z.writestr(n, nb)
            elif n == "Metadata/plate_1.gcode.md5":
                z.writestr(n, hashlib.md5(nb).hexdigest().upper())
            else:
                z.writestr(n, skel.read(n))
    return buf.getvalue()


class BambuLink:
    """One MQTT connection to the printer: send commands, wait for acks, cache status."""

    def __init__(self, ip, serial, code):
        self.ip = ip
        self.serial = serial
        self.code = code
        self.status = {}
        self.connected = False
        self.auth_failed = False
        self.disconnected_at = None
        self.last_report = 0.0
        # A-series firmware silently ignores commands with a stale sequence_id; start from a timestamp
        self._seq = int(time.time())
        self._lock = threading.Lock()
        self._ack_events = {}   # seq -> threading.Event
        self._ack_expect = {}   # seq -> command name we are waiting on
        self._acks = {}         # seq -> (result, reason)

        c = mqtt.Client(CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        c.username_pw_set("bblp", code)
        # the printer's certificate is signed by Bambu's private CA; skip verification on the LAN
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
            self.disconnected_at = None
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
        self.disconnected_at = time.time()

    def _on_message(self, c, userdata, msg):
        try:
            data = json.loads(msg.payload)
            p = data.get("print", {}) if isinstance(data, dict) else {}
            if not isinstance(p, dict):
                return
            seq = str(p.get("sequence_id", ""))
            # count it as our ack only if the command matches what we sent (status pushes carry sequence_id too)
            ev = self._ack_events.get(seq)
            if ev is not None and p.get("command") == self._ack_expect.get(seq):
                self._acks[seq] = (str(p.get("result", "?")), p.get("reason") or "")
                ev.set()
            for k in STATUS_KEYS:
                if k in p:
                    self.status[k] = p[k]
            self.last_report = time.time()
        except Exception as e:  # a bad report must never take the MQTT thread down
            print(f"[MQTT] ignoring malformed report: {type(e).__name__}: {e}", file=sys.stderr)

    def send_command(self, print_payload, timeout=5.0):
        """Publish one print command and wait for the printer's acknowledgement."""
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
        """Send one or more newline-separated G-code lines and wait for the acknowledgement."""
        return self.send_command(
            {"command": "gcode_line", "param": gcode.rstrip("\n") + "\n"}, timeout)

    def start_sd_print(self, name, md5=""):
        """Start a .3mf on the SD card (param names the gcode inside it). Studio-structured 3mf only."""
        subtask = name
        for ext in (".3mf", ".gcode"):
            if subtask.lower().endswith(ext):
                subtask = subtask[: -len(ext)]
        return self.send_command({
            "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "url": f"file:///sdcard/{name}",
            "file": name,
            "md5": md5,
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
    """Feed a loaded G-code file to the printer line by line (Printrun style), waiting for each ack."""

    def __init__(self, app):
        self.app = app
        self.lines = []
        self.filename = ""
        self.total = 0
        self.current = 0
        self.state = "idle"   # idle | running | paused | done | stopped | error
        self.error = ""
        self.note = ""
        self._stop = threading.Event()
        self._pause = threading.Event()

    def snapshot(self):
        return {"state": self.state, "current": self.current, "total": self.total,
                "filename": self.filename, "error": self.error, "note": self.note}

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
        self.note = ""
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
                self._heaters_off()
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
                self._heaters_off()
                return
        self.current = self.total
        self.state = "done"

    def _heaters_off(self):
        """A stopped or failed stream must not leave a hot nozzle sitting on the part."""
        link = self.app.link
        if link and link.connected:
            try:
                link.send_gcode("M104 S0\nM140 S0\nM106 P1 S0")
                self.note = "heaters turned off"
            except Exception:
                self.note = ""

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
    """Connection state machine: disconnected -> connecting -> connected / error."""

    def __init__(self, ip=None, serial=None, code=None):
        seed = {k: v for k, v in (("ip", ip), ("serial", serial), ("code", code)) if v}
        if seed:
            save_cache(seed)
        self.link = None
        self.phase = "disconnected"
        self.error = ""
        self.job = JobRunner(self)
        self._pending_code = ""
        self._busy = threading.Lock()

    def start_connect(self, code=None, ip=None, serial=None):
        upd = {k: v for k, v in (("ip", ip), ("serial", serial)) if v}
        if upd:
            save_cache(upd)
        code = code or load_cache().get("code", "")
        if not code:
            self.phase = "error"
            self.error = "Access code required (the 8 characters on the printer's LAN-only Mode screen)"
            return
        if not self._busy.acquire(blocking=False):
            return  # already connecting
        self._pending_code = code
        self.phase, self.error = "connecting", ""
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        try:
            cfg = load_cache()
            code = self._pending_code
            found = resolve(cfg.get("ip"), cfg.get("serial"), code)
            if not found:
                self.phase = "error"
                self.error = ("Printer not found. Check it is on and on the same network; "
                              "if broadcasts are filtered (campus Wi-Fi), read the IP from "
                              "the printer's Settings > Network screen and enter it below "
                              "(plus the serial number from Settings > Device if it still fails).")
                return
            if self.link:
                self.link.stop()
                self.link = None
            link = BambuLink(found["ip"], found["serial"], code)
            try:
                link.start()
            except OSError as e:
                self.phase, self.error = "error", f"Cannot reach {found['ip']}:8883 — {e}"
                return
            for _ in range(60):  # wait up to 6 s
                if link.connected or link.auth_failed:
                    break
                time.sleep(0.1)
            if link.connected:
                self.link = link
                self.phase, self.error = "connected", ""
                save_cache({"code": code})  # remembered only once it is known to work
                print(f"Connected to printer {found.get('name', '?')} @ {found['ip']}")
            else:
                link.stop()
                self.phase = "error"
                self.error = ("Connection refused or timed out. Is the access code right? "
                              "Are LAN-only Mode and Developer Mode both enabled?")
        except Exception as e:  # never leave the page stuck on "connecting"
            self.phase = "error"
            self.error = f"Internal error while connecting: {type(e).__name__}: {e}"
            print(f"[connect] {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            self._busy.release()


def make_handler(app: App, html_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet during class

        def _is_loopback_client(self):
            return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

        def _same_site(self):
            """Only loopback names / IP literals may address this server, and a browser
            Origin (sent on cross-site requests) must match the Host it was served from."""
            host = (self.headers.get("Host") or "").strip()
            m = re.match(r"^(\[[^\]]*\]|[^:]+)", host)
            hostname = m.group(1) if m else ""
            host_ok = (hostname in ("localhost", "127.0.0.1", "[::1]")
                       or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", hostname) is not None
                       or hostname.startswith("["))
            origin = self.headers.get("Origin")
            if origin and origin.split("//", 1)[-1].rstrip("/") != host:
                return False
            return host_ok

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/") and not self._same_site():
                self._json({"error": "forbidden"}, 403)
                return
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
                if (app.phase == "connected" and link and not link.connected
                        and link.disconnected_at and time.time() - link.disconnected_at > 15):
                    app.phase = "error"
                    app.error = "Lost contact with the printer - check its power and Wi-Fi, then press Connect"
                self._json({
                    "phase": app.phase,
                    "error": app.error,
                    "connected": bool(link and link.connected and not stale),
                    "ip": cfg.get("ip", ""),
                    "serial": cfg.get("serial", ""),
                    # pre-fill the access code only for the browser on this machine:
                    # with --host 0.0.0.0 every client on the LAN polls this endpoint
                    "code": cfg.get("code", "") if self._is_loopback_client() else "",
                    "status": link.status if link else {},
                    "job": app.job.snapshot(),
                })
            else:
                self.send_error(404)

        def _body(self, max_len=150_000_000):
            length = int(self.headers.get("Content-Length", 0))
            if length > max_len:
                raise ValueError("request body too large")
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        def do_POST(self):
            if not self._same_site():
                self._json({"error": "forbidden"}, 403)
                return
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json({"error": "JSON body expected"}, 415)
                return
            if self.path == "/api/connect":
                try:
                    data = self._body()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                app.start_connect(code=str(data.get("code", "")).strip(),
                                  ip=str(data.get("ip", "")).strip(),
                                  serial=str(data.get("serial", "")).strip())
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
                # upload the file unchanged (base64 in, raw bytes out): no wrapping, no rewriting
                try:
                    data = self._body()
                    filename = str(data.get("filename", "file.gcode"))
                    raw = base64.b64decode(str(data.get("data", "")).split(",", 1)[-1])
                except (ValueError, json.JSONDecodeError, base64.binascii.Error):
                    self._json({"error": "bad request"}, 400)
                    return
                if not raw:
                    self._json({"ok": False, "error": "file is empty"})
                    return
                if len(raw) > 100_000_000:
                    self._json({"ok": False, "error": "file too large (100 MB max)"})
                    return
                cfg = load_cache()
                name = safe_sd_name(filename)
                try:
                    sd_upload(cfg.get("ip", ""), cfg.get("code", ""), name, raw)
                    self._json({"ok": True, "name": name})
                except Exception as e:
                    self._json({"ok": False, "error": f"FTP upload failed: {e}"})
            elif self.path == "/api/print_file":
                # one-click print: wrap raw gcode in the skeleton (or pass a .3mf through) -> upload -> project_file
                try:
                    data = self._body()
                    filename = str(data.get("filename", "file.gcode"))
                    raw = base64.b64decode(str(data.get("data", "")).split(",", 1)[-1])
                except (ValueError, json.JSONDecodeError, base64.binascii.Error):
                    self._json({"error": "bad request"}, 400)
                    return
                link = app.link
                if not (link and link.connected):
                    self._json({"ok": False, "error": "printer not connected"})
                    return
                if not raw:
                    self._json({"ok": False, "error": "file is empty"})
                    return
                if len(raw) > 100_000_000:
                    self._json({"ok": False, "error": "file too large (100 MB max)"})
                    return
                name = safe_sd_name(filename)
                if name.lower().endswith(".3mf"):
                    payload = raw
                else:
                    try:
                        payload = wrap_gcode_in_skeleton(raw.decode("utf-8-sig", "replace"))
                    except Exception as e:
                        self._json({"ok": False, "error": f"could not wrap gcode: {e}"})
                        return
                    name = wrapped_3mf_name(name)
                cfg = load_cache()
                try:
                    sd_upload(cfg.get("ip", ""), cfg.get("code", ""), name, payload)
                except Exception as e:
                    self._json({"ok": False, "error": f"FTP upload failed: {e}"})
                    return
                r = link.start_sd_print(name, hashlib.md5(payload).hexdigest().upper())
                ok = r["result"] == "success"
                self._json({"ok": ok, "name": name,
                            "error": "" if ok else f"{r['result']} {r.get('reason', '')}".strip()})
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
                if "/" in name or "\\" in name:
                    self._json({"ok": False, "error": "bad file name"})
                    return
                cfg = load_cache()
                md5 = ""
                if not name.lower().endswith(".3mf"):
                    # raw gcode cannot be started remotely: fetch -> wrap in the skeleton -> upload as .gcode.3mf -> start
                    try:
                        raw = sd_download(cfg.get("ip", ""), cfg.get("code", ""), name)
                        payload = wrap_gcode_in_skeleton(raw.decode("utf-8-sig", "replace"))
                        name = wrapped_3mf_name(name)
                        sd_upload(cfg.get("ip", ""), cfg.get("code", ""), name, payload)
                        md5 = hashlib.md5(payload).hexdigest().upper()
                    except Exception as e:
                        self._json({"ok": False, "error": f"could not convert to 3mf: {e}"})
                        return
                r = link.start_sd_print(name, md5)
                ok = r["result"] == "success"
                self._json({"ok": ok, "name": name,
                            "error": "" if ok else f"{r['result']} {r.get('reason', '')}".strip()})
            elif self.path == "/api/sd/delete":
                try:
                    name = str(self._body().get("name", "")).strip()
                except (ValueError, json.JSONDecodeError):
                    self._json({"error": "bad request"}, 400)
                    return
                if not name.lower().endswith((".3mf", ".gcode", ".gco")) or "/" in name or "\\" in name:
                    self._json({"ok": False, "error": "not a printable file"})
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
    # Windows consoles default to cp1252/GBK, which breaks non-ASCII output; force UTF-8
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
    if sys.platform == "win32":
        ThreadingHTTPServer.allow_reuse_address = False  # so a second launch hits the OSError below
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
    try:
        main()
    except KeyboardInterrupt:
        pass
