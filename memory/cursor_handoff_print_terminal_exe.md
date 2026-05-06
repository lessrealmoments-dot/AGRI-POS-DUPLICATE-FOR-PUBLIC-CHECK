# Cursor AI: AgriBooks Print Terminal — Windows EXE

## Project Overview

Build a **Windows Desktop EXE application** for the AgriBooks Remote Branch Printing Terminal system. This EXE acts as a dedicated print receiver for a specific branch — it authenticates against the AgriBooks backend, connects via WebSocket, receives print jobs in real-time, and prints them silently (or with a manual queue).

---

## Technology Stack

- **Language**: Python 3.10+
- **GUI Framework**: PyQt6 (or Tkinter for simpler alternative)
- **Packaging**: PyInstaller (single-file EXE with all dependencies)
- **HTTP Client**: `requests` (REST API calls)
- **WebSocket**: `websocket-client` library
- **Auto-start**: Windows Registry via `winreg`
- **System Tray**: PyQt6 `QSystemTrayIcon`
- **Printing**: `win32print` (pywin32) for silent printing, or `subprocess` calling `mspaint /p` for HTML
- **HTML Printing**: `pywin32` + Internet Explorer COM object for silent HTML print, OR `subprocess + Chrome/Edge --print-to-default-printer`

**Dependencies (requirements.txt)**:
```
requests>=2.31.0
websocket-client>=1.7.0
PyQt6>=6.6.0
pywin32>=306
pyinstaller>=6.0.0
```

---

## Backend API Contract

### Base URL
The EXE must have a configurable `SERVER_URL` setting (default: `https://agri-books.com`).
All API calls use: `{SERVER_URL}/api/...`

### Authentication Flow

**Step 1: Login**
```
POST {SERVER_URL}/api/terminal/credential-pair
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "userpassword"
}
```

**Response (when admin, no branch yet)**:
```json
{
  "status": "select_branch",
  "branches": [
    {"id": "branch_uuid", "name": "Main Branch"},
    {"id": "branch_uuid_2", "name": "Branch 1"}
  ],
  "user_name": "Admin Name",
  "is_admin": true
}
```

**Step 2: Login with branch selected (admin)**:
```
POST {SERVER_URL}/api/terminal/credential-pair
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "userpassword",
  "branch_id": "branch_uuid"
}
```

**Response (paired successfully)**:
```json
{
  "status": "paired",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "terminal_id": "uuid-of-this-terminal",
  "branch_id": "branch_uuid",
  "branch_name": "Main Branch",
  "user_name": "Admin Name",
  "organization_id": "org_uuid",
  "is_admin": true
}
```

**Store credentials persistently** (config file or Windows Credential Store):
```json
{
  "token": "...",
  "terminal_id": "...",
  "branch_id": "...",
  "branch_name": "...",
  "user_name": "...",
  "organization_id": "...",
  "server_url": "https://agri-books.com"
}
```

---

### WebSocket Connection (Real-time Print Jobs)

```
WS: {SERVER_URL_WS}/api/terminal/ws/terminal/{terminal_id}

Where SERVER_URL_WS = SERVER_URL with http → ws (or https → wss)
Example: wss://agri-books.com/api/terminal/ws/terminal/uuid-of-this-terminal
```

**Messages received from server**:

```json
// Print job delivered
{
  "type": "print_job",
  "data": {
    "job_id": "uuid",
    "document_type": "sales_receipt",
    "document_name": "Sales Receipt #INV-2025-001",
    "document_id": "invoice_uuid",
    "reference_number": "INV-2025-001",
    "html_content": "<!DOCTYPE html><html>...",
    "metadata": {},
    "priority": "normal",
    "created_at": "2025-01-15T10:30:00Z"
  }
}

// Print mode changed remotely
{
  "type": "print_mode_changed",
  "data": {"mode": "auto"}
}
```

**Auto-reconnect**: Reconnect after 3 seconds on disconnect. On reconnect, also call the polling endpoint to catch any missed jobs.

---

### Polling Endpoint (Fallback / On Reconnect)

```
GET {SERVER_URL}/api/print/jobs/pending
Authorization: Bearer {token}
```

**Response**:
```json
{
  "jobs": [
    {
      "id": "job_uuid",
      "document_type": "sales_receipt",
      "document_name": "Sales Receipt #INV-001",
      "html_content": "<!DOCTYPE html>...",
      "status": "sent",
      "created_at": "2025-01-15T10:30:00Z"
    }
  ],
  "terminal_id": "uuid",
  "print_mode": "manual",
  "branch_id": "uuid",
  "branch_name": "Main Branch"
}
```

Call this endpoint:
- On app startup after WebSocket connects
- After every WebSocket reconnect
- Optionally every 60 seconds as background fallback

---

### Update Job Status (After Printing)

```
PUT {SERVER_URL}/api/print/jobs/{job_id}/status
Authorization: Bearer {token}
Content-Type: application/json

{"status": "printed"}    // or "failed" or "cancelled"
// For failed: {"status": "failed", "error_message": "Printer not found"}
```

---

### Token Refresh (Keep session alive)

```
POST {SERVER_URL}/api/terminal/refresh-token
Authorization: Bearer {token}
```

Call every 12 hours. If 401 returned, session expired — prompt re-login.

---

### Set Print Mode

```
POST {SERVER_URL}/api/print/terminal/set-mode
Authorization: Bearer {token}
Content-Type: application/json

{"terminal_id": "{terminal_id}", "mode": "auto"}
```

---

## Application Architecture

### Main Window (System Tray App)

