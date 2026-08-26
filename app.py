from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
import os
import re
import io
import shutil
import socket
from pathlib import Path
import qrcode
import qrcode.image.svg

app = FastAPI(title="File Share Server")

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

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
    # qrcode emits namespace-prefixed <svg:rect> elements — valid XML but the
    # HTML parser treats them as unknown elements and renders nothing. The root
    # <svg> declares the default namespace, so plain <rect> inherits it.
    svg = svg.replace("svg:rect", "rect").replace("svg:path", "path")
    # qrcode sizes everything in mm with no viewBox, so the browser draws it
    # at ~3.8px/mm stuck in the corner of its container. Convert the ROOT svg's
    # mm dimensions to a unitless viewBox so CSS can scale it to any size.
    # (Only the root tag — child rects keep their mm coords, which become
    # viewBox units once the root is unitless.)
    svg = re.sub(
        r'(<svg[^>]*)width="([\d.]+)mm" height="([\d.]+)mm"',
        r'\1viewBox="0 0 \2 \3" width="100%" height="100%"',
        svg,
        count=1,
    )
    svg = svg.replace('mm"', '"')
    return svg

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    files = []
    for f in UPLOAD_DIR.iterdir():
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "size_str": format_size(stat.st_size)
            })
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>File Share Server</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e; color: #eaeaea; min-height: 100vh; padding: 2rem;
            }
            .container { max-width: 800px; margin: 0 auto; }
            h1 { color: #00d9ff; margin-bottom: 1.5rem; font-weight: 600; }
            .upload-form { 
                background: #16213e; padding: 1.5rem; border-radius: 8px; 
                margin-bottom: 2rem; border: 1px solid #0f3460;
            }
            .upload-form input[type="file"] { 
                display: block; margin-bottom: 1rem; color: #eaeaea;
            }
            .upload-form button { 
                background: #00d9ff; color: #1a1a2e; border: none; 
                padding: 0.75rem 1.5rem; border-radius: 4px; cursor: pointer;
                font-weight: 600; font-size: 1rem;
            }
            .upload-form button:hover { background: #00b8d4; }
            table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; }
            th, td { padding: 1rem; text-align: left; border-bottom: 1px solid #0f3460; }
            th { background: #0f3460; color: #00d9ff; font-weight: 600; }
            tr:hover { background: #1f2a4a; }
            .file-name { font-family: monospace; }
            .file-size { color: #aaa; font-size: 0.9rem; }
            .actions a { 
                color: #00d9ff; text-decoration: none; margin-right: 1rem; 
                font-size: 0.9rem;
            }
            .actions a:hover { text-decoration: underline; }
            .actions a.delete { color: #ff4444; }
            .actions a.delete:hover { color: #ff6666; }
            .empty { text-align: center; color: #666; padding: 3rem; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>File Share Server <a href="/qr" style="font-size:1rem; color:#00d9ff; text-decoration:none; vertical-align:middle;">📱 QR code</a></h1>
            <form class="upload-form" action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="file" required>
                <button type="submit">Upload</button>
            </form>
            <table>
                <thead>
                    <tr>
                        <th>File Name</th>
                        <th>Size</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
    """
    
    if files:
        for f in files:
            html += f"""
                    <tr>
                        <td class="file-name">{f['name']}</td>
                        <td class="file-size">{f['size_str']}</td>
                        <td class="actions">
                            <a href="/files/{f['name']}">Download</a>
                            <a href="/delete/{f['name']}" class="delete" onclick="return confirm('Delete {f['name']}?')">Delete</a>
                        </td>
                    </tr>
            """
    else:
        html += """
                    <tr>
                        <td colspan="3" class="empty">No files uploaded yet</td>
                    </tr>
        """
    
    html += """
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

def format_size(bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = os.path.basename(file.filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = UPLOAD_DIR / safe_name
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"filename": safe_name, "size": file_path.stat().st_size}

@app.get("/files/{filename}")
async def download_file(filename: str):
    filename = os.path.basename(filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, filename=filename)

@app.get("/delete/{filename}")
async def delete_file(filename: str):
    filename = os.path.basename(filename)
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    file_path.unlink()
    return {"deleted": filename}

@app.get("/qr", response_class=HTMLResponse)
async def qr_page():
    ip = get_lan_ip()
    url = f"http://{ip}:8000"
    svg = make_qr_svg(url)
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Scan to connect — File Share Server</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e; color: #eaeaea; min-height: 100vh; padding: 2rem;
            }}
            .container {{ max-width: 480px; margin: 0 auto; text-align: center; }}
            h1 {{ color: #00d9ff; margin-bottom: 0.5rem; font-weight: 600; }}
            .hint {{ color: #aaa; margin-bottom: 1.5rem; }}
            .qr-card {{
                background: #ffffff; border-radius: 12px; padding: 1.5rem;
                display: inline-block; margin-bottom: 1.5rem;
            }}
            .qr-card svg {{ width: 280px; height: 280px; display: block; }}
            .url {{
                font-family: monospace; font-size: 1.2rem; color: #00d9ff;
                background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
                padding: 0.75rem 1rem; display: inline-block; margin-bottom: 1.5rem;
                user-select: all;
            }}
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
            <p class="back"><a href="/">← Back to file share</a></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)