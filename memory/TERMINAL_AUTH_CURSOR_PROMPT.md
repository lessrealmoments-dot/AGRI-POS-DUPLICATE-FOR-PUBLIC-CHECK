# AgriSmart Terminal — Device Authentication Hardening
## Web (Emergent) + Android (Cursor) Coordination Doc
### Created: 2026-04-06

---

## 1. PURPOSE

Strengthen the AgriSmart Terminal authentication so a terminal session cannot be hijacked by copying `localStorage` data to a different device. The fix binds each terminal session to the **physical Android device** that performed the pairing.

This document is the **Cursor AI build prompt** for the Android/Capacitor side. The backend changes are also described and must be made in parallel (or coordinated with the Emergent web agent).

---

## 2. OWNERSHIP MAP

| Component | Owner | Notes |
|---|---|---|
| `TerminalPairScreen.jsx` | Emergent (Web AI) | Pairing UI — sends `device_id` during pairing |
| `TerminalShell.jsx` | Emergent (Web AI) | Stores `device_id` in session, passes to action calls |
| `DocViewerPage.jsx` | Emergent (Web AI) | QR action panels — send `device_id` with every action |
| `DeviceIdentityPlugin.java` | Cursor (Android AI) | **NEW** — native plugin to expose Android device ID |
| `MainActivity.java` | Cursor (Android AI) | Register the new plugin |
| `terminal.py` (backend) | Emergent (Web AI) | Stores `device_id` on session; verifies on every action |
| `qr_actions.py` (backend) | Emergent (Web AI) | `_verify_terminal_session()` enforces device binding |

---

## 3. CURRENT AUTH ARCHITECTURE (what exists today)

### Pairing flow
```
Terminal device opens /terminal in Capacitor WebView
  → User enters 6-char code (or scans QR)
  → POST /api/terminal/pair-code  { code, branch_id }   (authenticated with admin JWT)
  → Backend creates terminal_sessions record:
      { terminal_id, branch_id, branch_name, token, status: "active" }
  → Response: { terminal_id, token, branch_id, branch_name, ... }
  → Frontend stores in localStorage["agrismart_terminal"]:
      { terminalId, token, branchId, branchName, organizationId }
```

### QR action flow (stock release / receive payment / transfer receive)
```
User scans QR code → DocViewerPage → action panel
  → POST /api/qr-actions/{code}/receive_payment
      Body: { terminal_id, pin, amount, method, ... }
  → Backend: _verify_terminal_session(terminal_id)
      → Looks up terminal_sessions where terminal_id + status=active
      → If found: OK
      → If not: 403
  → If OK: verify PIN → process action
```

### Security gap
```
terminal_sessions only verifies terminal_id exists and is active.
It does NOT verify WHICH DEVICE is making the call.

If an attacker copies { terminal_id, token } from localStorage
to any browser on any device, they can call all QR action endpoints.

terminal_id is a UUID — unguessable by random, but if extracted
from a rooted device, stolen via ADB, or man-in-the-middled,
it can be replayed from a different device.
```

---

## 4. THE FIX — DEVICE BINDING

### Strategy: Android ID Binding

During pairing, the Capacitor app **reads the Android device's permanent ID** and sends it to the backend. The backend **stores it on the terminal session**. On every subsequent QR action call, the device sends its ID again. The backend **rejects calls where the device ID does not match**.

**Device identifier to use:**
`android.provider.Settings.Secure.ANDROID_ID` — a 64-bit hex string unique to each Android device + user combination. Survives app reinstall but changes on factory reset. Available since API 3. Not user-visible, not modifiable without root.

**Why this works:**
- Copying localStorage to Chrome on a laptop → ANDROID_ID call fails (not in a real Capacitor app) → Backend rejects
- Copying to another Android device → Different ANDROID_ID → Backend rejects
- Attack requires physical access to the specific paired device

---

## 5. ANDROID CHANGES (Cursor builds these)

### 5a. New file: `DeviceIdentityPlugin.java`

**Location:** `frontend/android/app/src/main/java/com/agribooks/terminal/DeviceIdentityPlugin.java`

