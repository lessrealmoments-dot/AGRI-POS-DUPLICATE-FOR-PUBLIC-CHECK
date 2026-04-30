import { useState, useEffect, useCallback, useMemo } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { Responsive, useContainerWidth } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Separator } from '../components/ui/separator';
import { toast } from 'sonner';
import {
  DollarSign, ShoppingCart, Package, Users, AlertTriangle,
  TrendingUp, ArrowDown, Building2, Wallet, Receipt, ArrowRight,
  Truck, Clock, CreditCard, Banknote, CheckCircle2, Calendar,
  BarChart3, RefreshCw, ArrowUpRight, ArrowDownRight, Lock, ShieldCheck,
  CalendarClock, ChevronDown, ChevronRight, FileCheck, GripVertical,
  RotateCcw, Smartphone
} from 'lucide-react';
import { formatPHP } from '../lib/utils';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';
import PendingReviewsWidget from '../components/PendingReviewsWidget';
import SalesTrendsWidget from '../components/dashboard/SalesTrendsWidget';
import BranchComparisonWidget from '../components/dashboard/BranchComparisonWidget';
import AccountsPayableWidget from '../components/dashboard/AccountsPayableWidget';
import InternalProfitWidget from '../components/dashboard/InternalProfitWidget';
import AuditPulseWidget from '../components/dashboard/AuditPulseWidget';

const LAYOUT_KEY = 'agribooks_dashboard_layout';

// Minimum heights per widget to prevent collapsing
const MIN_H_MAP = {
  kpis: 3, 'sales-trends': 5, ap: 5, 'branch-comparison': 5,
  'pending-reviews': 4, branches: 7, payables: 4, alerts: 4,
  'top-revenue': 4, 'internal-profit': 6, 'cash-position': 5,
  'ar-overview': 5, 'credits-today': 5, 'ar-payments': 5,
  'top-products': 4, 'recent-sales': 4, 'audit-pulse': 7,
};
const MIN_W_MAP = {
  kpis: 6, 'sales-trends': 4, ap: 3, 'branch-comparison': 3,
  'pending-reviews': 4, branches: 6, payables: 3, alerts: 3,
  'top-revenue': 3, 'internal-profit': 4, 'cash-position': 3,
  'ar-overview': 4, 'credits-today': 3, 'ar-payments': 3,
  'top-products': 3, 'recent-sales': 3, 'audit-pulse': 3,
};

// Validates a saved layout — resets to defaults if any widget is smaller than minH
function validateLayouts(saved, defaults) {
  try {
    const isValid = Object.keys(defaults).every(bp => {
      if (!saved[bp] || !Array.isArray(saved[bp])) return false;
      return defaults[bp].every(item => {
        const s = saved[bp].find(x => x.i === item.i);
        if (!s) return false;
        return s.h >= (MIN_H_MAP[item.i] ?? 2) && s.w >= (MIN_W_MAP[item.i] ?? 2);
      });
    });
    return isValid ? saved : defaults;
  } catch { return defaults; }
}

// ── Shared small components ──────────────────────────────────────────────────

