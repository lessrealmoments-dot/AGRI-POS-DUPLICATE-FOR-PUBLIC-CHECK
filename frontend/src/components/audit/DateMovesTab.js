/**
 * Audit Center → Date Moves tab.
 *
 * Lists every invoice whose `order_date` / `invoice_date` was changed while
 * the ORIGINAL date was on a closed day — the dangerous subset that causes
 * closed-day Z-Reports to silently shift. Each row has a one-click "Restore"
 * button (PIN-gated) that puts the invoice's dates back to the original
 * closed-day value and recalculates `due_date`.
 *
 * Surfaces the Iter 231 bug where interest invoices INT-SB-001004/5 got
 * moved from 4/3/25 to 5/4/25 and appeared on BOTH days' Z-Reports.
 */
import { useState, useEffect, useCallback } from 'react';
import { api } from '../../contexts/AuthContext';
import { formatPHP } from '../../lib/utils';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/dialog';
import { RefreshCw, History, ShieldAlert, Undo2, Lock } from 'lucide-react';
import { toast } from 'sonner';

export default function DateMovesTab() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showAll, setShowAll] = useState(false);        // default = closed-source only
  const [restoring, setRestoring] = useState(null);     // invoice_id in-flight
  const [pinDialog, setPinDialog] = useState(null);     // { row } or null
  const [pin, setPin] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/invoices/audit/date-moves', {
        params: { closed_source_only: !showAll },
      });
      setRows(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load date-move audit');
    }
    setLoading(false);
  }, [showAll]);

  useEffect(() => { load(); }, [load]);

  const confirmRestore = (row) => { setPin(''); setPinDialog({ row }); };

  const doRestore = async () => {
    if (!pinDialog?.row || !pin) return;
    const row = pinDialog.row;
    setRestoring(row.invoice_id);
    try {
      const res = await api.post(`/invoices/${row.invoice_id}/restore-date`, { pin });
      toast.success(
        `Restored ${res.data.invoice.invoice_number} — ` +
        (res.data.changes || []).find(c => c.startsWith('order_date'))?.replace('(restored)', '').trim()
      );
      setPinDialog(null);
      setPin('');
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Restore failed');
    }
    setRestoring(null);
  };

  return (
    <div className="space-y-4" data-testid="date-moves-tab-content">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h3 className="text-lg font-semibold text-slate-800 flex items-center gap-2">
            <History size={18} className="text-amber-600" /> Date Moves
          </h3>
          <p className="text-xs text-slate-500 max-w-2xl mt-1">
            Invoices whose <code className="text-[11px] bg-slate-100 px-1 rounded">order_date</code> or
            <code className="text-[11px] bg-slate-100 px-1 rounded ml-1">invoice_date</code> was changed after the
            original date was closed. These records cause Z-Reports to split across days — restore them to put the
            ledger back in sync before your auditor pulls the books.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <label className="text-[11px] text-slate-600 flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showAll}
              onChange={e => setShowAll(e.target.checked)}
              className="rounded border-slate-300"
              data-testid="dmt-toggle-show-all" />
            Show all moves (not just closed-source)
          </label>
          <Button size="sm" variant="outline" onClick={load} disabled={loading} data-testid="dmt-refresh-btn">
            <RefreshCw size={14} className={`mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-12 text-slate-400">
          <RefreshCw size={22} className="animate-spin mr-2" /> Loading…
        </div>
      ) : rows.length === 0 ? (
        <div className="text-center py-12 bg-emerald-50 border border-emerald-200 rounded-lg">
          <ShieldAlert size={28} className="mx-auto text-emerald-400 mb-2" />
          <p className="text-sm font-medium text-emerald-700">
            No {showAll ? 'date moves' : 'closed-day date moves'} found
          </p>
          <p className="text-[11px] text-emerald-500 mt-1">
            {showAll
              ? 'No invoice has ever had its date changed.'
              : 'Every invoice currently sits on its original date. Your closed-day books are clean.'}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2 text-left">Invoice #</th>
                <th className="px-3 py-2 text-left">Customer</th>
                <th className="px-3 py-2 text-right">Amount</th>
                <th className="px-3 py-2 text-left">Move</th>
                <th className="px-3 py-2 text-left">Edited By</th>
                <th className="px-3 py-2 text-left">When</th>
                <th className="px-3 py-2 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const wasClosedSource = (r.closed_source_dates || []).length > 0;
                return (
                  <tr key={r.edit_id || r.invoice_id} className={wasClosedSource ? 'bg-rose-50/40' : 'bg-white'}>
                    <td className="px-3 py-2">
                      <span className="font-mono text-[12px] font-medium text-slate-800">{r.invoice_number}</span>
                      {r.sale_type === 'interest_charge' && (
                        <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700 font-medium">INT</span>
                      )}
                      {r.sale_type === 'penalty_charge' && (
                        <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-red-100 text-red-700 font-medium">PEN</span>
                      )}
                      {wasClosedSource && (
                        <span className="ml-1 text-[9px] px-1 py-0.5 rounded bg-rose-100 text-rose-700 font-medium flex-inline items-center gap-0.5">
                          <Lock size={8} className="inline" /> closed-day
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-700">{r.customer_name || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-700">{formatPHP(r.grand_total)}</td>
                    <td className="px-3 py-2 text-[11px]">
                      {(r.moves || []).map((m, i) => (
                        <div key={i} className="font-mono">
                          <span className="text-slate-400">{m.field}:</span>{' '}
                          <span className="text-rose-600 line-through">{m.from}</span>
                          {' → '}
                          <span className="text-emerald-700">{m.to}</span>
                        </div>
                      ))}
                    </td>
                    <td className="px-3 py-2 text-slate-600 text-[12px]">{r.edited_by_name || '—'}</td>
                    <td className="px-3 py-2 text-slate-500 text-[11px]">
                      {r.edited_at ? new Date(r.edited_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={restoring === r.invoice_id}
                        onClick={() => confirmRestore(r)}
                        className="text-emerald-700 border-emerald-300 hover:bg-emerald-50"
                        data-testid={`dmt-restore-btn-${r.invoice_id}`}>
                        <Undo2 size={13} className="mr-1" />
                        {restoring === r.invoice_id ? 'Restoring…' : 'Restore'}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* PIN dialog */}
      <Dialog open={!!pinDialog} onOpenChange={o => { if (!o) { setPinDialog(null); setPin(''); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Undo2 size={16} className="text-emerald-600" /> Restore Original Dates
            </DialogTitle>
          </DialogHeader>
          {pinDialog?.row && (
            <div className="space-y-3 text-sm">
              <p className="text-slate-600">
                Restore <span className="font-mono font-medium">{pinDialog.row.invoice_number}</span> to its original
                dates on closed day <span className="font-mono text-rose-600">{(pinDialog.row.closed_source_dates || [])[0] || '—'}</span>?
              </p>
              <p className="text-[11px] text-slate-500 bg-slate-50 p-2 rounded border border-slate-200">
                This also rewinds the <code>due_date</code> if terms are set, and writes an audit trail entry.
                Closed-day Z-Report snapshots are unchanged; only the live ledger moves back.
              </p>
              <div>
                <Label className="text-xs text-slate-500">Manager PIN</Label>
                <Input
                  type="password"
                  value={pin}
                  onChange={e => setPin(e.target.value)}
                  placeholder="Enter manager PIN"
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && pin && doRestore()}
                  data-testid="dmt-pin-input" />
              </div>
              <div className="flex gap-2 pt-2">
                <Button variant="outline" className="flex-1" onClick={() => { setPinDialog(null); setPin(''); }}>Cancel</Button>
                <Button
                  disabled={!pin || restoring === pinDialog.row.invoice_id}
                  className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white"
                  onClick={doRestore}
                  data-testid="dmt-pin-confirm-btn">
                  {restoring === pinDialog.row.invoice_id ? 'Restoring…' : 'Restore'}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
