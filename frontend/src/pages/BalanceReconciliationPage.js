/**
 * Phase 2A — Customer Balance Reconciliation Report (READ-ONLY).
 *
 * Diagnostic-only page surfaced under the Audit Center. Compares each
 * customer's stored AR balance against the ledger-computed balance derived
 * from their non-voided invoices. Surfaces drift > ₱0.50.
 *
 * STRICTLY READ-ONLY:
 *   - No "Fix Balance" button
 *   - No "Auto-Correct" action
 *   - No mutation calls of any kind
 *   - Every rendered control is a filter or a passive display
 */
import { useState, useEffect, useMemo, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { formatPHP, fmtDateTime } from '../lib/utils';
import {
  RefreshCw, Download, ArrowLeft, ShieldAlert, Eye, Info,
  AlertTriangle, CircleAlert, Building2, Search,
} from 'lucide-react';
import { toast } from 'sonner';

const RISK_BADGE = {
  'Minor Difference': 'bg-amber-100 text-amber-800 border-amber-200',
  'Needs Review': 'bg-orange-100 text-orange-800 border-orange-200',
  'Critical': 'bg-red-100 text-red-800 border-red-200',
};

const RISK_ICON = {
  'Minor Difference': <CircleAlert size={14} className="text-amber-600" />,
  'Needs Review': <AlertTriangle size={14} className="text-orange-600" />,
  'Critical': <ShieldAlert size={14} className="text-red-600" />,
};

function fmtDrift(n) {
  const v = Number(n) || 0;
  const sign = v >= 0 ? '+' : '−';
  return sign + '₱' + Math.abs(v).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function exportCsv(rows, generatedAt) {
  if (!rows || rows.length === 0) {
    toast.error('No rows to export');
    return;
  }
  const header = [
    'Customer ID', 'Customer Name', 'Phone', 'Branch', 'Stored Balance',
    'Ledger Balance', 'Drift', '|Drift|', 'Risk', 'Open Invoices',
    'Voided/Cancelled Invoices', 'Payments', 'Returns',
    'Last Transaction', 'Flags', 'Recommended Action',
  ];
  const escape = (v) => {
    const s = (v ?? '').toString();
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };
  const lines = [header.map(escape).join(',')];
  for (const r of rows) {
    lines.push([
      r.customer_id, r.customer_name, r.customer_phone, r.branch_name || r.branch_id,
      r.stored_balance, r.ledger_balance, r.drift, r.abs_drift,
      r.risk_level, r.open_invoice_count, r.voided_or_cancelled_count,
      r.payment_count, r.return_count, r.last_transaction_date,
      (r.flags || []).join('; '), r.recommended_action,
    ].map(escape).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const tag = (generatedAt || new Date().toISOString()).replace(/[:.]/g, '-');
  a.download = `balance-reconciliation_${tag}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function BalanceReconciliationPage() {
  const { user, branches } = useAuth();
  const navigate = useNavigate();

  const isAdmin = user?.role === 'admin' || user?.role === 'owner' || user?.is_super_admin;

  const [branchId, setBranchId] = useState('');
  const [minDrift, setMinDrift] = useState(0.5);
  const [search, setSearch] = useState('');
  const [riskFilter, setRiskFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [selectedRow, setSelectedRow] = useState(null);

  const fetchReport = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    try {
      const params = { min_drift: minDrift, limit: 500 };
      if (branchId && branchId !== 'all') params.branch_id = branchId;
      const res = await api.get('/admin/customer-balance-reconciliation', { params });
      setData(res.data);
    } catch (e) {
      const detail = e?.response?.data?.detail || 'Failed to load report';
      toast.error(detail);
    } finally {
      setLoading(false);
    }
  }, [isAdmin, branchId, minDrift]);

  useEffect(() => {
    fetchReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredRows = useMemo(() => {
    if (!data?.rows) return [];
    let rows = data.rows;
    if (riskFilter !== 'all') rows = rows.filter(r => r.risk_level === riskFilter);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter(r =>
        r.customer_name?.toLowerCase().includes(q) ||
        r.customer_phone?.toLowerCase().includes(q) ||
        r.customer_id?.toLowerCase().includes(q)
      );
    }
    return rows;
  }, [data, riskFilter, search]);

  if (!isAdmin) {
    return (
      <div className="p-6">
        <Card className="border-red-200 bg-red-50">
          <CardContent className="p-6 text-red-700 text-sm">
            This diagnostic is restricted to administrators and owners.
          </CardContent>
        </Card>
      </div>
    );
  }

  const summary = data?.summary;

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1400px] mx-auto" data-testid="balance-reconciliation-page">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate('/audit')} data-testid="back-to-audit-btn">
              <ArrowLeft size={14} className="mr-1" /> Audit Center
            </Button>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight mt-2">
            Customer Balance Reconciliation
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Read-only diagnostic. Compares each customer&apos;s stored balance with the
            ledger-computed balance derived from their non-voided invoices.
            Surfaces drift greater than ₱0.50.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline" size="sm"
            onClick={() => exportCsv(filteredRows, data?.generated_at)}
            disabled={!filteredRows?.length}
            data-testid="export-csv-btn"
          >
            <Download size={14} className="mr-1.5" />Export CSV
          </Button>
          <Button
            size="sm" onClick={fetchReport} disabled={loading}
            data-testid="refresh-report-btn"
          >
            <RefreshCw size={14} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            {loading ? 'Computing…' : 'Refresh'}
          </Button>
        </div>
      </div>

      {/* ── Read-only banner ────────────────────────────────────────────── */}
      <div
        className="flex items-start gap-2 p-3 rounded-md border border-blue-200 bg-blue-50 text-blue-900 text-xs"
        data-testid="readonly-banner"
      >
        <Info size={14} className="mt-0.5 flex-shrink-0" />
        <div>
          <strong>Diagnostic only.</strong> This report does not change any balance,
          invoice, payment, return, or credit. To act on a flagged customer, open
          their statement from the Customers page and resolve there.
        </div>
      </div>

      {/* ── Filters ─────────────────────────────────────────────────────── */}
      <Card>
        <CardContent className="p-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <Label className="text-xs text-slate-500">Branch</Label>
              <Select value={branchId || 'all'} onValueChange={(v) => setBranchId(v === 'all' ? '' : v)}>
                <SelectTrigger className="mt-1 h-9" data-testid="branch-filter-select">
                  <SelectValue placeholder="All branches" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All branches</SelectItem>
                  {(branches || []).map((b) => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Min drift (₱)</Label>
              <Input
                type="number" step="0.01" min="0"
                className="mt-1 h-9"
                value={minDrift}
                onChange={(e) => setMinDrift(parseFloat(e.target.value) || 0)}
                data-testid="min-drift-input"
              />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Risk filter</Label>
              <Select value={riskFilter} onValueChange={setRiskFilter}>
                <SelectTrigger className="mt-1 h-9" data-testid="risk-filter-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All risk levels</SelectItem>
                  <SelectItem value="Minor Difference">Minor Difference</SelectItem>
                  <SelectItem value="Needs Review">Needs Review</SelectItem>
                  <SelectItem value="Critical">Critical</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Search</Label>
              <div className="relative mt-1">
                <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  className="h-9 pl-7"
                  placeholder="Name, phone, or ID"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  data-testid="search-input"
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Summary cards ──────────────────────────────────────────────── */}
      {summary && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3" data-testid="summary-cards">
          <SummaryCard label="Customers scanned" value={summary.total_customers_scanned} />
          <SummaryCard label="With drift > ₱0.50" value={summary.total_with_drift} highlight />
          <SummaryCard
            label="Total |drift|"
            value={'₱' + Number(summary.total_abs_drift_amount || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 })}
          />
          <SummaryCard label="Needs Review" value={summary.by_risk?.['Needs Review'] || 0} accent="orange" />
          <SummaryCard label="Critical" value={summary.by_risk?.['Critical'] || 0} accent="red" />
        </div>
      )}

      {/* ── Rows table ──────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="py-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">
              Drift report ({filteredRows.length} of {data?.rows?.length || 0})
            </CardTitle>
            {data?.generated_at && (
              <span className="text-xs text-slate-400">
                Generated {fmtDateTime(data.generated_at)}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-6 text-center text-sm text-slate-500" data-testid="loading-state">
              Computing reconciliation…
            </div>
          ) : !data?.rows?.length ? (
            <div className="p-6 text-center text-sm text-slate-500" data-testid="empty-state">
              No drift detected for the current filter. ✓
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs" data-testid="recon-table">
                <thead className="bg-slate-50 text-slate-600 border-b">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Customer</th>
                    <th className="text-left px-3 py-2 font-medium">Branch</th>
                    <th className="text-right px-3 py-2 font-medium">Stored</th>
                    <th className="text-right px-3 py-2 font-medium">Ledger</th>
                    <th className="text-right px-3 py-2 font-medium">Drift</th>
                    <th className="text-center px-3 py-2 font-medium">Open / Voided</th>
                    <th className="text-center px-3 py-2 font-medium">Pmt / Ret</th>
                    <th className="text-left px-3 py-2 font-medium">Risk</th>
                    <th className="text-center px-3 py-2 font-medium"></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((r) => (
                    <tr
                      key={r.customer_id}
                      className="border-b hover:bg-slate-50/70"
                      data-testid={`recon-row-${r.customer_id}`}
                    >
                      <td className="px-3 py-2">
                        <div className="font-medium text-slate-800">{r.customer_name || '—'}</div>
                        <div className="text-[10px] text-slate-400">{r.customer_phone || r.customer_id?.slice(0, 8)}</div>
                        {r.flags?.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {r.flags.map((f, i) => (
                              <Badge key={i} variant="outline" className="text-[9px] py-0 px-1.5">
                                {f}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-slate-600">
                        <span className="inline-flex items-center gap-1">
                          <Building2 size={12} />
                          {r.branch_name || '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatPHP(r.stored_balance)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatPHP(r.ledger_balance)}</td>
                      <td className={`px-3 py-2 text-right tabular-nums font-semibold ${r.drift > 0 ? 'text-red-700' : 'text-blue-700'}`}>
                        {fmtDrift(r.drift)}
                      </td>
                      <td className="px-3 py-2 text-center text-slate-500">
                        {r.open_invoice_count} / {r.voided_or_cancelled_count}
                      </td>
                      <td className="px-3 py-2 text-center text-slate-500">
                        {r.payment_count} / {r.return_count}
                      </td>
                      <td className="px-3 py-2">
                        <Badge className={`text-[10px] inline-flex items-center gap-1 border ${RISK_BADGE[r.risk_level] || ''}`}>
                          {RISK_ICON[r.risk_level]}
                          {r.risk_level}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <Button
                          size="sm" variant="ghost"
                          onClick={() => setSelectedRow(r)}
                          data-testid={`view-detail-${r.customer_id}`}
                        >
                          <Eye size={14} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Detail drawer (read-only) ──────────────────────────────────── */}
      {selectedRow && (
        <DetailDrawer row={selectedRow} onClose={() => setSelectedRow(null)} />
      )}

      {/* ── Methodology footer ─────────────────────────────────────────── */}
      {data?.ledger_formula && (
        <Card className="bg-slate-50 border-slate-200">
          <CardContent className="p-4 text-[11px] text-slate-600 space-y-1">
            <div><strong>Formula:</strong> {data.ledger_formula}</div>
            <div>
              <strong>Excluded statuses:</strong>{' '}
              {data.non_ledger_invoice_statuses?.join(', ')}
            </div>
            <div>
              <strong>Risk bands:</strong> Minor ≤ ₱100 · Needs Review ≤ ₱5,000 · Critical &gt; ₱5,000
            </div>
            <div>
              <strong>Read-only:</strong> {String(!!data.read_only)} · <strong>Phase:</strong> {data.phase}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function SummaryCard({ label, value, accent, highlight }) {
  const accentClass = {
    orange: 'border-orange-200 bg-orange-50 text-orange-800',
    red: 'border-red-200 bg-red-50 text-red-800',
  }[accent] || (highlight ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-slate-200 bg-white text-slate-800');
  return (
    <div className={`rounded-md border px-3 py-3 ${accentClass}`} data-testid={`summary-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <div className="text-[10px] uppercase tracking-wide opacity-70">{label}</div>
      <div className="text-xl font-semibold mt-1 tabular-nums">{value}</div>
    </div>
  );
}

function DetailDrawer({ row, onClose }) {
  return (
    <div
      className="fixed inset-0 bg-black/30 z-50 flex justify-end"
      onClick={onClose}
      data-testid="detail-drawer"
    >
      <div
        className="bg-white w-full sm:w-[480px] h-full overflow-y-auto p-5 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold">{row.customer_name}</h2>
            <p className="text-xs text-slate-500 mt-1">{row.customer_phone || row.customer_id}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>×</Button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <Stat label="Stored balance" value={formatPHP(row.stored_balance)} />
          <Stat label="Ledger balance" value={formatPHP(row.ledger_balance)} />
          <Stat
            label="Drift"
            value={fmtDrift(row.drift)}
            valueClass={row.drift > 0 ? 'text-red-700' : 'text-blue-700'}
          />
          <Stat label="Risk" value={row.risk_level} />
          <Stat label="Open invoices" value={row.open_invoice_count} />
          <Stat label="Voided/Cancelled" value={row.voided_or_cancelled_count} />
          <Stat label="Payments recorded" value={row.payment_count} />
          <Stat label="Returns" value={row.return_count} />
          <Stat label="Branch" value={row.branch_name || row.branch_id || '—'} />
          <Stat label="Last transaction" value={row.last_transaction_date || '—'} />
        </div>
        {row.flags?.length > 0 && (
          <div>
            <Label className="text-xs text-slate-500">Flags</Label>
            <div className="flex flex-wrap gap-1 mt-1">
              {row.flags.map((f, i) => (
                <Badge key={i} variant="outline" className="text-[10px]">{f}</Badge>
              ))}
            </div>
          </div>
        )}
        <div>
          <Label className="text-xs text-slate-500">Recommended action</Label>
          <p className="text-xs text-slate-700 mt-1 leading-relaxed">
            {row.recommended_action}
          </p>
        </div>
        <div className="bg-amber-50 border border-amber-200 rounded p-2 text-[11px] text-amber-800 flex gap-2">
          <Info size={12} className="mt-0.5 flex-shrink-0" />
          <span>This view is diagnostic. Open the customer&apos;s statement to investigate; do not adjust balances directly.</span>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, valueClass }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${valueClass || 'text-slate-800'}`}>{value}</div>
    </div>
  );
}
