# H10 Hardware Integration — Master Reference
## Web (Emergent) + Android (Cursor) Coordination Doc
### Last updated: 2026-04-05 — Final stable state

---

## 1. OWNERSHIP MAP

| Component | Owner | Notes |
|-----------|-------|-------|
| `PrintEngine.js` | Emergent (Web AI) | Generates receipt HTML |
| `PrintBridge.js` | Emergent (Web AI) | Routes print to native or browser |
| `H10PPrinterPlugin.js` | Emergent (Web AI) | Capacitor JS-side plugin interface |
| `TerminalSales.jsx` | Emergent (Web AI) | Barcode scanner + print preview UI |
| `H10PPrinterPlugin.java` | Cursor (Android AI) | Native bitmap capture + SrPrinter SDK |
| `MainActivity.java` | Cursor (Android AI) | Plugin registration |
| APK build | Cursor (Android AI) | Android Studio / Gradle |

---

## 2. THERMAL PRINTER — COMPLETE IMPLEMENTATION

### 2a. Print Flow (end-to-end)

```
Sale completes
  → setLastSaleData({ ...saleData, invoice_number, ...res.data })
  → setShowPrintPrompt(true)  ← "Sale Complete" dialog opens

User taps "Print Receipt (58mm)"
  → handleShowReceiptPreview('thermal')
  → setShowPrintPrompt(false)  ← CLOSE first dialog (prevents WebView dialog stacking)
  → PrintEngine.generateHtml({ type, data, format: 'thermal', businessInfo, docCode })
  → setReceiptPreview({ html, type, format })  ← "Receipt Preview" dialog opens

User taps "Print 1 Copy" or "Print 2 Copies"
  → handlePrintCopies(1 or 2)
  → loop: await PrintBridge.print({ type, data, format, businessInfo, docCode })
  → 2 copies: 2-second gap between prints

PrintBridge.print() — Native path (Capacitor.isNativePlatform() === true):
  → PrintEngine.generateHtml() → HTML with api.qrserver.com QR URL
  → replaceQrWithLocalDataUrl(html)
       → QRCode.toDataURL(qrData, { width: 152, margin: 1, errorCorrectionLevel: 'M' })
       → replaces api.qrserver.com src with base64 PNG
       → NO network dependency, works offline
  → H10PPrinter.printHtml({ html, format, feedLinesAfter? })
  → Capacitor bridge → Java

H10PPrinterPlugin.java:
  → waitForSrPrinterReady(appCtx, 12000) on background thread
  → renderHtmlToBitmap(activity, html, 384px)
       → WebView attached to activity window (not headless — fixes layout)
       → Bitmap.Config.ARGB_8888 (NOT RGB_565)
       → captureDelay: 600ms normal, 2400ms if HTML has <img> tag
  → trimTrailingBlank(bitmap) — removes trailing white rows
  → SrPrinter.printBitmap(bitmap) (or printBitmapImmediately as fallback)
  → SrPrinter.nextLine(feedLinesAfter)  ← default 4 lines
```

### 2b. PrintEngine.js — Thermal CSS (FINAL STABLE STATE)

```css
/* CRITICAL: width:100% NOT width:384px */
/* On high-DPI Android, CSS viewport < 384px physical pixels.
   Fixed 384px body overflows viewport and gets CLIPPED in bitmap.
   width:100% fills whatever the WebView's CSS viewport is. */
html, body { width: 100%; max-width: 100%; overflow-x: hidden; }

body {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  font-weight: normal;  /* NOT bold — bold causes shadow artifacts on thermal */
  line-height: 1.35;
  padding: 2px 4px;
  color: #000;
  word-wrap: break-word;
  overflow-wrap: anywhere;
}

/* CRITICAL for print quality: disables font anti-aliasing */
/* Anti-aliased gray pixels binarize poorly on thermal paper → fuzzy text */
* {
  -webkit-font-smoothing: none;
  -moz-osx-font-smoothing: unset;
  text-rendering: optimizeSpeed;
}

/* ALL colors must be pure #000 — NO grays (#333, #444, #666) */
/* Gray pixels get dithered by printer SDK → blurry appearance */
```

### 2c. generateHtml() — Viewport Meta Tag

```javascript
// IN generateHtml(), for thermal format:
const viewportMeta = format === 'thermal'
  ? '<meta name="viewport" content="width=384, initial-scale=1.0, maximum-scale=1.0">'
  : '';
return `<!DOCTYPE html><html><head><meta charset="utf-8">${viewportMeta}...`;
```

**Why viewport meta:** Forces CSS viewport to exactly 384px regardless of device density.
Combined with `width: 100%`, the body fills the exact 384px bitmap width.

### 2d. QR Code — FINAL STABLE STATE

**Package:** `qrcode` v1.5.4 (installed via `yarn add qrcode`)

