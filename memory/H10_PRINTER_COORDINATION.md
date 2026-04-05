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
| **PrintEngine.js** `thermalCSS` | `width: 384px`, `padding: 4px 10px`, all font sizes increased ~40% (body 14px, biz-name 18px, grand total 18px) |
| **PrintEngine.js** `orderSlipThermal` | Removed verbose acknowledgment + signature block → shorter, cleaner POS receipt |
| **PrintEngine.js** `trustReceiptThermal` | Reduced signature spacer from 20px to 8px |
| **PrintBridge.js** | Replaced `inlineExternalImages` (external fetch) with `replaceQrWithLocalDataUrl` which uses the `qrcode` npm package to generate QR data URLs entirely client-side — no network dependency |
| **TerminalSales.jsx** `handleShowReceiptPreview` | Closes "Sale Complete" dialog BEFORE opening "Receipt Preview" dialog to prevent Android WebView dialog stacking |

---

## IMPORTANT — Fix needed in H10PPrinterPlugin.java (Android AI)

### Problem: "Ultra long rolling paper" — prints excessive blank space

**Root cause**: `view.getContentHeight()` can return 0 for headless (off-screen) WebViews. When it does, the Java fallback is `contentHeight = 2000`, which creates a 2000px tall bitmap with ~1600px of blank white space printed after the receipt content.

**Recommended Java fix** (in `renderHtmlToBitmap`):

1. Enable JavaScript on the headless WebView (needed for height evaluation):
```java
settings.setJavaScriptEnabled(true);
```

2. After `onPageFinished`, use `evaluateJavascript` to measure the TRUE scroll height:
```java
view.post(() -> {
    view.evaluateJavascript(
        "(function(){ return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight); })()",
        value -> {
            int contentHeight;
            try {
                contentHeight = Integer.parseInt(value.trim());
            } catch (Exception e) {
                contentHeight = view.getContentHeight();
            }
            if (contentHeight <= 0 || contentHeight > 4000) contentHeight = 1000;
            // continue with measure/layout/bitmap as before ...
        }
    );
});
```

3. If enabling JS is not desired, change the fallback from `2000` to `1000` as a minimum fix:
```java
if (contentHeight <= 0) contentHeight = 1000; // was 2000
```

---

## The Print Flow (current)

```
User taps "Print Receipt (58mm)"
  |
  v  (closes "Sale Complete" dialog, opens "Receipt Preview" dialog)
handleShowReceiptPreview('thermal')
  |
  v
User sees receipt preview in iframe (384px wide)
  |
  v
User taps "Print 1 Copy" or "Print 2 Copies"
  |
  v
await PrintBridge.print({ type, data, format: 'thermal', businessInfo })
  |
  v  (Capacitor.isNativePlatform() === true inside APK)
PrintEngine.generateHtml({ ... }) → HTML string (384px wide, inline CSS, QR src = api.qrserver.com)
  |
  v
replaceQrWithLocalDataUrl(html)
  → import('qrcode').then(QRCode => QRCode.toDataURL(qrData, { width: 100 }))
  → replace api.qrserver.com src with base64 PNG data URL
  → no external network request needed!
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

## Receipt Preview Feature (2026-04-05)

After a sale completes, the "Print Receipt (58mm)" button now opens a **receipt preview** instead of printing immediately. The preview shows the exact receipt content in an iframe (384px wide, scaled to fit the modal). The user can then choose:

- **Print 1 Copy** → single `PrintBridge.print()` call
- **Print 2 Copies** → two `PrintBridge.print()` calls with a 2-second gap between them
- **Skip** → close everything, no print

The "Print Full Page" button still prints directly (no preview) since full-page is for admin/desktop use.

**Android AI note**: No native changes needed for this feature. The JS generates the preview HTML client-side. The actual print flow is identical — `PrintBridge.print()` → `inlineExternalImages()` → `H10PPrinter.printHtml()`.

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
