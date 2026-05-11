import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP, fmtDateTime, fmtDate } from '../lib/utils';
import { formatTime, localTodayStr } from '../lib/dateFormat';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import CalcInput from '../components/CalcInput';
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
  PackageX, ShieldAlert, ChevronDown, Eye, EyeOff, User, Package, PauseCircle, Inbox, RotateCcw,
  ArrowUpRight, ArrowDownRight, Info, Unlock, Clock
} from 'lucide-react';
import { toast } from 'sonner';
import {
  cacheProducts, getProducts, cacheCustomers, getCustomers,
  cachePriceSchemes, getPriceSchemes, addPendingSale, getPendingSaleCount,
  setOfflineAdminPinHash, setOfflinePinGrants,
  getNextOfflineReceiptNumber, getPendingDraftCompletions,
} from '../lib/offlineDB';
import { syncPendingSales, startAutoSync, stopAutoSync, newEnvelopeId } from '../lib/syncManager';
import { isTrueNetworkError, pingBackendHealth } from '../lib/connectivity';
import { useConnectivity } from '../lib/useConnectivity';
import { useHistoricalCredit } from '../lib/useHistoricalCredit';
import {
  buildParkPayload, parkSale, discardParkedSale, consumeParkedSale, loadParkedSales, drainSyncQueue as drainParkQueue,
} from '../lib/parkedSalesSync';
import { useUnsavedChangesGuard } from '../lib/useUnsavedChangesGuard';
import ReferenceNumberPrompt from '../components/ReferenceNumberPrompt';
import CropCreditTypeDialog from '../components/CropCreditTypeDialog';
import RequestSignatureDialog from '../components/RequestSignatureDialog';
import { invalidateBalanceCache } from '../components/CustomerBalanceBadge';
import PrintEngine from '../lib/PrintEngine';
import GlobalPriceBadge from '../components/GlobalPriceBadge';
import PriceMatchModal from '../components/PriceMatchModal';
import LateEncodeDialog from '../components/LateEncodeDialog';
import HistoricalCreditBanner from '../components/HistoricalCreditBanner';
import HistoricalCreditDialog from '../components/HistoricalCreditDialog';
import CheckoutDialog from '../components/CheckoutDialog';

// ── Bounded Levenshtein distance ─────────────────────────────────────────────
// Returns the edit distance between `a` and `b`, BUT bails out early as soon
// as the running minimum exceeds `maxDist`. This keeps the typo-tolerant
// product search fast even when scanning hundreds of candidate words: most
// pairs short-circuit on the first row and never allocate the full matrix.
function levenshteinAtMost(a, b, maxDist) {
  if (a === b) return 0;
  const la = a.length, lb = b.length;
  if (Math.abs(la - lb) > maxDist) return maxDist + 1;
  if (la === 0) return lb;
  if (lb === 0) return la;
  // Two-row DP with early-exit on each row.
  let prev = new Array(lb + 1);
  let curr = new Array(lb + 1);
  for (let j = 0; j <= lb; j++) prev[j] = j;
  for (let i = 1; i <= la; i++) {
    curr[0] = i;
    let rowMin = curr[0];
    for (let j = 1; j <= lb; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min(
        prev[j] + 1,        // deletion
        curr[j - 1] + 1,    // insertion
        prev[j - 1] + cost, // substitution
      );
      if (curr[j] < rowMin) rowMin = curr[j];
    }
    if (rowMin > maxDist) return maxDist + 1; // early bail-out
    [prev, curr] = [curr, prev];
  }
  return prev[lb];
}

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

// ── PIN Session Badge ────────────────────────────────────────────────────────
// Shows a countdown when a PIN session is active (3 min warm window).
const PIN_SESSION_TTL = 3 * 60 * 1000;

function PinSessionBadge({ session, onExpired }) {
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    const update = () => {
      const left = Math.max(0, PIN_SESSION_TTL - (Date.now() - session.at));
      setRemaining(left);
      if (left <= 0) onExpired();
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [session.at, onExpired]);
  if (remaining <= 0) return null;
  const mins = Math.floor(remaining / 60000);
  const secs = Math.floor((remaining % 60000) / 1000);
  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 text-[11px] font-medium"
      data-testid="pin-session-badge"
      title={`PIN session active — ${session.name || 'Authorized'}. Expires in ${mins}:${secs.toString().padStart(2, '0')}. Will auto-bypass PIN prompts for permitted actions.`}
    >
      <Unlock size={11} />
      <span>PIN active{session.name ? ` · ${session.name}` : ''}</span>
      <span className="font-mono text-[10px] tabular-nums opacity-75">{mins}:{secs.toString().padStart(2, '0')}</span>
    </div>
  );
}

const EMPTY_LINE = {
  product_id: '', product_name: '', description: '',
  quantity: 1, rate: 0, original_rate: 0,
  cost_price: 0, moving_average_cost: 0, last_purchase_cost: 0,
  effective_capital: 0, capital_method: 'manual',
  discount_type: 'amount', discount_value: 0, is_repack: false,
};

