import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP, fmtDateTime, fmtDate } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import UploadQRDialog from '../components/UploadQRDialog';
import ReceiptUploadInline from '../components/ReceiptUploadInline';
import VerificationBadge from '../components/VerificationBadge';
import VerifyPinDialog from '../components/VerifyPinDialog';
import ViewQRDialog from '../components/ViewQRDialog';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Separator } from '../components/ui/separator';
import { ScrollArea } from '../components/ui/scroll-area';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import {
  Plus, Trash2, Send, CheckCircle2, Search, RefreshCw, Settings2,
  AlertTriangle, ChevronDown, ChevronUp, Building2, Package, X,
  TrendingUp, TrendingDown, Clock, ArrowRight, Eye, XCircle, Pencil, Upload, Check,
  ClipboardCheck, FileText, Smartphone, Lock, Pause, FolderOpen, Trash, Tag, Shield
} from 'lucide-react';
import { localTodayStr } from '../lib/dateFormat';
import PrintEngine from '../lib/PrintEngine';
import { toast } from 'sonner';
import { useUnsavedChangesGuard } from '../lib/useUnsavedChangesGuard';
import CalcInput from '../components/CalcInput';

// Smart quantity formatter — display max 3 decimal places, no trailing zeros.
// Raw precision is preserved in the database — this is display only.
// Examples: 5.999999997 → "6", 1.333333 → "1.333", 6.0 → "6", 5.25 → "5.25"
const fmtQty = (v) => {
  const n = parseFloat(v);
  if (isNaN(n)) return '0';
  return parseFloat(n.toFixed(3)).toString();
};

// Prevent accidental wheel-scroll changes on number inputs.
// (User reported their value changing when they hovered + scrolled the page.)
const noWheel = (e) => { e.currentTarget.blur(); };

const STATUS_COLORS = {
  draft: 'bg-slate-100 text-slate-600',
  pending_approval: 'bg-amber-100 text-amber-700',
  returned: 'bg-rose-100 text-rose-700',
  sent: 'bg-blue-100 text-blue-700',
  sent_to_terminal: 'bg-amber-100 text-amber-700',
  received_pending: 'bg-yellow-100 text-yellow-700',
  received: 'bg-emerald-100 text-emerald-700',
  disputed: 'bg-red-100 text-red-700',
  cancelled: 'bg-red-100 text-red-600',
};

function newRow() {
  return {
    id: Math.random().toString(36).slice(2),
    productSearch: '', productMatches: [], product: null,
    activeSearchIndex: -1,
    qty: 1,
    requested_qty: null,   // set when generated from stock request
    available_stock: null,  // set when generated from stock request
    branch_capital: 0,
    global_cost_price: 0,
    is_branch_specific_cost: false,
    transfer_capital: '',
    branch_retail: '',
    last_purchase_ref: null, moving_average_ref: null, last_branch_retail: null,
    // Target-branch snapshot (filled by selectProduct)
    target_branch_capital: 0, target_branch_moving_average: 0,
    target_branch_retail: 0, target_branch_stock: 0,
    override: false, override_reason: '',
  };
}

const MARKUP_TYPES = [
  { value: 'fixed', label: '+ Fixed ₱' },
  { value: 'percent', label: '+ % of Capital' },
];

// ── Per-row validation ──────────────────────────────────────────────────────
function validateRow(row, minMargin) {
  const tc = parseFloat(row.transfer_capital) || 0;
  const br = parseFloat(row.branch_retail) || 0;
  if (!tc && !br) return { ok: false, reason: 'incomplete', margin: 0 };
  if (!tc) return { ok: false, reason: 'no_capital', margin: 0 };
  if (!br) return { ok: false, reason: 'retail', margin: 0 };
  const margin = br - tc;
  if (tc > br) return { ok: false, reason: 'below_cost', margin };
  if (margin < minMargin) return { ok: false, reason: 'low_margin', margin };
  return { ok: true, reason: '', margin };
}

// ── Anchor-positioned dropdown (mirrors SmartProductSearch positioning) ───────
// Re-positions on scroll/resize, auto-flips up when there's no room below,
// clamps max-height to available viewport space. Used for both the main
// transfer product search and the stock-request product search.
function AnchoredDropdown({ anchor, open, children, minWidth = 320 }) {
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0, maxHeight: 320, flipUp: false });

  useEffect(() => {
    if (!open || !anchor) return;
    const compute = () => {
      const rect = anchor.getBoundingClientRect();
      const vh = window.innerHeight;
      const spaceBelow = vh - rect.bottom - 8;
      const spaceAbove = rect.top - 8;
      const flipUp = spaceBelow < 220 && spaceAbove > spaceBelow;
      const maxHeight = Math.max(160, Math.min(360, flipUp ? spaceAbove : spaceBelow));
      setPos({
        top: flipUp ? null : rect.bottom + 4,
        bottom: flipUp ? vh - rect.top + 4 : null,
        left: rect.left,
        width: Math.max(rect.width, minWidth),
        maxHeight,
        flipUp,
      });
    };
    compute();
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [open, anchor, minWidth]);

  if (!open || !anchor) return null;
  return createPortal(
    <div
      style={{
        position: 'fixed',
        top: pos.top != null ? pos.top : undefined,
        bottom: pos.bottom != null ? pos.bottom : undefined,
        left: pos.left,
        width: pos.width,
        maxHeight: pos.maxHeight,
        zIndex: 9999,
        // Always-visible scrollbar so the user can see their scroll position.
        // The global 6px scrollbar with transparent track was too subtle here.
        scrollbarColor: '#94a3b8 #f1f5f9',
        scrollbarWidth: 'auto',
      }}
      className="bg-white border border-slate-200 rounded-lg shadow-xl overflow-y-auto flex flex-col anchored-dropdown-scroll"
    >
      {children}
    </div>,
    document.body
  );
}

