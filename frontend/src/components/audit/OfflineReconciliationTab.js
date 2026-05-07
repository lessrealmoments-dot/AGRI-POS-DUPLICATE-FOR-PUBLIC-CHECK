/**
 * Iter 252 — Offline Reconciliation Tab
 *
 * Lists entries from `offline_reconciliation_queue` (created when offline
 * draft finalizations conflict at sync — e.g. draft already paid, draft not
 * found, items_diverged). Managers/admins can review and resolve each entry.
 */
import { useEffect, useState, useCallback } from 'react';
import { api } from '../../contexts/AuthContext';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { RefreshCw, AlertTriangle, Check } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP, fmtDateTime } from '../../lib/utils';

const REASON_LABELS = {
  draft_already_paid: 'Draft already finalized online',
  draft_already_voided: 'Draft was voided',
  draft_already_cancelled_draft: 'Draft was cancelled',
  draft_not_found: 'Draft no longer exists',
  items_diverged: 'Items differ from draft',
  payment_diverged: 'Payment differs from draft',
  inventory_issue: 'Inventory issue at sync',
  duplicate_sync_attempt: 'Duplicate sync attempt',
};

export default function OfflineReconciliationTab({ branchId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState('open');
  const [resolveDialog, setResolveDialog] = useState(null);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (branchId) params.set('branch_id', branchId);
      params.set('status', statusFilter);
      const res = await api.get(`/sync/offline-reconciliation?${params.toString()}`);
      setItems(res.data.items || []);
    } catch (err) {
      toast.error(`Failed to load reconciliation queue: ${err?.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  }, [branchId, statusFilter]);

  useEffect(() => { load(); }, [load]);

  const resolve = async (rec) => {
    try {
      await api.post(`/sync/offline-reconciliation/${rec.id}/resolve`, {
        action: 'manual',
        note: note || 'Resolved by manager',
      });
      toast.success('Reconciliation entry resolved');
      setResolveDialog(null);
      setNote('');
      load();
    } catch (err) {
      toast.error(`Failed: ${err?.response?.data?.detail || err.message}`);
    }
  };

  return (
    <div className="space-y-3" data-testid="offline-reconciliation-tab">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-700">Offline Reconciliation Queue</h3>
          {items.length > 0 && (
            <Badge className="bg-amber-100 text-amber-800 border-amber-300">{items.length}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-8 rounded-md border border-slate-300 bg-white px-2 text-xs"
            data-testid="reconciliation-status-filter"
          >
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
          </select>
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw size={13} className={`mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed">
        Conflicts surfaced when offline draft completions (linked OFF receipts) sync but the original
        draft state has changed. Each entry preserves the offline envelope snapshot for auditing.
      </p>

      {loading ? (
        <div className="text-center py-8 text-slate-400 text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <Card className="border-slate-200">
          <CardContent className="p-6 text-center text-slate-400">
            <Check size={28} className="mx-auto mb-2 text-emerald-500 opacity-60" />
            <p className="text-sm">No {statusFilter} reconciliation entries.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <Card key={it.id} className="border-amber-200" data-testid={`reconciliation-row-${it.id}`}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge className="bg-amber-100 text-amber-800 border-amber-300 text-[10px]">
                        <AlertTriangle size={10} className="mr-1" />
                        {REASON_LABELS[it.reason] || it.reason}
                      </Badge>
                      {it.status === 'resolved' && (
                        <Badge className="bg-emerald-100 text-emerald-700 border-emerald-300 text-[10px]">RESOLVED</Badge>
                      )}
                      <span className="text-[10px] text-slate-400">{fmtDateTime(it.created_at)}</span>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1.5 text-xs">
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">OFF Receipt</p>
                        <p className="font-mono font-bold text-slate-800">{it.off_receipt || '—'}</p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Draft Invoice</p>
                        <p className="font-mono font-bold text-slate-800">{it.draft_invoice_number || it.canonical_invoice_number || '—'}</p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Customer</p>
                        <p className="text-slate-700 truncate">{it.customer_name || 'Walk-in'}</p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Amount</p>
                        <p className="font-mono text-slate-700">{formatPHP(it.amount || 0)}</p>
                      </div>
                    </div>
                    {it.cashier_name && (
                      <p className="text-[10px] text-slate-400 mt-1">Cashier: {it.cashier_name}</p>
                    )}
                    {it.resolved_note && (
                      <p className="text-[11px] text-emerald-700 mt-1 italic">Note: {it.resolved_note}</p>
                    )}
                  </div>
                  {it.status === 'open' && (
                    <Button
                      size="sm" variant="outline"
                      onClick={() => { setResolveDialog(it); setNote(''); }}
                      data-testid={`reconciliation-resolve-${it.id}`}
                    >
                      <Check size={13} className="mr-1" /> Mark Resolved
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Resolve Dialog */}
      {resolveDialog && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" data-testid="reconciliation-resolve-dialog">
          <div className="bg-white rounded-xl shadow-2xl max-w-md w-full p-5 space-y-3">
            <h3 className="text-sm font-bold text-slate-800">Resolve Reconciliation Entry</h3>
            <div className="space-y-1 text-xs">
              <p><span className="text-slate-500">OFF Receipt:</span> <span className="font-mono">{resolveDialog.off_receipt}</span></p>
              <p><span className="text-slate-500">Draft:</span> <span className="font-mono">{resolveDialog.draft_invoice_number}</span></p>
              <p><span className="text-slate-500">Reason:</span> {REASON_LABELS[resolveDialog.reason] || resolveDialog.reason}</p>
            </div>
            <Input
              placeholder="Resolution note (e.g. 'Voided OFF, kept draft')"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              data-testid="reconciliation-resolve-note"
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button size="sm" variant="outline" onClick={() => { setResolveDialog(null); setNote(''); }}>
                Cancel
              </Button>
              <Button
                size="sm"
                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                onClick={() => resolve(resolveDialog)}
                data-testid="reconciliation-resolve-confirm"
              >
                Confirm Resolve
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
