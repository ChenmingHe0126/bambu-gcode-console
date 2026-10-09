# Bambu G-code Console

A small, Printrun-style control panel for Bambu Lab printers, made for **teaching**: jog the head, set temperatures, type G-code one line at a time and watch the machine do exactly that line, preview a G-code file, stream it line by line, or send it to the printer's SD card and start it with one click.

- Tested on a **Bambu Lab A1**, firmware 01.08.01.00 (October 2026). Other Bambu printers speak the same protocol but are untested.
- Runs on Windows, macOS and Linux. Needs Python 3.9+ and one library (`paho-mqtt`).
- Inspired by [Printrun / Pronterface](https://github.com/kliment/Printrun). Not affiliated with Bambu Lab.
- Written with [Claude Code](https://claude.com/claude-code) (agentic coding) by Chenming He for a university 3D-printing workshop. MIT license.

[中文说明 → README.zh-CN.md](README.zh-CN.md)

> **Safety.** This tool moves a machine with a 220 °C nozzle and a 65 °C bed. Stay next to the printer, keep hands out of the build volume, and know where the printer's power switch is. Use at your own risk; see [Disclaimer](#disclaimer).

## Why this exists

Older printers (Ender, Prusa, Anycubic…) have a USB port that behaves like a serial cable: a program such as Printrun can push one G-code line, get an `ok` back, push the next. That is wonderful in a classroom — students type `G1 X100` and the head moves 100 mm.

A Bambu A1 has no such port. What it does have, once you switch it to **LAN-only Mode** and **Developer Mode** on its touchscreen, is a small server on the local network that accepts commands over **MQTT** (a lightweight "publish a message to a topic" protocol, normally used by IoT devices) and file uploads over **FTPS** (the classic FTP file protocol wrapped in TLS encryption). This project is a thin bridge that turns those two channels back into the Printrun experience: a web page on your laptop talks to a tiny Python program, and the Python program talks to the printer.

## Features

| In the browser | What it does |
|---|---|
| **Jog pad** | X / Y / Z buttons with 0.1 / 1 / 10 / 50 mm steps, plus Home (`G28`). |
| **Temperatures** | Live nozzle and bed readings, one-tap presets (150 / 200 / 220 °C nozzle, 45 / 60 °C bed) and Off. |
| **G-code console** | Type any G-code, press Enter, see the printer's `ok` / `FAILED` reply. Arrow keys recall history. |
| **File preview** | Drop a `.gcode` file: the XY toolpath is drawn in the page (green = extruding, grey = travel), with a layer slider. Handles `G2`/`G3` arcs and CRLF / CR / LF line endings. |
| **Stream** | Sends the loaded file **one line at a time** and waits for each reply — the Printrun way. Pause / Stop, progress bar, and the preview highlights what has already been sent. Stop (or an error) switches the nozzle, bed and fan off. Best for short demo files (a few hundred lines). |
| **Print now** | Uploads the file to the SD card and starts it as a normal print, so the printer runs it at full speed by itself. Raw `.gcode` is wrapped into the format the firmware insists on (see [How printing works](#how-the-one-click-print-works)); Bambu Studio `.gcode.3mf` exports are sent as-is. |
| **Send to SD** | Just copies the file to the SD card unchanged (on by default when a file is loaded). Start it from the printer's screen, or pick it in the SD list and press Print. |
| **SD card files** | List, Print, Delete. Printing a raw `.gcode` from the list converts it on the fly. |
| **Printer controls** | Pause / Resume / Stop for whatever the printer is currently printing. |

A command-line version, `bambu_console.py`, offers the same line-by-line console in a terminal for people who prefer that.

## What you need

1. A Bambu Lab printer on the same Wi-Fi / LAN as your computer. Tested on the A1; the A1 mini, P1 and X1 series use the same commands but have not been tried.
2. **LAN-only Mode + Developer Mode** enabled on the printer (steps below). On firmware 01.05.00.00 and newer the printer refuses every control command without Developer Mode.
3. Python 3.9 or newer. Windows users: install from [python.org](https://www.python.org/downloads/) and tick *Add Python to PATH*.
4. Filament loaded and the bed levelled at least once from the printer's own menu — the example files reuse the stored levelling mesh.

## Printer setup (once)

1. On the touchscreen: **Settings → Network (WLAN) → LAN-only Mode → on**. The printer asks to restart; let it.
2. Back in the same menu, turn on **Developer Mode**. (Bambu's wording: it "opens the MQTT channel, live stream and FTP" and disables command verification. Bambu does not provide support in this mode.)
3. Write down the **Access Code** shown on the LAN-only Mode page — 8 characters. If it shows all zeros, toggle LAN-only Mode off and on again.
4. The printer's IP address is on the same page; its serial number is under *Settings → Device*. You usually don't need either: the console finds the printer by itself.

What you give up while in LAN-only Mode: the Bambu Handy app, cloud monitoring and over-the-air firmware updates (update from SD card instead). Both switches survive firmware updates.

## Install and run

```bash
git clone https://github.com/ChenmingHe0126/bambu-gcode-console.git
cd bambu-gcode-console
```

(or click **Code → Download ZIP** on GitHub and unzip it.)

- **Windows:** double-click `start_console.bat`.
- **macOS:** double-click `start_console.command` (right-click → Open the first time). **Linux, or any terminal:** `./start_console.sh`. On Pythons that refuse `pip install` (Debian/Ubuntu/Fedora/Homebrew, PEP 668) the script creates a private `.venv` inside the folder by itself.

The launcher installs `paho-mqtt` if it is missing, starts the bridge on `http://127.0.0.1:8347` and opens your browser. Type the access code, press **Connect**. The code, IP and serial are remembered in `~/.bambu-gcode-console.json` once a connection has succeeded, so next time it is one click. If the printer is not found automatically, type its IP into the same panel (and, if it still fails, the serial number from *Settings → Device*). Should the link drop later, a **Connect…** button appears in the header.

Closing the black terminal window stops the bridge. Only one console can run at a time (port 8347).

Manual start, with options:

```bash
python bambu_web.py --code 12345678            # first time; remembered afterwards
python bambu_web.py --ip 192.168.1.50          # if auto-discovery can't see the printer
python bambu_web.py --host 0.0.0.0 --port 8347 # let students' phones open the page (read the Safety note)
python bambu_console.py                        # terminal version
```

How the printer is found, in order: the `--ip` you gave (checked by probing port 8883) → the address that worked last time → listening for the printer's SSDP broadcast on UDP 2021/1990 for up to 20 s. Campus and office Wi-Fi often block broadcasts, and Windows blocks them on networks marked *Public*; if discovery fails, read the IP off the printer's screen and type it into the Connect panel once.

## Quick start: draw a square

The repository ships ready-to-run files in [`examples/`](examples/). `examples/square.gcode` draws one 60 × 60 mm square outline in PLA. Drop it onto the page, look at the preview, then either **Stream** it (the slow, educational way: you can pause between lines) or press **Print now**.

The heart of that file is just this:

```gcode
G90                          ; absolute XY positions
M83                          ; relative extrusion: each E is "how much filament for THIS move"
G0 F18000 X98 Y98 Z1.0       ; travel to the first corner, 1 mm above the bed (no filament)
G1 F1200 Z0.2                ; lower to layer height
G1 F600 E0.8                 ; prime: push 0.8 mm of filament so the line starts right at the corner
G1 F1200 X158 Y98  E2.22     ; side 1 -> right  (60 mm)
G1 F1200 X158 Y158 E2.22     ; side 2 -> back
G1 F1200 X98  Y158 E2.22     ; side 3 -> left
G1 F1200 X98  Y98  E2.22     ; side 4 -> front, square closed
G1 F600 E-0.8                ; retract so the nozzle does not drool while lifting
G0 F1200 Z5                  ; lift away from the print
```

Reading it line by line:

- `G90` / `M83` set the rules: positions are absolute, extrusion amounts are relative.
- `G0` is a travel move (no filament), `G1` is a working move. `F18000` means 18 000 mm/min = 300 mm/s; `F1200` is 20 mm/s — slow on purpose so people can watch.
- `E2.22` is the filament to push while drawing one 60 mm side. The number comes from geometry: a line 0.45 mm wide and 0.2 mm tall has a cross-section of 0.09 mm²; 1.75 mm filament has 2.405 mm². So each millimetre of line needs 0.09 / 2.405 ≈ 0.037 mm of filament, and 60 mm needs 2.22 mm. Change the width or height and the number changes — that is the whole secret of slicing.
- The 0.8 mm prime at the start and retract at the end are what make the corner crisp. Without the prime the first centimetre comes out empty, because the nozzle has nothing in it after travelling.

The full file wraps this body in `A1_start_minimal.gcode` (heat up, home, purge, draw a prime line along the left edge) and `A1_end_minimal.gcode` (heaters off, lift, present the bed). Both are short and commented — read them, then change the temperatures (`M140 S65`, `M109 S220`) to suit your filament.

| File | What it is |
|---|---|
| `examples/square.gcode` | The square above, complete with start/end sequences. ~90 lines. |
| `examples/star.gcode` | A five-pointed star generated from Rhino/Grasshopper, one layer. ~2 400 lines; a good Stream demo. |
| `examples/hello_world.gcode` | "Hello World" written in filament, one layer. ~4 000 lines. |
| `examples/A1_start_minimal.gcode` | Minimal start sequence for the A1 (no bed-levelling, reuses the stored mesh). |
| `examples/A1_end_minimal.gcode` | Minimal end sequence. |

Writing your own: start from `square.gcode`, keep the start/end blocks, replace the middle. Stay inside X 0–256, Y 0–256 (A1 bed), use `Z0.2` for the first layer, and always `G28` before any move — the start sequence does that for you.

## Using the console in class

- **Home first.** The jog pad and the console send raw moves; the printer has no idea where the head is until `G28` runs. Press ⌂ (or type `G28`) at the start of every session.
- **Jog** shows what X, Y, Z mean on a bed-slinger: X moves the head, Y moves the bed, Z moves the gantry.
- **Console** is the Printrun moment: `G1 X128 Y128 F6000`, `M104 S150`, `G1 Z50`. Each line is sent on its own and the reply shows up underneath.
- **Stream** a short file to show that a print is nothing but those lines, thousands of times. Pause mid-way, point at the preview, resume.
- **Print now** for the real thing. The printer runs the file itself, at full speed, with its own progress display; the page shows state and temperatures and offers Pause / Stop.

## How it works

```
browser (web_ui.html)  ──HTTP, localhost:8347──▶  bambu_web.py  ──MQTT over TLS, port 8883──▶  printer
                                                        └───────FTPS, port 990──────────────▶  SD card
```

- **MQTT** carries control. Every command is a JSON message published to `device/<serial>/request`; the printer answers on `device/<serial>/report`. A single G-code line goes as `{"print":{"command":"gcode_line","param":"G28\n"}}`. The user name is always `bblp`, the password is the access code.
- **The reply is only an acknowledgement.** The printer says "accepted", not "finished", and it never returns command output — `M114` (report position) and `M503` (report settings) produce nothing. Temperatures, state and progress arrive as periodic status pushes instead. This is a firmware property, not a limitation of this tool.
- **FTPS** carries files. Uploads land in the SD card's root folder.

### How the one-click print works

The firmware's "start a print" command (`project_file`) only executes `.gcode.3mf` files with the structure Bambu Studio produces — a zip containing the G-code plus a dozen metadata files. A plain `.gcode` on the SD card can be started from the touchscreen, but not remotely, and hand-made 3mf files are silently ignored or hang in "preparing" (we tried, at length).

So **Print now** injects your G-code into a genuine skeleton, `a1_skeleton.gcode.3mf`, sliced with the Bambu Studio command-line slicer from a 10 mm cube with stock A1 profiles: the skeleton's header comment blocks are kept, its executable block is replaced by your file, the MD5 sidecar is recomputed, and the result is uploaded and started. Files that already have Bambu Studio's header (anything exported from Studio) are used unchanged. Verified on the A1: `IDLE → PREPARE → RUNNING` within seconds.

Credits for the protocol knowledge: [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI), [ha-bambulab](https://github.com/greghesp/ha-bambulab), [bambulabs_api](https://github.com/acse-ci223/bambulabs_api), [bambuddy](https://github.com/maziggy/bambuddy) and [open-bamboo-networking](https://github.com/ClusterM/open-bamboo-networking).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Connect panel says *connection refused* | Wrong access code, or Developer Mode not enabled. Re-read the code from the LAN-only page; after toggling the modes, restart the printer. |
| *Printer not found* | Broadcast discovery is blocked (campus Wi-Fi, Windows "Public" network). Type the IP from the printer's screen into the Connect panel; if that is still not enough, add the serial number (*Settings → Device*). Also close Bambu Studio / OrcaSlicer — they occupy the discovery port. |
| Commands answer `ok` but nothing happens; the screen says *device is busy* | The firmware's job manager is wedged. Power-cycle the printer. |
| Print starts, but the first centimetre of the first line is empty | No prime after the start sequence. Add `G1 E0.8 F600` before the first drawing move (see the square example). |
| *FTP upload failed: timed out* | The printer's FTP server sometimes closes the connection late. The upload usually completed anyway; press Refresh in the SD list. |
| *port 8347 unavailable* | Another console window is still open. Close it (or use `--port 8350`). |
| Printer shows `FAILED` after you pressed Stop | That is simply Bambu's name for "cancelled". The next print starts normally. |
| Remote print never starts on a non-A1 printer | The skeleton is sliced for the A1. Slice any small object for your model in Bambu Studio, export it as `.gcode.3mf`, and replace `a1_skeleton.gcode.3mf` with it. |

## Limitations and notes

- No position read-back (`M114`) — see above. Teach the concept with a simulator such as OctoPrint's virtual printer if you need the reply format.
- `Stream` waits for an acknowledgement per line but the printer buffers moves, so "Pause" takes effect a few moves later. It is meant for demos, not for printing a 50 000-line file.
- `--host 0.0.0.0` publishes the page to everyone on the network **with no password**. Anyone who opens it can heat and move the printer. Use it only on a trusted classroom network and stop the bridge afterwards. (The access code itself is pre-filled only in the browser on the teacher's machine; other devices never receive it.)
- The access code is stored in plain text in `~/.bambu-gcode-console.json` (it is a LAN-only device password, but treat the file accordingly; delete it on shared computers).
- TLS certificate checks are disabled for the printer (it uses Bambu's private CA). Fine on a LAN you control.

## Disclaimer

This is classroom software written by a teacher, with an AI pair-programmer, over a few evenings. It is provided "as is", without warranty of any kind. It talks to an unofficial, undocumented interface that Bambu Lab may change at any time, and running a printer in Developer Mode is outside Bambu's support. You are responsible for anything the printer does while this tool is connected. Not affiliated with or endorsed by Bambu Lab.

## License

[MIT](LICENSE) © 2026 Chenming He
