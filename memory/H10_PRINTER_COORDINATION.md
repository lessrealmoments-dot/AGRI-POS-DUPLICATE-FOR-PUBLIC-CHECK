# H10 Printer Integration — Web AI <> Android AI Coordination

## Current State (as of 2026-04-05)

### What the Web AI (Emergent) owns:
- **PrintBridge.js** — Routes print calls: `Capacitor.isNativePlatform()` → native path, else browser popup
- **H10PPrinterPlugin.js** — Capacitor JS interface, registered via `registerPlugin('H10PPrinter', { web: fallback })`
- **PrintEngine.js** — Generates receipt HTML (thermal 58mm + full page) from sale/PO/transfer data
- **TerminalSales.jsx / TerminalShell.jsx / DocViewerPage.jsx** — All print button call sites

### What the Android AI (Cursor) owns:
- **H10PPrinterPlugin.java** — Native Capacitor plugin: HTML → Bitmap → PrinterService SDK
- **PrinterInterface.aidl** — AIDL binding to `recieptservice.com.recieptservice.service.PrinterService`
- **printer-release.aar** — Senraise SDK (present locally, gitignored)
- **MainActivity.java** — Plugin registration
- **APK build** — Android Studio / Gradle

---

## Changes Applied (Both Sides) — UNCOMMITTED

### Web side (applied by Emergent):
1. **All 5 print call sites** now `await PrintBridge.print(...)` inside try/catch
2. **Errors show toast**: `toast.error('Print failed: ' + err.message)`
3. Files changed: `TerminalSales.jsx`, `TerminalShell.jsx`, `DocViewerPage.jsx`

### Android side (applied by Cursor):
1. **`printHtml()`** — Polls up to 12s for `printerConnected` before printing (background thread)
2. **`checkStatus()`** — Waits up to 4s before responding
3. **`bindPrinterService()`** — Checks `bindService()` return value, logs with tag `H10PPrinter`
4. File changed: `H10PPrinterPlugin.java`

---

## The Print Flow (after both fixes)

```
User taps "Print Receipt (58mm)" in TerminalSales.jsx
  |
  v
await PrintBridge.print({ type, data, format: 'thermal', businessInfo })
  |
  v  (Capacitor.isNativePlatform() === true inside APK)
PrintEngine.generateHtml({ ... }) → full HTML string
  |
  v
H10PPrinter.printHtml({ html, format: 'thermal' })
  |  (Capacitor bridge → native Java)
  v
H10PPrinterPlugin.java :: printHtml()
  |
  |  [1] Check printerConnected — if false, call bindPrinterService()
  |  [2] Poll every 300ms, up to 12 seconds, for onServiceConnected
  |  [3] If still not connected → call.reject("Printer service not connected after 12s")
  |      → JS catches → toast.error("Print failed: ...")
  |
  |  [4] If connected:
  v
renderHtmlToBitmap(html, 384px) via headless WebView
  |
  v
printer.beginWork()
printer.printBitmap(bitmap)
printer.nextLine(3)
printer.endWork()
  |
  v
call.resolve({ success: true }) → JS promise resolves → no toast (success)
```

---

## What Needs To Happen Next

### Step 1: Commit + Push from Emergent
- User clicks "Save to GitHub" in Emergent chat
- This pushes: updated JS files (await/toast) + updated Java file (wait-for-bind + logging)
- **Note**: `printer-release.aar` is gitignored — won't be in the push (already on local machine)

### Step 2: Pull on Local Machine
```bash
cd /path/to/project
git pull origin main
```
- If Cursor already made the same Java changes locally, there may be merge conflicts in `H10PPrinterPlugin.java` — resolve by keeping the version with wait-for-bind (both sides wrote the same fix)

### Step 3: Rebuild APK
```bash
cd frontend/android
./gradlew assembleDebug
# APK at: app/build/outputs/apk/debug/app-debug.apk
```
Or use Android Studio: Build → Build APK

### Step 4: Install on H10
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
Or sideload via USB/file manager

### Step 5: Deploy Web to agri-books.com
- The APK uses live URL mode (`capacitor.config.ts` → `server.url: 'https://agri-books.com'`)
- The `await` + toast changes in JS only take effect when the live site is updated
- Until deployed: print errors will be silent (old JS), but the native wait-for-bind fix still works

---

## Debugging Checklist (if print still fails after rebuild)

1. **Is the recieptservice app installed?**
   ```bash
   adb shell pm list packages | findstr reciept
   ```
   Should show: `package:recieptservice.com.recieptservice`

2. **Check printer logs:**
   ```bash
   adb logcat | findstr H10PPrinter
   ```
   Expected: `bindService returned: true` → then print succeeds
   Problem: `bindService returned: false` → recieptservice APK missing

3. **Is the APK the latest build?**
   ```bash
   adb shell dumpsys package com.agribooks.terminal | findstr versionName
   ```

4. **Is the web content current?**
   - Open Chrome on H10 → navigate to agri-books.com → check if toast errors appear on print failure
   - If no toasts → web deploy hasn't happened yet (but native fix still works)

---

## Important Notes for Android AI

- **Do NOT modify PrintBridge.js, PrintEngine.js, or H10PPrinterPlugin.js** — these are managed by the Web AI
- **Do NOT change the Capacitor plugin name** — it must stay `H10PPrinter` (matches JS registration)
- **The HTML received by `printHtml()`** is a complete `<!DOCTYPE html>` document with inline CSS, no external stylesheets. The only external resource is the QR code image from `api.qrserver.com` (loads over H10's 4G)
- **Bitmap width**: 384px for thermal (58mm @ 203 DPI), 576px for full page
- **The `format` parameter** comes from JS as either `"thermal"` or `"full_page"`
