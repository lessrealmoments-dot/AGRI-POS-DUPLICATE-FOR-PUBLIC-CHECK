import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, Plus, Minus, Trash2, ShoppingCart, Camera, X, Check, CreditCard, Banknote, ChevronUp, ChevronDown, Wallet, Upload, Loader2, Clock, Printer, AlertTriangle, ShieldAlert, PackageX, Eye } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../../components/ui/dialog';
import { toast } from 'sonner';
import { formatPHP } from '../../lib/utils';
import PrintEngine from '../../lib/PrintEngine';
import PrintBridge from '../../lib/PrintBridge';
import {
  getProducts, getCustomers, getPriceSchemes,
  addPendingSale, getPendingSaleCount, getInventoryItem, getBranchPrice,
} from '../../lib/offlineDB';
import { newEnvelopeId } from '../../lib/syncManager';

export default function TerminalSales({ api, session, isOnline, pendingCount, setPendingCount }) {
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState([]);
  const [cart, setCart] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [schemes, setSchemes] = useState([]);
  const [activeScheme, setActiveScheme] = useState('retail');
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [scannerActive, setScannerActive] = useState(false);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [custSearch, setCustSearch] = useState('');
  const [showCustList, setShowCustList] = useState(false);
  const [cartExpanded, setCartExpanded] = useState(true);
  const searchRef = useRef(null);
  const scannerRef = useRef(null);
  const scannerContainerRef = useRef(null);
  const lastScanRef = useRef({ barcode: '', time: 0 });
  const CAMERA_SCAN_COOLDOWN = 2000;
  const [lastSaleData, setLastSaleData] = useState(null); // for print prompt
  const [showPrintPrompt, setShowPrintPrompt] = useState(false);
  const [businessInfo, setBusinessInfo] = useState({});
  // Insufficient stock override
  const [stockModal, setStockModal] = useState(false);
  const [insufficientItems, setInsufficientItems] = useState([]);
  const [pendingSaleData, setPendingSaleData] = useState(null);
  const [overridePin, setOverridePin] = useState('');
  const [overrideSubmitting, setOverrideSubmitting] = useState(false);
  const [overrideError, setOverrideError] = useState('');
  // HID Barcode Scanner (H10) — scan detection & quantity prompt
  const [scanQtyModal, setScanQtyModal] = useState(false);
  const [scanQtyProduct, setScanQtyProduct] = useState(null);
  const [scanQty, setScanQty] = useState('1');
  const autoAddProductsRef = useRef(new Set());
  const skipAllRef = useRef(false); // when true, all HID scans add 1 with no prompt this receipt
  const scanQtyModalOpenRef = useRef(false); // mirrors scanQtyModal — readable inside capture listener
  // lastScanRef already declared above (shared with camera scanner for unified 500ms dedup)
  // Receipt preview before printing
  const [receiptPreview, setReceiptPreview] = useState(null); // { html, type, format }
  const [printingCopies, setPrintingCopies] = useState(false);

  // Keep ref in sync with state so the capture-phase listener can read it without stale closure
  useEffect(() => { scanQtyModalOpenRef.current = scanQtyModal; }, [scanQtyModal]);

  // Load cached data
  useEffect(() => {
    (async () => {
      const [prods, custs, schs] = await Promise.all([getProducts(), getCustomers(), getPriceSchemes()]);
      setProducts(prods);
      setCustomers(custs);
      setSchemes(schs);
    })();
    api.get('/settings/business-info').then(r => setBusinessInfo(r.data || {})).catch(() => {});
  }, []); // eslint-disable-line

  // Search products
  useEffect(() => {
    if (!search.trim()) { setResults([]); return; }
    const q = search.toLowerCase();
    const filtered = products.filter(p =>
      (p.name || '').toLowerCase().includes(q) ||
      (p.sku || '').toLowerCase().includes(q) ||
      (p.barcode || '').includes(search)
    ).slice(0, 20);
    setResults(filtered);
  }, [search, products]);

  const getPrice = (product) => product.prices?.[activeScheme] ?? 0;

  const addToCart = useCallback((product) => {
    const price = product.prices?.[activeScheme] ?? 0;
    setCart(prev => {
      const existing = prev.find(c => c.product_id === product.id);
      if (existing) {
        return prev.map(c => c.product_id === product.id
          ? { ...c, quantity: c.quantity + 1, total: (c.quantity + 1) * c.price }
          : c
        );
      }
      return [...prev, {
        product_id: product.id, product_name: product.name, sku: product.sku,
        price, quantity: 1, total: price, unit: product.unit, is_repack: product.is_repack,
        cost_price: product.cost_price || 0,
        effective_capital: product.effective_capital || product.cost_price || 0,
        capital_method: product.capital_method || 'manual',
        original_price: price,
      }];
    });
    setSearch('');
    setResults([]);
    toast.success(product.name, { duration: 1500 });
  }, [activeScheme]);

  // ── HID Barcode Scanner Detection ──────────────────────────────────────────
  const SCAN_CHAR_SPEED = 50;      // max ms between chars for scanner
  const SCAN_MIN_CHARS = 4;        // minimum barcode length
  const SCAN_SETTLE_DELAY = 120;   // ms of silence before processing

  const processBarcodeScan = useCallback((barcode) => {
    // Dedup: ignore same barcode scanned within 500ms (scanner bounce protection)
    const now = Date.now();
    if (barcode === lastScanRef.current.barcode && now - lastScanRef.current.time < 500) return;
    lastScanRef.current = { barcode, time: now };

    const product = products.find(p => p.barcode === barcode);
    if (!product) {
      toast.error(`Barcode "${barcode}" not found. Scan again or add product on computer.`, { duration: 3000 });
      if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
      searchRef.current?.focus();
      return;
    }
    // Skip-all mode — silent 1-per-scan for everything this receipt
    if (skipAllRef.current) {
      addToCart(product);
      if (navigator.vibrate) navigator.vibrate(100);
      searchRef.current?.focus();
      return;
    }
    // Product already marked for auto-add this receipt
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
    // Enter on search field picks the first result
    if (e.key === 'Enter' && results.length > 0) addToCart(results[0]);
  }, [results, addToCart]);

  const handleSearchChange = useCallback((e) => {
    setSearch(e.target.value);
  }, []);

  // mode: 'add' (use qty input) | 'skip_product' (auto+1 this product) | 'skip_all' (auto+1 everything)
  const handleScanQtyConfirm = useCallback((mode = 'add') => {
    if (!scanQtyProduct) return;
    const qty = (mode === 'add') ? Math.max(1, parseInt(scanQty) || 1) : 1;
    if (mode === 'skip_product') autoAddProductsRef.current.add(scanQtyProduct.id);
    if (mode === 'skip_all') skipAllRef.current = true;
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

  const updateQty = (productId, delta) => {
    setCart(prev => prev.map(c => {
      if (c.product_id !== productId) return c;
      const newQty = Math.max(1, c.quantity + delta);
      return { ...c, quantity: newQty, total: newQty * c.price };
    }));
  };

  const removeItem = (productId) => setCart(prev => prev.filter(c => c.product_id !== productId));
  const clearCart = () => { setCart([]); setSelectedCustomer(null); setCustSearch(''); autoAddProductsRef.current.clear(); skipAllRef.current = false; };

  // Receipt preview → print copies
  const handleShowReceiptPreview = (format) => {
    if (!lastSaleData) return;
    const type = PrintEngine.getDocType(lastSaleData);
    const docCode = lastSaleData.doc_code || lastSaleData.invoice_number || '';
    const html = PrintEngine.generateHtml({ type, data: lastSaleData, format, businessInfo, docCode });
    // Close "Sale Complete" dialog first so Android WebView doesn't stack dialogs
    setShowPrintPrompt(false);
    setReceiptPreview({ html, type, format });
  };

  const handlePrintCopies = async (copies) => {
    if (!receiptPreview || !lastSaleData) return;
    setPrintingCopies(true);
    try {
      for (let i = 0; i < copies; i++) {
        await PrintBridge.print({ type: receiptPreview.type, data: lastSaleData, format: receiptPreview.format, businessInfo, docCode: lastSaleData.doc_code || lastSaleData.invoice_number || '' });
        if (i < copies - 1) await new Promise(r => setTimeout(r, 2000));
      }
      toast.success(`Printed ${copies} ${copies === 1 ? 'copy' : 'copies'}`);
    } catch (err) {
      toast.error(`Print failed: ${err.message || 'Unknown error'}`);
    }
    setPrintingCopies(false);
    setReceiptPreview(null);
    setShowPrintPrompt(false);
    setLastSaleData(null);
  };

  const grandTotal = cart.reduce((s, c) => s + c.total, 0);

  // Camera barcode scanner
  const startScanner = async () => {
    setScannerActive(true);
    // Wait for div to render before starting scanner
    await new Promise(r => setTimeout(r, 300));
    try {
      const { Html5Qrcode } = await import('html5-qrcode');
      const scanner = new Html5Qrcode('terminal-scanner-view');
      scannerRef.current = scanner;

      await scanner.start(
        { facingMode: 'environment' },
        { fps: 5, qrbox: { width: 250, height: 100 }, aspectRatio: 1.777 },
        (decodedText) => {
          // Debounce: skip if same barcode within cooldown
          const now = Date.now();
          if (decodedText === lastScanRef.current.barcode && now - lastScanRef.current.time < CAMERA_SCAN_COOLDOWN) return;
          lastScanRef.current = { barcode: decodedText, time: now };

          const product = products.find(p => p.barcode === decodedText);
          if (product) {
            addToCart(product);
            if (navigator.vibrate) navigator.vibrate(100);
          } else {
            toast.error(`No product for barcode: ${decodedText}`);
          }
        },
        () => {}
      );
    } catch (e) {
      console.error('Scanner error:', e);
      toast.error('Camera access denied. Check browser permissions.');
      setScannerActive(false);
    }
  };

  const stopScanner = async () => {
    if (scannerRef.current) {
      try { await scannerRef.current.stop(); } catch {}
      scannerRef.current = null;
    }
    setScannerActive(false);
  };

  // Single capture-phase barcode listener — fires BEFORE the input receives the event.
  // e.preventDefault() in capture phase blocks chars from being inserted into any input field.
  useEffect(() => {
    const state = { buf: '', lastMs: 0, scanning: false, timer: null };

    const onKey = (e) => {
      if (e.key.length !== 1 && e.key !== 'Enter') return;

      // Qty modal is open — block ALL scanner input so it doesn't type into the qty field
      if (scanQtyModalOpenRef.current) { e.preventDefault(); return; }

      // Let modal inputs (qty fields etc.) work normally — only intercept search or unfocused
      const tag = (e.target || {}).tagName;
      if ((tag === 'INPUT' || tag === 'TEXTAREA') && e.target !== searchRef.current) return;

      const now = Date.now();

      if (e.key === 'Enter') {
        if (state.buf.length >= SCAN_MIN_CHARS && state.scanning) {
          e.preventDefault();
          const barcode = state.buf.trim();
          state.buf = ''; state.lastMs = 0; state.scanning = false;
          clearTimeout(state.timer);
          setSearch(''); setResults([]);
          processBarcodeScan(barcode);
        }
        return;
      }

      const gap = now - state.lastMs;
      const isFast = state.lastMs > 0 && gap < SCAN_CHAR_SPEED;

      if (state.buf.length === 0 || isFast) {
        if (state.buf.length >= 1 && isFast) state.scanning = true;
        if (state.scanning) {
          e.preventDefault();           // block char from reaching the input
          setSearch(''); setResults([]); // clear any char that slipped through
        }
        state.buf += e.key;
        state.lastMs = now;
        clearTimeout(state.timer);
        state.timer = setTimeout(() => {
          if (state.buf.length >= SCAN_MIN_CHARS && state.scanning) {
            const barcode = state.buf.trim();
            setSearch(''); setResults([]);
            processBarcodeScan(barcode);
          }
          state.buf = ''; state.lastMs = 0; state.scanning = false;
        }, SCAN_SETTLE_DELAY);
      } else {
        // Gap too long — human typing, reset buffer
        state.buf = e.key; state.lastMs = now; state.scanning = false;
        clearTimeout(state.timer);
        state.timer = setTimeout(() => { state.buf = ''; state.lastMs = 0; }, SCAN_SETTLE_DELAY);
      }
    };

    window.addEventListener('keydown', onKey, true); // true = capture phase
    return () => { window.removeEventListener('keydown', onKey, true); clearTimeout(state.timer); };
  }, [processBarcodeScan]); // eslint-disable-line

  // Process sale
  // ── Checkout state ──
  const [paymentType, setPaymentType] = useState(''); // cash, digital, credit, split
  const [amountTendered, setAmountTendered] = useState('');
  const [digitalScreenshot, setDigitalScreenshot] = useState(null);
  const [digitalRef, setDigitalRef] = useState('');
  const [creditDays, setCreditDays] = useState(15);
  const [splitCash, setSplitCash] = useState('');
  const [splitDigital, setSplitDigital] = useState('');
  const [splitScreenshot, setSplitScreenshot] = useState(null);
  const fileInputRef = useRef(null);
  const splitFileInputRef = useRef(null);

  const resetCheckout = () => {
    setPaymentType(''); setAmountTendered(''); setDigitalScreenshot(null); setDigitalRef('');
    setCreditDays(15); setSplitCash(''); setSplitDigital(''); setSplitScreenshot(null);
  };

  const changeAmount = paymentType === 'cash' && amountTendered
    ? Math.max(0, parseFloat(amountTendered) - grandTotal) : 0;

  const processSale = async () => {
    if (cart.length === 0) { toast.error('Cart is empty'); return; }
    if (!paymentType) { toast.error('Select a payment type'); return; }
    if (paymentType === 'cash' && (!amountTendered || parseFloat(amountTendered) < grandTotal)) {
      toast.error('Amount tendered must be at least the total'); return;
    }
    if (paymentType === 'digital' && !digitalScreenshot) {
      toast.error('Upload a screenshot of the digital payment'); return;
    }
    if (paymentType === 'split') {
      const cashAmt = parseFloat(splitCash) || 0;
      const digAmt = parseFloat(splitDigital) || 0;
      if (cashAmt + digAmt < grandTotal) { toast.error('Cash + Digital must cover the total'); return; }
      if (digAmt > 0 && !splitScreenshot) { toast.error('Upload screenshot for the digital portion'); return; }
    }
    setSaving(true);

    const saleId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const envelopeId = newEnvelopeId();
    const today = new Date().toISOString().slice(0, 10);

    const paymentMethod = paymentType === 'cash' ? 'Cash'
      : paymentType === 'digital' ? 'Digital'
      : paymentType === 'credit' ? 'Credit'
      : paymentType === 'split' ? 'Split' : 'Cash';

    const amountPaid = paymentType === 'credit' ? 0
      : paymentType === 'split' ? parseFloat(splitCash) || 0
      : grandTotal;

    const saleItems = cart.map(c => ({
      product_id: c.product_id, product_name: c.product_name, sku: c.sku,
      quantity: c.quantity, rate: c.price, price: c.price, total: c.total,
      discount_type: 'amount', discount_value: 0, discount_amount: 0,
      is_repack: c.is_repack || false,
    }));

    const saleData = {
      id: saleId, envelope_id: envelopeId, branch_id: session.branchId,
      customer_id: selectedCustomer?.id || null,
      customer_name: selectedCustomer?.name || 'Walk-in',
      items: saleItems, subtotal: grandTotal, freight: 0, overall_discount: 0,
      grand_total: grandTotal, amount_paid: amountPaid,
      balance: paymentType === 'credit' ? grandTotal : 0,
      terms: paymentType === 'credit' ? 'Credit' : 'COD',
      terms_days: paymentType === 'credit' ? creditDays : 0,
      prefix: 'KS', order_date: today, invoice_date: today,
      payment_method: paymentMethod, payment_type: paymentType,
      fund_source: 'cashier', sale_type: selectedCustomer ? 'credit' : 'walk_in',
      mode: 'quick', source: 'agrismart_terminal', terminal_id: session.terminalId,
      status: paymentType === 'credit' ? 'unpaid' : 'paid',
      created_at: new Date().toISOString(),
      digital_reference: digitalRef || undefined,
    };

    if (paymentType === 'split') {
      saleData.split_cash = parseFloat(splitCash) || 0;
      saleData.split_digital = parseFloat(splitDigital) || 0;
    }

    if (isOnline) {
      try {
        const res = await api.post('/unified-sale', saleData);
        const invoiceNum = res.data.invoice_number || res.data.sale_number;
        toast.success(`Sale ${invoiceNum} completed!`);
        // Store sale data for print prompt
        setLastSaleData({ ...saleData, invoice_number: invoiceNum, ...res.data });
        setShowPrintPrompt(true);
        clearCart(); setCheckoutOpen(false); resetCheckout();
      } catch (e) {
        const detail = e.response?.data?.detail;
        if (e.response?.status === 422 && detail?.type === 'insufficient_stock') {
          setInsufficientItems(detail.items || []);
          setPendingSaleData(saleData);
          setCheckoutOpen(false);
          setStockModal(true);
          setSaving(false);
          return;
        }
        if (detail && typeof detail === 'object') {
          toast.error(detail.message || 'Sale failed');
        } else {
          toast.error(typeof detail === 'string' ? detail : 'Sale failed');
        }
        setSaving(false);
        return;
      }
    } else {
      await addPendingSale(saleData);
      const count = await getPendingSaleCount();
      setPendingCount(count);
      toast.success('Sale saved offline — will sync when connected');
      clearCart(); setCheckoutOpen(false); resetCheckout();
    }
    setSaving(false);
  };

  // Manager override for insufficient stock
  const handleStockOverride = async () => {
    if (!overridePin.trim() || !pendingSaleData) return;
    setOverrideSubmitting(true);
    setOverrideError('');
    try {
      const res = await api.post('/unified-sale', { ...pendingSaleData, manager_override_pin: overridePin.trim() });
      const invoiceNum = res.data.invoice_number || res.data.sale_number;
      toast.success(`Sale ${invoiceNum} completed (manager override). Ticket created.`, { duration: 4000 });
      setLastSaleData({ ...pendingSaleData, invoice_number: invoiceNum, ...res.data });
      setShowPrintPrompt(true);
      setStockModal(false);
      setPendingSaleData(null);
      setInsufficientItems([]);
      setOverridePin('');
      clearCart(); resetCheckout();
    } catch (e) {
      const d = e?.response?.data?.detail;
      setOverrideError(typeof d === 'string' ? d : d?.message || 'Invalid PIN — override denied');
    }
    setOverrideSubmitting(false);
  };

  // Helper: get stock for a product from the cached list
  const getStock = useCallback((productId) => {
    const p = products.find(pr => pr.id === productId);
    return p?.available ?? null;
  }, [products]);

  return (
    <div className="flex flex-col h-full" data-testid="terminal-sales">
      {/* Search Bar + Scanner Toggle */}
      <div className="p-3 bg-white border-b border-slate-200">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <Input
              ref={searchRef}
              value={search}
              onChange={handleSearchChange}
              onKeyDown={handleSearchKeyDown}
              placeholder="Search product or scan barcode..."
              className="pl-9 h-10 text-base"
              data-testid="terminal-search-input"
            />
            {search && (
              <button onClick={() => { setSearch(''); setResults([]); }} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400">
                <X size={16} />
              </button>
            )}
          </div>
          <Button
            variant={scannerActive ? 'default' : 'outline'}
            size="icon"
            className={`h-10 w-10 ${scannerActive ? 'bg-emerald-600 hover:bg-emerald-700' : ''}`}
            onClick={scannerActive ? stopScanner : startScanner}
            data-testid="camera-scanner-btn"
          >
            <Camera size={18} />
          </Button>
        </div>

        {/* Camera scanner view — clipped to show only the scanning strip */}
        {scannerActive && (
          <div className="mt-2 rounded-xl overflow-hidden border border-slate-200 bg-black" ref={scannerContainerRef}
               style={{ height: 140, position: 'relative' }}>
            <div id="terminal-scanner-view" className="w-full"
                 style={{ position: 'absolute', top: '50%', left: 0, right: 0, transform: 'translateY(-50%)' }} />
          </div>
        )}

        {/* Search results */}
        {results.length > 0 && (
          <div className="mt-2 bg-white rounded-xl border border-slate-200 shadow-lg max-h-60 overflow-auto" data-testid="search-results">
            {results.map(p => {
              const avail = p.available ?? 0;
              const isOut = avail <= 0;
              const isLow = avail > 0 && avail <= (p.reorder_point || 5);
              return (
              <button
                key={p.id}
                onClick={() => addToCart(p)}
                className={`w-full flex items-center justify-between px-3 py-2.5 border-b border-slate-100 last:border-0 text-left ${
                  isOut ? 'bg-red-50/40' : 'hover:bg-emerald-50'
                }`}
                data-testid={`search-result-${p.id}`}
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800 truncate">{p.name}</p>
                  <p className="text-xs text-slate-400">{p.sku} {p.barcode ? `· ${p.barcode}` : ''}</p>
                </div>
                <div className="text-right flex-shrink-0 ml-2">
                  <p className="text-sm font-bold text-[#1A4D2E]">{formatPHP(getPrice(p))}</p>
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                    isOut ? 'bg-red-100 text-red-600' :
                    isLow ? 'bg-amber-100 text-amber-700' :
                    'bg-emerald-50 text-emerald-700'
                  }`}>
                    {isOut ? 'Out' : `${avail} ${p.unit || ''}`}
                  </span>
                </div>
              </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Cart */}
      <div className="flex-1 overflow-auto p-3">
        {cart.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-400 py-12">
            <ShoppingCart size={36} className="mb-3 opacity-30" />
            <p className="text-sm">Scan or search to add items</p>
          </div>
        ) : (
          <div className="space-y-2" data-testid="cart-items">
            {cart.map(item => {
              const stock = getStock(item.product_id);
              const isOverStock = stock !== null && item.quantity > stock;
              return (
              <div key={item.product_id} className={`bg-white rounded-xl border p-3 flex items-center gap-3 ${isOverStock ? 'border-amber-300 bg-amber-50/30' : 'border-slate-200'}`} data-testid={`cart-item-${item.product_id}`}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-800 truncate">{item.product_name}</p>
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-slate-400">{formatPHP(item.price)} each</p>
                    {stock !== null && (
                      <span className={`text-[10px] font-medium ${isOverStock ? 'text-amber-600' : 'text-slate-400'}`}>
                        {isOverStock ? `Only ${stock} avail` : `${stock} avail`}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => updateQty(item.product_id, -1)}
                    className="w-9 h-9 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600 active:bg-slate-200"
                    data-testid={`qty-minus-${item.product_id}`}
                  >
                    <Minus size={16} />
                  </button>
                  <span className="w-10 text-center text-sm font-bold" data-testid={`qty-display-${item.product_id}`}>
                    {item.quantity}
                  </span>
                  <button
                    onClick={() => updateQty(item.product_id, 1)}
                    className="w-9 h-9 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600 active:bg-slate-200"
                    data-testid={`qty-plus-${item.product_id}`}
                  >
                    <Plus size={16} />
                  </button>
                </div>
                <p className="text-sm font-bold text-[#1A4D2E] w-20 text-right">{formatPHP(item.total)}</p>
                <button
                  onClick={() => removeItem(item.product_id)}
                  className="text-slate-300 hover:text-red-500 p-1"
                  data-testid={`remove-${item.product_id}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Bottom: Total + Checkout */}
      {cart.length > 0 && (
        <div className="bg-white border-t border-slate-200 p-3 safe-area-bottom">
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-xs text-slate-500">{cart.length} item(s)</p>
              <p className="text-xl font-bold text-[#1A4D2E]" data-testid="cart-total">{formatPHP(grandTotal)}</p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={clearCart} data-testid="clear-cart-btn">
                <Trash2 size={14} className="mr-1" /> Clear
              </Button>
              <Button
                onClick={() => setCheckoutOpen(true)}
                className="bg-[#1A4D2E] hover:bg-[#15412a] text-white px-6"
                data-testid="checkout-btn"
              >
                <Banknote size={16} className="mr-1.5" /> Checkout
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Checkout Dialog */}
      <Dialog open={checkoutOpen} onOpenChange={v => { setCheckoutOpen(v); if (!v) resetCheckout(); }}>
        <DialogContent className="max-w-md mx-auto max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="text-base font-bold" style={{ fontFamily: 'Manrope' }}>
              {!paymentType ? 'Checkout' : paymentType === 'cash' ? 'Cash Payment' : paymentType === 'digital' ? 'Digital Payment' : paymentType === 'credit' ? 'Credit Sale' : 'Split Payment'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {/* Customer selection */}
            <div>
              <label className="text-xs text-slate-500 font-medium mb-1 block">Customer</label>
              <Input
                value={custSearch}
                onChange={e => { setCustSearch(e.target.value); setShowCustList(true); }}
                placeholder="Walk-in (type to search)"
                className="h-9"
                data-testid="checkout-customer-input"
              />
              {showCustList && custSearch && (
                <div className="bg-white border rounded-lg mt-1 max-h-32 overflow-auto shadow-lg">
                  {customers.filter(c => c.name?.toLowerCase().includes(custSearch.toLowerCase())).slice(0, 5).map(c => (
                    <button key={c.id} onClick={() => { setSelectedCustomer(c); setCustSearch(c.name); setShowCustList(false); }}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-emerald-50 border-b last:border-0">
                      {c.name}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Order summary */}
            <div className="bg-slate-50 rounded-xl p-3 space-y-1.5">
              {cart.map(c => (
                <div key={c.product_id} className="flex justify-between text-xs">
                  <span className="text-slate-600 truncate max-w-[60%]">{c.product_name} x{c.quantity}</span>
                  <span className="font-mono text-slate-800">{formatPHP(c.total)}</span>
                </div>
              ))}
              <div className="border-t border-slate-200 pt-1.5 mt-2 flex justify-between font-bold text-sm">
                <span>Total</span>
                <span className="text-[#1A4D2E]">{formatPHP(grandTotal)}</span>
              </div>
            </div>

            {/* Payment Type Selection */}
            {!paymentType && (
              <div className="space-y-2" data-testid="payment-type-selection">
                <label className="text-xs text-slate-500 font-medium block">Payment Method</label>
                <div className="grid grid-cols-2 gap-2">
                  <button onClick={() => setPaymentType('cash')}
                    className="flex flex-col items-center gap-1.5 p-3 rounded-xl border-2 border-slate-200 hover:border-emerald-400 hover:bg-emerald-50 transition-colors"
                    data-testid="pay-type-cash">
                    <Banknote size={22} className="text-emerald-600" />
                    <span className="text-sm font-medium text-slate-700">Cash</span>
                  </button>
                  <button onClick={() => setPaymentType('digital')}
                    className="flex flex-col items-center gap-1.5 p-3 rounded-xl border-2 border-slate-200 hover:border-blue-400 hover:bg-blue-50 transition-colors"
                    data-testid="pay-type-digital">
                    <Wallet size={22} className="text-blue-600" />
                    <span className="text-sm font-medium text-slate-700">Digital</span>
                  </button>
                  <button onClick={() => setPaymentType('split')}
                    className="flex flex-col items-center gap-1.5 p-3 rounded-xl border-2 border-slate-200 hover:border-amber-400 hover:bg-amber-50 transition-colors"
                    data-testid="pay-type-split">
                    <CreditCard size={22} className="text-amber-600" />
                    <span className="text-sm font-medium text-slate-700">Split</span>
                  </button>
                  <button onClick={() => { if (!selectedCustomer) { toast.error('Select a customer for credit sales'); return; } setPaymentType('credit'); }}
                    className="flex flex-col items-center gap-1.5 p-3 rounded-xl border-2 border-slate-200 hover:border-red-400 hover:bg-red-50 transition-colors"
                    data-testid="pay-type-credit">
                    <Clock size={22} className="text-red-600" />
                    <span className="text-sm font-medium text-slate-700">Credit</span>
                  </button>
                </div>
              </div>
            )}

            {/* ── Cash Payment ── */}
            {paymentType === 'cash' && (
              <div className="space-y-3" data-testid="cash-payment-form">
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1 block">Amount Tendered</label>
                  <Input type="number" inputMode="decimal" min={0} step="0.01"
                    value={amountTendered} onChange={e => setAmountTendered(e.target.value)}
                    placeholder={formatPHP(grandTotal)} className="h-12 text-xl font-mono text-center font-bold"
                    data-testid="amount-tendered-input" autoFocus />
                </div>
                {amountTendered && parseFloat(amountTendered) >= grandTotal && (
                  <div className="flex items-center justify-between p-3 bg-emerald-50 border border-emerald-200 rounded-xl">
                    <span className="text-sm text-emerald-700 font-medium">Change</span>
                    <span className="text-xl font-bold font-mono text-emerald-700" data-testid="change-amount">{formatPHP(changeAmount)}</span>
                  </div>
                )}
                {/* Quick amount buttons */}
                <div className="flex gap-2 flex-wrap">
                  {[grandTotal, Math.ceil(grandTotal / 100) * 100, Math.ceil(grandTotal / 500) * 500, 1000, 2000].filter((v, i, a) => a.indexOf(v) === i && v >= grandTotal).slice(0, 4).map(amt => (
                    <button key={amt} onClick={() => setAmountTendered(String(amt))}
                      className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium border transition-colors ${
                        parseFloat(amountTendered) === amt ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white border-slate-200 text-slate-700 hover:border-emerald-300'
                      }`}>{formatPHP(amt)}</button>
                  ))}
                </div>
              </div>
            )}

            {/* ── Digital Payment ── */}
            {paymentType === 'digital' && (
              <div className="space-y-3" data-testid="digital-payment-form">
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1 block">Reference # (optional)</label>
                  <Input value={digitalRef} onChange={e => setDigitalRef(e.target.value)} placeholder="e.g. GCash ref number" className="h-9" />
                </div>
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1.5 block">Payment Screenshot <span className="text-red-500">*</span></label>
                  {digitalScreenshot ? (
                    <div className="relative rounded-xl overflow-hidden border border-emerald-300 bg-emerald-50">
                      <img src={URL.createObjectURL(digitalScreenshot)} alt="proof" className="w-full max-h-40 object-contain" />
                      <button onClick={() => setDigitalScreenshot(null)}
                        className="absolute top-2 right-2 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center">
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <button onClick={() => fileInputRef.current?.click()}
                      className="w-full p-6 border-2 border-dashed border-slate-300 rounded-xl text-center hover:border-blue-400 hover:bg-blue-50 transition-colors"
                      data-testid="upload-digital-proof">
                      <Upload size={24} className="mx-auto text-slate-400 mb-2" />
                      <p className="text-sm text-slate-600 font-medium">Tap to upload screenshot</p>
                      <p className="text-xs text-slate-400 mt-0.5">GCash, Maya, bank transfer, etc.</p>
                    </button>
                  )}
                  <input ref={fileInputRef} type="file" accept="image/*" capture="environment" className="hidden"
                    onChange={e => { if (e.target.files?.[0]) setDigitalScreenshot(e.target.files[0]); }} />
                </div>
              </div>
            )}

            {/* ── Credit Sale ── */}
            {paymentType === 'credit' && (
              <div className="space-y-3" data-testid="credit-payment-form">
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl">
                  <p className="text-xs text-amber-700 font-medium">Credit sale for: <b>{selectedCustomer?.name || 'Walk-in'}</b></p>
                  <p className="text-xs text-amber-600 mt-0.5">Customer will owe {formatPHP(grandTotal)}</p>
                </div>
                <div>
                  <label className="text-xs text-slate-500 font-medium mb-1 block">Payment Terms</label>
                  <div className="flex gap-2">
                    {[15, 30, 60].map(d => (
                      <button key={d} onClick={() => setCreditDays(d)}
                        className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                          creditDays === d ? 'bg-[#1A4D2E] text-white border-[#1A4D2E]' : 'bg-white border-slate-200 text-slate-700 hover:border-emerald-300'
                        }`}>{d} days</button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* ── Split Payment ── */}
            {paymentType === 'split' && (
              <div className="space-y-3" data-testid="split-payment-form">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-slate-500 font-medium mb-1 block">Cash Amount</label>
                    <Input type="number" inputMode="decimal" value={splitCash}
                      onChange={e => { setSplitCash(e.target.value); setSplitDigital(String(Math.max(0, grandTotal - (parseFloat(e.target.value) || 0)))); }}
                      placeholder="0.00" className="h-10 font-mono" data-testid="split-cash-input" />
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 font-medium mb-1 block">Digital Amount</label>
                    <Input type="number" inputMode="decimal" value={splitDigital}
                      onChange={e => { setSplitDigital(e.target.value); setSplitCash(String(Math.max(0, grandTotal - (parseFloat(e.target.value) || 0)))); }}
                      placeholder="0.00" className="h-10 font-mono" data-testid="split-digital-input" />
                  </div>
                </div>
                {parseFloat(splitDigital) > 0 && (
                  <div>
                    <label className="text-xs text-slate-500 font-medium mb-1.5 block">Digital Payment Screenshot <span className="text-red-500">*</span></label>
                    {splitScreenshot ? (
                      <div className="relative rounded-xl overflow-hidden border border-emerald-300 bg-emerald-50">
                        <img src={URL.createObjectURL(splitScreenshot)} alt="proof" className="w-full max-h-32 object-contain" />
                        <button onClick={() => setSplitScreenshot(null)}
                          className="absolute top-2 right-2 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center">
                          <X size={14} />
                        </button>
                      </div>
                    ) : (
                      <button onClick={() => splitFileInputRef.current?.click()}
                        className="w-full p-4 border-2 border-dashed border-slate-300 rounded-xl text-center hover:border-blue-400 hover:bg-blue-50 transition-colors"
                        data-testid="upload-split-proof">
                        <Upload size={20} className="mx-auto text-slate-400 mb-1" />
                        <p className="text-xs text-slate-600 font-medium">Upload digital payment proof</p>
                      </button>
                    )}
                    <input ref={splitFileInputRef} type="file" accept="image/*" capture="environment" className="hidden"
                      onChange={e => { if (e.target.files?.[0]) setSplitScreenshot(e.target.files[0]); }} />
                  </div>
                )}
              </div>
            )}

            {/* Action buttons */}
            {paymentType && (
              <div className="flex gap-2 pt-1">
                <Button variant="outline" onClick={resetCheckout} className="flex-1">Back</Button>
                <Button onClick={processSale} disabled={saving}
                  className="flex-1 bg-[#1A4D2E] hover:bg-[#15412a] text-white h-12"
                  data-testid="confirm-payment-btn">
                  {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : <Check size={16} className="mr-2" />}
                  {saving ? 'Processing...' : paymentType === 'credit' ? 'Confirm Credit Sale' : `Pay ${formatPHP(grandTotal)}`}
                </Button>
              </div>
            )}

            {!isOnline && (
              <p className="text-[10px] text-amber-600 text-center">You are offline. Sale will be saved and synced when connected.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Insufficient Stock Override Modal */}
      <Dialog open={stockModal} onOpenChange={(o) => { if (!o) { setStockModal(false); setPendingSaleData(null); setInsufficientItems([]); setOverridePin(''); setOverrideError(''); } }}>
        <DialogContent className="max-w-sm mx-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-amber-700" style={{ fontFamily: 'Manrope' }}>
              <PackageX size={18} className="text-amber-600" /> Insufficient Stock
            </DialogTitle>
            <DialogDescription>These items have less stock than needed.</DialogDescription>
          </DialogHeader>

          <div className="space-y-2 my-1">
            {insufficientItems.map((item, i) => (
              <div key={i} className="flex items-center justify-between bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm">
                <span className="font-medium text-slate-800 truncate max-w-[55%]">{item.product_name}</span>
                <span className="text-amber-700 font-mono text-xs">
                  Have: {item.system_qty} · Need: {item.needed_qty}
                </span>
              </div>
            ))}
          </div>

          <div className="p-3 rounded-xl border border-amber-200 bg-amber-50/50 space-y-2">
            <div className="flex items-center gap-2">
              <ShieldAlert size={15} className="text-amber-600 shrink-0" />
              <p className="text-sm font-semibold text-amber-800">Manager Override</p>
            </div>
            <p className="text-xs text-amber-700">Proceeds with sale. Inventory goes negative and a ticket is created.</p>
            <Input
              type="password"
              placeholder="Enter manager PIN"
              value={overridePin}
              onChange={e => { setOverridePin(e.target.value); setOverrideError(''); }}
              onKeyDown={e => e.key === 'Enter' && handleStockOverride()}
              className="h-10"
              data-testid="stock-override-pin"
            />
            {overrideError && <p className="text-xs text-red-600">{overrideError}</p>}
            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={() => { setStockModal(false); setPendingSaleData(null); setOverridePin(''); setOverrideError(''); }}
                data-testid="stock-override-cancel">
                Cancel
              </Button>
              <Button className="flex-1 bg-amber-600 hover:bg-amber-700 text-white" onClick={handleStockOverride}
                disabled={overrideSubmitting || !overridePin.trim()} data-testid="stock-override-confirm">
                {overrideSubmitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <ShieldAlert size={14} className="mr-1.5" />}
                Override
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Print Order Slip Prompt */}
      <Dialog open={showPrintPrompt} onOpenChange={setShowPrintPrompt}>
        <DialogContent className="max-w-xs mx-auto">
          <DialogHeader>
            <DialogTitle className="text-center text-base font-bold" style={{ fontFamily: 'Manrope' }}>
              Sale Complete
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-center">
            <div className="w-14 h-14 rounded-full bg-emerald-100 flex items-center justify-center mx-auto">
              <Check size={28} className="text-emerald-600" />
            </div>
            <p className="text-sm text-slate-600">
              <span className="font-mono font-bold text-slate-800">{lastSaleData?.invoice_number}</span>
            </p>
            <p className="text-lg font-bold text-[#1A4D2E]">{formatPHP(lastSaleData?.grand_total || 0)}</p>
            <p className="text-xs text-slate-500">Would you like to print a receipt?</p>
            <div className="space-y-2 pt-1">
              <Button onClick={() => handleShowReceiptPreview('thermal')}
                className="w-full bg-[#1A4D2E] hover:bg-[#15412a] text-white h-11" data-testid="print-thermal-btn">
                <Printer size={16} className="mr-2" /> Print Receipt (58mm)
              </Button>
              <Button variant="outline" onClick={async () => {
                try {
                  await PrintBridge.print({ type: PrintEngine.getDocType(lastSaleData), data: lastSaleData, format: 'full_page', businessInfo, docCode: lastSaleData.doc_code || lastSaleData.invoice_number || '' });
                } catch (err) { toast.error(`Print failed: ${err.message || 'Unknown error'}`); }
                setShowPrintPrompt(false); setLastSaleData(null);
              }} className="w-full h-10" data-testid="print-full-btn">
                <Printer size={14} className="mr-2" /> Print Full Page
              </Button>
              {lastSaleData?.release_mode === 'partial' && lastSaleData?.doc_code && (
                <Button
                  variant="outline"
                  className="w-full h-10 border-amber-300 text-amber-700 hover:bg-amber-50"
                  onClick={() => { window.open(`/doc/${lastSaleData.doc_code}`, '_blank'); }}
                  data-testid="view-release-history-btn"
                >
                  View / Manage Stock Releases
                </Button>
              )}
              <Button variant="ghost" onClick={() => { setShowPrintPrompt(false); setLastSaleData(null); }}
                className="w-full text-slate-500" data-testid="skip-print-btn">
                Skip
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Receipt Preview — shows before actual print */}
      <Dialog open={!!receiptPreview} onOpenChange={(o) => { if (!o && !printingCopies) setReceiptPreview(null); }}>
        <DialogContent className="max-w-sm mx-auto p-0 overflow-hidden" style={{ maxHeight: '85vh' }}>
          <DialogHeader className="px-4 pt-4 pb-2">
            <DialogTitle className="text-sm font-bold flex items-center gap-2" style={{ fontFamily: 'Manrope' }} data-testid="receipt-preview-title">
              <Eye size={16} /> Receipt Preview
            </DialogTitle>
            <DialogDescription className="sr-only">Preview receipt before printing</DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto px-2" style={{ maxHeight: '55vh' }}>
            <div className="mx-auto bg-white border border-slate-200 rounded shadow-inner" style={{ width: '384px', maxWidth: '100%', transform: 'scale(0.85)', transformOrigin: 'top center' }}>
              {receiptPreview?.html && (
                <iframe
                  srcDoc={receiptPreview.html}
                  title="Receipt Preview"
                  className="w-full border-0"
                  style={{ width: '384px', minHeight: '400px', maxHeight: '600px', pointerEvents: 'none' }}
                  sandbox=""
                  data-testid="receipt-preview-iframe"
                />
              )}
            </div>
          </div>
          <div className="space-y-2 px-4 pb-4 pt-2 border-t bg-slate-50">
            <Button
              onClick={() => handlePrintCopies(1)}
              disabled={printingCopies}
              className="w-full bg-[#1A4D2E] hover:bg-[#15412a] text-white h-11"
              data-testid="print-1-copy-btn"
            >
              <Printer size={16} className="mr-2" /> {printingCopies ? 'Printing...' : 'Print 1 Copy'}
            </Button>
            <Button
              variant="outline"
              onClick={() => handlePrintCopies(2)}
              disabled={printingCopies}
              className="w-full h-10"
              data-testid="print-2-copies-btn"
            >
              <Printer size={14} className="mr-2" /> {printingCopies ? 'Printing...' : 'Print 2 Copies'}
            </Button>
            <Button
              variant="ghost"
              onClick={() => { setReceiptPreview(null); setShowPrintPrompt(false); setLastSaleData(null); }}
              disabled={printingCopies}
              className="w-full text-slate-500"
              data-testid="skip-preview-btn"
            >
              Skip
            </Button>
          </div>
        </DialogContent>
      </Dialog>

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
              onClick={() => handleScanQtyConfirm('add')}
              className="w-full bg-[#1A4D2E] hover:bg-[#15412a] text-white h-11"
              data-testid="scan-qty-confirm-btn"
            >
              <Check size={16} className="mr-2" /> Add to Cart
            </Button>
            <div className="flex flex-col gap-2">
              <Button
                variant="outline"
                onClick={() => handleScanQtyConfirm('skip_product')}
                className="w-full text-slate-600 h-10 text-xs"
                data-testid="scan-qty-skip-product-btn"
              >
                Skip — this product only (auto +1)
              </Button>
              <Button
                variant="outline"
                onClick={() => handleScanQtyConfirm('skip_all')}
                className="w-full text-amber-700 border-amber-300 hover:bg-amber-50 h-10 text-xs"
                data-testid="scan-qty-skip-all-btn"
              >
                Skip — all products this receipt (auto +1)
              </Button>
            </div>
            <p className="text-[10px] text-slate-400 text-center -mt-1">Skip adds 1 and removes prompts for this receipt</p>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
