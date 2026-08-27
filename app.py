"""LAN File Share — fast, any-size file sharing over your local network.

Design goals:
- ANY size: uploads stream straight to disk in 1 MB chunks (never buffered in
  memory), downloads stream back with HTTP Range support (resumable).
- FAST: blocking I/O runs in a threadpool so the event loop never stalls and
  multiple transfers run concurrently at disk/network speed.
- NO INTERNET: binds 0.0.0.0 on the LAN; QR page is generated server-side.
- SAFE: every route sanitizes filenames against path traversal.
"""
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
import os
import re
import io
import html
import shutil
import socket
import mimetypes
from pathlib import Path
import qrcode
import qrcode.image.svg

app = FastAPI(title="LAN File Share")

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CHUNK = 1024 * 1024  # 1 MB — good balance for LAN throughput


# ---------------------------------------------------------------- helpers
def get_lan_ip() -> str:
    """Best-guess LAN IP: UDP connect picks the real NIC route (no packets sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgImage)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    # qrcode emits namespace-prefixed <svg:rect> — valid XML but the HTML
    # parser renders them as nothing. Root <svg> declares the namespace, so
    # plain <rect> inherits it.
    svg = svg.replace("svg:rect", "rect").replace("svg:path", "path")
    # qrcode sizes in mm with no viewBox -> draws tiny in a corner. Convert the
    # ROOT svg's mm dims to a unitless viewBox so CSS scales it to any size.
    svg = re.sub(
        r'(<svg[^>]*)width="([\d.]+)mm" height="([\d.]+)mm"',
        r'\1viewBox="0 0 \2 \3" width="100%" height="100%"',
        svg,
        count=1,
    )
    svg = svg.replace('mm"', '"')
    return svg


def format_size(num: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def unique_path(directory: Path, name: str) -> Path:
    """Avoid clobbering an existing file: foo.bin -> foo (1).bin, foo (2).bin..."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    i = 1
    while True:
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def iter_file(path: Path, start: int, end: int):
    """Yield [start, end] inclusive of a file in CHUNK pieces (sync generator —
    Starlette runs it in a threadpool, so it never blocks the event loop)."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


# ---------------------------------------------------------------- index UI
INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LAN File Share</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #eaeaea; min-height: 100vh; padding: 2rem; }
  .container { max-width: 860px; margin: 0 auto; }
  h1 { color: #00d9ff; margin-bottom: 1.5rem; font-weight: 600; }
  h1 a { font-size: 1rem; color: #00d9ff; text-decoration: none; vertical-align: middle; }
  .drop { border: 2px dashed #0f3460; border-radius: 12px; padding: 2.5rem 1.5rem;
          text-align: center; cursor: pointer; transition: all .15s; background: #16213e;
          margin-bottom: 1rem; }
  .drop.over { border-color: #00d9ff; background: #1f2a4a; }
  .drop .big { font-size: 1.1rem; color: #eaeaea; margin-bottom: .35rem; }
  .drop .sub { color: #888; font-size: .9rem; }
  .drop input { display: none; }
  #bars { margin-bottom: 1.5rem; }
  .bar { background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
         padding: .7rem 1rem; margin-bottom: .6rem; }
  .bar .row { display: flex; justify-content: space-between; font-size: .85rem;
              margin-bottom: .45rem; color: #ccc; }
  .bar .track { height: 8px; background: #0f3460; border-radius: 4px; overflow: hidden; }
  .bar .fill { height: 100%; width: 0%; background: #00d9ff; transition: width .15s; }
  .bar.done .fill { background: #2ecc71; }
  .bar.err .fill { background: #ff4444; }
  table { width: 100%; border-collapse: collapse; background: #16213e;
          border-radius: 8px; overflow: hidden; }
  th, td { padding: .9rem 1rem; text-align: left; border-bottom: 1px solid #0f3460; }
  th { background: #0f3460; color: #00d9ff; font-weight: 600; }
  tr:hover { background: #1f2a4a; }
  .file-name { font-family: monospace; word-break: break-all; }
  .file-size { color: #aaa; font-size: .9rem; white-space: nowrap; }
  .actions a { color: #00d9ff; text-decoration: none; margin-right: 1rem; font-size: .9rem; }
  .actions a:hover { text-decoration: underline; }
  .actions a.delete { color: #ff4444; }
  .empty { text-align: center; color: #666; padding: 3rem; }
</style>
</head>
<body>
<div class="container">
  <h1>LAN File Share <a href="/qr">&#128241; QR code</a></h1>

  <label class="drop" id="drop">
    <div class="big">Drop files here, or click to browse</div>
    <div class="sub">Any size &middot; streams straight to disk &middot; resumable downloads</div>
    <input type="file" id="fileInput" multiple>
  </label>
  <div id="bars"></div>

  <table>
    <thead><tr><th>File Name</th><th>Size</th><th>Actions</th></tr></thead>
    <tbody>
<!--ROWS-->
    </tbody>
  </table>
</div>

<script>
(function () {
  var drop = document.getElementById('drop');
  var input = document.getElementById('fileInput');
  var bars = document.getElementById('bars');
  var pending = 0, succeeded = 0;

  function fmtSpeed(bps) {
    if (!isFinite(bps) || bps <= 0) return '';
    var u = ['B/s','KB/s','MB/s','GB/s']; var i = 0;
    while (bps >= 1024 && i < u.length - 1) { bps /= 1024; i++; }
    return bps.toFixed(1) + ' ' + u[i];
  }

  function makeBar(name) {
    var el = document.createElement('div');
    el.className = 'bar';
    el.innerHTML =
      '<div class="row"><span class="nm"></span><span class="st">0%</span></div>' +
      '<div class="track"><div class="fill"></div></div>';
    el.querySelector('.nm').textContent = name;
    bars.appendChild(el);
    var lastBytes = 0, lastT = Date.now();
    return {
      el: el,
      set: function (frac, bps) {
        el.querySelector('.fill').style.width = (frac * 100).toFixed(1) + '%';
        var st = (frac * 100).toFixed(0) + '%';
        if (bps) st += ' &middot; ' + fmtSpeed(bps);
        el.querySelector('.st').innerHTML = st;
      },
      done: function () { el.classList.add('done'); el.querySelector('.fill').style.width = '100%'; el.querySelector('.st').textContent = 'Done'; },
      error: function () { el.classList.add('err'); el.querySelector('.st').textContent = 'Failed'; }
    };
  }

  function maybeReload() {
    if (pending === 0 && succeeded > 0) { setTimeout(function(){ location.reload(); }, 500); }
  }

  function uploadOne(file) {
    var bar = makeBar(file.name);
    var fd = new FormData();
    fd.append('file', file);
    var xhr = new XMLHttpRequest();
    var lastBytes = 0, lastT = Date.now();
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var now = Date.now(), dt = (now - lastT) / 1000;
      var bps = dt > 0 ? (e.loaded - lastBytes) / dt : 0;
      lastBytes = e.loaded; lastT = now;
      bar.set(e.loaded / e.total, bps);
    };
    xhr.onload = function () {
      pending--;
      if (xhr.status === 200) { succeeded++; bar.done(); } else { bar.error(); }
      maybeReload();
    };
    xhr.onerror = function () { pending--; bar.error(); maybeReload(); };
    pending++;
    xhr.open('POST', '/upload');
    xhr.send(fd);
  }

  function uploadFiles(list) {
    for (var i = 0; i < list.length; i++) uploadOne(list[i]);
  }

  input.addEventListener('change', function () { uploadFiles(input.files); input.value = ''; });
  drop.addEventListener('click', function () { input.click(); });
  ['dragenter','dragover'].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave','drop'].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    files = []
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            files.append((f.name, f.stat().st_size))
    files.sort(key=lambda x: x[0].lower())

    if files:
        rows = []
        for name, size in files:
            esc = html.escape(name, quote=True)
            rows.append(
                f'<tr><td class="file-name">{esc}</td>'
                f'<td class="file-size">{format_size(size)}</td>'
                f'<td class="actions">'
                f'<a href="/files/{html.escape(name, quote=True)}">Download</a>'
                f'<a href="/delete/{html.escape(name, quote=True)}" class="delete" '
                f"onclick=\"return confirm('Delete {esc}?')\">Delete</a>"
                f"</td></tr>"
            )
        rows_html = "\n".join(rows)
    else:
        rows_html = '<tr><td colspan="3" class="empty">No files yet — drop some above</td></tr>'

    return HTMLResponse(content=INDEX_TEMPLATE.replace("<!--ROWS-->", rows_html))


# ---------------------------------------------------------------- upload
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = os.path.basename(file.filename or "")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    dest = unique_path(UPLOAD_DIR, safe_name)

    def _stream_to_disk():
        # copyfileobj streams in CHUNK pieces — constant memory for any size.
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, length=CHUNK)

    await run_in_threadpool(_stream_to_disk)
    size = dest.stat().st_size
    return {"filename": dest.name, "size": size, "size_str": format_size(size)}


# ---------------------------------------------------------------- download (Range-capable)
@app.get("/files/{filename}")
async def download_file(filename: str, request: Request):
    filename = os.path.basename(filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = f'attachment; filename="{filename}"'
    base_headers = {"Accept-Ranges": "bytes", "Content-Disposition": disposition}

    range_header = request.headers.get("range")
    if not range_header:
        headers = dict(base_headers)
        headers["Content-Length"] = str(file_size)
        return StreamingResponse(
            iter_file(file_path, 0, file_size - 1),
            status_code=200, headers=headers, media_type=media_type,
        )

    m = re.search(r"bytes=(\d*)-(\d*)", range_header)
    if not m:
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    start_s, end_s = m.group(1), m.group(2)

    if start_s == "" and end_s == "":
        start, end = 0, file_size - 1
    elif start_s == "":
        suffix = int(end_s)                      # last N bytes
        start, end = max(0, file_size - suffix), file_size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    end = min(end, file_size - 1)

    if start > end or start >= file_size:
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    length = end - start + 1
    headers = dict(base_headers)
    headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    headers["Content-Length"] = str(length)
    return StreamingResponse(
        iter_file(file_path, start, end),
        status_code=206, headers=headers, media_type=media_type,
    )


# ---------------------------------------------------------------- delete
@app.get("/delete/{filename}")
async def delete_file(filename: str):
    filename = os.path.basename(filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    file_path.unlink()
    return {"deleted": filename}


# ---------------------------------------------------------------- QR page
@app.get("/qr", response_class=HTMLResponse)
async def qr_page():
    ip = get_lan_ip()
    url = f"http://{ip}:8000"
    svg = make_qr_svg(url)
    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scan to connect — LAN File Share</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #eaeaea; min-height: 100vh; padding: 2rem; }}
  .container {{ max-width: 480px; margin: 0 auto; text-align: center; }}
  h1 {{ color: #00d9ff; margin-bottom: .5rem; font-weight: 600; }}
  .hint {{ color: #aaa; margin-bottom: 1.5rem; }}
  .qr-card {{ background: #fff; border-radius: 12px; padding: 1.5rem;
             display: inline-block; margin-bottom: 1.5rem; }}
  .qr-card svg {{ width: 280px; height: 280px; display: block; }}
  .url {{ font-family: monospace; font-size: 1.2rem; color: #00d9ff; background: #16213e;
         border: 1px solid #0f3460; border-radius: 8px; padding: .75rem 1rem;
         display: inline-block; margin-bottom: 1.5rem; user-select: all; }}
  .back a {{ color: #00d9ff; text-decoration: none; }}
  .back a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="container">
  <h1>Scan to connect</h1>
  <p class="hint">Point your phone's camera at the code (same Wi-Fi network)</p>
  <div class="qr-card">{svg}</div>
  <div><span class="url">{url}</span></div>
  <p class="back"><a href="/">&larr; Back to file share</a></p>
</div>
</body>
</html>"""
    return HTMLResponse(content=page)


if __name__ == "__main__":
    import uvicorn
    print(f"LAN File Share starting — open http://{get_lan_ip()}:8000 from other devices")
    uvicorn.run(app, host="0.0.0.0", port=8000)
