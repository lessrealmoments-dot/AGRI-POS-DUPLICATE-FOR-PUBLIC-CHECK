# Cursor AI: AgriBooks Print Terminal — Windows EXE (Complete Build Guide)

## Overview

Build a **Windows Desktop EXE** for the AgriBooks Remote Branch Printing Terminal.
The EXE:
- Logs in with AgriBooks credentials
- Registers itself to a branch
- Connects via WebSocket to receive print jobs in real-time
- Supports **two job types**: HTML-based (AgriBooks documents) and **file-based** (uploaded PDFs/images)
- Prints silently (Auto mode) or shows a manual queue (Manual mode)
- Auto-starts with Windows, lives in the system tray

---

## Tech Stack

| Component      | Technology                                    |
|----------------|-----------------------------------------------|
| Language       | Python 3.10+                                  |
| GUI            | PyQt6 (system tray + windows)                 |
| HTTP           | `requests` (sync) or `httpx` (async)          |
| WebSocket      | `websocket-client` (threaded)                 |
| Print — HTML   | Edge/Chrome headless or `pywin32` IE COM      |
| Print — PDF    | Sumatra PDF CLI (`SumatraPDF.exe -print-to-default`) |
| Print — Image  | `PIL` + `win32print` raw send                 |
| Auto-start     | `winreg` (HKCU Run key)                       |
| Packaging      | PyInstaller (`--onefile --windowed`)          |
| Config storage | `%APPDATA%\AgriBooks\config.json`             |

**requirements.txt**
```
requests>=2.31.0
websocket-client>=1.7.0
PyQt6>=6.6.0
pywin32>=306
Pillow>=10.0.0
pyinstaller>=6.0.0
```

---

## Backend API Contract

### Base URL
Configurable in settings. Default: `https://agri-books.com`
- REST: `{BASE_URL}/api/...`
- WebSocket: `{BASE_URL_WS}/api/...` (replace `https` → `wss`, `http` → `ws`)

---

### 1. Login

```
POST {BASE_URL}/api/terminal/credential-pair
Content-Type: application/json

{
  "email": "staff@branch.com",
  "password": "password123"
}
```

**Response A — admin, must select branch:**
```json
{
  "status": "select_branch",
  "branches": [{"id": "uuid", "name": "Main Branch"}, ...],
  "user_name": "Admin Name",
  "is_admin": true
}
```

**Response B — logged in:**
```json
{
  "status": "paired",
  "token": "eyJhbGci...",
  "terminal_id": "uuid",
  "branch_id": "uuid",
  "branch_name": "Main Branch",
  "user_name": "Staff Name",
  "organization_id": "uuid",
  "is_admin": false
}
```

**Login with branch selection (for admins):**
```json
{
  "email": "...", "password": "...",
  "branch_id": "selected-branch-uuid"
}
```

**Persist to config:**
```json
{
  "token": "...", "terminal_id": "...",
  "branch_id": "...", "branch_name": "...",
  "user_name": "...", "organization_id": "...",
  "server_url": "https://agri-books.com",
  "print_mode": "manual"
}
```

---

### 2. WebSocket Connection (Real-time)

```
WSS: wss://agri-books.com/api/terminal/ws/terminal/{terminal_id}
```

**Received messages:**

```json
// ── AgriBooks HTML document ──────────────────────────────────────────
{
  "type": "print_job",
  "data": {
    "job_id": "uuid",
    "document_type": "sales_receipt",
    "document_name": "Sales Receipt #INV-2025-001",
    "source_type": "internal",
    "html_content": "<!DOCTYPE html><html>...",
    "priority": "normal",
    "created_at": "2025-01-15T10:30:00Z"
  }
}

// ── Uploaded external document (PDF or image) ────────────────────────
{
  "type": "print_job",
  "data": {
    "job_id": "uuid",
    "document_type": "external_document",
    "document_name": "Business Permit 2025",
    "source_type": "external",
    "file_url": "https://...presigned-url.../file.pdf",
    "file_type": "pdf",
    "file_name": "business_permit_2025.pdf",
    "description": "Print 3 copies",
    "priority": "normal",
    "created_at": "2025-01-15T10:30:00Z"
  }
}

// ── Print mode changed remotely ──────────────────────────────────────
{
  "type": "print_mode_changed",
  "data": {"mode": "auto"}
}
```