```
PrintTerminalApp
├── LoginWindow         # Shown on first launch or after logout
│   ├── ServerURL field (pre-filled with agri-books.com)
│   ├── Email field
│   ├── Password field
│   ├── Branch picker (shown after admin login if multiple branches)
│   └── Connect button
│
├── SystemTrayIcon      # Shows after successful login
│   ├── Green dot = Online/Connected
│   ├── Yellow dot = Connecting/Reconnecting
│   ├── Red dot = Offline/Error
│   └── Context menu:
│       ├── "AgriBooks Print Terminal"
│       ├── "Branch: {branch_name}"
│       ├── "Status: Connected / Offline"
│       ├── "--- Print Mode ---"
│       ├── "Auto Print" (checkable radio)
│       ├── "Manual Queue" (checkable radio)
│       ├── separator
│       ├── "Print Queue ({n} pending)"
│       ├── "Settings"
│       ├── "Logout"
│       └── "Exit"
│
├── PrintQueueWindow    # Shows when jobs arrive in manual mode
│   ├── List of pending jobs
│   ├── "Print Now" button per job
│   ├── "Cancel" button per job
│   └── "Print All" button
│
└── SettingsWindow
    ├── Server URL
    ├── Auto-start with Windows toggle
    ├── Print Mode (Auto / Manual)
    ├── Default printer selection
    └── About / Version info
```

---

## Print Modes

### Auto Mode
When `print_mode == "auto"`:
1. Job arrives via WebSocket
2. Extract `html_content` from job
3. Call `print_html_silent(html_content)` immediately
4. Call `PUT /api/print/jobs/{job_id}/status` with `{"status": "printed"}`
5. Show brief toast notification: "Printed: {document_name}"

### Manual Mode
When `print_mode == "manual"`:
1. Job arrives via WebSocket
2. Add to `PrintQueueWindow` list
3. Show system tray notification: "New print job: {document_name}"
4. Staff clicks "Print Now"
5. Call `print_html_silent(html_content)`
6. Call `PUT /api/print/jobs/{job_id}/status` with `{"status": "printed"}`
7. Remove from queue

---

## HTML Printing Implementation

**Option A (Recommended): Edge/Chrome silent print**
```python
import subprocess
import tempfile
import os

def print_html_silent(html_content: str) -> bool:
    """Print HTML silently using Edge or Chrome headless."""
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html_content)
        tmp_path = f.name
    
    try:
        # Try Microsoft Edge first
        edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        
        browser = edge_path if os.path.exists(edge_path) else chrome_path
        
        subprocess.run([
            browser,
            '--headless',
            '--disable-gpu',
            '--print-to-default-printer',
            f'--print-to-pdf-no-header',
            tmp_path
        ], timeout=30, check=True)
        return True
    except Exception as e:
        print(f"Print failed: {e}")
        return False
    finally:
        os.unlink(tmp_path)
```

**Option B: Windows print dialog (fallback)**
```python
import os
import subprocess

def print_html_with_dialog(html_content: str):
    """Open HTML in default browser and trigger print dialog."""
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html_content)
        tmp_path = f.name
    os.startfile(tmp_path, 'print')
```

---

## Windows Auto-Start

```python
import winreg
import sys

APP_NAME = "AgriBooks Print Terminal"
APP_PATH = sys.executable  # Path to the EXE

def enable_autostart():
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE
    )
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, APP_PATH)
    winreg.CloseKey(key)

def disable_autostart():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass
```

---

## Config Storage

Store in `%APPDATA%/AgriBooks/print_terminal_config.json`:

```python
import os
import json

CONFIG_DIR = os.path.join(os.environ['APPDATA'], 'AgriBooks')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'print_terminal_config.json')

def save_config(data: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {}
```

---

## PyInstaller Build Command

```bash
pyinstaller \
  --onefile \
  --windowed \
  --name "AgriBooks Print Terminal" \
  --icon "assets/printer.ico" \
  --add-data "assets;assets" \
  --hidden-import "websocket" \
  --hidden-import "win32print" \
  --hidden-import "win32api" \
  main.py
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Wrong credentials | Show error in login window, clear password |
| Token expired (401) | Auto-logout, show login window |
| Server unreachable | Yellow tray icon, retry every 30s, queue jobs locally |
| WebSocket disconnect | Yellow tray, auto-reconnect after 3s, poll on reconnect |
| Printer not found | Mark job as failed with error, show tray notification |
| Popup print blocked | Fall back to print dialog |
| Terminal purged (30 days) | 404 on token refresh → auto-logout with message |

---

## UI Requirements

- Minimal, clean Windows-native look (no web UI inside)
- Start minimized to system tray (no taskbar entry by default)
- Show print notification with document name when job arrives
- Print Queue window opens on double-click of tray icon (manual mode)
- Login window centered on screen with AgriBooks branding
- Status indicator in tray: green (connected), yellow (reconnecting), red (error/offline)

---

## Key Notes for Cursor

1. **The EXE reuses the existing AgriBooks terminal session system** — no new auth protocol needed
2. **Print jobs stay pending on server** until the terminal comes online and connects
3. **WebSocket auto-reconnect** is critical — power outages are common
4. **`credential-pair` is the login endpoint** — also used by the web terminal pages
5. **Token refresh every 12h** keeps the session alive indefinitely
6. **`organization_id` filtering** is handled server-side — the terminal only sees its own branch's jobs
7. **Print mode can be changed remotely** from the AgriBooks web admin (Print Center page)
8. **The EXE should feel like a native Windows app** — system tray, minimal UI, auto-start

---

## Testing Credentials (Development)

```
Server: https://agri-books.com (or your dev URL)
Email: janmarkeahig@gmail.com
Password: Aa@58798546521325
```

---

## Build & Distribution

1. Build with PyInstaller as single `.exe`
2. Users run the installer (or just the EXE)
3. EXE auto-registers to start with Windows on first launch
4. Staff enters server URL + credentials on first launch
5. Done — prints run silently in background forever
