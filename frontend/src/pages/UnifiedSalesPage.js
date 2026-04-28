import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import { ScrollArea } from '../components/ui/scroll-area';
import { Separator } from '../components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Switch } from '../components/ui/switch';
import SmartProductSearch from '../components/SmartProductSearch';
import { UnclosedDaysBanner } from '../components/UnclosedDaysBanner';
import {
  Search, Plus, Minus, Trash2, ShoppingCart, CreditCard, X, Wifi, WifiOff,
  RefreshCw, FileText, Lock, Zap, ClipboardList, AlertTriangle, Shield, CheckCircle2, Smartphone, Camera, Check,
  PackageX, ShieldAlert, ChevronDown
} from 'lucide-react';
import { toast } from 'sonner';
import {
  cacheProducts, getProducts, cacheCustomers, getCustomers,
  cachePriceSchemes, getPriceSchemes, addPendingSale, getPendingSaleCount
} from '../lib/offlineDB';
import { syncPendingSales, startAutoSync, stopAutoSync, newEnvelopeId } from '../lib/syncManager';
import ReferenceNumberPrompt from '../components/ReferenceNumberPrompt';
import CropCreditTypeDialog from '../components/CropCreditTypeDialog';
import RequestSignatureDialog from '../components/RequestSignatureDialog';
import { invalidateBalanceCache } from '../components/CustomerBalanceBadge';
import PrintEngine from '../lib/PrintEngine';

