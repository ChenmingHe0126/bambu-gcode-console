#!/usr/bin/env bash
# Bambu G-code Console — macOS / Linux launcher.
# Double-click (macOS: right-click > Open the first time) or run ./start_console.sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "[!] Python 3 not found. Install it from https://www.python.org/downloads/ (macOS: brew install python)"
  read -r -p "Press Enter to close..." _
  exit 1
fi
if ! "$PY" -c "import paho.mqtt" >/dev/null 2>&1; then
  echo "Installing the one dependency (paho-mqtt)..."
  "$PY" -m pip install --user "paho-mqtt>=2.0" || "$PY" -m pip install "paho-mqtt>=2.0"
fi
"$PY" bambu_web.py "$@"
echo
echo "Server stopped. The console only works while this window is open."
read -r -p "Press Enter to close..." _
