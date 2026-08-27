# 📁 LAN File Share

A tiny, zero-config file-sharing server for your local network. Drop it on one
machine, and every device on the same Wi-Fi can upload, download, and delete
files through a clean dark-themed web UI — no accounts, no cloud, no setup.

Includes a **QR code page**: point your phone's camera at the screen and you're
connected, no typing IP addresses.

## Features

- 🚀 **Any size, fast** — uploads stream straight to disk in 1 MB chunks
  (constant memory, no size cap); downloads stream back the same way.
  Measured ~230 MB/s upload and ~460 MB/s download over loopback.
- ⏸️ **Resumable downloads** — full HTTP `Range` support (206 Partial Content),
  so download managers and browsers can resume interrupted transfers.
- 🖱️ **Drag & drop** with live progress bars and per-file speed readout,
  multi-file uploads supported.
- 📥 **Real-time receiving progress** — the downloading device sees a live
  "received / total · %" bar with speed, and the file streams straight to disk
  (File System Access API) instead of buffering in memory.
- 📤 **Upload** files through the browser (drag into the form)
- 📋 **List** all shared files with human-readable sizes
- ⬇️ **Download** with one click
- 🗑️ **Delete** with confirmation
- 📱 **QR code page** (`/qr`) — auto-detects your LAN IP and renders a
  scannable code, generated server-side (works offline, no CDN)
- 🔒 **Path-traversal hardened** — all routes sanitize filenames
- 🌐 Binds `0.0.0.0` so phones, tablets, and other laptops can connect

## Quick start

**Requirements:** Python 3.9+

### Windows

Double-click `run.bat` — it creates a virtual environment, installs
dependencies, and starts the server.

### Linux / macOS

```bash
chmod +x run.sh
./run.sh
```

### Manual

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:8000** on the host machine.

## Connecting other devices

1. Find the host's LAN IP (the server prints it, or run `ipconfig` / `ip a`)
2. Open **http://\<lan-ip\>:8000/qr** on the host's screen
3. Scan the QR code with your phone's camera — done

> **Firewall note (Windows):** the first run may prompt for firewall access —
> allow it. If devices still can't connect, add a rule from an
> **Administrator** terminal:
>
> ```
> netsh advfirewall firewall add rule name="FileShare 8000" dir=in action=allow protocol=TCP localport=8000
> ```

## Endpoints

| Method | Path              | Description                    |
|--------|-------------------|--------------------------------|
| GET    | `/`               | Web UI (upload / list / delete)|
| POST   | `/upload`         | Multipart file upload          |
| GET    | `/files/{name}`   | Download a file                |
| GET    | `/delete/{name}`  | Delete a file                  |
| GET    | `/qr`             | QR code page for phone access  |

Uploaded files are stored in `uploads/` next to `app.py`.

## ⚠️ Security

This server has **no authentication** — anyone on your network can upload,
download, and delete files. It is designed for trusted home/office Wi-Fi.
Don't expose it to the internet (no port forwarding, no tunneling).

## Tech

[FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/),
QR codes via [qrcode](https://pypi.org/project/qrcode/) (server-side SVG).
Single file: `app.py`.

## License

MIT
