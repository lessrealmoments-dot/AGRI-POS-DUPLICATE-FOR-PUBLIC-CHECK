import { useState, useEffect, useMemo, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import {
  Printer, Search, ChevronDown, ChevronRight, TrendingUp, TrendingDown,
  Minus, ArrowRight, AlertTriangle, Package, RefreshCw
} from 'lucide-react';
import { toast } from 'sonner';

const formatPHP = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
})}`;

const RANGES = [
  { value: '30d',  label: 'Last 30 days' },
  { value: '60d',  label: 'Last 60 days' },
  { value: '90d',  label: 'Quarter (90d)' },
  { value: '180d', label: '6 Months' },
  { value: '365d', label: 'Year' },
  { value: 'all',  label: 'All time' },
];

const MONTH_LABEL = (yyyymm) => {
  if (!yyyymm || yyyymm.length < 7) return yyyymm;
  const [y, m] = yyyymm.split('-');
  const date = new Date(parseInt(y), parseInt(m) - 1, 1);
  return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
};

const FMT_DAY = (iso) => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('en-US',
      { month: 'short', day: 'numeric' });
  } catch { return ''; }
};

// ── Tiny inline change "pill" ────────────────────────────────────────────────
function ChangePill({ ch, showOldFirst = false }) {
  const cls = ch.direction === 'up'
    ? 'bg-rose-50 text-rose-700 border-rose-200'
    : ch.direction === 'down'
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : 'bg-slate-50 text-slate-600 border-slate-200';
  const Icon = ch.direction === 'up' ? TrendingUp
    : ch.direction === 'down' ? TrendingDown : Minus;
  const title = [
    FMT_DAY(ch.changed_at),
    ch.source_ref && `· ${ch.source_ref}`,
    ch.vendor && `· ${ch.vendor}`,
    ch.branch_name && `· ${ch.branch_name}`,
  ].filter(Boolean).join(' ');
  return (
    <span
      data-testid="capital-change-pill"
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-xs font-medium ${cls}`}
      title={title}
    >
      <Icon className="w-3 h-3" />
      {showOldFirst && (
        <>
          <span className="opacity-60 line-through">{formatPHP(ch.old_capital)}</span>
          <ArrowRight className="w-3 h-3 opacity-50" />
        </>
      )}
      <span>{formatPHP(ch.new_capital)}</span>
      {ch.delta_pct !== null && ch.delta_pct !== undefined && (
        <span className="opacity-70 text-[10px]">
          ({ch.delta_pct > 0 ? '+' : ''}{ch.delta_pct}%)
        </span>
      )}
    </span>
  );
}

// ── Sequence View: starting capital → c1 → c2 → ... → current ────────────────
function SequenceRow({ p }) {
  return (
    <tr className="border-t border-slate-100 align-top hover:bg-slate-50/40 break-inside-avoid">
      <td className="py-2 pr-3 text-sm font-medium text-slate-800 w-[26%]">
        <div>{p.name}</div>
        <div className="text-[11px] text-slate-400 font-normal">
          {p.sku && <span>SKU {p.sku}</span>}
          {p.unit && <span> · {p.unit}</span>}
          {p.is_repack && <span className="ml-1 text-amber-600">· repack</span>}
        </div>
      </td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-700 border border-slate-200">
            {formatPHP(p.first_capital)}
            <span className="ml-1 text-[10px] opacity-60">start</span>
          </span>
          {p.changes.map((ch, i) => (
            <span key={i} className="inline-flex items-center gap-1.5">
              <ArrowRight className="w-3 h-3 text-slate-400" />
              <ChangePill ch={ch} />
            </span>
          ))}
        </div>
      </td>
      <td className="py-2 pr-3 text-right whitespace-nowrap w-[14%]">
        <div className={`text-sm font-semibold ${
          p.net_delta > 0 ? 'text-rose-600'
          : p.net_delta < 0 ? 'text-emerald-600' : 'text-slate-500'
        }`}>
          {p.net_delta > 0 ? '+' : ''}{formatPHP(p.net_delta)}
        </div>
        {p.net_delta_pct !== null && p.net_delta_pct !== undefined && (
          <div className="text-[11px] text-slate-400">
            {p.net_delta_pct > 0 ? '+' : ''}{p.net_delta_pct}%
          </div>
        )}
      </td>
      <td className="py-2 text-right whitespace-nowrap w-[10%] text-sm font-bold text-slate-800">
        {formatPHP(p.current_capital)}
      </td>
    </tr>
  );
}