```java
package com.agribooks.terminal;

import android.provider.Settings;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "DeviceIdentity")
public class DeviceIdentityPlugin extends Plugin {

    /**
     * Returns the device's permanent Android ID.
     * This is a 64-bit hex string unique to each device + user account.
     * Used to bind terminal sessions to a specific physical device.
     *
     * Call from JavaScript:
     *   import { DeviceIdentity } from '../plugins/DeviceIdentityPlugin';
     *   const { deviceId } = await DeviceIdentity.getDeviceId();
     */
    @PluginMethod
    public void getDeviceId(PluginCall call) {
        String androidId = Settings.Secure.getString(
            getContext().getContentResolver(),
            Settings.Secure.ANDROID_ID
        );
        JSObject result = new JSObject();
        result.put("deviceId", androidId != null ? androidId : "unknown");
        call.resolve(result);
    }
}
```

### 5b. Register in `MainActivity.java`

**Location:** `frontend/android/app/src/main/java/com/agribooks/terminal/MainActivity.java`

Add the import and register the plugin in `onCreate`:

```java
import com.agribooks.terminal.DeviceIdentityPlugin;
// ... other imports

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Register custom plugins BEFORE super.onCreate()
        registerPlugin(H10PPrinterPlugin.class);
        registerPlugin(DeviceIdentityPlugin.class);   // <-- ADD THIS LINE
        super.onCreate(savedInstanceState);
    }
}
```

**No AndroidManifest.xml changes needed.** `READ_PHONE_STATE` permission is NOT required to access `ANDROID_ID`.

---

## 6. WEB APP CHANGES (Emergent implements these)

### 6a. New JS plugin bridge file

**Location:** `frontend/src/plugins/DeviceIdentityPlugin.js`

```js
import { registerPlugin } from '@capacitor/core';

const DeviceIdentity = registerPlugin('DeviceIdentity', {
  // Browser fallback — returns a stable fake ID based on localStorage
  // This fallback only activates in browser dev mode, never on a real device
  web: {
    async getDeviceId() {
      let devId = localStorage.getItem('_dev_device_id');
      if (!devId) {
        devId = 'browser-' + Math.random().toString(36).substring(2, 18);
        localStorage.setItem('_dev_device_id', devId);
      }
      return { deviceId: devId };
    }
  }
});

export { DeviceIdentity };
```

### 6b. `TerminalPairScreen.jsx` — send `device_id` during pairing

In both pairing flows (code-based and QR-based), read the device ID and include it in the request body.

**Add at the top of the component (after imports):**
```js
import { DeviceIdentity } from '../../plugins/DeviceIdentityPlugin';
```

**Before both `handleCredentialLogin()` and `handleQrPair()` submission, get the device ID:**
```js
// Get device ID (returns ANDROID_ID on device, stable fake on browser)
const { deviceId } = await DeviceIdentity.getDeviceId();
```

**Add `device_id: deviceId` to the request body for:**
- `POST /api/terminal/pair-code` → `{ code, branch_id, device_id: deviceId }`
- `POST /api/terminal/qr-pair` → `{ token, device_id: deviceId }`

**After successful pairing, store `deviceId` in the session object:**
```js
// When calling onPaired(data), include device_id:
onPaired({ ...data, deviceId });
```

### 6c. `TerminalPage.jsx` — persist `device_id` in localStorage

The `handlePaired(data)` function already saves to `localStorage["agrismart_terminal"]`.
Ensure the `deviceId` field is included when `TerminalPairScreen` calls `onPaired(data)`.
No additional changes needed — the `handlePaired` function spreads the object as-is.

### 6d. `TerminalShell.jsx` — include `device_id` in all QR action requests

The `session` object in TerminalShell already includes all pairing data.
When navigating to `/doc/{code}?branch={branchId}`, also pass `deviceId` as a search param or via React Router state:

```js
navigate(`/doc/${code}?branch=${session.branchId}&device=${session.deviceId || ''}`);
```

### 6e. `DocViewerPage.jsx` — send `device_id` with every terminal action

`DocViewerPage` already reads `?branch=` from searchParams. Add `device=` reading:

```js
const terminalDeviceId = searchParams.get('device') || terminalSession?.deviceId || '';
```

