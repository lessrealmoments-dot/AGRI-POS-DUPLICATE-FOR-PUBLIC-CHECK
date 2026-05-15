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
import { useState, useEffect, useCallback, useMemo } from 'react';
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
import { ClipboardList, Plus, Search, Trash2, Send, ArrowRight,
         Package, Truck, X, Check, RefreshCw, FileText, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';

const STATUS_BADGE = {
  draft:                   { label: 'Draft',           cls: 'bg-slate-100 text-slate-700' },
  sent:                    { label: 'Awaiting Triage', cls: 'bg-amber-100 text-amber-700' },
  in_triage:               { label: 'Triaging',        cls: 'bg-amber-100 text-amber-700' },
  fulfillment_generated:   { label: 'In Fulfillment',  cls: 'bg-sky-100 text-sky-700' },
  completed:               { label: 'Completed',       cls: 'bg-emerald-100 text-emerald-700' },
  cancelled:               { label: 'Cancelled',       cls: 'bg-rose-100 text-rose-700' },
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
function NewRequestForm({ currentBranch, branches, onCreated }) {
  const [supplyingBranchId, setSupplyingBranchId] = useState('');
  const [notes, setNotes] = useState('');
  const [items, setItems] = useState([]);   // {product_id, product_name, qty, unit, supplying_qty}
  const [search, setSearch] = useState('');
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const eligibleBranches = useMemo(
    () => (branches || []).filter(b => b.id !== currentBranch?.id),
    [branches, currentBranch?.id]
  );

  // Debounced product search showing inventory in BOTH branches.
  useEffect(() => {
    if (!search.trim() || !supplyingBranchId || !currentBranch?.id) {
      setResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await api.get(
          `/stock-requests/products-lookup?requesting_branch_id=${currentBranch.id}` +
          `&supplying_branch_id=${supplyingBranchId}&q=${encodeURIComponent(search.trim())}&limit=15`
        );
        setResults(res.data.items || []);
      } catch (e) { /* silent */ }
      setSearching(false);
    }, 250);
    return () => clearTimeout(t);
  }, [search, supplyingBranchId, currentBranch?.id]);

  const addItem = (p) => {
    if (items.find(i => i.product_id === p.id)) {
      toast.info(`${p.name} is already on the request.`);
      return;
    }
    setItems([...items, {
      product_id:   p.id,
      product_name: p.name,
      qty:          1,
      unit:         p.unit || '',
      requesting_qty: p.requesting_qty,
      supplying_qty:  p.supplying_qty,
    }]);
    setSearch('');
    setResults([]);
  };

  const updateQty = (idx, qty) => {
    const next = [...items];
    next[idx].qty = Math.max(0, parseFloat(qty) || 0);
    setItems(next);
  };
  const removeItem = (idx) => setItems(items.filter((_, i) => i !== idx));

  const submit = async () => {
    if (!supplyingBranchId) { toast.error('Pick a supplying branch first'); return; }
    if (!items.length) { toast.error('Add at least one product'); return; }
    if (items.some(i => i.qty <= 0)) { toast.error('Every line needs a qty > 0'); return; }
    setSubmitting(true);
    try {
      const res = await api.post('/stock-requests', {
        requesting_branch_id: currentBranch.id,
        supplying_branch_id:  supplyingBranchId,
        items: items.map(i => ({
          product_id:   i.product_id,
          product_name: i.product_name,
          qty:          i.qty,
          unit:         i.unit,
        })),
        notes,
      });
      // Immediately send (skip the "draft" intermediate state for MVP).
      await api.post(`/stock-requests/${res.data.id}/send`);
      toast.success(`Stock request ${res.data.request_number} sent.`);
      onCreated(res.data.id);
      setSupplyingBranchId(''); setNotes(''); setItems([]); setSearch('');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to send request');
    }
    setSubmitting(false);
  };

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
            <div>
              <Label className="text-xs text-slate-600">Add Products</Label>
              <div className="relative mt-1">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  value={search} onChange={e => setSearch(e.target.value)}
                  placeholder="Search by name or SKU…"
                  className="pl-8"
                  data-testid="product-search-input"
                />
                {searching && (
                  <RefreshCw size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />
                )}
              </div>
              {results.length > 0 && (
                <div className="mt-1 border rounded-lg bg-white shadow-sm divide-y max-h-64 overflow-y-auto"
                     data-testid="product-search-results">
                  {results.map(p => (
                    <button key={p.id} type="button" onClick={() => addItem(p)}
                            className="w-full text-left px-3 py-2 hover:bg-slate-50 grid grid-cols-12 gap-2 items-center"
                            data-testid={`product-result-${p.id}`}>
                      <div className="col-span-6 min-w-0">
                        <div className="font-medium text-sm text-slate-800 truncate">{p.name}</div>
                        <div className="text-[10px] text-slate-400">{p.sku}</div>
                      </div>
                      <div className="col-span-3 text-right">
                        <div className="text-[10px] text-slate-400">My stock</div>
                        <div className="font-mono text-xs font-semibold text-slate-700">{p.requesting_qty}</div>
                      </div>
                      <div className="col-span-3 text-right">
                        <div className="text-[10px] text-slate-400">Their stock</div>
                        <div className={`font-mono text-xs font-semibold ${
                          p.supplying_qty > 0 ? 'text-emerald-700' : 'text-rose-600'
                        }`}>{p.supplying_qty}</div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {items.length > 0 && (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs" data-testid="request-items-table">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-slate-600">Product</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">My stock</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">Their stock</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">Qty needed</th>
                    <th className="w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {items.map((i, idx) => (
                    <tr key={i.product_id} data-testid={`item-row-${i.product_id}`}>
                      <td className="px-3 py-2 font-medium text-slate-800">{i.product_name}</td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{i.requesting_qty}</td>
                      <td className={`px-3 py-2 text-right font-mono ${
                        i.supplying_qty > 0 ? 'text-emerald-700' : 'text-rose-600 font-semibold'
                      }`}>{i.supplying_qty}</td>
                      <td className="px-3 py-2 text-right">
                        <Input
                          type="number" min="0" step="any" value={i.qty}
                          onChange={e => updateQty(idx, e.target.value)}
                          className="w-20 h-7 text-right text-xs"
                        />
                      </td>
                      <td className="px-2">
                        <button onClick={() => removeItem(idx)} className="text-rose-400 hover:text-rose-600 p-1">
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div>
            <Label className="text-xs text-slate-600">Notes (optional)</Label>
            <Input value={notes} onChange={e => setNotes(e.target.value)} className="mt-1" placeholder="Any context for the supplying branch…" />
          </div>

          <Button onClick={submit} disabled={submitting || !items.length || !supplyingBranchId}
                  className="bg-teal-600 hover:bg-teal-700 text-white w-full md:w-auto"
                  data-testid="submit-request-btn">
            {submitting ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Send size={14} className="mr-1.5" />}
            Send Request
          </Button>
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

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/stock-requests/${requestId}`);
      setDoc(res.data);
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

  const submitTriage = async () => {
    if (!pin.trim()) { toast.error('PIN required'); return; }
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
                {(doc.pos || []).map(po => (
                  <div key={po.id} className="flex items-center gap-2 text-slate-700">
                    <FileText size={12} /> PO <span className="font-mono">{po.po_number}</span>
                    · {po.vendor} · <span className="font-mono">{formatPHP(po.grand_total)}</span>
                    <Badge className="text-[10px] bg-slate-100 text-slate-700">{po.status}</Badge>
                  </div>
                ))}
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
          </>
        )}
      </DialogContent>
    </Dialog>
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