export default function BranchTransferPage() {
  const { branches, currentBranch, user, canViewAllBranches, selectedBranchId, isConsolidatedView } = useAuth();
  const navigate = useNavigate();
  const isAdmin = user?.role === 'admin';
  const searchTimers = useRef({});
  const dropdownRefs = useRef({});
  const reqDropdownRefs = useRef({});
  const skipResetRef = useRef(false); // skip row/branch reset when loading from request
  const [searchParams, setSearchParams] = useSearchParams();

  // ── Lists / history ────────────────────────────────────────────────────────
  const [tab, setTab] = useState(() => searchParams.get('tab') || 'new');
  const [historyTab, setHistoryTab] = useState(() => searchParams.get('subtab') || 'all');
  const [stockRequests, setStockRequests] = useState([]);
  const [requestsLoading, setRequestsLoading] = useState(false);
  const [generatingTransfer, setGeneratingTransfer] = useState(null); // request id being processed
  // ── Request Stock form state ────────────────────────────────────────────
  const [reqTargetBranch, setReqTargetBranch] = useState('');
  const [reqRows, setReqRows] = useState([{ id: Date.now(), search: '', product: null, qty: '', matches: [] }]);

  // Unsaved-changes guard — fires when the request form has at least one
  // chosen product. Provider renders the dialog.
  useUnsavedChangesGuard({
    isDirty: (reqRows || []).some(r => r.product),
    label: 'Branch Transfer Request',
  });
  const [reqNotes, setReqNotes] = useState('');
  const [reqSaving, setReqSaving] = useState(false);
  const [reqShowRetail, setReqShowRetail] = useState(true);
  const [outgoingRequests, setOutgoingRequests] = useState([]);
  const [outgoingLoading, setOutgoingLoading] = useState(false);
  const [requestsView, setRequestsView] = useState('incoming'); // 'incoming' | 'outgoing'
  const reqSearchTimers = useRef({});

  // Sync tab state from URL params (for deep-linking from notifications)
  useEffect(() => {
    const urlTab = searchParams.get('tab');
    const urlSubtab = searchParams.get('subtab');
    if (urlTab && urlTab !== tab) setTab(urlTab);
    if (urlSubtab && urlSubtab !== historyTab) {
      // Map legacy subtab values to new status-based keys
      const subtabMap = { outgoing: 'all', incoming: 'all' };
      setHistoryTab(subtabMap[urlSubtab] || urlSubtab);
    }
    if (urlTab || urlSubtab) {
      setSearchParams({}, { replace: true });
    }
  }, [searchParams]); // eslint-disable-line

  const [orders, setOrders] = useState([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [viewOrder, setViewOrder] = useState(null);
  const [receiveDialog, setReceiveDialog] = useState(false);
  const [receiveSaving, setReceiveSaving] = useState(false);
  const [editingOrderId, setEditingOrderId] = useState(null); // ID of draft being edited
  const [requestContext, setRequestContext] = useState(null); // { po_id, po_number } when generated from stock request

  // Smart Capital Dialog for branch transfers
  const [transferCapitalDialog, setTransferCapitalDialog] = useState(false);
  const [transferCapitalPreview, setTransferCapitalPreview] = useState(null);
  const [transferCapitalChoices, setTransferCapitalChoices] = useState({});
  const [transferCapitalPendingItems, setTransferCapitalPendingItems] = useState(null);

  // ── New transfer form ──────────────────────────────────────────────────────
  const [fromBranchId, setFromBranchId] = useState(() => currentBranch?.id || '');
  const [toBranchId, setToBranchId] = useState('');
  const [minMargin, setMinMargin] = useState(20);
  const [categoryMarkups, setCategoryMarkups] = useState([]);
  const [markupPanelOpen, setMarkupPanelOpen] = useState(false);
  const [rows, setRows] = useState(() => [newRow()]);
  const [saving, setSaving] = useState(false);
  const [categories, setCategories] = useState([]);
  const [templateLoaded, setTemplateLoaded] = useState(false);

  // Park / Resume — draft transfer staging
  const [parkedTransfers, setParkedTransfers] = useState([]);
  const [parkDialogOpen, setParkDialogOpen] = useState(false);
  const [parkingNow, setParkingNow] = useState(false);
  const [resumeDialogOpen, setResumeDialogOpen] = useState(false);
  const [parkLabel, setParkLabel] = useState('');
  const [activeParkId, setActiveParkId] = useState(null); // ID of currently-resumed park, if any

  // Sync fromBranchId with currentBranch on initial load
  useEffect(() => {
    if (currentBranch?.id && !fromBranchId) setFromBranchId(currentBranch.id);
  }, [currentBranch?.id]); // eslint-disable-line

  const destBranch = branches.find(b => b.id === toBranchId);

  // Load categories once
  useEffect(() => {
    api.get('/products/categories').then(r => setCategories(r.data || [])).catch(() => {});
  }, []);

  // Reset rows and template when source branch changes
  useEffect(() => {
    if (skipResetRef.current) return;
    setRows([newRow()]);
    setToBranchId('');
    setTemplateLoaded(false);
  }, [fromBranchId]); // eslint-disable-line

  // Load markup template when destination branch changes
  useEffect(() => {
    if (!toBranchId) { setTemplateLoaded(false); return; }
    if (skipResetRef.current) { skipResetRef.current = false; setTemplateLoaded(true); return; }
    setRows([newRow()]); // Reset rows for new destination
    api.get(`/branch-transfers/markup-template/${toBranchId}`)
      .then(r => {
        setMinMargin(r.data.min_margin ?? 20);
        setCategoryMarkups(r.data.category_markups || []);
        setTemplateLoaded(true);
      })
      .catch(() => setTemplateLoaded(true));
  }, [toBranchId]);

  // Re-apply markups to all rows when categoryMarkups change
  useEffect(() => {
    if (!rows.length) return;
    setRows(prev => prev.map(row => applyMarkupToRow(row, categoryMarkups)));
  }, [categoryMarkups]); // eslint-disable-line

  // Load transfer history — filtered by branch context
  const loadOrders = useCallback(async () => {
    setOrdersLoading(true);
    try {
      const params = { limit: 100 };
      // Admin in specific branch view: filter by that branch
      if (canViewAllBranches && !isConsolidatedView && currentBranch?.id) {
        params.branch_id = currentBranch.id;
      }
      // isConsolidatedView (All Branches) → no filter, admin sees everything
      const res = await api.get('/branch-transfers', { params });
      setOrders(res.data.orders || []);
    } catch { toast.error('Failed to load transfer history'); }
    setOrdersLoading(false);
  }, [canViewAllBranches, isConsolidatedView, currentBranch?.id]);

  useEffect(() => { if (tab === 'history') loadOrders(); }, [tab, loadOrders]);

  // Auto-open view dialog when ?view=TRANSFER_ID is in URL (from Audit Center etc.)
  useEffect(() => {
    const viewId = searchParams.get('view');
    if (viewId && !viewOrder) {
      setTab('history');
      // Fetch the specific transfer and open view dialog
      api.get(`/branch-transfers/${viewId}`).then(res => {
        setViewOrder(res.data);
        searchParams.delete('view');
        setSearchParams(searchParams, { replace: true });
      }).catch(() => {});
    }
  }, [searchParams]); // eslint-disable-line

  // Load incoming stock requests (branch request POs directed at this branch)
  const loadRequests = useCallback(async () => {
    setRequestsLoading(true);
    try {
      const effectiveBranch = currentBranch?.id || user?.branch_id || '';
      const params = effectiveBranch ? { branch_id: effectiveBranch } : {};
      const res = await api.get('/purchase-orders/incoming-requests', { params });
      setStockRequests(res.data.requests || []);
    } catch { }
    setRequestsLoading(false);
  }, [currentBranch?.id, user?.branch_id]);

  useEffect(() => { if (tab === 'history') loadRequests(); }, [tab, loadRequests]);

  // Generate a branch transfer from a stock request
  const handleGenerateTransfer = async (request) => {
    setGeneratingTransfer(request.id);
    try {
      const res = await api.post(`/purchase-orders/${request.id}/generate-branch-transfer`);
      const transferData = res.data;
      // Store request context for linking
      setRequestContext({ po_id: transferData.po_id, po_number: transferData.po_number });
      // Skip reset effects when pre-filling from request
      skipResetRef.current = true;
      // Pre-load the New Transfer form with the data
      setFromBranchId(transferData.from_branch_id || '');
      setToBranchId(transferData.to_branch_id || '');
      setMinMargin(20);
      const editRows = transferData.items.map(item => ({
        ...newRow(),
        product: { id: item.product_id, name: item.product_name, sku: item.sku, category: item.category, unit: item.unit },
        productSearch: item.product_name,
        requested_qty: item.requested_qty ?? null,
        available_stock: item.available_stock ?? null,
        qty: item.qty,
        branch_capital: item.branch_capital || 0,
        global_cost_price: item.branch_capital || 0,
        is_branch_specific_cost: false,
        transfer_capital: String(item.transfer_capital || item.branch_capital || ''),
        branch_retail: item.show_retail === false ? '' : String(item.branch_retail || ''),
        override: false, override_reason: '',
      }));
      setRows(editRows.length ? editRows : [newRow()]);
      setTab('new');
      toast.success(`Transfer pre-filled from ${transferData.po_number}! Review quantities and set pricing.`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to generate transfer');
    }
    setGeneratingTransfer(null);
  };

  // ── Request Stock form helpers ────────────────────────────────────────────
  const newReqRow = () => ({ id: Date.now() + Math.random(), search: '', product: null, qty: '', matches: [] });
  const updateReqRow = (id, updates) => setReqRows(prev => prev.map(r => r.id === id ? { ...r, ...updates } : r));
  const addReqRow = () => setReqRows(prev => [...prev, newReqRow()]);
  const removeReqRow = (id) => setReqRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);

  const handleReqSearch = (rowId, query) => {
    updateReqRow(rowId, { search: query, product: null });
    clearTimeout(reqSearchTimers.current[rowId]);
    if (!query || query.length < 1) { updateReqRow(rowId, { matches: [] }); return; }
    reqSearchTimers.current[rowId] = setTimeout(async () => {
      try {
        const myBranch = currentBranch?.id || user?.branch_id || '';
        const res = await api.get('/products/search-detail', {
          params: {
            q: query,
            branch_id: reqTargetBranch || undefined,
            also_branch_id: myBranch || undefined,
            limit: 8,
          }
        });
        updateReqRow(rowId, { matches: res.data || [] });
      } catch { updateReqRow(rowId, { matches: [] }); }
    }, 200);
  };

  const selectReqProduct = (rowId, product) => {
    updateReqRow(rowId, { product, search: product.name, matches: [], qty: reqRows.find(r => r.id === rowId)?.qty || '1' });
  };

  const resetReqForm = () => {
    setReqTargetBranch('');
    setReqRows([newReqRow()]);
    setReqNotes('');
  };

  const handleSendRequest = async () => {
    if (!reqTargetBranch) { toast.error('Select a branch to request stock from'); return; }
    const validRows = reqRows.filter(r => r.product && parseFloat(r.qty) > 0);
    if (!validRows.length) { toast.error('Add at least one product with quantity'); return; }
    const targetBranch = branches.find(b => b.id === reqTargetBranch);
    setReqSaving(true);
    try {
      const items = validRows.map(r => ({
        product_id: r.product.id,
        product_name: r.product.name,
        sku: r.product.sku || '',
        unit: r.product.unit || '',
        quantity: parseFloat(r.qty),
        unit_price: 0,
        discount_type: 'none',
        discount_value: 0,
      }));
      await api.post('/purchase-orders', {
        vendor: `Branch Request → ${targetBranch?.name || reqTargetBranch}`,
        items,
        po_type: 'branch_request',
        branch_id: currentBranch?.id || user?.branch_id || '',
        supply_branch_id: reqTargetBranch,
        show_retail: reqShowRetail,
        purchase_date: localTodayStr(),
        notes: reqNotes,
      });
      toast.success(`Stock request sent to ${targetBranch?.name}! They will be notified.`);
      resetReqForm();
      setTab('history');
      setHistoryTab('requests');
      setRequestsView('outgoing');
      loadOutgoingRequests();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to send request');
    }
    setReqSaving(false);
  };

  // Load outgoing requests (requests THIS branch sent to other branches)
  const loadOutgoingRequests = useCallback(async () => {
    const branchId = selectedBranchId || currentBranch?.id;
    if (!branchId) return;
    setOutgoingLoading(true);
    try {
      const res = await api.get('/purchase-orders', { params: { po_type: 'branch_request', branch_id: branchId, limit: 100 } });
      setOutgoingRequests(res.data.purchase_orders || []);
    } catch { setOutgoingRequests([]); }
    setOutgoingLoading(false);
  }, [selectedBranchId, currentBranch]);

  useEffect(() => {
    if (tab === 'history' && historyTab === 'requests' && requestsView === 'outgoing') loadOutgoingRequests();
  }, [tab, historyTab, requestsView, loadOutgoingRequests]);

  // ── Row helpers ─────────────────────────────────────────────────────────────
  const updateRow = (id, updates) =>
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...updates } : r));

  const handleRowSearchKeyDown = (e, row) => {
    const matches = row.productMatches || [];
    if (!matches.length) {
      if (e.key === 'Escape') updateRow(row.id, { productSearch: '', productMatches: [], activeSearchIndex: -1 });
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      updateRow(row.id, { activeSearchIndex: Math.min((row.activeSearchIndex ?? -1) + 1, matches.length - 1) });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      updateRow(row.id, { activeSearchIndex: Math.max((row.activeSearchIndex ?? 0) - 1, 0) });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const idx = row.activeSearchIndex ?? -1;
      if (idx >= 0 && matches[idx]) selectProduct(row.id, matches[idx]);
    } else if (e.key === 'Escape') {
      updateRow(row.id, { productMatches: [], activeSearchIndex: -1 });
    }
  };

  function applyMarkupToRow(row, markups) {
    if (!row.product) return row;
    const cat = row.product.category || 'General';
    const rule = markups.find(m => m.category === cat);
    if (!rule) return row;
    const bc = parseFloat(row.branch_capital) || 0;
    const type = rule.type;
    const val = parseFloat(rule.value) || 0;
    const tc = type === 'percent' ? Math.round(bc * (1 + val / 100) * 100) / 100
                                   : Math.round((bc + val) * 100) / 100;
    return { ...row, transfer_capital: String(tc) };
  }

  const searchProduct = (rowId, q) => {
    updateRow(rowId, { productSearch: q, product: null });
    if (searchTimers.current[rowId]) clearTimeout(searchTimers.current[rowId]);
    if (!q || q.length < 1) { updateRow(rowId, { productMatches: [] }); return; }
    searchTimers.current[rowId] = setTimeout(async () => {
      try {
        const params = { q, from_branch_id: fromBranchId, to_branch_id: toBranchId };
        const res = await api.get('/branch-transfers/product-lookup', { params });
        updateRow(rowId, { productMatches: res.data || [] });
      } catch {}
    }, 200);
  };

  const selectProduct = (rowId, p) => {
    const rowUpdate = {
      product: p, productSearch: p.name, productMatches: [], activeSearchIndex: -1,
      branch_capital: p.branch_capital,
      global_cost_price: p.global_cost_price ?? p.branch_capital,
      is_branch_specific_cost: p.is_branch_specific_cost ?? false,
      last_purchase_ref: p.last_purchase_ref,
      moving_average_ref: p.moving_average_ref,
      last_branch_retail: p.last_branch_retail,
      branch_retail: p.last_branch_retail != null ? String(p.last_branch_retail) : '',
      // Target-branch snapshot (helps the sender decide pricing)
      target_branch_capital: p.target_branch_capital ?? 0,
      target_branch_moving_average: p.target_branch_moving_average ?? 0,
      target_branch_retail: p.target_branch_retail ?? 0,
      target_branch_stock: p.target_branch_stock ?? 0,
      // Repack children with their capital + current dest price
      repacks: (p.repacks || []).map(rp => ({
        ...rp,
        new_retail_price: '', // blank = no change
      })),
    };
    // Apply markup to get transfer capital
    const cat = p.category || 'General';
    const rule = categoryMarkups.find(m => m.category === cat);
    if (rule) {
      const bc = p.branch_capital;
      const val = parseFloat(rule.value) || 0;
      const tc = rule.type === 'percent'
        ? Math.round(bc * (1 + val / 100) * 100) / 100
        : Math.round((bc + val) * 100) / 100;
      rowUpdate.transfer_capital = String(tc);
    } else {
      rowUpdate.transfer_capital = String(p.branch_capital);
    }
    updateRow(rowId, rowUpdate);
  };

  // ── Category markup panel ───────────────────────────────────────────────────
  const setMarkup = (category, field, value) => {
    setCategoryMarkups(prev => {
      const existing = prev.find(m => m.category === category);
      if (existing) {
        return prev.map(m => m.category === category ? { ...m, [field]: value } : m);
      }
      return [...prev, { category, type: 'fixed', value: 0, [field]: value }];
    });
  };
  const getMarkup = (category) => categoryMarkups.find(m => m.category === category) || { type: 'fixed', value: '' };

  const saveTemplate = async () => {
    if (!toBranchId) return;
    try {
      await api.put(`/branch-transfers/markup-template/${toBranchId}`, {
        min_margin: minMargin,
        category_markups: categoryMarkups.filter(m => m.value !== '' && parseFloat(m.value) > 0),
      });
      toast.success('Markup template saved');
    } catch { toast.error('Failed to save template'); }
  };

  // ── Park / Resume — Draft transfer staging ────────────────────────────────
  const loadParkedTransfers = useCallback(async () => {
    if (!fromBranchId) { setParkedTransfers([]); return; }
    try {
      const res = await api.get('/parked-branch-transfers', { params: { from_branch_id: fromBranchId } });
      setParkedTransfers(res.data?.parks || []);
    } catch { /* silent */ }
  }, [fromBranchId]);

  // Load park count whenever from-branch changes
  useEffect(() => { loadParkedTransfers(); }, [loadParkedTransfers]);

  const handlePark = async () => {
    if (!fromBranchId) { toast.error('Source branch required'); return; }
    const populated = rows.filter(r => r.product);
    if (!populated.length) { toast.error('Nothing to park — add at least one product'); return; }

    setParkingNow(true);
    try {
      const payload = {
        id: activeParkId || undefined,
        from_branch_id: fromBranchId,
        to_branch_id: toBranchId || '',
        label: parkLabel.trim() || (toBranchId ? `To ${branches.find(b => b.id === toBranchId)?.name || 'branch'}` : 'Draft'),
        rows,
        min_margin: minMargin,
        category_markups: categoryMarkups,
        item_count: populated.length,
      };
      await api.post('/parked-branch-transfers', payload);
      toast.success(activeParkId ? 'Park updated — pick it up later' : 'Transfer parked — pick it up later');
      setParkDialogOpen(false);
      setParkLabel('');
      setActiveParkId(null);
      // Reset to a fresh draft
      setRows([newRow()]);
      setToBranchId('');
      await loadParkedTransfers();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to park transfer');
    }
    setParkingNow(false);
  };

  const handleResume = async (parkId) => {
    try {
      const res = await api.post(`/parked-branch-transfers/${parkId}/consume`);
      const doc = res.data;
      // Skip the source-branch reset effect when we set toBranchId so rows aren't wiped
      skipResetRef.current = true;
      setFromBranchId(doc.from_branch_id || fromBranchId);
      setToBranchId(doc.to_branch_id || '');
      setRows((doc.rows && doc.rows.length) ? doc.rows : [newRow()]);
      setMinMargin(doc.min_margin ?? 20);
      setCategoryMarkups(doc.category_markups || []);
      setActiveParkId(parkId); // remember so re-park overwrites instead of creating new
      setResumeDialogOpen(false);
      toast.success('Resumed — picked up where you left off');
      await loadParkedTransfers();
      // Allow effects again on next user-driven branch change
      setTimeout(() => { skipResetRef.current = false; }, 100);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to resume');
      await loadParkedTransfers();
    }
  };

  const handleDiscardPark = async (parkId) => {
    if (!window.confirm('Discard this parked transfer? This cannot be undone.')) return;
    try {
      await api.delete(`/parked-branch-transfers/${parkId}`);
      toast.success('Discarded');
      await loadParkedTransfers();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to discard');
    }
  };



  // ── Submit ─────────────────────────────────────────────────────────────────
  const handleSubmit = async (opts = {}) => {
    // opts may be a SyntheticEvent if called directly from <Button onClick>; defensive coercion.
    const requiresApproval = (opts && typeof opts === 'object' && opts.requiresApproval === true);

    if (!toBranchId) { toast.error('Select a destination branch'); return; }
    if (!rows.length || rows.every(r => !r.product)) { toast.error('Add at least one product'); return; }

    const validRows = rows.filter(r => r.product);
    const blockers = validRows.filter(r => {
      const v = validateRow(r, minMargin);
      // When submitting for approval, retail==0 is allowed (admin will set it)
      if (requiresApproval && v.reason === 'retail') return false;
      return !v.ok && !r.override;
    });
    if (blockers.length > 0) {
      toast.error(`${blockers.length} row(s) have validation errors. Fix or override them.`);
      return;
    }

    setSaving(true);
    try {
      // Build repack_price_updates from rows that have repack price entries
      const repack_price_updates = [];
      validRows.forEach(r => {
        (r.repacks || []).forEach(rp => {
          if (rp.new_retail_price && parseFloat(rp.new_retail_price) > 0) {
            repack_price_updates.push({
              repack_id: rp.id,
              repack_name: rp.name,
              new_retail_price: parseFloat(rp.new_retail_price),
              units_per_parent: rp.units_per_parent,
              capital_per_repack: rp.capital_per_repack,
              current_dest_retail: rp.current_dest_retail,
              parent_product_id: r.product.id,
              parent_product_name: r.product.name,
            });
          }
        });
      });

      await api.post('/branch-transfers', {
        from_branch_id: fromBranchId,
        to_branch_id: toBranchId,
        min_margin: minMargin,
        requires_approval: requiresApproval,
        category_markups: categoryMarkups.filter(m => parseFloat(m.value) > 0),
        repack_price_updates,
        request_po_id: requestContext?.po_id || '',
        request_po_number: requestContext?.po_number || '',
        items: validRows.map(r => ({
          product_id: r.product.id,
          product_name: r.product.name,
          sku: r.product.sku,
          category: r.product.category,
          unit: r.product.unit,
          qty: parseFloat(r.qty) || 0,
          requested_qty: r.requested_qty || null,
          branch_capital: r.branch_capital,
          transfer_capital: parseFloat(r.transfer_capital) || 0,
          branch_retail: parseFloat(r.branch_retail) || 0,
          last_purchase_ref: r.last_purchase_ref,
          moving_average_ref: r.moving_average_ref,
          override: r.override,
          override_reason: r.override_reason,
        })),
      });
      toast.success(requiresApproval ? 'Submitted for admin approval — admin notified by SMS' : 'Branch transfer order created!');
      setRows([newRow()]);
      setRequestContext(null);
      setTab('history');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to create transfer'); }
    setSaving(false);
  };

  // ── Edit existing draft order ──────────────────────────────────────────────
  const loadOrderIntoEdit = (order) => {
    skipResetRef.current = true;
    setFromBranchId(order.from_branch_id || '');
    setToBranchId(order.to_branch_id || '');
    setMinMargin(order.min_margin ?? 20);
    setCategoryMarkups(order.category_markups || []);
    setEditingOrderId(order.id);
    // Reconstruct rows from stored items
    const editRows = (order.items || []).map(item => ({
      ...newRow(),
      product: { id: item.product_id, name: item.product_name, sku: item.sku, category: item.category, unit: item.unit },
      productSearch: item.product_name,
      qty: item.qty,
      branch_capital: item.branch_capital || 0,
      global_cost_price: item.branch_capital || 0,
      is_branch_specific_cost: false,
      transfer_capital: String(item.transfer_capital || ''),
      branch_retail: String(item.branch_retail || ''),
      override: item.override || false,
      override_reason: item.override_reason || '',
    }));
    setRows(editRows.length ? editRows : [newRow()]);
    setTab('new');
  };

  const handleUpdateDraft = async () => {
    if (!editingOrderId) { handleSubmit(); return; }
    if (!rows.length || rows.every(r => !r.product)) { toast.error('Add at least one product'); return; }
    const validRows = rows.filter(r => r.product);
    const blockers = validRows.filter(r => { const v = validateRow(r, minMargin); return !v.ok && !r.override; });
    if (blockers.length > 0) { toast.error(`${blockers.length} row(s) have validation errors.`); return; }

    setSaving(true);
    try {
      await api.put(`/branch-transfers/${editingOrderId}`, {
        min_margin: minMargin,
        category_markups: categoryMarkups.filter(m => parseFloat(m.value) > 0),
        items: validRows.map(r => ({
          product_id: r.product.id,
          product_name: r.product.name,
          sku: r.product.sku,
          category: r.product.category,
          unit: r.product.unit,
          qty: parseFloat(r.qty) || 0,
          branch_capital: r.branch_capital,
          transfer_capital: parseFloat(r.transfer_capital) || 0,
          branch_retail: parseFloat(r.branch_retail) || 0,
          override: r.override,
          override_reason: r.override_reason,
        })),
      });
      toast.success('Draft transfer updated!');
      setEditingOrderId(null);
      setRows([newRow()]);
      setTab('history');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to update transfer'); }
    setSaving(false);
  };

  const cancelEdit = () => {
    setEditingOrderId(null);
    setRequestContext(null);
    setRows([newRow()]);
    setFromBranchId(currentBranch?.id || '');
    setToBranchId('');
  };
  const [receiveQtys, setReceiveQtys] = useState({});
  const [receiveNotes, setReceiveNotes] = useState('');
  const [receiveConfirmStep, setReceiveConfirmStep] = useState(false); // double-check step (variance)
  const [receiveStep, setReceiveStep] = useState('qty'); // 'qty' | 'upload' | 'summary'
  const [acceptDialog, setAcceptDialog] = useState(null); // order to accept
  const [disputeDialog, setDisputeDialog] = useState(null); // order to dispute
  const [disputeNote, setDisputeNote] = useState('');
  const [actionSaving, setActionSaving] = useState(false);
  const [btUploadQROpen, setBtUploadQROpen] = useState(false);
  const [btUploadOrderId, setBtUploadOrderId] = useState(null);
  const [btVerifyOpen, setBtVerifyOpen] = useState(false);
  const [btVerifyId, setBtVerifyId] = useState(null);
  const [btViewQROpen, setBtViewQROpen] = useState(false);
  const [receiveReceiptData, setReceiveReceiptData] = useState(null);

  const openReceive = (order) => {
    setViewOrder(order);
    const qtys = {};
    order.items.forEach(item => { qtys[item.product_id] = item.qty; });
    setReceiveQtys(qtys);
    setReceiveNotes('');
    setReceiveConfirmStep(false);
    setReceiveReceiptData(null);
    setReceiveStep('qty'); // reset wizard to first step
    setReceiveDialog(true);
  };

  // Compute variance for current receiveQtys
  const getVariances = (items) => {
    if (!items) return { hasVariance: false, shortages: [], excesses: [] };
    const shortages = [], excesses = [];
    items.forEach(item => {
      const ordered = parseFloat(item.qty) || 0;
      const received = parseFloat(receiveQtys[item.product_id] ?? ordered) || 0;
      const diff = received - ordered;
      if (diff < 0) shortages.push({ ...item, qty_received: received, diff });
      else if (diff > 0) excesses.push({ ...item, qty_received: received, diff });
    });
    return { hasVariance: shortages.length > 0 || excesses.length > 0, shortages, excesses };
  };

  const handleReceive = async () => {
    if (!viewOrder) return;

    const { hasVariance } = getVariances(viewOrder.items);

    // Soft warning if no receipt — but allow proceeding (one-time confirm).
    // Receipt is recommended for audit trail but not blocking.
    if (!receiveReceiptData?.fileCount && !receiveConfirmStep) {
      const ok = window.confirm(
        'No receipt / DR photo uploaded.\n\n' +
        'A receipt photo is recommended for audit trail. You can scan the QR code with your phone or upload from this computer using the Upload Receipt section above.\n\n' +
        'Proceed without a receipt?'
      );
      if (!ok) return;
    }

    // First click with variance: show double-check step
    if (hasVariance && !receiveConfirmStep) {
      setReceiveConfirmStep(true);
      return;
    }

    // Build items payload
    const items = viewOrder.items.map(item => ({
      product_id: item.product_id,
      qty: item.qty,
      qty_received: parseFloat(receiveQtys[item.product_id]) ?? item.qty,
      transfer_capital: item.transfer_capital,
      branch_retail: item.branch_retail,
    }));

    // Check capital preview for price drops
    try {
      const preview = await api.get(`/branch-transfers/${viewOrder.id}/capital-preview`);
      if (preview.data.has_warnings) {
        const defaultChoices = {};
        // Same smart rule as POs: price drop → default to moving_average
        preview.data.items.forEach(i => {
          defaultChoices[i.product_id] = i.needs_warning ? 'moving_average' : 'transfer_capital';
        });
        setTransferCapitalChoices(defaultChoices);
        setTransferCapitalPreview(preview.data);
        setTransferCapitalPendingItems(items);
        setTransferCapitalDialog(true);
        return;
      }
    } catch { /* ignore preview errors, proceed with receive */ }

    // No warnings — proceed directly
    await _doReceive(items, {});
  };

  const _doReceive = async (items, capitalChoices) => {
    setReceiveSaving(true);
    try {
      const payload = {
        items,
        notes: receiveNotes,
        capital_choices: capitalChoices,
      };
      if (receiveReceiptData?.sessionId) {
        payload.upload_session_ids = [receiveReceiptData.sessionId];
      }
      const res = await api.post(`/branch-transfers/${viewOrder.id}/receive`, payload);
      if (res.data.status === 'received_pending') {
        toast.warning('Quantities have variance — submitted for source branch confirmation.');
      } else {
        toast.success(res.data.message || 'Transfer received!');
      }
      setReceiveDialog(false);
      setReceiveConfirmStep(false);
      setTransferCapitalDialog(false);
      setTransferCapitalPreview(null);
      setViewOrder(null);
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Receive failed'); }
    setReceiveSaving(false);
  };

  const confirmTransferReceive = async () => {
    if (!transferCapitalPendingItems) return;
    await _doReceive(transferCapitalPendingItems, transferCapitalChoices);
  };

  const handleAcceptReceipt = async (orderId, note = '', action = 'accept') => {
    setActionSaving(true);
    try {
      const res = await api.post(`/branch-transfers/${orderId}/accept-receipt`, { note, action });
      if (action === 'accept_with_incident') {
        toast.success(`Receipt accepted. Incident ticket ${res.data.incident_ticket_number} created for investigation.`);
      } else {
        toast.success('Receipt accepted — inventory updated.');
      }
      setAcceptDialog(null);
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to accept'); }
    setActionSaving(false);
  };

  const handleDisputeReceipt = async () => {
    if (!disputeDialog || !disputeNote.trim()) { toast.error('Dispute reason is required'); return; }
    setActionSaving(true);
    try {
      await api.post(`/branch-transfers/${disputeDialog.id}/dispute-receipt`, { note: disputeNote });
      toast.success('Receipt disputed — destination has been notified to re-count.');
      setDisputeDialog(null);
      setDisputeNote('');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to dispute'); }
    setActionSaving(false);
  };

  const handleSend = async (orderId) => {
    try {
      await api.post(`/branch-transfers/${orderId}/send`);
      toast.success('Transfer marked as sent');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  const handleResubmit = async (orderId) => {
    if (!window.confirm('Resubmit this transfer for admin approval? Admins will be notified by SMS.')) return;
    try {
      await api.post(`/branch-transfers/${orderId}/resubmit`);
      toast.success('Resubmitted — admins notified');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to resubmit'); }
  };

  const sendTransferToTerminal = async (orderId) => {
    if (!window.confirm('Send this transfer to the terminal for receiving? It will be locked here until the terminal processes it.')) return;
    try {
      await api.post(`/branch-transfers/${orderId}/send-to-terminal`);
      toast.success('Transfer sent to terminal for checking');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to send to terminal'); }
  };

  // ── Print via centralized PrintEngine ──────────────────────────────────────
  const [bizInfo, setBizInfo] = useState({});
  useEffect(() => {
    api.get('/settings/business-info').then(r => setBizInfo(r.data)).catch(() => {});
  }, []);

  const printTransferOrder = async (order, format = 'full_page') => {
    const toBranch = branches.find(b => b.id === order.to_branch_id)?.name || '';
    const fromBranch = branches.find(b => b.id === order.from_branch_id)?.name || '';

    // Generate or fetch document code for QR
    let docCode = order.doc_code || '';
    if (!docCode) {
      try {
        const res = await api.post('/doc/generate-code', { doc_type: 'branch_transfer', doc_id: order.id });
        docCode = res.data.code || '';
      } catch { /* print without QR if code generation fails */ }
    }

    PrintEngine.print({
      type: 'branch_transfer',
      data: { ...order, from_branch_name: fromBranch, to_branch_name: toBranch },
      format,
      businessInfo: bizInfo,
      docCode,
    });
  };

  const handleCancel = async (orderId) => {    if (!window.confirm('Cancel this transfer order?')) return;
    try {
      await api.delete(`/branch-transfers/${orderId}`);
      toast.success('Transfer cancelled');
      loadOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  // ── Totals ─────────────────────────────────────────────────────────────────
  const validRows = rows.filter(r => r.product);
  const totalBranchCapital = validRows.reduce((s, r) => s + (r.branch_capital * (parseFloat(r.qty)||0)), 0);
  const totalTransfer = validRows.reduce((s, r) => s + ((parseFloat(r.transfer_capital)||0) * (parseFloat(r.qty)||0)), 0);
  const totalRetail = validRows.reduce((s, r) => s + ((parseFloat(r.branch_retail)||0) * (parseFloat(r.qty)||0)), 0);

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="branch-transfer-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
          <Building2 size={22} className="text-[#1A4D2E]" /> Branch Transfers
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Transfer stock between any branches with auto-computed pricing
        </p>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="new"><Plus size={14} className="mr-1" /> New Transfer</TabsTrigger>
          <TabsTrigger value="request" data-testid="request-stock-tab"><Package size={14} className="mr-1" /> Request Stock</TabsTrigger>
          <TabsTrigger value="history" data-testid="transfer-history-tab"><Clock size={14} className="mr-1" /> Transfers</TabsTrigger>
        </TabsList>

        {/* ── NEW TRANSFER TAB ── */}
        <TabsContent value="new" className="space-y-4 mt-4">
          {/* Editing banner */}
          {editingOrderId && (
            <div className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-amber-50 border border-amber-200">
              <div className="flex items-center gap-2 text-sm text-amber-800">
                <AlertTriangle size={15} className="shrink-0" />
                <span>Editing draft order — changes won't affect inventory until it's sent and received.</span>
              </div>
              <Button variant="ghost" size="sm" onClick={cancelEdit} className="text-amber-700 h-7">
                Discard Changes
              </Button>
            </div>
          )}

          {/* Header row */}
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex-1 min-w-[160px]">
              <Label className="text-xs">From Branch <span className="text-slate-400">(source)</span></Label>
              <Select value={fromBranchId} onValueChange={val => { setFromBranchId(val); setToBranchId(''); }}
                data-testid="from-branch-select">
                <SelectTrigger className="mt-1 h-9" data-testid="from-branch-trigger">
                  <SelectValue placeholder="Select source branch" />
                </SelectTrigger>
                <SelectContent>
                  {branches.filter(b => b.id !== toBranchId).map(b => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center pb-1">
              <ArrowRight size={18} className="text-slate-400" />
            </div>
            <div className="flex-1 min-w-[160px]">
              <Label className="text-xs">To Branch <span className="text-slate-400">(destination)</span></Label>
              <Select value={toBranchId} onValueChange={setToBranchId} disabled={!fromBranchId}>
                <SelectTrigger className="mt-1 h-9" data-testid="dest-branch-select">
                  <SelectValue placeholder="Select destination branch" />
                </SelectTrigger>
                <SelectContent>
                  {branches.filter(b => b.id !== fromBranchId).map(b => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="w-40">
              <Label className="text-xs">Min Margin (₱ / unit)</Label>
              <CalcInput value={minMargin}
 onChange={(v) => setMinMargin(parseFloat(v) || 0)}
 className="mt-1 h-9 font-mono" data-testid="min-margin-input" />
            </div>
            {toBranchId && (
              <Button variant="outline" size="sm" onClick={saveTemplate} className="h-9 mt-5">
                Save as Default
              </Button>
            )}
          </div>

          {/* Category Markup Panel */}
          {toBranchId && (
            <Card className="border-slate-200">
              <button
                type="button"
                onClick={() => setMarkupPanelOpen(v => !v)}
                className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 rounded-xl transition-colors"
                data-testid="markup-panel-toggle"
              >
                <span className="flex items-center gap-2">
                  <Settings2 size={15} className="text-[#1A4D2E]" />
                  Category Markup Rules
                  {categoryMarkups.filter(m => parseFloat(m.value) > 0).length > 0 && (
                    <Badge className="text-[10px] bg-[#1A4D2E]/10 text-[#1A4D2E]">
                      {categoryMarkups.filter(m => parseFloat(m.value) > 0).length} active
                    </Badge>
                  )}
                </span>
                {markupPanelOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
              {markupPanelOpen && (
                <CardContent className="pt-0 pb-4">
                  <p className="text-xs text-slate-400 mb-3">
                    Set per-category add-ons. Applies to all products when added. All values are editable per row.
                  </p>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {categories.map(cat => {
                      const mk = getMarkup(cat);
                      return (
                        <div key={cat} className="p-3 rounded-lg border border-slate-200 bg-slate-50">
                          <p className="text-xs font-semibold text-slate-600 mb-2">{cat}</p>
                          <div className="flex gap-1.5">
                            <Select value={mk.type || 'fixed'} onValueChange={v => setMarkup(cat, 'type', v)}>
                              <SelectTrigger className="h-7 text-xs w-28"><SelectValue /></SelectTrigger>
                              <SelectContent>
                                {MARKUP_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                              </SelectContent>
                            </Select>
                            <CalcInput placeholder="0"
 value={mk.value}
 onChange={(v) => setMarkup(cat, 'value', v)}
 className="h-7 text-xs font-mono flex-1"
 data-testid={`markup-${cat}`} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              )}
            </Card>
          )}

          {/* Request context banner */}
          {requestContext && (
            <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-blue-50 border border-blue-200">
              <Package size={18} className="text-blue-600 shrink-0" />
              <div className="flex-1">
                <p className="text-sm font-semibold text-blue-800">Fulfilling Stock Request {requestContext.po_number}</p>
                <p className="text-xs text-blue-600 mt-0.5">
                  Review requested quantities vs available stock. Adjust send qty based on what you can fulfill.
                </p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => { setRequestContext(null); cancelEdit(); }} className="text-blue-600 h-7">
                Cancel
              </Button>
            </div>
          )}

          {/* Product table */}
          {toBranchId && (
            <>
              {(() => { const isFromRequest = !!requestContext; return (
              <Card className="border-slate-200 overflow-visible">
                <CardContent className="p-0">
                  <table className="w-full text-sm border-collapse">
                    <thead>
                      <tr className="bg-slate-50 border-b-2 border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                        <th className="px-3 py-2 text-left" style={{minWidth:'200px'}}>Product</th>
                        {isFromRequest && (
                          <>
                            <th className="px-2 py-2 text-center" style={{minWidth:'70px'}}>
                              Requested
                              <span className="text-[9px] font-normal block text-blue-400">by branch</span>
                            </th>
                            <th className="px-2 py-2 text-center" style={{minWidth:'70px'}}>
                              Available
                              <span className="text-[9px] font-normal block text-slate-400">in stock</span>
                            </th>
                          </>
                        )}
                        <th className="px-2 py-2 text-center" style={{minWidth:'70px'}}>
                          {isFromRequest ? 'Send' : 'Qty'}
                        </th>
                        <th className="px-2 py-2 text-right" style={{minWidth:'100px'}}>Branch Capital</th>
                        <th className="px-2 py-2 text-right" style={{minWidth:'110px'}}>
                          Transfer Capital
                          <span className="text-[9px] font-normal block text-slate-400">(their cost)</span>
                        </th>
                        <th className="px-2 py-2 text-right" style={{minWidth:'110px'}}>
                          Branch Retail
                          <span className="text-[9px] font-normal block text-slate-400">(their sell price)</span>
                        </th>
                        <th className="px-2 py-2 text-center" style={{minWidth:'90px'}}>Margin</th>
                        {isAdmin && <th className="px-2 py-2 text-center" style={{minWidth:'80px'}}>Override</th>}
                        <th className="w-8"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => {
                        const v = row.product ? validateRow(row, minMargin) : { ok: true, margin: 0 };
                        const rowBad = !v.ok && !row.override;
                        return (
                          <React.Fragment key={row.id}>
                          <tr className={`border-b border-slate-100 ${rowBad ? 'bg-red-50/50' : 'hover:bg-slate-50/30'}`}>
                            {/* Product search */}
                            <td className="px-2 py-1.5" style={{minWidth:'200px'}}>
                              {row.product ? (
                                <div>
                                  <div className="flex items-center gap-1.5 bg-emerald-50 border border-emerald-200 rounded px-2 h-8">
                                    <span className="text-sm font-medium text-emerald-800 truncate flex-1">{row.product.name}</span>
                                    <button onClick={() => updateRow(row.id, { product: null, productSearch: '', transfer_capital: '', branch_retail: '' })}
                                      className="text-slate-300 hover:text-red-500">
                                      <X size={12} />
                                    </button>
                                  </div>
                                  <p className="text-[10px] text-slate-400 mt-0.5">{row.product.sku} · {row.product.category}</p>
                                </div>
                              ) : (
                                <div className="relative">
                                  <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
                                  <Input value={row.productSearch}
                                    onChange={e => searchProduct(row.id, e.target.value)}
                                    onKeyDown={e => handleRowSearchKeyDown(e, row)}
                                    placeholder="Search product..."
                                    className="h-8 pl-7 text-sm"
                                    data-testid={`product-search-${row.id}`}
                                    ref={el => { dropdownRefs.current[row.id] = el; }}
                                    autoComplete="new-password"
                                  />
                                  <AnchoredDropdown
                                    anchor={dropdownRefs.current[row.id]}
                                    open={(row.productMatches?.length || 0) > 0}
                                    minWidth={320}
                                  >
                                    {row.productMatches?.map((p, idx) => (
                                      <button key={p.id} onMouseDown={() => selectProduct(row.id, p)}
                                        className={`w-full text-left px-3 py-2 border-b last:border-0 transition-colors ${
                                          idx === row.activeSearchIndex
                                            ? 'bg-emerald-50 border-l-[3px] border-l-emerald-700'
                                            : 'hover:bg-slate-50'
                                        }`}>
                                        <div className="flex justify-between items-start">
                                          <div>
                                            <span className="font-medium text-sm">{p.name}</span>
                                            <span className="text-slate-400 text-xs ml-2">{p.sku} · {p.category}</span>
                                          </div>
                                          <span className="text-xs font-mono text-slate-600">{formatPHP(p.branch_capital)}</span>
                                        </div>
                                        {p.last_branch_retail && (
                                          <span className="text-[10px] text-blue-500">Last retail at branch: {formatPHP(p.last_branch_retail)}</span>
                                        )}
                                      </button>
                                    ))}
                                    <div className="px-3 py-1 bg-slate-50 text-[10px] text-slate-400 flex gap-3 border-t sticky bottom-0">
                                      <span>↑↓ navigate</span>
                                      <span>Enter to select</span>
                                      <span>Esc to close</span>
                                    </div>
                                  </AnchoredDropdown>
                                </div>
                              )}
                            </td>

                            {/* Requested Qty — read only, from stock request */}
                            {isFromRequest && (
                              <td className="px-2 py-1.5 text-center">
                                {row.requested_qty != null ? (
                                  <span className="font-mono text-sm text-blue-600 font-semibold">{row.requested_qty}</span>
                                ) : <span className="text-slate-300">—</span>}
                              </td>
                            )}
                            {/* Available Stock — read only */}
                            {isFromRequest && (
                              <td className="px-2 py-1.5 text-center">
                                {row.available_stock != null ? (
                                  <span className={`font-mono text-sm font-semibold ${row.available_stock < (row.requested_qty || 0) ? 'text-amber-600' : 'text-emerald-600'}`}>
                                    {row.available_stock}
                                    {row.available_stock < (row.requested_qty || 0) && (
                                      <span className="block text-[9px] text-amber-500 font-normal">low</span>
                                    )}
                                  </span>
                                ) : <span className="text-slate-300">—</span>}
                              </td>
                            )}

                            {/* Qty / Send Qty */}
                            <td className="px-2 py-1.5 text-center">
                              <CalcInput value={row.qty}
 onChange={(v) => updateRow(row.id, { qty: v })}
 className={`h-8 text-sm text-center font-mono w-16 ${
 isFromRequest && row.requested_qty != null && parseFloat(row.qty) < row.requested_qty
 ? 'border-amber-300 bg-amber-50'
 : ''
 }`}
 data-testid={`qty-${row.id}`} />
                              {isFromRequest && row.requested_qty != null && parseFloat(row.qty) < row.requested_qty && (
                                <span className="text-[9px] text-amber-500 block">partial</span>
                              )}
                            </td>

                            {/* Branch Capital — read-only */}
                            <td className="px-2 py-1.5 text-right">
                              <div className="h-auto flex flex-col items-end justify-center pr-2">
                                <span className="font-mono text-sm text-slate-600">{row.product ? formatPHP(row.branch_capital) : '—'}</span>
                                {row.product && row.is_branch_specific_cost && (
                                  <span className="text-[9px] text-amber-600 leading-tight">
                                    branch cost (global: {formatPHP(row.global_cost_price)})
                                  </span>
                                )}
                              </div>
                            </td>

                            {/* Transfer Capital — editable */}
                            <td className="px-2 py-1.5">
                              <div>
                                <CalcInput
                                  value={row.transfer_capital}
                                  onChange={(v) => updateRow(row.id, { transfer_capital: v })}
                                  onKeyDown={e => {
                                    // Tab on the LAST row when branch_retail is disabled (non-admin) → auto-add new row
                                    if (e.key === 'Tab' && !e.shiftKey && !isAdmin) {
                                      const isLastRow = rows[rows.length - 1]?.id === row.id;
                                      if (isLastRow && row.product) {
                                        e.preventDefault();
                                        const nr = newRow();
                                        setRows(rs => [...rs, nr]);
                                        setTimeout(() => {
                                          const nextInput = document.querySelector(`[data-testid="product-search-${nr.id}"]`);
                                          if (nextInput) nextInput.focus();
                                        }, 50);
                                      }
                                    }
                                  }}
                                  placeholder="0.00"
                                  className={`h-8 text-sm text-right font-mono font-bold ${rowBad && v.reason === 'below_cost' ? 'border-red-400 text-red-700 bg-red-50' : ''}`}
                                  data-testid={`transfer-capital-${row.id}`}
                                  disabled={!row.product}
                                />
                                {row.product && (
                                  <div className="flex flex-col gap-0 mt-0.5">
                                    <div className="flex gap-2">
                                      {row.last_purchase_ref != null && <span className="text-[9px] text-slate-400">LP: {formatPHP(row.last_purchase_ref)}</span>}
                                      {row.moving_average_ref != null && <span className="text-[9px] text-slate-400">MA: {formatPHP(row.moving_average_ref)}</span>}
                                    </div>
                                    {toBranchId && (row.target_branch_capital > 0 || row.target_branch_moving_average > 0) && (() => {
                                      const tStock = parseFloat(row.target_branch_stock) || 0;
                                      const sendQty = parseFloat(row.qty) || 0;
                                      const sendCap = parseFloat(row.transfer_capital) || 0;
                                      const newCap = (tStock + sendQty > 0 && sendCap > 0)
                                        ? (tStock * row.target_branch_capital + sendQty * sendCap) / (tStock + sendQty)
                                        : null;
                                      return (
                                        <div className="flex flex-wrap gap-1.5 text-[9px] mt-0.5 pt-0.5 border-t border-slate-100">
                                          <span className="text-purple-600 font-medium" title={`Target branch is currently booking this at ${formatPHP(row.target_branch_capital)}`}>
                                            🎯 {formatPHP(row.target_branch_capital)}
                                          </span>
                                          {row.target_branch_moving_average > 0 && (
                                            <span className="text-purple-400" title="Target branch moving-average">
                                              MA {formatPHP(row.target_branch_moving_average)}
                                            </span>
                                          )}
                                          {newCap != null && (
                                            <span className="text-emerald-600 font-bold" title={`Weighted: (${tStock} × ${formatPHP(row.target_branch_capital)} + ${sendQty} × ${formatPHP(sendCap)}) ÷ ${tStock + sendQty}`}>
                                              → {formatPHP(newCap)}
                                            </span>
                                          )}
                                        </div>
                                      );
                                    })()}
                                  </div>
                                )}
                              </div>
                            </td>

                            {/* Branch Retail — admin only for editing */}
                            <td className="px-2 py-1.5">
                              <div>
                                <CalcInput
                                  value={row.branch_retail}
                                  onChange={(v) => updateRow(row.id, { branch_retail: v })}
                                  onKeyDown={e => {
                                    // Tab on the LAST row's last input → auto-add new row & focus it
                                    if (e.key === 'Tab' && !e.shiftKey) {
                                      const isLastRow = rows[rows.length - 1]?.id === row.id;
                                      if (isLastRow && row.product) {
                                        e.preventDefault();
                                        const nr = newRow();
                                        setRows(rs => [...rs, nr]);
                                        setTimeout(() => {
                                          const nextInput = document.querySelector(`[data-testid="product-search-${nr.id}"]`);
                                          if (nextInput) nextInput.focus();
                                        }, 50);
                                      }
                                    }
                                  }}
                                  placeholder="0.00"
                                  className={`h-8 text-sm text-right font-mono font-bold ${rowBad ? 'border-red-400 text-red-700 bg-red-50' : ''} ${!isAdmin ? 'bg-slate-50 text-slate-400' : ''}`}
                                  data-testid={`branch-retail-${row.id}`}
                                  disabled={!row.product || !isAdmin}
                                />
                                {row.last_branch_retail != null && (
                                  <span className="text-[9px] text-blue-400 mt-0.5 block">Last: {formatPHP(row.last_branch_retail)}</span>
                                )}
                                {toBranchId && row.product && row.target_branch_retail > 0 && (
                                  <div className="flex items-center gap-1 mt-0.5">
                                    <span className="text-[9px] text-purple-600 font-medium" title={`Current retail at ${destBranch?.name || 'target branch'}`}>
                                      🎯 Now: {formatPHP(row.target_branch_retail)}
                                    </span>
                                    {isAdmin && parseFloat(row.branch_retail) !== parseFloat(row.target_branch_retail) && (
                                      <button
                                        type="button"
                                        onClick={() => updateRow(row.id, { branch_retail: String(row.target_branch_retail) })}
                                        className="text-[9px] text-blue-600 hover:text-blue-800 underline-offset-2 hover:underline font-medium"
                                        title="Use the target branch's current retail price"
                                        data-testid={`match-target-retail-${row.id}`}
                                      >match</button>
                                    )}
                                  </div>
                                )}
                                {!isAdmin && row.product && (
                                  <span className="text-[9px] text-slate-400 mt-0.5 block">Admin sets retail</span>
                                )}
                              </div>
                            </td>

                            {/* Margin badge */}
                            <td className="px-2 py-1.5 text-center">
                              {row.product && row.transfer_capital && row.branch_retail ? (
                                <div>
                                  <span className={`text-sm font-bold font-mono ${v.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                                    {v.margin >= 0 ? '+' : ''}{formatPHP(v.margin)}
                                  </span>
                                  {!v.ok && !row.override && (
                                    <p className="text-[9px] text-red-500 leading-tight">
                                      {v.reason === 'below_cost' ? 'Below cost' : `< ₱${minMargin} min`}
                                    </p>
                                  )}
                                </div>
                              ) : <span className="text-slate-300">—</span>}
                            </td>

                            {/* Admin override */}
                            {isAdmin && (
                              <td className="px-2 py-1.5 text-center">
                                {row.product && !v.ok ? (
                                  <div>
                                    <label className="flex items-center gap-1 justify-center cursor-pointer">
                                      <input type="checkbox" checked={row.override}
                                        onChange={e => updateRow(row.id, { override: e.target.checked })}
                                        className="rounded border-slate-300"
                                        data-testid={`override-${row.id}`}
                                      />
                                      <span className="text-[10px] text-amber-600">Override</span>
                                    </label>
                                    {row.override && (
                                      <Input value={row.override_reason}
                                        onChange={e => updateRow(row.id, { override_reason: e.target.value })}
                                        placeholder="Reason" className="h-6 text-[10px] mt-0.5" />
                                    )}
                                  </div>
                                ) : <span className="text-slate-200">—</span>}
                              </td>
                            )}

                            {/* Delete row */}
                            <td className="px-1">
                              {rows.length > 1 && (
                                <button onClick={() => setRows(r => r.filter(x => x.id !== row.id))}
                                  className="p-1 text-slate-300 hover:text-red-500 transition-colors">
                                  <Trash2 size={13} />
                                </button>
                              )}
                            </td>
                          </tr>

                          {/* ── Repack pricing sub-row ── */}
                          {row.product && (row.repacks || []).length > 0 && (
                            <tr className="bg-blue-50/40 border-b border-blue-100">
                              <td colSpan={isAdmin ? 9 : 8} className="px-3 py-2">
                                <div className="flex items-start gap-1.5 flex-wrap">
                                  <span className="text-[10px] font-semibold text-blue-600 uppercase tracking-wider mr-1 mt-1.5 shrink-0">
                                    📦 Repack prices at destination:
                                  </span>
                                  {(row.repacks || []).map((rp, ri) => {
                                    // Compute new repack capital after this transfer is received
                                    const sendQty = parseFloat(row.qty) || 0;
                                    const sendCap = parseFloat(row.transfer_capital) || 0;
                                    const tStock = parseFloat(row.target_branch_stock) || 0;
                                    const upp = parseFloat(rp.units_per_parent) || 1;
                                    const targetRepackCap = parseFloat(rp.target_capital_per_repack) || 0;
                                    const newParentCap = (tStock + sendQty > 0 && sendCap > 0)
                                      ? (tStock * (row.target_branch_capital || 0) + sendQty * sendCap) / (tStock + sendQty)
                                      : null;
                                    const newRepackCap = newParentCap != null ? newParentCap / upp : null;
                                    return (
                                    <div key={rp.id} className="flex items-center gap-1.5 bg-white border border-blue-200 rounded-lg px-2.5 py-1.5 text-xs">
                                      <div className="text-slate-600 min-w-0">
                                        <span className="font-medium">{rp.name}</span>
                                        <span className="text-slate-400 ml-1">({rp.units_per_parent}/bag)</span>
                                        <div className="flex items-center flex-wrap gap-x-2 gap-y-0.5 mt-0.5">
                                          <span className="text-slate-400 font-mono" title="Capital per repack at FROM branch">From: {formatPHP(rp.capital_per_repack)}</span>
                                          {targetRepackCap > 0 && (
                                            <span className="text-purple-600 font-mono font-medium" title={`Target branch is currently booking each repack at ${formatPHP(targetRepackCap)}`}>
                                              🎯 {formatPHP(targetRepackCap)}
                                            </span>
                                          )}
                                          {newRepackCap != null && (
                                            <span className="text-emerald-600 font-mono font-bold" title="New capital per repack after this transfer is received (weighted)">
                                              → {formatPHP(newRepackCap)}
                                            </span>
                                          )}
                                          <span className="text-blue-500 font-mono" title="Current retail at target branch">Retail: {formatPHP(rp.current_dest_retail)}</span>
                                        </div>
                                      </div>
                                      <div className="flex items-center gap-1 ml-1">
                                        <span className="text-slate-400">→</span>
                                        <CalcInput value={rp.new_retail_price}
 onChange={(v) => {
 const updated = (row.repacks || []).map((r2, r2i) =>
 r2i === ri ? { ...r2, new_retail_price: v } : r2
 );
 updateRow(row.id, { repacks: updated });
 }}
 placeholder="New price"
 className="w-24 h-7 border border-blue-300 rounded-md px-2 text-xs font-mono text-right focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white"
 data-testid={`repack-price-${rp.id}`} />
                                        {rp.new_retail_price && parseFloat(rp.new_retail_price) > 0 && (
                                          <span className={`text-[10px] font-bold font-mono ml-1 ${parseFloat(rp.new_retail_price) > rp.capital_per_repack ? 'text-emerald-600' : 'text-red-500'}`}>
                                            {parseFloat(rp.new_retail_price) > rp.capital_per_repack
                                              ? `+${formatPHP(parseFloat(rp.new_retail_price) - rp.capital_per_repack)}`
                                              : 'below cost!'}
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                    );
                                  })}
                                  <span className="text-[10px] text-slate-400 self-center ml-1">Leave blank to keep current price</span>
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                    </tbody>
                  </table>
                </CardContent>
              </Card>
              ); })()}

              {/* Add row + Totals + Submit */}
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setRows(r => [...r, newRow()])}
                    data-testid="add-row-btn">
                    <Plus size={14} className="mr-1" /> Add Product
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => { setParkLabel(''); setParkDialogOpen(true); }}
                    disabled={!rows.some(r => r.product)}
                    className="text-amber-700 border-amber-300 hover:bg-amber-50"
                    data-testid="park-transfer-btn"
                    title="Save current draft and continue later — like Park Sale">
                    <Pause size={13} className="mr-1" /> {activeParkId ? 'Update Park' : 'Park'}
                  </Button>
                  {parkedTransfers.length > 0 && (
                    <Button variant="outline" size="sm" onClick={() => setResumeDialogOpen(true)}
                      className="text-blue-700 border-blue-300 hover:bg-blue-50"
                      data-testid="resume-park-btn">
                      <FolderOpen size={13} className="mr-1" /> Resume <span className="ml-1 text-[10px] bg-blue-600 text-white rounded-full px-1.5">{parkedTransfers.length}</span>
                    </Button>
                  )}
                </div>

                <div className="flex items-center gap-6 text-sm">
                  <div className="text-right">
                    <p className="text-[10px] text-slate-400 uppercase tracking-wider">Our Cost</p>
                    <p className="font-mono font-bold text-slate-700">{formatPHP(totalBranchCapital)}</p>
                  </div>
                  <ArrowRight size={14} className="text-slate-400" />
                  <div className="text-right">
                    <p className="text-[10px] text-slate-400 uppercase tracking-wider">Transfer Price</p>
                    <p className="font-mono font-bold text-blue-700">{formatPHP(totalTransfer)}</p>
                  </div>
                  <ArrowRight size={14} className="text-slate-400" />
                  <div className="text-right">
                    <p className="text-[10px] text-slate-400 uppercase tracking-wider">Branch Retail</p>
                    <p className="font-mono font-bold text-emerald-700">{formatPHP(totalRetail)}</p>
                  </div>
                  <Button onClick={editingOrderId ? handleUpdateDraft : () => handleSubmit()}
                    disabled={saving || !toBranchId || !validRows.length}
                    className="bg-[#1A4D2E] hover:bg-[#14532d] text-white ml-4"
                    data-testid="create-transfer-btn">
                    {saving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Send size={14} className="mr-1.5" />}
                    {editingOrderId ? 'Save Draft' : 'Create Transfer Order'}
                  </Button>
                  {!editingOrderId && (
                    <Button onClick={() => handleSubmit({ requiresApproval: true })}
                      disabled={saving || !toBranchId || !validRows.length}
                      variant="outline"
                      className="ml-2 border-amber-400 text-amber-700 hover:bg-amber-50"
                      data-testid="submit-for-approval-btn"
                      title="Save the order without retail prices and notify Admin via SMS to approve. Useful when manager doesn't have permission to set retail."
                    >
                      <Shield size={14} className="mr-1.5" /> Submit for Approval
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}

          {!toBranchId && (
            <div className="text-center py-16 text-slate-400">
              <Building2 size={40} className="mx-auto mb-3 opacity-30" />
              <p>{!fromBranchId ? 'Select a source branch first.' : 'Select a destination branch to start building the transfer order.'}</p>
            </div>
          )}
        </TabsContent>


        {/* ── REQUEST STOCK TAB ── */}
        <TabsContent value="request" className="mt-4 space-y-4">
          <Card className="border-slate-200">
            <CardContent className="p-5 space-y-4">
              <h3 className="text-base font-semibold text-slate-800" style={{ fontFamily: 'Manrope' }}>Request Stock from Another Branch</h3>
              <p className="text-xs text-slate-500 -mt-2">Send a stock request to another branch. They&apos;ll receive a notification and can generate a transfer.</p>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-medium text-slate-500">Your Branch</label>
                  <Input className="mt-1 h-9 bg-slate-50" value={currentBranch?.name || '—'} disabled />
                </div>
                <div>
                  <label className="text-xs font-medium text-slate-500">Request From Branch <span className="text-red-500">*</span></label>
                  <Select value={reqTargetBranch} onValueChange={setReqTargetBranch}>
                    <SelectTrigger className="mt-1 h-9" data-testid="req-target-branch">
                      <SelectValue placeholder="Select branch..." />
                    </SelectTrigger>
                    <SelectContent>
                      {(branches || []).filter(b => b.id !== currentBranch?.id).map(b => (
                        <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* Show retail toggle */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500">Show retail price suggestion to supply branch:</span>
                <button onClick={() => setReqShowRetail(v => !v)}
                  className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${reqShowRetail ? 'bg-[#1A4D2E]' : 'bg-slate-300'}`}>
                  <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform mt-0.5 ${reqShowRetail ? 'translate-x-4.5' : 'translate-x-0.5'}`} />
                </button>
                <span className="text-[10px] font-medium text-slate-600">{reqShowRetail ? 'ON' : 'OFF'}</span>
              </div>

              {/* Product rows */}
              <div className="space-y-2">
                <div className="grid grid-cols-[1fr_100px_60px_40px] gap-2 text-xs font-medium text-slate-500 px-1">
                  <span>Product</span><span>Qty</span><span>Unit</span><span></span>
                </div>
                {reqRows.map((row) => {
                  const targetBranchObj = branches.find(b => b.id === reqTargetBranch);
                  return (
                  <div key={row.id} className="grid grid-cols-[1fr_100px_60px_40px] gap-2 items-start" data-testid={`req-row-${row.id}`}>
                    <div className="relative">
                      <Input
                        className="h-9 text-sm"
                        value={row.search}
                        onChange={e => handleReqSearch(row.id, e.target.value)}
                        placeholder="Search product..."
                        data-testid={`req-product-search-${row.id}`}
                        ref={el => { reqDropdownRefs.current[row.id] = el; }}
                      />
                      <AnchoredDropdown
                        anchor={reqDropdownRefs.current[row.id]}
                        open={(row.matches?.length || 0) > 0}
                        minWidth={300}
                      >
                        {(row.matches || []).map(p => (
                            <button key={p.id} onClick={() => selectReqProduct(row.id, p)}
                              className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 border-b border-slate-50 last:border-0"
                              data-testid={`req-match-${p.id}`}>
                              <div className="flex items-center justify-between">
                                <span className="truncate font-medium">{p.name}</span>
                                <span className="text-[10px] text-slate-400 ml-2 shrink-0 font-mono">{p.sku || ''}</span>
                              </div>
                              <div className="flex gap-3 mt-0.5">
                                {p.also_branch_stock !== undefined && (
                                  <span className={`text-[10px] font-medium ${p.also_branch_stock > 0 ? 'text-amber-600' : 'text-red-500'}`}>
                                    You: {fmtQty(p.also_branch_stock)} {p.unit || ''}
                                  </span>
                                )}
                                <span className={`text-[10px] font-medium ${(p.available || 0) > 0 ? 'text-emerald-600' : 'text-slate-400'}`}>
                                  {targetBranchObj?.name || 'Supplier'}: {fmtQty(p.available)} {p.unit || ''}
                                </span>
                              </div>
                            </button>
                          ))}
                      </AnchoredDropdown>
                      {row.product && (
                        <div className="flex items-center gap-3 mt-0.5 px-1">
                          <span className="text-[10px] text-slate-400">{row.product.sku || ''} {row.product.category ? `· ${row.product.category}` : ''}</span>
                          {row.product.also_branch_stock !== undefined && (
                            <span className={`text-[10px] font-semibold ${row.product.also_branch_stock > 0 ? 'text-amber-600' : 'text-red-500'}`}>
                              Your stock: {fmtQty(row.product.also_branch_stock)}
                            </span>
                          )}
                          <span className={`text-[10px] font-semibold ${(row.product.available || 0) > 0 ? 'text-emerald-600' : 'text-slate-400'}`}>
                            {targetBranchObj?.name || 'Supplier'}: {fmtQty(row.product.available)}
                          </span>
                        </div>
                      )}
                    </div>
                    <CalcInput className="h-9 text-sm text-center"
 value={row.qty}
 onChange={(v) => updateReqRow(row.id, { qty: v })}
 placeholder="0"
 data-testid={`req-qty-${row.id}`} />
                    <span className="h-9 flex items-center text-xs text-slate-500 px-1">{row.product?.unit || '—'}</span>
                    <Button variant="ghost" size="sm" className="h-9 w-9 p-0 text-slate-400 hover:text-red-500"
                      onClick={() => removeReqRow(row.id)} disabled={reqRows.length <= 1}>
                      <X size={14} />
                    </Button>
                  </div>
                  );
                })}
                <Button variant="outline" size="sm" className="h-8 text-xs w-full border-dashed" onClick={addReqRow} data-testid="req-add-row">
                  <Plus size={12} className="mr-1" /> Add Product
                </Button>
              </div>

              {/* Notes */}
              <div>
                <label className="text-xs font-medium text-slate-500">Notes (optional)</label>
                <Input className="mt-1 h-9 text-sm" value={reqNotes} onChange={e => setReqNotes(e.target.value)}
                  placeholder="e.g. Urgent — need by Friday" />
              </div>

              {/* Summary & Submit */}
              <div className="flex items-center justify-between pt-2 border-t border-slate-100">
                <div className="text-sm text-slate-600">
                  <span className="font-medium">{reqRows.filter(r => r.product).length}</span> product(s) ·{' '}
                  <span className="font-medium">{reqRows.filter(r => r.product).reduce((s, r) => s + (parseFloat(r.qty) || 0), 0)}</span> total units
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" className="h-10 px-4" onClick={resetReqForm}>Clear</Button>
                  <Button size="sm" className="h-10 px-6 bg-blue-600 hover:bg-blue-700 text-white font-semibold"
                    onClick={handleSendRequest} disabled={reqSaving} data-testid="send-stock-request-btn">
                    {reqSaving ? <RefreshCw size={14} className="animate-spin mr-2" /> : <ArrowRight size={14} className="mr-2" />}
                    Send Stock Request
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>


        {/* ── TRANSFERS TAB ── */}
        <TabsContent value="history" className="mt-4 space-y-4">
          {/* Status filter pills */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex gap-2 overflow-x-auto pb-1">
              {[
                { key: 'all', label: 'All', dot: 'bg-slate-400' },
                { key: 'requests', label: 'Requests', dot: 'bg-violet-500' },
                { key: 'pending_approval', label: 'Pending Approval', dot: 'bg-amber-500' },
                { key: 'returned', label: 'Returned', dot: 'bg-rose-500' },
                { key: 'draft', label: 'Drafts', dot: 'bg-slate-400' },
                { key: 'in_transit', label: 'In Transit', dot: 'bg-blue-500' },
                { key: 'checking', label: 'Terminal', dot: 'bg-amber-500' },
                { key: 'pending', label: 'Needs Review', dot: 'bg-orange-500' },
                { key: 'completed', label: 'Completed', dot: 'bg-emerald-500' },
                { key: 'disputes', label: 'Disputes', dot: 'bg-red-500' },
              ].map(st => {
                const count = st.key === 'all' ? orders.length
                  : st.key === 'requests' ? (stockRequests.length + outgoingRequests.length)
                  : st.key === 'pending_approval' ? orders.filter(o => o.status === 'pending_approval').length
                  : st.key === 'returned' ? orders.filter(o => o.status === 'returned').length
                  : st.key === 'draft' ? orders.filter(o => o.status === 'draft').length
                  : st.key === 'in_transit' ? orders.filter(o => o.status === 'sent').length
                  : st.key === 'checking' ? orders.filter(o => o.status === 'sent_to_terminal').length
                  : st.key === 'pending' ? orders.filter(o => o.status === 'received_pending').length
                  : st.key === 'completed' ? orders.filter(o => o.status === 'received').length
                  : st.key === 'disputes' ? orders.filter(o => o.status === 'disputed').length
                  : 0;
                const isActive = historyTab === st.key;
                const needsAttention = (st.key === 'pending' || st.key === 'disputes' || st.key === 'pending_approval' || st.key === 'returned') && count > 0;
                return (
                  <button key={st.key}
                    onClick={() => { setHistoryTab(st.key); if (st.key === 'requests') { loadRequests(); loadOutgoingRequests(); } }}
                    className={`flex items-center gap-2 px-4 py-2.5 rounded-full text-sm font-semibold transition-all whitespace-nowrap ${
                      isActive
                        ? 'bg-[#1A4D2E] text-white shadow-lg scale-105'
                        : needsAttention
                        ? 'bg-white text-orange-700 border-2 border-orange-400 hover:border-orange-500 shadow-sm'
                        : 'bg-white text-slate-600 border border-slate-200 hover:border-slate-400 hover:shadow-sm'
                    }`}
                    data-testid={`filter-${st.key}`}
                  >
                    <span className={`w-2.5 h-2.5 rounded-full ${isActive ? 'bg-white' : st.dot}`} />
                    {st.label}
                    {count > 0 && (
                      <span className={`text-xs font-bold min-w-[22px] text-center px-1.5 py-0.5 rounded-full ${
                        isActive ? 'bg-white/25 text-white' : needsAttention ? 'bg-orange-100 text-orange-700' : 'bg-slate-100 text-slate-600'
                      }`}>{count}</span>
                    )}
                  </button>
                );
              })}
            </div>
            <Button variant="outline" size="default" onClick={() => { loadOrders(); loadRequests(); loadOutgoingRequests(); }} disabled={ordersLoading} className="shrink-0 h-10 px-4">
              <RefreshCw size={15} className={`mr-2 ${ordersLoading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
          </div>

          {/* Content area */}
          {(() => {
            const effectiveBranchId = currentBranch?.id || user?.branch_id || '';

            // ── Stock Requests ──
            if (historyTab === 'requests') {
              const isIncoming = requestsView === 'incoming';
              const currentList = isIncoming ? stockRequests : outgoingRequests;
              const isLoading = isIncoming ? requestsLoading : outgoingLoading;
              return (
                <div className="space-y-3" data-testid="requests-list">
                  {/* Incoming / Outgoing toggle */}
                  <div className="flex items-center gap-2 pb-2">
                    <div className="flex bg-slate-100 rounded-lg p-0.5 gap-0.5">
                      <button onClick={() => { setRequestsView('incoming'); loadRequests(); }}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${isIncoming ? 'bg-white shadow-sm text-violet-700' : 'text-slate-500 hover:text-slate-700'}`}
                        data-testid="requests-incoming-tab">
                        Incoming ({stockRequests.length})
                      </button>
                      <button onClick={() => { setRequestsView('outgoing'); loadOutgoingRequests(); }}
                        className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${!isIncoming ? 'bg-white shadow-sm text-blue-700' : 'text-slate-500 hover:text-slate-700'}`}
                        data-testid="requests-outgoing-tab">
                        My Requests ({outgoingRequests.length})
                      </button>
                    </div>
                    <span className="text-xs text-slate-400 ml-2">
                      {isIncoming ? 'Requests from other branches for your stock' : 'Requests you sent to other branches'}
                    </span>
                  </div>

                  {isLoading && <div className="text-center py-12 text-slate-400"><RefreshCw size={18} className="animate-spin mx-auto mb-2" />Loading requests...</div>}
                  {!isLoading && currentList.length === 0 && (
                    <div className="text-center py-16 text-slate-400">
                      <Package size={36} className="mx-auto mb-3 opacity-30" />
                      <p className="text-sm">{isIncoming ? 'No incoming stock requests.' : 'You haven\'t sent any stock requests.'}</p>
                      {!isIncoming && <Button size="sm" variant="outline" className="mt-3" onClick={() => setTab('request')}>
                        <Plus size={12} className="mr-1" /> Create Stock Request
                      </Button>}
                    </div>
                  )}

                  {/* Incoming requests */}
                  {isIncoming && stockRequests.map(req => {
                    const reqBranch = branches.find(b => b.id === req.branch_id);
                    return (
                      <div key={req.id} className="bg-white rounded-xl border-2 border-slate-200 border-l-[5px] border-l-violet-400 p-5 hover:shadow-lg transition-all" data-testid={`request-card-${req.id}`}>
                        <div className="flex items-start justify-between">
                          <div>
                            <div className="flex items-center gap-2.5">
                              <span className="font-mono text-base font-bold text-violet-700">{req.po_number}</span>
                              <Badge className={`text-xs px-2.5 py-0.5 ${
                                req.status === 'requested' ? 'bg-blue-100 text-blue-700'
                                : req.status === 'fulfilled' ? 'bg-emerald-100 text-emerald-700'
                                : req.status === 'partially_fulfilled' ? 'bg-yellow-100 text-yellow-700'
                                : req.status === 'in_progress' ? 'bg-amber-100 text-amber-700'
                                : 'bg-slate-100 text-slate-600'
                              }`}>{req.status?.replace('_',' ')}</Badge>
                            </div>
                            <p className="text-sm text-slate-500 mt-2 flex items-center gap-2">
                              <Building2 size={14} className="text-slate-400" />
                              <span className="font-semibold text-slate-700">{reqBranch?.name || 'Unknown'}</span>
                              <span className="text-slate-400">requested stock</span>
                            </p>
                          </div>
                          <span className="text-sm text-slate-400">{req.purchase_date}</span>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {(req.items || []).slice(0, 4).map((item, i) => (
                            <span key={i} className="text-sm bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 text-slate-600">
                              {item.product_name} <span className="text-slate-400 font-mono">x{item.quantity}</span>
                            </span>
                          ))}
                          {req.items?.length > 4 && <span className="text-sm text-slate-400 self-center">+{req.items.length - 4} more</span>}
                        </div>
                        {req.notes && <p className="text-sm text-slate-500 mt-2 italic">&quot;{req.notes}&quot;</p>}
                        <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-end gap-2">
                          {(req.status === 'requested' || req.status === 'draft') && (
                            <Button size="sm" onClick={() => handleGenerateTransfer(req)} disabled={generatingTransfer === req.id}
                              className="h-10 px-5 bg-[#1A4D2E] hover:bg-[#14532d] text-white text-sm font-semibold" data-testid={`gen-transfer-${req.id}`}>
                              {generatingTransfer === req.id ? <RefreshCw size={14} className="animate-spin mr-2" /> : <ArrowRight size={14} className="mr-2" />}
                              Generate Transfer
                            </Button>
                          )}
                          {req.status === 'fulfilled' && (
                            <div className="flex items-center gap-2">
                              <CheckCircle2 size={16} className="text-emerald-600" />
                              <span className="text-sm text-emerald-700 font-semibold">Fulfilled</span>
                              {req.fulfilled_transfer_number && <span className="text-xs text-slate-400">({req.fulfilled_transfer_number})</span>}
                            </div>
                          )}
                          {req.status === 'partially_fulfilled' && (
                            <div className="flex items-center gap-2">
                              <AlertTriangle size={16} className="text-yellow-600" />
                              <span className="text-sm text-yellow-700 font-semibold">Partial</span>
                              {req.fulfilled_transfer_number && <span className="text-xs text-slate-400">({req.fulfilled_transfer_number})</span>}
                            </div>
                          )}
                          {req.status === 'in_progress' && <Badge className="text-xs px-2.5 py-1 bg-amber-100 text-amber-700">Transfer In Progress</Badge>}
                        </div>
                      </div>
                    );
                  })}

                  {/* Outgoing requests */}
                  {!isIncoming && outgoingRequests.map(req => {
                    const supplyBranch = branches.find(b => b.id === req.supply_branch_id);
                    return (
                      <div key={req.id} className="bg-white rounded-xl border-2 border-slate-200 border-l-[5px] border-l-blue-400 p-5 hover:shadow-lg transition-all" data-testid={`outgoing-request-${req.id}`}>
                        <div className="flex items-start justify-between">
                          <div>
                            <div className="flex items-center gap-2.5">
                              <span className="font-mono text-base font-bold text-blue-700">{req.po_number}</span>
                              <Badge className={`text-xs px-2.5 py-0.5 ${
                                req.status === 'requested' ? 'bg-blue-100 text-blue-700'
                                : req.status === 'fulfilled' ? 'bg-emerald-100 text-emerald-700'
                                : req.status === 'partially_fulfilled' ? 'bg-yellow-100 text-yellow-700'
                                : req.status === 'in_progress' ? 'bg-amber-100 text-amber-700'
                                : 'bg-slate-100 text-slate-600'
                              }`}>{req.status?.replace('_',' ')}</Badge>
                            </div>
                            <p className="text-sm text-slate-500 mt-2 flex items-center gap-2">
                              <ArrowRight size={14} className="text-blue-400" />
                              <span className="text-slate-400">Requested from</span>
                              <span className="font-semibold text-slate-700">{supplyBranch?.name || 'Unknown'}</span>
                            </p>
                          </div>
                          <span className="text-sm text-slate-400">{req.purchase_date}</span>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {(req.items || []).slice(0, 4).map((item, i) => (
                            <span key={i} className="text-sm bg-blue-50 border border-blue-100 rounded-lg px-3 py-1.5 text-slate-600">
                              {item.product_name} <span className="text-slate-400 font-mono">x{item.quantity}</span>
                            </span>
                          ))}
                          {req.items?.length > 4 && <span className="text-sm text-slate-400 self-center">+{req.items.length - 4} more</span>}
                        </div>
                        {req.notes && <p className="text-sm text-slate-500 mt-2 italic">&quot;{req.notes}&quot;</p>}
                        <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between">
                          <span className="text-xs text-slate-400">by {req.created_by_name} · {fmtDate(req.created_at)}</span>
                          <div className="flex items-center gap-2">
                            {req.status === 'fulfilled' && (
                              <div className="flex items-center gap-2">
                                <CheckCircle2 size={16} className="text-emerald-600" />
                                <span className="text-sm text-emerald-700 font-semibold">Fulfilled</span>
                                {req.fulfilled_transfer_number && <span className="text-xs text-slate-400">({req.fulfilled_transfer_number})</span>}
                              </div>
                            )}
                            {req.status === 'partially_fulfilled' && (
                              <div className="flex items-center gap-2">
                                <AlertTriangle size={16} className="text-yellow-600" />
                                <span className="text-sm text-yellow-700 font-semibold">Partial</span>
                                {req.fulfilled_transfer_number && <span className="text-xs text-slate-400">({req.fulfilled_transfer_number})</span>}
                              </div>
                            )}
                            {req.status === 'in_progress' && <Badge className="text-xs px-2.5 py-1 bg-amber-100 text-amber-700">Transfer In Progress</Badge>}
                            {req.status === 'requested' && <Badge className="text-xs px-2.5 py-1 bg-blue-100 text-blue-700">Awaiting Response</Badge>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            }

            // ── Transfer Cards ──
            const statusFilter = {
              all: () => true,
              pending_approval: o => o.status === 'pending_approval',
              returned: o => o.status === 'returned',
              draft: o => o.status === 'draft',
              in_transit: o => o.status === 'sent',
              checking: o => o.status === 'sent_to_terminal',
              pending: o => o.status === 'received_pending',
              completed: o => o.status === 'received',
              disputes: o => o.status === 'disputed',
            };
            const filterFn = statusFilter[historyTab] || statusFilter.all;
            const filteredOrders = orders.filter(filterFn).sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

            const borderColorMap = {
              draft: 'border-l-slate-300',
              pending_approval: 'border-l-amber-500',
              returned: 'border-l-rose-500',
              sent: 'border-l-blue-500',
              sent_to_terminal: 'border-l-amber-500',
              received_pending: 'border-l-orange-500',
              received: 'border-l-emerald-500',
              disputed: 'border-l-red-500',
              cancelled: 'border-l-red-300',
            };
            const statusLabel = {
              draft: 'Draft',
              pending_approval: 'Pending Approval',
              returned: 'Returned',
              sent: 'In Transit',
              sent_to_terminal: 'On Terminal',
              received_pending: 'Pending Review',
              received: 'Completed',
              disputed: 'Disputed',
              cancelled: 'Cancelled',
            };

            if (ordersLoading) return (
              <div className="text-center py-20 text-slate-400">
                <RefreshCw size={28} className="animate-spin mx-auto mb-4" />
                <p className="text-base">Loading transfers...</p>
              </div>
            );

            if (filteredOrders.length === 0) return (
              <div className="text-center py-20 text-slate-400">
                <Building2 size={48} className="mx-auto mb-4 opacity-30" />
                <p className="text-base">No transfers{historyTab !== 'all' ? ` in "${[...Object.entries(statusLabel)].find(([,v]) => v === (statusLabel[historyTab === 'in_transit' ? 'sent' : historyTab]))?.[1] || historyTab}"` : ''} found.</p>
              </div>
            );

            return (
              <div className="space-y-4" data-testid="transfers-list">
                {filteredOrders.map(o => {
                  const fromBr = branches.find(b => b.id === o.from_branch_id);
                  const toBr = branches.find(b => b.id === o.to_branch_id);
                  const isSourceBranch = o.from_branch_id === effectiveBranchId;
                  const isDestBranch = o.to_branch_id === effectiveBranchId;

                  // Timeline
                  const timelineSteps = [
                    { label: 'Created', done: true },
                    { label: 'Sent', done: ['sent','sent_to_terminal','received_pending','received','disputed'].includes(o.status) },
                    { label: o.status === 'sent_to_terminal' ? 'Terminal' : 'Checked',
                      done: ['sent_to_terminal','received_pending','received','disputed'].includes(o.status),
                      variant: o.status === 'sent_to_terminal' ? 'amber' : o.status === 'disputed' ? 'red' : o.status === 'received_pending' ? 'orange' : null },
                    { label: 'Complete', done: o.status === 'received' },
                  ];

                  return (
                    <div key={o.id}
                      className={`bg-white rounded-xl border-2 border-slate-200 border-l-[5px] ${borderColorMap[o.status] || 'border-l-slate-300'} overflow-hidden hover:shadow-xl transition-all duration-200`}
                      data-testid={`transfer-card-${o.id}`}
                    >
                      {/* Header */}
                      <div className="px-5 pt-4 pb-3 flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2.5 flex-wrap">
                            <span className="font-mono text-base font-bold text-slate-800 cursor-pointer hover:text-blue-700"
                              onClick={() => { setViewOrder(o); setReceiveDialog(false); }}>{o.order_number}</span>
                            {o.invoice_number && (
                              <span className="font-mono text-xs text-blue-600 bg-blue-50 border border-blue-200 rounded px-1.5 py-0.5" data-testid={`invoice-ref-${o.id}`}>
                                {o.invoice_number}
                              </span>
                            )}
                            <Badge className={`text-xs px-2.5 py-0.5 ${STATUS_COLORS[o.status]}`}>
                              {statusLabel[o.status] || o.status}
                            </Badge>
                            {o.has_shortage && <Badge className="text-xs px-2 py-0.5 bg-red-100 text-red-700">Shortage</Badge>}
                            {o.incident_ticket_number && (
                              <Badge className="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 cursor-pointer hover:bg-amber-200 transition-colors"
                                onClick={(e) => { e.stopPropagation(); navigate('/incident-tickets'); }}
                                data-testid={`ticket-badge-${o.id}`}>
                                <AlertTriangle size={10} className="mr-1" />{o.incident_ticket_number}
                              </Badge>
                            )}
                            {o.terminal_id && o.status === 'received' && (
                              <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">
                                <Smartphone size={11} /> Terminal verified
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mt-2">
                            <span className="text-base font-semibold text-slate-700">{fromBr?.name || '?'}</span>
                            <ArrowRight size={16} className="text-slate-400 shrink-0" />
                            <span className="text-base font-semibold text-slate-700">{toBr?.name || '?'}</span>
                            {isSourceBranch && (
                              <span className="inline-flex items-center gap-1 text-xs font-bold text-blue-700 bg-blue-50 border border-blue-200 rounded-full px-2.5 py-0.5 ml-1" data-testid={`role-sender-${o.id}`}>
                                <Send size={10} /> You: Sender
                              </span>
                            )}
                            {isDestBranch && !isSourceBranch && (
                              <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2.5 py-0.5 ml-1" data-testid={`role-receiver-${o.id}`}>
                                <CheckCircle2 size={10} /> You: Receiver
                              </span>
                            )}
                          </div>
                          {o.request_po_number && (
                            <p className="text-xs text-blue-500 mt-1 flex items-center gap-1">
                              <Package size={12} /> From request {o.request_po_number}
                            </p>
                          )}
                        </div>
                        <span className="text-sm text-slate-400 whitespace-nowrap shrink-0">{fmtDate(o.created_at)}</span>
                      </div>

                      {/* Returned reason banner */}
                      {o.status === 'returned' && o.rejection_reason && (
                        <div className="mx-5 mb-2 px-3 py-2 bg-rose-50 border border-rose-200 rounded-lg flex items-start gap-2" data-testid={`rejection-banner-${o.id}`}>
                          <AlertTriangle size={14} className="text-rose-600 mt-0.5 shrink-0" />
                          <div className="text-xs text-rose-700">
                            <span className="font-semibold">Returned by {o.rejected_by_name || 'admin'}:</span>{' '}
                            <span>{o.rejection_reason}</span>
                          </div>
                        </div>
                      )}

                      {/* Pending approval banner (shown to manager for context) */}
                      {o.status === 'pending_approval' && (
                        <div className="mx-5 mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg flex items-start gap-2">
                          <Clock size={14} className="text-amber-600 mt-0.5 shrink-0" />
                          <div className="text-xs text-amber-700">
                            <span className="font-semibold">Submitted for admin approval</span> — waiting for retail prices to be set and the order to be released.
                          </div>
                        </div>
                      )}

                      {/* Timeline */}
                      <div className="px-5 py-3 border-t border-slate-100">
                        <div className="flex items-center">
                          {timelineSteps.map((step, i) => {
                            const dotColor = step.done
                              ? (step.variant === 'amber' ? 'bg-amber-500' : step.variant === 'red' ? 'bg-red-500' : step.variant === 'orange' ? 'bg-orange-500' : 'bg-emerald-500')
                              : 'bg-slate-200';
                            return (
                              <React.Fragment key={i}>
                                <div className="flex flex-col items-center min-w-0">
                                  <div className={`w-3.5 h-3.5 rounded-full ${dotColor} shrink-0`} />
                                  <span className={`text-xs mt-1 truncate ${step.done ? 'text-slate-700 font-semibold' : 'text-slate-400'}`}>{step.label}</span>
                                </div>
                                {i < timelineSteps.length - 1 && (
                                  <div className={`flex-1 h-0.5 mx-1.5 rounded ${step.done && timelineSteps[i+1]?.done ? 'bg-emerald-400' : 'bg-slate-200'}`} />
                                )}
                              </React.Fragment>
                            );
                          })}
                        </div>
                      </div>

                      {/* Footer: financials + actions */}
                      <div className="px-5 py-3.5 bg-slate-50/80 border-t border-slate-100 flex items-center justify-between gap-3">
                        <div className="flex items-center gap-4 text-sm min-w-0">
                          <span className="text-slate-500 font-medium shrink-0">{o.items?.length || 0} items</span>
                          <Separator orientation="vertical" className="h-4" />
                          <span className="font-mono text-slate-700 font-semibold shrink-0">{formatPHP(o.total_at_transfer_capital)}</span>
                          <Separator orientation="vertical" className="h-4" />
                          <span className="font-mono text-emerald-600 font-bold shrink-0">{formatPHP(o.total_at_branch_retail)} retail</span>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <Button variant="ghost" size="sm" onClick={() => { setViewOrder(o); setReceiveDialog(false); }}
                            className="h-9 px-3 text-slate-500 hover:text-slate-800 text-sm" title="View" data-testid={`view-btn-${o.id}`}>
                            <Eye size={16} className="mr-1" /> View
                          </Button>
                          {['draft', 'sent', 'received', 'received_pending'].includes(o.status) && isSourceBranch && (
                            <Button variant="ghost" size="sm" onClick={() => printTransferOrder(o)}
                              className="h-9 px-3 text-slate-500 text-sm" title="Print Invoice" data-testid={`print-btn-${o.id}`}>
                              <FileText size={16} className="mr-1" /> Print
                            </Button>
                          )}
                          {o.status === 'draft' && isSourceBranch && (
                            <Button variant="ghost" size="sm" onClick={() => loadOrderIntoEdit(o)}
                              className="h-9 px-3 text-amber-600 text-sm" title="Edit Draft" data-testid={`edit-btn-${o.id}`}>
                              <Pencil size={16} className="mr-1" /> Edit
                            </Button>
                          )}
                          {o.status === 'draft' && isSourceBranch && (
                            <Button size="sm" onClick={() => handleSend(o.id)}
                              className="h-9 px-4 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold" data-testid={`send-btn-${o.id}`}>
                              <Send size={14} className="mr-1.5" /> Send
                            </Button>
                          )}
                          {/* Pending approval — Admin can review-and-approve / Manager just sees the badge */}
                          {o.status === 'pending_approval' && isAdmin && (
                            <Button size="sm" onClick={() => navigate(`/approve-transfer/${o.id}`)}
                              className="h-9 px-4 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold" data-testid={`approve-btn-${o.id}`}>
                              <Shield size={14} className="mr-1.5" /> Review & Approve
                            </Button>
                          )}
                          {o.status === 'pending_approval' && !isAdmin && (
                            <Badge className="h-9 px-3 bg-amber-50 text-amber-700 border border-amber-200 text-xs flex items-center gap-1.5">
                              <Clock size={12} /> Awaiting Admin
                            </Badge>
                          )}
                          {/* Returned — Manager can edit + resubmit */}
                          {o.status === 'returned' && isSourceBranch && (
                            <>
                              <Button variant="ghost" size="sm" onClick={() => loadOrderIntoEdit(o)}
                                className="h-9 px-3 text-amber-600 text-sm" title="Edit & Fix" data-testid={`edit-returned-btn-${o.id}`}>
                                <Pencil size={16} className="mr-1" /> Edit
                              </Button>
                              <Button size="sm" onClick={() => handleResubmit(o.id)}
                                className="h-9 px-4 bg-rose-600 hover:bg-rose-700 text-white text-sm font-semibold" data-testid={`resubmit-btn-${o.id}`}>
                                <RefreshCw size={14} className="mr-1.5" /> Resubmit
                              </Button>
                            </>
                          )}
                          {o.status === 'sent' && isDestBranch && (
                            <Button size="sm" onClick={() => openReceive(o)}
                              className="h-9 px-4 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold" data-testid={`receive-btn-${o.id}`}>
                              <CheckCircle2 size={14} className="mr-1.5" /> Receive
                            </Button>
                          )}
                          {o.status === 'sent' && isDestBranch && (
                            <Button variant="ghost" size="sm" onClick={() => sendTransferToTerminal(o.id)}
                              className="h-9 px-3 text-amber-600 text-sm" title="Send to Terminal" data-testid={`send-terminal-btn-${o.id}`}>
                              <Smartphone size={16} className="mr-1" /> Terminal
                            </Button>
                          )}
                          {o.status === 'sent_to_terminal' && (
                            <Badge className="text-xs px-2.5 py-1 bg-amber-100 text-amber-700 flex items-center gap-1.5">
                              <Lock size={12} /> On Terminal
                            </Badge>
                          )}
                          {o.status === 'disputed' && isDestBranch && (
                            <Button size="sm" onClick={() => openReceive(o)}
                              className="h-9 px-4 bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold" data-testid={`resubmit-btn-${o.id}`}>
                              <RefreshCw size={14} className="mr-1.5" /> Re-count
                            </Button>
                          )}
                          {o.status === 'received_pending' && isSourceBranch && (
                            <>
                              <Button size="sm" onClick={() => setAcceptDialog(o)}
                                className="h-9 px-4 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold" data-testid={`accept-btn-${o.id}`}>
                                <CheckCircle2 size={14} className="mr-1.5" /> Accept
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => { setDisputeDialog(o); setDisputeNote(''); }}
                                className="h-9 px-3 text-red-600 border-red-300 hover:bg-red-50 text-sm font-semibold" data-testid={`dispute-btn-${o.id}`}>
                                <XCircle size={14} className="mr-1.5" /> Dispute
                              </Button>
                            </>
                          )}
                          {(o.status === 'draft' || o.status === 'sent') && isSourceBranch && (
                            <Button variant="ghost" size="sm" onClick={() => handleCancel(o.id)}
                              className="h-9 px-3 text-red-400 hover:text-red-600 text-sm" title="Cancel" data-testid={`cancel-btn-${o.id}`}>
                              <XCircle size={16} />
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </TabsContent>
      </Tabs>

      {/* ── View / Receive Order Dialog ── */}
      <Dialog open={!!viewOrder && !receiveDialog} onOpenChange={() => setViewOrder(null)}>
        <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>
              {viewOrder?.order_number}
              {viewOrder?.invoice_number && (
                <span className="font-mono text-sm text-blue-600 bg-blue-50 border border-blue-200 rounded px-1.5 py-0.5 ml-2">{viewOrder.invoice_number}</span>
              )}
              <span className="text-slate-400 mx-1">—</span>
              {branches.find(b => b.id === viewOrder?.from_branch_id)?.name || '?'}
              <span className="text-slate-400 mx-1">→</span>
              {branches.find(b => b.id === viewOrder?.to_branch_id)?.name || '?'}
              <Badge className={`ml-2 text-[10px] ${STATUS_COLORS[viewOrder?.status]}`}>{viewOrder?.status}</Badge>
              {viewOrder?.has_shortage && (
                <Badge className="ml-2 text-[10px] bg-red-100 text-red-700">Shortage</Badge>
              )}
              {viewOrder?.incident_ticket_number && (
                <Badge className="ml-2 text-[10px] bg-amber-100 text-amber-700">{viewOrder.incident_ticket_number}</Badge>
              )}
            </DialogTitle>
            <Button size="sm" variant="outline" className="h-7 text-xs mt-1"
              onClick={() => { setBtUploadOrderId(viewOrder?.id); setBtUploadQROpen(true); }}>
              <Upload size={12} className="mr-1" /> Upload DR / Proof
            </Button>
            <div className="flex gap-2 mt-1">
              <Button size="sm" variant="outline" className="h-7 text-xs bg-slate-800 text-white border-slate-600 hover:bg-slate-700"
                onClick={() => { setBtViewQROpen(true); }}>
                <span className="mr-1">📱</span> View on Phone
              </Button>
              {viewOrder && !viewOrder.verified && (isAdmin || user?.is_auditor) && (
                <Button size="sm" variant="outline" className="h-7 text-xs text-[#1A4D2E] border-[#1A4D2E]/40 hover:bg-[#1A4D2E]/10"
                  onClick={() => { setBtVerifyId(viewOrder?.id); setBtVerifyOpen(true); }}>
                  <CheckCircle2 size={12} className="mr-1" /> Verify
                </Button>
              )}
              {viewOrder?.verified && <VerificationBadge doc={viewOrder} />}
            </div>
          </DialogHeader>

          {/* ── Status Timeline ── */}
          {viewOrder && (() => {
            const status = viewOrder.status;
            const order_status = status;
            const steps = [
              { key: 'requested', label: 'Requested', done: true, date: fmtDate(viewOrder.created_at), by: viewOrder.created_by_name },
              { key: 'draft', label: 'Transfer Created', done: ['draft','sent','received_pending','received','disputed'].includes(status), date: fmtDate(viewOrder.created_at), by: viewOrder.created_by_name },
              { key: 'sent', label: order_status === 'sent_to_terminal' ? 'On Terminal' : 'Sent',
                done: ['sent','sent_to_terminal','received_pending','received','disputed'].includes(status), date: fmtDate(viewOrder.sent_at),
                variant: status === 'sent_to_terminal' ? 'warning' : undefined },
              { key: 'received', label: status === 'received_pending' ? 'Pending Review' : status === 'disputed' ? 'Disputed' : 'Received',
                done: ['received_pending','received','disputed'].includes(status),
                date: fmtDate(viewOrder.received_at) || fmtDate(viewOrder.pending_receipt_at),
                by: viewOrder.received_by_name || viewOrder.pending_receipt_by_name,
                variant: status === 'disputed' ? 'error' : status === 'received_pending' ? 'warning' : 'success' },
              { key: 'settled', label: 'Settled', done: status === 'received' && !viewOrder.has_shortage, date: fmtDate(viewOrder.received_at) },
            ];
            // Filter out "requested" if not from a stock request
            const filteredSteps = viewOrder.request_po_id ? steps : steps.filter(s => s.key !== 'requested');
            const currentIdx = filteredSteps.findIndex(s => !s.done) - 1;
            return (
              <div className="flex items-center gap-0 px-2 py-2 mb-1 bg-slate-50 rounded-lg overflow-x-auto" data-testid="transfer-timeline">
                {filteredSteps.map((step, i) => {
                  const isActive = i === currentIdx || (i === filteredSteps.length - 1 && step.done);
                  const variantColors = {
                    error: 'bg-red-500', warning: 'bg-amber-500', success: 'bg-emerald-500',
                  };
                  const dotColor = step.done
                    ? (step.variant ? variantColors[step.variant] : 'bg-emerald-500')
                    : 'bg-slate-300';
                  const lineColor = step.done ? 'bg-emerald-300' : 'bg-slate-200';
                  return (
                    <div key={step.key} className="flex items-center min-w-0 shrink-0">
                      <div className="flex flex-col items-center">
                        <div className={`w-2.5 h-2.5 rounded-full ${dotColor} ${isActive ? 'ring-2 ring-offset-1 ring-emerald-400' : ''}`} />
                        <p className={`text-[10px] font-medium mt-0.5 ${isActive ? 'text-slate-700' : 'text-slate-400'}`}>{step.label}</p>
                        {step.date && <p className="text-[9px] text-slate-400">{step.date}</p>}
                      </div>
                      {i < filteredSteps.length - 1 && (
                        <div className={`flex-1 h-0.5 mx-1 ${lineColor} rounded`} />
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })()}

          {/* Request reference */}
          {viewOrder?.request_po_id && (
            <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded-lg text-xs">
              <Package size={13} className="text-blue-600" />
              <span className="text-blue-700">From stock request: <b>{viewOrder.request_po_number}</b></span>
            </div>
          )}

          {/* Incident ticket link */}
          {viewOrder?.incident_ticket_number && (
            <div className="flex items-center justify-between px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs cursor-pointer hover:bg-amber-100 transition-colors"
              onClick={() => navigate('/incident-tickets')} data-testid="transfer-ticket-link">
              <div className="flex items-center gap-2">
                <AlertTriangle size={14} className="text-amber-600" />
                <span className="text-amber-800">Incident Ticket: <b>{viewOrder.incident_ticket_number}</b></span>
              </div>
              <ArrowRight size={14} className="text-amber-500" />
            </div>
          )}

          {/* ── Dispute / Variance History Timeline ── */}
          {viewOrder && (viewOrder.dispute_note || viewOrder.has_shortage || viewOrder.has_excess || viewOrder.pending_receipt_at) && (() => {
            const events = [];
            // First count (pending receipt)
            if (viewOrder.pending_receipt_at) {
              events.push({
                type: 'counted',
                label: 'First Count Submitted',
                by: viewOrder.pending_receipt_by_name,
                at: viewOrder.pending_receipt_at,
                note: viewOrder.receive_notes,
                icon: 'count',
                shortages: viewOrder.shortages,
                excesses: viewOrder.excesses,
              });
            }
            // Dispute (if any)
            if (viewOrder.dispute_note || viewOrder.disputed_at) {
              events.push({
                type: 'disputed',
                label: 'Disputed by Source',
                by: viewOrder.disputed_by_name,
                at: viewOrder.disputed_at,
                note: viewOrder.dispute_note,
                icon: 'dispute',
              });
            }
            // Re-count (if disputed then re-received)
            if (viewOrder.disputed_at && viewOrder.received_at && viewOrder.received_at > viewOrder.disputed_at) {
              events.push({
                type: 're-counted',
                label: 'Re-count Submitted',
                by: viewOrder.received_by_name || viewOrder.pending_receipt_by_name,
                at: viewOrder.received_at,
                icon: 'recount',
              });
            }
            // Final acceptance
            if (viewOrder.accepted_at) {
              events.push({
                type: 'accepted',
                label: viewOrder.incident_ticket_number ? 'Accepted + Incident Created' : 'Variance Accepted',
                by: viewOrder.accepted_by_name,
                at: viewOrder.accepted_at,
                note: viewOrder.accept_note,
                icon: 'accept',
                ticket: viewOrder.incident_ticket_number,
              });
            }

            if (!events.length) return null;
            return (
              <div className="bg-slate-50 rounded-lg border border-slate-200 p-3 space-y-0" data-testid="dispute-history">
                <p className="text-[11px] font-semibold text-slate-600 mb-2 flex items-center gap-1.5">
                  <Clock size={12} className="text-slate-400" /> Variance History
                </p>
                <div className="relative pl-4 space-y-3">
                  <div className="absolute left-[7px] top-1 bottom-1 w-px bg-slate-300" />
                  {events.map((ev, i) => {
                    const colors = {
                      counted: 'bg-blue-500',
                      dispute: 'bg-red-500',
                      recount: 'bg-amber-500',
                      accept: 'bg-emerald-500',
                    };
                    return (
                      <div key={i} className="relative">
                        <div className={`absolute -left-4 top-0.5 w-2.5 h-2.5 rounded-full ${colors[ev.icon] || 'bg-slate-400'} ring-2 ring-white`} />
                        <div className="ml-2">
                          <p className="text-xs font-semibold text-slate-700">{ev.label}</p>
                          <p className="text-[10px] text-slate-500">
                            {ev.by && <span>{ev.by} · </span>}
                            {fmtDateTime(ev.at)}
                          </p>
                          {ev.note && <p className="text-[10px] text-slate-500 mt-0.5 italic">&quot;{ev.note}&quot;</p>}
                          {ev.ticket && (
                            <button onClick={() => navigate('/incident-tickets')} className="text-[10px] text-amber-700 font-medium mt-0.5 hover:underline flex items-center gap-1">
                              <AlertTriangle size={10} /> {ev.ticket}
                            </button>
                          )}
                          {ev.shortages && ev.shortages.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {ev.shortages.map((s, j) => (
                                <span key={j} className="text-[10px] bg-amber-100 text-amber-700 rounded px-1.5 py-0.5">
                                  {s.product_name}: -{s.variance} {s.unit}
                                </span>
                              ))}
                            </div>
                          )}
                          {ev.excesses && ev.excesses.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {ev.excesses.map((s, j) => (
                                <span key={j} className="text-[10px] bg-blue-100 text-blue-700 rounded px-1.5 py-0.5">
                                  {s.product_name}: +{Math.abs(s.variance)} {s.unit}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* Invoice reference */}
          {viewOrder?.invoice_number && (
            <div className="flex items-center justify-between px-3 py-1.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs">
              <span className="text-emerald-700 flex items-center gap-2">
                <ClipboardCheck size={13} className="text-emerald-600" />
                Invoice: <b>{viewOrder.invoice_number}</b>
                <span className="text-slate-400">|</span>
                Terms: Net 15
              </span>
              <Button variant="ghost" size="sm" className="h-6 text-xs text-emerald-700"
                onClick={() => printTransferOrder(viewOrder)}>
                Print Invoice
              </Button>
            </div>
          )}

          <ScrollArea className="flex-1">
            {/* Reconciliation view for received AND received_pending orders */}
            {(viewOrder?.status === 'received' || viewOrder?.status === 'received_pending') ? (
              <div className="space-y-3">
                {viewOrder?.status === 'received_pending' && (
                  <div className="p-3 rounded-lg bg-amber-50 border border-amber-300 text-sm text-amber-800">
                    <p className="font-semibold flex items-center gap-1.5">
                      <AlertTriangle size={14} /> Pending Your Review
                    </p>
                    <p className="text-xs mt-1">
                      {branches.find(b => b.id === viewOrder?.to_branch_id)?.name || 'Destination'} submitted received quantities with a variance.
                      Review the comparison below and Accept or Dispute.
                    </p>
                    {viewOrder.receive_notes && (
                      <p className="text-xs mt-1 text-amber-600">Receiver's note: "{viewOrder.receive_notes}"</p>
                    )}
                  </div>
                )}
                <div className="text-xs text-slate-500 bg-slate-50 rounded px-3 py-2 flex justify-between">
                  <span>
                    {viewOrder?.status === 'received_pending'
                      ? <>Counted by: <b>{viewOrder.pending_receipt_by_name}</b> · {fmtDateTime(viewOrder.pending_receipt_at)}</>
                      : <>Received by: <b>{viewOrder.received_by_name}</b> · {fmtDate(viewOrder.received_at)}</>
                    }
                  </span>
                  {viewOrder.has_shortage && (
                    <span className="text-red-600 font-medium flex items-center gap-1">
                      <AlertTriangle size={12}/> {viewOrder.shortages?.length} product(s) short
                    </span>
                  )}
                </div>
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-white border-b">
                    <tr className="text-[10px] uppercase text-slate-500">
                      <th className="px-3 py-2 text-left">Product</th>
                      <th className="px-3 py-2 text-right">Ordered</th>
                      <th className="px-3 py-2 text-right">Received</th>
                      <th className="px-3 py-2 text-right font-semibold">Variance</th>
                      <th className="px-3 py-2 text-right">Capital/unit</th>
                      <th className="px-3 py-2 text-right text-red-600">Capital Loss</th>
                      <th className="px-3 py-2 text-right">Retail/unit</th>
                      <th className="px-3 py-2 text-right text-red-600">Retail Loss</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(viewOrder?.pending_items || viewOrder?.items || []).map((item, i) => {
                      const qtyOrdered = item.qty_ordered ?? item.qty;
                      const qtyReceived = item.qty_received ?? item.qty;
                      const variance = qtyOrdered - qtyReceived;
                      const capLoss = variance * item.transfer_capital;
                      const retLoss = variance * item.branch_retail;
                      const hasShortage = variance > 0;
                      return (
                        <tr key={i} className={`border-b border-slate-50 ${hasShortage ? 'bg-red-50/40' : 'hover:bg-slate-50'}`}>
                          <td className="px-3 py-2">
                            <p className="font-medium">{item.product_name}</p>
                            <p className="text-[10px] text-slate-400 font-mono">{item.sku} · {item.category}</p>
                          </td>
                          <td className="px-3 py-2 text-right font-mono">{qtyOrdered} {item.unit}</td>
                          <td className="px-3 py-2 text-right font-mono font-bold">{qtyReceived} {item.unit}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${variance > 0 ? 'text-red-600' : variance < 0 ? 'text-blue-600' : 'text-emerald-600'}`}>
                            {variance === 0 ? '✓ OK' : variance > 0 ? `-${variance}` : `+${Math.abs(variance)}`}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.transfer_capital)}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${capLoss > 0 ? 'text-red-600' : 'text-slate-300'}`}>
                            {capLoss > 0 ? `-${formatPHP(capLoss)}` : '—'}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.branch_retail)}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${retLoss > 0 ? 'text-red-600' : 'text-slate-300'}`}>
                            {retLoss > 0 ? `-${formatPHP(retLoss)}` : '—'}
                          </td>
                        </tr>
                      );
                    })}
                    {/* Totals row */}
                    {(() => {
                      const items = viewOrder?.pending_items || viewOrder?.items || [];
                      const totalCapLoss = items.reduce((s,i) => {
                        const v = (i.qty_ordered ?? i.qty) - (i.qty_received ?? i.qty);
                        return s + (v > 0 ? v * i.transfer_capital : 0);
                      }, 0);
                      const totalRetLoss = items.reduce((s,i) => {
                        const v = (i.qty_ordered ?? i.qty) - (i.qty_received ?? i.qty);
                        return s + (v > 0 ? v * i.branch_retail : 0);
                      }, 0);
                      return (
                        <tr className="bg-slate-100 font-bold border-t-2 border-slate-300 text-sm">
                          <td className="px-3 py-2" colSpan={5}>Expected Losses (shortage)</td>
                          <td className="px-3 py-2 text-right font-mono text-red-700">{totalCapLoss > 0 ? `-${formatPHP(totalCapLoss)}` : '₱0.00'}</td>
                          <td className="px-3 py-2"></td>
                          <td className="px-3 py-2 text-right font-mono text-red-700">{totalRetLoss > 0 ? `-${formatPHP(totalRetLoss)}` : '₱0.00'}</td>
                        </tr>
                      );
                    })()}
                  </tbody>
                </table>
              </div>
            ) : (
              // Standard view for draft/sent
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white border-b">
                  <tr className="text-xs uppercase text-slate-500">
                    <th className="px-3 py-2 text-left">Product</th>
                    <th className="px-3 py-2 text-center">Qty</th>
                    <th className="px-3 py-2 text-right">Branch Capital</th>
                    <th className="px-3 py-2 text-right">Transfer Capital</th>
                    <th className="px-3 py-2 text-right">Branch Retail</th>
                    <th className="px-3 py-2 text-right">Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {(viewOrder?.items || []).map((item, i) => {
                    const margin = (item.branch_retail - item.transfer_capital);
                    return (
                      <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                        <td className="px-3 py-2">
                          <p className="font-medium">{item.product_name}</p>
                          <p className="text-xs text-slate-400 font-mono">{item.sku} · {item.category}</p>
                        </td>
                        <td className="px-3 py-2 text-center font-mono">{item.qty} {item.unit}</td>
                        <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.branch_capital)}</td>
                        <td className="px-3 py-2 text-right font-mono font-bold text-blue-700">{formatPHP(item.transfer_capital)}</td>
                        <td className="px-3 py-2 text-right font-mono font-bold text-emerald-700">{formatPHP(item.branch_retail)}</td>
                        <td className={`px-3 py-2 text-right font-mono font-bold ${margin >= 20 ? 'text-emerald-600' : 'text-red-500'}`}>
                          +{formatPHP(margin)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </ScrollArea>
          {((viewOrder?.status === 'sent' || viewOrder?.status === 'sent_to_terminal') && viewOrder?.to_branch_id === (currentBranch?.id || user?.branch_id || '')) && (
            <div className="pt-3 border-t space-y-3">
              {/* Incoming price updates preview */}
              {(viewOrder?.repack_price_updates || []).filter(rpu => rpu.new_retail_price).length > 0 && (
                <div className="rounded-xl border border-blue-200 bg-blue-50 p-3">
                  <p className="text-xs font-semibold text-blue-800 mb-2 flex items-center gap-1.5">
                    <span>🏷</span> Price Updates on Receive
                  </p>
                  <div className="space-y-1">
                    {(viewOrder.repack_price_updates || []).filter(rpu => rpu.new_retail_price > 0).map((rpu, i) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className="text-blue-700 font-medium">{rpu.repack_name}</span>
                        <div className="flex items-center gap-2">
                          <span className="text-slate-400 font-mono line-through">{rpu.current_dest_retail ? `₱${rpu.current_dest_retail}` : '—'}</span>
                          <span className="text-blue-600">→</span>
                          <span className="text-emerald-700 font-bold font-mono">₱{rpu.new_retail_price}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="text-[10px] text-blue-500 mt-1.5">These will be applied when you confirm receipt</p>
                </div>
              )}
              <div className="flex justify-between">
                <Button variant="outline" onClick={() => printTransferOrder(viewOrder)} data-testid="print-transfer-btn">
                  Print Transfer Order
                </Button>
                <Button onClick={() => setReceiveDialog(true)} className="bg-emerald-600 text-white">
                  <CheckCircle2 size={15} className="mr-1.5" /> Confirm Receipt
                </Button>
              </div>
            </div>
          )}
          {viewOrder?.status !== 'sent' && (
            <div className="pt-3 border-t flex justify-end">
              <Button variant="outline" onClick={() => printTransferOrder(viewOrder)} data-testid="print-transfer-btn">
                Print Transfer Order
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Receive Confirmation Dialog ── */}
      <Dialog open={receiveDialog} onOpenChange={v => { setReceiveDialog(v); if (!v) setViewOrder(null); }}>
        <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>
              Confirm Receipt — {viewOrder?.order_number}
              <span className="text-slate-400 text-sm font-normal ml-2">
                from {branches.find(b => b.id === viewOrder?.from_branch_id)?.name || '?'}
              </span>
            </DialogTitle>
          </DialogHeader>

          {/* Wizard step indicator */}
          <div className="flex items-center justify-center gap-1 px-1 pb-1" data-testid="receive-wizard-stepper">
            {[
              { key: 'qty', label: '1. Verify Quantities' },
              { key: 'upload', label: '2. Upload Receipt' },
              { key: 'summary', label: '3. Confirm' },
            ].map((s, i, arr) => {
              const stepIdx = ['qty', 'upload', 'summary'].indexOf(receiveStep);
              const myIdx = ['qty', 'upload', 'summary'].indexOf(s.key);
              const active = myIdx === stepIdx;
              const done = myIdx < stepIdx;
              return (
                <div key={s.key} className="flex items-center">
                  <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-medium transition-colors ${
                    active ? 'bg-emerald-600 text-white shadow-sm' :
                    done ? 'bg-emerald-100 text-emerald-700' :
                    'bg-slate-100 text-slate-400'
                  }`}>
                    {done ? <Check size={12} /> : <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] ${active ? 'bg-white/20' : 'bg-white/60'}`}>{myIdx + 1}</span>}
                    {s.label.replace(/^\d+\.\s/, '')}
                  </div>
                  {i < arr.length - 1 && <span className={`mx-1 ${myIdx < stepIdx ? 'text-emerald-300' : 'text-slate-200'}`}>—</span>}
                </div>
              );
            })}
          </div>

          {/* ── STEP 1: Verify Quantities ── */}
          {receiveStep === 'qty' && (
          <>
          {/* Explanation banner */}
          <div className="grid grid-cols-2 gap-3 px-1">
            <div className="flex gap-2 p-2.5 rounded-lg bg-amber-50 border border-amber-200">
              <span className="text-amber-600 font-bold text-lg leading-none">↓</span>
              <div>
                <p className="text-xs font-semibold text-amber-800">Shortage (received &lt; ordered)</p>
                <p className="text-[10px] text-amber-700 mt-0.5">Destination gets only what arrived. Missing qty stays in source. Source branch is notified.</p>
              </div>
            </div>
            <div className="flex gap-2 p-2.5 rounded-lg bg-blue-50 border border-blue-200">
              <span className="text-blue-600 font-bold text-lg leading-none">↑</span>
              <div>
                <p className="text-xs font-semibold text-blue-800">Excess (received &gt; ordered)</p>
                <p className="text-[10px] text-blue-700 mt-0.5">All extra stock goes to destination, deducted from source. Source branch is alerted to verify their stock.</p>
              </div>
            </div>
          </div>

          <ScrollArea className="flex-1 mt-1">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white border-b">
                <tr className="text-[10px] uppercase text-slate-500">
                  <th className="px-3 py-2 text-left">Product</th>
                  <th className="px-3 py-2 text-right">Ordered</th>
                  <th className="px-3 py-2 text-center w-28">Qty Received</th>
                  <th className="px-3 py-2 text-center">Variance</th>
                  <th className="px-3 py-2 text-right">Capital ₱</th>
                  <th className="px-3 py-2 text-right">Impact</th>
                </tr>
              </thead>
              <tbody>
                {(viewOrder?.items || []).map((item, i) => {
                  const ordered = parseFloat(item.qty) || 0;
                  const received = parseFloat(receiveQtys[item.product_id] ?? ordered) || 0;
                  const variance = received - ordered; // positive = excess, negative = shortage
                  const isShort = variance < 0;
                  const isExcess = variance > 0;
                  const isOk = variance === 0;
                  const impact = Math.abs(variance) * item.transfer_capital;
                  const retImpact = Math.abs(variance) * item.branch_retail;

                  return (
                    <tr key={i} className={`border-b ${isShort ? 'bg-amber-50/50' : isExcess ? 'bg-blue-50/50' : 'hover:bg-slate-50'}`}>
                      <td className="px-3 py-2">
                        <p className="font-medium text-sm">{item.product_name}</p>
                        <p className="text-[10px] text-slate-400">{item.sku} · {item.category}</p>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{ordered} {item.unit}</td>
                      <td className="px-3 py-2">
                        <CalcInput value={receiveQtys[item.product_id] ?? ordered}
 onChange={(v) => setReceiveQtys(q => ({ ...q, [item.product_id]: v }))}
 className={`h-8 text-sm text-center font-mono w-24 mx-auto block font-bold ${
 isShort ? 'border-amber-400 bg-amber-50 text-amber-800' :
 isExcess ? 'border-blue-400 bg-blue-50 text-blue-800' :
 'border-emerald-300'
 }`}
 data-testid={`receive-qty-${item.product_id}`} />
                      </td>
                      <td className="px-3 py-2 text-center">
                        {isOk && <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">✓ OK</span>}
                        {isShort && (
                          <span className="text-xs font-bold text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
                            ↓ Short {Math.abs(variance)} {item.unit}
                          </span>
                        )}
                        {isExcess && (
                          <span className="text-xs font-bold text-blue-700 bg-blue-100 px-2 py-0.5 rounded-full">
                            ↑ Excess {variance} {item.unit}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-sm text-blue-700">{formatPHP(item.transfer_capital)}</td>
                      <td className="px-3 py-2 text-right">
                        {isOk && <span className="text-slate-300 text-xs">—</span>}
                        {isShort && (
                          <div className="text-right">
                            <p className="text-[11px] font-bold text-amber-700">-{formatPHP(impact)}</p>
                            <p className="text-[9px] text-amber-500">-{formatPHP(retImpact)} retail</p>
                          </div>
                        )}
                        {isExcess && (
                          <div className="text-right">
                            <p className="text-[11px] font-bold text-blue-700">+{formatPHP(impact)}</p>
                            <p className="text-[9px] text-blue-500">+{formatPHP(retImpact)} retail</p>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Live variance summary */}
            {(() => {
              const items = viewOrder?.items || [];
              let totalShortCapital = 0, totalShortRetail = 0, shortCount = 0;
              let totalExcessCapital = 0, totalExcessRetail = 0, excessCount = 0;
              items.forEach(item => {
                const ordered = parseFloat(item.qty) || 0;
                const received = parseFloat(receiveQtys[item.product_id] ?? ordered) || 0;
                const variance = received - ordered;
                if (variance < 0) {
                  shortCount++;
                  totalShortCapital += Math.abs(variance) * item.transfer_capital;
                  totalShortRetail += Math.abs(variance) * item.branch_retail;
                } else if (variance > 0) {
                  excessCount++;
                  totalExcessCapital += variance * item.transfer_capital;
                  totalExcessRetail += variance * item.branch_retail;
                }
              });
              if (!shortCount && !excessCount) return null;
              return (
                <div className="mt-3 mx-3 space-y-2">
                  {shortCount > 0 && (
                    <div className="flex items-center justify-between p-3 rounded-lg bg-amber-50 border border-amber-200">
                      <div>
                        <p className="text-xs font-bold text-amber-800">Shortage Summary — {shortCount} product(s)</p>
                        <p className="text-[10px] text-amber-600 mt-0.5">Missing quantity stays in source branch. Source branch will be notified.</p>
                      </div>
                      <div className="text-right shrink-0 ml-4">
                        <p className="text-sm font-bold text-amber-800">-{formatPHP(totalShortCapital)} capital</p>
                        <p className="text-[11px] text-amber-600">-{formatPHP(totalShortRetail)} retail</p>
                      </div>
                    </div>
                  )}
                  {excessCount > 0 && (
                    <div className="flex items-center justify-between p-3 rounded-lg bg-blue-50 border border-blue-200">
                      <div>
                        <p className="text-xs font-bold text-blue-800">Excess Summary — {excessCount} product(s)</p>
                        <p className="text-[10px] text-blue-600 mt-0.5">Extra stock will be accepted. Source branch will be alerted to verify inventory.</p>
                      </div>
                      <div className="text-right shrink-0 ml-4">
                        <p className="text-sm font-bold text-blue-800">+{formatPHP(totalExcessCapital)} capital</p>
                        <p className="text-[11px] text-blue-600">+{formatPHP(totalExcessRetail)} retail</p>
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Notes field */}
            <div className="mx-3 mt-3 mb-2">
              <label className="text-xs text-slate-500 font-medium block mb-1">Receiving Notes (optional)</label>
              <textarea
                value={receiveNotes}
                onChange={e => setReceiveNotes(e.target.value)}
                placeholder="e.g. 2 boxes of ENERTONE arrived damaged, accepted 8 of 10..."
                rows={2}
                className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-emerald-300 resize-none"
                data-testid="receive-notes"
              />
            </div>
          </ScrollArea>
          </>
          )}

          {/* ── STEP 2: Upload Receipt ── */}
          {receiveStep === 'upload' && (
            <div className="flex-1 overflow-y-auto px-1 py-2 space-y-3" data-testid="receive-step-upload">
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                <div className="flex items-start gap-2">
                  <Upload size={16} className="text-blue-600 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-blue-800">Attach a photo of the paper receipt / DR</p>
                    <p className="text-[11px] text-blue-600 mt-0.5">
                      Recommended for audit trail. You can either drag-and-drop a photo from this computer below,
                      or scan the QR code with your phone camera and upload there. The upload is detected automatically.
                    </p>
                  </div>
                </div>
              </div>
              <ReceiptUploadInline
                required={false}
                label="Upload Receipt / DR Photo"
                recordType="branch_transfer"
                recordSummary={viewOrder ? {
                  type_label: 'Branch Transfer',
                  title: viewOrder.order_number || 'Transfer Receipt',
                  description: `${viewOrder.items?.length || 0} item(s)`,
                  amount: viewOrder.total_capital || 0,
                } : undefined}
                onUploaded={(data) => setReceiveReceiptData(data)}
              />
              {receiveReceiptData?.fileCount > 0 && (
                <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-50 border border-emerald-200">
                  <Check size={16} className="text-emerald-600" />
                  <p className="text-sm text-emerald-800 font-medium">
                    {receiveReceiptData.fileCount} {receiveReceiptData.fileCount === 1 ? 'photo' : 'photos'} uploaded — ready to continue
                  </p>
                </div>
              )}
            </div>
          )}

          {/* ── STEP 3: Confirm Summary ── */}
          {receiveStep === 'summary' && (() => {
            const items = viewOrder?.items || [];
            const { hasVariance, shortages, excesses } = getVariances(items);
            const totalReceivedCapital = items.reduce((sum, item) => {
              const r = parseFloat(receiveQtys[item.product_id] ?? item.qty) || 0;
              return sum + r * (item.transfer_capital || 0);
            }, 0);
            return (
              <div className="flex-1 overflow-y-auto px-1 py-2 space-y-3" data-testid="receive-step-summary">
                {/* Reference card — for writing on the paper receipt */}
                <div className="border-2 border-emerald-300 bg-gradient-to-br from-emerald-50 to-white rounded-lg p-4">
                  <p className="text-[10px] uppercase font-bold text-emerald-700 tracking-widest mb-1">Write this on the paper receipt</p>
                  <p className="text-2xl font-mono font-black text-slate-800 select-all" data-testid="receive-ref-number">{viewOrder?.order_number}</p>
                  <p className="text-[11px] text-slate-500 mt-1">
                    Reference for tracing this delivery in the system if there's a dispute later.
                    From <span className="font-medium">{branches.find(b => b.id === viewOrder?.from_branch_id)?.name}</span> to <span className="font-medium">{branches.find(b => b.id === viewOrder?.to_branch_id)?.name}</span>.
                  </p>
                </div>

                {/* Compact items recap */}
                <div className="border rounded-lg overflow-hidden">
                  <div className="bg-slate-50 px-3 py-2 text-[10px] uppercase tracking-wider text-slate-500 flex justify-between">
                    <span>Receipt summary — {items.length} {items.length === 1 ? 'item' : 'items'}</span>
                    <span className="font-mono font-bold text-emerald-700">{formatPHP(totalReceivedCapital)} total capital</span>
                  </div>
                  <div className="max-h-48 overflow-y-auto anchored-dropdown-scroll divide-y divide-slate-100">
                    {items.map((item, i) => {
                      const ordered = parseFloat(item.qty) || 0;
                      const received = parseFloat(receiveQtys[item.product_id] ?? ordered) || 0;
                      const diff = received - ordered;
                      return (
                        <div key={i} className="flex items-center justify-between px-3 py-1.5 text-xs">
                          <div className="min-w-0 flex-1 truncate">
                            <span className="font-medium">{item.product_name}</span>
                            <span className="text-slate-400 ml-1.5">{item.sku}</span>
                          </div>
                          <div className="flex items-center gap-3 shrink-0 font-mono">
                            <span className="text-slate-400">{ordered}→</span>
                            <span className={`font-bold ${diff < 0 ? 'text-amber-700' : diff > 0 ? 'text-blue-700' : 'text-emerald-700'}`}>{received}</span>
                            <span className="text-slate-500 w-20 text-right">{formatPHP(received * item.transfer_capital)}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Variance final warning */}
                {hasVariance && (
                  <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs">
                    <p className="font-bold text-amber-800 flex items-center gap-1.5">
                      <AlertTriangle size={12} /> Variance present — {shortages.length} shortage(s), {excesses.length} excess(es)
                    </p>
                    <p className="text-amber-700 mt-1">Source branch will be notified to verify their stock count.</p>
                  </div>
                )}

                {/* Receipt photo confirmation */}
                {receiveReceiptData?.fileCount > 0 ? (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-50 text-xs">
                    <Check size={14} className="text-emerald-600" />
                    <span className="text-emerald-800">{receiveReceiptData.fileCount} receipt photo(s) attached</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-50 text-xs">
                    <X size={14} className="text-slate-400" />
                    <span className="text-slate-500">No receipt photo attached (optional)</span>
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Footer with step-aware actions ── */}
          <div className="pt-3 border-t flex gap-2 justify-between items-center">
            <p className="text-xs text-slate-400">
              {receiveConfirmStep
                ? 'Quantities differ from the order. Submitting will send to source for confirmation.'
                : receiveStep === 'qty' ? 'Verify each quantity, then continue.'
                : receiveStep === 'upload' ? 'Optional but recommended for audit trail.'
                : 'Final review — write the reference number on the paper receipt before confirming.'}
            </p>
            <div className="flex gap-2">
              {receiveStep === 'qty' && (
                <>
                  <Button variant="outline" onClick={() => { setReceiveDialog(false); setReceiveConfirmStep(false); }}>Cancel</Button>
                  <Button onClick={() => setReceiveStep('upload')} className="bg-emerald-600 hover:bg-emerald-700 text-white" data-testid="continue-to-upload-btn">
                    Continue → Upload Receipt
                  </Button>
                </>
              )}
              {receiveStep === 'upload' && (
                <>
                  <Button variant="outline" onClick={() => setReceiveStep('qty')}>← Back</Button>
                  <Button variant="outline" onClick={() => setReceiveStep('summary')}
                    className="text-slate-500"
                    data-testid="skip-receipt-btn">
                    Skip — No Receipt
                  </Button>
                  <Button onClick={() => setReceiveStep('summary')} className="bg-emerald-600 hover:bg-emerald-700 text-white"
                    data-testid="continue-to-summary-btn"
                    disabled={!receiveReceiptData?.fileCount}>
                    Continue → Review
                  </Button>
                </>
              )}
              {receiveStep === 'summary' && (
                <>
                  <Button variant="outline" onClick={() => setReceiveStep('upload')}>← Back</Button>
                  {receiveConfirmStep ? (
                    <Button onClick={handleReceive} disabled={receiveSaving}
                      className="bg-amber-600 hover:bg-amber-700 text-white"
                      data-testid="confirm-receive-variance-btn">
                      {receiveSaving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <AlertTriangle size={14} className="mr-1.5" />}
                      Yes, Submit Variance for Review
                    </Button>
                  ) : (
                    <Button onClick={handleReceive} disabled={receiveSaving} className="bg-emerald-600 hover:bg-emerald-700 text-white"
                      data-testid="confirm-receive-btn">
                      {receiveSaving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <CheckCircle2 size={14} className="mr-1.5" />}
                      Confirm Receipt
                    </Button>
                  )}
                </>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {/* ── Accept Receipt Dialog — Enhanced with comparison table ── */}
      <Dialog open={!!acceptDialog} onOpenChange={() => setAcceptDialog(null)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>
              Review Variance — {acceptDialog?.order_number}
            </DialogTitle>
          </DialogHeader>
          {acceptDialog && (
            <div className="space-y-4">
              {/* Comparison Table */}
              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-slate-100">
                    <tr>
                      <th className="text-left px-3 py-2 font-semibold text-slate-600">Product</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">Sent</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">Received</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">Variance</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">Capital/unit</th>
                      <th className="text-right px-3 py-2 font-semibold text-red-600">Capital Loss</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {[...(acceptDialog.shortages || []), ...(acceptDialog.excesses || [])].map((v, i) => {
                      const isShort = v.variance > 0;
                      return (
                        <tr key={i} className={isShort ? 'bg-red-50/50' : 'bg-blue-50/50'}>
                          <td className="px-3 py-2">
                            <span className="font-medium text-slate-800">{v.product_name}</span>
                            <span className="text-[10px] text-slate-400 ml-1">{v.sku}</span>
                          </td>
                          <td className="px-3 py-2 text-right font-mono">{v.qty_ordered} {v.unit}</td>
                          <td className="px-3 py-2 text-right font-mono font-bold">{v.qty_received} {v.unit}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${isShort ? 'text-red-600' : 'text-blue-600'}`}>
                            {isShort ? `-${v.variance}` : `+${Math.abs(v.variance)}`}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(v.transfer_capital)}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${isShort ? 'text-red-600' : 'text-slate-300'}`}>
                            {isShort ? `-${formatPHP(v.capital_variance)}` : '—'}
                          </td>
                        </tr>
                      );
                    })}
                    {/* Totals */}
                    <tr className="bg-slate-100 font-bold border-t-2">
                      <td className="px-3 py-2" colSpan={5}>Total Capital Loss</td>
                      <td className="px-3 py-2 text-right font-mono text-red-700">
                        -{formatPHP((acceptDialog.shortages || []).reduce((s, v) => s + (v.capital_variance || 0), 0))}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Receiver info */}
              {acceptDialog.pending_receipt_by_name && (
                <div className="text-xs text-slate-500 bg-slate-50 rounded px-3 py-2">
                  Counted by: <b>{acceptDialog.pending_receipt_by_name}</b> · {fmtDateTime(acceptDialog.pending_receipt_at)}
                  {acceptDialog.receive_notes && <span className="ml-2 text-slate-400">Note: "{acceptDialog.receive_notes}"</span>}
                </div>
              )}

              {/* Note input */}
              <div>
                <label className="text-xs text-slate-500 font-medium block mb-1">Note <span className="text-slate-400">(required for investigation)</span></label>
                <textarea
                  placeholder="e.g. Verified with packing list, driver confirmed shortage..."
                  rows={2}
                  className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-emerald-300 resize-none"
                  id="accept-note-input" data-testid="accept-note"
                />
              </div>

              {/* 3 Action Buttons */}
              <div className="space-y-2 pt-2 border-t">
                <div className="flex gap-2">
                  <Button onClick={() => {
                    const note = document.getElementById('accept-note-input')?.value || '';
                    handleAcceptReceipt(acceptDialog.id, note, 'accept');
                  }} disabled={actionSaving} className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white" data-testid="confirm-accept-btn">
                    {actionSaving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <CheckCircle2 size={14} className="mr-1.5" />}
                    Accept Variance
                  </Button>
                  <Button onClick={() => {
                    const note = document.getElementById('accept-note-input')?.value || '';
                    if (!note.trim()) { toast.error('Note is required when creating an incident ticket'); return; }
                    handleAcceptReceipt(acceptDialog.id, note, 'accept_with_incident');
                  }} disabled={actionSaving} variant="outline" className="flex-1 border-amber-300 text-amber-700 hover:bg-amber-50" data-testid="accept-investigate-btn">
                    {actionSaving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <AlertTriangle size={14} className="mr-1.5" />}
                    Accept + Investigate
                  </Button>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => {
                    setAcceptDialog(null);
                    setDisputeDialog(acceptDialog);
                    setDisputeNote('');
                  }} className="flex-1 border-red-200 text-red-600 hover:bg-red-50" data-testid="switch-to-dispute-btn">
                    <XCircle size={14} className="mr-1.5" />
                    Dispute & Re-count
                  </Button>
                  <Button variant="ghost" onClick={() => setAcceptDialog(null)} className="text-slate-400">
                    Cancel
                  </Button>
                </div>
                <p className="text-[10px] text-slate-400 leading-relaxed">
                  <b>Accept Variance</b> — Acknowledge the difference, update inventory, log in audit.
                  <br /><b>Accept + Investigate</b> — Same as accept, but also creates an incident ticket to trace the loss (driver, damage, etc).
                  <br /><b>Dispute & Re-count</b> — Reject the numbers, ask receiver to re-count.
                </p>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Dispute Receipt Dialog ── */}
      <Dialog open={!!disputeDialog} onOpenChange={() => { setDisputeDialog(null); setDisputeNote(''); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Dispute Receipt — {disputeDialog?.order_number}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800">
              <p className="font-semibold mb-1">Disputing will:</p>
              <ul className="list-disc list-inside text-xs space-y-0.5">
                <li>NOT update any inventory</li>
                <li>Notify the destination branch to re-count</li>
                <li>Allow them to re-submit corrected quantities</li>
              </ul>
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1">Reason for dispute <span className="text-red-500">*</span></label>
              <textarea
                value={disputeNote}
                onChange={e => setDisputeNote(e.target.value)}
                placeholder="e.g. Our records show we packed 10 cases, please re-count..."
                rows={3}
                className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-300 resize-none"
                data-testid="dispute-note"
              />
            </div>
          </div>
          <div className="flex gap-2 justify-end pt-2 border-t mt-2">
            <Button variant="outline" onClick={() => { setDisputeDialog(null); setDisputeNote(''); }}>Cancel</Button>
            <Button onClick={handleDisputeReceipt} disabled={actionSaving || !disputeNote.trim()}
              className="bg-red-600 text-white" data-testid="confirm-dispute-btn">
              {actionSaving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <XCircle size={14} className="mr-1.5" />}
              Send Dispute
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── BRANCH TRANSFER UPLOAD QR ─────────────────────────────────── */}
      <UploadQRDialog
        open={btUploadQROpen}
        onClose={(count) => { setBtUploadQROpen(false); if (count > 0) toast.success(`${count} photo(s) attached to transfer.`); }}
        recordType="branch_transfer"
        recordId={btUploadOrderId}
      />
      <ViewQRDialog
        open={btViewQROpen}
        onClose={() => setBtViewQROpen(false)}
        recordType="branch_transfer"
        recordId={viewOrder?.id}
      />
      <VerifyPinDialog
        open={btVerifyOpen}
        onClose={() => setBtVerifyOpen(false)}
        docType="branch_transfer"
        docId={btVerifyId}
        docLabel={viewOrder?.order_number}
        onVerified={(result) => {
          setBtVerifyOpen(false);
          if (viewOrder) {
            setViewOrder(prev => ({ ...prev, verified: true, verified_by_name: result.verified_by, verification_status: result.status }));
          }
        }}
      />

      {/* Smart Capital Pricing Dialog for Branch Transfers */}
      {transferCapitalDialog && transferCapitalPreview && (
        <Dialog open={transferCapitalDialog} onOpenChange={setTransferCapitalDialog}>
          <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-amber-700">
                <TrendingDown size={18} className="text-amber-500" />
                Smart Capital Pricing — Transfer Price Drop
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
                <strong>Transfer {transferCapitalPreview.order_number}</strong> contains items arriving at{' '}
                <strong>{transferCapitalPreview.to_branch_name}</strong> priced{' '}
                <strong>lower than the branch's current capital</strong>. Choose how to update each product's capital.
              </div>

              <div className="flex gap-2">
                <Button size="sm" variant="outline" className="text-xs border-slate-300"
                  onClick={() => {
                    const all = {};
                    transferCapitalPreview.items.forEach(i => { all[i.product_id] = 'transfer_capital'; });
                    setTransferCapitalChoices(all);
                  }}>
                  Use all transfer prices
                </Button>
                <Button size="sm" variant="outline" className="text-xs border-slate-300"
                  onClick={() => {
                    const all = {};
                    transferCapitalPreview.items.forEach(i => { all[i.product_id] = 'moving_average'; });
                    setTransferCapitalChoices(all);
                  }}>
                  Use all moving averages
                </Button>
              </div>

              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-slate-600">Product</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-600">Current Capital</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-600">Transfer Price</th>
                      <th className="text-right px-3 py-2 font-medium text-slate-600">PO Moving Avg</th>
                      <th className="text-center px-3 py-2 font-medium text-slate-600">Use</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {transferCapitalPreview.items.map(item => (
                      <tr key={item.product_id} className={item.needs_warning ? 'bg-amber-50/60' : ''}>
                        <td className="px-3 py-2.5">
                          <div className="font-medium text-slate-800 leading-tight">{item.product_name}</div>
                          <div className="text-[10px] text-slate-400">{item.sku} · {item.qty} {item.unit}</div>
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-slate-700">
                          ₱{item.current_dest_capital.toFixed(2)}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono">
                          {item.needs_warning ? (
                            <span className="text-amber-700 font-semibold flex items-center justify-end gap-1">
                              <TrendingDown size={12} />₱{item.transfer_capital.toFixed(2)}
                              <span className="text-[10px] text-amber-500">(-{item.price_drop_pct}%)</span>
                            </span>
                          ) : (
                            <span className="text-emerald-700 flex items-center justify-end gap-1">
                              <TrendingUp size={12} />₱{item.transfer_capital.toFixed(2)}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-slate-500">
                          ₱{item.moving_avg.toFixed(2)}
                        </td>
                        <td className="px-3 py-2.5">
                          {item.needs_warning ? (
                            <div className="flex gap-1 justify-center">
                              <button
                                onClick={() => setTransferCapitalChoices(prev => ({ ...prev, [item.product_id]: 'transfer_capital' }))}
                                className={`px-2 py-1 rounded text-[10px] font-semibold transition-colors ${
                                  transferCapitalChoices[item.product_id] === 'transfer_capital'
                                    ? 'bg-amber-500 text-white'
                                    : 'bg-slate-100 text-slate-500 hover:bg-amber-100'
                                }`}>
                                Transfer
                              </button>
                              <button
                                onClick={() => setTransferCapitalChoices(prev => ({ ...prev, [item.product_id]: 'moving_average' }))}
                                className={`px-2 py-1 rounded text-[10px] font-semibold transition-colors ${
                                  transferCapitalChoices[item.product_id] === 'moving_average'
                                    ? 'bg-blue-500 text-white'
                                    : 'bg-slate-100 text-slate-500 hover:bg-blue-100'
                                }`}>
                                Avg
                              </button>
                            </div>
                          ) : (
                            <span className="text-[10px] text-emerald-600 text-center block">Auto</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="text-[11px] text-slate-500">
                <strong>Transfer Price</strong> — uses the source branch's transfer capital (good if permanently cheaper).
                <br /><strong>Moving Avg</strong> — uses purchase history average (good for temporary promotions or one-off transfers).
              </p>

              <div className="flex gap-2 pt-1 border-t">
                <Button variant="outline" className="flex-1" onClick={() => setTransferCapitalDialog(false)}>
                  Cancel
                </Button>
                <Button onClick={confirmTransferReceive} disabled={receiveSaving}
                  className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white">
                  {receiveSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                  Confirm Receive
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* ── Park Transfer Dialog ── */}
      <Dialog open={parkDialogOpen} onOpenChange={setParkDialogOpen}>
        <DialogContent className="sm:max-w-md" data-testid="park-transfer-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base text-amber-700">
              <Pause size={16} /> Park Transfer
            </DialogTitle>
            <DialogDescription>
              Save this draft and pick it up later. Stock is not reserved while parked.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 pt-1">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs space-y-1">
              <div className="flex justify-between"><span className="text-slate-600">From</span><span className="font-medium">{branches.find(b => b.id === fromBranchId)?.name || '—'}</span></div>
              <div className="flex justify-between"><span className="text-slate-600">To</span><span className="font-medium">{toBranchId ? (branches.find(b => b.id === toBranchId)?.name || '—') : <span className="text-slate-400">(not yet selected)</span>}</span></div>
              <div className="flex justify-between"><span className="text-slate-600">Items in draft</span><span className="font-mono font-bold">{rows.filter(r => r.product).length}</span></div>
              <div className="text-[10px] text-amber-600 mt-1">Auto-deletes after 24 hours.</div>
            </div>
            <div>
              <Label className="text-xs">Label (optional)</Label>
              <Input value={parkLabel} onChange={e => setParkLabel(e.target.value)}
                placeholder="e.g. Waiting on truck Tuesday"
                onKeyDown={e => e.key === 'Enter' && handlePark()}
                className="h-9 mt-1" data-testid="park-label-input" />
              <p className="text-[10px] text-slate-400 mt-0.5">Helps you find this park later</p>
            </div>
            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={() => setParkDialogOpen(false)}>Cancel</Button>
              <Button onClick={handlePark} disabled={parkingNow}
                className="flex-1 bg-amber-600 hover:bg-amber-700 text-white"
                data-testid="park-confirm-btn">
                {parkingNow ? 'Parking...' : (activeParkId ? 'Update Park' : 'Park Transfer')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Resume Park Dialog ── */}
      <Dialog open={resumeDialogOpen} onOpenChange={setResumeDialogOpen}>
        <DialogContent className="sm:max-w-2xl" data-testid="resume-park-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base text-blue-700">
              <FolderOpen size={16} /> Resume Parked Transfer
            </DialogTitle>
            <DialogDescription>
              Pick up where you (or a colleague at this branch) left off. Drafts auto-expire after 24 h.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 pt-1 max-h-96 overflow-y-auto">
            {parkedTransfers.length === 0 && (
              <p className="text-center text-sm text-slate-400 py-8">No parked transfers.</p>
            )}
            {parkedTransfers.map(p => {
              const dest = branches.find(b => b.id === p.to_branch_id);
              const ageMins = Math.round((Date.now() - new Date(p.created_at).getTime()) / 60000);
              const ageStr = ageMins < 60 ? `${ageMins}m ago` : `${Math.floor(ageMins / 60)}h ${ageMins % 60}m ago`;
              return (
                <div key={p.id} className="border border-slate-200 rounded-lg p-3 hover:bg-slate-50 transition-colors flex items-center justify-between gap-3"
                  data-testid={`parked-transfer-${p.id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-semibold text-slate-800 truncate">{p.label || 'Untitled draft'}</span>
                      <span className="text-[10px] bg-slate-100 text-slate-600 rounded px-1.5 py-0.5">{p.item_count} {p.item_count === 1 ? 'item' : 'items'}</span>
                    </div>
                    <div className="text-[11px] text-slate-500 flex flex-wrap gap-x-3 gap-y-0.5">
                      <span><Building2 size={9} className="inline mr-0.5" /> To: <span className="font-medium">{dest?.name || '(none)'}</span></span>
                      <span><Clock size={9} className="inline mr-0.5" /> {ageStr}</span>
                      <span>by {p.created_by_name || 'unknown'}</span>
                    </div>
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <Button size="sm" onClick={() => handleResume(p.id)}
                      className="h-8 bg-blue-600 hover:bg-blue-700 text-white"
                      data-testid={`resume-park-${p.id}`}>
                      Resume
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => handleDiscardPark(p.id)}
                      className="h-8 px-2 text-red-500 hover:text-red-700 hover:bg-red-50"
                      data-testid={`discard-park-${p.id}`}
                      title="Discard this park">
                      <Trash size={13} />
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </DialogContent>
      </Dialog>

    </div>
  );
}
