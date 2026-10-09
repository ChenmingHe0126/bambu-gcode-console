#!/usr/bin/env python3
"""
bambu_discovery.py - locate a Bambu printer on the LAN.

Three-step fallback chain (resolve):
  1. an explicit --ip: probe TCP port 8883 first, use it if reachable
  2. the address that worked last time (~/.bambu-gcode-console.json)
  3. passive SSDP: the printer broadcasts a NOTIFY on UDP 1990/2021 every few seconds
     carrying its IP (Location), serial (USN), model (DevModel.bambu.com) and name

If the IP is known but the serial is not (campus Wi-Fi blocks SSDP), the serial is read
off the printer's own MQTT report topic instead.

Note: office/campus Wi-Fi often filters client-to-client broadcasts and the Windows
firewall blocks inbound UDP on "Public" networks, so SSDP comes last; the cache is
what makes everyday use instant.
"""

import json
import os
import select
import socket
import ssl
import time
from pathlib import Path

CACHE_FILE = Path.home() / ".bambu-gcode-console.json"
SSDP_PORTS = (2021, 1990)


def probe(ip, port=8883, timeout=1.5):
    """Probe whether the printer's MQTT port is reachable over TCP."""
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
    """Merge-write: the file also holds the access code and other fields, never overwrite it wholesale."""
    try:
        merged = load_cache()
        merged.update({k: v for k, v in info.items() if v})
        CACHE_FILE.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        if os.name == "posix":
            os.chmod(CACHE_FILE, 0o600)  # it holds the access code
    except OSError:
        pass  # failing to write the cache is not fatal


def forget_cache():
    """Delete the cache file (access code included)."""
    try:
        CACHE_FILE.unlink()
    except OSError:
        pass


def discover(timeout=20.0, want_serial=None):
    """Listen passively for the SSDP broadcast; return {"ip","serial","model","name"} or None.

    With want_serial set, printers with another serial are ignored (rooms with several printers).
    """
    socks = []
    for port in SSDP_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            s.setblocking(False)
            socks.append(s)
        except OSError:
            continue  # port taken by Bambu Studio/OrcaSlicer - skip it
    if not socks:
        print("[SSDP] cannot listen on ports 1990/2021 (Bambu Studio/OrcaSlicer running?)")
        return None
    end = time.time() + timeout
    ignored = set()
    try:
        while time.time() < end:
            readable, _, _ = select.select(socks, [], [], 1.0)
            for s in readable:
                try:
                    data, addr = s.recvfrom(4096)
                except OSError:
                    continue
                info = _parse(data.decode(errors="replace"), addr[0])
                if not info:
                    continue
                if want_serial and info["serial"] != want_serial:
                    if info["serial"] not in ignored:
                        ignored.add(info["serial"])
                        print(f"[SSDP] ignoring {info['name']} ({info['serial']}) at {info['ip']}"
                              " - not the requested serial")
                    continue
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


def mqtt_probe_serial(ip, code, timeout=8.0):
    """Ask the printer itself: subscribe to device/+/report and read the serial off the topic."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return None
    found = {}

    def on_connect(c, userdata, flags, rc, props=None):
        if rc == 0:
            c.subscribe("device/+/report")
        else:
            found["refused"] = True

    def on_message(c, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) == 3 and parts[0] == "device" and parts[1]:
            found["serial"] = parts[1]

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    c.username_pw_set("bblp", code)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c.tls_set_context(ctx)
    c.on_connect, c.on_message = on_connect, on_message
    try:
        c.connect(ip, 8883, keepalive=15)
    except OSError:
        return None
    c.loop_start()
    end = time.time() + timeout
    while time.time() < end and "serial" not in found and "refused" not in found:
        time.sleep(0.2)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass
    return found.get("serial")


def resolve(ip=None, serial=None, code=None):
    """Resolve the printer in the order explicit args -> cache -> SSDP; return an info dict or None.

    An explicitly requested serial is honoured: cached or discovered printers with a
    different serial are never used in its place.
    """
    cache = load_cache()
    wanted = serial or ""
    serial = serial or cache.get("serial", "")
    code = code or cache.get("code", "")
    cached_ip = cache.get("ip")
    cached_serial = cache.get("serial", "")

    if ip:
        if probe(ip):
            # A new IP with no explicit serial: the cached serial may belong to another
            # printer, so ask this one for its serial over MQTT when we can.
            if not wanted and code and (not serial or ip != cached_ip):
                print(f"[{ip}] reachable; asking the printer for its serial number...")
                serial = mqtt_probe_serial(ip, code) or serial
            if serial:
                info = {"ip": ip, "serial": serial,
                        "model": cache.get("model", "?"), "name": cache.get("name", "?")}
                save_cache(info)
                return info
            save_cache({"ip": ip})  # remembered, so a later --serial alone is enough
            print(f"[{ip}] reachable, but the serial number is unknown - pass --serial "
                  "(Settings > Device on the printer), or wait for SSDP discovery...")
        else:
            print(f"[{ip}] port 8883 unreachable (printer IP changed?), trying fallbacks...")

    if (cached_ip and cached_ip != ip and serial
            and (not wanted or wanted == cached_serial) and probe(cached_ip)):
        print(f"Using last known address {cached_ip}")
        info = {"ip": cached_ip, "serial": serial,
                "model": cache.get("model", "?"), "name": cache.get("name", "?")}
        save_cache(info)
        return info

    print("Listening for the printer's SSDP broadcast (up to 20 s)..."
          + (f" (serial {wanted})" if wanted else ""))
    found = discover(want_serial=wanted or None)
    if found:
        save_cache(found)
        return found
    return None


if __name__ == "__main__":
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        found = resolve()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
    print(found if found else
          "Printer not found. Check it is on and on the same network; if broadcasts are "
          "filtered (campus Wi-Fi), read the IP from the printer's Settings > Network "
          "screen and pass it via --ip (it will be remembered).")