**Auto-reconnect logic:**
```python
def connect_ws():
    while True:
        try:
            ws = websocket.WebSocketApp(ws_url, on_message=on_message, ...)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception:
            pass
        time.sleep(3)  # reconnect after 3 seconds

# Run in background thread
threading.Thread(target=connect_ws, daemon=True).start()
```

**On reconnect: always poll pending jobs** (catches jobs delivered while offline).

---

### 3. Polling Endpoint (fallback + reconnect)

```
GET {BASE_URL}/api/print/jobs/pending
Authorization: Bearer {token}
```

**Response:**
```json
{
  "jobs": [
    {
      "id": "uuid",
      "document_type": "sales_receipt",
      "document_name": "Sales Receipt #INV-001",
      "source_type": "internal",
      "html_content": "<!DOCTYPE html>...",
      "status": "sent",
      "created_at": "..."
    },
    {
      "id": "uuid",
      "document_type": "external_document",
      "document_name": "Business Permit 2025",
      "source_type": "external",
      "file_url": "https://...presigned-url.../permit.pdf",
      "file_type": "pdf",
      "file_name": "permit.pdf",
      "description": "Print 3 copies",
      "status": "sent"
    }
  ],
  "terminal_id": "uuid",
  "print_mode": "manual",
  "branch_id": "uuid",
  "branch_name": "Main Branch"
}
```

Poll on startup + every reconnect. Optionally poll every 60s as fallback.

---

### 4. Update Job Status

```
PUT {BASE_URL}/api/print/jobs/{job_id}/status
Authorization: Bearer {token}
Content-Type: application/json

{"status": "printed"}
// or: {"status": "failed", "error_message": "Printer not found"}
// or: {"status": "cancelled"}
```

---

### 5. Get Fresh Presigned URL (for expired external doc URLs)

```
GET {BASE_URL}/api/print/jobs/{job_id}/file-url
Authorization: Bearer {token}
```

**Response:**
```json
{"job_id": "uuid", "file_url": "https://...new-24hr-url...", "expires_in": 86400}
```

Call this if the `file_url` seems expired (HTTP 403 or 401 when downloading).

---

### 6. Token Refresh (keep session alive)

```
POST {BASE_URL}/api/terminal/refresh-token
Authorization: Bearer {token}
```

Call every 12 hours. On 401 → re-login required.

---

### 7. Get/Set Print Mode

```
GET  {BASE_URL}/api/print/terminal/session    → returns {"print_mode": "manual"|"auto", ...}
POST {BASE_URL}/api/print/terminal/set-mode   → {"terminal_id": "...", "mode": "auto"|"manual"}
```

---

## Printing Logic

### Job Router (on_print_job)

```python
def handle_job(job: dict):
    source_type = job.get("source_type", "internal")
    if source_type == "external":
        handle_external_job(job)
    else:
        handle_html_job(job)
```

---

### HTML Job (AgriBooks internal documents)

```python
def handle_html_job(job: dict):
    html = job.get("html_content", "")
    if not html:
        mark_failed(job["job_id"], "No HTML content")
        return
    tmp = write_temp_html(html)
    success = print_html_file(tmp)
    os.unlink(tmp)
    if success:
        mark_printed(job["job_id"])
    else:
        mark_failed(job["job_id"], "Print error")

def write_temp_html(html: str) -> str:
    import tempfile
    # Inject auto-print script
    html = html.replace("</body>", """
<script>
var p=false;
function go(){if(p)return;p=true;window.print();setTimeout(function(){window.close();},3000);}
var imgs=document.images;
if(!imgs.length){go();return;}
var r=imgs.length;
for(var i=0;i<imgs.length;i++){
  if(imgs[i].complete){r--;if(!r)go();}
  else{imgs[i].onload=imgs[i].onerror=function(){r--;if(!r)go();};}
}
setTimeout(go,5000);
</script></body>""")
    f = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
    f.write(html)
    f.close()
    return f.name

def print_html_file(path: str) -> bool:
    """Try Edge, then Chrome, then os.startfile as fallback."""
    import subprocess, os
    browsers = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for b in browsers:
        if os.path.exists(b):
            try:
                subprocess.run([b, "--headless", "--disable-gpu",
                                "--print-to-default-printer", path],
                               timeout=30, check=True)
                return True
            except Exception:
                continue
    # Fallback: open in browser with print dialog
    os.startfile(path, "print")
    return True
```

---

### External Document Job (PDF / Image)

