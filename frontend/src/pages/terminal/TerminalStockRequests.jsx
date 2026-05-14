import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Inbox, Search, RefreshCw, Check, Loader2, Package, ArrowRight,
  Lock, AlertTriangle, MessageSquare, X,
} from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { toast } from 'sonner';
import { formatPHP, fmtDate } from '../../lib/utils';
import CalcInput from '../../components/CalcInput';

/**
 * TerminalStockRequests
 *
 * Surfaces *incoming* branch-request POs (status: requested / sent) where
 * the current terminal's branch is the SUPPLIER (po.supply_branch_id ===
 * session.branchId). The cashier reviews each line, types the actual
 * `approved_qty` they can spare, optionally adds a note per excess line,
 * and submits the confirmation with a manager/admin PIN.
 *
 * Backend: POST /api/purchase-orders/{po_id}/confirm-request
 *   • Mutates only items[].approved_qty + approved_note + approval log.
 *   • Does NOT create a Branch Transfer yet — the supplying-branch admin
 *     does that on the web (review → generate-branch-transfer), then ships.
 *   • Soft-locked if a non-cancelled BTO is already linked.
 *
 * This view intentionally mirrors `TerminalPOCheck` / `TerminalTransfers`
 * so cashiers have the same muscle memory across all three queues.
 */
