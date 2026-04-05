# Cursor Prompt: H10 HID Barcode Scanner Integration for TerminalSales.jsx

## Problem
The H10 handheld POS has a built-in barcode scanner that acts as an HID keyboard. When you scan a barcode, it types the characters into the focused input field very rapidly (much faster than human typing). It does NOT send an Enter key at the end. The current code types the barcode twice because there's no detection/cooldown.

## What to add to `frontend/src/pages/terminal/TerminalSales.jsx`

### A) New state variables (add after the `overrideError` state declaration):

```jsx
// HID Barcode Scanner (H10) — scan detection & quantity prompt
const [scanQtyModal, setScanQtyModal] = useState(false);
const [scanQtyProduct, setScanQtyProduct] = useState(null);
const [scanQty, setScanQty] = useState('1');
const autoAddProductsRef = useRef(new Set());
const scanDetectRef = useRef({ keyTimes: [], processTimer: null, cooldownUntil: 0 });
```

### B) Rename existing camera scanner cooldown constant:
Change `const SCAN_COOLDOWN = 2000;` to `const CAMERA_SCAN_COOLDOWN = 2000;`
And update the one reference to it (in the camera scanner's `onScanSuccess`) to use `CAMERA_SCAN_COOLDOWN`.

### C) New handler functions (add before `updateQty`):

```jsx
// ── HID Barcode Scanner Detection ──────────────────────────────────────────
const SCAN_CHAR_SPEED = 50;      // max ms between chars for scanner
const SCAN_MIN_CHARS = 4;        // minimum barcode length
const SCAN_SETTLE_DELAY = 120;   // ms of silence before processing
const SCAN_COOLDOWN = 1500;      // ms cooldown after scan (prevents "types twice")

const processBarcodeScan = useCallback((barcode) => {
  const product = products.find(p => p.barcode === barcode);
  if (!product) {
    toast.error(`Barcode "${barcode}" not found. Scan again or add product on computer.`, { duration: 3000 });
    if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
    searchRef.current?.focus();
    return;
  }
  // Auto-add if already confirmed for this transaction
  if (autoAddProductsRef.current.has(product.id)) {
    addToCart(product);
    if (navigator.vibrate) navigator.vibrate(100);
    searchRef.current?.focus();
    return;
  }
  // First scan of this product — show quantity prompt
  setScanQtyProduct(product);
  setScanQty('1');
  setScanQtyModal(true);
}, [products, addToCart]);

const handleSearchKeyDown = useCallback((e) => {
  const now = Date.now();
  const detect = scanDetectRef.current;
  // During cooldown after a scan, block all input
  if (now < detect.cooldownUntil) { e.preventDefault(); return; }
  if (e.key.length === 1) {
    detect.keyTimes.push(now);
    detect.keyTimes = detect.keyTimes.filter(t => now - t < 500);
    clearTimeout(detect.processTimer);
    detect.processTimer = setTimeout(() => {
      const times = detect.keyTimes;
      if (times.length >= SCAN_MIN_CHARS) {
        let allFast = true;
        for (let i = 1; i < times.length; i++) {
          if (times[i] - times[i - 1] > SCAN_CHAR_SPEED) { allFast = false; break; }
        }
        if (allFast) {
          const barcode = (searchRef.current?.value || '').trim();
          if (barcode.length >= SCAN_MIN_CHARS) {
            detect.cooldownUntil = Date.now() + SCAN_COOLDOWN;
            detect.keyTimes = [];
            setSearch('');
            setResults([]);
            processBarcodeScan(barcode);
            return;
          }
        }
      }
      detect.keyTimes = [];
    }, SCAN_SETTLE_DELAY);
  }
}, [processBarcodeScan]);

const handleSearchChange = useCallback((e) => {
  if (Date.now() < scanDetectRef.current.cooldownUntil) { setSearch(''); return; }
  setSearch(e.target.value);
}, []);

const handleScanQtyConfirm = useCallback((autoAdd = false) => {
  if (!scanQtyProduct) return;
  const qty = Math.max(1, parseInt(scanQty) || 1);
  if (autoAdd) autoAddProductsRef.current.add(scanQtyProduct.id);
  const price = scanQtyProduct.prices?.[activeScheme] ?? 0;
  setCart(prev => {
    const existing = prev.find(c => c.product_id === scanQtyProduct.id);
    if (existing) {
      const newQty = existing.quantity + qty;
      return prev.map(c => c.product_id === scanQtyProduct.id
        ? { ...c, quantity: newQty, total: newQty * c.price } : c);
    }
    return [...prev, {
      product_id: scanQtyProduct.id, product_name: scanQtyProduct.name, sku: scanQtyProduct.sku,
      price, quantity: qty, total: price * qty, unit: scanQtyProduct.unit, is_repack: scanQtyProduct.is_repack,
      cost_price: scanQtyProduct.cost_price || 0,
      effective_capital: scanQtyProduct.effective_capital || scanQtyProduct.cost_price || 0,
      capital_method: scanQtyProduct.capital_method || 'manual',
      original_price: price,
    }];
  });
  toast.success(`${scanQtyProduct.name} x${qty}`, { duration: 1500 });
  if (navigator.vibrate) navigator.vibrate(100);
  setScanQtyModal(false);
  setScanQtyProduct(null);
  setTimeout(() => searchRef.current?.focus(), 100);
}, [scanQtyProduct, scanQty, activeScheme]);
```

### D) Update `clearCart` to reset auto-add set:
Change: `const clearCart = () => { setCart([]); setSelectedCustomer(null); setCustSearch(''); };`
To: `const clearCart = () => { setCart([]); setSelectedCustomer(null); setCustSearch(''); autoAddProductsRef.current.clear(); };`

### E) Update the global keyboard listener (the `useEffect` with `scanBufferRef`):
The existing listener only works when Enter is sent. Update the timeout to also process if buffer has 4+ chars (for scanners without Enter key):

```jsx
scanTimerRef.current = setTimeout(() => {
  const buffer = scanBufferRef.current.trim();
  if (buffer.length >= SCAN_MIN_CHARS) {
    processBarcodeScan(buffer);
  }
  scanBufferRef.current = '';
}, 100);
```

Also add `processBarcodeScan` to the dependency array of that useEffect.

### F) Update the search Input element:
Change from: `onChange={e => setSearch(e.target.value)}`
To: `onChange={handleSearchChange}` and add `onKeyDown={handleSearchKeyDown}`

### G) Add the Scan Quantity Dialog (before the closing `</div>` of the component):

```jsx
{/* Barcode Scan — Quantity Prompt */}
<Dialog open={scanQtyModal} onOpenChange={(o) => { if (!o) { setScanQtyModal(false); setScanQtyProduct(null); setTimeout(() => searchRef.current?.focus(), 100); } }}>
  <DialogContent className="max-w-xs mx-auto">
    <DialogHeader>
      <DialogTitle className="text-base font-bold" style={{ fontFamily: 'Manrope' }} data-testid="scan-qty-title">
        Scanned Product
      </DialogTitle>
      <DialogDescription className="sr-only">Enter quantity for scanned product</DialogDescription>
    </DialogHeader>
    <div className="space-y-3">
      <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-3">
        <p className="text-sm font-semibold text-slate-800">{scanQtyProduct?.name}</p>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-lg font-bold text-[#1A4D2E]">{formatPHP(scanQtyProduct?.prices?.[activeScheme] ?? 0)}</span>
          <span className="text-xs text-slate-400 font-mono">{scanQtyProduct?.barcode}</span>
        </div>
        {scanQtyProduct && (
          <p className="text-[10px] text-slate-500 mt-1">
            {scanQtyProduct.available != null ? `Stock: ${scanQtyProduct.available} ${scanQtyProduct.unit || ''}` : ''}
          </p>
        )}
      </div>
      <div>
        <label className="text-xs text-slate-500 font-medium mb-1 block">Quantity</label>
        <Input
          type="number"
          inputMode="numeric"
          min={1}
          value={scanQty}
          onChange={e => setScanQty(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleScanQtyConfirm(false); }}
          className="h-12 text-xl font-mono text-center font-bold"
          data-testid="scan-qty-input"
          autoFocus
        />
      </div>
      <Button
        onClick={() => handleScanQtyConfirm(false)}
        className="w-full bg-[#1A4D2E] hover:bg-[#15412a] text-white h-11"
        data-testid="scan-qty-confirm-btn"
      >
        <Check size={16} className="mr-2" /> Add to Cart
      </Button>
      <Button
        variant="outline"
        onClick={() => handleScanQtyConfirm(true)}
        className="w-full text-slate-600 h-10"
        data-testid="scan-qty-auto-btn"
      >
        Skip Qty — Auto +1 for This Transaction
      </Button>
    </div>
  </DialogContent>
</Dialog>
```

## How It Works
1. Scanner types barcode very fast (<50ms between chars) into search input
2. `handleSearchKeyDown` tracks keystroke timing — detects rapid input as "scanner"
3. After 120ms of silence, checks if 4+ chars arrived at scanner speed
4. If yes → looks up product by barcode, shows qty dialog (first time) or auto-adds (+1)
5. 1.5s cooldown prevents the "types twice" issue
6. Human typing (>100ms between chars) passes through to normal search as usual