**In PrintBridge.js:**
```javascript
import QRCode from 'qrcode'; // STATIC import — dynamic import fails in Capacitor WebView

async function replaceQrWithLocalDataUrl(html) {
  // Finds api.qrserver.com URL, extracts 'data' param, generates locally
  const QRCode = ...; // already imported
  const dataUrl = await QRCode.toDataURL(qrData, {
    width: 152,   // 152px = 0.75 inch at 203 DPI (58mm printer)
    margin: 1,
    errorCorrectionLevel: 'M',
    color: { dark: '#000000', light: '#ffffff' },
  });
  return html.replace(qrUrl, dataUrl);
}
```

**In PrintEngine.js thermal functions:**
```javascript
if (docCode) html += qrImgTag(docCode, 152); // 0.75 inch
```

**Why local generation instead of fetch:**
- `api.qrserver.com` fetch fails silently in headless Android WebViews
- Local generation works offline, no network dependency
- Static import guarantees it's bundled (dynamic import can fail in Capacitor)

### 2e. PrintBridge.js — Additional Features

```javascript
// feedLinesAfter — controls how many blank lines after receipt
// pass in the print call:
PrintBridge.print({ ..., feedLinesAfter: 6 }); // more lines = more space before tear

// feedPaper — advance paper without printing
await PrintBridge.feedPaper(8); // feeds 8 blank lines
```

### 2f. Java Side — Key Decisions (Cursor owns this)

| Decision | Value | Why |
|----------|-------|-----|
| Bitmap config | `ARGB_8888` | Better quality than RGB_565, no 16-bit color artifacts |
| WebView attachment | Attached to activity window | Headless WebViews fail to compute `getContentHeight()` |
| Capture delay | 600ms (no img), 2400ms (has img) | img tag = QR present, needs extra load time |
| `trimTrailingBlank` | Yes | Removes trailing white rows, prevents excess paper feed |
| `contentHeight` fallback | 1200 (was 2000) | 2000px = blank paper waste |
| `SrPrinter` (not AIDL) | `SrPrinter.getInstance(ctx)` | Cursor replaced PrinterInterface.aidl with Senraise SrPrinter |

---

## 3. HID BARCODE SCANNER — COMPLETE IMPLEMENTATION

### 3a. How It Works

The H10's built-in laser scanner is an **HID keyboard emulator** — it fires keystrokes into whatever is focused, exactly like a keyboard. The implementation intercepts these at the window level BEFORE they reach any input field.

### 3b. Detection Strategy

**One single capture-phase global listener** (registered in TerminalSales.jsx):

```javascript
window.addEventListener('keydown', onKey, true); // true = CAPTURE PHASE
```

**Why capture phase matters:** Fires BEFORE the input field receives the event.
`e.preventDefault()` in capture phase blocks the char from being inserted into any input.

### 3c. Scanner Constants

```javascript
const SCAN_CHAR_SPEED = 50;      // ms — confirms scanner mode (2 consecutive fast chars)
const SCAN_HUMAN_RESET = 300;    // ms — gap that means definitely human typing → reset buffer
const SCAN_MIN_CHARS = 4;        // minimum barcode length to process
const SCAN_SETTLE_DELAY = 200;   // ms silence after last char before processing
```

**Why these values:**
- `SCAN_CHAR_SPEED = 50ms`: Strict enough to distinguish scanner from human (humans type 250ms+/char)
- `SCAN_HUMAN_RESET = 300ms`: The key fix for uppercase barcodes. HID scanner uses Shift for uppercase letters (AG...) — Shift key adds 50-150ms overhead between uppercase chars. OLD code reset buffer at 50ms gaps → dropped 'A', 'G' prefixes. NEW: only reset if gap > 300ms.
- `SCAN_SETTLE_DELAY = 200ms`: Old value was 120ms — timer fired BEFORE 'G' could arrive (130ms gap), wiping 'A' from buffer. 200ms gives enough room.
- `elapsed < 400ms` in flush(): validates total barcode arrival time — scanner sends 10 chars in ~50ms, human would take seconds.

### 3d. The Core State Machine

```javascript
const state = { buf: '', firstMs: 0, lastMs: 0, scanning: false, timer: null };

const flush = () => {
  const elapsed = state.lastMs - state.firstMs;
  if (barcode.length >= SCAN_MIN_CHARS && elapsed < 400) {
    processBarcodeScan(barcode); // 400ms = max scan time
  }
  // reset state
};

// On each keydown:
// 1. If qty modal open → e.preventDefault(), return (blocks scanner from typing in qty field)
// 2. If focus is on other modal input (NOT search) → return
// 3. If gap > SCAN_HUMAN_RESET (300ms) → reset buffer, start fresh
// 4. If gap < SCAN_CHAR_SPEED (50ms) AND buf has 1+ chars → set scanning=true
// 5. If scanning → e.preventDefault() + setSearch('') (block char from input)
// 6. Always: append char to buf, reset settle timer to 200ms
```

### 3e. Deduplication