// Returns today's date in YYYY-MM-DD using the ORG's configured timezone.
// Phase 4 cleanup — the page-local `localTodayStr()` helper was previously a
// near-duplicate of `lib/dateFormat.js#localTodayStr`. Both read the same
// `agribooks.org_tz` localStorage key with a Manila fallback and produce
// the same YYYY-MM-DD string. The page now imports the shared helper and
// uses it directly. No behavior change.

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
  const [historyDate, setHistoryDate] = useState(localTodayStr());
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

  // ── JIT repack retail PIN modal ──────────────────────────────────────────
  const [jitPinModal, setJitPinModal] = useState({ open: false, items: [], saleData: null });
  const [lateEncodeDialog, setLateEncodeDialog] = useState({ open: false, saleData: null, signatureData: null });
  const [jitPin, setJitPin] = useState('');

  // ── Pre-invoice signature (credit/partial) ───────────────────────────────
  // When set, signature dialog opens BEFORE invoice creation. After signing
  // (or bypass), processSale is called with the captured signature data so
  // the invoice is created with signature already attached.
  const [preInvoiceSig, setPreInvoiceSig] = useState(null);

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
  // Debounced search — heavy filter only runs 150ms after last keystroke
  // so typing in a 1000+ product catalog doesn't lock up the UI thread on
  // every letter (Iter 222 perf fix).
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 150);
    return () => clearTimeout(t);
  }, [search]);
  // Set when the strict search returns 0 results AND the fuzzy fallback
  // produced suggestions. The UI shows a banner so the user is never
  // confused about why an unexpected product appears in the grid.
  const [fuzzyHint, setFuzzyHint] = useState(null);
  const [customers, setCustomers] = useState([]);
  const [schemes, setSchemes] = useState([]);
  const [terms, setTerms] = useState([]);
  const [prefixes, setPrefixes] = useState({});
  const [users, setUsers] = useState([]);
  
  // Cart/Lines
  const [cart, setCart] = useState([]);
  const [lines, setLines] = useState([{ ...EMPTY_LINE }]);

  // Set of product IDs at the current branch that haven't been
  // price-reviewed yet — used to show the "Global Price" badge
  // inline on POS line items.
  const [pendingReviewIds, setPendingReviewIds] = useState(new Set());

  // ── Capital cost reveal (PIN-gated) ──────────────────────────────────
  // capitalShown: when true, every product card / line item shows
  //   `Stock: X · Cap: ₱Y · LP: ₱Z · MA: ₱A`. When false, only stock is shown.
  // costMap: { product_id: { effective_cost, last_purchase, moving_average } }
  // capitalPin: the validated PIN — kept in memory only; refresh = re-PIN.
  // capitalPinModal: when truthy, shows the unlock PIN dialog.
  // Default state: hidden for all roles. Admin/manager can re-enter the PIN
  // to reveal — the ground rule is "showing requires PIN" regardless of role.
  const [capitalShown, setCapitalShown] = useState(false);
  const [costMap, setCostMap] = useState({});
  const [capitalPin, setCapitalPin] = useState('');
  const [capitalPinModal, setCapitalPinModal] = useState(false);
  const [capitalPinInput, setCapitalPinInput] = useState('');
  const [capitalPinSubmitting, setCapitalPinSubmitting] = useState(false);
  
  // Customer
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [custSearch, setCustSearch] = useState('');
  const [custDropdownOpen, setCustDropdownOpen] = useState(false);
  const [newCustomerDialog, setNewCustomerDialog] = useState(false);
  const [newCustForm, setNewCustForm] = useState({ name: '', phone: '', address: '', price_scheme: 'retail' });
  
  // Order header
  const [header, setHeader] = useState({
    terms: 'COD', terms_days: 0, customer_po: '', sales_rep_id: '', sales_rep_name: '',
    prefix: 'SI', order_date: localTodayStr(),
    shipping_address: '', location: '', mod: '', check_number: '', req_ship_date: '', notes: '',
  });
  const [freight, setFreight] = useState(0);
  const [overallDiscount, setOverallDiscount] = useState(0);

  // Closed-day enforcement
  const [lastCloseDate, setLastCloseDate] = useState(null);
  const [floorDate, setFloorDate] = useState(null); // earliest operational date — blocks dates before this
  const [dateError, setDateError] = useState(null); // inline error shown below the Sale Date field

  // Phase 4A — admins/owners can encode notebook AR (Historical Credit /
  // Late-AR) that's older than the 7-day late-encode window. We keep the
  // existing 7-day cap for everyone else so cashier UX is unchanged.
  const isPrivilegedRole = useMemo(
    () => user?.is_super_admin
      || ['admin', 'owner', 'super_admin'].includes(user?.role || ''),
    [user?.role, user?.is_super_admin],
  );

  // Min selectable date for the date picker. Iter 243.1: expanded back by 7
  // days so cashiers can pick a closed past day to trigger the Late-Encode
  // flow (credit/partial only — server enforces). Capped at floorDate so
  // dates before the system existed are still blocked.
  const minAllowedDate = useMemo(() => {
    const today = localTodayStr();
    // Phase 4A: admins/owners may pick any past date (down to floorDate)
    // for Historical Credit / Notebook AR encoding.
    if (isPrivilegedRole) {
      return floorDate || '2000-01-01';
    }
    const d = new Date(today + 'T12:00:00');
    d.setDate(d.getDate() - 7); // 7-day late-encode window (backend cap)
    let candidate = d.toISOString().slice(0, 10);
    if (floorDate && candidate < floorDate) candidate = floorDate;
    return candidate;
  }, [floorDate, isPrivilegedRole]);

  // Iter 243: Max selectable date = today, OR tomorrow IF today is already
  // closed. This prevents the native date input from dead-locking (min > max
  // if today is closed) and exactly encodes the business rule: sales made
  // after the mid-day close get dated to the next open business day.
  // Forward-dating beyond +1 day is blocked (prevents stock laundering — see
  // /app/frontend/src/lib/nextOpenDate.js header for the full rationale).
  const maxAllowedDate = useMemo(() => {
    const today = localTodayStr();
    if (lastCloseDate && lastCloseDate >= today) {
      const d = new Date(today + 'T12:00:00');
      d.setDate(d.getDate() + 1);
      return d.toISOString().slice(0, 10);
    }
    return today;
  }, [lastCloseDate]);

  // ── Phase 4A — Historical Credit / Notebook AR triggers ─────────────
  // `daysBack` is computed here (still needed by both the HC mode flag
  // and the date-error banner). The HC mode flag itself + 12 state
  // fields + 4 helpers now live in `lib/useHistoricalCredit.js` —
  // wired below right after the cart/customer/totals are computed.
  const daysBack = useMemo(() => {
    if (!header.order_date) return 0;
    const today = localTodayStr();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(header.order_date)) return 0;
    const t1 = new Date(today + 'T00:00:00').getTime();
    const t2 = new Date(header.order_date + 'T00:00:00').getTime();
    return Math.round((t1 - t2) / 86400000);
  }, [header.order_date]);

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

  // ── Price Match flow (replaces ad-hoc "save as global price" dialog) ──────
  // When a cashier edits a line's price, the line is flagged with a yellow
  // badge and `original_price`/`original_rate` is preserved. At checkout, if
  // any line has `price !== original_price`, we open the PriceMatchModal to
  // collect a reason + manager PIN. The data is then sent to /unified-sale
  // which persists the new price to branch_prices and writes price_change_log.
  const [priceMatchModal, setPriceMatchModal] = useState(false);
  const [priceMatchSubmitting, setPriceMatchSubmitting] = useState(false);
  const [priceMatchError, setPriceMatchError] = useState('');
  // Approved payload, set after the modal is confirmed: { price_changes, pin }
  const [priceMatchApproved, setPriceMatchApproved] = useState(null);
  
  // Checkout
  const [checkoutDialog, setCheckoutDialog] = useState(false);
  const [paymentType, setPaymentType] = useState('cash'); // cash, partial, credit
  const [amountTendered, setAmountTendered] = useState(0);
  const [partialPayment, setPartialPayment] = useState(0);
  const [saving, setSaving] = useState(false);
  const [releaseMode, setReleaseMode] = useState('full'); // full | partial

  // ── Phase 4A — Historical Credit / Notebook AR (extracted) ─────────
  // State, trigger flags, and helpers now live in
  // `lib/useHistoricalCredit.js`. The hook is wired below right after
  // the cart/lines/totals/customer/branch are computed (see `hc =
  // useHistoricalCredit(...)` further down). We continue to destructure
  // `isHistoricalCreditMode` and `isBackdatedNonCreditBlocked` into
  // local consts under their old names so the existing JSX and
  // `handleCreditSale` callsites remain unchanged.


  // Reference number prompt
  const [refPrompt, setRefPrompt] = useState({ open: false, number: '', title: '', invoiceData: null });

  // Business info for printing
  const [bizInfo, setBizInfo] = useState({});
  useEffect(() => { api.get('/settings/business-info').then(r => setBizInfo(r.data)).catch(() => {}); }, []);

  // Refresh pending-review product IDs when the active branch changes.
  const refreshPendingReviewIds = useCallback(async () => {
    if (!currentBranch?.id) { setPendingReviewIds(new Set()); return; }
    try {
      const r = await api.get('/inventory/pending-review-ids', { params: { branch_id: currentBranch.id } });
      setPendingReviewIds(new Set(r.data?.product_ids || []));
    } catch { /* silent — badge just won't show */ }
  }, [currentBranch]);
  useEffect(() => { refreshPendingReviewIds(); }, [refreshPendingReviewIds]);

  // ── Capital reveal: bulk-fetch costs once unlocked ──────────────────
  const fetchCostMap = useCallback(async (ids, pin) => {
    if (!ids?.length || !currentBranch?.id || !pin) return;
    try {
      const r = await api.post(`/products/cost-details`, {
        branch_id: currentBranch.id, product_ids: ids, pin,
      });
      setCostMap(prev => ({ ...prev, ...(r.data?.costs || {}) }));
    } catch (e) {
      // PIN expired / changed mid-session — re-lock
      if (e.response?.status === 403) {
        setCapitalShown(false);
        setCapitalPin('');
        toast.error('Capital view re-locked. Click "Show Capital" to enter PIN again.');
      }
    }
  }, [currentBranch]);

  const submitCapitalPin = async () => {
    if (!capitalPinInput) { toast.error('PIN required'); return; }
    if (!currentBranch?.id) { toast.error('Pick a branch first'); return; }
    setCapitalPinSubmitting(true);
    try {
      // Validate the PIN by issuing one fetch — if cost-details succeeds the PIN is good.
      // If there are no products visible yet, send a no-op array of one dummy id; backend
      // will validate PIN before short-circuiting (check is first thing).
      await api.post(`/products/cost-details`, {
        branch_id: currentBranch.id,
        product_ids: ['__validate__'],
        pin: capitalPinInput,
      });
      setCapitalPin(capitalPinInput);
      setCapitalShown(true);
      setCapitalPinModal(false);
      startPinSession(capitalPinInput, 'pin', '');
      setCapitalPinInput('');
      toast.success('Capital visible');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Invalid PIN');
    }
    setCapitalPinSubmitting(false);
  };

  const hideCapital = () => {
    setCapitalShown(false);
    setCapitalPin('');
    setCostMap({});
  };

  const requestShowCapital = () => {
    // Auto-bypass if PIN session is warm
    if (isPinSessionWarm()) {
      setCapitalPinInput(pinSessionRef.current.pin);
      // Directly submit with cached PIN
      (async () => {
        setCapitalPinSubmitting(true);
        try {
          await api.post('/products/cost-details', {
            branch_id: currentBranch?.id,
            product_ids: ['__validate__'],
            pin: pinSessionRef.current.pin,
          });
          setCapitalPin(pinSessionRef.current.pin);
          setCapitalShown(true);
          toast.success('Capital visible (PIN active)');
        } catch {
          // Fallback to dialog
          clearPinSession();
          setCapitalPinInput('');
          setCapitalPinModal(true);
        }
        setCapitalPinSubmitting(false);
      })();
      return;
    }
    setCapitalPinInput('');
    setCapitalPinModal(true);
  };

  // Fetch cost data for any line item whose product is missing from costMap.
  // Quick mode handles its own fetch inline (during render); Order mode lines
  // are fetched here whenever cart/lines change while capital is shown.
  useEffect(() => {
    if (!capitalShown || !capitalPin) return;
    const ids = new Set();
    lines.forEach(l => l.product_id && !(l.product_id in costMap) && ids.add(l.product_id));
    cart.forEach(c => c.product_id && !(c.product_id in costMap) && ids.add(c.product_id));
    if (ids.size) fetchCostMap(Array.from(ids), capitalPin);
  }, [capitalShown, capitalPin, lines, cart, costMap, fetchCostMap]);
  
  // Credit approval
  const [creditApprovalDialog, setCreditApprovalDialog] = useState(false);
  const [managerPin, setManagerPin] = useState('');
  // Iter 192 — Offline credit-sale bypass reason (required when offline)
  const [offlineBypassReason, setOfflineBypassReason] = useState('');
  const [creditCheckResult, setCreditCheckResult] = useState(null);
  const [pendingCreditSale, setPendingCreditSale] = useState(null);
  // Crop credit type selection
  const [cropTypeDialog, setCropTypeDialog] = useState(false);
  const [cropCreditConfig, setCropCreditConfig] = useState(null);
  // Customer signature flow (after credit sale)
  const [sigDialog, setSigDialog] = useState({ open: false, invoice: null });
  
  // Offline
  // Phase 4 Pass 1 — connectivity state + effects extracted into
  // `lib/useConnectivity`. Behavior is unchanged: same three-state
  // indicator, same 10s reconnect grace cooperation (the processSale
  // retry loop still drives the countdown via the exposed setters),
  // same 30s health probe, same auto-sync wiring, same offline-queue
  // pending count. `onReconnect` preserves the existing post-sync
  // call to `loadData(true)` that refreshes the product grid.
  const loadDataRef = useRef(null);
  const onReconnectLoadData = useCallback(async () => {
    if (loadDataRef.current) await loadDataRef.current(true);
  }, []);
  const {
    isOnline,
    connectivityStatus,
    setConnectivityStatus,
    reconnectCountdown,
    setReconnectCountdown,
    pendingCount,
    setPendingCount,
    refreshPendingCount,
  } = useConnectivity({ onReconnect: onReconnectLoadData });
  const [dataLoaded, setDataLoaded] = useState(false);

  // Parked / Draft sales (server-shared, offline-capable)
  const [parkedSales, setParkedSales] = useState([]);
  const [parkedDialogOpen, setParkedDialogOpen] = useState(false);
  const [parkLabel, setParkLabel] = useState('');
  const [parkPromptOpen, setParkPromptOpen] = useState(false);
  const [discardPinPrompt, setDiscardPinPrompt] = useState({ open: false, parkId: null, pin: '' });

  // Draft / For Preparation orders
  const [activeDraftId, setActiveDraftId] = useState(null);
  const [draftOrders, setDraftOrders] = useState([]);
  const [draftOrdersOpen, setDraftOrdersOpen] = useState(false);
  // Iter 252: pending offline completions overlay — maps draft_invoice_id →
  // offline_receipt_number for drafts that were finalized offline and are
  // queued to sync. Lets the Draft Orders panel show a yellow pending badge
  // and disable duplicate finalization until sync runs.
  const [pendingDraftCompletions, setPendingDraftCompletions] = useState({});
  const [preparingOrder, setPreparingOrder] = useState(false);

  // ── PIN Session: cache a successful PIN for 3 min (per-sale scope) ────────
  // Once verified, subsequent PIN gates auto-bypass or auto-fill the cached PIN
  // so cashiers/managers aren't re-prompted for every action in the same sale.
  const [pinSession, setPinSession] = useState(null);
  // { pin: '...', method: 'manager_pin'|'admin_pin'|'totp', name: 'Manager Name', at: Date.now() }
  const pinSessionRef = useRef(null);
  useEffect(() => { pinSessionRef.current = pinSession; }, [pinSession]);

  const isPinSessionWarm = useCallback(() => {
    const s = pinSessionRef.current;
    return !!(s && (Date.now() - s.at) < PIN_SESSION_TTL);
  }, []);

  const startPinSession = useCallback((pin, method, name) => {
    // Defensive: only accept string/number PINs. If anything else (e.g. a stray
    // SyntheticEvent from a misbound onClick) is passed, refuse to cache it —
    // a poisoned warm PIN would cascade into every downstream verify/void/stock
    // override flow and silently abort axios requests.
    const pinStr = (typeof pin === 'string' || typeof pin === 'number') ? String(pin).trim() : '';
    if (!pinStr) return;
    setPinSession({ pin: pinStr, method, name: name || '', at: Date.now() });
  }, []);

  const clearPinSession = useCallback(() => setPinSession(null), []);

  // Auto-fill manager PIN in credit approval dialog when PIN session is warm
  useEffect(() => {
    if (creditApprovalDialog && isPinSessionWarm() && !managerPin) {
      setManagerPin(pinSessionRef.current.pin);
    }
  }, [creditApprovalDialog]); // eslint-disable-line react-hooks/exhaustive-deps

  // Linked Scanner
  const [scannerSession, setScannerSession] = useState(null); // { session_id, branch_id }
  const [scannerConnected, setScannerConnected] = useState(false);
  const [scannerQrOpen, setScannerQrOpen] = useState(false);
  const [scannerCreating, setScannerCreating] = useState(false);
  const addToCartRef = useRef(null); // ref to always call latest addToCart
  
  const searchRef = useRef(null);
  const qtyRefs = useRef([]);
  // Refs to auto-scroll the most-recently added item into view, so cashiers
  // can immediately see/edit the price of the latest add without manually
  // scrolling. Quick mode → cart bottom; Order mode → newest line row.
  const cartEndRef = useRef(null);
  const orderLinesEndRef = useRef(null);
  const orderLinesScrollRef = useRef(null);

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



  // Online/Offline detection — Phase 4 Pass 1: state, effects, and
  // listeners now live in `useConnectivity`. We only need to bind
  // `loadData` to the ref so the hook can call it after a successful
  // reconnect-sync. Mount-once, exactly like before.
  useEffect(() => {
    loadDataRef.current = loadData;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadData = async (forceOnline = false) => {
    // ── Stale-while-revalidate (Iter 222 perf fix) ─────────────────────
    // 1) Paint from IndexedDB cache IMMEDIATELY so the cart, search, and
    //    product grid are usable in <100ms on every navigation back to
    //    Sales — no more 5–10 sec blank state while the server responds.
    // 2) When online, fire the network refresh in the background and swap
    //    the cache (and React state) once it returns. This keeps prices
    //    and stock counts fresh without blocking the UI.
    try {
      const [cachedProds, cachedCusts, cachedSchs] = await Promise.all([
        getProducts(), getCustomers(), getPriceSchemes(),
      ]);
      if (Array.isArray(cachedProds) && cachedProds.length > 0) {
        setAllProducts(cachedProds);
        setCustomers(cachedCusts || []);
        setSchemes(cachedSchs || []);
        setDataLoaded(true);
      }
    } catch { /* no cache yet — first ever load */ }

    const online = forceOnline || navigator.onLine;
    if (!online) {
      // Truly offline and no cache → fall back to the disk read result
      // (already applied above if it succeeded). Mark loaded either way.
      setDataLoaded(true);
      return;
    }

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
      // C-1 (Audit 2026-02): admin_pin_hash is no longer shipped — null
      // out any stale value so deferred verification kicks in. Identity-only
      // grants are persisted for "Approved by …" UI labels.
      await setOfflineAdminPinHash(null);
      if (Array.isArray(posRes.data.offline_pin_grants)) {
        const identityOnly = posRes.data.offline_pin_grants.map((g) => ({
          verifier_id: g.verifier_id,
          verifier_name: g.verifier_name,
          method: g.method,
          role: g.role,
        }));
        await setOfflinePinGrants(identityOnly);
      }
      setDataLoaded(true);
    } catch (e) {
      console.warn('API failed, keeping cached data');
      setDataLoaded(true);
    }
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
      startPinSession(voidPin, 'manager_pin', res.data.authorized_by);
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

  // Consume a handoff snapshot from sessionStorage (Void & Reopen from Movement History)
  const consumedReopenRef = useRef(false);
  useEffect(() => {
    if (consumedReopenRef.current) return;
    if (!customers || !customers.length) return;
    let snap = null;
    try {
      const raw = sessionStorage.getItem('reopen_sale_snapshot');
      if (!raw) return;
      snap = JSON.parse(raw);
      sessionStorage.removeItem('reopen_sale_snapshot');
    } catch { return; }
    if (snap && typeof snap === 'object') {
      consumedReopenRef.current = true;
      reopenAsSale(snap);
    }
  }, [customers]); // eslint-disable-line

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
        toast.error('Cannot switch to Quick Sale — per-line discounts exist. Remove them or stay in Detailed Sale.');
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

  // Filter products — token-based (order-independent) matching with a
  // guarded typo-tolerant fallback when the strict pass returns 0 hits.
  //
  // Strict pass: splits the query on whitespace/dashes/slashes/commas and
  // requires every non-empty token to match. Token rules:
  //   • 1–3 digit pure numbers ("1", "14", "200") must PREFIX-match a whole
  //     word in the name. So "2" matches "Galimax 2" and "Galimax 21" but
  //     not "Galimax 1" or a random "P-FINEX-2281" SKU. This stops short
  //     numerics from leaking via SKU collisions.
  //   • Everything else (alphanumeric, longer numbers, alpha words) does a
  //     plain substring match across name + SKU + barcode.
  // Order-independent: "Galimax 1 Poultry Feeds Pilmico" still matches
  // "Galimax 1 Pilmico - Poultry Feeds".
  //
  // Fuzzy fallback (only when strict returns 0):
  //   • Levenshtein distance ≤ 1 for tokens 4–7 chars, ≤ 2 for 8+ chars
  //   • Pure-numeric / short tokens MUST still match exactly per the strict
  //     rule above — prevents "Galimax 1" silently matching "Galimax 2"
  //   • Capped to the first 200 products by name to keep typing snappy
  // The banner state below tells the UI to surface a "Showing fuzzy matches
  // for 'X'" hint with a Clear button so the user is never surprised.
  // Pre-computed search index — built once per `allProducts` change, NOT
  // per keystroke. Each entry stores the lowercase name / sku / barcode,
  // the joined haystack, and the pre-split word arrays used by the strict
  // and fuzzy passes. Cuts per-keystroke work from O(n × split-regex) to
  // O(n × cheap-string-compare). With 1000+ products this is the
  // difference between snappy typing and a 100ms+ lag every letter.
  const productIndex = useMemo(() => allProducts.map(p => {
    const name = (p.name || '').toLowerCase();
    const sku = (p.sku || '').toLowerCase();
    const barcode = (p.barcode || '').toLowerCase();
    const haystack = `${name} ${sku} ${barcode}`;
    return {
      p,
      name,
      haystack,
      nameWords: name.split(/[\s\-/,]+/).filter(Boolean),
      words: haystack.split(/[\s\-/,]+/).filter(Boolean),
    };
  }), [allProducts]);

  useEffect(() => {
    const search = debouncedSearch;
    if (!search) {
      setFilteredProducts(allProducts);
      setFuzzyHint(null);
      return;
    }
    const q = search.toLowerCase().trim();
    const tokens = q.split(/[\s\-/,]+/).map(t => t.trim()).filter(Boolean);
    if (tokens.length === 0) {
      setFilteredProducts(allProducts);
      setFuzzyHint(null);
      return;
    }

    const isShortNumeric = (t) => /^[0-9]{1,3}$/.test(t);
    const tokenMatches = (t, haystack, nameWords) => {
      if (isShortNumeric(t)) {
        return nameWords.some(w => w.startsWith(t));
      }
      return haystack.includes(t);
    };

    // ── Strict pass ──
    const strict = [];
    for (const idx of productIndex) {
      const { p, name, haystack, nameWords } = idx;
      if (!tokens.every(t => tokenMatches(t, haystack, nameWords))) continue;
      // Rank: 0 = name starts with the full query (prefix), 1 = full query is
      // a contiguous substring of name, 2 = token-only match. Tiebreak by
      // name length so "Galimax 2" outranks "Galimax 2 Plus Premium".
      let rank = 2;
      if (name.startsWith(q)) rank = 0;
      else if (name.includes(q)) rank = 1;
      strict.push({ p, rank, len: name.length });
    }
    if (strict.length > 0) {
      strict.sort((a, b) => a.rank - b.rank || a.len - b.len);
      setFilteredProducts(strict.map(m => m.p));
      setFuzzyHint(null);
      return;
    }

    // ── Fuzzy fallback ──
    // Only "fuzzable" tokens get edit-distance checks. Short numerics still
    // need a prefix-of-word hit per the strict rule above (kept in
    // exactRequired) so grade numbers stay honest.
    const fuzzable = tokens.filter(
      t => t.length >= 4 && !/^[0-9]+$/.test(t) && !/^[0-9]+[a-z]+$/i.test(t),
    );
    const exactRequired = tokens.filter(t => !fuzzable.includes(t));

    if (fuzzable.length === 0) {
      setFilteredProducts([]);
      setFuzzyHint(null);
      return;
    }

    const allowedDist = (t) => (t.length >= 8 ? 2 : 1);
    const candidates = productIndex.slice(0, 200);

    const fuzzy = [];
    for (const idx of candidates) {
      const { p, name, haystack, nameWords, words } = idx;

      // Every exact-required token still needs a literal hit (with the same
      // short-numeric prefix rule used in the strict pass).
      if (!exactRequired.every(t => tokenMatches(t, haystack, nameWords))) continue;

      let totalEdits = 0;
      let matchedAll = true;
      for (const t of fuzzable) {
        if (haystack.includes(t)) continue;
        let best = Infinity;
        const limit = allowedDist(t);
        for (const w of words) {
          if (Math.abs(w.length - t.length) > limit) continue;
          const d = levenshteinAtMost(t, w, limit);
          if (d < best) best = d;
          if (best === 0) break;
        }
        if (best > limit) { matchedAll = false; break; }
        totalEdits += best;
      }
      if (!matchedAll) continue;
      fuzzy.push({ p, edits: totalEdits, len: name.length });
    }

    if (fuzzy.length > 0) {
      fuzzy.sort((a, b) => a.edits - b.edits || a.len - b.len);
      setFilteredProducts(fuzzy.slice(0, 50).map(m => m.p));
      setFuzzyHint({ query: search.trim(), count: fuzzy.length });
    } else {
      setFilteredProducts([]);
      setFuzzyHint(null);
    }
  }, [debouncedSearch, allProducts, productIndex]);

  const filteredCusts = custSearch 
    ? customers.filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase())).slice(0, 8) 
    : [];

  const getPriceForCustomer = (product) => {
    // Repacks always price at retail tier — wholesale never applies (bulk
    // purchases use the parent product, not the repack).
    const scheme = product.is_repack ? 'retail' : activeScheme;
    return product.prices?.[scheme] ?? 0;
  };

  // Detect if a repack line has no branch retail set (red "No Retail" badge)
  const repackBranchHasRetail = (product) => {
    if (!product?.is_repack) return true;
    const branchSet = product.branch_set_scheme_keys || [];
    if (branchSet.includes('retail')) return true;
    return false;
  };

  // Quick mode: Add to cart
  const addToCart = (product) => {
    if (product?.disabled_at_branch) {
      toast.error(`${product.name} is disabled at this branch and cannot be sold.`);
      return;
    }
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
        // JIT retail: red flag when branch has no retail price set
        needs_jit_retail: product.is_repack && !repackBranchHasRetail(product) && (!price || price <= 0),
        global_only_retail: product.is_repack && !repackBranchHasRetail(product) && price > 0,
      }];
    });
    // Auto-scroll cart to the newest item so cashiers don't have to scroll
    // manually when adding the 6th/7th/8th product. Two RAFs ensure layout
    // settles after the new row mounts.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        cartEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
      });
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
    // Price editing now works offline too: the PriceMatchModal verifies the
    // manager/admin PIN locally via verifyOfflinePin (cached bcrypt hash +
    // branch-scoped grants), and the queued sale carries `price_changes` +
    // `price_match_pin` so the backend can re-validate and upsert
    // branch_prices when the connection is back. If no offline credentials
    // were ever cached, the modal will surface that and block submission.
    const str = String(newPrice);
    // Allow intermediate decimal input: ".", "0.", ".5", ""
    if (str === '' || str === '.' || str.endsWith('.') || (str.match(/^\.\d*$/) && isNaN(Number(str)))) {
      setCart(cart.map(c => c.product_id !== productId ? c : { ...c, _priceStr: str }));
      return;
    }
    const price = parseFloat(str) || 0;
    setCart(cart.map(c => c.product_id !== productId ? c : { ...c, price, total: price * c.quantity, _priceStr: undefined }));
  };

  const removeFromCart = (productId) => setCart(cart.filter(c => c.product_id !== productId));
  const clearCart = () => {
    setCart([]); setLines([{ ...EMPTY_LINE }]); setSelectedCustomer(null); setCustSearch('');
    setActiveScheme(defaultScheme);
    setCustEdits({ phone: '', address: '', shipping_address: '' });
    setCustEdited(false);
    setHeader(h => ({ ...h, shipping_address: '', location: '', mod: '', check_number: '', req_ship_date: '', notes: '', customer_po: '' }));
    // Reset Price Match approval — next sale must re-authorize if it has price edits
    setPriceMatchApproved(null);
    setPriceMatchError('');
    // Reset PIN session (per-sale scope)
    clearPinSession();
  };

  // ── Draft / For Preparation orders ─────────────────────────────────────────
  const refreshDraftOrders = useCallback(async () => {
    if (!currentBranch?.id || !isOnline) return;
    try {
      const res = await api.get(`/draft-orders?branch_id=${currentBranch.id}`);
      setDraftOrders(res.data.drafts || []);
    } catch (err) {
      console.error('Failed to load draft orders', err);
    }
  }, [currentBranch?.id, isOnline]);

  // Iter 252: load IndexedDB overlay so drafts that were finalized offline
  // show "Offline Completion Pending" until sync runs. Refresh whenever the
  // panel opens or pending count changes (sync just removed an entry).
  const refreshPendingDraftOverlay = useCallback(async () => {
    try {
      const map = await getPendingDraftCompletions();
      setPendingDraftCompletions(map);
    } catch (err) {
      console.error('Failed to load pending draft completions', err);
    }
  }, []);

  useEffect(() => { refreshDraftOrders(); }, [refreshDraftOrders]);
  useEffect(() => { refreshPendingDraftOverlay(); }, [refreshPendingDraftOverlay, pendingCount, draftOrdersOpen]);

  const handlePrepareOrder = async () => {
    const hasItems = mode === 'quick' ? cart.length > 0 : lines.some(l => l.product_id);
    if (!hasItems) { toast.error('Cart is empty — add items first'); return; }
    if (!currentBranch?.id) { toast.error('No active branch'); return; }
    if (!isOnline) { toast.error('Prepare Order requires an internet connection'); return; }

    setPreparingOrder(true);
    try {
      const draftItems = mode === 'quick'
        ? cart.map(c => ({
            product_id: c.product_id, product_name: c.product_name, sku: c.sku || '',
            quantity: c.quantity, rate: c.price, unit_price: c.price, price: c.price,
            total: c.total, discount_amount: 0, is_repack: c.is_repack || false,
          }))
        : lines.filter(l => l.product_id).map(l => ({
            product_id: l.product_id, product_name: l.product_name,
            description: l.description || '', sku: l.sku || '',
            quantity: parseFloat(l.quantity) || 0, rate: parseFloat(l.rate) || 0,
            unit_price: parseFloat(l.rate) || 0, price: parseFloat(l.rate) || 0,
            total: lineTotal ? lineTotal(l) : ((parseFloat(l.quantity) || 0) * (parseFloat(l.rate) || 0)),
            discount_amount: l.discount_type === 'percent'
              ? (parseFloat(l.quantity) || 0) * (parseFloat(l.rate) || 0) * (parseFloat(l.discount_value) || 0) / 100
              : (parseFloat(l.discount_value) || 0) * (parseFloat(l.quantity) || 0),
            is_repack: l.is_repack || false,
          }));

      const payload = {
        branch_id: currentBranch.id,
        items: draftItems,
        customer_id: selectedCustomer?.id || null,
        customer_name: selectedCustomer?.name || 'Walk-in',
        customer_phone: selectedCustomer?.phone || '',
        customer_address: selectedCustomer?.address || '',
        subtotal,
        freight: parseFloat(freight) || 0,
        overall_discount: parseFloat(overallDiscount) || 0,
        grand_total: grandTotal,
        sale_mode: mode,
        active_scheme: activeScheme,
        notes: header?.notes || '',
        prefix: header?.prefix || undefined,
        order_date: header?.order_date || undefined,
      };

      let draft;
      if (activeDraftId) {
        const res = await api.patch(`/draft-orders/${activeDraftId}`, payload);
        draft = res.data;
        toast.success(`Draft ${draft.invoice_number} updated`);
      } else {
        const res = await api.post('/draft-orders', payload);
        draft = res.data;
        setActiveDraftId(draft.id);
        toast.success(`Order saved — Ref: ${draft.invoice_number}`);
      }

      refreshDraftOrders();

      // Show the same print prompt as a regular sale with the reserved invoice number
      setRefPrompt({
        open: true,
        number: draft.invoice_number,
        title: draft.customer_name || 'Walk-in',
        invoiceData: {
          ...draft,
          cashier_name: user?.full_name || user?.username || '',
        },
      });
    } catch (e) {
      toast.error('Failed to save draft: ' + (e?.response?.data?.detail || e?.message || ''));
    } finally {
      setPreparingOrder(false);
    }
  };

  const loadDraftIntoCart = (draft) => {
    // Restore mode first
    if (draft.sale_mode === 'order') {
      setMode('order');
      setLines(draft.items.map((item, idx) => ({
        id: idx + 1,
        product_id: item.product_id,
        product_name: item.product_name,
        description: item.description || '',
        sku: item.sku || '',
        quantity: item.quantity,
        rate: item.rate || item.unit_price || 0,
        discount_type: 'amount',
        discount_value: item.discount_amount || 0,
        total: item.total,
        is_repack: item.is_repack || false,
        needs_jit_retail: false,
      })));
      setCart([]);
    } else {
      setMode('quick');
      setCart(draft.items.map(item => ({
        product_id: item.product_id,
        product_name: item.product_name,
        sku: item.sku || '',
        price: item.rate || item.unit_price || 0,
        quantity: item.quantity,
        total: item.total,
        is_repack: item.is_repack || false,
        cost_price: 0,
        original_price: item.rate || item.unit_price || 0,
      })));
      setLines([]);
    }
    // Restore customer
    if (draft.customer_id) {
      setSelectedCustomer({
        id: draft.customer_id, name: draft.customer_name,
        phone: draft.customer_phone, address: draft.customer_address,
      });
    } else {
      setSelectedCustomer(null);
    }
    setFreight(draft.freight || 0);
    setOverallDiscount(draft.overall_discount || 0);
    setActiveDraftId(draft.id);
    setDraftOrdersOpen(false);
    toast.success(`Draft ${draft.invoice_number} loaded — edit and pay when ready`);
  };

  const cancelDraftOrder = async (draftId) => {
    try {
      await api.delete(`/draft-orders/${draftId}`);
      toast.success('Draft cancelled');
      refreshDraftOrders();
      if (activeDraftId === draftId) {
        setActiveDraftId(null);
        setCart([]); setLines([]); setSelectedCustomer(null);
      }
    } catch (e) {
      toast.error('Failed to cancel draft');
    }
  };

  // ── Parked / Draft sales ────────────────────────────────────────────────
  // Save the current sale as a parked draft so the cashier can immediately
  // serve another customer. Works online (server-shared) and offline (queued
  // in IndexedDB and synced on reconnect). Branch-shared: any cashier on the
  // same branch can resume.
  const refreshParkedSales = useCallback(async () => {
    if (!currentBranch?.id) return;
    try {
      const list = await loadParkedSales(currentBranch.id);
      setParkedSales(list);
    } catch (err) {
      console.error('Failed to load parked sales', err);
    }
  }, [currentBranch?.id]);

  // Initial load + reload whenever branch changes
  useEffect(() => { refreshParkedSales(); }, [refreshParkedSales]);

  // Drain offline mutation queue + reload list whenever we come online
  useEffect(() => {
    if (isOnline) {
      drainParkQueue().finally(() => refreshParkedSales());
    }
  }, [isOnline, refreshParkedSales]);

  const computeParkSummary = () => {
    const itemCount = mode === 'quick'
      ? cart.reduce((s, c) => s + (parseFloat(c.quantity) || 0), 0)
      : lines.filter(l => l.product_id).reduce((s, l) => s + (parseFloat(l.quantity) || 0), 0);
    const sub = mode === 'quick'
      ? cart.reduce((s, c) => s + (c.total || 0), 0)
      : lines.filter(l => l.product_id).reduce((s, l) => s + (l.quantity * l.rate || 0), 0);
    return { itemCount, subtotal: sub };
  };

  const handleParkConfirm = async () => {
    const filledQuick = cart.length > 0;
    const filledOrder = lines.some(l => l.product_id);
    if (!filledQuick && !filledOrder) {
      toast.error('Cart is empty — nothing to park.');
      return;
    }
    if (!currentBranch?.id) {
      toast.error('No active branch — cannot park.');
      return;
    }
    const { itemCount, subtotal } = computeParkSummary();
    const auto = selectedCustomer?.name
      ? `${selectedCustomer.name} · ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
      : `Walk-in · ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    const label = parkLabel.trim() ? `${auto} — ${parkLabel.trim()}` : auto;
    const payload = buildParkPayload({
      branchId: currentBranch.id,
      mode,
      label,
      customer: selectedCustomer ? {
        id: selectedCustomer.id, name: selectedCustomer.name,
        phone: selectedCustomer.phone, price_scheme: selectedCustomer.price_scheme,
        address: selectedCustomer.address, shipping_address: selectedCustomer.shipping_address,
      } : null,
      activeScheme,
      cart, lines, header,
      itemCount, subtotal,
    });
    try {
      await parkSale(payload);
      toast.success(isOnline ? 'Sale parked' : 'Sale parked — will sync when online');
      setParkLabel('');
      setParkPromptOpen(false);
      clearCart();
      refreshParkedSales();
    } catch (err) {
      const msg = err?.response?.data?.detail || 'Failed to park sale';
      toast.error(msg);
    }
  };

  const resumeParkedSale = async (park) => {
    // Consume = atomic fetch + delete server-side. The row immediately
    // disappears from the branch list so no one else can resume it AND
    // so this same cashier doesn't see it lingering and wonder if the
    // customer came back. If the consume fails (race / network),
    // we still rehydrate from the cached snapshot.
    let snapshot = park;
    try {
      const fresh = await consumeParkedSale(park.id);
      if (fresh) snapshot = fresh;
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || '';
      if (err?.response?.status === 410) {
        toast.error(msg || 'Already resumed by another device');
        refreshParkedSales();
        setParkedDialogOpen(false);
        return;
      }
      // Other errors: keep going with the local snapshot — UX shouldn't
      // block resuming when offline / brief server hiccup.
    }
    // Push the saved snapshot back into live state. We rehydrate based on
    // the park's stored mode so Quick/Order distinction round-trips.
    if (snapshot.mode === 'quick') {
      setMode('quick');
      setCart(snapshot.cart || []);
      setLines([{ ...EMPTY_LINE }]);
    } else {
      setMode('order');
      const filled = (snapshot.lines || []).filter(l => l.product_id);
      setLines([...filled, { ...EMPTY_LINE }]);
      setCart([]);
    }
    if (snapshot.customer) {
      setSelectedCustomer(snapshot.customer);
      setCustSearch(snapshot.customer.name || '');
    } else {
      setSelectedCustomer(null);
      setCustSearch('');
    }
    if (snapshot.active_scheme) setActiveScheme(snapshot.active_scheme);
    if (snapshot.header) setHeader(h => ({ ...h, ...snapshot.header }));
    setParkedDialogOpen(false);
    refreshParkedSales();
    toast.success(`Resumed: ${snapshot.label || 'Parked sale'}`);
  };

  const handleDiscardClick = async (park) => {
    const isOwn = park.created_by === user?.id;
    if (!isOwn) {
      // Auto-bypass if PIN session is warm
      if (isPinSessionWarm()) {
        try {
          await discardParkedSale(park.id, { pin: pinSessionRef.current.pin });
          toast.success('Parked sale discarded (PIN active)');
          refreshParkedSales();
        } catch {
          clearPinSession();
          setDiscardPinPrompt({ open: true, parkId: park.id, pin: '' });
        }
        return;
      }
      // Other-user park — open PIN prompt
      setDiscardPinPrompt({ open: true, parkId: park.id, pin: '' });
      return;
    }
    try {
      await discardParkedSale(park.id);
      toast.success('Parked sale discarded');
      refreshParkedSales();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to discard');
    }
  };

  const submitDiscardWithPin = async () => {
    const { parkId, pin } = discardPinPrompt;
    if (!pin || pin.length < 4) {
      toast.error('PIN required');
      return;
    }
    try {
      await discardParkedSale(parkId, { pin });
      toast.success('Parked sale discarded');
      startPinSession(pin, 'manager_pin', '');
      setDiscardPinPrompt({ open: false, parkId: null, pin: '' });
      refreshParkedSales();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Invalid PIN');
    }
  };

  // ── Unsaved-changes guard ───────────────────────────────────────────
  // Triggers the leave-confirmation dialog whenever the user navigates
  // away (sidebar click, route change, browser tab close) OR uses the
  // exposed `requestSafe()` to wrap in-page destructive switches like
  // mainTab quote↔history. The "Park & leave" branch reuses the same
  // park flow as the manual button, so a misclick never costs work.
  const isSalesDirty = cart.length > 0 || lines.some(l => l.product_id);
  const autoParkForGuard = useCallback(async () => {
    if (!currentBranch?.id) {
      throw new Error('No active branch');
    }
    const { itemCount, subtotal } = computeParkSummary();
    const auto = selectedCustomer?.name
      ? `${selectedCustomer.name} · ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
      : `Walk-in · ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    const payload = buildParkPayload({
      branchId: currentBranch.id,
      mode,
      label: `${auto} — auto-saved on leave`,
      customer: selectedCustomer ? {
        id: selectedCustomer.id, name: selectedCustomer.name,
        phone: selectedCustomer.phone, price_scheme: selectedCustomer.price_scheme,
        address: selectedCustomer.address, shipping_address: selectedCustomer.shipping_address,
      } : null,
      activeScheme,
      cart, lines, header,
      itemCount, subtotal,
    });
    await parkSale(payload);
    toast.success(isOnline ? 'Sale parked — recoverable from Parked' : 'Sale parked — will sync when online');
    clearCart();
    refreshParkedSales();
  }, [currentBranch?.id, mode, selectedCustomer, activeScheme, cart, lines, header, isOnline, refreshParkedSales]); // eslint-disable-line

  const salesGuard = useUnsavedChangesGuard({
    isDirty: isSalesDirty,
    onPark: autoParkForGuard,
    label: 'Sales',
  });


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

  // Compute price-changed lines from cart (Quick) and lines (Order).
  // Returns: [{ product_id, product_name, old_price, new_price, scheme }]
  const computePriceChanges = useCallback(() => {
    const changes = [];
    // A "price change" is triggered when:
    //   • the user entered a valid new price (np > 0), AND
    //   • EITHER the original price was 0/unset (first-time pricing of a
    //     product whose retail was blank) OR the new price differs from the
    //     original by more than 1 centavo.
    // Previous logic required `op > 0` too, which skipped the prompt when
    // the retail was 0 → user set a price → never asked to save. (Iter 217b)
    if (mode === 'quick') {
      cart.forEach(c => {
        const op = parseFloat(c.original_price) || 0;
        const np = parseFloat(c.price) || 0;
        if (np <= 0 || c.is_repack) return;
        const isNewPricing = op === 0;
        const isChanged = op > 0 && Math.abs(np - op) > 0.001;
        if (isNewPricing || isChanged) {
          changes.push({
            product_id: c.product_id,
            product_name: c.product_name,
            old_price: op,
            new_price: np,
            scheme: activeScheme,
            reason_hint: isNewPricing ? 'first_time_pricing' : 'manual_change',
          });
        }
      });
    } else {
      lines.forEach(l => {
        if (!l.product_id) return;
        const op = parseFloat(l.original_rate) || 0;
        const np = parseFloat(l.rate) || 0;
        if (np <= 0 || l.is_repack) return;
        const isNewPricing = op === 0;
        const isChanged = op > 0 && Math.abs(np - op) > 0.001;
        if (isNewPricing || isChanged) {
          changes.push({
            product_id: l.product_id,
            product_name: l.product_name,
            old_price: op,
            new_price: np,
            scheme: activeScheme,
            reason_hint: isNewPricing ? 'first_time_pricing' : 'manual_change',
          });
        }
      });
    }
    return changes;
  }, [mode, cart, lines, activeScheme]);

  // Legacy: kept as no-op so existing references compile. The new flow uses
  // PriceMatchModal at checkout time instead of an ad-hoc save dialog.
  const triggerPriceSaveDialog = () => {};
  const dismissPriceSaveDialog = () => { setPriceSaveDialog(false); setPendingPriceChange(null); };
  const savePriceToScheme = async () => { dismissPriceSaveDialog(); };

  const handleRateBlur = () => {
    /* no-op — Price Match flow runs at checkout (see openCheckout). */
  };

  // Order mode: Handle lines
  const handleProductSelect = (index, product) => {
    const newLines = [...lines];
    // Repacks: always retail tier
    const scheme = product.is_repack ? 'retail' : activeScheme;
    const rate = product.prices?.[scheme] ?? 0;
    const branchSet = product.branch_set_scheme_keys || [];
    const hasBranchRetail = !product.is_repack || branchSet.includes('retail');
    newLines[index] = {
      ...newLines[index], product_id: product.id, product_name: product.name,
      description: product.description || '', rate, original_rate: rate,
      cost_price: product.cost_price || 0,
      moving_average_cost: product.moving_average_cost || 0,
      last_purchase_cost: product.last_purchase_cost || 0,
      effective_capital: product.effective_capital || product.cost_price || 0,
      capital_method: product.capital_method || 'manual',
      is_repack: product.is_repack || false,
      needs_jit_retail: product.is_repack && !hasBranchRetail && (!rate || rate <= 0),
      global_only_retail: product.is_repack && !hasBranchRetail && rate > 0,
    };
    if (index === lines.length - 1) newLines.push({ ...EMPTY_LINE });
    setLines(newLines);
    setTimeout(() => qtyRefs.current[index]?.focus(), 50);
    // Auto-scroll the order-lines table to the newest filled row so the
    // last addition is always visible (price/discount easy to verify).
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        orderLinesEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
      });
    });
  };

  const updateLine = (index, field, value) => {
    // Price edits work offline: the PriceMatchModal verifies the
    // manager/admin PIN locally and the queued sale carries the change.
    const numericFields = ['quantity', 'rate', 'discount_value'];
    const newLines = [...lines];
    if (numericFields.includes(field)) {
      const str = String(value);
      // Allow intermediate decimal input: ".", "0.", ".5", "" — commit only when parseable
      if (str === '' || str === '.' || str.endsWith('.') || (str.match(/^\.\d*$/) && isNaN(Number(str)))) {
        newLines[index] = { ...newLines[index], [`_${field}Str`]: str };
        setLines(newLines);
        return;
      }
      const numVal = parseFloat(str) || 0;
      newLines[index] = { ...newLines[index], [field]: numVal, [`_${field}Str`]: undefined };
    } else {
      newLines[index] = { ...newLines[index], [field]: value };
    }
    setLines(newLines);
  };

  // Finalize a numeric field on blur — clears intermediate string state
  const finalizeLineField = (index, field) => {
    const strKey = `_${field}Str`;
    if (lines[index]?.[strKey] !== undefined) {
      const numVal = parseFloat(lines[index][strKey]) || 0;
      const newLines = [...lines];
      newLines[index] = { ...newLines[index], [field]: numVal, [strKey]: undefined };
      setLines(newLines);
    }
  };

  const removeLine = (index) => {
    if (lines.length <= 1) return;
    setLines(lines.filter((_, i) => i !== index));
  };

  const lineTotal = (line) => {
    const base = line.quantity * line.rate;
    // Per-unit amount discounts: ₱X off each unit × qty (not flat per-line).
    // Percent discounts stay as a % of the full line — same math either way.
    const disc = line.discount_type === 'percent'
      ? base * line.discount_value / 100
      : line.discount_value * line.quantity;
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
    if (!currentBranch?.id) {
      // BUSINESS RULE PRESERVED: a sale must belong to a specific branch.
      // We only make the next-step crystal clear and add a testid so E2E
      // tooling can reliably assert the block.
      toast.error(
        'Select a specific branch to checkout. Open the branch picker in the top header and pick one — "All Branches" is for browsing only.',
        { id: 'checkout-blocked-no-branch', duration: 6000 },
      );
      return;
    }

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

    // ── Price Match: if any line has an edited price, capture reason+PIN
    // BEFORE checkout. We hold the result in priceMatchApproved and feed it
    // into processSale as `price_changes` + `price_match_pin`.
    const pendingPriceChanges = computePriceChanges();
    if (pendingPriceChanges.length > 0 && !priceMatchApproved) {
      setPriceMatchError('');
      setPriceMatchModal(true);
      return;
    }

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

  // ── Phase 4A — Historical Credit / Notebook AR (hook wiring) ─────
  // State, trigger flags, payload builders, preview, and commit all
  // live in `lib/useHistoricalCredit.js`. We pass in:
  //   • `daysBack`, `paymentType`, `isPrivilegedRole` — for the mode flag
  //   • `getItems` — page-owned cart→items mapper (mode-aware)
  //   • `getContext` — page-owned customer/branch/totals/date
  //   • `hasCustomer` — gate for runPreview
  //   • `onCommitted` — REQUIRED page-side cleanup after success
  // The destructured names (`isHistoricalCreditMode`,
  // `isBackdatedNonCreditBlocked`) preserve the existing JSX and
  // `handleCreditSale` call-sites so no broader refactor is needed.
  const hc = useHistoricalCredit({
    daysBack,
    paymentType,
    isPrivilegedRole,
    getItems: useCallback(() => {
      if (mode === 'quick') {
        return cart.map(c => ({
          product_id: c.product_id,
          product_name: c.product_name,
          sku: c.sku,
          quantity: c.quantity,
          rate: c.price,
          price: c.price,
          total: c.total,
        }));
      }
      return lines.filter(l => l.product_id).map(l => ({
        product_id: l.product_id,
        product_name: l.product_name,
        quantity: l.quantity,
        rate: l.rate,
        price: l.rate,
        total: lineTotal(l),
      }));
    }, [mode, cart, lines]),
    getContext: useCallback(() => ({
      customer_id: selectedCustomer?.id || '',
      branch_id: currentBranch?.id || '',
      transaction_date: header.order_date,
      subtotal,
      freight,
      overall_discount: overallDiscount,
      grand_total: grandTotal,
    }), [selectedCustomer?.id, currentBranch?.id, header.order_date, subtotal, freight, overallDiscount, grandTotal]),
    hasCustomer: useCallback(() => !!selectedCustomer?.id, [selectedCustomer?.id]),
    onCommitted: useCallback(() => {
      // Page-side post-commit cleanup. Behavior-identical to the inline
      // commit flow before extraction: clear the cart, reset the order
      // date back to today, clear any pending credit-sale latch, and
      // close the checkout dialog if it was still open.
      clearCart();
      setHeader(h => ({ ...h, order_date: localTodayStr() }));
      setPendingCreditSale(null);
      setCheckoutDialog(false);
    }, []),
  });
  const {
    isMode: isHistoricalCreditMode,
    isBackdatedNonCreditBlocked,
  } = hc;

  // Phase 4 Cleanup: pre-compute the Checkout "Confirm Payment" disabled
  // rule here in the page so the extracted CheckoutDialog stays purely
  // presentational. Behavior is byte-for-byte identical to the previous
  // inline `disabled={...}` block on the confirm-payment button.
  const confirmDisabled = (
    saving ||
    (paymentType === 'cash' && amountTendered < grandTotal) ||
    (paymentType === 'digital' && !digitalRefNumber.trim()) ||
    (paymentType === 'split' && (
      !digitalRefNumber.trim() ||
      Math.abs((parseFloat(splitCash||0) + parseFloat(splitDigital||0)) - grandTotal) > 0.01
    )) ||
    ((paymentType === 'partial' || paymentType === 'credit') && !selectedCustomer) ||
    isBackdatedNonCreditBlocked ||
    (isHistoricalCreditMode && hc.reason.trim().length < 20)
  );

  // Handle credit sale with approval
  const handleCreditSale = async () => {
    // Phase 4A — block backdated non-credit sales >7 days back. They
    // would alter today's cash collected and/or post-event Z-reports,
    // which is not permitted for old notebook reconstruction.
    if (isBackdatedNonCreditBlocked) {
      toast.error(
        'Backdated transactions older than 7 days are allowed for CREDIT only. '
        + 'Cash, digital, split, or partially paid transactions must be recorded on the actual payment date.',
        { duration: 6000 },
      );
      return;
    }

    // Phase 4A — Historical Credit / Notebook AR mode: open the dedicated
    // confirmation dialog instead of routing through the regular credit-
    // sale → manager-PIN path. Final commit posts to /api/historical-credit
    // and is gated by Owner/Admin TOTP server-side.
    if (isHistoricalCreditMode && selectedCustomer?.id) {
      setPendingCreditSale({ paymentType, partialPayment: 0, amountTendered: 0 });
      setCheckoutDialog(false);
      hc.openDialog();
      return;
    }

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
    // Auto-bypass if PIN session is warm (online only — offline needs reason)
    if (isPinSessionWarm() && isOnline) {
      verifyManagerPin(pinSessionRef.current.pin);
    } else {
      setCreditApprovalDialog(true);
    }
  };

  // Handle crop type confirmed — proceed to PIN approval
  const handleCropTypeConfirmed = (config) => {
    setCropCreditConfig(config);
    setCropTypeDialog(false);
    // Auto-bypass if PIN session is warm (online only — offline needs reason)
    if (isPinSessionWarm() && isOnline) {
      verifyManagerPin(pinSessionRef.current.pin);
    } else {
      setCreditApprovalDialog(true);
    }
  };

  // Verify manager PIN — accepts optional override for PIN session auto-bypass
  const verifyManagerPin = async (_sessionPin) => {
    // Defensive: when bound directly to onClick, React may pass a SyntheticEvent
    // here. Only treat string/number as a real session-PIN; ignore anything else.
    const sessionPinStr = (typeof _sessionPin === 'string' || typeof _sessionPin === 'number')
      ? String(_sessionPin) : '';
    const pinToUse = sessionPinStr || managerPin;
    if (!pinToUse) { toast.error('Enter authorization code'); return; }
    // Offline credit sale: a written reason is required for the audit log
    if (!isOnline && (paymentType === 'credit' || paymentType === 'partial') && selectedCustomer?.id) {
      if (!offlineBypassReason.trim() || offlineBypassReason.trim().length < 4) {
        toast.error('Reason is required for offline credit sales (e.g. "Customer in a hurry, signed paper slip")');
        return;
      }
    }
    
    try {
      const customerName = selectedCustomer?.name || 'Walk-in';
      const res = await api.post('/auth/verify-manager-pin', {
        pin: pinToUse.trim(),
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
        toast.success(`Approved by ${res.data.manager_name}${sessionPinStr ? ' (PIN active)' : ''}`);
        setCreditApprovalDialog(false);
        setManagerPin('');
        // Start/refresh PIN session
        startPinSession(pinToUse.trim(), res.data.method || 'manager_pin', res.data.manager_name);

        const balanceForCheck = grandTotal - (pendingCreditSale?.partialPayment || 0);
        const isCreditOrPartial = paymentType === 'credit' || paymentType === 'partial';

        // ── OFFLINE PATH ─────────────────────────────────────────────────
        // Iter 192: Skip signature (server unreachable) → attach offline_bypass
        // metadata to the sale. /sales/sync retroactively creates a
        // signature_session(status=bypassed) on replay for full audit.
        if (!isOnline && isCreditOrPartial && balanceForCheck > 0 && selectedCustomer?.id) {
          const offlineBypass = {
            method: res.data.method || 'admin_pin',
            by_name: res.data.manager_name || 'Manager',
            reason: offlineBypassReason.trim(),
            at: new Date().toISOString(),
            credit_type: cropCreditConfig?.type === 'charged_to_crop' ? 'charged_to_crop' : 'by_term',
            branch_name: currentBranch?.name || '',
          };
          setOfflineBypassReason('');
          await processSale(res.data.manager_name, null, null, offlineBypass);
          return;
        }

        // ── ONLINE PATH ──────────────────────────────────────────────────
        // Pre-invoice signature flow (credit / partial with customer attached)
        if (isCreditOrPartial && balanceForCheck > 0 && selectedCustomer?.id) {
          // Build a draft invoice for the signature dialog
          const draftItems = mode === 'quick'
            ? cart.map(c => ({
                product_name: c.product_name, quantity: c.quantity,
                rate: c.price, total: c.total, unit: c.unit || ''
              }))
            : lines.filter(l => l.product_id).map(l => ({
                product_name: l.product_name, quantity: l.quantity,
                rate: l.rate, total: lineTotal(l), unit: l.unit || ''
              }));
          // If we happen to be offline here (mid-checkout connectivity drop),
          // use an offline invoice number so the signed slip shows a real ID.
          let sigInvoiceNumber = '(pending)';
          if (!isOnline) {
            try {
              sigInvoiceNumber = await getNextOfflineReceiptNumber(
                header.prefix || 'SI',
                currentBranch?.branch_code || ''
              );
            } catch {}
          }
          setPreInvoiceSig({
            approvedBy: res.data.manager_name,
            invoice: {
              id: '',  // no invoice yet
              invoice_number: sigInvoiceNumber,
              customer_name: selectedCustomer.name,
              customer_id: selectedCustomer.id,
              branch_id: currentBranch?.id || '',
              branch_name: currentBranch?.name || '',
              items: draftItems,
              subtotal,
              discount: overallDiscount,
              partial_paid: pendingCreditSale?.partialPayment || 0,
              balance: balanceForCheck,
              payment_type: paymentType,
              order_date: header.order_date,
              credit_type: cropCreditConfig?.type === 'charged_to_crop' ? 'charged_to_crop' : 'by_term',
            },
          });
          return; // wait for sig before posting sale
        }

        await processSale(res.data.manager_name);
      } else {
        toast.error(res.data.detail || 'Invalid PIN / TOTP — check Settings > Security for accepted methods');
        if (sessionPinStr) clearPinSession(); // session PIN was stale
      }
    } catch (e) {
      if (sessionPinStr) {
        clearPinSession();
        setCreditApprovalDialog(true); // fallback: show the dialog
        toast.info('PIN session expired — please re-enter');
      } else {
        toast.error(e.response?.data?.detail || 'Verification failed — check your connection');
      }
    }
  };

  // Process the sale
  const processSale = async (approvedBy = null, jitOverridePin = null, signatureData = null, offlineBypass = null) => {
    setSaving(true);
    
    const actualPaymentType = pendingCreditSale?.paymentType || paymentType;
    const actualPartial = pendingCreditSale?.partialPayment || partialPayment;
    const actualTendered = pendingCreditSale?.amountTendered || amountTendered;
    
    const saleId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const envelopeId = newEnvelopeId(); // separate idempotency key for resilient sync
    const today = localTodayStr();

    // Offline receipt number — mirrors the online format (PREFIX-BRANCH-SEQ)
    // with an "OFF" marker so it can never collide with the server-side
    // sequence. Backend /sales/sync preserves this number on replay.
    let offlineInvoiceNumber = null;
    if (!isOnline) {
      try {
        offlineInvoiceNumber = await getNextOfflineReceiptNumber(
          header.prefix || 'SI',
          currentBranch?.branch_code || ''
        );
      } catch (err) {
        console.error('Offline receipt number generation failed:', err);
      }
    }
    
    // Calculate amounts
    const splitCashAmt = parseFloat(splitCash || 0);
    const splitDigitalAmt = parseFloat(splitDigital || 0);
    const amountPaid = actualPaymentType === 'cash' ? grandTotal
      : actualPaymentType === 'digital' ? grandTotal
      : actualPaymentType === 'split' ? (splitCashAmt + splitDigitalAmt)
      : actualPaymentType === 'partial' ? actualPartial
      : 0;
    const balance = grandTotal - amountPaid;

    // Collect JIT retail prices: any repack line that had its rate edited
    // because the branch had no retail price set. Persist these to
    // branch_prices after Owner PIN approval (collected after the sale's
    // 422 response if the user hasn't already entered the PIN).
    const collectJitRetail = (rows) => {
      const out = {};
      rows.forEach(r => {
        if (r.is_repack && r.needs_jit_retail) {
          const rate = parseFloat(r.rate ?? r.price ?? 0);
          if (rate > 0) out[r.product_id] = rate;
        }
      });
      return Object.entries(out).map(([product_id, retail]) => ({ product_id, retail }));
    };
    const jitRetailPrices = mode === 'quick'
      ? collectJitRetail(cart.map(c => ({ ...c, rate: c.price })))
      : collectJitRetail(lines.filter(l => l.product_id));

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
          discount_amount: l.discount_type === 'percent' ? l.quantity * l.rate * l.discount_value / 100 : l.discount_value * l.quantity,
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
      jit_retail_prices: jitRetailPrices,
      jit_owner_pin: jitOverridePin || undefined,
      // Price Match — permanent branch price changes triggered from the cart.
      // Backend persists to branch_prices and writes to price_change_log.
      price_changes: priceMatchApproved?.price_changes,
      price_match_pin: priceMatchApproved?.pin,
      price_scheme: activeScheme,
      branch_name: currentBranch?.name || '',
      // Pre-invoice signature (when captured via the new flow)
      signature: signatureData || undefined,
      signature_session_id: signatureData?.session_id || undefined,
      // Offline credit-sale bypass metadata (Iter 192) — replayed by /sales/sync
      // which retroactively creates a signature_session(status=bypassed)
      offline_bypass: offlineBypass || undefined,
      // Offline invoice number (present only when sale was built offline).
      // Backend /sales/sync preserves this via sale.get("invoice_number") so
      // the receipt handed to the customer never renumbers on sync.
      invoice_number: offlineInvoiceNumber || undefined,
      // Draft finalization: when paying a "for_preparation" draft, pass its ID
      // so the backend updates the existing invoice (preserving invoice number).
      draft_invoice_id: activeDraftId || undefined,
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
        setActiveDraftId(null);
        setCheckoutDialog(false);
        setPendingCreditSale(null);

        // ── Customer signature flow (credit / partial only) ──────────────
        // Skip if we already captured signature pre-invoice (signatureData
        // was passed). Only open this dialog as a fallback for legacy code
        // paths or if pre-invoice sig was not captured.
        if (balance > 0 && selectedCustomer?.id && !signatureData) {
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
          // Auto-bypass if PIN session is warm
          if (isPinSessionWarm()) {
            setSaving(false);
            try {
              await handleStockOverride(pinSessionRef.current.pin, saleData);
            } catch {
              clearPinSession();
              setStockOverrideModal(true);
            }
          } else {
            setStockOverrideModal(true);
            setSaving(false);
          }
          return;
        }
        // JIT retail PIN required — show Owner PIN modal then retry
        if (e?.response?.status === 422 && detail?.type === 'jit_retail_pin_required') {
          // Auto-bypass if PIN session is warm
          if (isPinSessionWarm()) {
            setSaving(false);
            const pinToUse = pinSessionRef.current.pin;
            setTimeout(() => processSale(null, pinToUse, signatureData), 30);
          } else {
            setJitPinModal({ open: true, items: detail.items || [], saleData, signatureData });
            setSaving(false);
          }
          return;
        }
        // Closed-day: offer Late-Encode flow for credit / partial sales
        if (e?.response?.status === 403 && typeof detail === 'string'
            && detail.includes('already closed')
            && ['credit', 'partial'].includes((saleData.payment_type || '').toLowerCase())) {
          setLateEncodeDialog({ open: true, saleData, signatureData });
          setSaving(false);
          return;
        }

        // ── Phase 4A.1: classify network failures vs real server errors ───
        // A response object means the server actually answered — surface
        // the real error to the cashier and KEEP the checkout open. Saving
        // offline here would silently mask validation/business-rule
        // failures (e.g., 400 missing field, 403 forbidden, 422 invalid
        // payload, 500 server bug). The cart stays so the cashier can fix
        // and retry.
        if (!isTrueNetworkError(e)) {
          let serverMsg = '';
          if (typeof detail === 'string') serverMsg = detail;
          else if (detail?.message) serverMsg = detail.message;
          else if (Array.isArray(detail?.errors)) serverMsg = detail.errors.join(' • ');
          else if (detail) serverMsg = JSON.stringify(detail).slice(0, 240);
          else serverMsg = e.message || `HTTP ${e?.response?.status || '???'}`;
          toast.error(`Sale rejected by server: ${serverMsg}`, { duration: 6500 });
          setSaving(false);
          return;
        }

        // ── Phase 4A.1: true network failure → 10s reconnect grace ─────
        // Same envelope_id is reused on every retry so the backend can
        // dedupe (Phase 2C payment idempotency). On success during the
        // grace, finalize as a normal online sale. On timeout, fall
        // through to the existing offline-save path below — also with
        // the same envelope_id so a later sync produces exactly one
        // invoice.
        setConnectivityStatus('reconnecting');
        const _retryStart = Date.now();
        const _retryWindowMs = 10000;
        const _retryInterval = 3000;
        let _resolvedRes = null;
        let _serverErrorDuringRetry = null;
        while (Date.now() - _retryStart < _retryWindowMs && !_resolvedRes && !_serverErrorDuringRetry) {
          const _elapsed = Date.now() - _retryStart;
          setReconnectCountdown(Math.max(0, Math.ceil((_retryWindowMs - _elapsed) / 1000)));
          await new Promise(r => setTimeout(r, _retryInterval));
          const healthy = await pingBackendHealth(2500);
          if (!healthy) continue;
          try {
            _resolvedRes = await api.post('/unified-sale', saleData);
          } catch (e2) {
            if (!isTrueNetworkError(e2)) { _serverErrorDuringRetry = e2; break; }
            // else: still flaky, loop and retry
          }
        }
        setReconnectCountdown(0);

        if (_serverErrorDuringRetry) {
          // Connection came back, but the server rejected the payload.
          // Treat exactly like the non-network branch above.
          setConnectivityStatus(navigator.onLine ? 'online' : 'offline');
          const d2 = _serverErrorDuringRetry?.response?.data?.detail;
          let msg = '';
          if (typeof d2 === 'string') msg = d2;
          else if (d2?.message) msg = d2.message;
          else if (d2) msg = JSON.stringify(d2).slice(0, 240);
          else msg = _serverErrorDuringRetry.message || `HTTP ${_serverErrorDuringRetry?.response?.status || '???'}`;
          toast.error(`Connection restored, but sale rejected: ${msg}`, { duration: 6500 });
          setSaving(false);
          return;
        }

        if (_resolvedRes) {
          // Online success during the grace window — same finalization
          // as the happy path below. Backend deduped via envelope_id.
          setConnectivityStatus('online');
          const invoiceNum = _resolvedRes.data.invoice_number || _resolvedRes.data.sale_number;
          invalidateBalanceCache();
          toast.success(`Connection restored — Sale ${invoiceNum} processed online.`, { duration: 5000 });
          clearCart();
          setActiveDraftId(null);
          setCheckoutDialog(false);
          setPendingCreditSale(null);
          setSaving(false);
          return;
        }

        // Still unreachable after 10s → auto-save offline (no extra prompt).
        // Fall through to the existing offline-save block below, which
        // already preserves saleData.envelope_id for the eventual sync.
        setConnectivityStatus('offline');

        // Save offline if API fails for other reasons.
        // ── Linked Offline Draft Finalization (Feb 2026) ──────────────────
        // If this is a draft finalization (activeDraftId set), preserve the
        // canonical draft number on the offline envelope and tag it with
        // kind="draft_finalization_offline" so /api/sales/sync routes it to
        // the dedicated handler that updates the existing draft instead of
        // creating a new invoice. The OFF number printed for the customer
        // becomes a `linked_offline_receipt_number` after sync; both numbers
        // resolve to the same official record.
        const isDraftFinalization = !!saleData.draft_invoice_id;
        if (isDraftFinalization) {
          saleData.kind = 'draft_finalization_offline';
          saleData.draft_invoice_number = (draftOrders.find(d => d.id === saleData.draft_invoice_id)?.invoice_number) || saleData.draft_invoice_number;
        }
        // If we didn't have an offline receipt number (we started online),
        // mint one now so the saved record carries a proper identifier.
        if (!saleData.invoice_number) {
          try {
            saleData.invoice_number = await getNextOfflineReceiptNumber(
              header.prefix || 'SI',
              currentBranch?.branch_code || ''
            );
          } catch {}
        }
        if (isDraftFinalization) {
          saleData.offline_receipt_number = saleData.invoice_number;
        }
        await addPendingSale(saleData);
        const count = await getPendingSaleCount();
        setPendingCount(count);
        if (isDraftFinalization) {
          toast.success(
            `Draft ${saleData.draft_invoice_number} finalized offline as ${saleData.invoice_number}. ` +
            `Will sync to the original invoice once online.`,
            { duration: 6000 }
          );
        } else {
          toast.success(saleData.invoice_number
            ? `${saleData.invoice_number} saved offline (will sync later)`
            : 'Sale saved offline (will sync later)');
        }
        clearCart();
        setActiveDraftId(null);
        setCheckoutDialog(false);
        setPendingCreditSale(null);
      }
    } else {
      // ── Offline-from-start branch (no internet at checkout) ────────────
      const isDraftFinalization = !!saleData.draft_invoice_id;
      if (isDraftFinalization) {
        saleData.kind = 'draft_finalization_offline';
        saleData.draft_invoice_number = (draftOrders.find(d => d.id === saleData.draft_invoice_id)?.invoice_number) || saleData.draft_invoice_number;
        saleData.offline_receipt_number = saleData.invoice_number;
      }
      await addPendingSale(saleData);
      const count = await getPendingSaleCount();
      setPendingCount(count);
      if (isDraftFinalization) {
        toast.success(
          `Draft ${saleData.draft_invoice_number} finalized offline as ${saleData.invoice_number}. ` +
          `Will sync to the original invoice once online.`,
          { duration: 6000 }
        );
      } else {
        toast.success(saleData.invoice_number
          ? `${saleData.invoice_number} saved offline!`
          : 'Sale saved offline!');
      }
      clearCart();
      setActiveDraftId(null);
      setCheckoutDialog(false);
      setPendingCreditSale(null);
    }
    
    setSaving(false);
  };

  const selectTerm = (label) => {
    const t = terms.find(x => x.label === label);
    setHeader(h => ({ ...h, terms: label, terms_days: t?.days || 0 }));
  };

  // Iter 243 polish: clear stale date errors when the underlying valid range
  // changes (e.g., a fresh /unclosed-days fetch updates lastCloseDate). Without
  // this, a user who triggered an error on the old min/max would keep seeing
  // it until they typed a new valid date.
  useEffect(() => {
    setDateError(null);
  }, [lastCloseDate, floorDate]);

  // Returns true if the given date is on/before the last closed day.
  // Used for the informational "Late-Encode" hint — NOT to block selection.
  const isDateClosed = useCallback((date) => {
    if (!lastCloseDate) return false;
    return date <= lastCloseDate;
  }, [lastCloseDate]);

  // Handle date selection from unclosed days banner OR the Sale Date field
  const handleEncodingDateChange = useCallback((date) => {
    // Iter 243: Guard against empty / partial date strings the browser emits
    // mid-type (e.g., "2026-0", "2026-04-") which lexically sort BEFORE
    // floorDate and used to spuriously fire "Cannot encode before system
    // start date". Ignore anything that isn't a full YYYY-MM-DD.
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return; // don't update, don't error — just wait for a complete value
    }
    if (floorDate && date < floorDate) {
      setDateError(`Cannot encode before system start date (${floorDate}).`);
      return;
    }
    // Iter 243: Forward-date cap — never allow dates more than 1 day past today.
    // Without this, a cashier could stamp `order_date = 2027-01-01`, deduct
    // stock from inventory today, and the sale would never show on any
    // Z-Report — "forward-dated stock laundering".
    if (date > maxAllowedDate) {
      setDateError(`Cannot encode beyond ${maxAllowedDate}. Future-dating is capped at the next open business day.`);
      return;
    }
    // Iter 243.1: Closed past dates are ALLOWED here so credit/partial sales
    // can flow into the Late-Encode dialog (auto-opens when the backend
    // returns "already closed" for the order). Cash sales on closed days
    // are still rejected by the server. We just show a hint, not an error,
    // so the cashier knows what to expect.
    setDateError(null);
    setHeader(h => ({ ...h, order_date: date }));
  }, [floorDate, maxAllowedDate]);

  // Handle manager override: retry the pending sale with override PIN
  const handleStockOverride = async (overridePin, _saleDataOverride) => {
    const data = _saleDataOverride || pendingSaleData;
    if (!data) return;
    const saleWithOverride = { ...data, manager_override_pin: overridePin };
    const res = await api.post('/unified-sale', saleWithOverride);
    const invoiceNum = res.data.invoice_number || res.data.sale_number;
    invalidateBalanceCache();
    startPinSession(overridePin, 'manager_pin', '');
    toast.success(`Sale ${invoiceNum} completed with manager override. Discrepancy ticket created.`, { duration: 5000 });
    setStockOverrideModal(false);
    setPendingSaleData(null);
    setInsufficientItems([]);
    clearCart();
    setPendingCreditSale(null);
    if ((data.payment_type === 'digital' || data.payment_type === 'split') && res.data.id) {
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
            // Iter 243: If today is already closed, auto-bump the sale date
            // to the next open day (tomorrow) so the cashier doesn't land on
            // a locked input. Only do this on first load — don't clobber a
            // date the user has explicitly picked.
            if (last_close_date) {
              const today = localTodayStr();
              if (last_close_date >= today) {
                setHeader(h => {
                  // Only auto-bump if order_date is still today's default
                  if (h.order_date !== today) return h;
                  const d = new Date(today + 'T12:00:00');
                  d.setDate(d.getDate() + 1);
                  return { ...h, order_date: d.toISOString().slice(0, 10) };
                });
              }
            }
          }}
          className="mx-1 mb-2"
        />
      )}

      {/* Header */}
      <div className="flex items-center justify-between px-1 py-3">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Sales</h1>

          {/* Main Tab: New Sale / History */}
          <div className="inline-flex items-center bg-slate-100/80 rounded-xl p-1 shadow-inner ring-1 ring-slate-200/40" data-testid="main-tab-toggle">
            <button
              onClick={() => salesGuard.requestSafe(() => setMainTab('sale'))}
              className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${mainTab === 'sale' ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'}`}
              data-testid="tab-new-sale"
            >
              <ShoppingCart size={14} /> New Sale
            </button>
            <button
              onClick={() => salesGuard.requestSafe(() => setMainTab('history'))}
              className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${mainTab === 'history' ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]' : 'text-slate-500 hover:text-slate-700'}`}
              data-testid="tab-history"
            >
              <FileText size={14} /> Sales History
            </button>
          </div>

          {/* Mode Toggle — segmented control with subtitle, only in new sale tab */}
          {mainTab === 'sale' && (
            <div className="inline-flex items-stretch bg-slate-100/80 rounded-xl p-1 shadow-inner ring-1 ring-slate-200/40" data-testid="mode-toggle">
              <button
                onClick={() => switchMode('quick')}
                className={`flex flex-col items-start justify-center gap-0 px-3.5 py-1.5 rounded-lg transition-all duration-200 ${
                  mode === 'quick'
                    ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
                data-testid="mode-quick"
              >
                <span className="flex items-center gap-1.5 text-sm font-semibold leading-tight">
                  <Zap size={14} /> Quick Sale
                </span>
                <span className={`text-[10px] leading-tight mt-0.5 ${mode === 'quick' ? 'text-slate-500' : 'text-slate-400'}`}>
                  Fast checkout
                </span>
              </button>
              <button
                onClick={() => switchMode('order')}
                className={`flex flex-col items-start justify-center gap-0 px-3.5 py-1.5 rounded-lg transition-all duration-200 ${
                  mode === 'order'
                    ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
                data-testid="mode-order"
              >
                <span className="flex items-center gap-1.5 text-sm font-semibold leading-tight">
                  <ClipboardList size={14} /> Detailed Sale
                </span>
                <span className={`text-[10px] leading-tight mt-0.5 ${mode === 'order' ? 'text-slate-500' : 'text-slate-400'}`}>
                  Per-line pricing
                </span>
              </button>
            </div>
          )}

          {/* Show / Hide Capital — PIN-gated for all roles */}
          {mainTab === 'sale' && (
            capitalShown ? (
              <button
                onClick={hideCapital}
                data-testid="hide-capital-btn"
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-amber-100/80 text-amber-800 ring-1 ring-amber-200 hover:bg-amber-200 transition-colors"
                title="Hide capital info on product cards"
              >
                <EyeOff size={13} /> Hide Capital
              </button>
            ) : (
              <button
                onClick={requestShowCapital}
                data-testid="show-capital-btn"
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-slate-100/80 text-slate-600 ring-1 ring-slate-200/60 hover:bg-slate-200 transition-colors"
                title="Reveal capital info — admin / manager PIN required"
              >
                <Eye size={13} /> Show Capital
                <Lock size={10} className="opacity-60" />
              </button>
            )
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* PIN Session Badge */}
          {pinSession && (
            <PinSessionBadge session={pinSession} onExpired={clearPinSession} />
          )}
          {/* Linked Scanner indicator */}
          {scannerSession && (
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium cursor-pointer ${
              scannerConnected ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'
            }`} onClick={() => setScannerQrOpen(true)} data-testid="scanner-status">
              <Smartphone size={12} />
              {scannerConnected ? 'Scanner Active' : 'Waiting...'}
            </div>
          )}

          {/* Phase 4A.1 — Connectivity indicator (3 states) ────────────
              'online'        → green Wifi  + 'Online'
              'reconnecting'  → amber pulse + 'Reconnecting…' + countdown
              'offline'       → amber WifiOff + 'Offline' */}
          <div
            data-testid="connectivity-status"
            data-status={connectivityStatus}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
              connectivityStatus === 'online'
                ? 'bg-emerald-50 text-emerald-700'
                : connectivityStatus === 'reconnecting'
                  ? 'bg-amber-50 text-amber-700 animate-pulse'
                  : 'bg-amber-50 text-amber-700'
            }`}
          >
            {connectivityStatus === 'online'
              ? <Wifi size={12} />
              : connectivityStatus === 'reconnecting'
                ? <RefreshCw size={12} className="animate-spin" />
                : <WifiOff size={12} />}
            {connectivityStatus === 'online' && 'Online'}
            {connectivityStatus === 'reconnecting' && (
              <span data-testid="reconnect-countdown">
                Reconnecting… {reconnectCountdown}s
              </span>
            )}
            {connectivityStatus === 'offline' && 'Offline'}
            {pendingCount > 0 && <Badge variant="secondary" className="ml-1 text-[10px] h-4">{pendingCount}</Badge>}
          </div>

          {/* Phase 4A.1.1 — Read-only "waiting to sync" reassurance pill.
              Visible ONLY when there are unsynced offline sales. Source:
              existing `pendingCount` state, fed by `getPendingSaleCount()`
              after every offline save and reset to 0 after `syncPendingSales()`
              completes. No click target — there is no dedicated pending-
              sync view yet. Disappears as soon as the queue drains. */}
          {pendingCount > 0 && (
            <div
              data-testid="pending-sync-pill"
              data-count={pendingCount}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-800 ring-1 ring-amber-200"
              title="These sales are saved on this device and will sync automatically once the server is reachable."
            >
              <Clock size={12} />
              {pendingCount} {pendingCount === 1 ? 'sale' : 'sales'} waiting to sync
            </div>
          )}

          {!scannerSession && (
            <Button variant="outline" size="sm" onClick={createScannerSession} disabled={scannerCreating} data-testid="link-scanner-btn">
              <Smartphone size={14} className="mr-1" /> {scannerCreating ? 'Creating...' : 'Link Scanner'}
            </Button>
          )}

          {/* Park current sale — "hold" so cashier can serve another customer */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setParkPromptOpen(true)}
            disabled={cart.length === 0 && !lines.some(l => l.product_id)}
            data-testid="park-sale-btn"
            title="Pause this sale and serve another customer"
          >
            <PauseCircle size={14} className="mr-1" /> Park
          </Button>

          {/* Parked sales list — resume any parked draft */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => { setParkedDialogOpen(true); refreshParkedSales(); }}
            data-testid="parked-sales-btn"
            title="View and resume parked sales"
          >
            <Inbox size={14} className="mr-1" />
            Parked
            {parkedSales.length > 0 && (
              <Badge variant="secondary" className="ml-1.5 text-[10px] h-4" data-testid="parked-count-badge">
                {parkedSales.length}
              </Badge>
            )}
          </Button>

          {/* Draft Orders — for_preparation invoices awaiting final payment */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => { setDraftOrdersOpen(true); refreshDraftOrders(); }}
            data-testid="draft-orders-btn"
            title="View and manage draft/preparation orders"
            className={draftOrders.length > 0 ? 'border-amber-400 text-amber-700 hover:bg-amber-50' : ''}
          >
            <ClipboardList size={14} className="mr-1" />
            Draft Orders
            {draftOrders.length > 0 && (
              <Badge className="ml-1.5 text-[10px] h-4 bg-amber-100 text-amber-700 border-amber-300" data-testid="draft-orders-count-badge">
                {draftOrders.length}
              </Badge>
            )}
          </Button>

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
                const time = formatTime(inv.created_at);
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
        <Card className="border-slate-200 border-l-4 border-l-blue-400">
          <CardContent className="p-3">
            <div className="flex flex-wrap items-end gap-4">
              <div className="relative flex-1 min-w-[200px]">
                <Label className="text-xs font-semibold text-blue-700 flex items-center gap-1.5">
                  <User size={12} />
                  Customer <span className="text-[10px] font-normal text-slate-400">(optional · for credit / receipt)</span>
                </Label>
                <div className="relative mt-1">
                  <User size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-blue-400 pointer-events-none" />
                  <Input
                    data-testid="customer-search"
                    className="h-9 pl-9 bg-blue-50/30 border-blue-200 focus:border-blue-400 focus:ring-blue-200 placeholder:text-blue-300"
                    value={custSearch}
                    placeholder="Customer name or phone… (leave blank for Walk-in)"
                    onChange={e => handleCustInput(e.target.value)}
                    onFocus={() => { if (custSearch) setCustDropdownOpen(true); }}
                    onBlur={() => setTimeout(() => setCustDropdownOpen(false), 200)}
                  />
                </div>
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

              {/* Sale Date — visible in BOTH Quick and Order modes so cashiers can
                  easily backdate forgotten credit sales to a past day (triggers
                  Late-Encode dialog automatically when the day is closed).   */}
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
                  max={maxAllowedDate}
                  onChange={e => handleEncodingDateChange(e.target.value)}
                  data-testid="sale-date-input"
                />
                {/* Closed-through label — always visible when there's a close history */}
                {lastCloseDate && !dateError && header.order_date > lastCloseDate && (
                  <p className="text-[9px] text-slate-400 mt-0.5 leading-tight">
                    Closed through {lastCloseDate}
                  </p>
                )}
                {/* Iter 243.1: Past-closed-day hint — when the cashier picks a
                    date on/before lastCloseDate, show an amber notice so they
                    know the sale will go through Late-Encode (credit/partial only,
                    triggers manager-PIN dialog on save). */}
                {!dateError && lastCloseDate && header.order_date <= lastCloseDate && (
                  <p className="text-[9px] text-amber-600 font-medium mt-0.5 leading-tight" data-testid="late-encode-hint">
                    Past closed day — credit/partial will require manager PIN (Late-Encode).
                  </p>
                )}
                {/* Inline error — shown instead of a toast so it can't be missed */}
                {dateError && (
                  <p className="text-[9px] text-red-600 font-medium mt-0.5 leading-tight">
                    {dateError}
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

      {/* Phase 4A — Historical Credit / Notebook AR Mode banner.
          Appears only when the cashier (admin/owner) has chosen a date
          >7 days back AND payment_type === credit AND a customer is set.
          Also shows a strict block warning for backdated cash/digital/
          split sales > 7 days, since those would taint today's cash.
          Extracted to `components/HistoricalCreditBanner.jsx` (Phase 4
          cleanup); behavior + every `data-testid` preserved verbatim. */}
      {mainTab === 'sale' && (
        <HistoricalCreditBanner
          enabled={isHistoricalCreditMode}
          blocked={isBackdatedNonCreditBlocked}
          daysBack={daysBack}
          hc={hc}
        />
      )}

      {/* Main Content */}
      <div className="flex-1 flex gap-4 px-1 overflow-hidden">
        {mode === 'quick' ? (
          // QUICK MODE: Product grid + Cart
          <>
            {/* Product Grid */}
            <div className="flex-1 flex flex-col min-w-0">
              <div className="mb-3">
                <Label className="text-xs font-semibold text-[#1A4D2E] flex items-center gap-1.5 mb-1">
                  <Package size={12} />
                  Product Search <span className="text-[10px] font-normal text-slate-400">(tap a tile or scan a barcode to add to cart)</span>
                </Label>
                <div className="relative">
                  <Package size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#1A4D2E] pointer-events-none" />
                  <Input
                    ref={searchRef}
                    data-testid="product-search"
                    className="pl-10 h-12 text-base font-medium border-2 border-[#1A4D2E]/30 focus:border-[#1A4D2E] focus:ring-[#1A4D2E]/20 bg-emerald-50/40 placeholder:text-slate-400 placeholder:font-normal"
                    placeholder="Search products by name, SKU, or barcode…"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                  />
                  <kbd className="absolute right-3 top-1/2 -translate-y-1/2 hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono text-slate-500 bg-white border border-slate-200 rounded">
                    <Search size={10} />
                    Products
                  </kbd>
                </div>
                {fuzzyHint && (
                  <div data-testid="fuzzy-hint-banner"
                    className="mt-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800">
                    <AlertTriangle size={14} className="text-amber-600 shrink-0" />
                    <span className="flex-1">
                      No exact match for <strong>"{fuzzyHint.query}"</strong> — showing
                      {' '}{fuzzyHint.count} closest match{fuzzyHint.count === 1 ? '' : 'es'}.
                      Did you mistype?
                    </span>
                    <button
                      type="button"
                      data-testid="fuzzy-hint-clear"
                      onClick={() => setSearch('')}
                      className="shrink-0 px-2 py-0.5 text-[11px] font-medium text-amber-800 hover:bg-amber-100 rounded transition-colors">
                      Clear
                    </button>
                  </div>
                )}
              </div>
              <ScrollArea className="flex-1">
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                  {(() => {
                    const visible = filteredProducts.slice(0, 50);
                    // Lazy bulk-fetch: when capital is unlocked, ensure cost
                    // for every visible product is in costMap.
                    if (capitalShown && capitalPin && visible.length) {
                      const missing = visible
                        .map(p => p.id)
                        .filter(id => id && !(id in costMap));
                      if (missing.length) {
                        // fire-and-forget; will rerender when state lands
                        fetchCostMap(missing, capitalPin);
                      }
                    }
                    return visible;
                  })().map(p => {
                    const avail = p.available ?? 0;
                    const isOut = avail <= 0;
                    const isLow = avail > 0 && avail <= (p.reorder_point || 5);
                    const isDisabled = !!p.disabled_at_branch;
                    const cost = capitalShown ? costMap[p.id] : null;
                    return (
                      <button
                        key={p.id}
                        data-testid={`product-${p.id}`}
                        disabled={isDisabled}
                        onClick={() => addToCart(p)}
                        className={`text-left p-3 rounded-lg border transition-all ${
                          isDisabled
                            ? 'border-slate-200 bg-slate-100/60 opacity-50 cursor-not-allowed'
                            : isOut
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
                          {isDisabled ? (
                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 flex items-center gap-1">
                              <EyeOff size={9} /> Disabled
                            </span>
                          ) : (
                            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                              isOut ? 'bg-red-100 text-red-600' :
                              isLow ? 'bg-amber-100 text-amber-700' :
                              'bg-emerald-50 text-emerald-700'
                            }`}>
                              {isOut ? 'Out' : `${avail.toFixed(0)} ${p.unit || ''}`}
                            </span>
                          )}
                        </div>
                        {capitalShown && cost && (
                          <div className="mt-1.5 pt-1.5 border-t border-slate-100 text-[10px] text-slate-500 leading-snug font-mono">
                            <div>
                              Cap: <span className="text-slate-700 font-semibold">{formatPHP(cost.effective_cost)}</span>
                            </div>
                            <div className="flex justify-between">
                              <span>LP: {cost.last_purchase ? formatPHP(cost.last_purchase) : '—'}</span>
                              <span>MA: {cost.moving_average ? formatPHP(cost.moving_average) : '—'}</span>
                            </div>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </ScrollArea>
            </div>

            {/* Cart */}
            <Card className="w-80 flex flex-col border-slate-200 min-h-0">
              <CardContent className="flex-1 flex flex-col p-0 min-h-0">
                <div className="p-3 border-b border-slate-100 flex items-center justify-between flex-shrink-0">
                  <div className="flex items-center gap-2">
                    <ShoppingCart size={16} className="text-slate-400" />
                    <span className="font-semibold text-sm">Cart</span>
                    <Badge variant="secondary" className="text-[10px]">{cart.length}</Badge>
                  </div>
                  {cart.length > 0 && (
                    <Button variant="ghost" size="sm" onClick={clearCart} className="text-xs text-slate-400">Clear</Button>
                  )}
                </div>
                
                <ScrollArea className="flex-1 p-3 min-h-0">
                  {cart.length === 0 ? (
                    <p className="text-center text-slate-400 text-sm py-8">Cart empty</p>
                  ) : (
                    <div className="space-y-2">
                      {cart.map(item => (
                        <div key={item.product_id} className={`p-2 rounded-lg ${item.needs_jit_retail ? 'bg-red-50 border border-red-200' : item.global_only_retail ? 'bg-amber-50 border border-amber-200' : 'bg-slate-50'} space-y-1.5`}>
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate">{item.product_name}</p>
                              {item.needs_jit_retail && (
                                <Badge variant="destructive" className="text-[9px] py-0 px-1.5 mt-0.5" data-testid={`no-retail-badge-${item.product_id}`}>No Retail — set below</Badge>
                              )}
                              {item.global_only_retail && !item.needs_jit_retail && (
                                <Badge className="text-[9px] py-0 px-1.5 mt-0.5 bg-amber-100 text-amber-700 hover:bg-amber-100" data-testid={`global-price-badge-${item.product_id}`}>Global Price</Badge>
                              )}
                            </div>
                            <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-red-400 flex-shrink-0" onClick={() => removeFromCart(item.product_id)}>
                              <Trash2 size={11} />
                            </Button>
                          </div>
                          <div className="flex items-center gap-1.5">
                            {/* Quantity controls */}
                            <div className="flex items-center border border-slate-200 rounded overflow-hidden flex-shrink-0">
                              <button className="px-1.5 py-1 text-slate-400 hover:bg-slate-100 h-7" onClick={() => updateQty(item.product_id, -1)}><Minus size={11} /></button>
                              <CalcInput
                                className="w-12 text-center text-sm h-7 border-0 focus:outline-none"
                                value={String(item._qtyStr ?? item.quantity ?? '')}
                                onChange={(v) => setCartQty(item.product_id, v)}
                                onBlur={() => {
                                  const n = parseFloat(item._qtyStr ?? item.quantity) || 0;
                                  if (n === 0) removeFromCart(item.product_id);
                                  else setCartQty(item.product_id, n);
                                }}
                                selectOnFocus
                                data-testid={`cart-qty-${item.product_id}`}
                              />
                              <button className="px-1.5 py-1 text-slate-400 hover:bg-slate-100 h-7" onClick={() => updateQty(item.product_id, 1)}><Plus size={11} /></button>
                            </div>
                            <span className="text-xs text-slate-400">×</span>
                            {/* Price (editable) */}
                            <CalcInput
                              className={`w-24 h-7 text-sm text-right px-2 border rounded focus:outline-none focus:ring-1 focus:ring-[#1A4D2E]/30 ${
                                item.price <= 0 ? 'border-amber-400 bg-amber-50 text-amber-700'
                                : !item.is_repack && parseFloat(item.original_price) > 0 && Math.abs(item.price - item.original_price) > 0.001 ? 'border-amber-500 bg-amber-50 text-amber-800 font-semibold'
                                : canViewCost && (item.effective_capital || item.cost_price) > 0 && item.price < (item.effective_capital || item.cost_price) ? 'border-red-300 bg-red-50 text-red-600'
                                : 'border-slate-200'
                              }`}
                              value={String(item._priceStr ?? item.price ?? '')}
                              onChange={(v) => updateCartPrice(item.product_id, v)}
                              onBlur={() => {
                                if (item._priceStr !== undefined) {
                                  const p = parseFloat(item._priceStr) || 0;
                                  setCart(prev => prev.map(c => c.product_id !== item.product_id ? c : { ...c, price: p, total: p * c.quantity, _priceStr: undefined }));
                                }
                              }}
                              selectOnFocus
                              disabled={!canDiscount}
                              title={!canDiscount ? 'No permission to change prices' : 'Edit to trigger Price Match (manager PIN required at checkout)'}
                            />
                            <span className="text-xs font-semibold text-[#1A4D2E] text-right flex-1">{formatPHP(item.total)}</span>
                          </div>
                          {!item.is_repack && parseFloat(item.original_price) > 0 && Math.abs(item.price - item.original_price) > 0.001 && (() => {
                            const delta = item.price - item.original_price;
                            const isIncrease = delta > 0;
                            const pct = item.original_price > 0 ? (delta / item.original_price) * 100 : 0;
                            return (
                              <p className={`text-[10px] flex items-center gap-1 font-medium ${isIncrease ? 'text-emerald-700' : 'text-rose-700'}`} data-testid={`price-match-flag-${item.product_id}`}>
                                {isIncrease ? <ArrowUpRight size={11} className="text-emerald-600" /> : <ArrowDownRight size={11} className="text-rose-600" />}
                                <span className="font-semibold">Price {isIncrease ? 'increased' : 'decreased'}</span>
                                <span className="line-through opacity-60">{formatPHP(item.original_price)}</span>
                                <span>→ {formatPHP(item.price)}</span>
                                <span className={`px-1 rounded ${isIncrease ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>
                                  {isIncrease ? '+' : '−'}{formatPHP(Math.abs(delta))} ({Math.abs(pct).toFixed(1)}%)
                                </span>
                                <span className="text-slate-500 italic">(manager PIN at checkout)</span>
                              </p>
                            );
                          })()}
                          {item.price <= 0 && (
                            <p className="text-[10px] text-amber-600 flex items-center gap-1"><AlertTriangle size={9}/> Set price before checkout</p>
                          )}
                          {item.is_repack && item.needs_jit_retail && item.price > 0 && (item.effective_capital || item.cost_price) > 0 && (
                            <p className="text-[10px] text-emerald-700 flex items-center gap-1" data-testid={`jit-capital-${item.product_id}`}>
                              Capital ₱{(item.effective_capital || item.cost_price).toFixed(2)}
                              {' · '}
                              Markup ₱{(item.price - (item.effective_capital || item.cost_price)).toFixed(2)}
                              {' '}({(((item.price - (item.effective_capital || item.cost_price)) / (item.effective_capital || item.cost_price)) * 100).toFixed(1)}%)
                              {' — '}<span className="italic text-slate-500">PIN required at checkout to save.</span>
                            </p>
                          )}
                          {canViewCost && item.price > 0 && (item.effective_capital || item.cost_price) > 0 && item.price < (item.effective_capital || item.cost_price) && (
                            <p className="text-[10px] text-red-600 flex items-center gap-1">
                              <AlertTriangle size={9}/> Below capital ₱{(item.effective_capital || item.cost_price).toFixed(2)}
                              {item.capital_method && item.capital_method !== 'manual' && (
                                <span className="opacity-60">({item.capital_method.replace('_',' ')})</span>
                              )}
                            </p>
                          )}
                          {/* Capital reference — read-only display only. Sales NEVER modify
                              moving average or capital_method (only POs / transfers / Count Sheet do). */}
                          {canViewCost && (item.moving_average_cost > 0 || item.last_purchase_cost > 0) && (
                            <div
                              className="flex items-center gap-2 text-[10px] mt-0.5"
                              title="Reference values only — sales do NOT change moving average or capital. Updates happen via POs, Transfers, or Count Sheet."
                            >
                              <Info size={9} className="text-slate-300 shrink-0" />
                              <span className="text-slate-300 italic">Ref:</span>
                              {item.moving_average_cost > 0 && (
                                <span className={`${item.price > 0 && item.price < item.moving_average_cost ? 'text-red-500 font-semibold' : 'text-slate-400'}`}>
                                  MA ₱{item.moving_average_cost.toFixed(2)}
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
                      {/* Anchor for auto-scroll: keeps the latest cart item visible. */}
                      <div ref={cartEndRef} aria-hidden="true" />
                    </div>
                  )}
                </ScrollArea>

                <div className="p-3 border-t border-slate-100 space-y-2 flex-shrink-0">
                  <div className="flex justify-between text-sm"><span>Subtotal</span><span>{formatPHP(subtotal)}</span></div>
                  <Separator />
                  <div className="flex justify-between font-bold"><span>Total</span><span className="text-lg">{formatPHP(grandTotal)}</span></div>
                  {activeDraftId && (
                    <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 font-medium">
                      <ClipboardList size={11} />
                      Editing Draft: <span className="font-mono font-bold">{draftOrders.find(d => d.id === activeDraftId)?.invoice_number || '...'}</span>
                    </div>
                  )}
                  <Button
                    data-testid="prepare-order-btn"
                    variant="outline"
                    className="w-full border-amber-400 text-amber-700 hover:bg-amber-50"
                    onClick={handlePrepareOrder}
                    disabled={cart.length === 0 || preparingOrder}
                  >
                    <ClipboardList size={15} className="mr-1.5" />
                    {preparingOrder ? 'Saving...' : activeDraftId ? 'Update Draft' : 'Prepare Order'}
                  </Button>
                  <Button 
                    data-testid="checkout-btn"
                    className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                    onClick={openCheckout}
                    disabled={cart.length === 0 || !currentBranch?.id}
                    title={!currentBranch?.id
                      ? 'A sale must belong to a specific branch. Open the branch picker in the top header and pick one.'
                      : undefined}
                  >
                    <CreditCard size={16} className="mr-2" /> {activeDraftId ? 'Finalize & Pay' : (!currentBranch?.id ? 'Select a branch to checkout' : 'Checkout')}
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
                        <th className="text-right px-3 py-2 text-xs uppercase text-slate-500 font-medium w-28" title="Amount discounts apply per unit (₱X × qty). Percent discounts apply to the line total.">Discount<span className="text-[9px] lowercase text-slate-400 normal-case font-normal ml-1">/unit</span></th>
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
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <p className="text-sm font-medium truncate">{line.product_name}</p>
                                    {line.needs_jit_retail && (
                                      <Badge variant="destructive" className="text-[9px] py-0 px-1.5" data-testid={`line-no-retail-${i}`}>No Retail</Badge>
                                    )}
                                    {line.global_only_retail && !line.needs_jit_retail && (
                                      <Badge className="text-[9px] py-0 px-1.5 bg-amber-100 text-amber-700 hover:bg-amber-100" data-testid={`line-global-price-${i}`}>Global Price</Badge>
                                    )}
                                    {pendingReviewIds.has(line.product_id) && currentBranch?.id && (
                                      <GlobalPriceBadge
                                        productId={line.product_id}
                                        branchId={currentBranch.id}
                                        reviewed={false}
                                        onMarked={() => {
                                          setPendingReviewIds(prev => {
                                            const s = new Set(prev); s.delete(line.product_id); return s;
                                          });
                                        }}
                                      />
                                    )}
                                  </div>
                                  {line.description && <p className="text-[11px] text-slate-400 truncate">{line.description}</p>}
                                  {/* Stock — always visible */}
                                  {line.available !== undefined && line.available !== null && (
                                    <p className="text-[10px] text-slate-400 mt-0.5">
                                      Stock: <span className="font-medium text-slate-600">{Number(line.available).toFixed(0)} {line.unit || ''}</span>
                                      {capitalShown && costMap[line.product_id] && (
                                        <span className="ml-2 font-mono">
                                          · Cap <span className="text-slate-700">{formatPHP(costMap[line.product_id].effective_cost)}</span>
                                          {costMap[line.product_id].last_purchase ? <span className="ml-1.5">· LP {formatPHP(costMap[line.product_id].last_purchase)}</span> : null}
                                          {costMap[line.product_id].moving_average ? <span className="ml-1.5">· MA {formatPHP(costMap[line.product_id].moving_average)}</span> : null}
                                        </span>
                                      )}
                                    </p>
                                  )}
                                  {/* JIT capital hint — always visible for repacks needing retail */}
                                  {line.needs_jit_retail && (line.effective_capital || line.cost_price) > 0 && (
                                    <p className="text-[10px] text-emerald-700 mt-0.5 font-mono" data-testid={`line-jit-capital-${i}`}>
                                      Capital ₱{(line.effective_capital || line.cost_price).toFixed(2)}
                                      {line.rate > 0 && (
                                        <>
                                          {' · Markup ₱'}{(line.rate - (line.effective_capital || line.cost_price)).toFixed(2)}
                                          {' '}({(((line.rate - (line.effective_capital || line.cost_price)) / (line.effective_capital || line.cost_price)) * 100).toFixed(1)}%)
                                        </>
                                      )}
                                    </p>
                                  )}
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
                            <CalcInput
                              ref={el => qtyRefs.current[i] = el}
                              className="h-8 text-right w-16"
                              value={String(line._quantityStr ?? line.quantity ?? '')}
                              selectOnFocus
                              onChange={(v) => updateLine(i, 'quantity', v)}
                              onBlur={() => finalizeLineField(i, 'quantity')}
                            />
                          </td>
                          <td className="px-3 py-1">
                            <div>
                              <CalcInput
                                className={`h-8 text-right w-24 ${
                                  line.product_id && line.rate <= 0 ? 'border-amber-400 bg-amber-50'
                                  : line.product_id && !line.is_repack && parseFloat(line.original_rate) > 0 && Math.abs(line.rate - line.original_rate) > 0.001 ? 'border-amber-500 bg-amber-50 text-amber-800 font-semibold'
                                  : canViewCost && line.product_id && (line.effective_capital || line.cost_price) > 0 && line.rate > 0 && line.rate < (line.effective_capital || line.cost_price) ? 'border-red-300 bg-red-50 text-red-700'
                                  : ''
                                }`}
                                value={String(line._rateStr ?? line.rate ?? '')}
                                selectOnFocus
                                onChange={(v) => updateLine(i, 'rate', v)}
                                onBlur={() => finalizeLineField(i, 'rate')}
                                disabled={!canDiscount}
                                title={!canDiscount ? 'No permission to change prices' : 'Edit to trigger Price Match (manager PIN required at checkout)'}
                              />
                              {/* Price Match flag for changed rate — directional + delta */}
                              {line.product_id && !line.is_repack && parseFloat(line.original_rate) > 0 && Math.abs(line.rate - line.original_rate) > 0.001 && (() => {
                                const delta = line.rate - line.original_rate;
                                const isIncrease = delta > 0;
                                const pct = line.original_rate > 0 ? (delta / line.original_rate) * 100 : 0;
                                return (
                                  <p className={`text-[10px] flex items-center gap-1 font-medium mt-0.5 ${isIncrease ? 'text-emerald-700' : 'text-rose-700'}`}
                                    data-testid={`order-price-match-flag-${line.product_id}`}>
                                    {isIncrease ? <ArrowUpRight size={11} className="text-emerald-600" /> : <ArrowDownRight size={11} className="text-rose-600" />}
                                    <span className="font-semibold">{isIncrease ? 'Up' : 'Down'}</span>
                                    <span className="line-through opacity-60">{formatPHP(line.original_rate)}</span>
                                    <span>→ {formatPHP(line.rate)}</span>
                                    <span className={`px-1 rounded ${isIncrease ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>
                                      {isIncrease ? '+' : '−'}{formatPHP(Math.abs(delta))} ({Math.abs(pct).toFixed(1)}%)
                                    </span>
                                  </p>
                                );
                              })()}
                              {/* Capital reference — read-only display only. Sales NEVER modify
                                  moving average or capital_method. */}
                              {canViewCost && line.product_id && (line.moving_average_cost > 0 || line.last_purchase_cost > 0) && (
                                <div
                                  className="flex flex-col gap-0.5 mt-0.5"
                                  title="Reference values only — sales do NOT change moving average or capital. Updates happen via POs, Transfers, or Count Sheet."
                                >
                                  <span className="text-[9px] text-slate-300 italic flex items-center gap-1">
                                    <Info size={8} /> Ref (read-only)
                                  </span>
                                  {line.moving_average_cost > 0 && (
                                    <span className={`text-[10px] ${line.rate > 0 && line.rate < line.moving_average_cost ? 'text-red-500 font-semibold' : 'text-slate-400'}`}>
                                      MA ₱{line.moving_average_cost.toFixed(2)}
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
                                  <CalcInput
                                    className={`h-8 text-right w-20 ${isBelowCap ? 'border-red-400 bg-red-50 text-red-700' : ''} ${!canDiscount ? 'bg-slate-100 cursor-not-allowed' : ''}`}
                                    value={String(line._discount_valueStr ?? line.discount_value ?? '')}
                                    selectOnFocus
                                    onChange={(v) => updateLine(i, 'discount_value', v)}
                                    onBlur={() => finalizeLineField(i, 'discount_value')}
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
                  {/* Anchor for auto-scroll: keeps the latest order line visible. */}
                  <div ref={orderLinesEndRef} aria-hidden="true" />
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
                          <CalcInput className="h-7 w-24 text-right" value={String(freight || '')} onChange={(v) => setFreight(parseFloat(v) || 0)} />
                        </div>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-slate-500">Discount</span>
                          <CalcInput className={`h-7 w-24 text-right ${!canDiscount ? 'bg-slate-100 cursor-not-allowed' : ''}`} value={String(overallDiscount || '')} onChange={(v) => setOverallDiscount(parseFloat(v) || 0)} disabled={!canDiscount} />
                        </div>
                        <Separator />
                        <div className="flex justify-between font-bold text-base" style={{ fontFamily: 'Manrope' }}>
                          <span>Total</span><span className="text-[#1A4D2E]">{formatPHP(grandTotal)}</span>
                        </div>
                        {activeDraftId && (
                          <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 font-medium">
                            <ClipboardList size={11} />
                            Editing Draft: <span className="font-mono font-bold">{draftOrders.find(d => d.id === activeDraftId)?.invoice_number || '...'}</span>
                          </div>
                        )}
                        <Button
                          data-testid="prepare-order-btn"
                          variant="outline"
                          className="w-full border-amber-400 text-amber-700 hover:bg-amber-50"
                          onClick={handlePrepareOrder}
                          disabled={!lines.some(l => l.product_id) || preparingOrder}
                        >
                          <ClipboardList size={15} className="mr-1.5" />
                          {preparingOrder ? 'Saving...' : activeDraftId ? 'Update Draft' : 'Prepare Order'}
                        </Button>
                        <Button
                          data-testid="checkout-btn"
                          className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white mt-1"
                          onClick={openCheckout}
                          disabled={items.length === 0 || !currentBranch?.id}
                          title={!currentBranch?.id
                            ? 'A sale must belong to a specific branch. Open the branch picker in the top header and pick one.'
                            : undefined}
                        >
                          <CreditCard size={15} className="mr-2" /> {activeDraftId ? 'Finalize & Pay' : (!currentBranch?.id ? 'Select a branch to checkout' : 'Complete & Pay')}
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
      {/* Phase 4 Cleanup — extracted to `components/CheckoutDialog.jsx`.
          Payment state stays page-owned for this pass; the dialog is a
          pure render that consumes setters via props. `confirmDisabled`
          is pre-computed above so the dialog stays presentational. */}
      <CheckoutDialog
        open={checkoutDialog}
        onOpenChange={setCheckoutDialog}
        selectedCustomer={selectedCustomer}
        setSelectedCustomer={setSelectedCustomer}
        custSearch={custSearch}
        setCustSearch={setCustSearch}
        customers={customers}
        canViewBalance={canViewBalance}
        grandTotal={grandTotal}
        change={change}
        paymentType={paymentType}
        setPaymentType={setPaymentType}
        amountTendered={amountTendered}
        setAmountTendered={setAmountTendered}
        partialPayment={partialPayment}
        setPartialPayment={setPartialPayment}
        splitCash={splitCash}
        setSplitCash={setSplitCash}
        splitDigital={splitDigital}
        setSplitDigital={setSplitDigital}
        digitalPlatform={digitalPlatform}
        setDigitalPlatform={setDigitalPlatform}
        digitalRefNumber={digitalRefNumber}
        setDigitalRefNumber={setDigitalRefNumber}
        digitalSender={digitalSender}
        setDigitalSender={setDigitalSender}
        releaseMode={releaseMode}
        setReleaseMode={setReleaseMode}
        onConfirm={handleCreditSale}
        saving={saving}
        confirmDisabled={confirmDisabled}
        isHistoricalCreditMode={isHistoricalCreditMode}
      />

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
              const r = await api.post('/doc/generate-code', { doc_type: 'invoice', doc_id: inv.id });
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

      {/* Pre-invoice signature dialog (credit/partial). Customer signs FIRST,
          then sale is submitted with signature attached, then RefPrompt opens. */}
      <RequestSignatureDialog
        open={!!preInvoiceSig}
        onOpenChange={(open) => { if (!open) setPreInvoiceSig(null); }}
        invoice={preInvoiceSig?.invoice}
        preInvoice={true}
        onConfirmSale={async (sigData) => {
          const approver = preInvoiceSig?.approvedBy || null;
          setPreInvoiceSig(null);
          await processSale(approver, null, sigData);
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
              {managerPin && isPinSessionWarm() && (
                <p className="text-[10px] text-emerald-600 mb-1 flex items-center gap-1 font-medium" data-testid="credit-pin-session-hint">
                  <Unlock size={10} /> PIN auto-filled from active session
                </p>
              )}
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

            {/* Offline credit-sale: required reason for audit log */}
            {!isOnline && (paymentType === 'credit' || paymentType === 'partial') && selectedCustomer?.id && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
                <p className="text-[11px] font-semibold text-amber-800 flex items-center gap-1">
                  <WifiOff size={11} /> Offline mode — Admin PIN or branch Manager PIN
                </p>
                <Label className="text-[11px]">Reason for offline credit (required)</Label>
                <Input
                  data-testid="offline-bypass-reason"
                  value={offlineBypassReason}
                  onChange={e => setOfflineBypassReason(e.target.value)}
                  placeholder="e.g. Customer in a hurry, signed paper slip"
                  className="h-9 text-xs bg-white"
                />
                <p className="text-[10px] text-amber-700 leading-relaxed">
                  No signature can be captured offline. The bypass + reason is logged for audit when the device reconnects.
                </p>
              </div>
            )}

            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setCreditApprovalDialog(false); setManagerPin(''); setOfflineBypassReason(''); }}>
                Cancel
              </Button>
              <Button 
                data-testid="verify-pin"
                className="flex-1 bg-amber-500 hover:bg-amber-600 text-white"
                onClick={() => verifyManagerPin()}
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

      {/* Price Save Dialog removed — replaced by PriceMatchModal at checkout */}

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
                      <p className="text-[10px] text-red-500">By {selectedInvoice.void_authorized_by} · {fmtDateTime(selectedInvoice.voided_at)}</p>
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
                        const pin = isPinSessionWarm() ? pinSessionRef.current.pin : prompt('Enter manager PIN to verify this payment:');
                        if (!pin) return;
                        try {
                          await api.post(`/uploads/mark-reviewed/invoice/${selectedInvoice.id}`, { pin });
                          startPinSession(pin, 'manager_pin', '');
                          toast.success('Payment verified!');
                          setSelectedInvoice({ ...selectedInvoice, receipt_review_status: 'reviewed' });
                          loadHistory();
                        } catch (e) {
                          if (isPinSessionWarm()) clearPinSession();
                          toast.error(e.response?.data?.detail || 'Verification failed');
                        }
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
                  onClick={() => {
                    if (isPinSessionWarm()) setVoidPin(pinSessionRef.current.pin);
                    setVoidDialog(true);
                  }}
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

      {/* Capital Reveal — PIN-gated unlock */}
      <Dialog open={capitalPinModal} onOpenChange={(o) => { if (!o) { setCapitalPinModal(false); setCapitalPinInput(''); } }}>
        <DialogContent className="sm:max-w-sm" data-testid="capital-pin-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Lock size={16} className="text-amber-600" />
              Show Capital
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <p className="text-sm text-slate-600">
              Enter an admin or manager PIN (or 6-digit TOTP) to reveal capital cost,
              last purchase, and moving average on product cards.
            </p>
            <Input
              type="password" autoComplete="off" inputMode="numeric"
              value={capitalPinInput} onChange={e => setCapitalPinInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submitCapitalPin(); }}
              placeholder="Enter PIN or TOTP" className="h-9"
              autoFocus data-testid="capital-pin-input"
            />
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" size="sm" onClick={() => { setCapitalPinModal(false); setCapitalPinInput(''); }}>
                Cancel
              </Button>
              <Button size="sm" onClick={submitCapitalPin} disabled={capitalPinSubmitting || !capitalPinInput}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="capital-pin-confirm">
                {capitalPinSubmitting ? <RefreshCw size={12} className="animate-spin mr-1.5" /> : <Eye size={12} className="mr-1.5" />}
                Reveal Capital
              </Button>
            </div>
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

      {/* JIT Repack Retail PIN Modal */}
      <Dialog open={jitPinModal.open} onOpenChange={(o) => { if (!o) { setJitPinModal({ open: false, items: [], saleData: null }); setJitPin(''); } }}>
        <DialogContent className="max-w-md" data-testid="jit-pin-dialog">
          <DialogHeader>
            <DialogTitle>Save new repack retail price{(jitPinModal.items?.length || 0) > 1 ? 's' : ''}?</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <p className="text-slate-600">
              You're saving <strong>{jitPinModal.items?.length || 0}</strong> new repack retail price{(jitPinModal.items?.length || 0) > 1 ? 's' : ''} for <strong>{currentBranch?.name}</strong>.
              Future sales of these repacks at this branch will use these prices automatically.
            </p>
            <ul className="text-xs space-y-1 max-h-40 overflow-auto bg-slate-50 rounded p-2">
              {(jitPinModal.items || []).map((it, i) => (
                <li key={i} className="flex justify-between" data-testid={`jit-item-${i}`}>
                  <span className="truncate">{it.product_id}</span>
                  <span className="font-mono font-semibold">{formatPHP(it.retail)}</span>
                </li>
              ))}
            </ul>
            <Input
              type="password"
              value={jitPin}
              onChange={(e) => setJitPin(e.target.value)}
              placeholder="Owner PIN (or 6-digit TOTP)"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter' && jitPin) document.getElementById('jit-pin-confirm-btn')?.click(); }}
              data-testid="jit-pin-input"
            />
            <div className="flex justify-end gap-2 pt-1">
              <Button
                variant="outline"
                onClick={() => { setJitPinModal({ open: false, items: [], saleData: null }); setJitPin(''); }}
                data-testid="jit-pin-cancel-btn"
              >
                Cancel sale
              </Button>
              <Button
                id="jit-pin-confirm-btn"
                disabled={!jitPin}
                data-testid="jit-pin-confirm-btn"
                onClick={async () => {
                  if (!jitPin) return;
                  const pinToUse = jitPin;
                  const sigToReuse = jitPinModal.signatureData || null;
                  setJitPinModal({ open: false, items: [], saleData: null });
                  setJitPin('');
                  // Re-trigger save with PIN passed inline + signature carried over
                  setTimeout(() => processSale(null, pinToUse, sigToReuse), 30);
                }}
              >
                Confirm & Continue Sale
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Price Match Modal ─────────────────────────────────────────── */}
      <PriceMatchModal
        open={priceMatchModal}
        priceChanges={computePriceChanges()}
        schemeName={schemes.find(s => s.key === activeScheme)?.name || activeScheme}
        branchName={currentBranch?.name || ''}
        customerName={selectedCustomer?.name || ''}
        warmPin={isPinSessionWarm() ? pinSessionRef.current?.pin : null}
        submitting={priceMatchSubmitting}
        error={priceMatchError}
        onCancel={() => {
          if (priceMatchSubmitting) return;
          setPriceMatchModal(false);
          setPriceMatchError('');
        }}
        onConfirm={async ({ price_changes, pin }) => {
          // Online: server validates the PIN at /unified-sale time.
          // Offline: validate locally NOW so we can fail fast (clear error
          // in the modal) instead of letting a bad PIN reach the queue.
          // Either way the plaintext PIN ships with the queued sale and the
          // backend re-validates on sync.
          setPriceMatchSubmitting(true);
          setPriceMatchError('');
          if (!isOnline) {
            try {
              const { verifyOfflinePin } = await import('../lib/offlineAuth');
              const result = await verifyOfflinePin(pin);
              if (!result.ok) {
                setPriceMatchError(result.reason || 'Invalid PIN (offline)');
                setPriceMatchSubmitting(false);
                return;
              }
            } catch {
              setPriceMatchError('Could not verify PIN offline. Sync once while online to enable offline price match.');
              setPriceMatchSubmitting(false);
              return;
            }
          }
          setPriceMatchApproved({ price_changes, pin });
          startPinSession(pin, 'manager_pin', '');
          setPriceMatchSubmitting(false);
          setPriceMatchModal(false);
          // Continue directly to the next checkout step. Calling openCheckout()
          // here would capture a stale closure where priceMatchApproved is still
          // null, causing the modal to re-open on first click (Iter 220 fix).
          setPaymentType('cash');
          setAmountTendered(grandTotal);
          setPartialPayment(0);
          setReleaseMode('full');
          if (selectedCustomer && custEdited) {
            setCustSaveDialog(true);
          } else {
            setCheckoutDialog(true);
          }
        }}
      />

      {/* Late-Encode for Closed Days — credit/partial only */}
      <LateEncodeDialog
        open={lateEncodeDialog.open}
        orderDate={lateEncodeDialog.saleData?.order_date}
        paymentType={lateEncodeDialog.saleData?.payment_type}
        warmPin={isPinSessionWarm() ? pinSessionRef.current?.pin : null}
        onClose={() => setLateEncodeDialog({ open: false, saleData: null, signatureData: null })}
        onConfirm={async ({ reason, pin }) => {
          const retryData = {
            ...lateEncodeDialog.saleData,
            late_encode: { reason, pin },
          };
          try {
            const res = await api.post('/unified-sale', retryData);
            const invoiceNum = res.data.invoice_number || res.data.sale_number;
            invalidateBalanceCache();
            toast.success(`⚠ Late-encoded for ${retryData.order_date} — Invoice ${invoiceNum}`);
            setRefPrompt({
              open: true, number: invoiceNum,
              title: retryData.customer_name || 'Walk-in',
              invoiceData: { ...retryData, ...res.data, invoice_number: invoiceNum, late_encoded: true },
            });
            clearCart();
            setCheckoutDialog(false);
            setPendingCreditSale(null);
            setLateEncodeDialog({ open: false, saleData: null, signatureData: null });
          } catch (err) {
            const msg = err?.response?.data?.detail || 'Late-encode failed';
            toast.error(msg);
            setLateEncodeDialog({ open: false, saleData: null, signatureData: null });
          }
        }}
      />

      {/* ── Park Sale: optional label prompt ─────────────────────────────── */}
      <Dialog open={parkPromptOpen} onOpenChange={(o) => { setParkPromptOpen(o); if (!o) setParkLabel(''); }}>
        <DialogContent data-testid="park-prompt-dialog" className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <PauseCircle size={18} className="text-amber-600" /> Park this sale
            </DialogTitle>
            <DialogDescription>
              Save the current cart so you can serve another customer. You'll be able to resume it from <strong>Parked</strong> on this or any device at this branch.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <Label className="text-xs">Optional note (helps you find it later)</Label>
              <Input
                value={parkLabel}
                onChange={e => setParkLabel(e.target.value)}
                placeholder='e.g. "guy in red shirt", "rice farmer Pedro"'
                maxLength={60}
                data-testid="park-label-input"
                onKeyDown={(e) => { if (e.key === 'Enter') handleParkConfirm(); }}
                autoFocus
              />
              <p className="text-[10px] text-slate-400 mt-1">
                Auto-tagged with {selectedCustomer?.name ? <b>{selectedCustomer.name}</b> : <b>Walk-in</b>} and the time.
              </p>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-50 rounded p-2">
              {(() => { const { itemCount, subtotal } = computeParkSummary(); return (
                <>
                  <ShoppingCart size={12} />
                  <span>{itemCount.toFixed(0)} item{itemCount === 1 ? '' : 's'} · {formatPHP(subtotal)}</span>
                  <span className="ml-auto text-amber-600 italic">Stock NOT reserved</span>
                </>
              ); })()}
            </div>
            {!isOnline && (
              <div className="flex items-center gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                <WifiOff size={12} /> Offline — will sync to other devices when you're back online.
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => { setParkPromptOpen(false); setParkLabel(''); }} data-testid="park-cancel-btn">
              Cancel
            </Button>
            <Button
              className="bg-amber-600 hover:bg-amber-700 text-white"
              onClick={handleParkConfirm}
              data-testid="park-confirm-btn"
            >
              <PauseCircle size={14} className="mr-1.5" /> Park sale
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Parked Sales: list + resume + discard ────────────────────────── */}
      <Dialog open={parkedDialogOpen} onOpenChange={setParkedDialogOpen}>
        <DialogContent data-testid="parked-sales-dialog" className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Inbox size={18} className="text-[#1A4D2E]" /> Parked Sales
              {parkedSales.length > 0 && (
                <Badge variant="secondary" className="ml-1">{parkedSales.length}</Badge>
              )}
            </DialogTitle>
            <DialogDescription>
              Drafts saved at this branch. Auto-purged after 24 hours.
              {!isOnline && <span className="ml-2 text-amber-600">· Offline view (cached)</span>}
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[60vh] overflow-y-auto -mx-6 px-6 divide-y divide-slate-100">
            {parkedSales.length === 0 ? (
              <div className="text-center py-8 text-slate-400 text-sm">
                No parked sales right now.
              </div>
            ) : parkedSales.map((park) => {
              const isOwn = park.created_by === user?.id;
              const isPending = park._sync && park._sync !== 'synced';
              const ageMin = Math.max(0, Math.floor((Date.now() - new Date(park.created_at).getTime()) / 60000));
              const ageStr = ageMin < 60 ? `${ageMin}m ago` : ageMin < 1440 ? `${Math.floor(ageMin / 60)}h ago` : `${Math.floor(ageMin / 1440)}d ago`;
              return (
                <div key={park.id} className="py-3 flex items-start gap-3" data-testid={`parked-row-${park.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm truncate">{park.label || 'Untitled park'}</span>
                      <Badge variant="outline" className="text-[9px] py-0 px-1.5">
                        {park.mode === 'quick' ? <><Zap size={9} className="mr-0.5" /> Quick Sale</> : <><ClipboardList size={9} className="mr-0.5" /> Detailed Sale</>}
                      </Badge>
                      {!isOwn && (
                        <Badge className="text-[9px] py-0 px-1.5 bg-slate-100 text-slate-600 hover:bg-slate-100">
                          {park.created_by_name || 'Other cashier'}
                        </Badge>
                      )}
                      {isPending && (
                        <Badge className="text-[9px] py-0 px-1.5 bg-amber-100 text-amber-700 hover:bg-amber-100" title="Will sync to server when online">
                          {park._sync === 'pending_create' ? 'Pending sync' : 'Pending discard'}
                        </Badge>
                      )}
                    </div>
                    <div className="flex gap-3 mt-0.5 text-[11px] text-slate-500">
                      <span>{park.item_count?.toFixed(0) || 0} items</span>
                      <span>·</span>
                      <span className="font-semibold text-slate-700">{formatPHP(park.subtotal || 0)}</span>
                      <span>·</span>
                      <span>{ageStr}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    <Button
                      size="sm"
                      onClick={() => resumeParkedSale(park)}
                      data-testid={`resume-park-${park.id}`}
                      className="bg-[#1A4D2E] hover:bg-[#14532d] text-white h-8"
                    >
                      <RotateCcw size={12} className="mr-1" /> Resume
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDiscardClick(park)}
                      data-testid={`discard-park-${park.id}`}
                      className="h-8 text-red-500 hover:bg-red-50"
                      title={isOwn ? 'Discard this park' : "Discard (manager PIN required for other cashier's park)"}
                    >
                      <Trash2 size={12} />
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex justify-end pt-2">
            <Button variant="outline" size="sm" onClick={refreshParkedSales} disabled={!isOnline}>
              <RefreshCw size={12} className="mr-1" /> Refresh
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Discard PIN prompt (other-cashier parks only) ─────────────────── */}
      <Dialog open={discardPinPrompt.open} onOpenChange={(o) => !o && setDiscardPinPrompt({ open: false, parkId: null, pin: '' })}>
        <DialogContent data-testid="discard-park-pin-dialog" className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Shield size={16} className="text-amber-600" /> Manager PIN required
            </DialogTitle>
            <DialogDescription>
              Discarding another cashier's parked sale needs a manager or admin PIN.
            </DialogDescription>
          </DialogHeader>
          <Input
            type="password"
            placeholder="Enter PIN"
            value={discardPinPrompt.pin}
            onChange={e => setDiscardPinPrompt(p => ({ ...p, pin: e.target.value }))}
            onKeyDown={e => { if (e.key === 'Enter') submitDiscardWithPin(); }}
            data-testid="discard-park-pin-input"
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDiscardPinPrompt({ open: false, parkId: null, pin: '' })}>Cancel</Button>
            <Button
              className="bg-red-600 hover:bg-red-700 text-white"
              onClick={submitDiscardWithPin}
              data-testid="discard-park-pin-confirm"
            >
              Discard
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Unsaved-changes leave guard ───────────────────────────────────── */}
      {/* Dialog is rendered by UnsavedChangesProvider — we only register here. */}

      {/* ── Draft Orders panel ────────────────────────────────────────────── */}
      <Dialog open={draftOrdersOpen} onOpenChange={setDraftOrdersOpen}>
        <DialogContent data-testid="draft-orders-dialog" className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ClipboardList size={18} className="text-amber-600" />
              Draft Orders
              {draftOrders.length > 0 && (
                <Badge className="ml-1 bg-amber-100 text-amber-700 border-amber-300">{draftOrders.length}</Badge>
              )}
            </DialogTitle>
            <DialogDescription>
              Orders saved for preparation — not yet paid. Invoice numbers are already reserved.
            </DialogDescription>
          </DialogHeader>

          {draftOrders.length === 0 ? (
            <div className="text-center py-8 text-slate-400">
              <ClipboardList size={32} className="mx-auto mb-2 opacity-40" />
              <p className="text-sm">No draft orders right now.</p>
              <p className="text-xs mt-1">Use "Prepare Order" on the sales screen to create one.</p>
            </div>
          ) : (
            <div className="divide-y">
              {draftOrders.map(draft => {
                const pendingOff = pendingDraftCompletions[draft.id];
                const isPendingOffline = !!pendingOff;
                return (
                <div key={draft.id} className="py-4 space-y-2.5" data-testid={`draft-order-row-${draft.id}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono font-bold text-sm text-slate-800">{draft.invoice_number}</span>
                        {isPendingOffline ? (
                          <Badge className="bg-yellow-100 text-yellow-800 border-yellow-300 text-[10px] px-1.5" data-testid={`draft-pending-offline-${draft.id}`}>
                            Offline Completion Pending — {pendingOff.offline_receipt_number}
                          </Badge>
                        ) : (
                          <Badge className="bg-amber-100 text-amber-800 border-amber-300 text-[10px] px-1.5">
                            FOR PREPARATION
                          </Badge>
                        )}
                        {draft.id === activeDraftId && !isPendingOffline && (
                          <Badge className="bg-blue-100 text-blue-700 border-blue-300 text-[10px] px-1.5">
                            Currently Editing
                          </Badge>
                        )}
                      </div>
                      <div className="text-sm text-slate-600 mt-0.5 truncate">
                        {draft.customer_name && draft.customer_name !== 'Walk-in' ? draft.customer_name : 'Walk-in Customer'}
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5">
                        {draft.items?.length || 0} item{draft.items?.length !== 1 ? 's' : ''} &middot; {fmtDate(draft.order_date)} &middot; {draft.cashier_name}
                      </div>
                      {isPendingOffline && (
                        <div className="text-[11px] text-yellow-700 mt-1.5 italic">
                          This draft was finalized offline as <span className="font-mono font-semibold">{pendingOff.offline_receipt_number}</span> and will sync when online. Editing & finalizing are disabled to prevent duplicates.
                        </div>
                      )}
                    </div>
                    <div className="text-sm font-bold text-[#1A4D2E] whitespace-nowrap">{formatPHP(draft.grand_total)}</div>
                  </div>

                  <div className="flex gap-2 flex-wrap">
                    <Button
                      size="sm" variant="outline"
                      className="text-slate-700"
                      onClick={() => loadDraftIntoCart(draft)}
                      disabled={isPendingOffline}
                      data-testid={`draft-open-${draft.id}`}
                      title={isPendingOffline ? 'Disabled — offline completion pending sync' : ''}
                    >
                      <ClipboardList size={13} className="mr-1" /> Open / Edit
                    </Button>
                    <Button
                      size="sm" variant="outline"
                      className="text-emerald-700 border-emerald-300 hover:bg-emerald-50"
                      onClick={() => { loadDraftIntoCart(draft); setTimeout(() => openCheckout(), 300); }}
                      disabled={isPendingOffline}
                      data-testid={`draft-pay-${draft.id}`}
                      title={isPendingOffline ? 'Disabled — offline completion pending sync' : ''}
                    >
                      <CreditCard size={13} className="mr-1" /> Pay Now
                    </Button>
                    <Button
                      size="sm" variant="outline"
                      className="text-blue-600 border-blue-200 hover:bg-blue-50"
                      onClick={async () => {
                        let docCode = draft.doc_code || '';
                        if (!docCode && draft.id) {
                          try {
                            const r = await api.post('/doc/generate-code', { doc_type: 'invoice', doc_id: draft.id });
                            docCode = r.data?.code || '';
                          } catch { /* print without QR */ }
                        }
                        PrintEngine.print({
                          type: 'order_slip',
                          data: { ...draft, cashier_name: user?.full_name || user?.username || '' },
                          format: 'full_page',
                          businessInfo: businessInfo || {},
                          docCode,
                        });
                      }}
                      data-testid={`draft-print-${draft.id}`}
                    >
                      Print Copy
                    </Button>
                    <Button
                      size="sm" variant="outline"
                      className="text-red-500 border-red-200 hover:bg-red-50"
                      onClick={() => cancelDraftOrder(draft.id)}
                      disabled={isPendingOffline}
                      data-testid={`draft-cancel-${draft.id}`}
                      title={isPendingOffline ? 'Disabled — offline completion pending sync' : ''}
                    >
                      <X size={13} className="mr-1" /> Cancel
                    </Button>
                  </div>
                </div>
                );
              })}
            </div>
          )}

          <div className="flex justify-between items-center pt-2 border-t">
            <Button variant="outline" size="sm" onClick={refreshDraftOrders} disabled={!isOnline}>
              <RefreshCw size={13} className="mr-1" /> Refresh
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDraftOrdersOpen(false)}>Close</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ─── Phase 4A — Historical Credit / Notebook AR commit dialog ───
          Final confirmation modal: shows preview output and gates the
          commit behind an Owner / Admin Authenticator (TOTP) code that
          is verified server-side. The frontend never sees the secret.
          Extracted to `components/HistoricalCreditDialog.jsx` (Phase 4
          cleanup); behavior + every `data-testid` preserved verbatim. */}
      <HistoricalCreditDialog
        hc={hc}
        customer={selectedCustomer}
        branch={currentBranch}
        orderDate={header.order_date}
        daysBack={daysBack}
        itemsCount={items.length}
        grandTotal={grandTotal}
      />

    </div>
  );
}
