# Auracast Sender

**Broadcast audio from your Mac to Auracast-enabled hearing aids** — microphone, system audio, or audio files, sent via a USB LE Audio dongle straight to your ears.

Built for and tested with **GN ReSound Nexia** hearing aids and the **FlooGoo FMA120** dongle on Apple Silicon Macs.

> **Why this exists:** macOS cannot transmit Bluetooth LE Audio / Auracast natively — Apple's Bluetooth stack does not expose ISO channels, on any Mac. This app solves it pragmatically: a class-compliant USB dongle handles the Auracast transmission internally, and this app routes any audio you want into it, with a native GUI and an HTTP API for remote control.

## Features

- 🎤 **Three audio sources:** microphone, system audio (via BlackHole loopback), or WAV files
- 🖥️ **Native macOS GUI** (Tkinter, no browser needed) with live VU meter and volume control
- 🌐 **Built-in REST API** (port 8765) — control broadcasts from a web app or scripts; OpenAPI docs at `/docs`
- 🔍 **Auto-detects Auracast dongles** (FMA120 shows up as "QCC3086 USB Dongle" on macOS)
- 📦 **Ships as a standalone .app** — no Python installation needed on the target machine

## Hardware requirements

| What | Details |
|------|---------|
| Mac | Apple Silicon (M1 or newer) for the prebuilt app; Intel works from source |
| Dongle | **FlooGoo FMA120** (recommended for hearing aids) — [flairmesh.com](https://www.flairmesh.com/Dongle/FMA120.html) |
| Receiver | Auracast-capable hearing aids (tested: ReSound Nexia) or headphones |

### ⚠️ Hard-won compatibility lessons

These cost days of debugging — read them before buying hardware:

1. **The built-in Mac Bluetooth chip cannot do Auracast.** Not with any software. The chip (e.g. BCM4378) hangs off PCIe and macOS never exposes LE Audio ISO channels. A USB dongle is mandatory.
2. **Hearing aids can only receive *standard-quality* Auracast broadcasts.** The Sennheiser BTD 700 transmits high-quality-only broadcasts — hearing aids will *see* the broadcast but fail to sync audio ("stream not found"). It works great for headphones, not for hearing aids.
3. **The FMA120 must be configured once** using [FlooCast](https://github.com/Flairmesh/FlooCast): set mode to **Broadcast**, and turn **"Broadcast High-Quality Music" OFF**. Settings persist inside the dongle.
4. **iPhone Auracast support is limited (2026).** It works on iOS with this version, but Android seems more stable. Use an Android phone with your hearing aid app if you need stability (e.g. ReSound Smart 3D) as the broadcast assistant. 
5. **Hearing aids must rejoin the broadcast** in their app every time the stream restarts. Silence usually means "not joined", not "broken".

## Installation

### Option A — prebuilt app (Apple Silicon)

1. Download the latest release from the [Releases page](../../releases)
2. Unzip, then double-click `Installera.command` (installs to /Applications and puts a shortcut on your Desktop), **or** just drag `Auracast Sender.app` to /Applications
3. If macOS complains about an unidentified developer: right-click the app → Open → Open

### Option B — from source (any Mac)

```bash
git clone https://github.com/matsguldbrand/auracast-sender.git
cd auracast-sender
bash setup.sh              # installs Homebrew deps + Python venv + packages
./start_auracast.command   # GUI (includes the API server)
```

### System audio (optional)

To broadcast what the Mac is playing (music, video calls, YouTube):

```bash
brew install blackhole-2ch   # then reboot
```

Set **BlackHole 2ch** as the output device in System Settings → Sound, and pick "System sound" as the source in the app.

## Usage

1. Plug in the FMA120
2. Start Auracast Sender — the dongle is auto-detected (★ in the output list)
3. Pick a source, hit start
4. On your phone's hearing aid app, join the broadcast (named `FlooGoo_xxxxxx`)

## HTTP API

The app embeds a REST API on `http://localhost:8765` (open CORS), so you can build your own remote control — a web app, Shortcuts, a Stream Deck button:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | Liveness check |
| `GET /api/devices` | List audio devices; Auracast dongles flagged |
| `GET /api/status` | Broadcast state + VU level (poll for meters) |
| `POST /api/start` | Start broadcast `{source, volume, loop, file_path?}` |
| `POST /api/stop` | Stop broadcast |
| `POST /api/volume` | Live volume `{volume: 0..1}` |
| `POST /api/upload` | Upload a WAV file |

Interactive documentation at `http://localhost:8765/docs`. To control it over the internet, put a tunnel in front (e.g. `cloudflared tunnel --url http://localhost:8765`).

## Architecture notes (for the curious)

- **No Bluetooth code at all.** The dongle is a class-compliant USB audio device; Auracast happens inside it. The app "just" routes PCM audio — which turned out to be the reliable path.
- **Audio I/O runs in a fresh subprocess per broadcast** (`audio_worker.py`). Auracast dongles get a *new* CoreAudio device ID when they switch modes, and a long-lived process that initialized PortAudio earlier can never reopen them (error -10851). A fresh process always can. In the frozen .app, the binary re-executes itself with `--worker`.
- **Devices are resolved by name, not index** — indices shuffle when dongles re-enumerate.
- The GUI and the HTTP API share one `BroadcastManager`, so a broadcast started from a web app is visible and stoppable in the GUI, and vice versa.

## Building the .app

```bash
pip3 install pyinstaller
bash bygg_app.sh   # PyInstaller + Info.plist mic-permission patch + codesign
```

The Info.plist patch matters: without `NSMicrophoneUsageDescription`, macOS silently blocks microphone access for bundled apps.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dongle not detected | Re-plug, click "Uppdatera" in the app |
| Broadcast visible but no audio in hearing aids | FlooCast: turn "Broadcast High-Quality Music" **OFF** |
| Broadcast not visible at all | FlooCast: set dongle mode to **Broadcast** |
| Silent after restarting a broadcast | Rejoin the broadcast in your hearing aid app |
| iPhone can't join | Use Android; iOS Auracast support is still limited |
| Mic silent (from source) | Grant microphone permission; for custom builds use `bygg_app.sh` |

## License

[MIT](LICENSE) — do whatever you like, no warranty.

*The app UI is currently in Swedish. PRs for localization are welcome.*

---

*Built with [Claude Code](https://claude.com/claude-code) through many hours of stubborn debugging. If this helped you hear something you otherwise couldn't — that's what it was for.* 💙