// ── Insufficient Stock Override Modal ────────────────────────────────────────
function InsufficientStockModal({ open, insufficientItems, onOverride, onCancel, onGoPO }) {
  const [pin, setPin] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleOverride = async () => {
    if (!pin.trim()) { setError('Manager PIN required'); return; }
    setSubmitting(true);
    setError('');
    try {
      await onOverride(pin.trim());
    } catch (e) {
      const d = e?.response?.data?.detail;
      setError(typeof d === 'string' ? d : d?.message || 'Invalid PIN — override denied');
    }
    setSubmitting(false);
  };

  return (
    <Dialog open={open} onOpenChange={() => {}}>
      <DialogContent className="max-w-md" data-testid="insufficient-stock-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-700">
            <PackageX size={18} className="text-amber-600" /> Insufficient Stock
          </DialogTitle>
          <DialogDescription>
            The following item(s) have less system stock than needed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 my-1">
          {(insufficientItems || []).map((item, i) => (
            <div key={i} className="flex items-center justify-between bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm">
              <span className="font-medium text-slate-800 truncate max-w-[60%]">{item.product_name}</span>
              <span className="text-amber-700 font-mono text-xs">
                System: {item.system_qty} · Need: {item.needed_qty}
              </span>
            </div>
          ))}
        </div>

        <Separator />

        <div className="space-y-3">
          <button
            onClick={onGoPO}
            className="w-full flex items-center gap-3 p-3 rounded-xl border border-blue-200 bg-blue-50 hover:bg-blue-100 transition-colors text-left"
            data-testid="go-encode-po-btn"
          >
            <FileText size={16} className="text-blue-600 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-blue-800">Encode / Receive a Purchase Order first</p>
              <p className="text-xs text-blue-600">Recommended — encode the missing PO to restore stock</p>
            </div>
          </button>

          <div className="p-3 rounded-xl border border-amber-200 bg-amber-50/50 space-y-2">
            <div className="flex items-center gap-2">
              <ShieldAlert size={15} className="text-amber-600 shrink-0" />
              <p className="text-sm font-semibold text-amber-800">Manager Override (Negative Stock)</p>
            </div>
            <p className="text-xs text-amber-700 leading-relaxed">
              Proceeds with sale. Inventory goes negative and a discrepancy ticket is auto-created for investigation.
            </p>
            <Input
              type="password"
              placeholder="Enter manager PIN"
              value={pin}
              onChange={e => { setPin(e.target.value); setError(''); }}
              onKeyDown={e => e.key === 'Enter' && handleOverride()}
              className="h-9 text-sm font-mono"
              data-testid="override-pin-input"
            />
            {error && (
              <p className="text-xs text-red-600 flex items-center gap-1" data-testid="override-pin-error">
                <AlertTriangle size={11} /> {error}
              </p>
            )}
            <Button
              onClick={handleOverride}
              disabled={submitting || !pin}
              className="w-full bg-amber-600 hover:bg-amber-700 text-white h-9 text-sm"
              data-testid="override-submit-btn"
            >
              {submitting ? <RefreshCw size={13} className="animate-spin mr-1" /> : <Shield size={13} className="mr-1" />}
              Proceed with Override
            </Button>
          </div>

          <button
            onClick={onCancel}
            className="w-full text-sm text-slate-500 hover:text-slate-700 py-1"
            data-testid="override-cancel-btn"
          >
            Cancel sale
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const EMPTY_LINE = {
  product_id: '', product_name: '', description: '',
  quantity: 1, rate: 0, original_rate: 0,
  cost_price: 0, moving_average_cost: 0, last_purchase_cost: 0,
  effective_capital: 0, capital_method: 'manual',
  discount_type: 'amount', discount_value: 0, is_repack: false,
};

// Returns today's date in YYYY-MM-DD using LOCAL time (not UTC), so it always
// shows the correct Philippine date regardless of server timezone.
const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export default function UnifiedSalesPage() {
  const { currentBranch, user, effectiveBranchId, hasPerm } = useAuth();
  
  // Permission flags for discount/price editing
  const canDiscount = hasPerm('sales', 'give_discount');
  const canSellBelowCost = hasPerm('sales', 'sell_below_cost');
  const canViewCost = hasPerm('products', 'view_cost');
  const canViewBalance = hasPerm('customers', 'view_balance');
  
  // Mode: 'quick' or 'order'
  const [mode, setMode] = useState('quick');

  // Main tab: 'sale' | 'history'
  const [mainTab, setMainTab] = useState('sale');

  // ── Sales History ────────────────────────────────────────────────────────
  const [historyDate, setHistoryDate] = useState(localToday());
  const [historySearch, setHistorySearch] = useState('');
  const [historyList, setHistoryList] = useState([]);
  const [historyTotals, setHistoryTotals] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedInvoice, setSelectedInvoice] = useState(null); // detail modal
  const selectInvoiceWithReceipts = async (inv) => {
    setSelectedInvoice(inv);
    // Load receipts for digital/split invoices
    if (inv && (inv.fund_source === 'digital' || inv.fund_source === 'split')) {
      try {
        const res = await api.get(`/uploads/record/invoice/${inv.id}`);
        const sessions = res.data?.sessions || [];
        const receipts = [];
        for (const s of sessions) {
          for (const f of (s.files || [])) {
            if (f.url) receipts.push({ url: f.url, name: f.original_name || f.name });
          }
        }
        setSelectedInvoice(prev => prev?.id === inv.id ? { ...prev, _receipts: receipts } : prev);
      } catch {}
    }
  };
  const [voidDialog, setVoidDialog] = useState(false);
  const [voidReason, setVoidReason] = useState('');
  const [voidPin, setVoidPin] = useState('');
  const [voidSaving, setVoidSaving] = useState(false);

  // ── Negative stock override modal ────────────────────────────────────────
  const [stockOverrideModal, setStockOverrideModal] = useState(false);
  const [insufficientItems, setInsufficientItems] = useState([]);
  const [pendingSaleData, setPendingSaleData] = useState(null);

  // ── Digital payment ───────────────────────────────────────────────────────
  const [digitalPlatform, setDigitalPlatform] = useState('GCash');
  const [digitalRefNumber, setDigitalRefNumber] = useState('');
  const [digitalSender, setDigitalSender] = useState('');
  const [digitalReceiptQR, setDigitalReceiptQR] = useState(null); // { invoice_id, token }
  const [showDigitalQR, setShowDigitalQR] = useState(false);

  // ── Persistent receipt upload tracking ──────────────────────────────────
  const PENDING_RECEIPT_KEY = 'agribooks_pending_receipt';

  // Save pending receipt to localStorage whenever dialog opens
  const showReceiptDialog = (data) => {
    setDigitalReceiptQR(data);
    setShowDigitalQR(true);
    try {
      localStorage.setItem(PENDING_RECEIPT_KEY, JSON.stringify({
        invoice_id: data.invoice_id,
        invoice_number: data.invoice_number,
      }));
    } catch {}
  };

  // Clear pending receipt from localStorage when upload completes
  const closeReceiptDialog = () => {
    setShowDigitalQR(false);
    setDigitalReceiptQR(null);
    try { localStorage.removeItem(PENDING_RECEIPT_KEY); } catch {}
  };

  // Poll for phone uploads — detects when QR-scanned phone completes upload
  useEffect(() => {
    if (!showDigitalQR || !digitalReceiptQR?.token || digitalReceiptQR?._uploaded) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/uploads/preview/${digitalReceiptQR.token}`);
        if (res.ok) {
          const data = await res.json();
          if (data.file_count > 0) {
            setDigitalReceiptQR(prev => prev ? ({ ...prev, _uploaded: true, _fileCount: data.file_count }) : prev);
            toast.success('Receipt uploaded from phone!');
          }
        }
      } catch {}
    }, 3000);
    return () => clearInterval(interval);
  }, [showDigitalQR, digitalReceiptQR?.token, digitalReceiptQR?._uploaded]);

  // ── Split payment (cash + digital) ────────────────────────────────────────
  const [splitCash, setSplitCash] = useState('');
  const [splitDigital, setSplitDigital] = useState('');
  
  // Products & Data
  const [allProducts, setAllProducts] = useState([]);
  const [filteredProducts, setFilteredProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [customers, setCustomers] = useState([]);
  const [schemes, setSchemes] = useState([]);
  const [terms, setTerms] = useState([]);
  const [prefixes, setPrefixes] = useState({});
  const [users, setUsers] = useState([]);
  
  // Cart/Lines
  const [cart, setCart] = useState([]);
  const [lines, setLines] = useState([{ ...EMPTY_LINE }]);
  
  // Customer
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [custSearch, setCustSearch] = useState('');
  const [custDropdownOpen, setCustDropdownOpen] = useState(false);
  const [newCustomerDialog, setNewCustomerDialog] = useState(false);
  const [newCustForm, setNewCustForm] = useState({ name: '', phone: '', address: '', price_scheme: 'retail' });
  
  // Order header
  const [header, setHeader] = useState({
    terms: 'COD', terms_days: 0, customer_po: '', sales_rep_id: '', sales_rep_name: '',
    prefix: 'SI', order_date: localToday(),
    shipping_address: '', location: '', mod: '', check_number: '', req_ship_date: '', notes: '',
  });
  const [freight, setFreight] = useState(0);
  const [overallDiscount, setOverallDiscount] = useState(0);

  // Closed-day enforcement
  const [lastCloseDate, setLastCloseDate] = useState(null);
  const [floorDate, setFloorDate] = useState(null); // earliest operational date — blocks dates before this
  const [dateError, setDateError] = useState(null); // inline error shown below the Sale Date field

  // Min selectable date for the date picker — day AFTER the last close date.
  // The browser natively greys out every date before this, making closed dates visually obvious.
  const minAllowedDate = useMemo(() => {
    if (lastCloseDate) {
      const d = new Date(lastCloseDate + 'T12:00:00');
      d.setDate(d.getDate() + 1);
      return d.toISOString().slice(0, 10);
    }
    return floorDate || undefined;
  }, [lastCloseDate, floorDate]);

  // Order header collapse
  const [headerCollapsed, setHeaderCollapsed] = useState(true);

  // Editable customer fields (overrides for this order)
  const [custEdits, setCustEdits] = useState({ phone: '', address: '', shipping_address: '' });
  const [custEdited, setCustEdited] = useState(false); // true if user changed customer info
  const [custSaveDialog, setCustSaveDialog] = useState(false); // "Save to record?" dialog
  
  // Default price scheme for walk-in customers
  const [defaultScheme, setDefaultScheme] = useState('retail');
  // Active scheme for the current transaction (may differ from customer's stored scheme)
  const [activeScheme, setActiveScheme] = useState('retail');
  // Scheme save dialog (when customer's scheme is overridden during a sale)
  const [schemeSaveDialog, setSchemeSaveDialog] = useState(false);
  const [pendingSchemeChange, setPendingSchemeChange] = useState(null);

  // Price save dialog
  const [priceSaveDialog, setPriceSaveDialog] = useState(false);
  const [pendingPriceChange, setPendingPriceChange] = useState(null);
  
  // Checkout
  const [checkoutDialog, setCheckoutDialog] = useState(false);
  const [paymentType, setPaymentType] = useState('cash'); // cash, partial, credit
  const [amountTendered, setAmountTendered] = useState(0);
  const [partialPayment, setPartialPayment] = useState(0);
  const [saving, setSaving] = useState(false);
  const [releaseMode, setReleaseMode] = useState('full'); // full | partial
  
  // Reference number prompt
  const [refPrompt, setRefPrompt] = useState({ open: false, number: '', title: '', invoiceData: null });

  // Business info for printing
  const [bizInfo, setBizInfo] = useState({});
  useEffect(() => { api.get('/settings/business-info').then(r => setBizInfo(r.data)).catch(() => {}); }, []);
  
  // Credit approval
  const [creditApprovalDialog, setCreditApprovalDialog] = useState(false);
  const [managerPin, setManagerPin] = useState('');
  const [creditCheckResult, setCreditCheckResult] = useState(null);
  const [pendingCreditSale, setPendingCreditSale] = useState(null);
  // Crop credit type selection
  const [cropTypeDialog, setCropTypeDialog] = useState(false);
  const [cropCreditConfig, setCropCreditConfig] = useState(null);
  // Customer signature flow (after credit sale)
  const [sigDialog, setSigDialog] = useState({ open: false, invoice: null });
  
  // Offline
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [pendingCount, setPendingCount] = useState(0);
  const [dataLoaded, setDataLoaded] = useState(false);

  // Linked Scanner
  const [scannerSession, setScannerSession] = useState(null); // { session_id, branch_id }
  const [scannerConnected, setScannerConnected] = useState(false);
  const [scannerQrOpen, setScannerQrOpen] = useState(false);
  const [scannerCreating, setScannerCreating] = useState(false);
  const addToCartRef = useRef(null); // ref to always call latest addToCart
  
  const searchRef = useRef(null);
  const qtyRefs = useRef([]);

  // ── Barcode Scanner Listener ─────────────────────────────────────────────
  // USB barcode scanners type characters rapidly and end with Enter.
  // We detect this pattern and look up the product by barcode.
  const scanBufferRef = useRef('');
  const scanTimerRef = useRef(null);

  const handleBarcodeScan = useCallback(async (barcode) => {
    if (!barcode || barcode.length < 3) return;
    // First check locally cached products
    const localMatch = allProducts.find(p => p.barcode === barcode);
    if (localMatch) {
      addToCart(localMatch);
      toast.success(`Scanned: ${localMatch.name}`);
      return;
    }
    // If not found locally, try API lookup
    try {
      const branchId = currentBranch?.id && currentBranch.id !== 'all' ? currentBranch.id : undefined;
      const res = await api.get(`/products/barcode-lookup/${encodeURIComponent(barcode)}`, {
        params: branchId ? { branch_id: branchId } : {}
      });
      if (res.data) {
        addToCart(res.data);
        toast.success(`Scanned: ${res.data.name}`);
      }
    } catch {
      toast.error(`No product found for barcode: ${barcode}`);
    }
  }, [allProducts, currentBranch]); // eslint-disable-line

  useEffect(() => {
    const handleKeyPress = (e) => {
      // Ignore if user is typing in an input/textarea (except the main search)
      const tag = e.target.tagName;
      const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
      // Allow scan capture even when focused on search input
      const isSearchInput = e.target.getAttribute('data-testid') === 'product-search-input';

      if (e.key === 'Enter' && scanBufferRef.current.length >= 3) {
        e.preventDefault();
        const barcode = scanBufferRef.current.trim();
        scanBufferRef.current = '';
        clearTimeout(scanTimerRef.current);
        handleBarcodeScan(barcode);
        return;
      }

      // Only capture printable characters, not in other inputs
      if (e.key.length === 1 && (!isInput || isSearchInput)) {
        scanBufferRef.current += e.key;
        clearTimeout(scanTimerRef.current);
        // Scanner sends all chars within ~50ms, clear buffer if idle for 100ms
        scanTimerRef.current = setTimeout(() => { scanBufferRef.current = ''; }, 100);
      }
    };

    window.addEventListener('keydown', handleKeyPress);
    return () => {
      window.removeEventListener('keydown', handleKeyPress);
      clearTimeout(scanTimerRef.current);
    };
  }, [handleBarcodeScan]);

  // ── Linked Scanner — REST polling (reliable) ───────────────────────────────
  const API_URL = process.env.REACT_APP_BACKEND_URL;
  const scanPollIndexRef = useRef(0);

  const createScannerSession = async () => {
    if (!currentBranch?.id || currentBranch.id === 'all') {
      toast.error('Select a specific branch to link a scanner');
      return;
    }
    setScannerCreating(true);
    try {
      const res = await api.post('/scanner/create-session', { branch_id: currentBranch.id });
      const { session_id, branch_id } = res.data;
      setScannerSession({ session_id, branch_id });
      scanPollIndexRef.current = 0;
      setScannerQrOpen(true);
    } catch (e) {
      toast.error('Failed to create scanner session');
    }
    setScannerCreating(false);
  };

  const closeScannerSession = async () => {
    if (scannerSession) {
      try { await api.post(`/scanner/close-session/${scannerSession.session_id}`); } catch {}
    }
    setScannerSession(null);
    setScannerConnected(false);
    setScannerQrOpen(false);
    scanPollIndexRef.current = 0;
  };

  // Poll for new scans every 1.5s
  useEffect(() => {
    if (!scannerSession) return;
    const interval = setInterval(async () => {
      try {
        const res = await api.get(`/scanner/scans/${scannerSession.session_id}`, {
          params: { after: scanPollIndexRef.current }
        });
        const { scans, total, status } = res.data;

        // Update connected status
        if (status === 'connected' && !scannerConnected) {
          setScannerConnected(true);
        }

        // Process new scans
        if (scans && scans.length > 0) {
          for (const scan of scans) {
            if (scan.found && scan.product) {
              if (addToCartRef.current) addToCartRef.current(scan.product);
              toast.success(`Scanned: ${scan.product.name}`);
            } else if (!scan.found) {
              toast.error(`No product for barcode: ${scan.barcode}`);
            }
          }
          scanPollIndexRef.current = total;
        }
      } catch {}
    }, 1500);
    return () => clearInterval(interval);
  }, [scannerSession, scannerConnected]); // eslint-disable-line



  // Online/Offline detection
  useEffect(() => {
    const goOnline = async () => {
      setIsOnline(true);
      toast.success('Back online! Syncing...');
      const result = await syncPendingSales();
      if (result?.synced > 0) toast.success(`${result.synced} sale(s) synced!`);
      const count = await getPendingSaleCount();
      setPendingCount(count);
      await loadData(true);
    };
    const goOffline = () => {
      setIsOnline(false);
      toast('Offline Mode - Sales saved locally', { duration: 4000 });
    };
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    startAutoSync();
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
      stopAutoSync();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadData = async (forceOnline = false) => {
    const online = forceOnline || navigator.onLine;
    if (online) {
      try {
        // Use effectiveBranchId (always available from localStorage/user data)
        // currentBranch depends on branches[] loading first and may be null
        const branchId = currentBranch?.id || (effectiveBranchId && effectiveBranchId !== 'all' ? effectiveBranchId : null);
        const branchParams = branchId ? { branch_id: branchId } : {};
        const [posRes, custRes, termRes, prefixRes, userRes, schemeRes] = await Promise.all([
          api.get('/sync/pos-data', { params: branchParams }),
          api.get('/customers', { params: { limit: 500, ...branchParams } }),
          api.get('/settings/terms-options').catch(() => ({ data: [] })),
          api.get('/settings/invoice-prefixes').catch(() => ({ data: {} })),
          api.get('/users').catch(() => ({ data: [] })),
          api.get('/price-schemes').catch(() => ({ data: [] })),
        ]);
        setAllProducts(posRes.data.products);
        setCustomers(custRes.data.customers || posRes.data.customers);
        setSchemes(schemeRes.data || posRes.data.price_schemes);
        setTerms(termRes.data || []);
        setPrefixes(prefixRes.data || {});
        setUsers(userRes.data || []);
        await Promise.all([
          cacheProducts(posRes.data.products),
          cacheCustomers(custRes.data.customers || posRes.data.customers),
          cachePriceSchemes(schemeRes.data || posRes.data.price_schemes),
        ]);
        setDataLoaded(true);
        return;
      } catch (e) { console.warn('API failed, using offline cache'); }
    }
    const [prods, custs, schs] = await Promise.all([getProducts(), getCustomers(), getPriceSchemes()]);
    setAllProducts(prods);
    setCustomers(custs);
    setSchemes(schs);
    setDataLoaded(true);
  };

  // Load data on mount and reload whenever branch changes
  // effectiveBranchId is available immediately (from localStorage/user.branch_id)
  // while currentBranch requires branches[] to be loaded first
  useEffect(() => {
    loadData();
    getPendingSaleCount().then(setPendingCount);
  }, [effectiveBranchId]); // eslint-disable-line

  // ── Recover pending receipt upload on mount ────────────────────────────
  useEffect(() => {
    const checkPendingReceipt = async () => {
      try {
        const stored = localStorage.getItem(PENDING_RECEIPT_KEY);
        if (!stored) return;
        const { invoice_id, invoice_number } = JSON.parse(stored);
        if (!invoice_id) return;
        // Verify with backend that receipt is still pending
        const res = await api.get('/pending-receipt-uploads');
        const pending = (res.data || []).find(inv => inv.id === invoice_id);
        if (pending) {
          // Re-show the dialog — receipt was never uploaded
          setDigitalReceiptQR({ invoice_id, invoice_number: invoice_number || pending.invoice_number });
          setShowDigitalQR(true);
        } else {
          // Receipt was already uploaded or invoice doesn't exist anymore
          localStorage.removeItem(PENDING_RECEIPT_KEY);
        }
      } catch {
        // If API fails, still show based on localStorage (safer to require upload)
        try {
          const stored = localStorage.getItem(PENDING_RECEIPT_KEY);
          if (stored) {
            const { invoice_id, invoice_number } = JSON.parse(stored);
            if (invoice_id) {
              setDigitalReceiptQR({ invoice_id, invoice_number });
              setShowDigitalQR(true);
            }
          }
        } catch {}
      }
    };
    checkPendingReceipt();
  }, []); // eslint-disable-line

  // ── Prevent page close/refresh while receipt upload is pending ──────────
  useEffect(() => {
    if (!showDigitalQR) return;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = 'You must upload the e-payment receipt before leaving. Your sale is recorded but the receipt is required.';
      return e.returnValue;
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [showDigitalQR]);

  // Load history when tab becomes active or date/search changes
  const loadHistory = useCallback(async () => {
    if (!isOnline) return;
    setHistoryLoading(true);
    try {
      const params = { date: historyDate, include_voided: true };
      if (currentBranch?.id && currentBranch.id !== 'all') params.branch_id = currentBranch.id;
      if (historySearch) params.search = historySearch;
      const res = await api.get('/invoices/history/by-date', { params });
      setHistoryList(res.data.invoices || []);
      setHistoryTotals(res.data.totals || null);
    } catch { toast.error('Failed to load sales history'); }
    setHistoryLoading(false);
  }, [historyDate, historySearch, isOnline, currentBranch?.id]); // eslint-disable-line

  useEffect(() => {
    if (mainTab === 'history') loadHistory();
  }, [mainTab, loadHistory]);

  const handleVoidInvoice = async () => {
    if (!voidReason.trim()) { toast.error('Please enter a reason'); return; }
    if (!voidPin) { toast.error('Manager PIN required'); return; }
    setVoidSaving(true);
    try {
      const res = await api.post(`/invoices/${selectedInvoice.id}/void`, {
        reason: voidReason,
        manager_pin: voidPin,
      });
      toast.success(`${selectedInvoice.invoice_number} voided — authorized by ${res.data.authorized_by}`);
      const snap = res.data.snapshot;
      setVoidDialog(false);
      setVoidReason('');
      setVoidPin('');
      setSelectedInvoice(null);
      loadHistory();
      // Auto-reopen: switch to New Sale with original items pre-filled
      reopenAsSale(snap);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Void failed');
    }
    setVoidSaving(false);
  };

  const reopenAsSale = (snapshot) => {
    // Switch to new sale tab and pre-fill
    setMainTab('sale');
    setMode('order');
    // Pre-fill header with original date (preserves interest calculation)
    setHeader(h => ({
      ...h,
      order_date: snapshot.invoice_date || snapshot.order_date || h.order_date,
      terms: snapshot.terms || 'COD',
      terms_days: snapshot.terms_days || 0,
    }));
    // Pre-fill customer — properly set selectedCustomer object, not just search text
    if (snapshot.customer_id) {
      const matchedCustomer = customers.find(c => c.id === snapshot.customer_id);
      if (matchedCustomer) {
        setSelectedCustomer(matchedCustomer);
        setCustSearch(matchedCustomer.name);
      } else {
        // Customer not in local list — create a minimal customer object from snapshot
        const snapCustomer = {
          id: snapshot.customer_id,
          name: snapshot.customer_name || '',
          phone: snapshot.customer_contact || '',
          address: '',
          price_scheme: 'retail',
          balance: 0,
          credit_limit: 0,
          interest_rate: snapshot.interest_rate || 0,
        };
        setSelectedCustomer(snapCustomer);
        setCustSearch(snapshot.customer_name || '');
      }
    }
    // Pre-fill lines from snapshot items
    const newLines = (snapshot.items || []).map(item => ({
      product_id: item.product_id || '',
      product_name: item.product_name || item.name || '',
      description: item.description || '',
      quantity: item.quantity || 1,
      rate: item.rate || item.price || 0,
      original_rate: item.rate || item.price || 0,
      cost_price: item.cost_price || 0,
      moving_average_cost: 0,
      last_purchase_cost: 0,
      effective_capital: item.cost_price || 0,
      capital_method: 'manual',
      discount_type: item.discount_type || 'amount',
      discount_value: item.discount_value || 0,
      is_repack: item.is_repack || false,
    }));
    setLines(newLines.length ? newLines : [{ ...EMPTY_LINE }]);
    toast.success('Sale re-opened — original date preserved for interest calculation');
  };

  // ── Mode switching with item transfer ────────────────────────────────────
  const switchMode = (newMode) => {
    if (newMode === mode) return;

    if (newMode === 'order' && cart.length > 0) {
      // Quick → Order: copy cart items into order lines
      const newLines = cart.map(c => ({
        product_id: c.product_id,
        product_name: c.product_name,
        description: '',
        quantity: c.quantity,
        rate: c.price,
        original_rate: c.original_price ?? c.price,
        cost_price: c.cost_price || 0,
        moving_average_cost: c.moving_average_cost || 0,
        last_purchase_cost: c.last_purchase_cost || 0,
        effective_capital: c.effective_capital || c.cost_price || 0,
        capital_method: c.capital_method || 'manual',
        discount_type: 'amount',
        discount_value: 0,
        is_repack: c.is_repack || false,
      }));
      newLines.push({ ...EMPTY_LINE }); // trailing empty row
      setLines(newLines);
    }

    if (newMode === 'quick') {
      const filledLines = lines.filter(l => l.product_id);
      const hasDiscount = filledLines.some(l => l.discount_value > 0);
      if (hasDiscount) {
        toast.error('Cannot switch to Quick mode — per-line discounts exist. Remove them or stay in Order mode.');
        return;
      }
      if (filledLines.length > 0) {
        // Order → Quick: copy lines into cart
        const newCart = filledLines.map(l => ({
          product_id: l.product_id,
          product_name: l.product_name,
          sku: '',
          price: l.rate,
          quantity: l.quantity,
          total: l.quantity * l.rate,
          unit: '',
          is_repack: l.is_repack || false,
          cost_price: l.cost_price || 0,
          moving_average_cost: l.moving_average_cost || 0,
          last_purchase_cost: l.last_purchase_cost || 0,
          effective_capital: l.effective_capital || l.cost_price || 0,
          capital_method: l.capital_method || 'manual',
          original_price: l.original_rate ?? l.rate,
        }));
        setCart(newCart);
      }
    }

    setMode(newMode);
  };

  // Filter products
  useEffect(() => {
    if (!search) { setFilteredProducts(allProducts); return; }
    const q = search.toLowerCase();
    setFilteredProducts(allProducts.filter(p =>
      p.name.toLowerCase().includes(q) || p.sku?.toLowerCase().includes(q) || (p.barcode && p.barcode.includes(q))
    ));
  }, [search, allProducts]);

  const filteredCusts = custSearch 
    ? customers.filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase())).slice(0, 8) 
    : [];

  const getPriceForCustomer = (product) => {
    // Use activeScheme — unified for both customer and walk-in
    return product.prices?.[activeScheme] ?? 0;
  };

  // Quick mode: Add to cart
  const addToCart = (product) => {
    const price = getPriceForCustomer(product);
    setCart(prev => {
      const existing = prev.find(c => c.product_id === product.id);
      if (existing) {
        return prev.map(c => c.product_id === product.id ? { ...c, quantity: c.quantity + 1, total: (c.quantity + 1) * c.price } : c);
      }
      return [...prev, {
        product_id: product.id, product_name: product.name, sku: product.sku,
        price, quantity: 1, total: price, unit: product.unit, is_repack: product.is_repack,
        cost_price: product.cost_price || 0,
        moving_average_cost: product.moving_average_cost || 0,
        last_purchase_cost: product.last_purchase_cost || 0,
        effective_capital: product.effective_capital || product.cost_price || 0,
        capital_method: product.capital_method || 'manual',
        original_price: price,
      }];
    });
  };
  // Keep ref in sync so WebSocket handler always calls latest addToCart
  addToCartRef.current = addToCart;

  const updateQty = (productId, delta) => {
    setCart(cart.map(c => {
      if (c.product_id !== productId) return c;
      const newQty = Math.max(0, c.quantity + delta);
      return newQty === 0 ? null : { ...c, quantity: newQty, total: newQty * c.price };
    }).filter(Boolean));
  };

  const setCartQty = (productId, qty) => {
    const str = String(qty);
    // Allow intermediate decimal input: "0.", "1.", ".5", ""
    if (str === '' || str.endsWith('.') || str === '.') {
      setCart(cart.map(c => c.product_id !== productId ? c : { ...c, _qtyStr: str }));
      return;
    }
    const newQty = Math.max(0, parseFloat(str) || 0);
    setCart(cart.map(c => c.product_id !== productId ? c : { ...c, quantity: newQty, total: newQty * c.price, _qtyStr: undefined }));
  };

  const updateCartPrice = (productId, newPrice) => {
    const price = parseFloat(newPrice) || 0;
    setCart(cart.map(c => c.product_id !== productId ? c : { ...c, price, total: price * c.quantity }));
  };

  const removeFromCart = (productId) => setCart(cart.filter(c => c.product_id !== productId));
  const clearCart = () => {
    setCart([]); setLines([{ ...EMPTY_LINE }]); setSelectedCustomer(null); setCustSearch('');
    setActiveScheme(defaultScheme);
    setCustEdits({ phone: '', address: '', shipping_address: '' });
    setCustEdited(false);
    setHeader(h => ({ ...h, shipping_address: '', location: '', mod: '', check_number: '', req_ship_date: '', notes: '', customer_po: '' }));
  };

  // Apply a scheme change: updates activeScheme and reprices all open cart/line items
  const applySchemeChange = (scheme) => {
    setActiveScheme(scheme);
    if (allProducts.length > 0) {
      setCart(prev => prev.map(c => {
        const product = allProducts.find(p => p.id === c.product_id);
        if (!product) return c;
        const newPrice = product.prices?.[scheme] ?? 0;
        return { ...c, price: newPrice, original_price: newPrice, total: newPrice * c.quantity };
      }));
      setLines(prev => prev.map(l => {
        if (!l.product_id) return l;
        const product = allProducts.find(p => p.id === l.product_id);
        if (!product) return l;
        const newRate = product.prices?.[scheme] ?? 0;
        return { ...l, rate: newRate, original_rate: newRate };
      }));
    }
  };

  // Handle scheme selection: unified for both walk-in and customer
  const handleSchemeChange = (newScheme) => {
    if (!selectedCustomer) {
      // Walk-in: update both active and default (session preference)
      setDefaultScheme(newScheme);
      applySchemeChange(newScheme);
    } else if (newScheme !== selectedCustomer.price_scheme) {
      // Customer with a different scheme: apply for this sale, ask to save
      applySchemeChange(newScheme);
      setPendingSchemeChange({ newScheme });
      setSchemeSaveDialog(true);
    } else {
      // Same as stored scheme: just apply
      applySchemeChange(newScheme);
    }
  };

  // Persist the scheme change to the customer record
  const saveSchemeToCustomer = async () => {
    if (!pendingSchemeChange || !selectedCustomer) return;
    try {
      await api.put(`/customers/${selectedCustomer.id}`, { price_scheme: pendingSchemeChange.newScheme });
      const updated = { ...selectedCustomer, price_scheme: pendingSchemeChange.newScheme };
      setSelectedCustomer(updated);
      setCustomers(prev => prev.map(c => c.id === selectedCustomer.id ? updated : c));
      const schemeName = schemes.find(s => s.key === pendingSchemeChange.newScheme)?.name || pendingSchemeChange.newScheme;
      toast.success(`${selectedCustomer.name}'s price scheme updated to ${schemeName}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to update customer scheme');
    }
    setSchemeSaveDialog(false);
    setPendingSchemeChange(null);
  };

  const clearLine = (index) => {
    const newLines = [...lines];
    newLines[index] = { ...EMPTY_LINE };
    setLines(newLines);
  };

  const triggerPriceSaveDialog = (productId, productName, oldPrice, newPrice) => {
    if (!productId || newPrice === oldPrice || newPrice <= 0) return;
    // Use activeScheme — this respects any override the user has applied this sale
    const schemeName = schemes.find(s => s.key === activeScheme)?.name || activeScheme;
    setPendingPriceChange({ product_id: productId, product_name: productName, old_price: oldPrice, new_price: newPrice, scheme_key: activeScheme, scheme_name: schemeName });
    setPriceSaveDialog(true);
  };

  const dismissPriceSaveDialog = () => {
    if (pendingPriceChange) {
      // Update originals so dialog doesn't retrigger on next blur
      setLines(lines.map(l => l.product_id === pendingPriceChange.product_id
        ? { ...l, original_rate: pendingPriceChange.new_price } : l
      ));
      setCart(cart.map(c => c.product_id === pendingPriceChange.product_id
        ? { ...c, original_price: pendingPriceChange.new_price } : c
      ));
    }
    setPriceSaveDialog(false);
    setPendingPriceChange(null);
  };

  const savePriceToScheme = async () => {
    if (!pendingPriceChange) return;
    try {
      await api.put(`/products/${pendingPriceChange.product_id}/update-price`, {
        scheme: pendingPriceChange.scheme_key,
        price: pendingPriceChange.new_price,
      });
      toast.success(`${pendingPriceChange.scheme_name} price updated to ₱${pendingPriceChange.new_price.toFixed(2)}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save price');
    }
    dismissPriceSaveDialog();
  };

  const handleRateBlur = (line) => {
    if (line.product_id && line.rate !== line.original_rate) {
      triggerPriceSaveDialog(line.product_id, line.product_name, line.original_rate, line.rate);
    }
  };

  // Order mode: Handle lines
  const handleProductSelect = (index, product) => {
    const newLines = [...lines];
    const rate = product.prices?.[activeScheme] ?? 0;
    newLines[index] = {
      ...newLines[index], product_id: product.id, product_name: product.name,
      description: product.description || '', rate, original_rate: rate,
      cost_price: product.cost_price || 0,
      moving_average_cost: product.moving_average_cost || 0,
      last_purchase_cost: product.last_purchase_cost || 0,
      effective_capital: product.effective_capital || product.cost_price || 0,
      capital_method: product.capital_method || 'manual',
      is_repack: product.is_repack || false,
    };
    if (index === lines.length - 1) newLines.push({ ...EMPTY_LINE });
    setLines(newLines);
    setTimeout(() => qtyRefs.current[index]?.focus(), 50);
  };

  const updateLine = (index, field, value) => {
    const newLines = [...lines];
    newLines[index] = { ...newLines[index], [field]: value };
    setLines(newLines);
  };

  const removeLine = (index) => {
    if (lines.length <= 1) return;
    setLines(lines.filter((_, i) => i !== index));
  };

  const lineTotal = (line) => {
    const base = line.quantity * line.rate;
    const disc = line.discount_type === 'percent' ? base * line.discount_value / 100 : line.discount_value;
    return Math.max(0, base - disc);
  };

  // Customer selection
  const selectCustomer = (custId) => {
    const c = customers.find(x => x.id === custId);
    if (c) {
      setSelectedCustomer(c);
      setCustSearch(c.name);
      setCustDropdownOpen(false);
      setCustEdits({ phone: c.phone || '', address: c.address || '', shipping_address: '' });
      setCustEdited(false);
      applySchemeChange(c.price_scheme || 'retail'); // Reprice cart for customer's scheme
    }
  };

  const handleCustInput = (val) => {
    setCustSearch(val);
    setCustDropdownOpen(val.length > 0);
    const match = customers.find(c => c.name.toLowerCase() === val.toLowerCase());
    if (match) selectCustomer(match.id);
    else setSelectedCustomer(null);
  };

  // Create new customer
  const openNewCustomerDialog = () => {
    setNewCustForm({ name: custSearch, phone: '', address: '', price_scheme: 'retail' });
    setCustDropdownOpen(false);
    setNewCustomerDialog(true);
  };

  const createNewCustomer = async () => {
    if (!newCustForm.name.trim()) { toast.error('Customer name is required'); return; }
    try {
      const res = await api.post('/customers', {
        ...newCustForm,
        branch_id: currentBranch?.id,
      });
      // Add to local customers list
      setCustomers([...customers, res.data]);
      // Select the new customer and apply their scheme
      setSelectedCustomer(res.data);
      setCustSearch(res.data.name);
      applySchemeChange(res.data.price_scheme || 'retail');
      setNewCustomerDialog(false);
      toast.success('Customer created!');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to create customer');
    }
  };

  // Calculations
  const items = mode === 'quick' ? cart : lines.filter(l => l.product_id);
  const subtotal = mode === 'quick' 
    ? cart.reduce((s, c) => s + c.total, 0)
    : lines.reduce((s, l) => s + lineTotal(l), 0);
  const grandTotal = subtotal + freight - overallDiscount;
  const balanceDue = paymentType === 'cash' ? 0 : (paymentType === 'partial' ? grandTotal - partialPayment : grandTotal);
  const change = paymentType === 'cash' ? amountTendered - grandTotal : 0;

  // Check credit limit
  const checkCreditLimit = async () => {
    if (!selectedCustomer) return { allowed: true };
    
    const currentBalance = selectedCustomer.balance || 0;
    const creditLimit = selectedCustomer.credit_limit || 0;
    const newTotal = currentBalance + balanceDue;
    
    if (creditLimit > 0 && newTotal > creditLimit) {
      return {
        allowed: false,
        currentBalance,
        creditLimit,
        newTotal,
        exceededBy: newTotal - creditLimit,
      };
    }
    return { allowed: true, currentBalance, creditLimit, newTotal };
  };

  // Open checkout
  const openCheckout = () => {
    if (items.length === 0) { toast.error('Add items first'); return; }
    if (!currentBranch) { toast.error('Select a branch'); return; }

    // Check for zero-price items (no price set for selected scheme)
    const zeroPriceItem = mode === 'quick'
      ? cart.find(c => c.price <= 0)
      : lines.find(l => l.product_id && l.rate <= 0);
    if (zeroPriceItem) {
      toast.error(`"${zeroPriceItem.product_name}" has no price — edit the price directly on the receipt before checkout`);
      return;
    }

    // Check for below-capital items — uses effective_capital (respects product's capital_method)
    // Only block if user does NOT have sell_below_cost permission
    if (!canSellBelowCost) {
      const belowCostItem = mode === 'quick'
        ? cart.find(c => (c.effective_capital || c.cost_price) > 0 && c.price < (c.effective_capital || c.cost_price))
        : lines.find(l => l.product_id && (l.effective_capital || l.cost_price) > 0 && l.rate > 0 && l.rate < (l.effective_capital || l.cost_price));
      if (belowCostItem) {
        const p = belowCostItem.price ?? belowCostItem.rate;
        const cap = belowCostItem.effective_capital || belowCostItem.cost_price;
        const method = (belowCostItem.capital_method || 'manual').replace('_', ' ');
        toast.error(`Cannot sell "${belowCostItem.product_name}" at ₱${p.toFixed(2)} — below capital ₱${cap.toFixed(2)} (${method})`);
        return;
      }

      // Check for discounts that push net price below capital (Order mode only)
      if (mode === 'order') {
        const discountBelowCap = lines.find(l => {
          if (!l.product_id || l.discount_value <= 0) return false;
          const cap = l.effective_capital || l.cost_price;
          if (cap <= 0) return false;
          const netTotal = lineTotal(l);
          const netPerUnit = l.quantity > 0 ? netTotal / l.quantity : 0;
          return netPerUnit < cap;
        });
        if (discountBelowCap) {
          const cap = discountBelowCap.effective_capital || discountBelowCap.cost_price;
          const netPerUnit = discountBelowCap.quantity > 0 ? lineTotal(discountBelowCap) / discountBelowCap.quantity : 0;
          toast.error(`Cannot sell "${discountBelowCap.product_name}" — after discount, net ₱${netPerUnit.toFixed(2)}/unit is below capital ₱${cap.toFixed(2)}`);
          return;
        }
      }
    }

    setPaymentType('cash');
    setAmountTendered(grandTotal);
    setPartialPayment(0);
    setReleaseMode('full');

    // If customer info was edited, ask whether to save first
    if (selectedCustomer && custEdited) {
      setCustSaveDialog(true);
      return;
    }

    setCheckoutDialog(true);
  };

  // After customer save dialog choice, proceed to checkout
  const proceedToCheckoutAfterCustSave = () => {
    setCustSaveDialog(false);
    setCheckoutDialog(true);
  };

  const saveCustomerEditsAndCheckout = async () => {
    if (!selectedCustomer) { proceedToCheckoutAfterCustSave(); return; }
    try {
      const update = {};
      if (custEdits.phone !== (selectedCustomer.phone || '')) update.phone = custEdits.phone;
      if (custEdits.address !== (selectedCustomer.address || '')) update.address = custEdits.address;
      if (Object.keys(update).length > 0) {
        const res = await api.put(`/customers/${selectedCustomer.id}`, update);
        const updated = { ...selectedCustomer, ...update };
        setSelectedCustomer(updated);
        setCustomers(prev => prev.map(c => c.id === selectedCustomer.id ? { ...c, ...update } : c));
        toast.success(`${selectedCustomer.name}'s info updated`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to update customer');
    }
    setCustEdited(false);
    proceedToCheckoutAfterCustSave();
  };

  // Handle credit sale with approval
  const handleCreditSale = async () => {
    // Cash and digital sales — proceed directly (no credit involved)
    if (paymentType === 'cash' || paymentType === 'digital') {
      await processSale();
      return;
    }

    // Split: only cash portion involved, digital portion tracked separately — proceed
    if (paymentType === 'split') {
      await processSale();
      return;
    }

    // Credit or Partial — ask if By Term or Charged to Crop (only when customer is selected)
    if ((paymentType === 'credit' || paymentType === 'partial') && selectedCustomer?.id) {
      setPendingCreditSale({ paymentType, partialPayment, amountTendered });
      setCheckoutDialog(false);
      setCropTypeDialog(true);
      return;
    }

    // Credit without a customer — proceed to PIN directly
    setPendingCreditSale({ paymentType, partialPayment, amountTendered });
    setCheckoutDialog(false);
    setCreditApprovalDialog(true);
  };

  // Handle crop type confirmed — proceed to PIN approval
  const handleCropTypeConfirmed = (config) => {
    setCropCreditConfig(config);
    setCropTypeDialog(false);
    setCreditApprovalDialog(true);
  };

  // Verify manager PIN
  const verifyManagerPin = async () => {
    if (!managerPin) { toast.error('Enter authorization code'); return; }
    
    try {
      const customerName = selectedCustomer?.name || 'Walk-in';
      const res = await api.post('/auth/verify-manager-pin', {
        pin: managerPin.trim(),
        action_key: 'credit_sale_approval',
        context: {
          type: 'credit_sale',
          description: `₱${grandTotal.toFixed(2)} ${paymentType} sale to ${customerName}`,
          amount: grandTotal,
          customer_name: customerName,
          payment_type: paymentType,
          branch_id: currentBranch?.id,
          branch_name: currentBranch?.name,
        }
      });
      if (res.data.valid) {
        toast.success(`Approved by ${res.data.manager_name}`);
        setCreditApprovalDialog(false);
        setManagerPin('');
        await processSale(res.data.manager_name);
      } else {
        toast.error(res.data.detail || 'Invalid PIN / TOTP — check Settings > Security for accepted methods');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Verification failed — check your connection');
    }
  };

  // Process the sale
  const processSale = async (approvedBy = null) => {
    setSaving(true);
    
    const actualPaymentType = pendingCreditSale?.paymentType || paymentType;
    const actualPartial = pendingCreditSale?.partialPayment || partialPayment;
    const actualTendered = pendingCreditSale?.amountTendered || amountTendered;
    
    const saleId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const envelopeId = newEnvelopeId(); // separate idempotency key for resilient sync
    const today = localToday();
    
    // Calculate amounts
    const splitCashAmt = parseFloat(splitCash || 0);
    const splitDigitalAmt = parseFloat(splitDigital || 0);
    const amountPaid = actualPaymentType === 'cash' ? grandTotal
      : actualPaymentType === 'digital' ? grandTotal
      : actualPaymentType === 'split' ? (splitCashAmt + splitDigitalAmt)
      : actualPaymentType === 'partial' ? actualPartial
      : 0;
    const balance = grandTotal - amountPaid;
    
    // Prepare items
    const saleItems = mode === 'quick' 
      ? cart.map(c => ({
          product_id: c.product_id, product_name: c.product_name, sku: c.sku,
          quantity: c.quantity, rate: c.price, price: c.price, total: c.total,
          discount_type: 'amount', discount_value: 0, discount_amount: 0,
          is_repack: c.is_repack || false,
        }))
      : lines.filter(l => l.product_id).map(l => ({
          product_id: l.product_id, product_name: l.product_name,
          description: l.description, quantity: l.quantity, rate: l.rate,
          discount_type: l.discount_type, discount_value: l.discount_value,
          discount_amount: l.discount_type === 'percent' ? l.quantity * l.rate * l.discount_value / 100 : l.discount_value,
          total: lineTotal(l), is_repack: l.is_repack || false,
        }));

    const saleData = {
      id: saleId,
      envelope_id: envelopeId,
      branch_id: currentBranch.id,
      customer_id: selectedCustomer?.id || null,
      customer_name: selectedCustomer?.name || custSearch || 'Walk-in',
      customer_contact: selectedCustomer ? (custEdits.phone || selectedCustomer.phone || '') : '',
      customer_phone: selectedCustomer ? (custEdits.phone || selectedCustomer.phone || '') : '',
      customer_address: selectedCustomer ? (custEdits.address || selectedCustomer.address || '') : '',
      items: saleItems,
      subtotal,
      freight,
      overall_discount: overallDiscount,
      grand_total: grandTotal,
      amount_paid: amountPaid,
      balance,
      terms: header.terms,
      terms_days: header.terms_days,
      customer_po: header.customer_po,
      sales_rep_id: header.sales_rep_id,
      sales_rep_name: header.sales_rep_name,
      prefix: header.prefix,
      order_date: header.order_date,
      invoice_date: header.order_date,
      // extra order mode fields
      shipping_address: header.shipping_address || undefined,
      location: header.location || undefined,
      mod: header.mod || undefined,
      check_number: header.check_number || undefined,
      req_ship_date: header.req_ship_date || undefined,
      notes: header.notes || undefined,
      // Digital payment routing
      payment_method: actualPaymentType === 'digital' ? digitalPlatform
        : actualPaymentType === 'split' ? 'Split'
        : actualPaymentType === 'cash' ? 'Cash'
        : actualPaymentType === 'partial' ? 'Partial'
        : 'Credit',
      payment_type: actualPaymentType,
      fund_source: actualPaymentType === 'digital' ? 'digital' : actualPaymentType === 'split' ? 'split' : 'cashier',
      digital_platform: (actualPaymentType === 'digital' || actualPaymentType === 'split') ? digitalPlatform : undefined,
      digital_ref_number: (actualPaymentType === 'digital' || actualPaymentType === 'split') ? digitalRefNumber : undefined,
      digital_sender: (actualPaymentType === 'digital' || actualPaymentType === 'split') ? digitalSender : undefined,
      cash_amount: actualPaymentType === 'split' ? parseFloat(splitCash || 0) : undefined,
      digital_amount: actualPaymentType === 'split' ? parseFloat(splitDigital || 0) : undefined,
      sale_type: 'walk_in',
      mode: mode,
      approved_by: approvedBy,
      interest_rate: selectedCustomer?.interest_rate || 0,
      cashier_id: user?.id,
      cashier_name: user?.full_name || user?.username,
      status: balance > 0 ? 'open' : 'paid',
      release_mode: releaseMode,
      created_at: new Date().toISOString(),
    };

    if (isOnline) {
      try {
        const res = await api.post('/unified-sale', saleData);
        const invoiceNum = res.data.invoice_number || res.data.sale_number;
        invalidateBalanceCache();
        toast.success(balance > 0
          ? `Invoice ${invoiceNum} created! Balance: ${formatPHP(balance)}`
          : `Sale ${invoiceNum} completed!`
        );
        setRefPrompt({ open: true, number: invoiceNum, title: saleData.customer_name || 'Walk-in', invoiceData: { ...saleData, ...res.data, invoice_number: invoiceNum } });
        clearCart();
        setCheckoutDialog(false);
        setPendingCreditSale(null);

        // ── Customer signature flow (credit / partial only) ──────────────
        if (balance > 0 && selectedCustomer?.id) {
          setSigDialog({
            open: true,
            invoice: {
              id: res.data.id,
              invoice_number: invoiceNum,
              customer_name: saleData.customer_name,
              customer_id: selectedCustomer.id,
              branch_id: currentBranch?.id || '',
              branch_name: currentBranch?.name || '',
              items: saleData.items,
              subtotal: saleData.subtotal,
              discount: saleData.overall_discount || 0,
              partial_paid: saleData.partial_payment || 0,
              balance,
              payment_type: saleData.payment_type,
              order_date: saleData.order_date,
              credit_type: cropCreditConfig?.type === 'charged_to_crop' ? 'charged_to_crop' : 'by_term',
            },
          });
        }

        // ── Crop Credit linking (after invoice created) ──────────────────
        if (cropCreditConfig?.type === 'charged_to_crop' && selectedCustomer?.id && balance > 0) {
          try {
            const cropPayload = {
              amount: balance,
              invoice_id: res.data.id || '',
              invoice_number: invoiceNum || '',
              description: `Credit sale ${invoiceNum}`,
              date: saleData.order_date,
            };

            if (cropCreditConfig.activeCreditId) {
              // Add to existing season
              await api.post(`/crop-credits/${cropCreditConfig.activeCreditId}/add-credit`, cropPayload);
            } else {
              // Create new season
              await api.post('/crop-credits', {
                customer_id: selectedCustomer.id,
                planting_date: cropCreditConfig.plantingDate,
                initial_amount: balance,
                invoice_id: res.data.id || '',
                invoice_number: invoiceNum || '',
                description: `Credit sale ${invoiceNum}`,
                branch_id: currentBranch?.id || '',
              });
            }

            // Link existing term invoices if requested
            if (cropCreditConfig.linkExistingTerms && cropCreditConfig.termInvoices?.length > 0) {
              // Fetch the newly created/updated crop credit
              const blockCheck = await api.get(`/crop-credits/check-block/${selectedCustomer.id}`);
              const creditId = blockCheck.data.active_credit_id;
              if (creditId) {
                for (const inv of cropCreditConfig.termInvoices) {
                  await api.post(`/crop-credits/${creditId}/add-credit`, {
                    amount: inv.balance,
                    invoice_id: inv.id,
                    invoice_number: inv.invoice_number,
                    description: `Linked term credit: ${inv.invoice_number}`,
                    date: inv.order_date || saleData.order_date,
                  }).catch(() => {});
                }
              }
            }

            toast.success('Linked to Crop Credit season');
          } catch (cropErr) {
            console.error('Crop credit link failed:', cropErr);
            // Non-blocking — sale was already created successfully
          }
          setCropCreditConfig(null);
        }
        // For digital/split payments: MANDATORY receipt upload
        if ((actualPaymentType === 'digital' || actualPaymentType === 'split') && res.data.id) {
          try {
            const qrRes = await api.post(`${process.env.REACT_APP_BACKEND_URL}/api/uploads/generate-link`, {
              record_type: 'invoice', record_id: res.data.id,
            });
            showReceiptDialog({ invoice_id: res.data.id, invoice_number: invoiceNum, ...qrRes.data });
          } catch (uploadErr) {
            console.error('Receipt upload link generation failed:', uploadErr);
            // Still show the dialog — they must upload from Sales History
            showReceiptDialog({ invoice_id: res.data.id, invoice_number: invoiceNum, fallback: true });
          }
        }
        setDigitalRefNumber('');
        setDigitalSender('');
      } catch (e) {
        // Insufficient stock — show override modal instead of saving offline
        const detail = e?.response?.data?.detail;
        if (e?.response?.status === 422 && detail?.type === 'insufficient_stock') {
          setInsufficientItems(detail.items || []);
          setPendingSaleData(saleData);
          setCheckoutDialog(false);
          setStockOverrideModal(true);
          setSaving(false);
          return;
        }
        // Save offline if API fails for other reasons
        await addPendingSale(saleData);
        const count = await getPendingSaleCount();
        setPendingCount(count);
        toast.success('Sale saved offline (will sync later)');
        clearCart();
        setCheckoutDialog(false);
        setPendingCreditSale(null);
      }
    } else {
      await addPendingSale(saleData);
      const count = await getPendingSaleCount();
      setPendingCount(count);
      toast.success(`Sale saved offline!`);
      clearCart();
      setCheckoutDialog(false);
      setPendingCreditSale(null);
    }
    
    setSaving(false);
  };

  const selectTerm = (label) => {
    const t = terms.find(x => x.label === label);
    setHeader(h => ({ ...h, terms: label, terms_days: t?.days || 0 }));
  };

  // Returns true if the given date is blocked (on/before last closed day OR before floor date)
  const isDateClosed = useCallback((date) => {
    if (floorDate && date < floorDate) return true;
    if (!lastCloseDate) return false;
    return date <= lastCloseDate;
  }, [lastCloseDate, floorDate]);

  // Handle date selection from unclosed days banner OR the Sale Date field
  const handleEncodingDateChange = useCallback((date) => {
    if (floorDate && date < floorDate) {
      setDateError(`Cannot encode before system start date (${floorDate}).`);
      return;
    }
    if (isDateClosed(date)) {
      setDateError(`${date} is a closed day — the last close was ${lastCloseDate}. Please choose a date after ${lastCloseDate}.`);
      return; // reject — don't update order_date
    }
    setDateError(null);
    setHeader(h => ({ ...h, order_date: date }));
  }, [isDateClosed, floorDate, lastCloseDate]);

  // Handle manager override: retry the pending sale with override PIN
  const handleStockOverride = async (overridePin) => {
    if (!pendingSaleData) return;
    const saleWithOverride = { ...pendingSaleData, manager_override_pin: overridePin };
    const res = await api.post('/unified-sale', saleWithOverride);
    const invoiceNum = res.data.invoice_number || res.data.sale_number;
    invalidateBalanceCache();
    toast.success(`Sale ${invoiceNum} completed with manager override. Discrepancy ticket created.`, { duration: 5000 });
    setStockOverrideModal(false);
    setPendingSaleData(null);
    setInsufficientItems([]);
    clearCart();
    setPendingCreditSale(null);
    if ((pendingSaleData.payment_type === 'digital' || pendingSaleData.payment_type === 'split') && res.data.id) {
      try {
        const qrRes = await api.post(`${process.env.REACT_APP_BACKEND_URL}/api/uploads/generate-link`, {
          record_type: 'invoice', record_id: res.data.id,
        });
        showReceiptDialog({ invoice_id: res.data.id, invoice_number: invoiceNum, ...qrRes.data });
      } catch {}
    }
  };

  return (
    <div className="h-[calc(100vh-80px)] flex flex-col animate-fadeIn" data-testid="unified-sales-page">
      {/* Unclosed Days Banner */}
      {mainTab === 'sale' && currentBranch?.id && (
        <UnclosedDaysBanner
          branchId={currentBranch.id}
          currentDate={header.order_date}
          onDateSelect={handleEncodingDateChange}
          onDataLoaded={({ last_close_date, floor_date }) => {
            setLastCloseDate(last_close_date);
            if (floor_date) setFloorDate(floor_date);
          }}
          className="mx-1 mb-2"
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between px-1 py-3">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Sales</h1>

          {/* Main Tab: New Sale / History */}
          <div className="flex items-center bg-slate-100 rounded-lg p-1">
            <button
              onClick={() => setMainTab('sale')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${mainTab === 'sale' ? 'bg-white shadow-sm text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'}`}
              data-testid="tab-new-sale"
            >
              <ShoppingCart size={14} /> New Sale
            </button>
            <button
              onClick={() => setMainTab('history')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${mainTab === 'history' ? 'bg-white shadow-sm text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'}`}
              data-testid="tab-history"
            >
              <FileText size={14} /> Sales History
            </button>
          </div>

          {/* Mode Toggle — only in new sale tab */}
          {mainTab === 'sale' && (
            <div className="flex items-center gap-2 bg-slate-100 rounded-lg p-1">
              <button
                onClick={() => switchMode('quick')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                  mode === 'quick' ? 'bg-white shadow-sm text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'
                }`}
                data-testid="mode-quick"
              >
                <Zap size={14} /> Quick
              </button>
              <button
                onClick={() => switchMode('order')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
                  mode === 'order' ? 'bg-white shadow-sm text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'
                }`}
                data-testid="mode-order"
              >
                <ClipboardList size={14} /> Order
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* Linked Scanner indicator */}
          {scannerSession && (
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium cursor-pointer ${
              scannerConnected ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'
            }`} onClick={() => setScannerQrOpen(true)} data-testid="scanner-status">
              <Smartphone size={12} />
              {scannerConnected ? 'Scanner Active' : 'Waiting...'}
            </div>
          )}

          {/* Offline indicator */}
          <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
            isOnline ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
          }`}>
            {isOnline ? <Wifi size={12} /> : <WifiOff size={12} />}
            {isOnline ? 'Online' : 'Offline'}
            {pendingCount > 0 && <Badge variant="secondary" className="ml-1 text-[10px] h-4">{pendingCount}</Badge>}
          </div>

          {!scannerSession && (
            <Button variant="outline" size="sm" onClick={createScannerSession} disabled={scannerCreating} data-testid="link-scanner-btn">
              <Smartphone size={14} className="mr-1" /> {scannerCreating ? 'Creating...' : 'Link Scanner'}
            </Button>
          )}
          
          <Button variant="outline" size="sm" onClick={() => loadData(true)} disabled={!isOnline}>
            <RefreshCw size={14} className="mr-1" /> Sync
          </Button>
        </div>
      </div>

      {/* ─── HISTORY TAB ─────────────────────────────────────────────────── */}
      {mainTab === 'history' && (
        <div className="flex-1 overflow-auto px-1">
          {/* Running totals */}
          {historyTotals && (
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-4">
              {[
                { label: 'Cash Sales', value: formatPHP(historyTotals.cash), color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
                { label: 'Digital Sales', value: formatPHP(historyTotals.digital || 0), color: 'text-blue-700', bg: 'bg-blue-50', border: 'border-blue-200' },
                { label: 'Credit Sales', value: formatPHP(historyTotals.credit), color: 'text-amber-700', bg: 'bg-amber-50', border: 'border-amber-200' },
                { label: 'Grand Total', value: formatPHP(historyTotals.grand_total), color: 'text-[#1A4D2E]', bg: 'bg-emerald-50', border: 'border-[#1A4D2E]/30' },
                { label: 'Transactions', value: historyTotals.count, color: 'text-slate-700', bg: 'bg-slate-50', border: 'border-slate-200', sub: historyTotals.voided_count > 0 ? `${historyTotals.voided_count} voided` : null },
              ].map(k => (
                <div key={k.label} className={`rounded-xl border ${k.border} ${k.bg} px-4 py-3`}>
                  <p className="text-[11px] text-slate-500 font-medium">{k.label}</p>
                  <p className={`text-lg font-bold font-mono ${k.color}`}>{k.value}</p>
                  {k.sub && <p className="text-[10px] text-slate-400">{k.sub}</p>}
                </div>
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex gap-2 mb-3">
            <Input type="date" value={historyDate} onChange={e => setHistoryDate(e.target.value)}
              className="h-9 w-40 text-sm" />
            <Input placeholder="Search invoice # or customer..." value={historySearch}
              onChange={e => setHistorySearch(e.target.value)} className="h-9 flex-1 text-sm" />
            <Button variant="outline" size="sm" onClick={loadHistory} disabled={historyLoading || !isOnline} className="h-9">
              <RefreshCw size={13} className={historyLoading ? 'animate-spin' : ''} />
            </Button>
          </div>

          {/* Sales list */}
          {!isOnline ? (
            <div className="text-center py-12 text-slate-400">
              <WifiOff size={20} className="mx-auto mb-2" />
              <p className="text-sm">History requires internet connection</p>
            </div>
          ) : historyLoading ? (
            <div className="text-center py-12"><RefreshCw size={20} className="animate-spin mx-auto text-slate-400" /></div>
          ) : historyList.length === 0 ? (
            <div className="text-center py-12 text-slate-400">
              <FileText size={28} className="mx-auto mb-2 opacity-40" />
              <p className="text-sm">No sales found for {historyDate}</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {historyList.map(inv => {
                const isVoided = inv.status === 'voided';
                const ptype = inv.payment_type || 'cash';
                const isSplit = ptype === 'split';
                const isDigital = ptype === 'digital' || isSplit;
                const isCash = ptype === 'cash' || (ptype !== 'credit' && ptype !== 'partial' && !isDigital && !inv.customer_id);
                const isCredit = ptype === 'credit' || ptype === 'partial';
                const hasBalance = inv.balance > 0 && !isVoided;
                const time = inv.created_at?.slice(11, 16) || '';
                const badgeInfo = isVoided ? { label: 'VOIDED', cls: 'bg-slate-200 text-slate-500' }
                  : isSplit ? { label: `Split · ${inv.digital_platform || 'Digital'}`, cls: 'bg-indigo-100 text-indigo-700' }
                  : ptype === 'digital' ? { label: inv.digital_platform || 'Digital', cls: 'bg-blue-100 text-blue-700' }
                  : isCredit ? { label: 'Credit', cls: 'bg-amber-100 text-amber-700' }
                  : { label: 'Cash', cls: 'bg-emerald-100 text-emerald-700' };
                return (
                  <button key={inv.id} onClick={() => selectInvoiceWithReceipts(inv)}
                    data-testid={`history-row-${inv.id}`}
                    className={`w-full text-left rounded-xl border px-4 py-3 transition-all hover:shadow-sm ${isVoided ? 'bg-slate-50 border-slate-100 opacity-60' : 'bg-white border-slate-200 hover:border-[#1A4D2E]/30'}`}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="text-[11px] text-slate-400 font-mono w-10 shrink-0">{time}</span>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-semibold text-blue-700">{inv.invoice_number}</span>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${badgeInfo.cls}`}>{badgeInfo.label}</span>
                          </div>
                          <p className="text-xs text-slate-500 truncate max-w-[180px]">{inv.customer_name || 'Walk-in'}</p>
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <p className={`font-bold font-mono ${isVoided ? 'text-slate-400 line-through' : 'text-slate-800'}`}>{formatPHP(inv.grand_total)}</p>
                        {hasBalance && <p className="text-[10px] text-amber-600">bal {formatPHP(inv.balance)}</p>}
                        {!hasBalance && !isVoided && isDigital && (
                          <p className={`text-[10px] ${inv.receipt_review_status === 'reviewed' ? 'text-emerald-600' : 'text-blue-500'}`}>
                            {inv.receipt_review_status === 'reviewed' ? 'verified' : 'needs verify'}
                          </p>
                        )}
                        {!hasBalance && !isVoided && !isDigital && <p className="text-[10px] text-emerald-600">paid</p>}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ─── NEW SALE TAB (existing content) ─────────────────────────────── */}
      {mainTab === 'sale' && (
      <>

      {/* Customer Selection */}
      <div className="px-1 pb-3">
        <Card className="border-slate-200">
          <CardContent className="p-3">
            <div className="flex flex-wrap items-end gap-4">
              <div className="relative flex-1 min-w-[200px]">
                <Label className="text-xs text-slate-500">Customer</Label>
                <Input
                  data-testid="customer-search"
                  className="h-9"
                  value={custSearch}
                  placeholder="Search customer or type name..."
                  onChange={e => handleCustInput(e.target.value)}
                  onFocus={() => { if (custSearch) setCustDropdownOpen(true); }}
                  onBlur={() => setTimeout(() => setCustDropdownOpen(false), 200)}
                />
                {custDropdownOpen && (
                  <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                    {filteredCusts.map(c => (
                      <button key={c.id} className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 border-b border-slate-50"
                        onMouseDown={() => selectCustomer(c.id)}>
                        <span className="font-medium">{c.name}</span>
                        <span className="text-xs text-slate-400 ml-2">{c.phone || ''}</span>
                        {canViewBalance && c.balance > 0 && <Badge variant="outline" className="ml-2 text-[10px] text-red-600">Bal: {formatPHP(c.balance)}</Badge>}
                      </button>
                    ))}
                    {custSearch && !customers.find(c => c.name.toLowerCase() === custSearch.toLowerCase()) && (
                      <button
                        data-testid="create-customer-btn"
                        className="w-full text-left px-3 py-2.5 text-sm bg-[#1A4D2E]/5 hover:bg-[#1A4D2E]/10 text-[#1A4D2E] font-medium border-t border-slate-100"
                        onMouseDown={openNewCustomerDialog}
                      >
                        <Plus size={14} className="inline mr-2" />
                        Create "{custSearch}" as new customer
                      </button>
                    )}
                  </div>
                )}
              </div>
              
              {selectedCustomer && (
                <div className="flex items-center gap-4 text-sm">
                  {selectedCustomer.price_scheme !== activeScheme && (
                    <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-300 bg-amber-50 font-medium">
                      Override
                    </Badge>
                  )}
                  {canViewBalance && (
                  <div>
                    <span className="text-xs text-slate-500">Balance:</span>
                    <span className={`ml-1 font-medium ${selectedCustomer.balance > 0 ? 'text-red-600' : ''}`}>
                      {formatPHP(selectedCustomer.balance || 0)}
                    </span>
                  </div>
                  )}
                  {canViewBalance && (
                  <div>
                    <span className="text-xs text-slate-500">Limit:</span>
                    <span className="ml-1 font-medium">{formatPHP(selectedCustomer.credit_limit || 0)}</span>
                  </div>
                  )}
                </div>
              )}

              {/* Price Scheme — always visible for both customer and walk-in */}
              <div className="w-36">
                <Label className="text-xs text-slate-500">
                  Price Scheme{selectedCustomer ? ` (default: ${selectedCustomer.price_scheme})` : ''}
                </Label>
                <Select value={activeScheme} onValueChange={handleSchemeChange}>
                  <SelectTrigger className="h-9" data-testid="price-scheme-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {schemes.map(s => (
                      <SelectItem key={s.key} value={s.key}>{s.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {schemes.length <= 1 && (
                  <p className="text-[10px] text-amber-600 mt-1 leading-tight" data-testid="single-scheme-warning">
                    Only {schemes.length} scheme — <a href="/price-schemes" className="underline font-medium">add more</a>
                  </p>
                )}
              </div>

              {mode === 'order' && (
                <>
                  <div className="w-32">
                    <Label className="text-xs text-slate-500">Terms</Label>
                    <Select value={header.terms} onValueChange={selectTerm}>
                      <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {terms.map(t => <SelectItem key={t.label} value={t.label}>{t.label}</SelectItem>)}
                        <SelectItem value="Custom">Custom</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="w-28">
                    <Label className="text-xs text-slate-500">Customer PO</Label>
                    <Input className="h-9" value={header.customer_po} onChange={e => setHeader(h => ({ ...h, customer_po: e.target.value }))} />
                  </div>
                  <div className="w-36">
                    <Label className="text-xs text-[#1A4D2E] font-semibold flex items-center gap-1">
                      Sale Date
                      <span className="text-[9px] normal-case font-normal text-slate-400">(reports)</span>
                    </Label>
                    <Input
                      type="date"
                      className={`h-9 font-medium ${dateError
                        ? 'border-red-400 bg-red-50 text-red-700 focus:border-red-500'
                        : 'border-[#1A4D2E]/40 bg-emerald-50 focus:border-[#1A4D2E] text-[#1A4D2E]'
                      }`}
                      value={header.order_date}
                      min={minAllowedDate}
                      max={localToday()}
                      onChange={e => handleEncodingDateChange(e.target.value)}
                      data-testid="sale-date-input"
                    />
                    {/* Closed-through label — always visible when there's a close history */}
                    {lastCloseDate && !dateError && (
                      <p className="text-[9px] text-slate-400 mt-0.5 leading-tight">
                        Closed through {lastCloseDate}
                      </p>
                    )}
                    {/* Inline error — shown instead of a toast so it can't be missed */}
                    {dateError && (
                      <p className="text-[9px] text-red-600 font-medium mt-0.5 leading-tight">
                        {dateError}
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Order Mode: Expanded Header (Collapsible) ── */}
      {mode === 'order' && (
        <div className="px-1 pb-2">
          <Card className="border-slate-200">
            <CardContent className="p-0">
              {/* Toggle bar */}
              <button
                onClick={() => setHeaderCollapsed(h => !h)}
                className="w-full flex items-center justify-between px-3 py-1.5 text-xs font-semibold uppercase tracking-widest text-slate-400 hover:bg-slate-50 transition-colors"
                data-testid="order-header-toggle"
              >
                <span>{selectedCustomer ? `${selectedCustomer.name} — Details & Order Info` : 'Customer Details & Order Info'}</span>
                <ChevronDown size={14} className={`transition-transform ${headerCollapsed ? '' : 'rotate-180'}`} />
              </button>

              {!headerCollapsed && (
              <div className="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-slate-100 border-t border-slate-100">
                {/* Left: Contact + Addresses */}
                <div className="p-3 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Contact / Phone</Label>
                      <Input className="h-8 text-sm mt-0.5"
                        data-testid="cust-phone-input"
                        value={selectedCustomer ? custEdits.phone : ''}
                        placeholder={selectedCustomer ? 'Add phone...' : 'Select customer first'}
                        disabled={!selectedCustomer}
                        onChange={e => {
                          setCustEdits(p => ({ ...p, phone: e.target.value }));
                          setCustEdited(true);
                        }}
                      />
                    </div>
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Billing Address</Label>
                      <Input className="h-8 text-sm mt-0.5"
                        data-testid="cust-address-input"
                        value={selectedCustomer ? custEdits.address : ''}
                        placeholder={selectedCustomer ? 'Add address...' : '—'}
                        disabled={!selectedCustomer}
                        onChange={e => {
                          setCustEdits(p => ({ ...p, address: e.target.value }));
                          setCustEdited(true);
                        }}
                      />
                    </div>
                  </div>
                  <div>
                    <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Shipping Address</Label>
                    <Input className="h-8 text-sm mt-0.5" placeholder="(same as billing)"
                      data-testid="cust-shipping-input"
                      value={custEdits.shipping_address || header.shipping_address}
                      onChange={e => {
                        setCustEdits(p => ({ ...p, shipping_address: e.target.value }));
                        setHeader(h => ({ ...h, shipping_address: e.target.value }));
                      }}
                    />
                  </div>
                </div>

                {/* Right: Order Meta */}
                <div className="p-3 space-y-2">
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Sales Rep</Label>
                      <Select value={header.sales_rep_id || 'none'} onValueChange={v => {
                        const u = users.find(x => x.id === v);
                        setHeader(h => ({ ...h, sales_rep_id: v === 'none' ? '' : v, sales_rep_name: u?.full_name || u?.username || '' }));
                      }}>
                        <SelectTrigger className="h-8 text-sm mt-0.5"><SelectValue placeholder="None" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">None</SelectItem>
                          {users.map(u => <SelectItem key={u.id} value={u.id}>{u.full_name || u.username}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Location</Label>
                      <Input className="h-8 text-sm mt-0.5" value={header.location}
                        onChange={e => setHeader(h => ({ ...h, location: e.target.value }))} />
                    </div>
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Req. Ship Date</Label>
                      <Input type="date" className="h-8 text-sm mt-0.5" value={header.req_ship_date}
                        onChange={e => setHeader(h => ({ ...h, req_ship_date: e.target.value }))} />
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">MOD</Label>
                      <Input className="h-8 text-sm mt-0.5" placeholder="e.g. Delivery" value={header.mod}
                        onChange={e => setHeader(h => ({ ...h, mod: e.target.value }))} />
                    </div>
                    <div>
                      <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Check #</Label>
                      <Input className="h-8 text-sm mt-0.5" value={header.check_number}
                        onChange={e => setHeader(h => ({ ...h, check_number: e.target.value }))} />
                    </div>
                  </div>
                </div>
              </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 flex gap-4 px-1 overflow-hidden">
        {mode === 'quick' ? (
          // QUICK MODE: Product grid + Cart
          <>
            {/* Product Grid */}
            <div className="flex-1 flex flex-col min-w-0">
              <div className="relative mb-3">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  ref={searchRef}
                  data-testid="product-search"
                  className="pl-9 h-10"
                  placeholder="Search products by name, SKU, or barcode..."
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                />
              </div>
              <ScrollArea className="flex-1">
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                  {filteredProducts.slice(0, 50).map(p => {
                    const avail = p.available ?? 0;
                    const isOut = avail <= 0;
                    const isLow = avail > 0 && avail <= (p.reorder_point || 5);
                    return (
                      <button
                        key={p.id}
                        data-testid={`product-${p.id}`}
                        onClick={() => addToCart(p)}
                        className={`text-left p-3 rounded-lg border transition-all ${
                          isOut
                            ? 'border-red-200 bg-red-50/40 opacity-70'
                            : isLow
                            ? 'border-amber-200 hover:border-amber-400 hover:bg-amber-50'
                            : 'border-slate-200 hover:border-[#1A4D2E]/50 hover:bg-slate-50'
                        }`}
                      >
                        <p className="font-medium text-sm truncate leading-tight">{p.name}</p>
                        <p className="text-xs text-slate-400 truncate">{p.sku}</p>
                        <div className="flex items-center justify-between mt-2">
                          <span className="text-sm font-semibold text-[#1A4D2E]">{formatPHP(getPriceForCustomer(p))}</span>
                          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                            isOut ? 'bg-red-100 text-red-600' :
                            isLow ? 'bg-amber-100 text-amber-700' :
                            'bg-emerald-50 text-emerald-700'
                          }`}>
                            {isOut ? 'Out' : `${avail.toFixed(0)} ${p.unit || ''}`}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </ScrollArea>
            </div>

            {/* Cart */}
            <Card className="w-80 flex flex-col border-slate-200">
              <CardContent className="flex-1 flex flex-col p-0">
                <div className="p-3 border-b border-slate-100 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <ShoppingCart size={16} className="text-slate-400" />
                    <span className="font-semibold text-sm">Cart</span>
                    <Badge variant="secondary" className="text-[10px]">{cart.length}</Badge>
                  </div>
                  {cart.length > 0 && (
                    <Button variant="ghost" size="sm" onClick={clearCart} className="text-xs text-slate-400">Clear</Button>
                  )}
                </div>
                
                <ScrollArea className="flex-1 p-3">
                  {cart.length === 0 ? (
                    <p className="text-center text-slate-400 text-sm py-8">Cart empty</p>
                  ) : (
                    <div className="space-y-2">
                      {cart.map(item => (
                        <div key={item.product_id} className="p-2 rounded-lg bg-slate-50 space-y-1.5">
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-sm font-medium truncate flex-1">{item.product_name}</p>
                            <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-red-400 flex-shrink-0" onClick={() => removeFromCart(item.product_id)}>
                              <Trash2 size={11} />
                            </Button>
                          </div>
                          <div className="flex items-center gap-1.5">
                            {/* Quantity controls */}
                            <div className="flex items-center border border-slate-200 rounded overflow-hidden flex-shrink-0">
                              <button className="px-1.5 py-1 text-slate-400 hover:bg-slate-100 h-7" onClick={() => updateQty(item.product_id, -1)}><Minus size={11} /></button>
                              <input
                                type="text"
                                inputMode="decimal"
                                className="w-12 text-center text-sm h-7 border-0 focus:outline-none"
                                value={item._qtyStr ?? item.quantity}
                                onChange={e => setCartQty(item.product_id, e.target.value)}
                                onBlur={e => {
                                  const v = parseFloat(e.target.value) || 0;
                                  if (v === 0) removeFromCart(item.product_id);
                                  else setCartQty(item.product_id, v);
                                }}
                                onFocus={e => e.target.select()}
                                data-testid={`cart-qty-${item.product_id}`}
                              />
                              <button className="px-1.5 py-1 text-slate-400 hover:bg-slate-100 h-7" onClick={() => updateQty(item.product_id, 1)}><Plus size={11} /></button>
                            </div>
                            <span className="text-xs text-slate-400">×</span>
                            {/* Price (editable) */}
                            <input
                              type="number"
                              className={`w-24 h-7 text-sm text-right px-2 border rounded focus:outline-none focus:ring-1 focus:ring-[#1A4D2E]/30 ${
                                item.price <= 0 ? 'border-amber-400 bg-amber-50 text-amber-700'
                                : canViewCost && (item.effective_capital || item.cost_price) > 0 && item.price < (item.effective_capital || item.cost_price) ? 'border-red-300 bg-red-50 text-red-600'
                                : 'border-slate-200'
                              }`}
                              value={item.price}
                              onChange={e => updateCartPrice(item.product_id, e.target.value)}
                              onBlur={() => {
                                const ci = cart.find(c => c.product_id === item.product_id);
                                if (ci) triggerPriceSaveDialog(ci.product_id, ci.product_name, ci.original_price ?? ci.price, ci.price);
                              }}
                              onFocus={e => e.target.select()}
                              min="0" step="0.01"
                              readOnly={!canDiscount}
                              title={!canDiscount ? 'No permission to change prices' : ''}
                            />
                            <span className="text-xs font-semibold text-[#1A4D2E] text-right flex-1">{formatPHP(item.total)}</span>
                          </div>
                          {item.price <= 0 && (
                            <p className="text-[10px] text-amber-600 flex items-center gap-1"><AlertTriangle size={9}/> Set price before checkout</p>
                          )}
                          {canViewCost && item.price > 0 && (item.effective_capital || item.cost_price) > 0 && item.price < (item.effective_capital || item.cost_price) && (
                            <p className="text-[10px] text-red-600 flex items-center gap-1">
                              <AlertTriangle size={9}/> Below capital ₱{(item.effective_capital || item.cost_price).toFixed(2)}
                              {item.capital_method && item.capital_method !== 'manual' && (
                                <span className="opacity-60">({item.capital_method.replace('_',' ')})</span>
                              )}
                            </p>
                          )}
                          {/* Capital reference — always visible when product has PO history */}
                          {canViewCost && (item.moving_average_cost > 0 || item.last_purchase_cost > 0) && (
                            <div className="flex items-center gap-2 text-[10px] mt-0.5">
                              {item.moving_average_cost > 0 && (
                                <span className={`${item.price > 0 && item.price < item.moving_average_cost ? 'text-red-500 font-semibold' : 'text-slate-400'}`}>
                                  Avg ₱{item.moving_average_cost.toFixed(2)}
                                </span>
                              )}
                              {item.last_purchase_cost > 0 && item.last_purchase_cost !== item.moving_average_cost && (
                                <>
                                  <span className="text-slate-200">·</span>
                                  <span className={`${item.price > 0 && item.price < item.last_purchase_cost ? 'text-amber-500 font-semibold' : 'text-slate-400'}`}>
                                    Last ₱{item.last_purchase_cost.toFixed(2)}
                                  </span>
                                </>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </ScrollArea>

                <div className="p-3 border-t border-slate-100 space-y-2">
                  <div className="flex justify-between text-sm"><span>Subtotal</span><span>{formatPHP(subtotal)}</span></div>
                  <Separator />
                  <div className="flex justify-between font-bold"><span>Total</span><span className="text-lg">{formatPHP(grandTotal)}</span></div>
                  <Button 
                    data-testid="checkout-btn"
                    className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                    onClick={openCheckout}
                    disabled={cart.length === 0}
                  >
                    <CreditCard size={16} className="mr-2" /> Checkout
                  </Button>
                </div>
              </CardContent>
            </Card>
          </>
        ) : (
          // ORDER MODE: Excel-style line items
          <div className="flex-1 flex flex-col min-w-0">
            <Card className="flex-1 flex flex-col border-slate-200 min-h-0">
              <CardContent className="flex-1 flex flex-col p-0 min-h-0">
                <div className="overflow-auto flex-1">
                  <table className="w-full text-sm" data-testid="order-lines-table">
                    <thead className="sticky top-0 bg-slate-50 z-10">
                      <tr className="border-b">
                        <th className="text-left px-3 py-2 text-xs uppercase text-slate-500 font-medium w-8">#</th>
                        <th className="text-left px-3 py-2 text-xs uppercase text-slate-500 font-medium min-w-[240px]">Item</th>
                        <th className="text-left px-3 py-2 text-xs uppercase text-slate-500 font-medium min-w-[120px]">Description</th>
                        <th className="text-right px-3 py-2 text-xs uppercase text-slate-500 font-medium w-20">Qty</th>
                        <th className="text-right px-3 py-2 text-xs uppercase text-slate-500 font-medium w-28">Unit Price</th>
                        <th className="text-right px-3 py-2 text-xs uppercase text-slate-500 font-medium w-28">Discount</th>
                        <th className="text-right px-3 py-2 text-xs uppercase text-slate-500 font-medium w-28">Sub-Total</th>
                        <th className="w-10"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map((line, i) => (
                        <tr key={i} className="border-b border-slate-100 hover:bg-slate-50/50">
                          <td className="px-3 py-1 text-xs text-slate-400">{i + 1}</td>
                          <td className="px-3 py-1 min-w-[280px]">
                            {line.product_id ? (
                              <div className="flex items-center gap-2">
                                <div className="flex-1 min-w-0">
                                  <p className="text-sm font-medium truncate">{line.product_name}</p>
                                  {line.description && <p className="text-[11px] text-slate-400 truncate">{line.description}</p>}
                                </div>
                                <button
                                  onClick={() => clearLine(i)}
                                  className="text-slate-300 hover:text-red-500 transition-colors flex-shrink-0"
                                  title="Remove product"
                                >
                                  <X size={13} />
                                </button>
                              </div>
                            ) : (
                              <SmartProductSearch
                                branchId={currentBranch?.id}
                                onSelect={(p) => handleProductSelect(i, p)}
                                onCreateNew={() => {}}
                              />
                            )}
                          </td>
                          <td className="px-2 py-1">
                            <input
                              className="w-full h-8 px-2 text-sm border border-transparent hover:border-slate-200 focus:border-[#1A4D2E] focus:outline-none rounded bg-transparent"
                              placeholder="—"
                              value={line.description || ''}
                              onChange={e => updateLine(i, 'description', e.target.value)}
                            />
                          </td>
                          <td className="px-3 py-1">
                            <Input
                              ref={el => qtyRefs.current[i] = el}
                              type="number"
                              className="h-8 text-right w-16"
                              value={line.quantity}
                              onChange={e => updateLine(i, 'quantity', parseFloat(e.target.value) || 0)}
                            />
                          </td>
                          <td className="px-3 py-1">
                            <div>
                              <Input
                                type="number"
                                className={`h-8 text-right w-24 ${
                                  line.product_id && line.rate <= 0 ? 'border-amber-400 bg-amber-50'
                                  : canViewCost && line.product_id && (line.effective_capital || line.cost_price) > 0 && line.rate > 0 && line.rate < (line.effective_capital || line.cost_price) ? 'border-red-300 bg-red-50 text-red-700'
                                  : ''
                                }`}
                                value={line.rate}
                                onChange={e => updateLine(i, 'rate', parseFloat(e.target.value) || 0)}
                                onBlur={() => handleRateBlur(lines[i])}
                                readOnly={!canDiscount}
                                title={!canDiscount ? 'No permission to change prices' : ''}
                              />
                              {/* Capital reference — shown when a product is selected */}
                              {canViewCost && line.product_id && (line.moving_average_cost > 0 || line.last_purchase_cost > 0) && (
                                <div className="flex flex-col gap-0.5 mt-0.5">
                                  {line.moving_average_cost > 0 && (
                                    <span className={`text-[10px] ${line.rate > 0 && line.rate < line.moving_average_cost ? 'text-red-500 font-semibold' : 'text-slate-400'}`}>
                                      Avg ₱{line.moving_average_cost.toFixed(2)}
                                    </span>
                                  )}
                                  {line.last_purchase_cost > 0 && line.last_purchase_cost !== line.moving_average_cost && (
                                    <span className={`text-[10px] ${line.rate > 0 && line.rate < line.last_purchase_cost ? 'text-amber-500 font-semibold' : 'text-slate-400'}`}>
                                      Last ₱{line.last_purchase_cost.toFixed(2)}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-1">
                            {(() => {
                              const cap = line.effective_capital || line.cost_price;
                              const net = line.product_id && line.quantity > 0 && line.discount_value > 0
                                ? lineTotal(line) / line.quantity : null;
                              const isBelowCap = canViewCost && net !== null && cap > 0 && net < cap;
                              return (
                                <div>
                                  <Input
                                    type="number"
                                    className={`h-8 text-right w-20 ${isBelowCap ? 'border-red-400 bg-red-50 text-red-700' : ''} ${!canDiscount ? 'bg-slate-100 cursor-not-allowed' : ''}`}
                                    value={line.discount_value}
                                    onChange={e => updateLine(i, 'discount_value', parseFloat(e.target.value) || 0)}
                                    disabled={!canDiscount}
                                    title={!canDiscount ? 'No discount permission' : ''}
                                  />
                                  {isBelowCap && (
                                    <p className="text-[10px] text-red-600 mt-0.5 flex items-center gap-0.5">
                                      <AlertTriangle size={9}/> Net ₱{net.toFixed(2)} &lt; cap ₱{cap.toFixed(2)}
                                    </p>
                                  )}
                                </div>
                              );
                            })()}
                          </td>
                          <td className="px-3 py-1 text-right font-medium">{formatPHP(lineTotal(line))}</td>
                          <td className="px-1">
                            {lines.length > 1 && line.product_id && (
                              <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-red-500" onClick={() => removeLine(i)}>
                                <Trash2 size={12} />
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Order bottom — Notes + Totals */}
                <div className="border-t border-slate-100">
                  <div className="grid grid-cols-1 lg:grid-cols-3 divide-y lg:divide-y-0 lg:divide-x divide-slate-100">

                    {/* Notes */}
                    <div className="p-3 lg:col-span-2">
                      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-1.5">Important / Notes</p>
                      <textarea
                        className="w-full h-16 text-sm border border-slate-200 rounded-lg px-3 py-2 focus:border-[#1A4D2E] focus:outline-none resize-none"
                        placeholder="Delivery instructions, special notes..."
                        value={header.notes || ''}
                        onChange={e => setHeader(h => ({ ...h, notes: e.target.value }))}
                      />
                    </div>

                    {/* Totals */}
                    <div className="p-3">
                      <div className="space-y-1.5">
                        <div className="flex justify-between text-sm"><span className="text-slate-500">Sub-Total</span><span className="font-medium">{formatPHP(subtotal)}</span></div>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Freight</span>
                          <Input type="number" className="h-7 w-24 text-right" value={freight} onChange={e => setFreight(parseFloat(e.target.value) || 0)} />
                        </div>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Discount</span>
                          <Input type="number" className={`h-7 w-24 text-right ${!canDiscount ? 'bg-slate-100 cursor-not-allowed' : ''}`} value={overallDiscount} onChange={e => setOverallDiscount(parseFloat(e.target.value) || 0)} disabled={!canDiscount} />
                        </div>
                        <Separator />
                        <div className="flex justify-between font-bold text-base" style={{ fontFamily: 'Manrope' }}>
                          <span>Total</span><span className="text-[#1A4D2E]">{formatPHP(grandTotal)}</span>
                        </div>
                        <Button
                          data-testid="checkout-btn"
                          className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white mt-1"
                          onClick={openCheckout}
                          disabled={items.length === 0}
                        >
                          <CreditCard size={15} className="mr-2" /> Complete & Pay
                        </Button>
                      </div>
                    </div>

                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>

      {/* Checkout Dialog */}
      <Dialog open={checkoutDialog} onOpenChange={setCheckoutDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Payment</DialogTitle>
          </DialogHeader>
          
          <div className="space-y-4 overflow-y-auto max-h-[calc(85vh-120px)] pr-1">
            {/* Customer display / quick picker */}
            {selectedCustomer ? (
              <div className="bg-slate-50 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-slate-500">Customer</p>
                    <p className="font-medium">{selectedCustomer.name}</p>
                  </div>
                  <button
                    onClick={() => { setSelectedCustomer(null); setCustSearch(''); }}
                    className="text-xs text-slate-400 hover:text-red-500 transition-colors px-2 py-1 rounded"
                    data-testid="clear-customer-btn"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="flex gap-4 mt-1 text-xs text-slate-500">
                  {canViewBalance && <span>Balance: <span className={selectedCustomer.balance > 0 ? 'text-red-600 font-medium' : ''}>{formatPHP(selectedCustomer.balance || 0)}</span></span>}
                  {canViewBalance && <span>Limit: {formatPHP(selectedCustomer.credit_limit || 0)}</span>}
                </div>
              </div>
            ) : (
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-sm text-slate-500 mb-1.5">Customer</p>
                <div className="relative" data-testid="checkout-customer-picker">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input
                    data-testid="checkout-customer-search"
                    value={custSearch}
                    onChange={e => {
                      setCustSearch(e.target.value);
                      const match = customers.find(c => c.name.toLowerCase() === e.target.value.toLowerCase());
                      if (match) setSelectedCustomer(match);
                      else setSelectedCustomer(null);
                    }}
                    placeholder="Search customer or type Walk-in..."
                    className="pl-8 h-9 text-sm"
                  />
                  {custSearch && !selectedCustomer && (
                    <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-36 overflow-y-auto">
                      {customers
                        .filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase()))
                        .slice(0, 6)
                        .map(c => (
                          <button
                            key={c.id}
                            onClick={() => { setSelectedCustomer(c); setCustSearch(c.name); }}
                            className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex justify-between items-center"
                            data-testid={`checkout-cust-${c.id}`}
                          >
                            <span className="font-medium truncate">{c.name}</span>
                            <span className="text-[10px] text-slate-400 shrink-0 ml-2">
                              {canViewBalance && c.balance > 0 ? `AR: ${formatPHP(c.balance)}` : ''}
                            </span>
                          </button>
                        ))
                      }
                      {customers.filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase())).length === 0 && (
                        <p className="px-3 py-2 text-xs text-slate-400">No matching customers</p>
                      )}
                    </div>
                  )}
                </div>
                {!custSearch && <p className="text-xs text-slate-400 mt-1">Walk-in customer (no AR)</p>}
              </div>
            )}

            {/* Total */}
            <div className="text-center py-4">
              <p className="text-sm text-slate-500">Total Amount</p>
              <p className="text-3xl font-bold text-[#1A4D2E]" style={{ fontFamily: 'Manrope' }}>{formatPHP(grandTotal)}</p>
            </div>

            {/* Payment Type */}
            <div className="space-y-2">
              <Label className="text-sm">Payment Type</Label>
              <Tabs value={paymentType} onValueChange={v => { setPaymentType(v); setDigitalRefNumber(''); setDigitalSender(''); setSplitCash(''); setSplitDigital(''); }}>
                <TabsList className="grid grid-cols-5 w-full">
                  <TabsTrigger value="cash" data-testid="pay-cash">Cash</TabsTrigger>
                  <TabsTrigger value="digital" data-testid="pay-digital">Digital</TabsTrigger>
                  <TabsTrigger value="split" data-testid="pay-split">Split</TabsTrigger>
                  <TabsTrigger value="partial" data-testid="pay-partial">Partial</TabsTrigger>
                  <TabsTrigger value="credit" data-testid="pay-credit">Credit</TabsTrigger>
                </TabsList>
              </Tabs>
              {!selectedCustomer && (paymentType === 'partial' || paymentType === 'credit') && (
                <p className="text-xs text-amber-600 flex items-center gap-1">
                  <AlertTriangle size={12} /> Select a customer first — {paymentType === 'credit' ? 'credit' : 'partial'} balance goes to Accounts Receivable
                </p>
              )}
            </div>

            {/* Payment inputs */}
            {paymentType === 'cash' && (
              <div>
                <Label>Amount Tendered</Label>
                <Input
                  data-testid="amount-tendered"
                  type="number"
                  value={amountTendered}
                  onChange={e => setAmountTendered(parseFloat(e.target.value) || 0)}
                  className="text-lg h-12"
                />
                {change > 0 && (
                  <p className="text-right mt-2 text-lg font-bold text-emerald-600">Change: {formatPHP(change)}</p>
                )}
              </div>
            )}

            {paymentType === 'digital' && (
              <div className="space-y-3 rounded-xl bg-blue-50 border border-blue-200 p-3">
                <div className="flex items-center gap-2 mb-1">
                  <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center">
                    <span className="text-white text-[10px] font-bold">₱</span>
                  </div>
                  <span className="text-sm font-semibold text-blue-800">Digital Payment → Digital Wallet</span>
                </div>
                <div>
                  <Label className="text-xs text-blue-700">Platform *</Label>
                  <select
                    value={digitalPlatform}
                    onChange={e => setDigitalPlatform(e.target.value)}
                    className="w-full mt-1 h-9 rounded-lg border border-blue-200 bg-white px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                    data-testid="digital-platform"
                  >
                    {['GCash', 'Maya', 'PayMaya', 'Bank Transfer', 'Instapay', 'Pesonet', 'ShopeePay', 'GrabPay', 'Coins.ph', 'SeaBank', 'Other'].map(p => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label className="text-xs text-blue-700">Reference / Transaction # *</Label>
                  <Input
                    value={digitalRefNumber}
                    onChange={e => setDigitalRefNumber(e.target.value)}
                    placeholder="e.g. GC2026XXXXXXXX"
                    className="mt-1 h-9 border-blue-200 focus:ring-blue-300"
                    data-testid="digital-ref-number"
                  />
                </div>
                <div>
                  <Label className="text-xs text-blue-700">Sender Name / Number (optional)</Label>
                  <Input
                    value={digitalSender}
                    onChange={e => setDigitalSender(e.target.value)}
                    placeholder="e.g. Juan Dela Cruz / 09XX-XXX-XXXX"
                    className="mt-1 h-9 border-blue-200 focus:ring-blue-300"
                    data-testid="digital-sender"
                  />
                </div>
                <div className="flex items-center gap-2 text-[10px] text-blue-600 bg-blue-100 rounded-lg px-2.5 py-1.5">
                  <span>After sale: QR code will appear to upload the {digitalPlatform} receipt screenshot</span>
                </div>
                <p className="text-lg font-bold text-blue-800 text-center">{formatPHP(grandTotal)}</p>
              </div>
            )}

            {paymentType === 'split' && (
              <div className="space-y-3 rounded-xl bg-gradient-to-br from-emerald-50 to-blue-50 border border-slate-200 p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-semibold text-slate-700">Split: Cash + {digitalPlatform}</span>
                  <span className="text-xs text-slate-400">Total: {formatPHP(grandTotal)}</span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-xs text-emerald-700">Cash Amount</Label>
                    <Input type="number" value={splitCash} data-testid="split-cash"
                      onChange={e => { setSplitCash(e.target.value); setSplitDigital(String(Math.max(0, grandTotal - (parseFloat(e.target.value)||0)))); }}
                      placeholder="0.00" className="mt-1 h-9 border-emerald-200" />
                  </div>
                  <div>
                    <Label className="text-xs text-blue-700">{digitalPlatform} Amount</Label>
                    <Input type="number" value={splitDigital} data-testid="split-digital"
                      onChange={e => { setSplitDigital(e.target.value); setSplitCash(String(Math.max(0, grandTotal - (parseFloat(e.target.value)||0)))); }}
                      placeholder="0.00" className="mt-1 h-9 border-blue-200" />
                  </div>
                </div>
                {(parseFloat(splitCash||0) + parseFloat(splitDigital||0)) !== grandTotal && (
                  <p className="text-xs text-amber-600 text-center">
                    Cash + Digital must equal {formatPHP(grandTotal)}
                    {' '} (currently {formatPHP(parseFloat(splitCash||0) + parseFloat(splitDigital||0))})
                  </p>
                )}
                <div>
                  <Label className="text-xs text-blue-700">Platform *</Label>
                  <select value={digitalPlatform} onChange={e => setDigitalPlatform(e.target.value)}
                    className="w-full mt-1 h-9 rounded-lg border border-blue-200 bg-white px-2.5 text-sm focus:outline-none">
                    {['GCash', 'Maya', 'PayMaya', 'Bank Transfer', 'Instapay', 'Pesonet', 'ShopeePay', 'GrabPay', 'Other'].map(p => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label className="text-xs text-blue-700">Digital Ref # *</Label>
                  <Input value={digitalRefNumber} onChange={e => setDigitalRefNumber(e.target.value)}
                    placeholder="e.g. GC2026XXXXXXXX" className="mt-1 h-9 border-blue-200" data-testid="split-ref-number" />
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Sender (optional)</Label>
                  <Input value={digitalSender} onChange={e => setDigitalSender(e.target.value)}
                    placeholder="Name / number" className="mt-1 h-9" />
                </div>
                <div className="flex items-center gap-2 text-[10px] text-blue-600 bg-blue-100 rounded-lg px-2.5 py-1.5">
                  <span>After sale: QR code will appear to upload the {digitalPlatform} payment screenshot</span>
                </div>
              </div>
            )}
            {paymentType === 'partial' && (
              <div>
                {selectedCustomer ? (
                  <>
                    <Label>Amount Paid Now</Label>
                    <Input
                      data-testid="partial-amount"
                      type="number"
                      value={partialPayment}
                      onChange={e => setPartialPayment(Math.min(parseFloat(e.target.value) || 0, grandTotal))}
                      className="text-lg h-12"
                    />
                    <div className="flex justify-between mt-2 p-2 bg-amber-50 rounded-lg">
                      <span className="text-sm text-amber-700">Balance (to AR)</span>
                      <span className="font-bold text-amber-700">{formatPHP(grandTotal - partialPayment)}</span>
                    </div>
                  </>
                ) : (
                  <div className="p-3 bg-amber-50 rounded-lg">
                    <p className="text-sm text-amber-700 font-medium">Select a customer above</p>
                    <p className="text-xs text-amber-600 mt-1">Partial payment balance goes to Accounts Receivable and must be assigned to a customer</p>
                  </div>
                )}
              </div>
            )}

            {paymentType === 'credit' && (
              <div className="p-3 bg-red-50 rounded-lg">
                <p className="text-sm text-red-700 font-medium">Full Credit Sale</p>
                {selectedCustomer ? (
                  <p className="text-xs text-red-600 mt-1">
                    {formatPHP(grandTotal)} will be added to {selectedCustomer.name}'s receivables
                  </p>
                ) : (
                  <p className="text-xs text-red-600 mt-1">
                    Select a customer above — credit balance must be assigned to an account
                  </p>
                )}
                <p className="text-xs text-slate-500 mt-2 flex items-center gap-1">
                  <Shield size={12} /> Requires manager approval
                </p>
              </div>
            )}

            {/* Stock Release Mode */}
            <div className="space-y-2 pt-1">
              <Label className="text-sm font-semibold text-slate-700">Stock Release</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  data-testid="release-mode-full"
                  onClick={() => setReleaseMode('full')}
                  className={`flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all ${
                    releaseMode === 'full'
                      ? 'border-[#1A4D2E] bg-emerald-50'
                      : 'border-slate-200 bg-slate-50 hover:border-slate-300'
                  }`}
                >
                  <span className={`text-sm font-semibold ${releaseMode === 'full' ? 'text-[#1A4D2E]' : 'text-slate-700'}`}>
                    Full Release
                  </span>
                  <span className="text-[11px] text-slate-500 mt-0.5">All items given now</span>
                </button>
                <button
                  data-testid="release-mode-partial"
                  onClick={() => setReleaseMode('partial')}
                  className={`flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all ${
                    releaseMode === 'partial'
                      ? 'border-amber-500 bg-amber-50'
                      : 'border-slate-200 bg-slate-50 hover:border-slate-300'
                  }`}
                >
                  <span className={`text-sm font-semibold ${releaseMode === 'partial' ? 'text-amber-700' : 'text-slate-700'}`}>
                    Partial Release
                  </span>
                  <span className="text-[11px] text-slate-500 mt-0.5">Items released via QR</span>
                </button>
              </div>
              {releaseMode === 'partial' && (
                <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 flex items-start gap-1.5">
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  Stock stays reserved until each batch is scanned and released. Use the invoice QR code to release items.
                </p>
              )}
            </div>

            {/* Action buttons — checkout confirm */}
            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={() => setCheckoutDialog(false)}>Cancel</Button>
              <Button 
                data-testid="confirm-payment"
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                onClick={handleCreditSale}
                disabled={
                  saving ||
                  (paymentType === 'cash' && amountTendered < grandTotal) ||
                  (paymentType === 'digital' && !digitalRefNumber.trim()) ||
                  (paymentType === 'split' && (
                    !digitalRefNumber.trim() ||
                    Math.abs((parseFloat(splitCash||0) + parseFloat(splitDigital||0)) - grandTotal) > 0.01
                  )) ||
                  ((paymentType === 'partial' || paymentType === 'credit') && !selectedCustomer)
                }
              >
                {saving ? 'Processing...' : (
                  paymentType === 'cash' ? 'Complete Sale' :
                  paymentType === 'digital' ? `Complete — ${digitalPlatform}` :
                  paymentType === 'split' ? `Split: ₱${parseFloat(splitCash||0).toFixed(0)} Cash + ₱${parseFloat(splitDigital||0).toFixed(0)} ${digitalPlatform}` :
                  'Confirm & Create Invoice'
                )}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Credit Approval Dialog — Respects PIN Policies */}
      {/* Crop Credit Type Dialog — shown before PIN approval for credit sales */}
      <CropCreditTypeDialog
        open={cropTypeDialog}
        onClose={() => { setCropTypeDialog(false); setCropCreditConfig(null); setPendingCreditSale(null); }}
        onConfirm={handleCropTypeConfirmed}
        customerId={selectedCustomer?.id}
        customerName={selectedCustomer?.name || 'Customer'}
        saleAmount={balanceDue}
        branchId={currentBranch?.id}
      />

      <RequestSignatureDialog
        open={sigDialog.open}
        onOpenChange={(open) => setSigDialog(prev => ({ ...prev, open }))}
        invoice={sigDialog.invoice}
        onPrintReceipt={async ({ signature_url, bypass_method, signed_at, verification_token }) => {
          try {
            const inv = sigDialog.invoice || {};
            // Fetch fresh QR code for the receipt (best-effort)
            let docCode = '';
            try {
              const r = await api.post('/doc/generate-code', { doc_type: 'sale', doc_id: inv.id });
              docCode = r.data?.code || '';
            } catch { /* print without QR */ }
            const printData = {
              ...inv,
              cashier_name: user?.full_name || user?.username || '',
              signature_url: signature_url || null,
              bypass_method: bypass_method || null,
              signature_signed_at: signed_at || null,
              signature_verification_token: verification_token || null,
              // Translate field names PrintEngine expects
              overall_discount: inv.discount || 0,
              amount_paid: inv.partial_paid || 0,
              grand_total: (inv.subtotal || 0) - (inv.discount || 0),
            };
            PrintEngine.print({
              type: PrintEngine.getDocType(printData) || 'trust_receipt',
              data: printData,
              format: 'full_page',
              businessInfo: businessInfo || {},
              docCode,
            });
          } catch (e) {
            toast.error('Print failed: ' + (e?.message || ''));
          }
        }}
      />

      <Dialog open={creditApprovalDialog} onOpenChange={setCreditApprovalDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Shield className="text-amber-500" /> Authorization Required
            </DialogTitle>
            <DialogDescription>
              Credit/Partial sales require PIN or TOTP authorization
            </DialogDescription>
          </DialogHeader>
          
          <div className="space-y-4">
            {/* Credit check result */}
            {creditCheckResult && !creditCheckResult.allowed && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-sm font-medium text-red-700 flex items-center gap-1">
                  <AlertTriangle size={14} /> Credit Limit Exceeded
                </p>
                <div className="mt-2 space-y-1 text-xs text-red-600">
                  <div className="flex justify-between"><span>Current Balance:</span><span>{formatPHP(creditCheckResult.currentBalance)}</span></div>
                  <div className="flex justify-between"><span>This Sale:</span><span>{formatPHP(balanceDue)}</span></div>
                  <div className="flex justify-between font-medium"><span>New Total:</span><span>{formatPHP(creditCheckResult.newTotal)}</span></div>
                  <div className="flex justify-between"><span>Credit Limit:</span><span>{formatPHP(creditCheckResult.creditLimit)}</span></div>
                  <Separator className="my-1" />
                  <div className="flex justify-between font-bold text-red-700">
                    <span>Exceeded By:</span><span>{formatPHP(creditCheckResult.exceededBy)}</span>
                  </div>
                </div>
              </div>
            )}

            {creditCheckResult?.allowed && (
              <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
                <p className="text-sm text-amber-700">
                  This credit sale of <strong>{formatPHP(balanceDue)}</strong> requires authorization.
                </p>
              </div>
            )}

            {/* PIN / TOTP Input — supports all configured methods */}
            <div>
              <Label>Authorization Code</Label>
              <p className="text-[10px] text-slate-400 mb-2">
                Enter Admin PIN, Manager PIN, or TOTP code from Authenticator app
              </p>
              <Input
                data-testid="manager-pin"
                type="password" autoComplete="new-password"
                value={managerPin}
                onChange={e => setManagerPin(e.target.value)}
                placeholder="PIN or 6-digit TOTP code"
                className="text-center text-2xl tracking-widest h-14"
                onKeyDown={e => e.key === 'Enter' && managerPin && verifyManagerPin()}
              />
              <div className="flex flex-wrap gap-1.5 mt-2">
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-50 border border-emerald-200 text-emerald-700 font-medium">Admin PIN</span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-700 font-medium">Manager PIN</span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-50 border border-purple-200 text-purple-700 font-medium">TOTP (Authenticator)</span>
              </div>
            </div>

            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setCreditApprovalDialog(false); setManagerPin(''); }}>
                Cancel
              </Button>
              <Button 
                data-testid="verify-pin"
                className="flex-1 bg-amber-500 hover:bg-amber-600 text-white"
                onClick={verifyManagerPin}
                disabled={!managerPin || saving}
              >
                <CheckCircle2 size={16} className="mr-2" /> Authorize Sale
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* New Customer Dialog */}
      <Dialog open={newCustomerDialog} onOpenChange={setNewCustomerDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Create New Customer</DialogTitle>
            <DialogDescription>Add a new customer to use in this sale</DialogDescription>
          </DialogHeader>
          
          <div className="space-y-4">
            <div>
              <Label>Customer Name *</Label>
              <Input
                data-testid="new-cust-name"
                value={newCustForm.name}
                onChange={e => setNewCustForm({ ...newCustForm, name: e.target.value })}
                placeholder="Enter customer name"
                className="h-10"
                autoFocus
              />
            </div>
            <div>
              <Label>Phone Number</Label>
              <Input
                data-testid="new-cust-phone"
                value={newCustForm.phone}
                onChange={e => setNewCustForm({ ...newCustForm, phone: e.target.value })}
                placeholder="09xx xxx xxxx"
              />
            </div>
            <div>
              <Label>Address</Label>
              <Input
                value={newCustForm.address}
                onChange={e => setNewCustForm({ ...newCustForm, address: e.target.value })}
                placeholder="Customer address"
              />
            </div>
            <div>
              <Label>Price Scheme</Label>
              <Select value={newCustForm.price_scheme} onValueChange={v => setNewCustForm({ ...newCustForm, price_scheme: v })}>
                <SelectTrigger className="h-10">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {schemes.map(s => (
                    <SelectItem key={s.key} value={s.key}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            
            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={() => setNewCustomerDialog(false)}>
                Cancel
              </Button>
              <Button 
                data-testid="save-new-customer"
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                onClick={createNewCustomer}
              >
                <Plus size={16} className="mr-2" /> Create Customer
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Price Save Dialog */}
      <Dialog open={priceSaveDialog} onOpenChange={(o) => { if (!o) dismissPriceSaveDialog(); }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Save Price Change?</DialogTitle>
            <DialogDescription>Choose whether to update the price scheme permanently</DialogDescription>
          </DialogHeader>
          {pendingPriceChange && (
            <div className="space-y-4">
              <div className="bg-slate-50 rounded-lg p-3 space-y-1">
                <p className="font-medium text-sm">{pendingPriceChange.product_name}</p>
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-slate-400 line-through">{formatPHP(pendingPriceChange.old_price)}</span>
                  <span className="text-[#1A4D2E] font-bold">{formatPHP(pendingPriceChange.new_price)}</span>
                  <Badge variant="outline" className="capitalize text-[10px]">{pendingPriceChange.scheme_name}</Badge>
                </div>
              </div>
              <p className="text-sm text-slate-600">
                Save <strong>{formatPHP(pendingPriceChange.new_price)}</strong> as the new <strong>{pendingPriceChange.scheme_name}</strong> price for this product?
              </p>
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={dismissPriceSaveDialog}>
                  No, this sale only
                </Button>
                <Button
                  data-testid="save-price-to-scheme"
                  className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  onClick={savePriceToScheme}
                >
                  Yes, update price
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Scheme Save Dialog */}
      <Dialog open={schemeSaveDialog} onOpenChange={(o) => { if (!o) { setSchemeSaveDialog(false); setPendingSchemeChange(null); } }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Update Customer Scheme?</DialogTitle>
            <DialogDescription>
              Save this price scheme for {selectedCustomer?.name}?
            </DialogDescription>
          </DialogHeader>
          {pendingSchemeChange && (
            <div className="space-y-4">
              <div className="bg-slate-50 rounded-lg p-3 space-y-1">
                <p className="font-medium text-sm">{selectedCustomer?.name}</p>
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-slate-400 capitalize">{schemes.find(s => s.key === selectedCustomer?.price_scheme)?.name || selectedCustomer?.price_scheme}</span>
                  <span className="text-slate-300">→</span>
                  <span className="text-[#1A4D2E] font-bold capitalize">
                    {schemes.find(s => s.key === pendingSchemeChange.newScheme)?.name || pendingSchemeChange.newScheme}
                  </span>
                </div>
              </div>
              <p className="text-sm text-slate-600">
                Save <strong>{schemes.find(s => s.key === pendingSchemeChange.newScheme)?.name || pendingSchemeChange.newScheme}</strong> as {selectedCustomer?.name}'s default price scheme?
              </p>
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => { setSchemeSaveDialog(false); setPendingSchemeChange(null); }}>
                  No, this sale only
                </Button>
                <Button
                  data-testid="save-scheme-to-customer"
                  className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  onClick={saveSchemeToCustomer}
                >
                  Yes, update customer
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Customer Info Save Dialog */}
      <Dialog open={custSaveDialog} onOpenChange={(o) => { if (!o) { proceedToCheckoutAfterCustSave(); } }}>
        <DialogContent className="sm:max-w-sm" data-testid="cust-save-dialog">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Save Customer Changes?</DialogTitle>
            <DialogDescription>
              You edited {selectedCustomer?.name}'s info. Save to their permanent record?
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {custEdits.phone !== (selectedCustomer?.phone || '') && (
              <div className="bg-slate-50 rounded-lg px-3 py-2 text-sm">
                <span className="text-slate-400">Phone:</span>{' '}
                <span className="line-through text-slate-400 mr-1">{selectedCustomer?.phone || '(empty)'}</span>
                <span className="text-[#1A4D2E] font-medium">{custEdits.phone || '(empty)'}</span>
              </div>
            )}
            {custEdits.address !== (selectedCustomer?.address || '') && (
              <div className="bg-slate-50 rounded-lg px-3 py-2 text-sm">
                <span className="text-slate-400">Address:</span>{' '}
                <span className="line-through text-slate-400 mr-1">{selectedCustomer?.address || '(empty)'}</span>
                <span className="text-[#1A4D2E] font-medium">{custEdits.address || '(empty)'}</span>
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={proceedToCheckoutAfterCustSave} data-testid="cust-save-skip">
                This order only
              </Button>
              <Button
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                onClick={saveCustomerEditsAndCheckout}
                data-testid="cust-save-permanent"
              >
                Save to record
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      </>
      )}

      {/* ── SALE DETAIL MODAL ────────────────────────────────────────────── */}
      {selectedInvoice && (
        <div
          className="fixed inset-0 flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 9999 }}
          onClick={e => { if (e.target === e.currentTarget) setSelectedInvoice(null); }}
        >
          <div className="bg-white rounded-2xl shadow-2xl w-full overflow-y-auto" style={{ maxWidth: '520px', maxHeight: '90vh' }}>
            <div className="p-5">
              {/* Header */}
              <div className="flex items-start justify-between mb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-lg font-mono text-blue-700">{selectedInvoice.invoice_number}</span>
                    {selectedInvoice.status === 'voided' ? (
                      <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-500">VOIDED</span>
                    ) : selectedInvoice.payment_type === 'cash' || !selectedInvoice.customer_id ? (
                      <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-700">Walk-in / Cash</span>
                    ) : (
                      <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-amber-100 text-amber-700">Credit Sale</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {selectedInvoice.invoice_date || selectedInvoice.order_date} · {selectedInvoice.cashier_name || 'Unknown cashier'}
                  </p>
                  {selectedInvoice.customer_name && selectedInvoice.customer_name !== 'Walk-in' && (
                    <p className="text-sm font-semibold text-slate-700 mt-0.5">{selectedInvoice.customer_name}</p>
                  )}
                  {selectedInvoice.status === 'voided' && (
                    <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2">
                      <p className="text-xs text-red-700 font-semibold">Voided: {selectedInvoice.void_reason}</p>
                      <p className="text-[10px] text-red-500">By {selectedInvoice.void_authorized_by} · {selectedInvoice.voided_at?.slice(0, 16)?.replace('T', ' ')}</p>
                    </div>
                  )}
                  {selectedInvoice.interest_accrued > 0 && (
                    <div className="mt-1 rounded-lg bg-amber-50 border border-amber-200 px-3 py-1.5">
                      <p className="text-xs text-amber-700">Interest accrued: <b>{formatPHP(selectedInvoice.interest_accrued)}</b> · Rate: {selectedInvoice.interest_rate}%/mo</p>
                    </div>
                  )}
                </div>
                <button onClick={() => setSelectedInvoice(null)} className="w-7 h-7 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center">
                  <X size={14} className="text-slate-500" />
                </button>
              </div>

              {/* Items */}
              <div className="rounded-xl border border-slate-200 overflow-hidden mb-4">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-slate-500">Item</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-500 w-12">Qty</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-500 w-20">Price</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-500 w-20">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(selectedInvoice.items || []).map((item, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-3 py-2">
                          <p className="font-medium">{item.product_name || item.name}</p>
                          {item.description && <p className="text-[10px] text-slate-400">{item.description}</p>}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">{item.quantity}</td>
                        <td className="px-3 py-2 text-right font-mono">{formatPHP(item.rate || item.price || 0)}</td>
                        <td className="px-3 py-2 text-right font-mono font-semibold">{formatPHP(item.total || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Totals */}
              <div className="space-y-1 mb-4">
                {selectedInvoice.freight > 0 && (
                  <div className="flex justify-between text-xs text-slate-500"><span>Freight</span><span className="font-mono">{formatPHP(selectedInvoice.freight)}</span></div>
                )}
                {selectedInvoice.overall_discount > 0 && (
                  <div className="flex justify-between text-xs text-emerald-600"><span>Discount</span><span className="font-mono">-{formatPHP(selectedInvoice.overall_discount)}</span></div>
                )}
                <div className="flex justify-between text-sm font-bold border-t border-slate-200 pt-1.5 mt-1.5">
                  <span>Grand Total</span><span className="font-mono text-[#1A4D2E]">{formatPHP(selectedInvoice.grand_total)}</span>
                </div>
                <div className="flex justify-between text-xs text-slate-500">
                  <span>Amount Paid</span><span className="font-mono text-emerald-700">{formatPHP(selectedInvoice.amount_paid)}</span>
                </div>
                {selectedInvoice.balance > 0 && (
                  <div className="flex justify-between text-sm font-semibold text-amber-700">
                    <span>Balance Due</span><span className="font-mono">{formatPHP(selectedInvoice.balance)}</span>
                  </div>
                )}
              </div>

              {/* E-Payment Details & Receipt */}
              {(selectedInvoice.fund_source === 'digital' || selectedInvoice.fund_source === 'split') && (
                <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-blue-800 uppercase">E-Payment Details</span>
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                      selectedInvoice.receipt_review_status === 'reviewed' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
                    }`}>
                      {selectedInvoice.receipt_review_status === 'reviewed' ? 'Verified' : 'Pending Verification'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                    <div><span className="text-slate-500">Platform:</span> <span className="font-semibold">{selectedInvoice.digital_platform || 'N/A'}</span></div>
                    <div><span className="text-slate-500">Ref #:</span> <span className="font-mono font-semibold">{selectedInvoice.digital_ref_number || 'N/A'}</span></div>
                    {selectedInvoice.digital_sender && <div><span className="text-slate-500">Sender:</span> <span className="font-semibold">{selectedInvoice.digital_sender}</span></div>}
                    {selectedInvoice.fund_source === 'split' && (
                      <>
                        <div><span className="text-slate-500">Cash:</span> <span className="font-mono text-emerald-700">{formatPHP(selectedInvoice.cash_amount || 0)}</span></div>
                        <div><span className="text-slate-500">Digital:</span> <span className="font-mono text-blue-700">{formatPHP(selectedInvoice.digital_amount || 0)}</span></div>
                      </>
                    )}
                  </div>
                  {/* Receipt photos */}
                  {selectedInvoice._receipts && selectedInvoice._receipts.length > 0 ? (
                    <div>
                      <p className="text-[10px] text-blue-600 font-semibold mb-1">Receipt Screenshot(s):</p>
                      <div className="flex gap-2 overflow-x-auto">
                        {selectedInvoice._receipts.map((r, i) => (
                          <a key={i} href={r.url} target="_blank" rel="noopener noreferrer"
                            className="shrink-0 w-16 h-16 rounded-lg border border-blue-200 overflow-hidden hover:ring-2 hover:ring-blue-400">
                            <img src={r.url} alt="Receipt" className="w-full h-full object-cover" />
                          </a>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between">
                      <p className="text-[10px] text-blue-500">No receipt screenshot uploaded yet</p>
                      <button
                        onClick={async () => {
                          try {
                            const qrRes = await api.post(`${process.env.REACT_APP_BACKEND_URL}/api/uploads/generate-link`, {
                              record_type: 'invoice', record_id: selectedInvoice.id,
                            });
                            showReceiptDialog({ invoice_id: selectedInvoice.id, invoice_number: selectedInvoice.invoice_number, ...qrRes.data });
                          } catch { toast.error('Failed to generate upload link'); }
                        }}
                        className="text-[10px] font-semibold text-blue-700 hover:text-blue-900 underline"
                        data-testid="upload-receipt-btn"
                      >Upload Now</button>
                    </div>
                  )}
                  {/* Verify button for admin */}
                  {selectedInvoice.receipt_review_status !== 'reviewed' && selectedInvoice._receipts?.length > 0 && (
                    <button
                      onClick={async () => {
                        const pin = prompt('Enter manager PIN to verify this payment:');
                        if (!pin) return;
                        try {
                          await api.post(`/uploads/mark-reviewed/invoice/${selectedInvoice.id}`, { pin });
                          toast.success('Payment verified!');
                          setSelectedInvoice({ ...selectedInvoice, receipt_review_status: 'reviewed' });
                          loadHistory();
                        } catch (e) { toast.error(e.response?.data?.detail || 'Verification failed'); }
                      }}
                      className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors"
                      data-testid="verify-epayment-btn"
                    >
                      <CheckCircle2 size={13} /> Verify E-Payment
                    </button>
                  )}
                </div>
              )}

              {/* Action buttons */}
              {selectedInvoice.status !== 'voided' && (
                <button
                  onClick={() => setVoidDialog(true)}
                  className="w-full py-2.5 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 text-sm font-medium transition-colors flex items-center justify-center gap-2"
                  data-testid="reopen-sale-btn"
                >
                  <RefreshCw size={14} /> Void &amp; Re-open for Editing
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── VOID CONFIRMATION ─────────────────────────────────────────────── */}
      {voidDialog && selectedInvoice && (
        <div
          className="fixed inset-0 flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 99999 }}
          onClick={e => { if (e.target === e.currentTarget) { setVoidDialog(false); setVoidReason(''); setVoidPin(''); } }}
        >
          <div className="bg-white rounded-2xl shadow-2xl w-full p-5" style={{ maxWidth: '400px' }}>
            <p className="font-bold text-slate-800 mb-0.5">Void & Reopen Sale</p>
            <p className="text-xs text-slate-500 mb-4">{selectedInvoice.invoice_number} · {formatPHP(selectedInvoice.grand_total)}</p>

            <div className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2.5 mb-4 text-xs text-amber-800">
              This will: reverse inventory, reverse cashflow
              {selectedInvoice.balance > 0 ? ', and reverse customer AR balance.' : '.'}
              <br />
              {selectedInvoice.customer_id && <><b>Interest note:</b> Original invoice date will be preserved when re-saved.</>}
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-slate-600 block mb-1">Reason *</label>
                <textarea
                  value={voidReason}
                  onChange={e => setVoidReason(e.target.value)}
                  placeholder="e.g. Wrong item entered, customer cancelled..."
                  rows={2}
                  className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none resize-none focus:ring-2 focus:ring-red-200"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 block mb-1">Manager PIN *</label>
                <Input
                  type="password" autoComplete="new-password"
                  value={voidPin}
                  onChange={e => setVoidPin(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleVoidInvoice()}
                  placeholder="Enter manager PIN"
                  className="h-9"
                  autoFocus
                />
              </div>
            </div>

            <div className="flex gap-2 mt-4">
              <button
                onClick={() => { setVoidDialog(false); setVoidReason(''); setVoidPin(''); }}
                className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={handleVoidInvoice}
                disabled={voidSaving || !voidReason || !voidPin}
                className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-700 text-white text-sm font-semibold disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {voidSaving ? <RefreshCw size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Void & Reverse
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── DIGITAL RECEIPT UPLOAD — MANDATORY, NON-DISMISSIBLE ─────── */}
      {showDigitalQR && digitalReceiptQR && (
        <div
          data-testid="receipt-upload-overlay"
          className="fixed inset-0 flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.92)', zIndex: 99999 }}
          onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation(); }}
        >
          <div className="bg-white rounded-2xl shadow-2xl w-full p-5" style={{ maxWidth: '400px' }}>
            <div className="text-center mb-4">
              <div className="w-14 h-14 rounded-full bg-red-100 flex items-center justify-center mx-auto mb-3">
                <Camera size={26} className="text-red-600" />
              </div>
              <p className="font-bold text-slate-800 text-lg">Receipt Upload Required</p>
              <p className="font-semibold text-blue-700 mt-1">{digitalReceiptQR.invoice_number}</p>
              <p className="text-xs text-slate-500 mt-2">Upload a screenshot or photo of the e-payment transfer. This step <strong>cannot be skipped</strong>.</p>
              <div className="mt-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg">
                <p className="text-[11px] text-amber-700 font-medium">You cannot close this dialog or navigate away until the receipt is uploaded.</p>
              </div>
            </div>

            {/* Direct upload from device */}
            <div className="mb-3">
              <label
                data-testid="receipt-upload-input-label"
                className={`flex items-center justify-center gap-2 py-3 rounded-xl border-2 border-dashed cursor-pointer transition-all ${
                  digitalReceiptQR._uploaded
                    ? 'border-green-400 bg-green-50 text-green-700'
                    : 'border-blue-300 bg-blue-50 hover:bg-blue-100 text-blue-700'
                }`}
              >
                {digitalReceiptQR._uploading ? (
                  <><RefreshCw size={16} className="animate-spin" /> Uploading...</>
                ) : digitalReceiptQR._uploaded ? (
                  <><Check size={16} /> Receipt uploaded</>
                ) : (
                  <><Camera size={16} /> Take Photo or Choose File</>
                )}
                <input
                  data-testid="receipt-upload-input"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    setDigitalReceiptQR(prev => ({ ...prev, _uploading: true }));
                    try {
                      const formData = new FormData();
                      formData.append('files', file);
                      formData.append('record_type', 'invoice');
                      if (digitalReceiptQR._sessionId) {
                        formData.append('session_id', digitalReceiptQR._sessionId);
                      }
                      const uploadRes = await api.post('/uploads/direct', formData, {
                        headers: { 'Content-Type': 'multipart/form-data' },
                      });
                      const sid = uploadRes.data?.session_id || digitalReceiptQR._sessionId;
                      // Link session to invoice
                      if (sid && digitalReceiptQR.invoice_id) {
                        await api.post('/uploads/reassign', {
                          session_id: sid,
                          record_type: 'invoice',
                          record_id: digitalReceiptQR.invoice_id,
                        }).catch(() => {});
                      }
                      setDigitalReceiptQR(prev => ({ ...prev, _uploading: false, _uploaded: true, _sessionId: sid, _fileCount: (prev._fileCount || 0) + 1 }));
                      toast.success('Receipt uploaded!');
                    } catch (err) {
                      setDigitalReceiptQR(prev => ({ ...prev, _uploading: false }));
                      toast.error('Upload failed — try again');
                    }
                    e.target.value = '';
                  }}
                />
              </label>
              {digitalReceiptQR._fileCount > 0 && (
                <p className="text-xs text-green-600 text-center mt-1">{digitalReceiptQR._fileCount} file(s) uploaded</p>
              )}
            </div>

            {/* QR option — always visible alongside direct upload */}
            {digitalReceiptQR.token && (
              <div className="mb-3">
                <div className="flex items-center gap-2 mb-2">
                  <div className="flex-1 h-px bg-slate-200" />
                  <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">or upload from phone</span>
                  <div className="flex-1 h-px bg-slate-200" />
                </div>
                <div className="flex justify-center">
                  <div style={{ border: '2px solid #93c5fd', borderRadius: '10px', padding: '6px', background: '#fff' }}>
                    <img
                      src={`https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=${encodeURIComponent(`${window.location.origin}/upload/${digitalReceiptQR.token}`)}`}
                      alt="QR Code"
                      width={120} height={120}
                      style={{ display: 'block' }}
                    />
                  </div>
                </div>
                <p className="text-[10px] text-slate-400 text-center mt-1.5">Scan QR with phone camera to upload screenshot</p>
              </div>
            )}

            <button
              data-testid="receipt-done-btn"
              onClick={() => {
                if (!digitalReceiptQR._uploaded) {
                  toast.error('You must upload the e-payment receipt before proceeding');
                  return;
                }
                closeReceiptDialog();
              }}
              disabled={!digitalReceiptQR._uploaded}
              className={`w-full py-3 rounded-xl text-sm font-semibold transition-all ${
                digitalReceiptQR._uploaded
                  ? 'bg-green-600 hover:bg-green-700 text-white'
                  : 'bg-slate-200 text-slate-400 cursor-not-allowed'
              }`}
            >
              {digitalReceiptQR._uploaded ? 'Done — Proceed' : 'Upload receipt to continue'}
            </button>
          </div>
        </div>
      )}

      {/* Scanner Link QR Dialog */}
      <Dialog open={scannerQrOpen} onOpenChange={setScannerQrOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Smartphone size={18} /> Link Phone Scanner
            </DialogTitle>
            <DialogDescription>
              Scan this QR code with your phone camera to connect as a barcode scanner
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col items-center gap-4 py-4">
            {scannerSession && (
              <>
                <div className="bg-white p-3 rounded-xl border-2 border-slate-200">
                  <img
                    src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(`${window.location.origin}/scanner/${scannerSession.session_id}`)}`}
                    alt="Scanner QR"
                    width={200} height={200}
                  />
                </div>
                <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium ${
                  scannerConnected ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                }`}>
                  {scannerConnected ? (
                    <><CheckCircle2 size={14} /> Phone Connected</>
                  ) : (
                    <><Wifi size={14} className="animate-pulse" /> Waiting for phone...</>
                  )}
                </div>
                <p className="text-xs text-slate-400 text-center">
                  Branch-locked scanner session. Scanned products will appear in your cart automatically.
                </p>
              </>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={() => setScannerQrOpen(false)}>
              {scannerConnected ? 'Minimize' : 'Close'}
            </Button>
            <Button variant="destructive" className="flex-1" onClick={closeScannerSession} data-testid="disconnect-scanner-btn">
              Disconnect
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <ReferenceNumberPrompt
        open={refPrompt.open}
        onClose={() => setRefPrompt(p => ({ ...p, open: false }))}
        referenceNumber={refPrompt.number}
        type="sale"
        title={refPrompt.title}
        invoiceData={refPrompt.invoiceData}
        businessInfo={bizInfo}
      />

      {/* Insufficient Stock Override Modal */}
      <InsufficientStockModal
        open={stockOverrideModal}
        insufficientItems={insufficientItems}
        onOverride={handleStockOverride}
        onCancel={() => {
          setStockOverrideModal(false);
          setPendingSaleData(null);
          setInsufficientItems([]);
        }}
        onGoPO={() => {
          setStockOverrideModal(false);
          setPendingSaleData(null);
          setInsufficientItems([]);
          window.location.href = '/purchase-orders';
        }}
      />

    </div>
  );
}