function KpiCard({ label, value, sub, icon: Icon, color, testId }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3.5 hover:shadow-sm transition-shadow" data-testid={testId}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] text-slate-500 font-medium">{label}</span>
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${color.bg}`}>
          <Icon size={14} className={color.text} strokeWidth={1.8} />
        </div>
      </div>
      <p className={`text-lg font-bold tracking-tight ${color.text}`} style={{ fontFamily: 'Manrope' }}>{value}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function AgingBar({ aging }) {
  const total = aging?.total || 1;
  return (
    <div className="space-y-1.5 text-xs">
      {[
        { label: '0–30d', val: aging?.current || 0, color: 'bg-emerald-400' },
        { label: '31–60d', val: aging?.days_31_60 || 0, color: 'bg-amber-400' },
        { label: '61–90d', val: aging?.days_61_90 || 0, color: 'bg-orange-400' },
        { label: '90+d', val: aging?.over_90 || 0, color: 'bg-red-500' },
      ].map(({ label, val, color }) => (
        <div key={label}>
          <div className="flex justify-between mb-0.5 text-[10px]">
            <span className="text-slate-500">{label}</span>
            <span className="font-mono font-semibold">{formatPHP(val)}</span>
          </div>
          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div className={`h-full ${color} rounded-full`} style={{ width: `${Math.max(2, (val / total) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function BranchCard({ branch, onSelect }) {
  const today = new Date().toISOString().slice(0, 10);
  const lastClose = branch.last_close_date;
  const daysSinceClose = lastClose ? Math.floor((new Date(today) - new Date(lastClose)) / 86400000) : null;
  const dot = branch.status === 'good' ? 'bg-emerald-400' : branch.status === 'warning' ? 'bg-amber-400' : 'bg-red-500';

  return (
    <Card className="border-slate-200 hover:border-[#1A4D2E]/40 transition-all cursor-pointer hover:shadow-md group" onClick={() => onSelect(branch.id)}>
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2"><div className={`w-2 h-2 rounded-full ${dot}`} /><h3 className="font-semibold text-sm">{branch.name}</h3></div>
          <ArrowRight size={13} className="text-slate-300 group-hover:text-[#1A4D2E] transition-colors" />
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <div><p className="text-slate-400">Total Sales</p><p className="font-bold text-emerald-700">{formatPHP(branch.today_revenue || 0)}</p></div>
          <div><p className="text-slate-400">Cash Sales</p><p className="font-bold text-green-700">{formatPHP(branch.today_cash_sales || 0)}</p></div>
          <div><p className="text-slate-400">New Credit</p><p className="font-bold text-amber-700">{formatPHP(branch.today_new_credit || 0)}</p></div>
          <div><p className="text-slate-400">Digital Sales</p><p className="font-bold text-indigo-700">{formatPHP(branch.today_digital_sales || 0)}</p></div>
          <div><p className="text-slate-400">Cashier + Safe</p><p className="font-bold text-blue-700">{formatPHP(branch.total_cash || 0)}</p></div>
          <div><p className="text-slate-400">AR Outstanding</p><p className={`font-bold ${branch.receivables > 0 ? 'text-amber-600' : 'text-slate-400'}`}>{formatPHP(branch.receivables || 0)}</p></div>
        </div>
        {branch.inventory_value && (<><Separator className="my-2" /><div className="grid grid-cols-2 gap-x-4 text-[10px]">
          <div><span className="text-slate-400">Stock @ Capital</span><p className="font-semibold text-slate-700 font-mono">{formatPHP(branch.inventory_value.capital_value)}</p></div>
          <div><span className="text-slate-400">Stock @ Retail</span><p className="font-semibold text-emerald-700 font-mono">{formatPHP(branch.inventory_value.retail_value)}</p></div>
        </div></>)}
        <Separator className="my-2" />
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1"><Lock size={9} /> {lastClose ? daysSinceClose === 0 ? 'Closed today' : `Last close: ${lastClose}` : 'Never closed'}</span>
          {branch.low_stock_count > 0 && <span className="flex items-center gap-1 text-amber-600"><AlertTriangle size={9} /> {branch.low_stock_count} low stock</span>}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Grid Widget Wrapper ──────────────────────────────────────────────────────
function GridWidget({ title, children, className = '' }) {
  return (
    <div className={`h-full flex flex-col bg-white rounded-xl border border-slate-200 overflow-hidden ${className}`}>
      {title && (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100 cursor-grab active:cursor-grabbing drag-handle shrink-0">
          <GripVertical size={12} className="text-slate-300" />
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">{title}</span>
        </div>
      )}
      <div className="flex-1 overflow-auto p-0">{children}</div>
    </div>
  );
}

// ── DEFAULT LAYOUTS ──────────────────────────────────────────────────────────
const OWNER_DEFAULT_LAYOUT = {
  lg: [
    { i: 'kpis', x: 0, y: 0, w: 12, h: 3, static: true, minH: 3, minW: 6 },
    { i: 'sales-trends', x: 0, y: 3, w: 8, h: 8, minH: 5, minW: 4 },
    { i: 'ap', x: 8, y: 3, w: 4, h: 8, minH: 5, minW: 3 },
    { i: 'audit-pulse', x: 0, y: 11, w: 4, h: 9, minH: 7, minW: 3 },
    { i: 'branch-comparison', x: 4, y: 11, w: 4, h: 9, minH: 5, minW: 3 },
    { i: 'pending-reviews', x: 8, y: 11, w: 4, h: 9, minH: 4, minW: 4 },
    { i: 'branches', x: 0, y: 20, w: 12, h: 9, static: true, minH: 7, minW: 6 },
    { i: 'payables', x: 0, y: 29, w: 4, h: 7, minH: 4, minW: 3 },
    { i: 'alerts', x: 4, y: 29, w: 4, h: 7, minH: 4, minW: 3 },
    { i: 'top-revenue', x: 8, y: 29, w: 4, h: 7, minH: 4, minW: 3 },
    { i: 'internal-profit', x: 0, y: 36, w: 6, h: 10, minH: 6, minW: 4 },
  ],
  md: [
    { i: 'kpis', x: 0, y: 0, w: 10, h: 3, static: true, minH: 3, minW: 6 },
    { i: 'sales-trends', x: 0, y: 3, w: 10, h: 8, minH: 5, minW: 4 },
    { i: 'audit-pulse', x: 0, y: 11, w: 5, h: 9, minH: 7, minW: 3 },
    { i: 'ap', x: 5, y: 11, w: 5, h: 9, minH: 5, minW: 3 },
    { i: 'branch-comparison', x: 0, y: 20, w: 5, h: 8, minH: 5, minW: 3 },
    { i: 'pending-reviews', x: 5, y: 20, w: 5, h: 8, minH: 4, minW: 4 },
    { i: 'branches', x: 0, y: 28, w: 10, h: 12, static: true, minH: 7, minW: 6 },
    { i: 'payables', x: 0, y: 40, w: 5, h: 7, minH: 4, minW: 3 },
    { i: 'alerts', x: 5, y: 40, w: 5, h: 7, minH: 4, minW: 3 },
    { i: 'top-revenue', x: 0, y: 47, w: 10, h: 6, minH: 4, minW: 3 },
    { i: 'internal-profit', x: 0, y: 53, w: 10, h: 10, minH: 6, minW: 4 },
  ],
};

const BRANCH_DEFAULT_LAYOUT = {
  lg: [
    { i: 'kpis', x: 0, y: 0, w: 12, h: 3, static: true, minH: 3, minW: 6 },
    { i: 'sales-trends', x: 0, y: 3, w: 8, h: 8, minH: 5, minW: 4 },
    { i: 'cash-position', x: 8, y: 3, w: 4, h: 8, minH: 5, minW: 3 },
    { i: 'ar-overview', x: 0, y: 11, w: 8, h: 7, minH: 5, minW: 4 },
    { i: 'ap', x: 8, y: 11, w: 4, h: 7, minH: 5, minW: 3 },
    { i: 'audit-pulse', x: 0, y: 18, w: 4, h: 9, minH: 7, minW: 3 },
    { i: 'pending-reviews', x: 4, y: 18, w: 8, h: 9, minH: 4, minW: 4 },
    { i: 'credits-today', x: 0, y: 27, w: 6, h: 7, minH: 5, minW: 3 },
    { i: 'ar-payments', x: 6, y: 27, w: 6, h: 7, minH: 5, minW: 3 },
    { i: 'payables', x: 0, y: 34, w: 4, h: 7, minH: 4, minW: 3 },
    { i: 'top-products', x: 4, y: 34, w: 4, h: 7, minH: 4, minW: 3 },
    { i: 'recent-sales', x: 8, y: 34, w: 4, h: 7, minH: 4, minW: 3 },
  ],
  md: [
    { i: 'kpis', x: 0, y: 0, w: 10, h: 4, static: true, minH: 3, minW: 6 },
    { i: 'sales-trends', x: 0, y: 4, w: 10, h: 8, minH: 5, minW: 4 },
    { i: 'cash-position', x: 0, y: 12, w: 5, h: 7, minH: 5, minW: 3 },
    { i: 'ap', x: 5, y: 12, w: 5, h: 7, minH: 5, minW: 3 },
    { i: 'audit-pulse', x: 0, y: 19, w: 5, h: 9, minH: 7, minW: 3 },
    { i: 'ar-overview', x: 5, y: 19, w: 5, h: 9, minH: 5, minW: 4 },
    { i: 'pending-reviews', x: 0, y: 28, w: 10, h: 5, minH: 4, minW: 4 },
    { i: 'credits-today', x: 0, y: 33, w: 5, h: 7, minH: 5, minW: 3 },
    { i: 'ar-payments', x: 5, y: 33, w: 5, h: 7, minH: 5, minW: 3 },
    { i: 'payables', x: 0, y: 40, w: 5, h: 7, minH: 4, minW: 3 },
    { i: 'top-products', x: 5, y: 40, w: 5, h: 7, minH: 4, minW: 3 },
    { i: 'recent-sales', x: 0, y: 47, w: 10, h: 6, minH: 4, minW: 3 },
  ],
};

// ── MAIN DASHBOARD ───────────────────────────────────────────────────────────
export default function DashboardPage() {
  const { isConsolidatedView, switchBranch, selectedBranchId, currentBranch, user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [branchSummary, setBranchSummary] = useState(null);
  const [poSummary, setPoSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [detailType, setDetailType] = useState('sale');
  const openDetailModal = (num, type = 'sale') => { setSelectedInvoiceNumber(num); setDetailType(type); setInvoiceModalOpen(true); };
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const openCustStmt = (id, name) => { if (!id) return; setStmtCustomer({ id, name }); setStmtOpen(true); };
  const [analyticsPeriod, setAnalyticsPeriod] = useState('this_month');
  const [unclosedDays, setUnclosedDays] = useState(null);
  // Self-heal banner: shown when settings.company_info is missing (e.g.
  // post-Reset). One-tap restore reads from the immutable organizations row
  // so SMS signatures stop reading just "- MAIN BRANCH".
  const [companyInfoMissing, setCompanyInfoMissing] = useState(false);
  const [companyInfoSuggested, setCompanyInfoSuggested] = useState(null);
  const [restoringCompanyInfo, setRestoringCompanyInfo] = useState(false);
  const { width: gridWidth, containerRef: gridRef, mounted: gridMounted } = useContainerWidth();

  const layoutKey = `${LAYOUT_KEY}_${user?.id || 'default'}_${isConsolidatedView ? 'owner' : 'branch'}`;
  const defaultLayout = isConsolidatedView ? OWNER_DEFAULT_LAYOUT : BRANCH_DEFAULT_LAYOUT;

  const [layouts, setLayouts] = useState(() => {
    try {
      const saved = localStorage.getItem(layoutKey);
      return saved ? validateLayouts(JSON.parse(saved), defaultLayout) : defaultLayout;
    } catch { return defaultLayout; }
  });

  // Synchronously reset layouts when the view switches (owner ↔ branch).
  // Uses the "getDerivedStateFromProps" hook pattern: calling setState during render
  // causes React to immediately re-render with updated state BEFORE painting to DOM,
  // preventing the grid from ever rendering with the wrong layout (which would trigger
  // onLayoutChange → write bad data to localStorage before a useEffect could correct it).
  const [prevLayoutKey, setPrevLayoutKey] = useState(layoutKey);
  if (prevLayoutKey !== layoutKey) {
    setPrevLayoutKey(layoutKey);
    try {
      const saved = localStorage.getItem(layoutKey);
      const loaded = saved ? validateLayouts(JSON.parse(saved), defaultLayout) : defaultLayout;
      setLayouts(loaded);
    } catch { setLayouts(defaultLayout); }
  }

  const onLayoutChange = (_, allLayouts) => {
    setLayouts(allLayouts);
    localStorage.setItem(layoutKey, JSON.stringify(allLayouts));
  };

  const resetLayout = () => {
    setLayouts(defaultLayout);
    localStorage.removeItem(layoutKey);
  };

  const loadData = async (silent = false) => {
    if (!silent) setLoading(true); else setRefreshing(true);
    try {
      if (isConsolidatedView) {
        const [summaryRes, statsRes, poRes] = await Promise.all([
          api.get('/dashboard/branch-summary'),
          api.get('/dashboard/stats'),
          api.get('/purchase-orders/unpaid-summary').catch(() => ({ data: null })),
        ]);
        setBranchSummary(summaryRes.data);
        setStats(statsRes.data);
        setPoSummary(poRes.data);
      } else {
        const [res, poRes] = await Promise.all([
          api.get('/dashboard/stats', { params: selectedBranchId !== 'all' ? { branch_id: selectedBranchId } : {} }),
          api.get('/purchase-orders/unpaid-summary').catch(() => ({ data: null })),
        ]);
        setStats(res.data);
        setPoSummary(poRes.data);
        setBranchSummary(null);
        // Fetch unclosed days for the selected branch
        if (selectedBranchId && selectedBranchId !== 'all') {
          try {
            const ucRes = await api.get('/daily-close/unclosed-days', { params: { branch_id: selectedBranchId } });
            const pastDays = (ucRes.data.unclosed_days || []).filter(d => d.date < new Date().toISOString().slice(0, 10));
            setUnclosedDays(pastDays.length > 0 ? { days: pastDays, last_close_date: ucRes.data.last_close_date } : null);
          } catch { setUnclosedDays(null); }
        } else {
          setUnclosedDays(null);
        }
      }
    } catch (err) { console.error('Dashboard load error', err); }
    setLoading(false);
    setRefreshing(false);
  };

  useEffect(() => { loadData(); }, [isConsolidatedView, selectedBranchId]); // eslint-disable-line

  // Check if company_info is missing (post-reset self-heal)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get('/settings/company-info-status');
        if (cancelled) return;
        setCompanyInfoMissing(!r.data?.has_company_info);
        setCompanyInfoSuggested(r.data?.suggested || null);
      } catch { /* silent — banner just won't show */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleRestoreCompanyInfo = async () => {
    setRestoringCompanyInfo(true);
    try {
      const r = await api.post('/settings/restore-company-info');
      if (r.data?.restored) {
        toast.success(`Company info restored: ${r.data.value?.name}`);
        setCompanyInfoMissing(false);
      } else {
        toast.info('Company info already set');
        setCompanyInfoMissing(false);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to restore company info');
    }
    setRestoringCompanyInfo(false);
  };

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-slate-400">
      <RefreshCw size={20} className="animate-spin mr-2" /> Loading dashboard...
    </div>
  );

  const today = stats?.today || new Date().toISOString().slice(0, 10);
  const dayOfWeek = stats?.day_of_week || '';

  // ─── OWNER CONSOLIDATED VIEW ─────────────────────────────────────────────
  if (isConsolidatedView && branchSummary) {
    const totals = branchSummary.totals || {};
    const branches = branchSummary.branches || [];

    const ownerWidgets = {
      'kpis': (
        <div className="p-4">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[
              { label: 'Total Sales Today', value: formatPHP(totals.today_revenue || 0), icon: DollarSign, color: { bg: 'bg-emerald-50', text: 'text-emerald-700' }, sub: `${branches.reduce((s, b) => s + (b.today_sales_count || 0), 0)} transactions` },
              { label: 'Cash Position', value: formatPHP(totals.total_cash || 0), icon: Wallet, color: { bg: 'bg-blue-50', text: 'text-blue-700' }, sub: 'Cashier + Safe' },
              { label: 'Outstanding AR', value: formatPHP(totals.total_receivables || 0), icon: Receipt, color: { bg: 'bg-amber-50', text: 'text-amber-700' }, sub: 'Unpaid credit' },
              { label: 'Low Stock', value: totals.low_stock_count || 0, icon: AlertTriangle, color: { bg: 'bg-red-50', text: (totals.low_stock_count || 0) > 0 ? 'text-red-600' : 'text-slate-400' }, sub: 'Below threshold' },
              { label: 'Audit Health', value: stats?.last_audit ? `${stats.last_audit.overall_score}/100` : 'N/A', icon: ShieldCheck, color: { bg: 'bg-violet-50', text: stats?.last_audit?.overall_score >= 80 ? 'text-emerald-600' : 'text-amber-600' }, sub: stats?.days_since_audit != null ? `${stats.days_since_audit}d ago` : 'No audit' },
            ].map(kpi => <KpiCard key={kpi.label} {...kpi} />)}
          </div>
        </div>
      ),
      'sales-trends': <SalesTrendsWidget branchId={null} onPeriodChange={setAnalyticsPeriod} />,
      'ap': <AccountsPayableWidget branchId={null} />,
      'branch-comparison': <BranchComparisonWidget period={analyticsPeriod} branchId={null} />,
      'pending-reviews': (
        <Card className="border-slate-200 h-full">
          <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><FileCheck size={14} className="text-amber-600" /> Pending Reviews</CardTitle></CardHeader>
          <CardContent><PendingReviewsWidget /></CardContent>
        </Card>
      ),
      'branches': (
        <div className="p-4">
          <h2 className="text-base font-semibold mb-3 flex items-center gap-2" style={{ fontFamily: 'Manrope' }}><BarChart3 size={16} className="text-[#1A4D2E]" /> Branch Performance Today</h2>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {branches.map(branch => <BranchCard key={branch.id} branch={branch} onSelect={switchBranch} />)}
          </div>
        </div>
      ),
      'payables': (
        <Card className="border-slate-200 h-full">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2"><CalendarClock size={14} className="text-red-600" /> Upcoming Payables</CardTitle>
          </CardHeader>
          <CardContent>
            {poSummary?.total_count > 0 ? (
              <div className="space-y-1.5 text-xs">
                {[...(poSummary.overdue || []), ...(poSummary.due_soon || [])].slice(0, 5).map(po => (
                  <div key={po.id || po.po_number} className="flex justify-between py-1.5 border-b border-slate-50 last:border-0">
                    <div><p className="font-semibold">{po.vendor}</p><button className="text-blue-600 font-mono hover:underline" onClick={() => openDetailModal(po.po_number, 'po')}>{po.po_number}</button></div>
                    <p className="font-bold text-red-700">{formatPHP(po.balance)}</p>
                  </div>
                ))}
                <button onClick={() => navigate('/pay-supplier')} className="text-[10px] text-[#1A4D2E] hover:underline">View all →</button>
              </div>
            ) : <p className="text-xs text-emerald-600">All payments up to date</p>}
          </CardContent>
        </Card>
      ),
      'alerts': (
        <Card className="border-slate-200 h-full">
          <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><AlertTriangle size={14} className="text-amber-500" /> Alerts</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {branches.filter(b => b.low_stock_count > 0).map(b => (
              <div key={b.id} className="flex items-center justify-between p-2 rounded bg-amber-50 text-xs">
                <span className="font-medium">{b.name}</span><Badge className="bg-amber-100 text-amber-700 text-[10px]">{b.low_stock_count} low stock</Badge>
              </div>
            ))}
            {!branches.some(b => b.low_stock_count > 0) && <p className="text-xs text-slate-400">No alerts</p>}
          </CardContent>
        </Card>
      ),
      'top-revenue': (
        <Card className="border-slate-200 h-full">
          <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><TrendingUp size={14} className="text-emerald-600" /> Top Revenue Today</CardTitle></CardHeader>
          <CardContent>
            {[...branches].sort((a, b) => (b.today_revenue || 0) - (a.today_revenue || 0)).slice(0, 8).map((b, i) => (
              <div key={b.id} className="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0 text-sm">
                <div className="flex items-center gap-2">
                  <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${i === 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{i + 1}</span>
                  <span className="text-xs">{b.name}</span>
                </div>
                <span className="font-semibold text-emerald-700 font-mono text-xs">{formatPHP(b.today_revenue || 0)}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      ),
      'internal-profit': <InternalProfitWidget />,
      'audit-pulse': <AuditPulseWidget branchId={null} />,
    };

    return (
      <div className="space-y-4 animate-fadeIn" data-testid="owner-dashboard">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Building2 size={20} className="text-[#1A4D2E]" />
              <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Owner Dashboard</h1>
            </div>
            <p className="text-sm text-slate-500 mt-0.5">{dayOfWeek}, {today} · {branches.length} branches</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={resetLayout} title="Reset layout">
              <RotateCcw size={13} className="mr-1" /> Reset Layout
            </Button>
            <Button variant="outline" size="sm" onClick={() => loadData(true)} disabled={refreshing}>
              <RefreshCw size={13} className={`mr-1.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh
            </Button>
          </div>
        </div>

        <div ref={gridRef}>
        {gridMounted && (
        <Responsive
          width={gridWidth}
          layouts={layouts}
          breakpoints={{ lg: 1200, md: 768 }}
          cols={{ lg: 12, md: 10 }}
          rowHeight={30}
          onLayoutChange={onLayoutChange}
          draggableHandle=".drag-handle"
          isResizable={true}
          resizeHandles={['se']}
          compactType="vertical"
          containerPadding={[0, 0]}
          margin={[12, 12]}
        >
          {Object.entries(ownerWidgets).map(([key, widget]) => (
            <div key={key} className="h-full">
              <GridWidget title={key === 'kpis' || key === 'branches' ? null : key.replace(/-/g, ' ')}>
                {widget}
              </GridWidget>
            </div>
          ))}
        </Responsive>
        )}
        </div>
      </div>
    );
  }

  // ─── BRANCH / SINGLE VIEW ────────────────────────────────────────────────
  const ar = stats?.ar_aging || {};
  const todayNetCash = stats?.today_net_cash || 0;
  const lastClose = stats?.last_close_date;
  const daysSinceClose = stats?.days_since_close;

  const branchWidgets = {
    'kpis': (
      <div className="p-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            { label: 'Total Sales', value: formatPHP(stats?.today_revenue), icon: DollarSign, color: { bg: 'bg-emerald-50', text: 'text-emerald-700' }, sub: `${stats?.today_sales_count || 0} txns` },
            { label: 'Cash Sales', value: formatPHP(stats?.today_cash_sales), icon: Banknote, color: { bg: 'bg-green-50', text: 'text-green-700' }, sub: stats?.today_digital_sales > 0 ? `+${formatPHP(stats?.today_digital_sales)} digital` : 'Cash received' },
            { label: 'New Credit', value: formatPHP(stats?.today_credit_sales), icon: CreditCard, color: { bg: 'bg-amber-50', text: 'text-amber-700' }, sub: 'AR created today' },
            { label: 'AR Collected', value: formatPHP(stats?.today_ar_collected), icon: Receipt, color: { bg: 'bg-blue-50', text: 'text-blue-700' }, sub: 'Payments received' },
            { label: 'Expenses', value: formatPHP(stats?.today_expenses), icon: ArrowDown, color: { bg: 'bg-red-50', text: 'text-red-600' }, sub: 'Paid out today' },
            { label: 'Net Cash Flow', value: (todayNetCash >= 0 ? '+' : '') + formatPHP(todayNetCash), icon: todayNetCash >= 0 ? TrendingUp : ArrowDownRight, color: { bg: todayNetCash >= 0 ? 'bg-emerald-50' : 'bg-red-50', text: todayNetCash >= 0 ? 'text-emerald-700' : 'text-red-600' }, sub: 'Cash + digital − out' },
          ].map(kpi => <KpiCard key={kpi.label} {...kpi} />)}
        </div>
      </div>
    ),
    'sales-trends': <SalesTrendsWidget branchId={selectedBranchId} onPeriodChange={setAnalyticsPeriod} />,
    'cash-position': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><Wallet size={15} className="text-blue-600" /> Cash Position</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {[
            { label: 'Operating Fund', value: stats?.cashier_balance || 0, color: 'text-blue-700', bg: 'bg-blue-50' },
            { label: 'Safe', value: stats?.safe_balance || 0, color: 'text-indigo-700', bg: 'bg-indigo-50' },
            { label: 'Total Cash', value: stats?.total_cash_position || 0, color: 'text-emerald-700', bg: 'bg-emerald-50', bold: true },
          ].map(({ label, value, color, bg, bold }) => (
            <div key={label} className={`flex justify-between items-center p-2.5 rounded-lg ${bg}`}>
              <span className="text-xs text-slate-600">{label}</span>
              <span className={`font-mono font-${bold ? 'bold' : 'semibold'} text-sm ${color}`}>{formatPHP(value)}</span>
            </div>
          ))}
          {stats?.inventory_value && (<>
            <Separator className="my-2" />
            <div className="grid grid-cols-2 gap-2">
              <div className="p-2 rounded-lg bg-slate-50"><p className="text-[10px] text-slate-400">Stock @ Capital</p><p className="text-sm font-bold font-mono">{formatPHP(stats.inventory_value.capital_value)}</p></div>
              <div className="p-2 rounded-lg bg-emerald-50"><p className="text-[10px] text-emerald-500">Stock @ Retail</p><p className="text-sm font-bold font-mono text-emerald-700">{formatPHP(stats.inventory_value.retail_value)}</p></div>
            </div>
          </>)}
        </CardContent>
      </Card>
    ),
    'ar-overview': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold flex items-center gap-2"><Receipt size={15} className="text-amber-600" /> Accounts Receivable</CardTitle>
            <div className="text-right"><p className="text-xl font-bold text-amber-700 font-mono">{formatPHP(ar.total || 0)}</p><p className="text-[10px] text-slate-400">Outstanding</p></div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <AgingBar aging={ar} />
            <div>
              <p className="text-xs font-semibold text-slate-400 uppercase mb-2">Top Debtors</p>
              {(stats?.top_debtors || []).length === 0 ? <p className="text-xs text-slate-400">No outstanding AR</p>
                : (stats?.top_debtors || []).map((d, i) => (
                  <div key={i} className="flex justify-between text-xs py-1.5 border-b border-slate-50 last:border-0">
                    <span className="font-medium truncate max-w-[110px]">{d.customer}</span>
                    <span className="font-mono font-semibold text-amber-700">{formatPHP(d.balance)}</span>
                  </div>
                ))}
            </div>
          </div>
        </CardContent>
      </Card>
    ),
    'ap': <AccountsPayableWidget branchId={selectedBranchId} />,
    'audit-pulse': <AuditPulseWidget branchId={selectedBranchId !== 'all' ? selectedBranchId : null} />,
    'pending-reviews': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><FileCheck size={14} className="text-amber-600" /> Receipts Awaiting Review</CardTitle></CardHeader>
        <CardContent><PendingReviewsWidget branchId={selectedBranchId !== 'all' ? selectedBranchId : undefined} compact={true} /></CardContent>
      </Card>
    ),
    'credits-today': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold flex items-center gap-2"><CreditCard size={15} className="text-amber-600" /> Credit Extended Today</CardTitle>
            <span className="text-xs font-semibold text-amber-700">{formatPHP(stats?.today_credit_sales || 0)}</span>
          </div>
        </CardHeader>
        <CardContent>
          {(stats?.credit_customers_today || []).length === 0 ? <p className="text-xs text-slate-400">No new credit today</p>
            : <div className="space-y-1.5">{(stats?.credit_customers_today || []).map((c, i) => (
              <div key={i} className="flex items-center justify-between text-xs bg-amber-50 rounded px-3 py-2">
                <div><p className="font-semibold">{c.customer_id ? <button onClick={() => openCustStmt(c.customer_id, c.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{c.customer_name}</button> : c.customer_name}</p><button className="text-blue-600 font-mono hover:underline" onClick={() => openDetailModal(c.invoice_number)}>{c.invoice_number}</button></div>
                <div className="text-right"><p className="font-bold text-amber-700">{formatPHP(c.amount)}</p><p className="text-slate-400">bal {formatPHP(c.balance)}</p></div>
              </div>
            ))}</div>
          }
        </CardContent>
      </Card>
    ),
    'ar-payments': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold flex items-center gap-2"><Banknote size={15} className="text-blue-600" /> AR Payments Today</CardTitle>
            <span className="text-xs font-semibold text-blue-700">{formatPHP(stats?.today_ar_collected || 0)}</span>
          </div>
        </CardHeader>
        <CardContent>
          {(stats?.recent_ar_payments || []).length === 0 ? <p className="text-xs text-slate-400">No AR payments today</p>
            : <div className="space-y-1.5">{(stats?.recent_ar_payments || []).map((p, i) => (
              <div key={i} className="flex items-center justify-between text-xs bg-blue-50 rounded px-3 py-2">
                <div><p className="font-semibold">{p.customer_id ? <button onClick={() => openCustStmt(p.customer_id, p.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{p.customer_name}</button> : p.customer_name}</p><button className="text-blue-600 font-mono hover:underline" onClick={() => openDetailModal(p.invoice_number)}>{p.invoice_number}</button></div>
                <p className="font-bold text-blue-700">{formatPHP(p.amount)}</p>
              </div>
            ))}</div>
          }
        </CardContent>
      </Card>
    ),
    'payables': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2"><CalendarClock size={14} className="text-red-600" /> Upcoming Payables</CardTitle>
        </CardHeader>
        <CardContent>
          {poSummary?.total_count > 0 ? (
            <div className="space-y-1.5 text-xs">
              {[...(poSummary.overdue || []), ...(poSummary.due_soon || [])].slice(0, 5).map(po => (
                <div key={po.id || po.po_number} className="flex justify-between py-1.5 border-b border-slate-50 last:border-0">
                  <div><p className="font-semibold">{po.vendor}</p><button className="text-blue-600 font-mono hover:underline" onClick={() => openDetailModal(po.po_number, 'po')}>{po.po_number}</button></div>
                  <p className="font-bold text-red-700">{formatPHP(po.balance)}</p>
                </div>
              ))}
              <button onClick={() => navigate('/pay-supplier')} className="text-[10px] text-[#1A4D2E] hover:underline">View all →</button>
            </div>
          ) : <p className="text-xs text-emerald-600">All payments up to date</p>}
        </CardContent>
      </Card>
    ),
    'top-products': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold flex items-center gap-2"><TrendingUp size={14} className="text-emerald-600" /> Top Products</CardTitle></CardHeader>
        <CardContent>
          {(stats?.top_products || []).length === 0 ? <p className="text-xs text-slate-400">No sales data</p>
            : (stats?.top_products || []).map((p, i) => (
              <div key={i} className="flex items-center justify-between text-xs py-1.5 border-b border-slate-50 last:border-0">
                <div className="flex items-center gap-1.5">
                  <span className="w-4 h-4 rounded-full bg-slate-100 flex items-center justify-center text-[9px] font-bold text-slate-500">{i + 1}</span>
                  <span className="truncate max-w-[100px]">{p.name}</span>
                </div>
                <div className="text-right"><p className="font-semibold text-emerald-700">{formatPHP(p.revenue)}</p><p className="text-slate-400">{p.quantity} sold</p></div>
              </div>
            ))}
        </CardContent>
      </Card>
    ),
    'recent-sales': (
      <Card className="border-slate-200 h-full">
        <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold">Recent Transactions</CardTitle></CardHeader>
        <CardContent>
          {(stats?.recent_sales || []).length === 0 ? <p className="text-xs text-slate-400">No recent sales</p>
            : (stats?.recent_sales || []).slice(0, 5).map(sale => (
              <div key={sale.id} className="py-1.5 border-b border-slate-50 last:border-0">
                <div className="flex justify-between items-start text-xs">
                  <div><p className="font-semibold font-mono"><button className="text-blue-600 hover:underline" onClick={() => openDetailModal(sale.invoice_number || sale.sale_number)}>{sale.invoice_number || sale.sale_number}</button></p><p className="text-slate-500">{sale.customer_id ? <button onClick={() => openCustStmt(sale.customer_id, sale.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{sale.customer_name || 'Walk-in'}</button> : (sale.customer_name || 'Walk-in')}</p></div>
                  <div className="text-right">
                    <p className="font-bold">{formatPHP(sale.grand_total || sale.total)}</p>
                    <Badge className={`text-[9px] ${sale.payment_type === 'cash' ? 'bg-emerald-100 text-emerald-700' : sale.payment_type === 'digital' ? 'bg-blue-100 text-blue-700' : 'bg-amber-100 text-amber-700'}`}>
                      {sale.payment_type || 'cash'}
                    </Badge>
                  </div>
                </div>
              </div>
            ))}
        </CardContent>
      </Card>
    ),
  };

  return (
    <div className="space-y-4 animate-fadeIn" data-testid="dashboard-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Dashboard</h1>
          <p className="text-sm text-slate-500 mt-0.5 flex items-center gap-2">
            <Calendar size={13} /> {dayOfWeek}, {today}
            {lastClose && <span className={`flex items-center gap-1 ${daysSinceClose > 0 ? 'text-amber-600' : 'text-emerald-600'}`}><Lock size={11} /> {daysSinceClose === 0 ? 'Closed today' : `Last close: ${lastClose}`}</span>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={resetLayout} title="Reset layout"><RotateCcw size={13} className="mr-1" /> Reset Layout</Button>
          <Button variant="outline" size="sm" onClick={() => loadData(true)} disabled={refreshing}><RefreshCw size={13} className={`mr-1.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh</Button>
          <Button size="sm" onClick={() => navigate('/close-wizard')} className="bg-[#1A4D2E] text-white"><Lock size={13} className="mr-1.5" /> Close Wizard</Button>
        </div>
      </div>

      {/* Self-heal banner: company_info missing (typically post-Reset) */}
      {companyInfoMissing && (
        <div className="flex items-center justify-between px-4 py-3 rounded-lg border border-emerald-300 bg-emerald-50" data-testid="dashboard-restore-company-info">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-emerald-100 flex items-center justify-center flex-shrink-0">
              <Building2 size={18} className="text-emerald-700" />
            </div>
            <div>
              <p className="text-sm font-semibold text-emerald-900">
                Your company info is missing
              </p>
              <p className="text-xs text-emerald-700">
                {companyInfoSuggested?.name
                  ? <>Restore <b>{companyInfoSuggested.name}</b> as your business name so SMS signatures &amp; receipts read correctly.</>
                  : 'Restore your business name so SMS signatures and receipts read correctly.'}
              </p>
            </div>
          </div>
          <Button
            size="sm"
            onClick={handleRestoreCompanyInfo}
            disabled={restoringCompanyInfo}
            className="bg-emerald-700 hover:bg-emerald-800 text-white flex-shrink-0"
            data-testid="dashboard-restore-company-info-btn">
            {restoringCompanyInfo ? <RefreshCw size={13} className="mr-1.5 animate-spin" /> : <Building2 size={13} className="mr-1.5" />}
            {restoringCompanyInfo ? 'Restoring...' : 'Restore Company Info'}
          </Button>
        </div>
      )}

      {/* Unclosed Days Warning */}
      {unclosedDays && unclosedDays.days.length > 0 && (
        <div className="flex items-center justify-between px-4 py-3 rounded-lg border border-amber-300 bg-amber-50" data-testid="dashboard-unclosed-warning">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
              <AlertTriangle size={18} className="text-amber-600" />
            </div>
            <div>
              <p className="text-sm font-semibold text-amber-800">
                {unclosedDays.days.length} unclosed day{unclosedDays.days.length > 1 ? 's' : ''} need attention
              </p>
              <p className="text-xs text-amber-600">
                {unclosedDays.days[0].date} to {unclosedDays.days[unclosedDays.days.length - 1].date}
                {unclosedDays.last_close_date ? ` · Last closed: ${unclosedDays.last_close_date}` : ' · No previous close on record'}
              </p>
            </div>
          </div>
          <Button size="sm" onClick={() => navigate('/close-wizard')} className="bg-amber-600 hover:bg-amber-700 text-white flex-shrink-0" data-testid="dashboard-go-close-wizard">
            <Lock size={13} className="mr-1.5" /> Close Wizard
          </Button>
        </div>
      )}

      <div ref={gridRef}>
      {gridMounted && (
      <Responsive
        width={gridWidth}
        layouts={layouts}
        breakpoints={{ lg: 1200, md: 768 }}
        cols={{ lg: 12, md: 10 }}
        rowHeight={30}
        onLayoutChange={onLayoutChange}
        draggableHandle=".drag-handle"
        isResizable={true}
        resizeHandles={['se']}
        compactType="vertical"
        containerPadding={[0, 0]}
        margin={[12, 12]}
      >
        {Object.entries(branchWidgets).map(([key, widget]) => (
          <div key={key} className="h-full">
            <GridWidget title={key === 'kpis' ? null : key.replace(/-/g, ' ')}>
              {widget}
            </GridWidget>
          </div>
        ))}
      </Responsive>
      )}
      </div>
      <ReviewDetailDialog
        open={invoiceModalOpen && detailType === 'po'}
        onOpenChange={(open) => { if (!open) setInvoiceModalOpen(false); }}
        poNumber={selectedInvoiceNumber}
        showReviewAction={false}
        showPayAction={false}
      />
      <InvoiceDetailModal compact
        open={invoiceModalOpen && detailType === 'sale'}
        onOpenChange={(open) => { if (!open) setInvoiceModalOpen(false); }}
        invoiceNumber={selectedInvoiceNumber}
      />
      <CustomerStatementModal
        open={stmtOpen}
        onOpenChange={setStmtOpen}
        customer={stmtCustomer}
      />
    </div>
  );
}