Pass `terminalDeviceId` to all three action panels:
- `StockReleaseManager` → add `deviceId={terminalDeviceId}` prop
- `ReceivePaymentPanel` → add `deviceId={terminalDeviceId}` prop
- `TransferReceivePanel` → add `deviceId={terminalDeviceId}` prop

In each panel's API submission, add `device_id: deviceId` to the request body alongside `terminal_id`.

---

## 7. BACKEND CHANGES (Emergent implements these)

### 7a. `routes/terminal.py` — store `device_id` on session creation

In both `pair_code()` and `qr_pair_terminal()`:

```python
# Add to request body extraction:
device_id = (data.get("device_id") or "").strip()

# Add to session document:
session = {
    ...existing fields...,
    "device_id": device_id,   # <-- ADD THIS
}
```

### 7b. `routes/qr_actions.py` — verify `device_id` in `_verify_terminal_session()`

```python
async def _verify_terminal_session(terminal_id: str, device_id: str = ""):
    """Verify terminal session exists AND that the device_id matches the paired device."""
    if not terminal_id:
        raise HTTPException(403, "Actions require an AgriSmart Terminal.")
    
    from config import _raw_db
    session = await _raw_db.terminal_sessions.find_one(
        {"terminal_id": terminal_id, "status": "active"}, {"_id": 0}
    )
    if not session:
        raise HTTPException(403, "Invalid or expired terminal session. Please re-pair.")
    
    # Device binding check — only enforce if both the session and the caller have a device_id
    stored_device_id = session.get("device_id", "")
    if stored_device_id and device_id and stored_device_id != device_id:
        raise HTTPException(403, {
            "message": "Device mismatch. This terminal session was paired on a different device. Please re-pair.",
            "code": "DEVICE_MISMATCH"
        })
```

Then in all three action endpoints, pass `device_id` to the call:

```python
# release_stocks:
await _verify_terminal_session(data.get("terminal_id", ""), data.get("device_id", ""))

# receive_payment (Path 1 only):
await _verify_terminal_session(terminal_id, data.get("device_id", ""))

# transfer_receive:
await _verify_terminal_session(data.get("terminal_id", ""), data.get("device_id", ""))
```

### 7c. Graceful rollback for existing sessions

Sessions created before this update have no `device_id`. The check `if stored_device_id and device_id` handles this:
- Old session (no stored device_id) + any caller → **passes** (backward compatible)
- New session (has device_id) + same device → **passes**
- New session (has device_id) + different device → **blocked**

This means: existing paired terminals continue to work after update. Only NEW pairings (after the update is deployed) gain device binding. Old sessions can be force-expired to require re-pairing for full security.

### 7d. Optional: Force-expire old sessions after deploy

```python
# Admin endpoint to force re-pairing of old unbound sessions:
@router.post("/admin/expire-unbound-sessions")
async def expire_unbound_sessions(user=Depends(get_current_user)):
    """Expire all terminal sessions that were created without device binding."""
    result = await _raw_db.terminal_sessions.update_many(
        {"status": "active", "device_id": {"$exists": False}},
        {"$set": {"status": "expired", "expired_at": now_iso(), "expire_reason": "device_binding_upgrade"}}
    )
    return {"expired": result.modified_count}
```

---

## 8. UPDATED DATA FLOW AFTER IMPLEMENTATION

```
PAIRING (new):
  Capacitor app  →  DeviceIdentityPlugin.getDeviceId()  →  ANDROID_ID
  Terminal screen →  POST /api/terminal/pair-code
                        { code, branch_id, device_id: "a1b2c3d4..." }
  Backend         →  terminal_sessions: { ..., device_id: "a1b2c3d4..." }
  localStorage    →  { terminalId, token, branchId, deviceId: "a1b2c3d4..." }

QR ACTION (new):
  DocViewerPage   →  reads session.deviceId from localStorage
  Action panel    →  POST /api/qr-actions/{code}/receive_payment
                        { terminal_id, pin, amount, device_id: "a1b2c3d4...", ... }
  Backend         →  _verify_terminal_session(terminal_id, device_id)
                        → checks terminal_sessions.device_id == "a1b2c3d4..."
                        → MATCH → proceed
                        → MISMATCH → 403 DEVICE_MISMATCH
```

