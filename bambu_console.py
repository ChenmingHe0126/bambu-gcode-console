#!/usr/bin/env python3
"""
bambu_console.py - minimal line-by-line G-code console for Bambu Lab printers (Pronterface style).

Usage:
    pip install "paho-mqtt>=2.0"
    python bambu_console.py [--ip 192.168.x.x] [--serial SN] [--code ACCESS_CODE]

Prerequisites (set on the printer's touchscreen, see README):
  1. Settings -> Network: enable LAN-only Mode, then restart the printer once
  2. In the same menu enable Developer Mode - required on firmware 01.05.00.00+,
     otherwise the printer rejects every control command (read-only status still works)
  3. The access code is shown on the LAN-only Mode page; the serial is under Settings -> Device

Protocol (see the OpenBambuAPI docs): MQTT over TLS, port 8883, user bblp, password = access code.
  publish:   device/{serial}/request   {"print": {"command": "gcode_line", "sequence_id": "N", "param": "G28" + newline}}
  subscribe: device/{serial}/report    the printer echoes the command with a result ("accepted",
             not "finished") and pushes temperature/state JSON periodically.
"""

import argparse
import json
import ssl
import sys
import time

import paho.mqtt.client as mqtt

from bambu_discovery import load_cache, resolve, save_cache

seq = 0
last_status = {}  # fields of interest from the latest status report


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
    # The printer's certificate is signed by Bambu's private CA, so public CAs can't verify it.
    # On a classroom LAN we skip verification; for strict checking, trust the ca_cert.pem
    # from the OpenBambuAPI repository instead (see README).
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ctx)

    def on_connect(c, userdata, flags, rc, props=None):
        if rc == 0:
            print(f"[connected to {args.ip}] subscribing to status reports…")
            c.subscribe(f"device/{args.serial}/report")
            # request one full status push, to get initial temperatures etc.
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
        # acknowledgement of a G-code command (like the serial "ok" / "error")
        if p.get("command") == "gcode_line":
            result = str(p.get("result", "?")).lower()
            reason = p.get("reason") or ""
            tag = "ok" if result == "success" else f"FAILED {reason}".strip()
            print(f"  << [{p.get('sequence_id')}] {tag}")
        # cache status fields for the status command
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
    # Windows consoles default to cp1252/GBK, which breaks non-ASCII output; force UTF-8
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