// ── Month-bucket View: one column per month, multiple changes per cell ───────
function MonthBucketTable({ products, months }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500 border-b border-slate-200">
            <th className="py-2 pr-3 w-[24%]">Product</th>
            <th className="py-2 pr-3 whitespace-nowrap">Start</th>
            {months.map(m => (
              <th key={m} className="py-2 pr-3 whitespace-nowrap">{MONTH_LABEL(m)}</th>
            ))}
            <th className="py-2 pr-3 text-right whitespace-nowrap">Current</th>
            <th className="py-2 text-right whitespace-nowrap">Net</th>
          </tr>
        </thead>
        <tbody>
          {products.map(p => (
            <tr key={p.product_id} className="border-t border-slate-100 align-top break-inside-avoid hover:bg-slate-50/40">
              <td className="py-2 pr-3 font-medium text-slate-800">
                <div>{p.name}</div>
                <div className="text-[11px] text-slate-400 font-normal">
                  {p.sku && <span>SKU {p.sku}</span>}
                  {p.unit && <span> · {p.unit}</span>}
                </div>
              </td>
              <td className="py-2 pr-3 text-slate-600 whitespace-nowrap">
                {formatPHP(p.first_capital)}
              </td>
              {months.map(m => {
                const cell = p.by_month?.[m] || [];
                if (!cell.length) {
                  return <td key={m} className="py-2 pr-3 text-slate-300">—</td>;
                }
                return (
                  <td key={m} className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {cell.map((ch, i) => <ChangePill key={i} ch={ch} />)}
                    </div>
                  </td>
                );
              })}
              <td className="py-2 pr-3 text-right whitespace-nowrap font-bold text-slate-800">
                {formatPHP(p.current_capital)}
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                <span className={`text-sm font-semibold ${
                  p.net_delta > 0 ? 'text-rose-600'
                  : p.net_delta < 0 ? 'text-emerald-600' : 'text-slate-500'
                }`}>
                  {p.net_delta > 0 ? '+' : ''}{formatPHP(p.net_delta)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Category Section (collapsible) ───────────────────────────────────────────
function CategorySection({ cat, viewMode, allMonths }) {
  const [open, setOpen] = useState(true);
  const monthsInCat = useMemo(() => {
    const set = new Set();
    cat.products.forEach(p => Object.keys(p.by_month || {}).forEach(m => set.add(m)));
    return Array.from(set).sort();
  }, [cat]);
  const monthCols = allMonths.length ? allMonths : monthsInCat;
  return (
    <Card className="border-slate-200 print:shadow-none print:border-slate-300 print:break-inside-avoid">
      <div
        className="flex items-center justify-between px-4 py-3 bg-slate-50/60 border-b border-slate-200 cursor-pointer print:bg-white"
        onClick={() => setOpen(o => !o)}
        data-testid={`capital-category-header-${cat.category}`}
      >
        <div className="flex items-center gap-2">
          {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
          <h3 className="text-sm font-semibold text-slate-800 tracking-wide">{cat.category}</h3>
          <Badge variant="outline" className="text-[10px] py-0 px-1.5">
            {cat.count} product{cat.count !== 1 ? 's' : ''}
          </Badge>
        </div>
      </div>

      {open && (
        <CardContent className="p-4 print:p-2">
          {viewMode === 'sequence' ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500 border-b border-slate-200">
                    <th className="py-2 pr-3 w-[26%]">Product</th>
                    <th className="py-2 pr-3">Cost progression</th>
                    <th className="py-2 pr-3 text-right w-[14%]">Net change</th>
                    <th className="py-2 text-right w-[10%]">Current</th>
                  </tr>
                </thead>
                <tbody>
                  {cat.products.map(p => <SequenceRow key={p.product_id} p={p} />)}
                </tbody>
              </table>
            </div>
          ) : (
            <MonthBucketTable products={cat.products} months={monthCols} />
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────
export default function CapitalChangeReportPage() {
  const { branches, selectedBranchId, canViewAllBranches } = useAuth();
  const [range, setRange] = useState('30d');
  const [category, setCategory] = useState('all');
  const [branchScope, setBranchScope] = useState(canViewAllBranches ? 'all' : (selectedBranchId || 'all'));
  const [sourceType, setSourceType] = useState('purchase_order');
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState('sequence');
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState(null);

  const fetchReport = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        range,
        source_type: sourceType,
      };
      if (category && category !== 'all') params.category = category;
      if (branchScope && branchScope !== 'all') params.branch_id = branchScope;
      if (search.trim()) params.search = search.trim();
      const { data: res } = await api.get('/products/capital-change-report', { params });
      setData(res);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to load capital change report');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [range, sourceType, category, branchScope, search]);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  // Global month axis for the bucket view across all categories ---------------
  const allMonths = useMemo(() => {
    if (!data?.categories) return [];
    const set = new Set();
    data.categories.forEach(c =>
      c.products.forEach(p =>
        Object.keys(p.by_month || {}).forEach(m => set.add(m))));
    return Array.from(set).sort();
  }, [data]);

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="space-y-4 p-4 sm:p-6 max-w-[1400px] mx-auto print:p-0 print:max-w-none">
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3 print:hidden">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Capital Change Report</h1>
          <p className="text-sm text-slate-500 mt-1">
            Products whose cost moved within the window. Use this to update your printed price list.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchReport}
            data-testid="capital-report-refresh-btn"
          >
            <RefreshCw className="w-4 h-4 mr-1.5" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handlePrint}
            className="bg-slate-900 hover:bg-slate-800"
            data-testid="capital-report-print-btn"
          >
            <Printer className="w-4 h-4 mr-1.5" />
            Print
          </Button>
        </div>
      </div>

      {/* ── Print Header (visible on paper only) ─────────────────────────── */}
      <div className="hidden print:block mb-3">
        <h1 className="text-xl font-bold">Capital Change Report</h1>
        <div className="text-xs text-slate-600">
          Range: {RANGES.find(r => r.value === range)?.label}
          {category !== 'all' && ` · Category: ${category}`}
          {branchScope !== 'all' && ` · Branch: ${branches.find(b => b.id === branchScope)?.name || branchScope}`}
          {sourceType !== 'all' && ` · Source: ${sourceType.replace('_', ' ')}`}
          {` · Generated ${new Date().toLocaleString('en-US')}`}
        </div>
      </div>

      {/* ── Filter Bar ───────────────────────────────────────────────────── */}
      <Card className="print:hidden">
        <CardContent className="p-3 flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wide text-slate-500">Range</label>
            <div className="flex flex-wrap gap-1" data-testid="capital-range-tabs">
              {RANGES.map(r => (
                <button
                  key={r.value}
                  data-testid={`capital-range-${r.value}`}
                  onClick={() => setRange(r.value)}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium border transition ${
                    range === r.value
                      ? 'bg-slate-900 text-white border-slate-900'
                      : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wide text-slate-500">Category</label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger className="w-[180px] h-8 text-xs" data-testid="capital-category-filter">
                <SelectValue placeholder="All categories" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All categories</SelectItem>
                {(data?.all_categories || []).map(c => (
                  <SelectItem key={c} value={c}>{c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {canViewAllBranches && branches?.length > 1 && (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] uppercase tracking-wide text-slate-500">Branch</label>
              <Select value={branchScope} onValueChange={setBranchScope}>
                <SelectTrigger className="w-[160px] h-8 text-xs" data-testid="capital-branch-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All branches</SelectItem>
                  {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wide text-slate-500">Source</label>
            <Select value={sourceType} onValueChange={setSourceType}>
              <SelectTrigger className="w-[150px] h-8 text-xs" data-testid="capital-source-filter">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="purchase_order">PO arrivals</SelectItem>
                <SelectItem value="manual_edit">Manual edits</SelectItem>
                <SelectItem value="all">Both</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
            <label className="text-[11px] uppercase tracking-wide text-slate-500">Search</label>
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Product name or SKU..."
                className="h-8 text-xs pl-7"
                data-testid="capital-search-input"
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] uppercase tracking-wide text-slate-500">View</label>
            <div className="inline-flex bg-slate-100 rounded-md p-0.5 border border-slate-200">
              <button
                onClick={() => setViewMode('sequence')}
                className={`px-2.5 py-1 rounded text-xs font-medium ${
                  viewMode === 'sequence' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
                }`}
                data-testid="capital-view-sequence"
              >
                Sequence
              </button>
              <button
                onClick={() => setViewMode('month')}
                className={`px-2.5 py-1 rounded text-xs font-medium ${
                  viewMode === 'month' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600'
                }`}
                data-testid="capital-view-month"
              >
                Month
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Summary ─────────────────────────────────────────────────────── */}
      {data?.summary && (
        <Card className="print:shadow-none print:border-slate-300">
          <CardContent className="p-3 flex flex-wrap items-center gap-4 text-xs">
            <div className="flex items-center gap-2">
              <Package className="w-4 h-4 text-slate-400" />
              <span className="text-slate-500">Products affected</span>
              <span className="font-bold text-slate-900" data-testid="capital-summary-products">
                {data.summary.products_affected}
              </span>
            </div>
            <div className="text-slate-300">·</div>
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Categories</span>
              <span className="font-bold text-slate-900">{data.summary.categories_affected}</span>
            </div>
            <div className="text-slate-300">·</div>
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Total changes</span>
              <span className="font-bold text-slate-900" data-testid="capital-summary-total">
                {data.summary.total_changes}
              </span>
            </div>
            <div className="text-slate-300">·</div>
            <div className="flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-rose-500" />
              <span className="text-rose-600 font-semibold">{data.summary.increases}</span>
              <span className="text-slate-500">increases</span>
            </div>
            <div className="flex items-center gap-1.5">
              <TrendingDown className="w-3.5 h-3.5 text-emerald-500" />
              <span className="text-emerald-600 font-semibold">{data.summary.decreases}</span>
              <span className="text-slate-500">decreases</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      {loading && (
        <Card><CardContent className="p-8 text-center text-sm text-slate-400">Loading…</CardContent></Card>
      )}

      {!loading && data && data.categories.length === 0 && (
        <Card>
          <CardContent className="p-10 text-center">
            <AlertTriangle className="w-8 h-8 text-slate-300 mx-auto mb-2" />
            <div className="text-sm text-slate-600 font-medium">No capital changes in this window</div>
            <div className="text-xs text-slate-400 mt-1">
              Try widening the time range or switching the source filter.
            </div>
          </CardContent>
        </Card>
      )}

      {!loading && data && data.categories.length > 0 && (
        <div className="space-y-4" data-testid="capital-report-body">
          {data.categories.map(cat => (
            <CategorySection
              key={cat.category}
              cat={cat}
              viewMode={viewMode}
              allMonths={allMonths}
            />
          ))}
        </div>
      )}

      {/* ── Print styles ─────────────────────────────────────────────────── */}
      <style>{`
        @media print {
          @page { size: A4 landscape; margin: 12mm; }
          body { background: white !important; }
          .print\\:hidden { display: none !important; }
          .print\\:block { display: block !important; }
          .print\\:p-0 { padding: 0 !important; }
          .print\\:p-2 { padding: 0.5rem !important; }
          .print\\:shadow-none { box-shadow: none !important; }
          .print\\:break-inside-avoid { break-inside: avoid; }
          .print\\:bg-white { background: white !important; }
          .print\\:max-w-none { max-width: none !important; }
        }
      `}</style>
    </div>
  );
}