```javascript
const lastScanRef = useRef({ barcode: '', time: 0 });

// At top of processBarcodeScan:
if (barcode === lastScanRef.current.barcode && now - lastScanRef.current.time < 500) return;
lastScanRef.current = { barcode, time: now };
```
- Same barcode within 500ms → silently ignored (scanner bounce protection)
- Different barcode → processes immediately regardless of timing
- Shared with camera scanner (both paths use the same ref)

### 3f. Qty Modal Integration

```javascript
const scanQtyModalOpenRef = useRef(false);
useEffect(() => { scanQtyModalOpenRef.current = scanQtyModal; }, [scanQtyModal]);
```

When qty modal is open:
```javascript
if (scanQtyModalOpenRef.current) { e.preventDefault(); return; }
```
Scanner is **completely deaf** while user is entering quantity. Nothing types into the qty field.

### 3g. Skip Mode (Cart-Level)

```javascript
const autoAddProductsRef = useRef(new Set()); // products marked for auto-add
const skipAllRef = useRef(false);              // true = all products auto-add
```

**First scan of new product → qty modal with 3 options:**
1. **Add to Cart** — uses typed qty
2. **Skip — this product** → `autoAddProductsRef.add(product.id)` + add 1
3. **Skip — all products** → `skipAllRef.current = true` + add 1

**Subsequent scans:** if product in autoAddRef OR skipAllRef → straight to cart, no modal.

**On cart clear:** both refs reset.

### 3h. handleSearchKeyDown (simplified)

After the scanner refactor, the search field's `handleSearchKeyDown` only handles Enter:
```javascript
const handleSearchKeyDown = useCallback((e) => {
  if (e.key === 'Enter' && results.length > 0) addToCart(results[0]);
}, [results, addToCart]);
```
All scanner detection happens in the global capture listener.

### 3i. processBarcodeScan Flow

```
barcode received
  → dedup check (500ms same-barcode guard)
  → products.find(p => p.barcode === barcode)  ← searches local cache (no API call)
  → not found: toast.error + vibrate
  → skipAllRef OR autoAddProductsRef has product: addToCart(product) + focus search
  → first scan: setScanQtyProduct + setScanQtyModal(true)
```

---

## 4. CAMERA SCANNER

Separate path from HID scanner:
- Uses `html5-qrcode` library, camera access
- On scan: `lastScanRef` check (2000ms cooldown for camera vs 500ms for HID)
- No qty prompt — always adds 1 directly
- Shared `lastScanRef` with HID scanner

---

## 5. RECEIPT PREVIEW MODAL

Two-step print flow after sale:

**Step 1 — "Sale Complete" dialog:**
- Shows invoice number + total
- Buttons: Print Receipt (58mm) | Print Full Page | Skip
- "Print Receipt (58mm)" calls `handleShowReceiptPreview('thermal')`
  → closes this dialog first → opens preview dialog

**Step 2 — "Receipt Preview" dialog:**
- iframe shows rendered receipt (384px scaled to 85%)
- Uses `PrintEngine.generateHtml()` with external QR URL (browser can load it for preview)
- Buttons: Print 1 Copy | Print 2 Copies | Skip
- Actual print: `PrintBridge.print()` which replaces QR with local data URL

---

## 6. GIT / DEPLOYMENT NOTES

- **Emergent** pushes to `main` branch (via "Save to Github")
- **Cursor** also pushes to `main` branch
- Both AIs modify `PrintEngine.js`, `PrintBridge.js`, `TerminalSales.jsx`
- **When merging:** Emergent's version is the CORRECT reference for JS/web files
- **Cursor's version** is the CORRECT reference for Java files
- **Production deploy:** `cd /var/www/agribooks && git pull origin main && cd frontend && yarn install && yarn build`
- `yarn install` required if new npm packages were added (e.g., `qrcode` v1.5.4)

---

## 7. KEY PACKAGES

| Package | Version | Purpose |
|---------|---------|---------|
| `qrcode` | ^1.5.4 | Local QR generation (no external fetch) |
| `html5-qrcode` | ^2.3.8 | Camera barcode scanner |
| `@capacitor/core` | - | Native bridge |

---

## 8. THINGS THAT LOOK WRONG BUT ARE CORRECT

1. **`width: 100%` on body** (not `width: 384px`) — fixed-px causes right-side clipping on high-DPI
2. **Static `import QRCode from 'qrcode'`** (not dynamic import) — dynamic import fails in Capacitor WebView
3. **`SCAN_CHAR_SPEED = 50ms` but buffer doesn't reset on slow chars** — intentional. Strict 50ms only confirms scan mode. Buffer only resets on 300ms gaps (human threshold). Allows uppercase letters with Shift overhead.
4. **`SCAN_SETTLE_DELAY = 200ms`** (not 120ms) — 120ms timer fired before uppercase 'G' could arrive after 'A', wiping 'A' from buffer
5. **Close first dialog BEFORE opening second** — Android WebView can't stack two overlapping Dialogs
6. **`-webkit-font-smoothing: none`** — prevents gray anti-aliasing pixels that binarize to white on thermal paper
