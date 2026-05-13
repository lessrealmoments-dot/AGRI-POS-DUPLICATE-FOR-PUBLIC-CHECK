/**
 * POSSalesHistoryView — POS-terminal sales history with time-range chips.
 *
 * Replaces the legacy single-date Sales History view. Supports:
 *   • Range chips: today / week / month / 30d / custom
 *   • Custom range = two date pickers (from / to)
 *   • Free-text search (invoice number OR customer name)
 *   • Auto-branch-scoped via `currentBranch.id`
 *   • Row click → opens existing InvoiceDetailModal via onSelect(inv)
 *   • Running totals strip (cash/digital/credit/grand/count)
 *
 * Backend: GET /api/invoices/history/by-range
 */
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../contexts/AuthContext';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { RefreshCw, FileText, WifiOff, Search } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: 'week',  label: 'This Week' },
  { value: 'month', label: 'This Month' },
  { value: '30d',   label: 'Last 30 Days' },
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

export default function POSSalesHistoryView({
  currentBranch,
  isOnline,
  onSelectInvoice,
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
      const params = { range, include_voided: true };
      if (range === 'custom') {
        if (fromDate) params.from_date = fromDate;
        if (toDate)   params.to_date   = toDate;
      }
      if (currentBranch?.id && currentBranch.id !== 'all') params.branch_id = currentBranch.id;
      if (search.trim()) params.search = search.trim();
      const res = await api.get('/invoices/history/by-range', { params });
      setList(res.data.invoices || []);
      setTotals(res.data.totals || null);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to load sales history');
    } finally {
      setLoading(false);
    }
  }, [range, fromDate, toDate, search, currentBranch?.id, isOnline]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-3" data-testid="pos-sales-history-view">
      {/* Range chips */}
      <div className="flex flex-wrap items-center gap-1.5" data-testid="pos-sales-range-tabs">
        {RANGES.map(r => (
          <button
            key={r.value}
            onClick={() => setRange(r.value)}
            data-testid={`pos-sales-range-${r.value}`}
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
              data-testid="pos-sales-custom-from"
            />
            <span className="text-xs text-slate-400">→</span>
            <Input
              type="date"
              value={toDate}
              onChange={e => setToDate(e.target.value)}
              className="h-8 w-36 text-xs"
              data-testid="pos-sales-custom-to"
            />
          </div>
        )}
      </div>

      {/* Running totals */}
      {totals && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[
            { label: 'Cash Sales',    value: formatPHP(totals.cash),        color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
            { label: 'Digital Sales', value: formatPHP(totals.digital || 0),color: 'text-blue-700',    bg: 'bg-blue-50',    border: 'border-blue-200' },
            { label: 'Credit Sales',  value: formatPHP(totals.credit),      color: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-200' },
            { label: 'Grand Total',   value: formatPHP(totals.grand_total), color: 'text-[#1A4D2E]',   bg: 'bg-emerald-50', border: 'border-[#1A4D2E]/30' },
            { label: 'Transactions',  value: totals.count,                  color: 'text-slate-700',   bg: 'bg-slate-50',   border: 'border-slate-200',
              sub: totals.voided_count > 0 ? `${totals.voided_count} voided` : null },
          ].map(k => (
            <div key={k.label} className={`rounded-xl border ${k.border} ${k.bg} px-4 py-3`}>
              <p className="text-[11px] text-slate-500 font-medium">{k.label}</p>
              <p className={`text-lg font-bold font-mono ${k.color}`}>{k.value}</p>
              {k.sub && <p className="text-[10px] text-slate-400">{k.sub}</p>}
            </div>
          ))}
        </div>
      )}

      {/* Search + Refresh */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search invoice # or customer..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="h-9 pl-9 text-sm"
            data-testid="pos-sales-search-input"
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
          <FileText size={28} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">No sales found in this window</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {list.map(inv => {
            const isVoided = inv.status === 'voided';
            const ptype = inv.payment_type || 'cash';
            const badge = isVoided ? { label: 'VOIDED', cls: 'bg-slate-200 text-slate-500' }
              : ptype === 'split'   ? { label: `Split · ${inv.digital_platform || 'Digital'}`, cls: 'bg-indigo-100 text-indigo-700' }
              : ptype === 'digital' ? { label: inv.digital_platform || 'Digital',               cls: 'bg-blue-100 text-blue-700' }
              : ptype === 'credit' || ptype === 'partial' ? { label: 'Credit', cls: 'bg-amber-100 text-amber-700' }
              : { label: 'Cash', cls: 'bg-emerald-100 text-emerald-700' };
            return (
              <button
                key={inv.id}
                onClick={() => onSelectInvoice && onSelectInvoice(inv)}
                data-testid={`pos-sales-history-row-${inv.id}`}
                className={`w-full text-left rounded-xl border px-4 py-3 transition-all hover:shadow-sm ${
                  isVoided ? 'bg-slate-50 border-slate-100 opacity-60'
                           : 'bg-white border-slate-200 hover:border-[#1A4D2E]/30'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="text-[11px] text-slate-400 font-mono w-20 shrink-0 leading-tight">
                      <div>{fmtDayISO(inv.created_at)}</div>
                      <div>{fmtTimeISO(inv.created_at)}</div>
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-blue-700">{inv.invoice_number}</span>
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${badge.cls}`}>{badge.label}</span>
                      </div>
                      <p className="text-xs text-slate-500 truncate max-w-[200px]">{inv.customer_name || 'Walk-in'}</p>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <p className={`font-bold font-mono ${isVoided ? 'text-slate-400 line-through' : 'text-slate-800'}`}>
                      {formatPHP(inv.grand_total)}
                    </p>
                    {inv.balance > 0 && !isVoided && (
                      <p className="text-[10px] text-amber-600">bal {formatPHP(inv.balance)}</p>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
          <div className="text-[10px] text-slate-300 text-center pt-2">
            {list.length} transaction{list.length !== 1 ? 's' : ''} · newest first
          </div>
        </div>
      )}
    </div>
  );
}