```python
def handle_external_job(job: dict):
    file_url  = job.get("file_url", "")
    file_type = job.get("file_type", "pdf")   # "pdf" or "image"
    file_name = job.get("file_name", "document")
    job_id    = job["job_id"]

    # Download file
    try:
        tmp_path = download_file(job_id, file_url, file_name)
    except Exception as e:
        # URL may have expired — fetch fresh URL
        try:
            fresh = refresh_file_url(job_id)
            tmp_path = download_file(job_id, fresh, file_name)
        except Exception as e2:
            mark_failed(job_id, f"Download failed: {e2}")
            return

    # Print
    try:
        if file_type == "pdf":
            success = print_pdf(tmp_path)
        else:
            success = print_image(tmp_path)
        if success:
            mark_printed(job_id)
        else:
            mark_failed(job_id, "Print returned failure")
    except Exception as e:
        mark_failed(job_id, str(e))
    finally:
        try: os.unlink(tmp_path)
        except: pass


def download_file(job_id: str, url: str, original_name: str) -> str:
    import requests, tempfile, os
    ext = original_name.rsplit(".", 1)[-1] if "." in original_name else "bin"
    tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
    r = requests.get(url, timeout=60, stream=True)
    r.raise_for_status()
    for chunk in r.iter_content(8192):
        tmp.write(chunk)
    tmp.close()
    return tmp.name


def print_pdf(path: str) -> bool:
    """Silent PDF print using Sumatra PDF CLI."""
    import subprocess, os
    sumatra_paths = [
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        os.path.join(os.path.dirname(sys.executable), "SumatraPDF.exe"),
    ]
    for s in sumatra_paths:
        if os.path.exists(s):
            subprocess.run([s, "-print-to-default", "-silent", path],
                           timeout=60, check=True)
            return True

    # Fallback: Edge headless print PDF
    import subprocess
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if os.path.exists(edge):
        subprocess.run([edge, "--headless", "--disable-gpu",
                        "--print-to-default-printer", path],
                       timeout=60, check=True)
        return True

    # Last fallback: shell print (shows dialog)
    os.startfile(path, "print")
    return True


def print_image(path: str) -> bool:
    """Print image using PIL + win32print."""
    import win32print, win32ui
    from PIL import Image

    printer_name = win32print.GetDefaultPrinter()
    img = Image.open(path)

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_name)
    hdc.StartDoc(path)
    hdc.StartPage()

    dpi_x = hdc.GetDeviceCaps(88)  # LOGPIXELSX
    dpi_y = hdc.GetDeviceCaps(90)  # LOGPIXELSY
    page_w = hdc.GetDeviceCaps(110) # PHYSICALWIDTH
    page_h = hdc.GetDeviceCaps(111) # PHYSICALHEIGHT

    # Scale to fit page
    img_w, img_h = img.size
    scale = min(page_w / img_w, page_h / img_h)
    w = int(img_w * scale)
    h = int(img_h * scale)

    dib = img.convert("RGB")
    hdc.StretchBlt((0, 0), (w, h), win32ui.CreateBitmapFromImage(dib), (0, 0), (img_w, img_h), 0xCC0020)
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()
    return True
```

---

## App Architecture

```
AgriBooksPrintTerminal/
├── main.py               # Entry point, PyQt6 app
├── config.py             # Load/save config.json
├── auth.py               # Login, token management, refresh
├── websocket_client.py   # WS connect, reconnect, dispatch
├── print_engine.py       # HTML print, PDF print, image print
├── api_client.py         # REST calls (status updates, polling)
├── ui/
│   ├── login_window.py   # Login form + branch selector
│   ├── tray_icon.py      # System tray icon + menu
│   ├── queue_window.py   # Manual print queue UI
│   └── settings_window.py# Server URL, autostart, printer
└── assets/
    ├── printer.ico       # App icon (green printer)
    └── SumatraPDF.exe    # Bundle Sumatra PDF for PDF printing
```

---

## System Tray Menu

```
AgriBooks Print Terminal
─────────────────────────
Branch: Main Branch
Status: ● Connected (Online)
─────────────────────────
Print Mode
  ○ Auto Print
  ● Manual Queue  ✓
─────────────────────────
Print Queue (3 pending)
Settings
─────────────────────────
Logout
Exit
```

- **Green dot** = WebSocket connected
- **Yellow dot** = Reconnecting
- **Red dot** = Offline / Error
- Double-click tray icon = open Print Queue window

---

## Print Queue Window (Manual Mode)

