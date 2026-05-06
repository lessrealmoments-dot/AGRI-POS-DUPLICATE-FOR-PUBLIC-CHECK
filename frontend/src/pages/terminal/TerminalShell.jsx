import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShoppingCart, ClipboardCheck, ArrowLeftRight, Wifi, WifiOff, RefreshCw, Settings, ChevronRight, Unlink, Search, X, Loader2, Printer, FileText, ExternalLink, CheckCircle2, ScanLine, FolderUp, Check } from 'lucide-react';
import { toast } from 'sonner';
import TerminalSales from './TerminalSales';
import TerminalPOCheck from './TerminalPOCheck';
import TerminalTransfers from './TerminalTransfers';
import TerminalDocUpload from './TerminalDocUpload';
import axios from 'axios';
import PrintEngine from '../../lib/PrintEngine';
import PrintBridge from '../../lib/PrintBridge';
import { DeviceIdentity } from '../../plugins/DeviceIdentityPlugin';
import {
  cacheProducts, getProducts, cacheCustomers,
  cachePriceSchemes, cacheInventory,
  cacheBranchPrices, setOfflineOrg, getPendingSaleCount,
  getProductCount, getLastSyncTime, setLastSyncTime,
  mergeProducts, mergeCustomers, updateInventoryBatch,
  getMeta, setOfflineAdminPinHash, setOfflinePinGrants,
} from '../../lib/offlineDB';
import { syncPendingSales, startAutoSync, stopAutoSync } from '../../lib/syncManager';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const WS_URL = BACKEND_URL.replace(/^http/, 'ws');
const fmtPHP = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 })}`;

// ── QuickScan Cloud Print — terminal-scoped send-to-print picker ──────────────
function QuickScanCloudPrint({ basic, branchId, api, businessInfo, onClose }) {
  const [terminals, setTerminals] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    api.get(`/print/terminals/for-branch/${branchId}`)
      .then(res => {
        const list = res.data || [];
        setTerminals(list);
        if (list.length === 1) setSelected(list[0].terminal_id);
        else { const online = list.find(t => t.is_online); if (online) setSelected(online.terminal_id); else if (list[0]) setSelected(list[0].terminal_id); }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [branchId, api]);

  const getHtml = () => {
    try {
      const data = basicDocToPrintData(basic);
      const type = basic.doc_type === 'invoice' ? 'order_slip' : basic.doc_type === 'purchase_order' ? 'purchase_order' : 'branch_transfer';
      return PrintEngine.generateHtml({ type, data, format: 'full_page', businessInfo: businessInfo || {}, docCode: '' });
    } catch { return '<p>Document</p>'; }
  };

  const getDocType = () => basic.doc_type === 'invoice' ? 'sales_receipt' : basic.doc_type === 'purchase_order' ? 'purchase_order' : 'branch_transfer';
  const getDocName = () => {
    const labels = { invoice: 'Sales Receipt', purchase_order: 'Purchase Order', branch_transfer: 'Branch Transfer' };
    return `${labels[basic.doc_type] || 'Document'} #${basic.number || ''}`;
  };

  const handleSend = async () => {
    if (!selected) return;
    setSending(true);
    try {
      await api.post('/print/jobs', {
        terminal_id: selected, branch_id: branchId,
        document_type: getDocType(), document_name: getDocName(),
        document_id: basic.id || '', reference_number: basic.number || '',
        html_content: getHtml(), metadata: { doc_type: basic.doc_type },
      });
      setSent(true);
    } catch { /* silent */ }
    setSending(false);
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-end" data-testid="terminal-cloud-print">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full bg-white rounded-t-3xl p-5 space-y-4" style={{ boxShadow: '0 -4px 24px rgba(0,0,0,0.13)' }}>
        <div className="flex items-center justify-between">
          <p className="font-bold text-slate-900 text-base" style={{ fontFamily: 'Manrope' }}>
            <Printer size={15} className="inline mr-1.5 text-emerald-600" /> Send to Cloud Printer
          </p>
          <button onClick={onClose} className="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center"><X size={13} /></button>
        </div>
        <p className="text-xs text-slate-500 truncate">{getDocName()}</p>
        {sent ? (
          <div className="text-center py-4 text-emerald-600">
            <CheckCircle2 size={32} className="mx-auto mb-1" />
            <p className="font-semibold text-sm">Sent to printer!</p>
            <button onClick={onClose} className="mt-3 text-xs text-slate-400 hover:underline">Close</button>
          </div>
        ) : loading ? (
          <div className="flex items-center justify-center py-4 text-slate-400"><Loader2 size={18} className="animate-spin mr-2" /> Loading...</div>
        ) : terminals.length === 0 ? (
          <p className="text-sm text-amber-600 text-center py-3">No printers registered for this branch.</p>
        ) : (
          <>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {terminals.map(t => (
                <button key={t.terminal_id} onClick={() => setSelected(t.terminal_id)}
                  className={`w-full flex items-center gap-3 p-2.5 rounded-xl border text-left transition-all ${selected === t.terminal_id ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200'}`}>
                  <Printer size={14} className={t.is_online ? 'text-emerald-600' : 'text-slate-400'} />
                  <span className="text-sm font-medium text-slate-800 flex-1 truncate">{t.user_name || t.branch_name || 'Terminal'}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${t.is_online ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                    {t.is_online ? 'Online' : 'Offline'}
                  </span>
                </button>
              ))}
            </div>
            <button onClick={handleSend} disabled={sending || !selected}
              className="w-full py-3 rounded-2xl bg-emerald-700 text-white font-semibold text-sm disabled:opacity-50 active:scale-95 transition-all flex items-center justify-center gap-2"
              data-testid="terminal-cloud-print-send">
              {sending ? <Loader2 size={14} className="animate-spin" /> : <Printer size={14} />}
              {sending ? 'Sending...' : 'Send to Print'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Transform basic doc data (from /api/doc/view/:code) into PrintEngine-compatible format
function basicDocToPrintData(basic) {
  if (!basic) return {};
  if (basic.doc_type === 'invoice') {
    return {
      invoice_number: basic.number,
      customer_name: basic.customer_name,
      order_date: basic.order_date || basic.date,
      created_at: basic.date,
      items: (basic.items || []).map(i => ({
        product_name: i.name, quantity: i.qty, rate: i.price, total: i.total, discount_amount: 0,
      })),
      subtotal: basic.subtotal,
      overall_discount: basic.discount || 0,
      grand_total: basic.grand_total,
      amount_paid: basic.amount_paid,
      balance: basic.balance,
      payment_method: basic.payment_method,
      payment_type: basic.payment_type,
    };
  }
  if (basic.doc_type === 'purchase_order') {
    return {
      po_number: basic.number,
      purchase_date: basic.date,
      vendor: basic.supplier_name,
      status: basic.raw_status || basic.status,
      items: (basic.items || []).map(i => ({
        product_name: i.name, quantity: i.qty, unit_price: i.price, total: i.total,
      })),
      subtotal: basic.grand_total,
      grand_total: basic.grand_total,
      payment_status: basic.payment_status,
    };
  }
  if (basic.doc_type === 'branch_transfer') {
    return {
      order_number: basic.number,
      created_at: basic.date,
      from_branch_name: basic.from_branch,
      to_branch_name: basic.to_branch,
      status: basic.raw_status || basic.status,
      items: (basic.items || []).map(i => ({
        product_name: i.name, qty: i.qty, transfer_capital: i.price, branch_retail: 0,
      })),
    };
  }
  return basic;
}

// Format relative time: "Just now", "2 min ago", "1 hr ago", etc.
function formatRelativeTime(isoString) {
  if (!isoString) return 'Never';
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 0) return 'Just now';
  const secs = Math.floor(diff / 1000);
  if (secs < 30) return 'Just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}


const TABS = [
  { key: 'sales', label: 'Sales', icon: ShoppingCart, color: 'text-emerald-600 bg-emerald-50' },
  { key: 'po', label: 'PO Check', icon: ClipboardCheck, color: 'text-amber-600 bg-amber-50' },
  { key: 'transfers', label: 'Transfers', icon: ArrowLeftRight, color: 'text-blue-600 bg-blue-50' },
];

export default function TerminalShell({ session, onLogout, onSessionUpdate }) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('sales');
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [syncing, setSyncing] = useState(false);
  const [dataReady, setDataReady] = useState(false);
  const [docCodeInput, setDocCodeInput] = useState('');
  const [showDocSearch, setShowDocSearch] = useState(false);
  const [docSearchResults, setDocSearchResults] = useState([]);
  const [docSearchLoading, setDocSearchLoading] = useState(false);
  // Quick scan sheet — shown when hardware scanner reads a doc QR code
  const [quickScanDoc, setQuickScanDoc] = useState(null);  // { basic, code, loading }
  const [businessInfo, setBusinessInfo] = useState({});
  const [pendingCount, setPendingCount] = useState(0);
  const [syncProgress, setSyncProgress] = useState('');
  const [backgroundSyncStatus, setBackgroundSyncStatus] = useState(''); // '', 'syncing', 'done', 'error'
  const [syncVersion, setSyncVersion] = useState(0); // Incremented after background sync to trigger TerminalSales re-read
  const [lastSyncDisplay, setLastSyncDisplay] = useState(''); // "2 min ago", "Just now", etc.
  const [notifications, setNotifications] = useState([]);
  const wsRef = useRef(null);
  const poRefreshRef = useRef(null); // callback to refresh PO list
  const transferRefreshRef = useRef(null);
  // Global hardware scanner buffer (for H10P Newland HID keyboard wedge)
  const globalScanBufferRef = useRef('');
  const globalScanTimerRef = useRef(null);
  // QR Camera Scanner state
  const [qrScannerOpen, setQrScannerOpen] = useState(false);
  const qrScannerRef = useRef(null);
  const qrLastScanRef = useRef({ code: '', time: 0 });
  const QR_SCAN_COOLDOWN = 3000;

  // Token ref — always holds the latest token for axios interceptor
  const tokenRef = useRef(session.token);

  // Document Upload overlay state
  const [docUploadOpen, setDocUploadOpen] = useState(false);

  // Print Terminal state
  const [printMode, setPrintMode] = useState('manual'); // 'auto' | 'manual'
  const [printQueue, setPrintQueue] = useState([]); // pending print jobs in manual mode
  const [printQueueOpen, setPrintQueueOpen] = useState(false);
  const [quickScanCloudPrint, setQuickScanCloudPrint] = useState(false); // cloud print for scanned doc

  // Android back button / browser back navigation handler
  const backExitRef = useRef(false);
  const backExitTimerRef = useRef(null);

  useEffect(() => {
    // Push initial history entry so first back doesn't exit
    window.history.pushState({ terminal: true }, '');

    const handlePopState = () => {
      // Priority 1: Close any open overlay/modal
      if (qrScannerOpen) {
        setQrScannerOpen(false);
        window.history.pushState({ terminal: true }, '');
        return;
      }
      if (docUploadOpen) {
        setDocUploadOpen(false);
        window.history.pushState({ terminal: true }, '');
        return;
      }
      if (settingsOpen) {
        setSettingsOpen(false);
        window.history.pushState({ terminal: true }, '');
        return;
      }
      if (quickScanDoc) {
        setQuickScanDoc(null);
        window.history.pushState({ terminal: true }, '');
        return;
      }
      if (showDocSearch) {
        setShowDocSearch(false);
        setDocCodeInput('');
        setDocSearchResults([]);
        window.history.pushState({ terminal: true }, '');
        return;
      }
      if (modeMenuOpen) {
        setModeMenuOpen(false);
        window.history.pushState({ terminal: true }, '');
        return;
      }

      // Priority 2: Go back to Sales tab if on another tab
      if (activeTab !== 'sales') {
        setActiveTab('sales');
        window.history.pushState({ terminal: true }, '');
        return;
      }

      // Priority 3: Double-tap to exit
      if (backExitRef.current) {
        // Second press within window — allow exit
        return;
      }
      // First press — show toast, block exit
      backExitRef.current = true;
      window.history.pushState({ terminal: true }, '');
      toast('Press back again to exit', { duration: 2000 });
      clearTimeout(backExitTimerRef.current);
      backExitTimerRef.current = setTimeout(() => { backExitRef.current = false; }, 2000);
    };

    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
      clearTimeout(backExitTimerRef.current);
    };
  }, [activeTab, qrScannerOpen, docUploadOpen, settingsOpen, quickScanDoc, showDocSearch, modeMenuOpen]);

  // Authenticated axios instance — uses tokenRef for latest token
  const [api] = useState(() => {
    const instance = axios.create({ baseURL: `${BACKEND_URL}/api` });
    instance.interceptors.request.use(config => {
      config.headers.Authorization = `Bearer ${tokenRef.current}`;
      if (config.method === 'get' && session.branchId) {
        config.params = { ...config.params, branch_id: session.branchId };
      }
      return config;
    });
    return instance;
  });

  // Token auto-refresh — refreshes every 12 hours to keep terminal connected indefinitely
  useEffect(() => {
    const REFRESH_INTERVAL = 12 * 60 * 60 * 1000; // 12 hours

    const refreshToken = async () => {
      if (!navigator.onLine) return;
      try {
        const res = await axios.post(`${BACKEND_URL}/api/terminal/refresh-token`, {}, {
          headers: { Authorization: `Bearer ${tokenRef.current}` }
        });
        const newToken = res.data.token;
        if (newToken) {
          tokenRef.current = newToken;
          if (onSessionUpdate) onSessionUpdate({ token: newToken });
        }
      } catch (err) {
        // If 401, token is fully expired — need to re-pair
        if (err?.response?.status === 401) {
          toast.error('Session expired. Please re-pair the terminal.');
          onLogout();
        }
      }
    };

    const interval = setInterval(refreshToken, REFRESH_INTERVAL);
    // Also refresh on first load if terminal has been idle
    refreshToken();

    return () => clearInterval(interval);
  }, [onLogout, onSessionUpdate]);

  // ── Smart scan helpers ────────────────────────────────────────────────────
  // Extract 8-char doc code from various input formats (URL, deeplink, raw code)
  const extractDocCode = (input) => {
    const t = input.trim();
    // Full URL containing /doc/CODE
    const urlMatch = t.match(/\/doc\/([A-Z0-9]{6,10})(?:[?/#]|$)/i);
    if (urlMatch) return urlMatch[1].toUpperCase();
    // agrismart:// deep link
    if (t.toLowerCase().startsWith('agrismart://doc/')) {
      const code = t.split('agrismart://doc/')[1]?.split(/[?/#]/)[0].toUpperCase();
      if (/^[A-Z0-9]{6,10}$/.test(code)) return code;
    }
    // Raw doc code: 8 uppercase alphanumeric, not all-digits (barcode is all-digits)
    const upper = t.toUpperCase();
    if (/^[A-Z0-9]{8}$/.test(upper) && !/^\d+$/.test(upper)) return upper;
    return null;
  };

  // Detect invoice/PO/transfer number patterns (e.g. KS-001, PO-2025-001)
  const looksLikeDocNumber = (input) => /^[A-Z]{1,5}[-/]\d/i.test(input.trim());

  // Search backend by document number
  const performDocSearch = useCallback(async (query) => {
    if (!query || query.length < 2) { setDocSearchResults([]); return; }
    setDocSearchLoading(true);
    try {
      const res = await api.get('/doc/search', { params: { q: query, branch_id: session.branchId } });
      setDocSearchResults(res.data.results || []);
    } catch { setDocSearchResults([]); }
    setDocSearchLoading(false);
  }, [api, session.branchId]);

  // Route any scanned/typed input to the right action
  const handleSmartInput = useCallback(async (scanned) => {
    const docCode = extractDocCode(scanned);
    if (docCode) {
      // Stop QR camera scanner if it's running
      if (qrScannerRef.current) {
        try { await qrScannerRef.current.stop(); } catch {}
        qrScannerRef.current = null;
        setQrScannerOpen(false);
      }
      // Show QuickScan sheet — fetch basic doc info and offer Reprint or View options
      setQuickScanDoc({ code: docCode, basic: null, loading: true });
      try {
        const res = await axios.get(`${BACKEND_URL}/api/doc/view/${docCode}`, {
          headers: { Authorization: `Bearer ${session.token}` },
        });
        setQuickScanDoc({ code: docCode, basic: res.data, loading: false });
      } catch {
        setQuickScanDoc(null);
        navigate(`/doc/${docCode}?branch=${session.branchId}&device=${session.deviceId || ''}`);
      }
      return;
    }
    if (looksLikeDocNumber(scanned)) {
      setDocCodeInput(scanned);
      setShowDocSearch(true);
      performDocSearch(scanned);
      return;
    }
    // Falls through — product barcode handled by TerminalSales keyboard listener
  }, [navigate, session.branchId, session.token, session.deviceId, performDocSearch]); // eslint-disable-line

  // ── QR Camera Scanner (for scanning document QR codes) ──────────────────
  const startQrScanner = useCallback(async () => {
    setQrScannerOpen(true);
    setModeMenuOpen(false);
    await new Promise(r => setTimeout(r, 350));
    try {
      const { Html5Qrcode } = await import('html5-qrcode');
      const scanner = new Html5Qrcode('terminal-qr-scanner-view');
      qrScannerRef.current = scanner;
      await scanner.start(
        { facingMode: 'environment' },
        { fps: 10 },
        (decodedText) => {
          const now = Date.now();
          if (decodedText === qrLastScanRef.current.code && now - qrLastScanRef.current.time < QR_SCAN_COOLDOWN) return;
          qrLastScanRef.current = { code: decodedText, time: now };
          if (navigator.vibrate) navigator.vibrate(150);
          handleSmartInput(decodedText);
        },
        () => {}
      );
    } catch (e) {
      console.error('QR Scanner error:', e);
      toast.error('Camera access denied or unavailable');
      setQrScannerOpen(false);
    }
  }, [handleSmartInput, QR_SCAN_COOLDOWN]);

  const stopQrScanner = useCallback(async () => {
    if (qrScannerRef.current) {
      try { await qrScannerRef.current.stop(); } catch {}
      qrScannerRef.current = null;
    }
    setQrScannerOpen(false);
  }, []);

  // Cleanup QR scanner on unmount
  useEffect(() => {
    return () => {
      if (qrScannerRef.current) {
        try { qrScannerRef.current.stop(); } catch {}
        qrScannerRef.current = null;
      }
    };
  }, []);

  // Global keyboard scanner — intercepts H10P HID hardware scanner output in any tab
  useEffect(() => {
    const handleGlobalKey = (e) => {
      // Only intercept when NOT typing in an input/textarea
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
      if (e.key === 'Enter') {
        const scanned = globalScanBufferRef.current.trim();
        globalScanBufferRef.current = '';
        clearTimeout(globalScanTimerRef.current);
        if (scanned.length >= 3) handleSmartInput(scanned);
        return;
      }
      if (e.key.length === 1) {
        globalScanBufferRef.current += e.key;
        clearTimeout(globalScanTimerRef.current);
        // Reset buffer after 200ms — scanner fires much faster than human typing
        globalScanTimerRef.current = setTimeout(() => { globalScanBufferRef.current = ''; }, 200);
      }
    };
    window.addEventListener('keydown', handleGlobalKey);
    return () => { window.removeEventListener('keydown', handleGlobalKey); clearTimeout(globalScanTimerRef.current); };
  }, [handleSmartInput]);

  // Online/offline detection — Phase 4: robust connectivity check
  // navigator.onLine alone false-positives "offline" (captive portals,
  // stale browser state). We additionally ping /api/health every 30s.
  // Only flip to offline if BOTH navigator says offline AND ping fails,
  // OR ping fails 3 times in a row while navigator claims online.
  useEffect(() => {
    let pingFailures = 0;
    let timer = null;
    const probe = async () => {
      // Browser thinks we're offline — trust it (saves bandwidth)
      if (!navigator.onLine) {
        setIsOnline(false);
        pingFailures = 0;
        return;
      }
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 4000);
        const res = await fetch(`${BACKEND_URL}/api/health`, {
          method: 'GET', signal: ctrl.signal, cache: 'no-store',
        });
        clearTimeout(t);
        if (res.ok) {
          if (pingFailures > 0) {
            // Recovered
            setIsOnline(true);
          } else {
            // Make sure state is online (handles initial mount)
            setIsOnline((prev) => prev ? prev : true);
          }
          pingFailures = 0;
        } else {
          throw new Error('non-ok');
        }
      } catch {
        pingFailures += 1;
        // Browser claims online but backend unreachable: be patient — only
        // flip after 3 consecutive failures (90s window) to avoid the
        // "false offline" toast the user hit before.
        if (pingFailures >= 3) {
          setIsOnline(false);
        }
      }
    };
    const goOnline = () => { pingFailures = 0; setIsOnline(true); toast.success('Back online'); };
    const goOffline = () => { setIsOnline(false); toast('Working offline', { duration: 3000 }); };
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    // Run a probe shortly after mount + every 30s
    const initial = setTimeout(probe, 2000);
    timer = setInterval(probe, 30000);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
      clearTimeout(initial);
      if (timer) clearInterval(timer);
    };
  }, []);

  // WebSocket connection for real-time events
  useEffect(() => {
    if (!session.terminalId) return;

    const connectWS = () => {
      try {
        const ws = new WebSocket(`${WS_URL}/api/terminal/ws/terminal/${session.terminalId}`);
        wsRef.current = ws;

        ws.onmessage = (event) => {
          const msg = JSON.parse(event.data);
          switch (msg.type) {
            case 'po_assigned':
              toast.success(`New PO: ${msg.data.po_number || 'PO'} from ${msg.data.vendor || 'vendor'}`, { duration: 5000 });
              setNotifications(prev => [...prev, { type: 'po', ...msg.data, time: Date.now() }]);
              // Auto-refresh PO list
              if (poRefreshRef.current) poRefreshRef.current();
              break;
            case 'transfer_assigned':
              toast.success(`New Transfer: ${msg.data.transfer_number || 'Transfer'}`, { duration: 5000 });
              setNotifications(prev => [...prev, { type: 'transfer', ...msg.data, time: Date.now() }]);
              if (transferRefreshRef.current) transferRefreshRef.current();
              break;
            case 'print_job':
              handleIncomingPrintJob(msg.data);
              break;
            case 'print_mode_changed':
              setPrintMode(msg.data.mode || 'manual');
              toast(`Print mode: ${msg.data.mode === 'auto' ? 'Auto Print' : 'Manual'}`, { duration: 2000 });
              break;
            default:
              break;
          }
        };

        ws.onclose = () => {
          wsRef.current = null;
          // Reconnect after 3 seconds
          setTimeout(() => { if (navigator.onLine) connectWS(); }, 3000);
        };

        ws.onerror = () => { ws.close(); };
      } catch { /* WebSocket not available */ }
    };

    if (navigator.onLine) connectWS();

    return () => { if (wsRef.current) { wsRef.current.close(); wsRef.current = null; } };
  }, [session.terminalId]);

  // ── Background delta sync (non-blocking) ─────────────────────────────────
  const backgroundSync = useCallback(async (isFullSync = false) => {
    if (!navigator.onLine) return;
    setBackgroundSyncStatus('syncing');
    try {
      const params = { branch_id: session.branchId };
      // Delta: pass last_sync timestamp unless forcing full sync
      if (!isFullSync) {
        const lastSync = await getLastSyncTime();
        if (lastSync) params.last_sync = lastSync;
      }

      const [posRes, custRes, schemeRes] = await Promise.all([
        api.get('/sync/pos-data', { params }),
        api.get('/customers', { params: { limit: 500, branch_id: session.branchId } }),
        api.get('/price-schemes'),
      ]);

      const isDelta = posRes.data.is_delta;
      const products = posRes.data.products || [];
      const customers = custRes.data.customers || posRes.data.customers || [];
      const inventory = posRes.data.inventory || [];
      const branchPrices = posRes.data.branch_prices || [];
      const deletedIds = posRes.data.deleted_ids || [];

      if (isDelta && products.length > 0) {
        // Delta merge — only upsert changed records
        await mergeProducts(products, deletedIds);
      } else if (products.length > 0) {
        // Full replace
        await cacheProducts(products);
      }

      // Always full-replace these (lightweight collections)
      if (customers.length > 0) await cacheCustomers(customers);
      await cachePriceSchemes(schemeRes.data || posRes.data.price_schemes || []);

      if (inventory.length) {
        await cacheInventory(
          inventory.map(item => ({
            product_id: item.product_id, quantity: item.quantity ?? 0,
            branch_id: item.branch_id, updated_at: item.updated_at || new Date().toISOString(),
          }))
        );
      }
      if (branchPrices.length) {
        await cacheBranchPrices(
          branchPrices.map(bp => ({
            product_id: bp.product_id, prices: bp.prices || {},
            cost_price: bp.cost_price ?? null, branch_id: bp.branch_id,
          }))
        );
      }

      // Save sync timestamp
      await setLastSyncTime(posRes.data.sync_time || new Date().toISOString());
      // Phase 2: cache admin_pin hash for offline manager bypass
      if (posRes.data.admin_pin_hash) {
        await setOfflineAdminPinHash(posRes.data.admin_pin_hash);
      }
      if (Array.isArray(posRes.data.offline_pin_grants)) {
        await setOfflinePinGrants(posRes.data.offline_pin_grants);
      }

      // Sync pending offline sales
      const count = await getPendingSaleCount();
      setPendingCount(count);
      if (count > 0) {
        await syncPendingSales();
        setPendingCount(await getPendingSaleCount());
      }

      // Fetch business info for receipt printing
      api.get('/settings/business-info').then(r => setBusinessInfo(r.data || {})).catch(() => {});

      setBackgroundSyncStatus('done');
      // Trigger TerminalSales to re-read cache
      setSyncVersion(v => v + 1);
      // Show subtle toast only when delta brought actual changes
      if (isDelta && products.length > 0) {
        toast.success(`${products.length} product(s) updated`, { duration: 2000 });
      }
      // Clear "done" indicator after 3s
      setTimeout(() => setBackgroundSyncStatus(''), 3000);
    } catch (e) {
      console.error('Background sync failed:', e);
      setBackgroundSyncStatus('error');
      setTimeout(() => setBackgroundSyncStatus(''), 5000);
    }
  }, [api, session.branchId]);

  // ── Initial data load — Instant from cache, background sync ─────────────
  const loadData = useCallback(async () => {
    if (session.organizationId) setOfflineOrg(session.organizationId);

    // Step 1: Check if we have cached data in IndexedDB
    const cachedCount = await getProductCount();

    if (cachedCount > 0) {
      // ✅ INSTANT LOAD — show terminal immediately from cache
      setDataReady(true);
      setSyncing(false);
      // Kick off background delta sync (non-blocking)
      backgroundSync(false);
    } else {
      // First time — must do full download (no cache yet)
      setSyncing(true);
      setSyncProgress('Connecting...');

      if (navigator.onLine) {
        try {
          setSyncProgress('Downloading products...');
          const params = { branch_id: session.branchId };
          const [posRes, custRes, schemeRes] = await Promise.all([
            api.get('/sync/pos-data', { params }),
            api.get('/customers', { params: { limit: 500, ...params } }),
            api.get('/price-schemes'),
          ]);

          setSyncProgress('Saving to local storage...');
          await Promise.all([
            cacheProducts(posRes.data.products || []),
            cacheCustomers(custRes.data.customers || posRes.data.customers || []),
            cachePriceSchemes(schemeRes.data || posRes.data.price_schemes || []),
            posRes.data.inventory?.length ? cacheInventory(
              posRes.data.inventory.map(item => ({
                product_id: item.product_id, quantity: item.quantity ?? 0,
                branch_id: item.branch_id, updated_at: item.updated_at || new Date().toISOString(),
              }))
            ) : Promise.resolve(),
            posRes.data.branch_prices?.length ? cacheBranchPrices(
              posRes.data.branch_prices.map(bp => ({
                product_id: bp.product_id, prices: bp.prices || {},
                cost_price: bp.cost_price ?? null, branch_id: bp.branch_id,
              }))
            ) : Promise.resolve(),
          ]);

          await setLastSyncTime(posRes.data.sync_time || new Date().toISOString());
          // Phase 2: cache admin_pin hash for offline manager bypass
          if (posRes.data.admin_pin_hash) {
            await setOfflineAdminPinHash(posRes.data.admin_pin_hash);
          }
          if (Array.isArray(posRes.data.offline_pin_grants)) {
            await setOfflinePinGrants(posRes.data.offline_pin_grants);
          }

          const count = await getPendingSaleCount();
          setPendingCount(count);
          if (count > 0) {
            setSyncProgress(`Syncing ${count} pending sale(s)...`);
            await syncPendingSales();
            setPendingCount(await getPendingSaleCount());
          }

          setSyncProgress('');
          setDataReady(true);
          setSyncVersion(v => v + 1); // Update last-synced display
          toast.success(`Data synced — ${posRes.data.products?.length || 0} products loaded`);
          api.get('/settings/business-info').then(r => setBusinessInfo(r.data || {})).catch(() => {});
        } catch (e) {
          console.error('Sync failed:', e);
          await loadOfflineData();
        }
      } else {
        await loadOfflineData();
      }
      setSyncing(false);
    }
  }, [api, session.branchId, session.organizationId, backgroundSync]);

  const loadOfflineData = async () => {
    const prods = await getProducts();
    if (prods.length > 0) {
      setDataReady(true);
      toast('Loaded from offline cache', { duration: 3000 });
    } else {
      toast.error('No cached data — connect to internet first');
    }
    setSyncProgress('');
  };

  useEffect(() => { loadData(); }, [loadData]);

  // Bind this device's ID to the terminal session (handles code-based pairing
  // where device_id wasn't available at pairing time; idempotent, fire-and-forget)
  useEffect(() => {
    if (!session.terminalId) return;
    DeviceIdentity.getDeviceId()
      .then(({ deviceId }) => {
        if (!deviceId) return;
        // Update local session with deviceId if not already set
        if (!session.deviceId && onSessionUpdate) onSessionUpdate({ deviceId });
        // Bind to backend session
        axios.post(`${BACKEND_URL}/api/terminal/bind-device`, {
          terminal_id: session.terminalId,
          device_id: deviceId,
        }, { headers: { Authorization: `Bearer ${tokenRef.current}` } }).catch(() => {}); // fire-and-forget
      })
      .catch(() => {});
  }, [session.terminalId]); // eslint-disable-line

  // Auto sync
  useEffect(() => {
    startAutoSync(() => session.branchId);
    return () => stopAutoSync();
  }, [session.branchId]);

  // "Last Synced: X min ago" ticker — updates display every 30s
  useEffect(() => {
    let mounted = true;
    const updateDisplay = async () => {
      const ts = await getLastSyncTime();
      if (mounted) setLastSyncDisplay(formatRelativeTime(ts));
    };
    updateDisplay();
    const timer = setInterval(updateDisplay, 30000);
    return () => { mounted = false; clearInterval(timer); };
  }, [syncVersion]); // re-run when sync completes

  // Expose __triggerBackgroundSync for Android Capacitor onResume hook
  useEffect(() => {
    window.__triggerBackgroundSync = () => backgroundSync(false);
    return () => { delete window.__triggerBackgroundSync; };
  }, [backgroundSync]);

  const handleManualSync = async () => {
    setSyncing(true);
    await backgroundSync(true); // Force full sync on manual trigger
    setSyncing(false);
  };

  const handleLogout = () => {
    if (wsRef.current) wsRef.current.close();
    localStorage.removeItem('agrismart_terminal');
    onLogout();
  };

  // ── Print Job Handler ─────────────────────────────────────────────────────
  const markJobStatus = async (jobId, status, errorMessage = '') => {
    try {
      await axios.put(`${BACKEND_URL}/api/print/jobs/${jobId}/status`, { status, error_message: errorMessage }, {
        headers: { Authorization: `Bearer ${tokenRef.current}` }
      });
    } catch { /* fire and forget */ }
  };

  const handleIncomingPrintJob = (jobData) => {
    if (!jobData?.job_id) return;

    if (printMode === 'auto' || jobData.priority === 'urgent') {
      // Auto mode: trigger print immediately
      toast.success(`Printing: ${jobData.document_name || 'Document'}`, { duration: 3000 });
      triggerPrint(jobData);
    } else {
      // Manual mode: add to queue
      setPrintQueue(prev => {
        // Avoid duplicates
        if (prev.find(j => j.job_id === jobData.job_id)) return prev;
        return [jobData, ...prev];
      });
      setPrintQueueOpen(true);
      toast(`New print job: ${jobData.document_name || 'Document'}`, {
        duration: 5000,
        action: { label: 'Print Now', onClick: () => { triggerPrint(jobData); } }
      });
    }
  };

  const triggerPrint = (jobData) => {
    if (!jobData?.html_content) {
      markJobStatus(jobData.job_id, 'failed', 'No HTML content');
      return;
    }
    try {
      const win = window.open('', '_blank', 'width=900,height=700');
      if (!win) {
        toast.error('Please allow popups to print');
        markJobStatus(jobData.job_id, 'failed', 'Popup blocked');
        return;
      }
      // Inject auto-print script into the HTML
      const htmlWithPrint = jobData.html_content.replace(
        '</body>',
        `<script>(function(){var p=false;function go(){if(p)return;p=true;window.print();setTimeout(function(){window.close();},2000);}var imgs=document.images;if(!imgs.length){go();return;}var r=imgs.length;for(var i=0;i<imgs.length;i++){if(imgs[i].complete){r--;if(!r)go();}else{imgs[i].onload=imgs[i].onerror=function(){r--;if(!r)go();};}}setTimeout(go,5000);})()</script></body>`
      );
      win.document.write(htmlWithPrint);
      win.document.close();
      win.focus();
      // Mark as printed after a delay
      setTimeout(() => markJobStatus(jobData.job_id, 'printed'), 4000);
      // Remove from queue
      setPrintQueue(prev => prev.filter(j => j.job_id !== jobData.job_id));
    } catch (err) {
      markJobStatus(jobData.job_id, 'failed', err.message || 'Print error');
    }
  };

  // Load print mode from session on startup
  useEffect(() => {
    if (!session.token) return;
    axios.get(`${BACKEND_URL}/api/print/terminal/session`, {
      headers: { Authorization: `Bearer ${session.token}` }
    }).then(res => {
      if (res.data?.print_mode) setPrintMode(res.data.print_mode);
    }).catch(() => {});
  }, [session.token]);

  // Notification badge count (unread)
  const unreadCount = notifications.filter(n => Date.now() - n.time < 60000).length;

  if (!dataReady) {
    return (
      <div className="min-h-screen bg-slate-900 flex flex-col items-center justify-center text-white" data-testid="terminal-loading">
        <RefreshCw className="w-10 h-10 animate-spin text-emerald-400 mb-4" />
        <p className="text-slate-300 text-sm">{syncProgress || 'Preparing terminal...'}</p>
        <p className="text-slate-500 text-xs mt-2">{session.branchName}</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F5F5F0] flex flex-col" data-testid="terminal-shell">
      {/* Top bar */}
      <div className="bg-white border-b border-slate-200 px-3 py-2 flex items-center justify-between safe-area-top">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-[#1A4D2E]" style={{ fontFamily: 'Manrope' }}>AgriSmart</span>
          <span className="text-xs text-slate-500 border-l border-slate-200 pl-2">{session.branchName}</span>
          {/* Background sync indicator */}
          {backgroundSyncStatus === 'syncing' && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600" data-testid="sync-indicator-syncing">
              <RefreshCw size={10} className="animate-spin" /> Syncing
            </span>
          )}
          {backgroundSyncStatus === 'done' && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-600" data-testid="sync-indicator-done">
              <Check size={10} /> Up to date
            </span>
          )}
          {backgroundSyncStatus === 'error' && (
            <span className="flex items-center gap-1 text-[10px] text-red-500" data-testid="sync-indicator-error">
              <WifiOff size={10} /> Sync failed
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* Smart Doc Search — accepts 8-char doc codes, invoice numbers, PO numbers */}
          {showDocSearch ? (
            <div className="relative">
              <div className="flex items-center gap-1">
                <input
                  autoFocus
                  type="text"
                  value={docCodeInput}
                  onChange={e => {
                    const v = e.target.value.toUpperCase();
                    setDocCodeInput(v);
                    // Auto-navigate if it looks like a raw 8-char doc code
                    const code = v.trim();
                    if (/^[A-Z0-9]{8}$/.test(code) && !/^\d+$/.test(code)) {
                      navigate(`/doc/${code}?branch=${session.branchId}&device=${session.deviceId || ''}`);
                      setShowDocSearch(false); setDocCodeInput(''); setDocSearchResults([]);
                      return;
                    }
                    performDocSearch(v);
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      const v = docCodeInput.trim();
                      if (v.length >= 2) { handleSmartInput(v); setShowDocSearch(false); setDocCodeInput(''); setDocSearchResults([]); }
                    }
                    if (e.key === 'Escape') { setShowDocSearch(false); setDocCodeInput(''); setDocSearchResults([]); }
                  }}
                  placeholder="Code or invoice #..."
                  maxLength={20}
                  className="h-7 w-36 text-center font-mono text-sm rounded-lg border border-slate-200 bg-white px-2 uppercase tracking-widest"
                  data-testid="terminal-doc-code-input"
                />
                {docSearchLoading && <Loader2 size={12} className="animate-spin text-slate-400" />}
                <button onClick={() => { setShowDocSearch(false); setDocCodeInput(''); setDocSearchResults([]); }} className="h-7 px-1.5 rounded-lg border border-slate-200 text-slate-400 text-xs">
                  <X size={12} />
                </button>
              </div>
              {/* Search results dropdown */}
              {docSearchResults.length > 0 && (
                <div className="absolute top-8 left-0 right-0 bg-white border border-slate-200 rounded-xl shadow-xl z-50 overflow-hidden min-w-[240px]" data-testid="doc-search-results">
                  {docSearchResults.map((r, i) => (
                    <button key={i} onClick={() => { navigate(`/doc/${r.doc_code}?branch=${session.branchId}&device=${session.deviceId || ''}`); setShowDocSearch(false); setDocCodeInput(''); setDocSearchResults([]); }}
                      className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-emerald-50 border-b border-slate-50 last:border-0"
                      data-testid={`doc-search-result-${r.doc_code}`}
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-bold text-slate-800 truncate">{r.number}</p>
                        <p className="text-[10px] text-slate-400 truncate">{r.label}</p>
                      </div>
                      <div className="text-right ml-2 shrink-0">
                        <p className="text-[10px] font-mono text-emerald-700">{r.doc_code}</p>
                        <p className="text-[9px] text-slate-400 capitalize">{r.doc_type.replace('_', ' ')}</p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <button onClick={() => setShowDocSearch(true)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" title="Find by doc code or invoice number" data-testid="terminal-find-code-btn">
              <Search size={14} />
            </button>
          )}
          {pendingCount > 0 && (
            <span className="bg-amber-100 text-amber-700 text-[10px] font-medium px-2 py-0.5 rounded-full" data-testid="pending-badge">
              {pendingCount} pending
            </span>
          )}
          <span className={`flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full ${isOnline ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-600'}`} data-testid="online-status">
            {isOnline ? <Wifi size={10} /> : <WifiOff size={10} />}
            {isOnline ? 'Online' : 'Offline'}
          </span>
          <button onClick={handleManualSync} disabled={syncing} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500" data-testid="sync-btn">
            <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'sales' && (
          <TerminalSales api={api} session={session} isOnline={isOnline} pendingCount={pendingCount} setPendingCount={setPendingCount} syncVersion={syncVersion} />
        )}
        {activeTab === 'po' && (
          <TerminalPOCheck api={api} session={session} isOnline={isOnline} onRefreshRef={poRefreshRef} />
        )}
        {activeTab === 'transfers' && (
          <TerminalTransfers api={api} session={session} isOnline={isOnline} onRefreshRef={transferRefreshRef} />
        )}
      </div>

      {/* Floating Mode Selector — lower left */}
      <div className="fixed bottom-4 left-4 z-50 safe-area-bottom" data-testid="mode-selector">
        {/* Mode menu popup */}
        {modeMenuOpen && (
          <div className="absolute bottom-14 left-0 bg-white rounded-2xl border border-slate-200 overflow-hidden w-52" style={{boxShadow:'0 4px 16px rgba(0,0,0,0.12)'}}>
            {TABS.map(tab => {
              const Icon = tab.icon;
              const active = activeTab === tab.key;
              const hasBadge = (tab.key === 'po' && notifications.some(n => n.type === 'po' && Date.now() - n.time < 60000)) ||
                               (tab.key === 'transfers' && notifications.some(n => n.type === 'transfer' && Date.now() - n.time < 60000));
              return (
                <button key={tab.key}
                  onClick={() => { setActiveTab(tab.key); setModeMenuOpen(false); setNotifications(prev => prev.filter(n => n.type !== (tab.key === 'po' ? 'po' : 'transfer'))); }}
                  className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors ${
                    active ? 'bg-[#1A4D2E] text-white' : 'hover:bg-slate-50 text-slate-700'
                  }`}
                  data-testid={`mode-${tab.key}`}
                >
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${active ? 'bg-white/20' : tab.color}`}>
                    <Icon size={16} />
                  </div>
                  <span className="text-sm font-medium flex-1">{tab.label}</span>
                  {hasBadge && <span className="w-2.5 h-2.5 bg-red-500 rounded-full" />}
                  {active && <ChevronRight size={14} className="opacity-60" />}
                </button>
              );
            })}
            <div className="border-t border-slate-100">
              <button onClick={startQrScanner}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 text-slate-700"
                data-testid="mode-scan-qr">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-purple-50 text-purple-600">
                  <ScanLine size={16} />
                </div>
                <span className="text-sm font-medium">Scan QR</span>
              </button>
              <button onClick={() => { setModeMenuOpen(false); setDocUploadOpen(true); }}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 text-slate-700"
                data-testid="mode-upload-doc">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-teal-50 text-teal-600">
                  <FolderUp size={16} />
                </div>
                <span className="text-sm font-medium">Upload Doc</span>
              </button>
              <button onClick={() => { setModeMenuOpen(false); setPrintQueueOpen(true); }}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 text-slate-700"
                data-testid="mode-print-queue">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-emerald-50 text-emerald-600 relative">
                  <Printer size={16} />
                  {printQueue.length > 0 && (
                    <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full text-white text-[9px] flex items-center justify-center font-bold">
                      {printQueue.length}
                    </span>
                  )}
                </div>
                <span className="text-sm font-medium flex-1">Print Queue</span>
                {printQueue.length > 0 && (
                  <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
                    {printQueue.length}
                  </span>
                )}
              </button>
            </div>
            <div className="border-t border-slate-100">
              <button onClick={() => { setModeMenuOpen(false); setSettingsOpen(true); }}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 text-slate-500"
                data-testid="terminal-settings-btn">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-slate-100">
                  <Settings size={16} />
                </div>
                <span className="text-sm font-medium">Settings</span>
              </button>
            </div>
          </div>
        )}

        {/* Floating button */}
        <button
          onClick={() => setModeMenuOpen(v => !v)}
          className={`w-12 h-12 rounded-full shadow-lg flex items-center justify-center transition-all ${
            modeMenuOpen ? 'bg-slate-800 text-white rotate-90' : 'bg-[#1A4D2E] text-white hover:bg-[#14532d]'
          }`}
          data-testid="mode-toggle-btn"
        >
          {(() => {
            const CurrentIcon = TABS.find(t => t.key === activeTab)?.icon || ShoppingCart;
            return modeMenuOpen ? <ChevronRight size={20} /> : <CurrentIcon size={20} />;
          })()}
        </button>

        {/* Notification dot */}
        {notifications.length > 0 && !modeMenuOpen && (
          <span className="absolute -top-1 -right-1 w-3.5 h-3.5 bg-red-500 rounded-full border-2 border-[#F5F5F0]" />
        )}
      </div>

      {/* Click-away backdrop when menu open */}
      {modeMenuOpen && (
        <div className="fixed inset-0 z-40" onClick={() => setModeMenuOpen(false)} />
      )}

      {/* Settings Panel */}
      {settingsOpen && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onClick={() => setSettingsOpen(false)}>
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-slate-100">
              <h3 className="text-base font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Terminal Settings</h3>
              <p className="text-xs text-slate-400 mt-0.5">{session.branchName}</p>
            </div>
            <div className="p-4 space-y-3">
              <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                <div>
                  <p className="text-xs text-slate-500 font-medium">Branch</p>
                  <p className="text-sm font-semibold text-slate-800">{session.branchName}</p>
                </div>
                <span className="text-[10px] bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full">Linked</span>
              </div>
              <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                <div>
                  <p className="text-xs text-slate-500 font-medium">Paired by</p>
                  <p className="text-sm text-slate-700">{session.userName || 'Unknown'}</p>
                </div>
              </div>
              <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                <div>
                  <p className="text-xs text-slate-500 font-medium">Status</p>
                  <p className="text-sm text-slate-700 flex items-center gap-1.5">
                    {isOnline ? <Wifi size={12} className="text-emerald-600" /> : <WifiOff size={12} className="text-red-500" />}
                    {isOnline ? 'Online' : 'Offline'}
                  </p>
                </div>
                <button onClick={() => { setSettingsOpen(false); handleManualSync(); }}
                  className="text-xs text-blue-600 hover:underline">Sync now</button>
              </div>
              {/* Last Synced display */}
              <button
                onClick={() => { setSettingsOpen(false); handleManualSync(); }}
                className="w-full flex items-center justify-between p-3 bg-slate-50 rounded-xl hover:bg-slate-100 transition-colors text-left"
                data-testid="last-sync-row"
              >
                <div>
                  <p className="text-xs text-slate-500 font-medium">Last Synced</p>
                  <p className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
                    <RefreshCw size={12} className={`${backgroundSyncStatus === 'syncing' ? 'animate-spin text-amber-500' : 'text-slate-400'}`} />
                    {backgroundSyncStatus === 'syncing' ? 'Syncing now...' : lastSyncDisplay || 'Never'}
                  </p>
                </div>
                <span className="text-[10px] text-blue-600 font-medium">Tap to sync</span>
              </button>
            </div>
            <div className="p-4 border-t border-slate-100 space-y-2">
              {/* Print Mode Toggle */}
              <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                <div>
                  <p className="text-xs text-slate-500 font-medium">Print Mode</p>
                  <p className="text-sm font-semibold text-slate-800 capitalize">{printMode}</p>
                  <p className="text-[10px] text-slate-400 mt-0.5">
                    {printMode === 'auto' ? 'Prints immediately on arrival' : 'Manual — click Print Now'}
                  </p>
                </div>
                <button
                  onClick={async () => {
                    const newMode = printMode === 'auto' ? 'manual' : 'auto';
                    try {
                      const sessRes = await axios.get(`${BACKEND_URL}/api/print/terminal/session`, { headers: { Authorization: `Bearer ${tokenRef.current}` } });
                      await axios.post(`${BACKEND_URL}/api/print/terminal/set-mode`, { terminal_id: sessRes.data.terminal_id, mode: newMode }, { headers: { Authorization: `Bearer ${tokenRef.current}` } });
                      setPrintMode(newMode);
                      toast.success(`Print mode: ${newMode}`);
                    } catch { toast.error('Failed to change print mode'); }
                  }}
                  data-testid="toggle-print-mode-btn"
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${printMode === 'auto' ? 'bg-emerald-600 text-white border-emerald-700' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}`}
                >
                  <Printer size={12} className="inline mr-1" />
                  {printMode === 'auto' ? 'Auto' : 'Manual'}
                </button>
              </div>

              <button onClick={() => { setSettingsOpen(false); handleLogout(); }}
                className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-red-50 text-red-600 hover:bg-red-100 transition-colors text-sm font-medium"
                data-testid="unlink-terminal-btn">
                <Unlink size={16} />
                Unlink Terminal
              </button>              <button onClick={() => setSettingsOpen(false)}
                className="w-full py-2.5 text-sm text-slate-500 hover:text-slate-700 transition-colors">
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── QR Camera Scanner Overlay ── */}
      {qrScannerOpen && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-black" data-testid="qr-scanner-overlay">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 bg-black/80">
            <div className="flex items-center gap-2.5">
              <ScanLine size={18} className="text-purple-400" />
              <div>
                <p className="text-sm font-semibold text-white">Scan Document QR</p>
                <p className="text-[10px] text-slate-400">Point camera at receipt or document QR code</p>
              </div>
            </div>
            <button
              onClick={stopQrScanner}
              className="w-9 h-9 rounded-full bg-white/10 flex items-center justify-center text-white hover:bg-white/20 transition-colors"
              data-testid="qr-scanner-close"
            >
              <X size={18} />
            </button>
          </div>

          {/* Scanner view */}
          <div className="flex-1 relative overflow-hidden">
            <div id="terminal-qr-scanner-view" className="w-full h-full" />
          </div>
        </div>
      )}

      {/* ── QuickScan Sheet — shown when hardware scanner reads a doc QR code ── */}
      {quickScanDoc && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end" data-testid="quickscan-sheet">
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/40" onClick={() => setQuickScanDoc(null)} />
          {/* Sheet */}
          <div className="relative bg-white rounded-t-3xl overflow-hidden" style={{boxShadow:'0 -4px 24px rgba(0,0,0,0.13)'}}>
            {/* Handle bar */}
            <div className="flex justify-center pt-3 pb-1">
              <div className="w-10 h-1 rounded-full bg-slate-300" />
            </div>

            {quickScanDoc.loading ? (
              <div className="px-5 py-8 flex flex-col items-center gap-3">
                <RefreshCw size={24} className="animate-spin text-emerald-500" />
                <p className="text-sm text-slate-500">Loading document...</p>
                <p className="text-xs font-mono text-slate-400">{quickScanDoc.code}</p>
              </div>
            ) : quickScanDoc.basic ? (
              <div className="px-5 pb-6 space-y-4">
                {/* Doc header */}
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider font-semibold text-slate-400">
                      {quickScanDoc.basic.doc_type === 'invoice' ? 'Sales Receipt'
                        : quickScanDoc.basic.doc_type === 'purchase_order' ? 'Purchase Order'
                        : 'Branch Transfer'}
                    </p>
                    <p className="text-xl font-bold text-slate-900 mt-0.5" data-testid="quickscan-doc-number">
                      {quickScanDoc.basic.number}
                    </p>
                    <p className="text-sm text-slate-500 mt-0.5">
                      {quickScanDoc.basic.customer_name || quickScanDoc.basic.supplier_name
                        || `${quickScanDoc.basic.from_branch} → ${quickScanDoc.basic.to_branch}`}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-xl font-bold font-mono text-[#1A4D2E]" data-testid="quickscan-doc-amount">
                      {fmtPHP(quickScanDoc.basic.grand_total || quickScanDoc.basic.total || 0)}
                    </p>
                    {quickScanDoc.basic.balance > 0 && (
                      <p className="text-xs text-red-500 font-semibold">
                        Balance: {fmtPHP(quickScanDoc.basic.balance)}
                      </p>
                    )}
                    <p className="text-[10px] text-slate-400 mt-0.5">{quickScanDoc.basic.branch_name}</p>
                  </div>
                </div>

                {/* Status + item count */}
                <div className="flex items-center gap-2 text-xs">
                  <span className="px-2.5 py-1 rounded-full bg-slate-100 text-slate-600 font-medium">
                    {quickScanDoc.basic.status}
                  </span>
                  <span className="text-slate-400">
                    {quickScanDoc.basic.items?.length || 0} item(s)
                  </span>
                  <span className="text-slate-300">·</span>
                  <span className="font-mono text-slate-400 text-[10px]">{quickScanDoc.code}</span>
                </div>

                {/* Action buttons */}
                <div className="space-y-2" data-testid="quickscan-actions">
                  <button
                    onClick={() => {
                      navigate(`/doc/${quickScanDoc.code}?branch=${session.branchId}&device=${session.deviceId || ''}`);
                      setQuickScanDoc(null);
                    }}
                    className="w-full flex items-center justify-center gap-2 py-3.5 rounded-2xl bg-[#1A4D2E] text-white font-semibold text-sm active:scale-95 transition-transform"
                    data-testid="quickscan-view-doc"
                  >
                    <ExternalLink size={15} /> View & Reprint
                  </button>
                  <button
                    onClick={() => setQuickScanCloudPrint(true)}
                    className="w-full flex items-center justify-center gap-2 py-3 rounded-2xl border border-emerald-200 text-emerald-700 bg-emerald-50 font-semibold text-sm active:scale-95 transition-transform"
                    data-testid="quickscan-cloud-print"
                  >
                    <Printer size={15} /> Send to Cloud Printer
                  </button>
                  <button
                    onClick={() => setQuickScanDoc(null)}
                    className="w-full py-2.5 text-sm text-slate-400 hover:text-slate-600 transition-colors"
                    data-testid="quickscan-close"
                  >
                    Close
                  </button>
                </div>
              </div>
            ) : (
              <div className="px-5 py-8 text-center space-y-3">
                <CheckCircle2 size={28} className="text-slate-300 mx-auto" />
                <p className="text-sm text-slate-500">Document not found</p>
                <button onClick={() => setQuickScanDoc(null)} className="text-xs text-slate-400 hover:underline">Close</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Document Upload Overlay */}
      {docUploadOpen && (
        <TerminalDocUpload
          branchId={session.branchId}
          onClose={() => setDocUploadOpen(false)}
        />
      )}

      {/* QuickScan Cloud Print Overlay */}
      {quickScanCloudPrint && quickScanDoc?.basic && (
        <QuickScanCloudPrint
          basic={quickScanDoc.basic}
          branchId={session.branchId}
          api={api}
          businessInfo={businessInfo}
          onClose={() => setQuickScanCloudPrint(false)}
        />
      )}

      {/* Print Queue Overlay */}      {printQueueOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end" data-testid="print-queue-overlay">
          <div className="absolute inset-0 bg-black/40" onClick={() => setPrintQueueOpen(false)} />
          <div className="relative bg-white rounded-t-3xl overflow-hidden max-h-[70vh]" style={{ boxShadow: '0 -4px 24px rgba(0,0,0,0.13)' }}>
            <div className="flex justify-center pt-3 pb-1">
              <div className="w-10 h-1 rounded-full bg-slate-300" />
            </div>
            <div className="px-5 pb-2 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-slate-900" style={{ fontFamily: 'Manrope' }}>
                  <Printer size={15} className="inline mr-1.5 text-emerald-600" />
                  Print Queue
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">Mode: <b className="capitalize">{printMode}</b></p>
              </div>
              <button onClick={() => setPrintQueueOpen(false)} className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-slate-500">
                <X size={15} />
              </button>
            </div>
            <div className="overflow-y-auto px-5 pb-6 space-y-2">
              {printQueue.length === 0 ? (
                <div className="text-center py-8 text-slate-400">
                  <Printer size={28} className="mx-auto mb-2 opacity-30" />
                  <p className="text-sm">No pending print jobs</p>
                </div>
              ) : (
                printQueue.map(job => (
                  <div key={job.job_id} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl border border-slate-200" data-testid={`print-queue-item-${job.job_id}`}>
                    <div className="w-9 h-9 rounded-lg bg-emerald-100 text-emerald-700 flex items-center justify-center shrink-0">
                      <Printer size={16} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-slate-800 truncate">{job.document_name || 'Document'}</p>
                      <p className="text-[10px] text-slate-400">{job.document_type?.replace(/_/g, ' ')}</p>
                    </div>
                    <div className="flex gap-1.5 shrink-0">
                      <button
                        onClick={() => { triggerPrint(job); setPrintQueueOpen(false); }}
                        className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700 active:scale-95 transition-all"
                        data-testid={`print-now-${job.job_id}`}
                      >
                        <Printer size={11} className="inline mr-1" />Print
                      </button>
                      <button
                        onClick={() => { markJobStatus(job.job_id, 'cancelled'); setPrintQueue(prev => prev.filter(j => j.job_id !== job.job_id)); }}
                        className="px-2.5 py-1.5 rounded-lg bg-slate-200 text-slate-500 text-xs hover:bg-red-50 hover:text-red-600 transition-colors"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

