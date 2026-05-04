import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import { toast } from 'sonner';
import {
  Package, RefreshCw, ExternalLink, AlertTriangle, Search,
  CheckCircle2, Lock, ShieldCheck, Boxes, ChevronDown
} from 'lucide-react';
import CalcInput from '../components/CalcInput';

const fmtDate = (d) => { try { return new Date(d).toLocaleDateString('en-PH', { month: 'short', day: 'numeric', year: 'numeric' }); } catch { return d || ''; } };
const fmtDateTime = (d) => { try { return new Date(d).toLocaleString('en-PH', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch { return d || ''; } };

function daysSince(dateStr) {
  try { return Math.floor((Date.now() - new Date(dateStr).getTime()) / 86400000); }
  catch { return 0; }
}

// ── Inline Release Dialog ─────────────────────────────────────────────────────
function ReleaseDialog({ open, onClose, invoice, onReleased }) {
  const [state, setState] = useState('loading'); // loading | form | confirming | done
  const [reservations, setReservations] = useState([]);
  const [releaseHistory, setReleaseHistory] = useState([]);
  const [releaseItems, setReleaseItems] = useState([]);
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [confirmItems, setConfirmItems] = useState([]);
  const [releasing, setReleasing] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!open || !invoice) return;
    setState('loading');
    setPin(''); setPinError(''); setResult(null);
    api.get(`/stock-releases/${invoice.id}`)
      .then(res => {
        const resv = res.data.reservations || [];
        const hist = res.data.invoice?.stock_releases || [];
        setReservations(resv);
        setReleaseHistory(hist);
        setReleaseItems(resv.map(r => ({
          ...r,
          input_qty: r.sold_qty_remaining > 0 ? String(r.sold_qty_remaining) : '0',
        })));
        setState('form');
      })
      .catch(() => { toast.error('Failed to load release details'); onClose(); });
  }, [open, invoice]); // eslint-disable-line

  const setQty = (idx, val) =>
    setReleaseItems(prev => prev.map((it, i) => i === idx ? { ...it, input_qty: val } : it));

  const handlePrepare = () => {
    if (!pin) { setPinError('PIN is required'); return; }
    const toRelease = releaseItems.filter(it => parseFloat(it.input_qty || 0) > 0 && it.sold_qty_remaining > 0);
    if (!toRelease.length) { setPinError('Enter at least one quantity to release'); return; }
    for (const it of toRelease) {
      if (parseFloat(it.input_qty) > it.sold_qty_remaining + 0.001) {
        setPinError(`Cannot release more than ${it.sold_qty_remaining} for ${it.sold_product_name}`); return;
      }
    }
    setPinError('');
    setConfirmItems(toRelease);
    setState('confirming');
  };

  const handleConfirm = async () => {
    setReleasing(true); setPinError('');
    const releaseRef = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    const items = confirmItems.map(it => ({
      sold_product_id: it.sold_product_id,
      qty_release: parseFloat(it.input_qty),
    }));
    try {
      const res = await api.post(`/qr-actions/${invoice.doc_code}/release_stocks`, {
        pin, release_ref: releaseRef, items,
      });
      setResult(res.data);
      setState('done');
      if (onReleased) onReleased(invoice.id, res.data.stock_release_status);
    } catch (e) {
      setPinError(e.response?.data?.detail || 'Release failed');
      setState('form');
    }
    setReleasing(false);
  };

  const canRelease = invoice?.stock_release_status !== 'fully_released' &&
                     invoice?.stock_release_status !== 'expired';

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Manrope' }}>
            Release Stocks — {invoice?.invoice_number}
          </DialogTitle>
          <p className="text-xs text-slate-500">{invoice?.customer_name || 'Walk-in'}</p>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-4 pr-1">

          {/* Release history */}
          {releaseHistory.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Release History</p>
              <div className="space-y-1.5">
                {releaseHistory.map((r, i) => (
                  <div key={i} className="bg-slate-50 rounded-lg px-3 py-2 text-xs">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-semibold text-slate-700">Release #{r.release_number}</span>
                      <span className="text-slate-400">{fmtDateTime(r.released_at)}</span>
                    </div>
                    {r.items?.map((it, j) => (
                      <div key={j} className="flex justify-between text-slate-500">
                        <span className="truncate">{it.product_name}</span>
                        <span className="font-medium ml-2 shrink-0">{it.qty_released} {it.unit}</span>
                      </div>
                    ))}
                    <div className="text-slate-400 mt-1">
                      By {r.released_by_name}
                      {r.remaining_after > 0
                        ? <span className="text-amber-600 ml-1">· {r.remaining_after} remaining</span>
                        : <span className="text-emerald-600 ml-1">· All released</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {state === 'loading' && (
            <div className="flex items-center justify-center py-8 text-slate-400">
              <RefreshCw size={18} className="animate-spin mr-2" /> Loading...
            </div>
          )}

          {state === 'done' && result && (
            <div className="text-center py-4 space-y-2">
              <CheckCircle2 size={36} className="text-emerald-500 mx-auto" />
              <p className="font-semibold text-emerald-700">
                Release #{result.release_number} recorded!
              </p>
              <p className="text-sm text-slate-500">{result.message}</p>
              {!result.fully_released && (
                <Button variant="outline" size="sm" onClick={() => {
                  setState('form'); setResult(null); setPin(''); setPinError('');
                  // reload reservations
                  api.get(`/stock-releases/${invoice.id}`).then(res => {
                    const resv = res.data.reservations || [];
                    const hist = res.data.invoice?.stock_releases || [];
                    setReservations(resv);
                    setReleaseHistory(hist);
                    setReleaseItems(resv.map(r => ({
                      ...r, input_qty: r.sold_qty_remaining > 0 ? String(r.sold_qty_remaining) : '0',
                    })));
                  });
                }}>
                  Release More
                </Button>
              )}
            </div>
          )}

          {(state === 'form') && canRelease && (
            <div className="space-y-3">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                {releaseHistory.length > 0 ? 'Next Release' : 'Items to Release'}
              </p>
              <div className="space-y-2">
                {releaseItems.map((it, idx) => {
                  const done = it.sold_qty_remaining <= 0;
                  return (
                    <div key={it.sold_product_id}
                      className={`rounded-lg border p-3 ${done ? 'bg-slate-50 opacity-50' : 'bg-white border-slate-200'}`}>
                      <div className="flex items-center justify-between mb-1.5">
                        <div>
                          <p className="text-sm font-semibold text-slate-800">{it.sold_product_name}</p>
                          <p className="text-xs text-slate-400">
                            Ordered: {it.sold_qty_ordered} · Released: {it.sold_qty_released} ·{' '}
                            <span className={it.sold_qty_remaining > 0 ? 'text-amber-600 font-medium' : 'text-emerald-600'}>
                              {it.sold_qty_remaining} remaining
                            </span>
                          </p>
                        </div>
                        {done && <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />}
                      </div>
                      {!done && (
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-slate-500 shrink-0">Release now:</span>
                          <CalcInput autoComplete="off"
 value={it.input_qty}
 onChange={(v) => setQty(idx, v)}
 className="h-8 w-24 text-center font-semibold rounded-md border border-input bg-background text-sm px-2"
 data-testid={`release-qty-${it.sold_product_id}`} />
                          <span className="text-xs text-slate-400">{it.sold_unit}</span>
                          <button
                            onClick={() => setQty(idx, String(it.sold_qty_remaining))}
                            className="text-xs text-blue-600 hover:underline ml-auto">
                            All
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* PIN */}
              <div className="space-y-1.5 pt-1">
                <label className="text-xs font-medium text-slate-600 flex items-center gap-1.5">
                  <Lock size={11} /> Authorization PIN
                </label>
                <Input
                  type="password" autoComplete="one-time-code"
                  value={pin} onChange={e => { setPin(e.target.value); setPinError(''); }}
                  onKeyDown={e => e.key === 'Enter' && handlePrepare()}
                  placeholder="Manager PIN, Admin PIN, or TOTP"
                  className="h-10 font-mono text-center tracking-widest"
                  data-testid="release-dialog-pin"
                />
              </div>
              {pinError && (
                <p className="text-red-500 text-xs flex items-center gap-1">
                  <AlertTriangle size={12} />{pinError}
                </p>
              )}
            </div>
          )}

          {state === 'form' && !canRelease && releaseHistory.length === 0 && (
            <p className="text-slate-400 text-sm text-center py-4">
              {invoice?.stock_release_status === 'fully_released' ? 'All stock has been released.' : 'No release actions available.'}
            </p>
          )}

          {/* Confirmation step */}
          {state === 'confirming' && (
            <div className="space-y-3">
              <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-2">
                <p className="text-sm font-semibold text-amber-800 flex items-center gap-2">
                  <Boxes size={14} /> Confirm Stock Release
                </p>
                <div className="rounded-lg border border-amber-200 overflow-hidden mt-2">
                  <div className="bg-amber-50/50 px-3 py-1.5 flex justify-between text-[10px] font-semibold text-slate-500 uppercase">
                    <span>Product</span><span>Qty</span>
                  </div>
                  {confirmItems.map((it, i) => (
                    <div key={i} className={`px-3 py-2 flex justify-between text-sm ${i > 0 ? 'border-t border-amber-100' : ''}`}>
                      <span className="text-slate-700">{it.sold_product_name}</span>
                      <span className="font-bold text-amber-700">{parseFloat(it.input_qty)} {it.sold_unit}</span>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-slate-500 flex items-center gap-1 mt-1">
                  <ShieldCheck size={11} className="text-emerald-500" /> PIN verified
                </p>
              </div>
              {pinError && (
                <p className="text-red-500 text-xs flex items-center gap-1">
                  <AlertTriangle size={12} />{pinError}
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div className="pt-3 border-t border-slate-100 flex gap-2">
          {state === 'form' && canRelease && (
            <>
              <Button variant="outline" className="flex-1" onClick={onClose}>Cancel</Button>
              <Button
                className="flex-1 bg-amber-600 hover:bg-amber-700 text-white"
                onClick={handlePrepare}
                data-testid="release-dialog-prepare-btn"
              >
                <Boxes size={13} className="mr-1.5" /> Review & Release
              </Button>
            </>
          )}
          {state === 'confirming' && (
            <>
              <Button variant="outline" className="flex-1" onClick={() => setState('form')}>Back</Button>
              <Button
                className="flex-1 bg-amber-600 hover:bg-amber-700 text-white font-semibold"
                onClick={handleConfirm} disabled={releasing}
                data-testid="release-dialog-confirm-btn"
              >
                {releasing
                  ? <RefreshCw size={13} className="animate-spin mr-1.5" />
                  : <CheckCircle2 size={13} className="mr-1.5" />}
                Confirm Release
              </Button>
            </>
          )}
          {(state === 'done' || (!canRelease && state === 'form')) && (
            <Button className="flex-1" onClick={onClose}>Close</Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}


// ── Main Page ─────────────────────────────────────────────────────────────────
export default function PendingReleasesPage() {
  const { currentBranch, branches, user } = useAuth();
  const [invoices, setInvoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [branchFilter, setBranchFilter] = useState('__all__');
  const [statusFilter, setStatusFilter] = useState('pending');
  const [summary, setSummary] = useState(null);
  const [releaseTarget, setReleaseTarget] = useState(null);

  const isAdmin = user?.role === 'admin';
  const effectiveBranch = (branchFilter === '__all__' ? '' : branchFilter) || currentBranch?.id || '';
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (effectiveBranch) params.branch_id = effectiveBranch;
      if (statusFilter !== 'pending') params.status = 'all';
      const [listRes, summaryRes] = await Promise.all([
        api.get('/stock-releases', { params }),
        api.get('/stock-releases/summary', { params: effectiveBranch ? { branch_id: effectiveBranch } : {} }),
      ]);
      setInvoices(listRes.data.invoices || []);
      setSummary(summaryRes.data);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, [effectiveBranch, statusFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filtered = invoices.filter(inv => {
    if (!search) return true;
    const s = search.toLowerCase();
    return inv.invoice_number?.toLowerCase().includes(s) || inv.customer_name?.toLowerCase().includes(s);
  });

  const handleReleased = (invoiceId, newStatus) => {
    setInvoices(prev => prev.map(inv =>
      inv.id === invoiceId ? { ...inv, stock_release_status: newStatus } : inv
    ));
    if (newStatus === 'fully_released') {
      setTimeout(() => fetchData(), 800);
    }
  };

  const statusBadge = (s) => {
    const map = {
      not_released: 'bg-amber-100 text-amber-700',
      partially_released: 'bg-blue-100 text-blue-700',
      fully_released: 'bg-emerald-100 text-emerald-700',
      expired: 'bg-slate-200 text-slate-500',
    };
    const labels = {
      not_released: 'Not Released', partially_released: 'Partially Released',
      fully_released: 'Fully Released', expired: 'Expired',
    };
    return { cls: map[s] || 'bg-slate-100 text-slate-600', label: labels[s] || s };
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="pending-releases-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Pending Releases</h1>
          <p className="text-sm text-slate-500 mt-0.5">Track and release stock reserved for customer pickup</p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchData} disabled={loading} className="h-9" data-testid="refresh-releases">
          <RefreshCw size={13} className={loading ? 'animate-spin mr-1.5' : 'mr-1.5'} /> Refresh
        </Button>
      </div>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card className="border-slate-200">
            <CardContent className="p-4">
              <p className="text-xs text-slate-500 uppercase font-medium mb-1">Pending Invoices</p>
              <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{summary.pending_invoice_count}</p>
            </CardContent>
          </Card>
          <Card className="border-slate-200">
            <CardContent className="p-4">
              <p className="text-xs text-slate-500 uppercase font-medium mb-1">Total Reserved Qty</p>
              <p className="text-2xl font-bold text-amber-600" style={{ fontFamily: 'Manrope' }}>{summary.total_reserved_qty}</p>
              <p className="text-[10px] text-slate-400">units on hold</p>
            </CardContent>
          </Card>
          <Card className={`border-slate-200 ${summary.has_overdue ? 'border-red-200 bg-red-50/40' : ''}`}>
            <CardContent className="p-4">
              <p className="text-xs text-slate-500 uppercase font-medium mb-1">Overdue (&gt;30 days)</p>
              <p className={`text-2xl font-bold ${summary.has_overdue ? 'text-red-600' : 'text-slate-400'}`} style={{ fontFamily: 'Manrope' }}>
                {summary.overdue_reservations}
              </p>
              {summary.has_overdue && <p className="text-[10px] text-red-500">Will auto-return to stock</p>}
            </CardContent>
          </Card>
          <Card className="border-slate-200">
            <CardContent className="p-4">
              <p className="text-xs text-slate-500 uppercase font-medium mb-1">Showing</p>
              <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{filtered.length}</p>
              <p className="text-[10px] text-slate-400">invoices</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Overdue warning */}
      {summary?.has_overdue && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 flex items-start gap-3">
          <AlertTriangle size={16} className="text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700">
            <strong>{summary.overdue_reservations}</strong> reservation{summary.overdue_reservations !== 1 ? 's are' : ' is'} overdue (&gt;30 days).
            These will be automatically returned to available inventory at 7:30 AM.
          </p>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="relative flex-1 min-w-48 max-w-72">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search invoice # or customer..."
            className="pl-8 h-9 text-sm" data-testid="search-releases" />
        </div>
        {isAdmin && branches.length > 1 && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="h-9 w-44 text-sm" data-testid="branch-filter">
              <SelectValue placeholder="All Branches" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All Branches</SelectItem>
              {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="h-9 w-40 text-sm" data-testid="status-filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">Pending Only</SelectItem>
            <SelectItem value="all">All (incl. completed)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-slate-400">
          <RefreshCw size={20} className="animate-spin mr-2" /> Loading...
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-slate-400">
          <CheckCircle2 size={36} className="mx-auto mb-3 opacity-30" />
          <p className="font-medium">No pending releases</p>
          <p className="text-sm mt-1">All stock has been released or no partial-release invoices found</p>
        </div>
      ) : (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Invoice</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Customer</TableHead>
                  {isAdmin && branches.length > 1 && <TableHead className="text-xs uppercase text-slate-500">Branch</TableHead>}
                  <TableHead className="text-xs uppercase text-slate-500">Sale Date</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Age</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Status</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Progress</TableHead>
                  <TableHead className="w-32 text-xs uppercase text-slate-500">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map(inv => {
                  const days = daysSince(inv.created_at);
                  const isOverdue = days >= 30;
                  const s = statusBadge(inv.stock_release_status);
                  const pct = inv.reservation_summary?.pct_released || 0;
                  const remaining = inv.reservation_summary?.total_remaining || 0;
                  const total = inv.reservation_summary?.total_ordered || 0;
                  const isPending = ['not_released', 'partially_released'].includes(inv.stock_release_status);

                  return (
                    <TableRow key={inv.id} className={isOverdue ? 'bg-red-50/30' : ''} data-testid={`release-row-${inv.id}`}>
                      <TableCell>
                        <button
                          className="font-mono text-sm text-blue-600 font-medium hover:underline hover:text-blue-800 text-left"
                          onClick={() => { setSelectedInvoiceId(inv.id); setInvoiceModalOpen(true); }}
                          data-testid={`invoice-link-${inv.id}`}
                        >
                          {inv.invoice_number}
                        </button>
                        {inv.doc_code && (
                          <div className="text-[10px] text-slate-400 font-mono mt-0.5">{inv.doc_code}</div>
                        )}
                      </TableCell>
                      <TableCell className="text-sm font-medium">{inv.customer_name || 'Walk-in'}</TableCell>
                      {isAdmin && branches.length > 1 && (
                        <TableCell className="text-xs text-slate-500">
                          {branches.find(b => b.id === inv.branch_id)?.name || '—'}
                        </TableCell>
                      )}
                      <TableCell className="text-xs text-slate-500">{fmtDate(inv.created_at)}</TableCell>
                      <TableCell>
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                          isOverdue ? 'bg-red-100 text-red-700' :
                          days >= 14 ? 'bg-amber-100 text-amber-700' :
                          'bg-slate-100 text-slate-600'
                        }`}>{days}d</span>
                      </TableCell>
                      <TableCell>
                        <Badge className={`text-[10px] border-0 ${s.cls}`}>{s.label}</Badge>
                      </TableCell>
                      <TableCell>
                        {total > 0 && (
                          <div className="space-y-1">
                            <div className="flex items-center gap-2">
                              <div className="flex-1 bg-slate-200 rounded-full h-1.5 min-w-16">
                                <div
                                  className={`h-1.5 rounded-full transition-all ${pct >= 100 ? 'bg-emerald-500' : pct > 0 ? 'bg-blue-500' : 'bg-amber-400'}`}
                                  style={{ width: `${Math.min(100, pct)}%` }}
                                />
                              </div>
                              <span className="text-xs text-slate-400 shrink-0">{pct.toFixed(0)}%</span>
                            </div>
                            {remaining > 0 && (
                              <p className="text-[10px] text-amber-600">{remaining} units pending</p>
                            )}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          {isPending && inv.doc_code && (
                            <Button
                              size="sm"
                              className="h-7 px-2.5 text-xs bg-amber-600 hover:bg-amber-700 text-white"
                              onClick={() => setReleaseTarget(inv)}
                              data-testid={`release-btn-${inv.id}`}
                            >
                              <Boxes size={11} className="mr-1" /> Release
                            </Button>
                          )}
                          {inv.doc_code && (
                            <Button
                              variant="ghost" size="sm"
                              className="h-7 w-7 p-0 text-slate-400 hover:text-blue-600"
                              onClick={() => window.open(`/doc/${inv.doc_code}`, '_blank')}
                              title="Open QR page"
                              data-testid={`open-doc-${inv.id}`}
                            >
                              <ExternalLink size={12} />
                            </Button>
                          )}
                          {!inv.doc_code && isPending && (
                            <span className="text-[10px] text-slate-300">No QR code</span>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Release Dialog */}
      <ReleaseDialog
        open={!!releaseTarget}
        invoice={releaseTarget}
        onClose={() => setReleaseTarget(null)}
        onReleased={(id, status) => {
          handleReleased(id, status);
          if (status === 'fully_released') setReleaseTarget(null);
        }}
      />

      {/* Invoice Detail Modal — same as Sales History */}
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        saleId={selectedInvoiceId}
        onUpdated={fetchData}
      />
    </div>
  );
}
