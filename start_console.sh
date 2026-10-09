#!/usr/bin/env bash
# Bambu G-code Console - macOS / Linux launcher.
# Run in a terminal:  ./start_console.sh        (macOS Finder users: double-click start_console.command)
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "[!] Python 3 not found. Install it from https://www.python.org/downloads/ (macOS: brew install python)"
  read -r -p "Press Enter to close..." _
  exit 1
fi

# A virtual environment created on a previous run takes precedence.
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

if ! "$PY" -c "import paho.mqtt.client" >/dev/null 2>&1; then
  echo "Installing the one dependency (paho-mqtt)..."
  if ! "$PY" -m pip install --user "paho-mqtt>=2.0" >/dev/null 2>&1; then
    # Debian/Ubuntu/Fedora/Homebrew Pythons refuse system-wide pip installs (PEP 668):
    # fall back to a private virtual environment inside this folder.
    echo "pip refused a user install - creating a local virtual environment in .venv ..."
    if "$PY" -m venv .venv && .venv/bin/python -m pip install "paho-mqtt>=2.0"; then
      PY=".venv/bin/python"
    else
      echo "[!] Could not install paho-mqtt. Install it yourself, then re-run:"
      echo "    $PY -m pip install --user \"paho-mqtt>=2.0\""
      read -r -p "Press Enter to close..." _
      exit 1
    fi
  fi
fi

"$PY" bambu_web.py "$@"
echo
echo "Server stopped. The console only works while this window is open."
read -r -p "Press Enter to close..." _
