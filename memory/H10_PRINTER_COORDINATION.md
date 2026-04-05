# H10 Printer Integration — Web <> Android Coordination

## Current State (as of 2026-04-05, updated after first print test)

### What the Web AI (Emergent) owns:
- **PrintBridge.js** — Routes print calls: native path with **QR image inlining** (fetch → base64 data URL before sending to native), else browser popup
- **H10PPrinterPlugin.js** — Capacitor JS interface, registered via `registerPlugin('H10PPrinter', { web: fallback })`
- **PrintEngine.js** — Generates receipt HTML. **Thermal CSS optimized for 384px bitmap** (H10P physical width)
- **TerminalSales.jsx / TerminalShell.jsx / DocViewerPage.jsx** — All print button call sites (await + toast)

### What the Android AI (Cursor) owns:
- **H10PPrinterPlugin.java** — Native Capacitor plugin: HTML → Bitmap → PrinterService SDK
- **PrinterInterface.aidl** — AIDL binding to `recieptservice.com.recieptservice.service.PrinterService`
- **printer-release.aar** — Senraise SDK (present locally, gitignored)
- **MainActivity.java** — Plugin registration
- **APK build** — Android Studio / Gradle

---

## Issues Fixed (2026-04-05 — second round)

### Problem: Receipt too long, not centered, fonts too small, QR not printing

**Root causes:**
1. `thermalCSS` used `width: 58mm` which WebView interprets at screen DPI, not printer DPI → content too narrow
2. Font sizes (7-10px) designed for screen were tiny when rendered to 384px bitmap and printed on paper
3. Excessive padding/margins in acknowledgment and signature sections
4. QR code from `api.qrserver.com` loaded as external URL — headless WebView often can't fetch it in time before bitmap capture

**Fixes applied (Web side — Emergent):**

| File | Change |
|------|--------|
| **PrintEngine.js** `thermalCSS` | `width: 384px` (matches bitmap exactly), `padding: 4px 12px` (safe area for H10 margins), all font sizes increased ~30-40% |
| **PrintEngine.js** thermal builders | Compact acknowledgment section, reduced signature line spacing |
| **PrintEngine.js** `qrImgTag` | Reduced default size from 120px to 100px, reduced margin from 4 to 2 |
| **PrintBridge.js** | Added `inlineExternalImages()` — fetches all `<img src="https://...">` and converts to base64 data URLs before sending HTML to native plugin |

### Font size mapping (384px bitmap → 58mm paper):

| Element | Old | New | ~Paper size |
|---------|-----|-----|-------------|
| Body text | 10px | 13px | ~2mm |
| Business name | 12px | 16px | ~2.5mm |
| Doc title | 11px | 15px | ~2.3mm |
| Meta rows | 9px | 12px | ~1.8mm |
| Items table | 9px | 12px | ~1.8mm |
| Grand total | 12px | 16px | ~2.5mm |
| Footer/disclaimer | 7px | 9px | ~1.4mm |

---

## The Print Flow (current)

```
User taps "Print Receipt (58mm)"
  |
  v
await PrintBridge.print({ type, data, format: 'thermal', businessInfo })
  |
  v  (Capacitor.isNativePlatform() === true inside APK)
PrintEngine.generateHtml({ ... }) → HTML string (384px wide, inline CSS)
  |
  v
inlineExternalImages(html) → fetch QR image → replace src with base64 data URL
  |
  v
H10PPrinter.printHtml({ html, format: 'thermal' })
  |  (Capacitor bridge → native Java)
  v
H10PPrinterPlugin.java :: printHtml()
  → waitForPrinterConnection(12000) on background thread
  → renderHtmlToBitmap(html, 384px) via headless WebView
  → printer.beginWork() → printer.printBitmap(bitmap) → printer.endWork()
  → call.resolve({ success: true })
```

---

## Important Notes for Android AI

- **Do NOT modify PrintBridge.js, PrintEngine.js, or H10PPrinterPlugin.js** — these are managed by the Web AI
- **Do NOT change the Capacitor plugin name** — it must stay `H10PPrinter` (matches JS registration)
- **The HTML received by `printHtml()`** is a complete `<!DOCTYPE html>` document with:
  - Inline CSS (no external stylesheets)
  - QR images already converted to base64 data URLs (no external fetch needed)
  - Body width set to 384px (matches thermal bitmap width exactly)
- **Bitmap width**: 384px for thermal (58mm @ 203 DPI), 576px for full page
- **The `format` parameter** comes from JS as either `"thermal"` or `"full_page"`
