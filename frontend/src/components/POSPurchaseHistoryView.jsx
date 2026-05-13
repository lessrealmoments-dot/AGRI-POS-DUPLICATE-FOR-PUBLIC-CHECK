/**
 * POSPurchaseHistoryView — POS-terminal purchase-order history with range chips.
 *
 * Backend: GET /api/purchase-orders/history/by-range
 *
 * Mirrors POSSalesHistoryView in layout / chip semantics. Row click hands the
 * PO up via onSelectPO(po) so the parent can open the detail/reprint dialog.
 */
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../contexts/AuthContext';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { RefreshCw, Truck, WifiOff, Search } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';

const RANGES = [
  { value: 'today',  label: 'Today' },
  { value: 'week',   label: 'This Week' },
  { value: 'month',  label: 'This Month' },
  { value: '30d',    label: 'Last 30 Days' },
  { value: 'custom', label: 'Custom' },
];

const fmtTimeISO = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
};
const fmtDayISO = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return ''; }
};

const STATUS_BADGE = {
  draft:     { cls: 'bg-slate-200 text-slate-600',    label: 'Draft'    },
  open:      { cls: 'bg-blue-100 text-blue-700',      label: 'Open'     },
  partial:   { cls: 'bg-amber-100 text-amber-700',    label: 'Partial'  },
  received:  { cls: 'bg-emerald-100 text-emerald-700',label: 'Received' },
  cancelled: { cls: 'bg-rose-100 text-rose-600',      label: 'Cancelled'},
};

export default function POSPurchaseHistoryView({
  currentBranch,
  isOnline,
  onSelectPO,
}) {
  const [range, setRange] = useState('today');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [search, setSearch] = useState('');
  const [list, setList] = useState([]);
  const [totals, setTotals] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!isOnline) return;
    setLoading(true);
    try {
      const params = { range };
      if (range === 'custom') {
        if (fromDate) params.from_date = fromDate;
        if (toDate)   params.to_date   = toDate;
      }
      if (currentBranch?.id && currentBranch.id !== 'all') params.branch_id = currentBranch.id;
      if (search.trim()) params.search = search.trim();
      const res = await api.get('/purchase-orders/history/by-range', { params });
      setList(res.data.purchase_orders || []);
      setTotals(res.data.totals || null);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to load purchase history');
    } finally {
      setLoading(false);
    }
  }, [range, fromDate, toDate, search, currentBranch?.id, isOnline]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-3" data-testid="pos-purchase-history-view">
      {/* Range chips */}
      <div className="flex flex-wrap items-center gap-1.5" data-testid="pos-purchase-range-tabs">
        {RANGES.map(r => (
          <button
            key={r.value}
            onClick={() => setRange(r.value)}
            data-testid={`pos-purchase-range-${r.value}`}
            className={`px-2.5 py-1 rounded-md text-xs font-medium border transition ${
              range === r.value
                ? 'bg-slate-900 text-white border-slate-900'
                : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
            }`}
          >
            {r.label}
          </button>
        ))}
        {range === 'custom' && (
          <div className="flex items-center gap-1.5 ml-1">
            <Input
              type="date"
              value={fromDate}
              onChange={e => setFromDate(e.target.value)}
              className="h-8 w-36 text-xs"
              data-testid="pos-purchase-custom-from"
            />
            <span className="text-xs text-slate-400">→</span>
            <Input
              type="date"
              value={toDate}
              onChange={e => setToDate(e.target.value)}
              className="h-8 w-36 text-xs"
              data-testid="pos-purchase-custom-to"
            />
          </div>
        )}
      </div>

      {/* Running totals */}
      {totals && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Total Spent',  value: formatPHP(totals.spent),       color: 'text-slate-800',  bg: 'bg-slate-50',    border: 'border-slate-200' },
            { label: 'Paid',         value: formatPHP(totals.paid),        color: 'text-emerald-700',bg: 'bg-emerald-50',  border: 'border-emerald-200' },
            { label: 'Outstanding',  value: formatPHP(totals.outstanding), color: 'text-amber-700',  bg: 'bg-amber-50',    border: 'border-amber-200' },
            { label: 'Purchase Orders', value: totals.count,               color: 'text-blue-700',   bg: 'bg-blue-50',     border: 'border-blue-200',
              sub: totals.by_status ? (
                Object.entries(totals.by_status)
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => `${n} ${k}`).join(' · ')
              ) : null },
          ].map(k => (
            <div key={k.label} className={`rounded-xl border ${k.border} ${k.bg} px-4 py-3`}>
              <p className="text-[11px] text-slate-500 font-medium">{k.label}</p>
              <p className={`text-lg font-bold font-mono ${k.color}`}>{k.value}</p>
              {k.sub && <p className="text-[10px] text-slate-400 truncate">{k.sub}</p>}
            </div>
          ))}
        </div>
      )}

      {/* Search + Refresh */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search PO # or supplier..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="h-9 pl-9 text-sm"
            data-testid="pos-purchase-search-input"
          />
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading || !isOnline} className="h-9">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </Button>
      </div>

      {/* List */}
      {!isOnline ? (
        <div className="text-center py-12 text-slate-400">
          <WifiOff size={20} className="mx-auto mb-2" />
          <p className="text-sm">History requires internet connection</p>
        </div>
      ) : loading ? (
        <div className="text-center py-12">
          <RefreshCw size={20} className="animate-spin mx-auto text-slate-400" />
        </div>
      ) : list.length === 0 ? (
        <div className="text-center py-12 text-slate-400">
          <Truck size={28} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">No purchase orders in this window</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {list.map(po => {
            const status = (po.status || 'open').toLowerCase();
            const sb = STATUS_BADGE[status] || STATUS_BADGE.open;
            const bal = parseFloat(po.balance || 0);
            const cancelled = status === 'cancelled';
            return (
              <button
                key={po.id}
                onClick={() => onSelectPO && onSelectPO(po)}
                data-testid={`pos-purchase-history-row-${po.id}`}
                className={`w-full text-left rounded-xl border px-4 py-3 transition-all hover:shadow-sm ${
                  cancelled ? 'bg-slate-50 border-slate-100 opacity-60'
                            : 'bg-white border-slate-200 hover:border-[#1A4D2E]/30'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="text-[11px] text-slate-400 font-mono w-20 shrink-0 leading-tight">
                      <div>{fmtDayISO(po.created_at || po.date)}</div>
                      <div>{fmtTimeISO(po.created_at)}</div>
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-blue-700">{po.po_number}</span>
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${sb.cls}`}>{sb.label}</span>
                        {po.po_type && (
                          <span className="text-[9px] uppercase tracking-wide text-slate-400">
                            {po.po_type}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-500 truncate max-w-[200px]">
                        {po.vendor || '— No supplier —'}
                      </p>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <p className={`font-bold font-mono ${cancelled ? 'text-slate-400 line-through' : 'text-slate-800'}`}>
                      {formatPHP(po.grand_total)}
                    </p>
                    {bal > 0 && !cancelled && (
                      <p className="text-[10px] text-amber-600">bal {formatPHP(bal)}</p>
                    )}
                    {bal <= 0 && !cancelled && (
                      <p className="text-[10px] text-emerald-600">paid</p>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
          <div className="text-[10px] text-slate-300 text-center pt-2">
            {list.length} PO{list.length !== 1 ? 's' : ''} · newest first
          </div>
        </div>
      )}
    </div>
  );
}