---

## 9. ATTACK SCENARIO ANALYSIS AFTER FIX

| Attack | Before fix | After fix |
|---|---|---|
| Copy localStorage to browser on PC | ✅ Works (full access) | ❌ Blocked — browser has no ANDROID_ID, sends browser-XXXX which won't match |
| Copy localStorage to another Android device | ✅ Works | ❌ Blocked — different ANDROID_ID |
| Copy to same rooted device, different user | ✅ Works | ❌ Blocked — ANDROID_ID changes per user account |
| Physical theft of the actual terminal device | ✅ Works | ✅ Works (intended — device IS paired) |
| Token expired, re-pair on different device | N/A | ❌ Blocked — new pairing required |

---

## 10. FILES CHANGED SUMMARY

### Cursor (Android) — ✅ DONE
| File | Change |
|---|---|
| `frontend/android/app/src/main/java/com/agribooks/terminal/DeviceIdentityPlugin.java` | **NEW** — exposes `getDeviceId()` |
| `frontend/android/app/src/main/java/com/agribooks/terminal/MainActivity.java` | Registers `DeviceIdentityPlugin` before `super.onCreate()` |

### Emergent (Web) — ✅ DONE
| File | Change |
|---|---|
| `frontend/src/plugins/DeviceIdentityPlugin.js` | **NEW** — Capacitor JS bridge + browser fallback |
| `frontend/src/pages/terminal/TerminalPairScreen.jsx` | Calls `getDeviceId()` in all 4 pairing flows (QR URL, WebSocket, polling, credential); sends `device_id` in backend requests; includes `deviceId` in `onPaired()` data |
| `frontend/src/pages/terminal/TerminalShell.jsx` | Calls `POST /api/terminal/bind-device` on init (handles code-pairing); passes `?device=` param in all `/doc/` navigations |
| `frontend/src/pages/DocViewerPage.jsx` | Reads `terminalDeviceId` from `?device=` param; passes `deviceId` prop to all 3 action panels |
| `frontend/src/pages/DocViewerPage.jsx` (StockReleaseManager) | Sends `device_id` in `release_stocks` API call |
| `frontend/src/pages/DocViewerPage.jsx` (ReceivePaymentPanel) | Sends `device_id` in `receive_payment` API call |
| `frontend/src/pages/DocViewerPage.jsx` (TransferReceivePanel) | Sends `device_id` in `transfer_receive` API call |
| `backend/routes/terminal.py` | Stores `device_id` in session on QR pair + credential pair; new `POST /api/terminal/bind-device` for code pairing |
| `backend/routes/qr_actions.py` | `_verify_terminal_session(terminal_id, device_id)` now verifies device binding; all 3 action endpoints pass `device_id` |

---

## 11. TESTING CHECKLIST

1. **Pair a new terminal** → verify `terminal_sessions` document has `device_id` field in MongoDB
2. **Perform QR action (stock release / receive payment) from that terminal** → success
3. **Copy `agrismart_terminal` localStorage to Chrome browser on PC** → `getDeviceId()` returns `browser-XXXX` → action call returns 403 DEVICE_MISMATCH
4. **Existing sessions (no device_id) continue to work** → backward compat check
5. **Re-pair after calling `/admin/expire-unbound-sessions`** → new session has device_id, old one is expired

---

## 12. NOTES FOR CURSOR AI

- Do NOT modify `PrintEngine.js`, `PrintBridge.js`, or any existing printer/scanner logic
- The `ANDROID_ID` is available without any Android permissions
- In the Capacitor plugin, the `@CapacitorPlugin(name = "DeviceIdentity")` name MUST match the string passed to `registerPlugin('DeviceIdentity', ...)` in the JS file
- The browser fallback in `DeviceIdentityPlugin.js` is intentional — it allows the web app to work in browser dev mode without Capacitor. The fallback ID will never match a real Android ID, so the backend check gracefully handles old (non-device-bound) sessions only
- After the Cursor build is done, the Emergent agent will implement the web + backend side changes
- This is a **backward-compatible rollout** — no terminal needs to be force-re-paired unless the admin explicitly calls the expire endpoint
