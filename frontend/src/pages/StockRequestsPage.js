/**
 * Stock Requests Page — Branch A creates requests, Branch B triages them
 * into BTOs and DRAFT POs.
 *
 * MVP shape (Feb 2026):
 *  • Top tabs: Inbox (incoming) | Outbox (outgoing) | New Request
 *  • New Request: product search reuses sales pattern, shows BOTH branches'
 *    on-hand qty so the requesting branch can see what's actually available.
 *  • Triage: per-line dropdown {Personal Transfer | Supplier PO | Unfulfilled}
 *    with supplier picker + unit_price for supplier_po rows.
 *  • Detail panel shows linked child docs (BTO + POs) with status.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP, fmtDateTime } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ClipboardList, Plus, Send, ArrowRight,
         Truck, X, Check, RefreshCw, FileText, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import SmartProductSearch from '../components/SmartProductSearch';
import CalcInput from '../components/CalcInput';

const STATUS_BADGE = {
  draft:                   { label: 'Draft',           cls: 'bg-slate-100 text-slate-700' },
  sent:                    { label: 'Awaiting Triage', cls: 'bg-amber-100 text-amber-700' },
  in_triage:               { label: 'Triaging',        cls: 'bg-amber-100 text-amber-700' },
  fulfillment_generated:   { label: 'In Fulfillment',  cls: 'bg-sky-100 text-sky-700' },
  completed:               { label: 'Completed',       cls: 'bg-emerald-100 text-emerald-700' },
  cancelled:               { label: 'Cancelled',       cls: 'bg-rose-100 text-rose-700' },
};

const VARIANCE_BADGE = {
  completed:        { label: 'Completed',       cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  under_delivered:  { label: 'Under-delivered', cls: 'bg-amber-100 text-amber-700 border-amber-200' },
  over_delivered:   { label: 'Over-delivered',  cls: 'bg-sky-100 text-sky-700 border-sky-200' },
  extra_items:      { label: 'Extra items',     cls: 'bg-violet-100 text-violet-700 border-violet-200' },
  missing_items:    { label: 'Missing items',   cls: 'bg-rose-100 text-rose-700 border-rose-200' },
};

const StatusBadge = ({ status }) => {
  const s = STATUS_BADGE[status] || { label: status, cls: 'bg-slate-100 text-slate-700' };
  return <Badge className={`${s.cls} text-[10px] font-medium`}>{s.label}</Badge>;
};

export default function StockRequestsPage() {
  const { user, currentBranch, branches } = useAuth();
  const navigate = useNavigate();

  const [tab, setTab] = useState('inbox');         // inbox | outbox | new
  const [inbox, setInbox] = useState([]);
  const [outbox, setOutbox] = useState([]);
  const [loading, setLoading] = useState(false);
  const [detailId, setDetailId] = useState(null);  // active request id for the side-panel

  const loadLists = useCallback(async () => {
    if (!currentBranch?.id) return;
    setLoading(true);
    try {
      const [inRes, outRes] = await Promise.all([
        api.get(`/stock-requests?branch_id=${currentBranch.id}&role=supplying&limit=50`),
        api.get(`/stock-requests?branch_id=${currentBranch.id}&role=requesting&limit=50`),
      ]);
      setInbox(inRes.data.items || []);
      setOutbox(outRes.data.items || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load stock requests');
    }
    setLoading(false);
  }, [currentBranch?.id]);

  useEffect(() => { loadLists(); }, [loadLists]);

  return (
    <div className="p-6 max-w-7xl mx-auto" data-testid="stock-requests-page">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
            <ClipboardList size={22} className="text-teal-600" /> Stock Requests
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Cross-branch fulfillment hub — route lines to Branch Transfer or supplier PO.
          </p>
        </div>
        <Button onClick={() => setTab('new')} className="bg-teal-600 hover:bg-teal-700 text-white"
                data-testid="new-request-btn">
          <Plus size={14} className="mr-1.5" /> New Request
        </Button>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="inbox" data-testid="inbox-tab">
            <ArrowRight size={13} className="mr-1.5 rotate-180" />
            Incoming
            {inbox.filter(r => r.status === 'sent').length > 0 && (
              <Badge className="ml-1.5 bg-amber-500 text-white text-[10px]">
                {inbox.filter(r => r.status === 'sent').length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="outbox" data-testid="outbox-tab">
            <ArrowRight size={13} className="mr-1.5" />
            My Requests
          </TabsTrigger>
          <TabsTrigger value="new" data-testid="new-tab">
            <Plus size={13} className="mr-1.5" /> New
          </TabsTrigger>
        </TabsList>

        <TabsContent value="inbox" className="mt-4">
          <RequestList rows={inbox} loading={loading} onOpen={setDetailId}
                       emptyLabel="No incoming requests right now." testid="inbox-list" />
        </TabsContent>
        <TabsContent value="outbox" className="mt-4">
          <RequestList rows={outbox} loading={loading} onOpen={setDetailId}
                       emptyLabel="You haven't sent any requests yet." testid="outbox-list" />
        </TabsContent>
        <TabsContent value="new" className="mt-4">
          <NewRequestForm
            currentBranch={currentBranch} branches={branches}
            onCreated={(id) => { setDetailId(id); loadLists(); setTab('outbox'); }}
          />
        </TabsContent>
      </Tabs>

      {detailId && (
        <DetailDialog
          requestId={detailId}
          currentBranchId={currentBranch?.id}
          onClose={() => { setDetailId(null); loadLists(); }}
        />
      )}
    </div>
  );
}

/* ── Request list (used for both inbox and outbox) ────────────────────── */
function RequestList({ rows, loading, onOpen, emptyLabel, testid }) {
  if (loading) return <div className="text-sm text-slate-400 py-10 text-center">Loading…</div>;
  if (!rows.length) return (
    <div className="text-sm text-slate-400 py-10 text-center border-2 border-dashed border-slate-200 rounded-lg" data-testid={`${testid}-empty`}>
      {emptyLabel}
    </div>
  );
  return (
    <div className="space-y-2" data-testid={testid}>
      {rows.map(r => (
        <Card key={r.id} className="hover:shadow-md transition-shadow cursor-pointer"
              onClick={() => onOpen(r.id)} data-testid={`request-row-${r.id}`}>
          <CardContent className="p-3 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="font-mono text-sm font-semibold text-slate-800">{r.request_number}</span>
                <StatusBadge status={r.status} />
              </div>
              <div className="text-xs text-slate-500 truncate">
                {(r.items || []).length} line(s) · {r.created_by_name || 'Unknown'} ·{' '}
                {fmtDateTime(r.created_at)}
              </div>
            </div>
            <ArrowRight size={16} className="text-slate-400 shrink-0" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/* ── New request form ─────────────────────────────────────────────────── */
const newRow = () => ({ id: Date.now() + Math.random(), product: null, qty: '' });

function NewRequestForm({ currentBranch, branches, onCreated }) {
  const [supplyingBranchId, setSupplyingBranchId] = useState('');
  const [notes, setNotes] = useState('');
  // Sales-style row model — each row holds either an unfilled SmartProductSearch
  // OR a filled product card + qty input.  Last row is always blank and auto-
  // grows to a new one when filled, mirroring BranchTransferPage.selectReqProduct.
  const [rows, setRows] = useState([newRow()]);
  const [submitting, setSubmitting] = useState(false);

  // Per-row qty input refs so we can focus-handoff after picking a product
  // (same pattern UnifiedSalesPage / BranchTransferPage use).
  const qtyRefs = useRef({});

  const eligibleBranches = useMemo(
    () => (branches || []).filter(b => b.id !== currentBranch?.id),
    [branches, currentBranch?.id]
  );

  const updateRow = (id, patch) =>
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r));
  const removeRow = (id) =>
    setRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);

  // Mirror BranchTransferPage.selectReqProduct exactly: fill the row, append
  // a fresh row when the last gets filled, then focus this row's qty input.
  const selectProduct = (rowId, p) => {
    // Dedup — refuse to add the same product twice across rows.
    if (rows.some(r => r.product && r.product.id === p.id)) {
      toast.info(`${p.name} is already on the request.`);
      return;
    }
    setRows(prev => {
      const idx = prev.findIndex(r => r.id === rowId);
      if (idx < 0) return prev;
      const next = [...prev];
      // SmartProductSearch (mode='request') gives us {id, name, sku, unit,
      // available, also_branch_stock}. Normalise into our row shape.
      const product = {
        id:                p.id,
        name:              p.name,
        sku:               p.sku || '',
        unit:              p.unit || '',
        supplying_qty:     p.available ?? 0,            // their stock
        requesting_qty:    p.also_branch_stock ?? 0,    // my stock
      };
      next[idx] = { ...next[idx], product, qty: next[idx].qty || '1' };
      if (idx === next.length - 1) next.push(newRow());
      return next;
    });
    setTimeout(() => qtyRefs.current[rowId]?.focus?.(), 60);
  };

  // Tab-from-qty → focus the next row's search input (auto-grow if needed).
  const handleQtyKeyDown = (e, rowIdx) => {
    if (e.key !== 'Tab' || e.shiftKey) return;
    const isLastFilled = rowIdx === rows.length - 1 ||
      (rowIdx === rows.length - 2 && !rows[rows.length - 1].product);
    if (isLastFilled) {
      // Let React commit then focus the (now-existing) next row's search.
      setTimeout(() => {
        const nextSearch = document.querySelector(
          `[data-testid="sr-row-search-${rows[rowIdx + 1]?.id || rows[rows.length - 1].id}"] input`
        );
        nextSearch?.focus?.();
      }, 30);
    }
  };

  const resetForm = () => {
    setSupplyingBranchId('');
    setNotes('');
    setRows([newRow()]);
  };

  const submit = async () => {
    if (!supplyingBranchId) { toast.error('Pick a supplying branch first'); return; }
    const filled = rows.filter(r => r.product && parseFloat(r.qty) > 0);
    if (!filled.length) { toast.error('Add at least one product with qty > 0'); return; }
    setSubmitting(true);
    try {
      const res = await api.post('/stock-requests', {
        requesting_branch_id: currentBranch.id,
        supplying_branch_id:  supplyingBranchId,
        items: filled.map(r => ({
          product_id:   r.product.id,
          product_name: r.product.name,
          qty:          parseFloat(r.qty) || 0,
          unit:         r.product.unit,
        })),
        notes,
      });
      await api.post(`/stock-requests/${res.data.id}/send`);
      toast.success(`Stock request ${res.data.request_number} sent.`);
      onCreated(res.data.id);
      resetForm();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to send request');
    }
    setSubmitting(false);
  };

  const myBranchId = currentBranch?.id;
  const targetBranch = branches?.find(b => b.id === supplyingBranchId);
  const filledCount = rows.filter(r => r.product).length;

  return (
    <div className="space-y-4" data-testid="new-request-form">
      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <Label className="text-xs text-slate-600">My Branch</Label>
              <div className="mt-1 px-3 py-2 bg-slate-100 rounded text-sm font-medium">
                {currentBranch?.name || '(no branch selected)'}
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-600">Requesting From</Label>
              <Select value={supplyingBranchId} onValueChange={setSupplyingBranchId}>
                <SelectTrigger className="mt-1" data-testid="supplying-branch-select">
                  <SelectValue placeholder="Pick a branch to request from" />
                </SelectTrigger>
                <SelectContent>
                  {eligibleBranches.map(b => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {supplyingBranchId && (
            <div className="space-y-2" data-testid="sr-row-list">
              <div className="grid grid-cols-[1fr_110px_60px_40px] gap-2 text-[10px] uppercase tracking-wider text-slate-400 px-1">
                <span>Product</span>
                <span className="text-center">Qty needed</span>
                <span className="text-center">Unit</span>
                <span></span>
              </div>

              {rows.map((row, idx) => (
                <div key={row.id}
                     className="grid grid-cols-[1fr_110px_60px_40px] gap-2 items-start"
                     data-testid={`sr-row-${row.id}`}>
                  {/* Empty row → SmartProductSearch (typeahead w/ both-branch qty)
                      Filled row → compact summary card + remove btn. */}
                  <div className="relative" data-testid={`sr-row-search-${row.id}`}>
                    {!row.product ? (
                      <SmartProductSearch
                        mode="request"
                        branchId={supplyingBranchId || undefined}
                        alsoBranchId={myBranchId || undefined}
                        onSelect={(p) => selectProduct(row.id, p)}
                        placeholder={idx === 0 ? 'Search product to request…' : 'Add another product…'}
                      />
                    ) : (
                      <div className="h-9 px-3 rounded-md border border-emerald-200 bg-emerald-50/40 flex items-center justify-between gap-3"
                           data-testid={`sr-row-product-${row.id}`}>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="truncate font-medium text-sm">{row.product.name}</span>
                            <span className="text-[10px] text-slate-400 font-mono shrink-0">{row.product.sku}</span>
                          </div>
                          <div className="flex gap-3 text-[10px] -mt-0.5">
                            <span className={`font-semibold ${row.product.supplying_qty > 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                              {targetBranch?.name || 'Supply'}: {row.product.supplying_qty} {row.product.unit}
                            </span>
                            <span className={`font-semibold ${row.product.requesting_qty > 0 ? 'text-amber-600' : 'text-slate-400'}`}>
                              You: {row.product.requesting_qty} {row.product.unit}
                            </span>
                          </div>
                        </div>
                        <button type="button"
                                onClick={() => updateRow(row.id, { product: null, qty: '' })}
                                className="text-slate-300 hover:text-rose-500 shrink-0"
                                title="Change product"
                                data-testid={`sr-row-clear-${row.id}`}>
                          <X size={13} />
                        </button>
                      </div>
                    )}
                  </div>

                  <CalcInput
                    ref={(el) => { qtyRefs.current[row.id] = el; }}
                    className="h-9 text-sm text-center"
                    value={row.qty}
                    onChange={(v) => updateRow(row.id, { qty: v })}
                    placeholder="0"
                    disabled={!row.product}
                    onKeyDown={(e) => handleQtyKeyDown(e, idx)}
                    data-testid={`sr-row-qty-${row.id}`}
                  />
                  <span className="h-9 flex items-center justify-center text-xs text-slate-500">
                    {row.product?.unit || '—'}
                  </span>
                  <Button variant="ghost" size="sm" tabIndex={-1}
                          className="h-9 w-9 p-0 text-slate-400 hover:text-rose-500"
                          onClick={() => removeRow(row.id)}
                          disabled={rows.length <= 1}>
                    <X size={14} />
                  </Button>
                </div>
              ))}

              <p className="text-[10px] text-slate-400 italic pt-1">
                Tip: search → Tab → type qty → Tab again → next product. Last empty row auto-grows.
              </p>
            </div>
          )}

          <div>
            <Label className="text-xs text-slate-600">Notes (optional)</Label>
            <Input value={notes} onChange={e => setNotes(e.target.value)} className="mt-1"
                   placeholder="Any context for the supplying branch…" />
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-slate-100">
            <div className="text-xs text-slate-500">
              <span className="font-medium">{filledCount}</span> product(s) ready
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="h-9 px-4" onClick={resetForm}
                      disabled={submitting}>
                Clear
              </Button>
              <Button onClick={submit} disabled={submitting || !filledCount || !supplyingBranchId}
                      className="bg-teal-600 hover:bg-teal-700 text-white h-9 px-6"
                      data-testid="submit-request-btn">
                {submitting ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Send size={14} className="mr-1.5" />}
                Send Request
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* ── Detail dialog with triage ────────────────────────────────────────── */
function DetailDialog({ requestId, currentBranchId, onClose }) {
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [suppliers, setSuppliers] = useState([]);
  const [assignments, setAssignments] = useState({});  // item_id → assignment
  const [pin, setPin] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // Phase 2 — Mark Ordered inline form per PO
  const [orderingPoId, setOrderingPoId] = useState(null);
  const [orderForm, setOrderForm] = useState({ pin: '', supplier_ref: '', expected_delivery_date: '', notes: '' });
  const [orderSubmitting, setOrderSubmitting] = useState(false);
  // Phase 3+ — Timeline
  const [timeline, setTimeline] = useState([]);
  const [timelineLoading, setTimelineLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setTimelineLoading(true);
    try {
      const [res, tlRes] = await Promise.all([
        api.get(`/stock-requests/${requestId}`),
        api.get(`/stock-requests/${requestId}/timeline`),
      ]);
      setDoc(res.data);
      setTimeline(tlRes.data?.events || []);
      // Init blank assignments for triage UI
      const init = {};
      (res.data.items || []).forEach(it => {
        init[it.id] = {
          item_id: it.id,
          fulfillment_type: it.fulfillment_type || '',
          supplier_id: it.supplier_id || '',
          supplier_name: it.supplier_name || '',
          unit_price: 0,
        };
      });
      setAssignments(init);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load request');
    }
    setLoading(false);
    setTimelineLoading(false);
  }, [requestId]);

  useEffect(() => { reload(); }, [reload]);

  // Load suppliers for the REQUESTING branch (PO will live there)
  useEffect(() => {
    if (!doc?.requesting_branch_id) return;
    api.get(`/suppliers?branch_id=${doc.requesting_branch_id}`)
      .then(r => setSuppliers(r.data?.suppliers || r.data || []))
      .catch(() => setSuppliers([]));
  }, [doc?.requesting_branch_id]);

  const isSupplyingBranch = doc && doc.supplying_branch_id === currentBranchId;
  const canTriage = isSupplyingBranch && ['sent', 'in_triage'].includes(doc?.status);

  const updateAssign = (item_id, patch) => {
    setAssignments(prev => ({ ...prev, [item_id]: { ...prev[item_id], ...patch } }));
  };

  const submitTriage = async () => {    if (!pin.trim()) { toast.error('PIN required'); return; }
    const assigns = Object.values(assignments).filter(a => a.fulfillment_type);
    if (!assigns.length) { toast.error('Assign at least one line'); return; }
    setSubmitting(true);
    try {
      const res = await api.post(`/stock-requests/${requestId}/triage`, {
        pin,
        assignments: assigns,
      });
      toast.success(
        `Generated: ${res.data.bto_id ? '1 BTO' : '0 BTO'} + ${res.data.po_ids.length} PO(s)` +
        (res.data.unfulfilled ? ` (${res.data.unfulfilled} unfulfilled)` : '')
      );
      reload();
      setPin('');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Triage failed');
    }
    setSubmitting(false);
  };

  const submitMarkOrdered = async (po_id) => {
    if (!orderForm.pin.trim()) { toast.error('PIN required to mark ordered'); return; }
    setOrderSubmitting(true);
    try {
      const res = await api.post(
        `/stock-requests/${requestId}/po/${po_id}/mark-ordered`,
        {
          pin: orderForm.pin,
          supplier_ref: orderForm.supplier_ref,
          expected_delivery_date: orderForm.expected_delivery_date,
          notes: orderForm.notes,
        }
      );
      toast.success(`PO ${res.data.po_number} marked Ordered. Branch A notified.`);
      setOrderingPoId(null);
      setOrderForm({ pin: '', supplier_ref: '', expected_delivery_date: '', notes: '' });
      reload();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to mark ordered');
    }
    setOrderSubmitting(false);
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto" data-testid="request-detail-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ClipboardList size={18} className="text-teal-600" />
            {doc?.request_number || 'Loading…'}
            {doc && <StatusBadge status={doc.status} />}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {doc && `Created ${fmtDateTime(doc.created_at)} by ${doc.created_by_name || 'Unknown'}`}
          </DialogDescription>
        </DialogHeader>

        {loading || !doc ? (
          <div className="py-10 text-center text-slate-400 text-sm">Loading…</div>
        ) : (
          <>
            {/* ── Linked child docs banner (post-triage) ─────────────── */}
            {(doc.bto || (doc.pos || []).length > 0) && (
              <div className="bg-sky-50 border border-sky-200 rounded-lg p-3 text-xs space-y-1.5" data-testid="linked-docs">
                <p className="font-semibold text-sky-900">Fulfillment plan</p>
                {doc.bto && (
                  <div className="flex items-center gap-2 text-slate-700">
                    <Truck size={12} /> BTO <span className="font-mono">{doc.bto.order_number}</span>
                    <StatusBadge status={doc.bto.status} />
                  </div>
                )}
                {(doc.pos || []).map(po => {
                  const isDraftPhantom = po.status === 'draft' && po.phantom_for_branch_id === currentBranchId;
                  const isExpanded = orderingPoId === po.id;
                  return (
                    <div key={po.id} className="space-y-1.5" data-testid={`linked-po-${po.id}`}>
                      <div className="flex items-center gap-2 text-slate-700 flex-wrap">
                        <FileText size={12} /> PO <span className="font-mono">{po.po_number}</span>
                        · {po.vendor} · <span className="font-mono">{formatPHP(po.grand_total)}</span>
                        <Badge className="text-[10px] bg-slate-100 text-slate-700">{po.status}</Badge>
                        {po.received_variance_kind && VARIANCE_BADGE[po.received_variance_kind] && (
                          <Badge className={`text-[10px] border ${VARIANCE_BADGE[po.received_variance_kind].cls}`}
                                 data-testid={`variance-badge-${po.id}`}>
                            {po.received_variance_kind === 'completed' ? <Check size={9} className="mr-0.5" /> : <AlertTriangle size={9} className="mr-0.5" />}
                            {VARIANCE_BADGE[po.received_variance_kind].label}
                          </Badge>
                        )}
                        {isDraftPhantom && !isExpanded && (
                          <Button
                            size="sm" variant="outline"
                            className="h-6 px-2 text-[10px] ml-auto border-teal-300 text-teal-700 hover:bg-teal-50"
                            onClick={() => { setOrderingPoId(po.id); }}
                            data-testid={`mark-ordered-btn-${po.id}`}
                          >
                            <Send size={10} className="mr-1" /> Mark Ordered
                          </Button>
                        )}
                      </div>

                      {/* Per-item variance breakdown — shown only when there
                          IS a non-trivial variance (skip 'completed' since
                          everything matched). */}
                      {po.received_variance
                        && po.received_variance_kind !== 'completed'
                        && (po.received_variance.items_variance || []).some(iv => iv.kind !== 'match') && (
                        <div className="bg-white border border-slate-200 rounded text-[10px] ml-5 p-2 space-y-0.5"
                             data-testid={`variance-detail-${po.id}`}>
                          <p className="font-semibold text-slate-600 mb-0.5">Variance vs ordered</p>
                          {po.received_variance.items_variance.filter(iv => iv.kind !== 'match').map((iv, k) => {
                            const tone = iv.kind === 'missing' ? 'text-rose-600'
                                      : iv.kind === 'extra'   ? 'text-violet-600'
                                      : iv.kind === 'under'   ? 'text-amber-600'
                                      : 'text-sky-600';
                            const arrow = iv.delta > 0 ? '+' : '';
                            return (
                              <div key={k} className={`flex justify-between ${tone}`}>
                                <span className="truncate">{iv.product_name}</span>
                                <span className="font-mono shrink-0">
                                  {iv.ordered_qty} → {iv.received_qty}
                                  <span className="text-[9px] ml-1">({arrow}{iv.delta})</span>
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {isExpanded && (
                        <div className="bg-white border border-teal-200 rounded-lg p-3 space-y-2"
                             data-testid={`mark-ordered-form-${po.id}`}>
                          <p className="text-[11px] text-teal-900 font-semibold">
                            Confirm with {po.vendor} — locks the price + notifies Branch A
                          </p>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                            <div>
                              <Label className="text-[10px] text-slate-500">Supplier reference (optional)</Label>
                              <Input value={orderForm.supplier_ref}
                                     onChange={e => setOrderForm(f => ({ ...f, supplier_ref: e.target.value }))}
                                     placeholder="e.g. SUP-INV-2026-0042" className="h-8 text-xs"
                                     data-testid={`order-supplier-ref-${po.id}`} />
                            </div>
                            <div>
                              <Label className="text-[10px] text-slate-500">Expected delivery</Label>
                              <Input type="date" value={orderForm.expected_delivery_date}
                                     onChange={e => setOrderForm(f => ({ ...f, expected_delivery_date: e.target.value }))}
                                     className="h-8 text-xs"
                                     data-testid={`order-delivery-date-${po.id}`} />
                            </div>
                          </div>
                          <div>
                            <Label className="text-[10px] text-slate-500">Notes (optional)</Label>
                            <Input value={orderForm.notes}
                                   onChange={e => setOrderForm(f => ({ ...f, notes: e.target.value }))}
                                   placeholder="Delivery method, special instructions…"
                                   className="h-8 text-xs"
                                   data-testid={`order-notes-${po.id}`} />
                          </div>
                          <div className="flex gap-2 items-end pt-1">
                            <div>
                              <Label className="text-[10px] text-slate-500">Branch PIN</Label>
                              <Input type="password" value={orderForm.pin}
                                     onChange={e => setOrderForm(f => ({ ...f, pin: e.target.value }))}
                                     placeholder="••••••" className="w-28 h-8 text-xs"
                                     data-testid={`order-pin-${po.id}`} />
                            </div>
                            <Button onClick={() => submitMarkOrdered(po.id)}
                                    disabled={orderSubmitting}
                                    className="bg-teal-600 hover:bg-teal-700 text-white h-8 text-xs"
                                    data-testid={`order-submit-${po.id}`}>
                              {orderSubmitting ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Check size={12} className="mr-1" />}
                              Confirm Ordered
                            </Button>
                            <Button variant="outline" onClick={() => setOrderingPoId(null)}
                                    className="h-8 text-xs">
                              Cancel
                            </Button>
                          </div>
                        </div>
                      )}
                      {po.status === 'ordered' && (po.ordered_by_name || po.supplier_ref) && (
                        <p className="text-[10px] text-slate-400 ml-5">
                          Ordered by {po.ordered_by_name || 'Manager'}
                          {po.supplier_ref && <> · ref <span className="font-mono">{po.supplier_ref}</span></>}
                          {po.expected_delivery_date && <> · ETA {po.expected_delivery_date}</>}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* ── Items table + triage UI ────────────────────────────── */}
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs" data-testid="detail-items-table">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-slate-600">Product</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">Qty</th>
                    {canTriage && <th className="text-left px-3 py-2 font-medium text-slate-600 w-44">Fulfill via</th>}
                    {canTriage && <th className="text-left px-3 py-2 font-medium text-slate-600">Supplier / Price</th>}
                    {!canTriage && <th className="text-left px-3 py-2 font-medium text-slate-600">Status</th>}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(doc.items || []).map(it => {
                    const a = assignments[it.id] || {};
                    return (
                      <tr key={it.id} data-testid={`detail-row-${it.id}`}>
                        <td className="px-3 py-2 font-medium text-slate-800">{it.product_name}</td>
                        <td className="px-3 py-2 text-right font-mono">{it.qty} {it.unit}</td>
                        {canTriage ? (
                          <>
                            <td className="px-3 py-2">
                              <Select value={a.fulfillment_type || ''}
                                      onValueChange={(v) => updateAssign(it.id, { fulfillment_type: v })}>
                                <SelectTrigger className="h-8 text-xs" data-testid={`assign-type-${it.id}`}>
                                  <SelectValue placeholder="Pick…" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="transfer">🚚 Personal Transfer</SelectItem>
                                  <SelectItem value="supplier_po">📦 Order from Supplier</SelectItem>
                                  <SelectItem value="unfulfilled">❌ Cannot Fulfill</SelectItem>
                                </SelectContent>
                              </Select>
                            </td>
                            <td className="px-3 py-2">
                              {a.fulfillment_type === 'supplier_po' && (
                                <div className="flex gap-1.5">
                                  <SupplierPicker
                                    suppliers={suppliers}
                                    valueId={a.supplier_id}
                                    valueName={a.supplier_name}
                                    onChange={(sid, sname) =>
                                      updateAssign(it.id, { supplier_id: sid, supplier_name: sname })}
                                    testid={`supplier-picker-${it.id}`}
                                  />
                                  <Input
                                    type="number" placeholder="Price/unit"
                                    value={a.unit_price || ''}
                                    onChange={e => updateAssign(it.id, { unit_price: parseFloat(e.target.value) || 0 })}
                                    className="w-24 h-8 text-xs"
                                    data-testid={`unit-price-${it.id}`}
                                  />
                                </div>
                              )}
                            </td>
                          </>
                        ) : (
                          <td className="px-3 py-2">
                            <Badge className="text-[10px] bg-slate-100 text-slate-700">
                              {it.fulfillment_type || 'pending'}
                            </Badge>
                            {it.supplier_name && (
                              <span className="text-[10px] text-slate-400 ml-1.5">{it.supplier_name}</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* ── PIN + Apply Triage ─────────────────────────────────── */}
            {canTriage && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2" data-testid="triage-apply">
                <Label className="text-xs text-amber-900 font-semibold">Manager / Branch PIN</Label>
                <div className="flex gap-2">
                  <Input type="password" value={pin} onChange={e => setPin(e.target.value)}
                         placeholder="••••••" className="w-32"
                         data-testid="triage-pin-input" />
                  <Button onClick={submitTriage} disabled={submitting}
                          className="bg-teal-600 hover:bg-teal-700 text-white"
                          data-testid="triage-submit-btn">
                    {submitting ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                    Generate Fulfillment Plan
                  </Button>
                </div>
                <p className="text-[10px] text-amber-700">
                  Will create 1 BTO (transfer lines coalesced) + 1 DRAFT PO per supplier.
                  Once generated, prices on supplier POs are locked.
                </p>
              </div>
            )}

            {!canTriage && doc.status === 'sent' && !isSupplyingBranch && (
              <p className="text-xs text-slate-400 italic">Waiting on the supplying branch to triage.</p>
            )}

            {/* ── Activity Timeline (Phase 3+) ────────────────────────── */}
            <Timeline events={timeline} loading={timelineLoading} />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ── Activity Timeline ────────────────────────────────────────────────── */
const KIND_STYLE = {
  'request.created':   { color: 'bg-slate-400',   Icon: Plus },
  'request.sent':      { color: 'bg-amber-500',   Icon: Send },
  'request.triaged':   { color: 'bg-sky-500',     Icon: Check },
  'request.cancelled': { color: 'bg-rose-500',    Icon: X },
  'request.completed': { color: 'bg-emerald-500', Icon: Check },
  'bto.created':       { color: 'bg-amber-500',   Icon: Truck },
  'bto.sent':          { color: 'bg-amber-600',   Icon: Truck },
  'bto.received':      { color: 'bg-emerald-500', Icon: Check },
  'bto.cancelled':     { color: 'bg-rose-500',    Icon: X },
  'po.created':        { color: 'bg-slate-400',   Icon: FileText },
  'po.ordered':        { color: 'bg-teal-500',    Icon: Send },
  'po.received':       { color: 'bg-emerald-500', Icon: Check },
  'po.cancelled':      { color: 'bg-rose-500',    Icon: X },
};

function Timeline({ events, loading }) {
  if (loading && !events.length) {
    return <div className="text-xs text-slate-400 py-4 text-center">Loading timeline…</div>;
  }
  if (!events.length) return null;
  return (
    <div className="pt-2" data-testid="timeline">
      <p className="text-xs font-semibold text-slate-700 mb-2 flex items-center gap-1.5">
        <RefreshCw size={11} className="text-slate-400" /> Activity
      </p>
      <div className="relative pl-6 space-y-2.5 border-l-2 border-slate-100 ml-2">
        {events.map((e, i) => {
          const style = KIND_STYLE[e.kind] || { color: 'bg-slate-400', Icon: Check };
          const Icon = style.Icon;
          return (
            <div key={i} className="relative" data-testid={`timeline-event-${e.kind}`}>
              <div className={`absolute -left-[27px] top-0.5 w-4 h-4 rounded-full ${style.color} flex items-center justify-center ring-2 ring-white`}>
                <Icon size={9} className="text-white" />
              </div>
              <div className="text-xs">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-medium text-slate-800">{e.label}</span>
                  {e.actor && (
                    <span className="text-[10px] text-slate-500">· {e.actor}</span>
                  )}
                  <span className="text-[10px] text-slate-400 ml-auto font-mono">
                    {fmtDateTime(e.at)}
                  </span>
                </div>
                {e.detail && (
                  <p className="text-[11px] text-slate-500 mt-0.5">{e.detail}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Supplier picker (existing + quick-create) ────────────────────────── */
function SupplierPicker({ suppliers, valueId, valueName, onChange, testid }) {
  const [mode, setMode] = useState(valueName && !valueId ? 'quick' : 'list');
  if (mode === 'quick') {
    return (
      <div className="flex gap-1">
        <Input
          value={valueName || ''} onChange={e => onChange('', e.target.value)}
          placeholder="New supplier name" className="w-40 h-8 text-xs"
          data-testid={testid}
        />
        <button onClick={() => { setMode('list'); onChange('', ''); }}
                className="text-slate-400 hover:text-slate-600 px-1">
          <X size={12} />
        </button>
      </div>
    );
  }
  return (
    <div className="flex gap-1">
      <Select value={valueId || ''} onValueChange={(v) => {
        const s = suppliers.find(x => x.id === v);
        onChange(v, s?.name || '');
      }}>
        <SelectTrigger className="h-8 text-xs w-40" data-testid={testid}>
          <SelectValue placeholder="Supplier…" />
        </SelectTrigger>
        <SelectContent>
          {suppliers.length === 0 && (
            <div className="px-2 py-1.5 text-[11px] text-slate-400">No suppliers — use Quick Create</div>
          )}
          {suppliers.map(s => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
        </SelectContent>
      </Select>
      <button type="button" onClick={() => setMode('quick')}
              className="text-[10px] text-teal-600 hover:underline px-1 whitespace-nowrap">
        + new
      </button>
    </div>
  );
}