```
┌─────────────────────────────────────────────────────────┐
│  AgriBooks Print Queue — Main Branch                     │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │ [📄] Sales Receipt #INV-2025-001    [Print] [✕] │    │
│  │      AgriBooks Document · 2 min ago             │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ [📎] Business Permit 2025.pdf       [Print] [✕] │    │
│  │      External Document · Just now              │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  [Print All]                      [Close]               │
└─────────────────────────────────────────────────────────┘
```

Icons: 📄 = HTML/internal, 📎 = External file (PDF/image)

---

## Auto-Start with Windows

```python
import winreg, sys

APP_NAME = "AgriBooks Print Terminal"
EXE_PATH = sys.executable  # path to the built EXE

def enable_autostart():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, EXE_PATH)
    winreg.CloseKey(key)

def disable_autostart():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass
```

---

## Config Storage (`%APPDATA%\AgriBooks\config.json`)

```json
{
  "server_url": "https://agri-books.com",
  "token": "eyJhbGci...",
  "terminal_id": "uuid",
  "branch_id": "uuid",
  "branch_name": "Main Branch",
  "user_name": "Staff Name",
  "organization_id": "uuid",
  "print_mode": "manual",
  "autostart": true,
  "default_printer": ""
}
```

---

## Error Handling Table

| Scenario                     | Action                                        |
|------------------------------|-----------------------------------------------|
| Wrong login credentials      | Show error in login window                    |
| Token expired (401)          | Auto-logout → show login window              |
| Server unreachable           | Yellow tray, retry every 30s                  |
| WS disconnect                | Yellow tray, reconnect after 3s, poll on reconnect |
| File URL expired (403)       | Call `/api/print/jobs/{id}/file-url` for fresh URL |
| Sumatra PDF not found        | Fallback to Edge headless → shell print       |
| Printer not found            | Mark job failed, show tray notification       |
| File download timeout        | Mark job failed with error message            |
| Terminal purged (30 days)    | 404 on refresh → logout with message          |

---

## PyInstaller Build

```bash
# Option 1: Bundle SumatraPDF
pyinstaller \
  --onefile --windowed \
  --name "AgriBooks Print Terminal" \
  --icon "assets/printer.ico" \
  --add-data "assets/SumatraPDF.exe;assets" \
  --hidden-import "win32print" \
  --hidden-import "win32ui" \
  --hidden-import "PIL._tkinter_finder" \
  main.py

# Option 2: Standalone (user must have Sumatra/Edge installed)
pyinstaller \
  --onefile --windowed \
  --name "AgriBooks Print Terminal" \
  --icon "assets/printer.ico" \
  main.py
```

---

## Login Flow (First Launch)

```
1. App starts → check config.json
2. If token exists → validate via GET /api/print/terminal/session
   - If valid → skip login, connect WS
   - If 401 → show login window
3. Login window:
   a. Enter Server URL (default: https://agri-books.com)
   b. Enter email + password → POST /api/terminal/credential-pair
   c. If status="select_branch" → show branch dropdown → re-POST with branch_id
   d. On success → save config → connect WS → show tray icon
4. On logout:
   - Clear token from config (keep server_url + branch_name for convenience)
   - Disconnect WS
   - Show login window
```

---

## Testing Credentials (Development)

```
Server:   https://agri-books.com
Email:    janmarkeahig@gmail.com
Password: Aa@58798546521325
```

**Test branches:**
- Main Branch (id: 56f8368b-2059-40c3-8beb-769f14f2e43d)
- Branch 1 (id: c435277f-9fc7-4d83-83e7-38be5b4423ac)

---

## Sumatra PDF Download

Include SumatraPDF portable (no install needed):
→ https://www.sumatrapdfreader.org/dl/rel/3.5.2/SumatraPDF-3.5.2-64.exe

Run with `-install` flag or use the portable EXE directly.

---

## Key Notes

1. **`source_type` field** determines how to print: `"internal"` → HTML, `"external"` → file download
2. **File URLs are 24-hour presigned R2 URLs** — refresh via `/api/print/jobs/{id}/file-url` if expired
3. **Offline resilience**: On WS reconnect, poll `/api/print/jobs/pending` to catch missed jobs
4. **Print mode is set server-side** (admin can change it from the Print Center page) — EXE should respect `print_mode_changed` WS events
5. **Token is permanent** until manually logged out or terminal is purged (30 days inactive)
6. **Multi-tenant**: Organization scoping is automatic — the terminal only sees its own branch's print jobs
7. **Bundle SumatraPDF** inside the EXE for reliable silent PDF printing — no user installation required