export default function TerminalStockRequests({ api, session, isOnline, onRefreshRef }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [items, setItems] = useState([]);
  const [notes, setNotes] = useState('');
  const [pin, setPin] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submittedFor, setSubmittedFor] = useState(null);  // PO id of just-confirmed (success splash)

  const loadRequests = useCallback(async () => {
    if (!isOnline) { toast('Stock requests require internet', { duration: 2000 }); return; }
    setLoading(true);
    try {
      const res = await api.get('/purchase-orders/incoming-requests', {
        params: { branch_id: session.branchId },
      });
      const list = res.data?.requests || res.data || [];
      // Only show those still actionable; hide cancelled (server already
      // filters them) and ones that already have a linked BTO so cashiers
      // don't accidentally re-confirm.
      const actionable = (Array.isArray(list) ? list : []).filter(r => {
        if (r.status === 'cancelled') return false;
        if (r.linked_bto_id && r.linked_bto_status !== 'cancelled') return false;
        return true;
      });
      setRequests(actionable);
    } catch {
      toast.error('Failed to load incoming stock requests');
    }
    setLoading(false);
  }, [api, session.branchId, isOnline]);

  useEffect(() => { if (isOnline) loadRequests(); }, [isOnline, loadRequests]);

  // Expose refresh to TerminalShell so a WebSocket notification can poke us.
  useEffect(() => {
    if (onRefreshRef) onRefreshRef.current = loadRequests;
  }, [onRefreshRef, loadRequests]);

  const openRequest = (po) => {
    setSelected(po);
    setNotes('');
    setPin('');
    // Default approved_qty = requested quantity. The cashier edits down
    // (or up, with note) to match what they can actually spare.
    setItems((po.items || []).map(item => ({
      product_id:    item.product_id,
      product_name:  item.product_name,
      requested_qty: parseFloat(item.quantity) || 0,
      approved_qty:  String(item.approved_qty ?? item.quantity ?? 0),
      approved_note: item.approved_note || '',
      unit:          item.unit || 'pc',
      rate:          parseFloat(item.rate) || 0,
    })));
  };

  const closeRequest = () => {
    setSelected(null); setItems([]); setNotes(''); setPin('');
  };

  // Track which lines need a note (approved > requested = "excess" requires
  // justification per backend validation).
  const linesNeedingNote = useMemo(() => {
    return items
      .map((it, idx) => ({ it, idx }))
      .filter(({ it }) => {
        const a = parseFloat(it.approved_qty) || 0;
        return a > it.requested_qty && !it.approved_note.trim();
      })
      .map(({ idx }) => idx);
  }, [items]);

  const totalApproved = useMemo(
    () => items.reduce((sum, it) => sum + (parseFloat(it.approved_qty) || 0), 0),
    [items],
  );
  const totalRequested = useMemo(
    () => items.reduce((sum, it) => sum + it.requested_qty, 0),
    [items],
  );

  const canSubmit = pin.length >= 4
    && items.length > 0
    && linesNeedingNote.length === 0
    && !submitting;

  const submit = async () => {
    if (!canSubmit || !selected) return;
    setSubmitting(true);
    try {
      const payload = {
        pin,
        notes: notes.trim(),
        items: items.map(it => ({
          product_id:    it.product_id,
          approved_qty:  parseFloat(it.approved_qty) || 0,
          approved_note: it.approved_note.trim(),
        })),
        // Terminal-session credentials. The confirm-request endpoint itself
        // isn't terminal-gated yet (it's also reachable from web admin
        // panels), but we send them anyway so future tightening is a flip
        // of one dependency, not a frontend rewrite.
        terminal_id: session.terminalId || '',
        device_id:   session.deviceId || '',
      };
      await api.post(`/purchase-orders/${selected.id}/confirm-request`, payload);
      toast.success(
        `${selected.po_number || 'Stock request'} confirmed — continue on web to generate the Branch Transfer.`,
        { duration: 5000 },
      );
      setSubmittedFor(selected.id);
      closeRequest();
      // Best-effort refresh — the PO won't disappear until a BTO is linked,
      // but its `approved_qty` will be updated so a re-tap shows the new
      // numbers.
      setTimeout(() => loadRequests(), 600);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const msg = typeof detail === 'object' ? (detail.message || JSON.stringify(detail)) : (detail || 'Confirmation failed');
      toast.error(msg);
    } finally {
      setSubmitting(false);
      // Clear the splash after a moment so the success badge doesn't stick.
      setTimeout(() => setSubmittedFor(null), 4000);
    }
  };

  const filteredRequests = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return requests;
    return requests.filter(r =>
      (r.po_number || '').toLowerCase().includes(q)
      || (r.requesting_branch_name || r.branch_name || '').toLowerCase().includes(q),
    );
  }, [requests, search]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-3" data-testid="terminal-stock-requests">
      {/* Header */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by PO# or branch…"
            className="pl-9 h-10 bg-white"
            data-testid="stock-requests-search"
          />
        </div>
        <Button
          variant="outline" size="icon"
          onClick={loadRequests} disabled={loading || !isOnline}
          className="h-10 w-10 shrink-0"
          data-testid="stock-requests-refresh"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
        </Button>
      </div>

      {/* Help blurb — explains what this queue is for. Mirrors the tone of
          the other terminal panels so the cashier knows what they're looking
          at on first open. */}
      <div className="rounded-lg border border-purple-200 bg-purple-50 px-3 py-2 text-[11px] text-purple-900 flex gap-2">
        <Inbox size={14} className="shrink-0 mt-0.5 text-purple-600" />
        <div>
          <p className="font-semibold">Incoming stock requests from other branches.</p>
          <p className="mt-0.5 text-purple-800/80 leading-snug">
            Confirm how much you can supply per line. After this, an admin on
            the web converts your confirmation into a Branch Transfer and
            ships the items.
          </p>
        </div>
      </div>

      {/* List */}
      {!isOnline && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 flex items-center gap-2">
          <AlertTriangle size={14} /> Offline — stock requests sync when reconnected.
        </div>
      )}

      {loading && requests.length === 0 && (
        <div className="text-center py-8 text-slate-500 text-sm flex items-center justify-center gap-2">
          <Loader2 size={14} className="animate-spin" /> Loading requests…
        </div>
      )}

      {!loading && filteredRequests.length === 0 && (
        <div className="text-center py-10 text-slate-500 text-sm" data-testid="stock-requests-empty">
          <Inbox size={28} className="mx-auto mb-2 text-slate-300" />
          {search ? 'No matches.' : 'No incoming stock requests right now.'}
        </div>
      )}

      <div className="space-y-2">
        {filteredRequests.map((r) => {
          const requesterName = r.requesting_branch_name || r.branch_name || '(Unknown branch)';
          const itemCount = (r.items || []).length;
          const justConfirmed = submittedFor === r.id;
          return (
            <button
              key={r.id}
              onClick={() => openRequest(r)}
              className="w-full text-left bg-white border-2 border-purple-100 hover:border-purple-300 active:border-purple-400 rounded-xl p-3 transition shadow-sm"
              data-testid={`stock-request-card-${r.po_number || r.id}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-900 truncate">
                    {r.po_number || '(no PO#)'}
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
                    <span className="font-medium">{requesterName}</span>
                    <ArrowRight size={11} className="text-slate-400" />
                    <span>Your branch</span>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <Badge className="bg-purple-100 text-purple-700 hover:bg-purple-100 text-[10px]">
                    {r.status === 'requested' ? 'Requested' : (r.status || '—').replace(/_/g, ' ')}
                  </Badge>
                  {justConfirmed && (
                    <Badge className="bg-emerald-100 text-emerald-700 text-[10px]">
                      <Check size={10} className="mr-0.5" /> Confirmed
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between mt-2 text-[11px] text-slate-500">
                <span className="flex items-center gap-1">
                  <Package size={11} /> {itemCount} item{itemCount === 1 ? '' : 's'}
                </span>
                <span>{fmtDate(r.created_at || r.order_date)}</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Confirm dialog */}
      <Dialog open={!!selected} onOpenChange={(v) => { if (!v) closeRequest(); }}>
        <DialogContent className="max-w-md p-0 overflow-hidden" data-testid="stock-request-confirm-dialog">
          <DialogHeader className="px-4 pt-4 pb-2 border-b">
            <DialogTitle className="text-base flex items-center gap-2">
              <Inbox size={16} className="text-purple-600" />
              Confirm Stock Request
            </DialogTitle>
            {selected && (
              <p className="text-[11px] text-slate-500 mt-0.5">
                <span className="font-medium text-slate-700">{selected.po_number}</span>
                {' · from '}
                <span className="font-medium">{selected.requesting_branch_name || selected.branch_name || '(Unknown)'}</span>
              </p>
            )}
          </DialogHeader>

          <div className="px-4 py-3 space-y-3 max-h-[60vh] overflow-y-auto">
            {items.map((it, idx) => {
              const a = parseFloat(it.approved_qty) || 0;
              const excess = a > it.requested_qty;
              const short = a < it.requested_qty;
              const needsNote = linesNeedingNote.includes(idx);
              return (
                <div key={it.product_id} className="bg-slate-50 rounded-lg p-2.5 space-y-1.5" data-testid={`stock-request-line-${idx}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-semibold text-slate-900 truncate">{it.product_name}</p>
                      <p className="text-[10px] text-slate-500 mt-0.5">
                        Requested: <span className="font-medium">{it.requested_qty} {it.unit}</span>
                        {it.rate > 0 && <> · {formatPHP(it.rate)}</>}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-slate-600 shrink-0">Approve:</span>
                    <CalcInput
                      value={it.approved_qty}
                      onChange={(v) => {
                        const next = [...items];
                        next[idx] = { ...next[idx], approved_qty: v };
                        setItems(next);
                      }}
                      className="h-8 text-sm flex-1"
                      placeholder="0"
                      data-testid={`stock-request-approved-qty-${idx}`}
                    />
                    <span className="text-[10px] text-slate-500 shrink-0">{it.unit}</span>
                  </div>
                  {excess && (
                    <div className="space-y-1">
                      <p className="text-[10px] text-amber-700 font-medium flex items-center gap-1">
                        <AlertTriangle size={10} /> Approving more than requested — please add a note:
                      </p>
                      <Input
                        value={it.approved_note}
                        onChange={(e) => {
                          const next = [...items];
                          next[idx] = { ...next[idx], approved_note: e.target.value };
                          setItems(next);
                        }}
                        placeholder="Why are you over-supplying this line?"
                        className={`h-8 text-xs ${needsNote ? 'border-red-400 bg-red-50' : ''}`}
                        data-testid={`stock-request-line-note-${idx}`}
                      />
                    </div>
                  )}
                  {short && (
                    <p className="text-[10px] text-slate-500 italic">
                      Approving {it.requested_qty - a} {it.unit} less than requested.
                    </p>
                  )}
                </div>
              );
            })}

            <div className="border-t pt-3 space-y-2">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-slate-500">Total approved</span>
                <span className="font-semibold text-slate-900">
                  {totalApproved.toFixed(2)} / {totalRequested.toFixed(2)} units
                </span>
              </div>

              <div className="space-y-1">
                <label className="text-[10px] font-semibold text-slate-600 flex items-center gap-1">
                  <MessageSquare size={11} /> Cashier note (optional)
                </label>
                <Input
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g., short on stock until Friday delivery"
                  className="h-9 text-xs"
                  data-testid="stock-request-overall-notes"
                />
              </div>

              <div className="space-y-1">
                <label className="text-[10px] font-semibold text-slate-600 flex items-center gap-1">
                  <Lock size={11} /> Manager / Admin PIN or TOTP
                </label>
                <Input
                  type="password"
                  inputMode="numeric"
                  value={pin}
                  onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, '').slice(0, 8))}
                  placeholder="4-8 digit PIN or 6-digit TOTP"
                  className="h-9 text-sm"
                  autoFocus
                  data-testid="stock-request-pin"
                />
              </div>
            </div>
          </div>

          <div className="px-4 py-3 border-t bg-slate-50 flex items-center justify-between gap-2">
            <Button
              variant="ghost" size="sm"
              onClick={closeRequest} disabled={submitting}
              data-testid="stock-request-cancel-btn"
            >
              <X size={13} className="mr-1" /> Cancel
            </Button>
            <Button
              size="sm"
              onClick={submit}
              disabled={!canSubmit}
              className="bg-purple-600 hover:bg-purple-700 text-white"
              data-testid="stock-request-submit-btn"
            >
              {submitting ? (
                <><Loader2 size={13} className="mr-1 animate-spin" /> Submitting…</>
              ) : (
                <><Check size={13} className="mr-1" /> Confirm & Send</>
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
