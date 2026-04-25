import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../components/ui/collapsible';
import {
  BarChart3, ChevronDown, ChevronRight, Printer,
  TrendingUp, AlertCircle, DollarSign, Calendar, RefreshCw, Filter, UserCheck, AlertTriangle, Percent, TrendingDown
} from 'lucide-react';
import { toast } from 'sonner';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

function KpiCard({ label, value, sub, accent = 'slate' }) {
  const colors = {
    slate: 'bg-slate-50 border-slate-200 text-slate-700',
    red: 'bg-red-50 border-red-200 text-red-700',
    amber: 'bg-amber-50 border-amber-200 text-amber-700',
    emerald: 'bg-emerald-50 border-emerald-200 text-emerald-700',
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
  };
  return (
    <Card className={`border ${colors[accent]}`}>
      <CardContent className="p-4">
        <p className="text-xs font-medium text-current opacity-70 mb-1">{label}</p>
        <p className="text-2xl font-bold font-mono">{value}</p>
        {sub && <p className="text-xs mt-1 opacity-60">{sub}</p>}
      </CardContent>
    </Card>
  );
}

function PrintButton({ onClick }) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} data-testid="report-print-btn">
      <Printer size={14} className="mr-1.5" /> Print
    </Button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  AR AGING TAB
// ─────────────────────────────────────────────────────────────────────────────
function ArAgingReport({ branches, selectedBranchId, canExport }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [branchFilter, setBranchFilter] = useState(selectedBranchId || 'all');
  const [expandedRows, setExpandedRows] = useState({});
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const { canViewAllBranches } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (branchFilter && branchFilter !== 'all') params.set('branch_id', branchFilter);
      const res = await api.get(`${BACKEND_URL}/api/reports/ar-aging?${params}`);
      setData(res.data);
    } catch (e) {
      toast.error('Failed to load AR aging data');
    } finally {
      setLoading(false);
    }
  }, [branchFilter]);

  useEffect(() => { load(); }, [load]);

  const handlePrint = () => {
    const win = window.open('', '_blank');
    const totals = data?.totals || {};
    const rows = data?.rows || [];
    win.document.write(`
      <html><head><title>AR Aging Report</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 12px; padding: 20px; }
        h2 { color: #1A4D2E; margin-bottom: 4px; }
        .sub { color: #666; margin-bottom: 16px; font-size: 11px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th { background: #1A4D2E; color: white; padding: 6px 10px; text-align: left; font-size: 11px; }
        td { padding: 5px 10px; border-bottom: 1px solid #e5e7eb; font-size: 11px; }
        tr:nth-child(even) td { background: #f9fafb; }
        .num { text-align: right; font-family: monospace; }
        .total-row td { font-weight: bold; background: #f1f5f9; border-top: 2px solid #94a3b8; }
        .badge-current { color: #15803d; } .badge-31 { color: #b45309; } .badge-61 { color: #c2410c; } .badge-90 { color: #b91c1c; }
      </style></head><body>
      <h2>AgriBooks — AR Aging Report</h2>
      <div class="sub">As of ${data?.as_of_date || ''}</div>
      <table>
        <thead><tr>
          <th>Customer</th>
          <th class="num">Current (0–30d)</th>
          <th class="num">31–60 days</th>
          <th class="num">61–90 days</th>
          <th class="num">90+ days</th>
          <th class="num">Total</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `<tr>
            <td>${r.customer_name}</td>
            <td class="num">${r.current > 0 ? '₱' + r.current.toLocaleString('en-PH', {minimumFractionDigits:2}) : '—'}</td>
            <td class="num">${r.b31_60 > 0 ? '₱' + r.b31_60.toLocaleString('en-PH', {minimumFractionDigits:2}) : '—'}</td>
            <td class="num">${r.b61_90 > 0 ? '₱' + r.b61_90.toLocaleString('en-PH', {minimumFractionDigits:2}) : '—'}</td>
            <td class="num">${r.b90plus > 0 ? '₱' + r.b90plus.toLocaleString('en-PH', {minimumFractionDigits:2}) : '—'}</td>
            <td class="num">₱${r.total.toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
          </tr>`).join('')}
          <tr class="total-row">
            <td>TOTAL</td>
            <td class="num">₱${(totals.current||0).toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
            <td class="num">₱${(totals.b31_60||0).toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
            <td class="num">₱${(totals.b61_90||0).toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
            <td class="num">₱${(totals.b90plus||0).toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
            <td class="num">₱${(totals.total||0).toLocaleString('en-PH', {minimumFractionDigits:2})}</td>
          </tr>
        </tbody>
      </table>
      </body></html>
    `);
    win.document.close();
    win.print();
  };

  const toggleRow = (cid) => setExpandedRows(p => ({ ...p, [cid]: !p[cid] }));

  const bucketLabel = {
    current: { label: '0–30 days', color: 'text-emerald-600' },
    b31_60: { label: '31–60 days', color: 'text-amber-600' },
    b61_90: { label: '61–90 days', color: 'text-orange-600' },
    b90plus: { label: '90+ days', color: 'text-red-600' },
  };

  const totals = data?.totals || {};

  return (
    <div className="space-y-4" data-testid="ar-aging-tab">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        {canViewAllBranches && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="w-44 h-8 text-xs" data-testid="ar-branch-filter">
              <SelectValue placeholder="All Branches" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Branches</SelectItem>
              {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button size="sm" variant="outline" onClick={load} disabled={loading} data-testid="ar-refresh-btn">
          <RefreshCw size={13} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
        <div className="ml-auto">
          {canExport && <PrintButton onClick={handlePrint} />}
        </div>
      </div>

      {/* KPI Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Current (0–30d)" value={formatPHP(totals.current || 0)} accent="emerald" />
        <KpiCard label="31–60 days" value={formatPHP(totals.b31_60 || 0)} accent="amber" />
        <KpiCard label="61–90 days" value={formatPHP(totals.b61_90 || 0)} accent="amber" />
        <KpiCard label="90+ days (Critical)" value={formatPHP(totals.b90plus || 0)} accent="red" />
      </div>
      <KpiCard label="Total Outstanding AR" value={formatPHP(totals.total || 0)} accent="blue"
        sub={`As of ${data?.as_of_date || '—'} · ${data?.rows?.length || 0} customers`} />

      {/* Table */}
      <Card>
        <CardHeader className="py-3 px-4 bg-slate-50 border-b">
          <CardTitle className="text-sm font-semibold text-slate-700">Customer Aging Detail</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table data-testid="ar-aging-table">
            <TableHeader>
              <TableRow className="bg-slate-50">
                <TableHead className="w-8"></TableHead>
                <TableHead>Customer</TableHead>
                <TableHead className="text-right text-emerald-700">0–30d</TableHead>
                <TableHead className="text-right text-amber-700">31–60d</TableHead>
                <TableHead className="text-right text-orange-700">61–90d</TableHead>
                <TableHead className="text-right text-red-700">90+d</TableHead>
                <TableHead className="text-right font-bold">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.rows || []).map(row => (
                <>
                  <TableRow
                    key={row.customer_id}
                    className="cursor-pointer hover:bg-slate-50"
                    onClick={() => toggleRow(row.customer_id)}
                  >
                    <TableCell className="w-8 text-slate-400">
                      {expandedRows[row.customer_id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </TableCell>
                    <TableCell className="font-medium">
                      <button onClick={(e) => { e.stopPropagation(); setStmtCustomer({ id: row.customer_id, name: row.customer_name }); setStmtOpen(true); }} className="hover:text-[#1A4D2E] hover:underline text-left">
                        {row.customer_name}
                      </button>
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      {row.current > 0 ? <span className="text-emerald-600">{formatPHP(row.current)}</span> : <span className="text-slate-300">—</span>}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      {row.b31_60 > 0 ? <span className="text-amber-600">{formatPHP(row.b31_60)}</span> : <span className="text-slate-300">—</span>}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      {row.b61_90 > 0 ? <span className="text-orange-600">{formatPHP(row.b61_90)}</span> : <span className="text-slate-300">—</span>}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      {row.b90plus > 0 ? <span className="text-red-600 font-semibold">{formatPHP(row.b90plus)}</span> : <span className="text-slate-300">—</span>}
                    </TableCell>
                    <TableCell className="text-right font-bold font-mono">{formatPHP(row.total)}</TableCell>
                  </TableRow>
                  {expandedRows[row.customer_id] && (row.invoices || []).map(inv => (
                    <TableRow key={inv.invoice_number} className="bg-slate-50/50 text-xs">
                      <TableCell />
                      <TableCell className="pl-8 text-slate-500">
                        <button className="text-blue-600 hover:underline" onClick={() => { setSelectedInvoiceNumber(inv.invoice_number); setInvoiceModalOpen(true); }}>{inv.invoice_number}</button>
                        <span className="ml-2 text-slate-400">{inv.invoice_date}</span>
                        <span className={`ml-2 font-medium ${bucketLabel[inv.bucket]?.color}`}>
                          {inv.days_old}d old
                        </span>
                      </TableCell>
                      <TableCell colSpan={4} className="text-slate-500">
                        Grand: {formatPHP(inv.grand_total)} · Paid: {formatPHP(inv.amount_paid)} · Due: {inv.due_date}
                      </TableCell>
                      <TableCell className="text-right font-mono font-medium text-slate-700">{formatPHP(inv.balance)}</TableCell>
                    </TableRow>
                  ))}
                </>
              ))}
              {/* Totals row */}
              {data?.rows?.length > 0 && (
                <TableRow className="bg-slate-100 font-bold border-t-2 border-slate-300">
                  <TableCell />
                  <TableCell>TOTAL</TableCell>
                  <TableCell className="text-right font-mono text-emerald-700">{formatPHP(totals.current || 0)}</TableCell>
                  <TableCell className="text-right font-mono text-amber-700">{formatPHP(totals.b31_60 || 0)}</TableCell>
                  <TableCell className="text-right font-mono text-orange-700">{formatPHP(totals.b61_90 || 0)}</TableCell>
                  <TableCell className="text-right font-mono text-red-700">{formatPHP(totals.b90plus || 0)}</TableCell>
                  <TableCell className="text-right font-mono">{formatPHP(totals.total || 0)}</TableCell>
                </TableRow>
              )}
              {!loading && data?.rows?.length === 0 && (
                <TableRow><TableCell colSpan={7} className="text-center text-slate-400 py-10">No outstanding receivables found.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <InvoiceDetailModal compact open={invoiceModalOpen} onOpenChange={setInvoiceModalOpen} invoiceNumber={selectedInvoiceNumber} />
      <CustomerStatementModal open={stmtOpen} onOpenChange={setStmtOpen} customer={stmtCustomer} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  SALES REPORT TAB
// ─────────────────────────────────────────────────────────────────────────────
function SalesReport({ branches, selectedBranchId, canExport }) {
  const today = new Date();
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().slice(0, 10);
  const todayStr = today.toISOString().slice(0, 10);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(todayStr);
  const [branchFilter, setBranchFilter] = useState(selectedBranchId || 'all');
  const [view, setView] = useState('summary'); // summary | transactions
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const { canViewAllBranches } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (branchFilter && branchFilter !== 'all') params.set('branch_id', branchFilter);
      const res = await api.get(`${BACKEND_URL}/api/reports/sales?${params}`);
      setData(res.data);
    } catch (e) {
      toast.error('Failed to load sales data');
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, branchFilter]);

  useEffect(() => { load(); }, [load]);

  const handlePrint = () => {
    const win = window.open('', '_blank');
    const cats = data?.categories || [];
    win.document.write(`
      <html><head><title>Sales Report</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 12px; padding: 20px; }
        h2 { color: #1A4D2E; margin-bottom: 4px; }
        .sub { color: #666; margin-bottom: 16px; font-size: 11px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th { background: #1A4D2E; color: white; padding: 6px 10px; text-align: left; font-size: 11px; }
        td { padding: 5px 10px; border-bottom: 1px solid #e5e7eb; font-size: 11px; }
        tr:nth-child(even) td { background: #f9fafb; }
        .num { text-align: right; font-family: monospace; }
        .total-row td { font-weight: bold; background: #f1f5f9; border-top: 2px solid #94a3b8; }
      </style></head><body>
      <h2>AgriBooks — Sales Report</h2>
      <div class="sub">Period: ${data?.date_from || ''} to ${data?.date_to || ''} &nbsp;|&nbsp; Total: ₱${(data?.grand_total||0).toLocaleString('en-PH',{minimumFractionDigits:2})}</div>
      <h3 style="font-size:12px;margin-bottom:6px;">Sales by Category</h3>
      <table>
        <thead><tr><th>Category</th><th class="num">Qty</th><th class="num">Amount</th><th class="num">% of Total</th></tr></thead>
        <tbody>
          ${cats.map(c => `<tr>
            <td>${c.category}</td>
            <td class="num">${c.qty}</td>
            <td class="num">₱${c.total.toLocaleString('en-PH',{minimumFractionDigits:2})}</td>
            <td class="num">${data?.grand_total > 0 ? ((c.total/data.grand_total)*100).toFixed(1)+'%' : '—'}</td>
          </tr>`).join('')}
          <tr class="total-row">
            <td>TOTAL</td>
            <td class="num">${cats.reduce((s,c)=>s+c.qty,0)}</td>
            <td class="num">₱${(data?.grand_total||0).toLocaleString('en-PH',{minimumFractionDigits:2})}</td>
            <td class="num">100%</td>
          </tr>
        </tbody>
      </table>
      </body></html>
    `);
    win.document.close();
    win.print();
  };

  const paymentTotals = data?.payment_totals || {};
  const cashTotal = paymentTotals['cash'] || 0;
  const creditTotal = paymentTotals['credit'] || 0;

  return (
    <div className="space-y-4" data-testid="sales-report-tab">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Calendar size={14} className="text-slate-400" />
          <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="h-8 text-xs w-36" data-testid="sales-date-from" />
          <span className="text-slate-400 text-xs">to</span>
          <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="h-8 text-xs w-36" data-testid="sales-date-to" />
        </div>
        {canViewAllBranches && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="w-40 h-8 text-xs" data-testid="sales-branch-filter">
              <SelectValue placeholder="All Branches" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Branches</SelectItem>
              {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button size="sm" onClick={load} disabled={loading} className="bg-[#1A4D2E] hover:bg-[#15402A] text-white" data-testid="sales-run-btn">
          <Filter size={13} className="mr-1.5" /> Run Report
        </Button>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw size={13} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant={view === 'summary' ? 'default' : 'outline'} onClick={() => setView('summary')} data-testid="sales-summary-view">Summary</Button>
          <Button size="sm" variant={view === 'transactions' ? 'default' : 'outline'} onClick={() => setView('transactions')} data-testid="sales-transactions-view">Transactions</Button>
          {canExport && <PrintButton onClick={handlePrint} />}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Total Sales" value={formatPHP(data?.grand_total || 0)} accent="emerald"
          sub={`${data?.date_from} → ${data?.date_to}`} />
        <KpiCard label="Walk-in (Cash)" value={formatPHP(cashTotal)} accent="blue" />
        <KpiCard label="Credit Sales" value={formatPHP(creditTotal)} accent="amber" />
        <KpiCard label="Categories Sold" value={data?.categories?.length || 0} accent="slate"
          sub={`${data?.transactions?.length || 0} transactions`} />
      </div>

      {view === 'summary' && (
        <>
          {/* Category Breakdown */}
          <Card>
            <CardHeader className="py-3 px-4 bg-slate-50 border-b">
              <CardTitle className="text-sm font-semibold text-slate-700">Sales by Category</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table data-testid="sales-category-table">
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Qty</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead className="text-right">% of Total</TableHead>
                    <TableHead className="text-right">Cash</TableHead>
                    <TableHead className="text-right">Credit</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data?.categories || []).map(cat => (
                    <TableRow key={cat.category}>
                      <TableCell className="font-medium">{cat.category}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{cat.qty}</TableCell>
                      <TableCell className="text-right font-mono text-sm font-semibold">{formatPHP(cat.total)}</TableCell>
                      <TableCell className="text-right text-sm text-slate-500">
                        {data?.grand_total > 0 ? ((cat.total / data.grand_total) * 100).toFixed(1) + '%' : '—'}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-emerald-600">
                        {cat.by_payment?.cash ? formatPHP(cat.by_payment.cash) : '—'}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-amber-600">
                        {cat.by_payment?.credit ? formatPHP(cat.by_payment.credit) : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                  {(data?.categories?.length || 0) > 0 && (
                    <TableRow className="bg-slate-100 font-bold border-t-2 border-slate-300">
                      <TableCell>TOTAL</TableCell>
                      <TableCell className="text-right font-mono">{(data?.categories || []).reduce((s, c) => s + c.qty, 0)}</TableCell>
                      <TableCell className="text-right font-mono">{formatPHP(data?.grand_total || 0)}</TableCell>
                      <TableCell className="text-right">100%</TableCell>
                      <TableCell className="text-right font-mono text-emerald-700">{formatPHP(cashTotal)}</TableCell>
                      <TableCell className="text-right font-mono text-amber-700">{formatPHP(creditTotal)}</TableCell>
                    </TableRow>
                  )}
                  {!loading && (data?.categories?.length || 0) === 0 && (
                    <TableRow><TableCell colSpan={6} className="text-center text-slate-400 py-10">No sales data found for the selected period.</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* Daily Breakdown */}
          {(data?.daily?.length || 0) > 0 && (
            <Card>
              <CardHeader className="py-3 px-4 bg-slate-50 border-b">
                <CardTitle className="text-sm font-semibold text-slate-700">Daily Sales</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50">
                      <TableHead>Date</TableHead>
                      <TableHead className="text-right">Transactions</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(data?.daily || []).map(d => (
                      <TableRow key={d.date}>
                        <TableCell>{d.date}</TableCell>
                        <TableCell className="text-right text-sm">{d.count}</TableCell>
                        <TableCell className="text-right font-mono text-sm font-semibold">{formatPHP(d.total)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {view === 'transactions' && (
        <Card>
          <CardHeader className="py-3 px-4 bg-slate-50 border-b">
            <CardTitle className="text-sm font-semibold text-slate-700">
              Transactions ({data?.transactions?.length || 0})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table data-testid="sales-transactions-table">
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead>Invoice #</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Customer</TableHead>
                  <TableHead>Cashier</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Paid</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.transactions || []).map(t => (
                  <TableRow key={t.invoice_number}>
                    <TableCell><button className="font-mono text-xs text-blue-600 hover:underline" onClick={() => { setSelectedInvoiceNumber(t.invoice_number); setInvoiceModalOpen(true); }}>{t.invoice_number}</button></TableCell>
                    <TableCell className="text-sm">{t.date}</TableCell>
                    <TableCell className="text-sm">{t.customer_id ? <button onClick={() => { setStmtCustomer({ id: t.customer_id, name: t.customer_name }); setStmtOpen(true); }} className="hover:text-[#1A4D2E] hover:underline">{t.customer_name}</button> : t.customer_name}</TableCell>
                    <TableCell className="text-sm text-slate-500">{t.cashier_name}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={t.payment_type === 'cash' ? 'border-emerald-200 text-emerald-700' : 'border-amber-200 text-amber-700'}>
                        {t.payment_type === 'cash' ? 'Walk-in' : t.payment_type === 'credit' ? 'Credit' : 'Partial'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">{formatPHP(t.grand_total)}</TableCell>
                    <TableCell className="text-right font-mono text-sm">{formatPHP(t.amount_paid)}</TableCell>
                    <TableCell className="text-right font-mono text-sm text-red-600">{t.balance > 0 ? formatPHP(t.balance) : '—'}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={
                        t.status === 'paid' ? 'border-emerald-200 text-emerald-700' :
                        t.status === 'partial' ? 'border-blue-200 text-blue-700' :
                        'border-amber-200 text-amber-700'
                      }>{t.status}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {!loading && (data?.transactions?.length || 0) === 0 && (
                  <TableRow><TableCell colSpan={9} className="text-center text-slate-400 py-10">No transactions found.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <InvoiceDetailModal compact open={invoiceModalOpen} onOpenChange={setInvoiceModalOpen} invoiceNumber={selectedInvoiceNumber} />
      <CustomerStatementModal open={stmtOpen} onOpenChange={setStmtOpen} customer={stmtCustomer} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  EXPENSE REPORT TAB
// ─────────────────────────────────────────────────────────────────────────────
function ExpenseReport({ branches, selectedBranchId, canExport }) {
  const today = new Date();
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().slice(0, 10);
  const todayStr = today.toISOString().slice(0, 10);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(todayStr);
  const [branchFilter, setBranchFilter] = useState(selectedBranchId || 'all');
  const [catFilter, setCatFilter] = useState('all');
  const [view, setView] = useState('summary');
  const { canViewAllBranches } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (branchFilter && branchFilter !== 'all') params.set('branch_id', branchFilter);
      if (catFilter && catFilter !== 'all') params.set('category', catFilter);
      const res = await api.get(`${BACKEND_URL}/api/reports/expenses?${params}`);
      setData(res.data);
    } catch (e) {
      toast.error('Failed to load expense data');
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, branchFilter, catFilter]);

  useEffect(() => { load(); }, [load]);

  const handlePrint = () => {
    const win = window.open('', '_blank');
    const cats = data?.categories || [];
    win.document.write(`
      <html><head><title>Expense Report</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 12px; padding: 20px; }
        h2 { color: #1A4D2E; margin-bottom: 4px; }
        .sub { color: #666; margin-bottom: 16px; font-size: 11px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th { background: #1A4D2E; color: white; padding: 6px 10px; text-align: left; font-size: 11px; }
        td { padding: 5px 10px; border-bottom: 1px solid #e5e7eb; font-size: 11px; }
        tr:nth-child(even) td { background: #f9fafb; }
        .num { text-align: right; font-family: monospace; }
        .total-row td { font-weight: bold; background: #f1f5f9; border-top: 2px solid #94a3b8; }
      </style></head><body>
      <h2>AgriBooks — Expense Report</h2>
      <div class="sub">Period: ${data?.date_from||''} to ${data?.date_to||''} &nbsp;|&nbsp; Total: ₱${(data?.grand_total||0).toLocaleString('en-PH',{minimumFractionDigits:2})}</div>
      <h3 style="font-size:12px;margin-bottom:6px;">Expenses by Category</h3>
      <table>
        <thead><tr><th>Category</th><th class="num">Count</th><th class="num">Amount</th><th class="num">% of Total</th></tr></thead>
        <tbody>
          ${cats.map(c => `<tr>
            <td>${c.category}</td>
            <td class="num">${c.count}</td>
            <td class="num">₱${c.total.toLocaleString('en-PH',{minimumFractionDigits:2})}</td>
            <td class="num">${data?.grand_total > 0 ? ((c.total/data.grand_total)*100).toFixed(1)+'%' : '—'}</td>
          </tr>`).join('')}
          <tr class="total-row">
            <td>TOTAL</td>
            <td class="num">${cats.reduce((s,c)=>s+c.count,0)}</td>
            <td class="num">₱${(data?.grand_total||0).toLocaleString('en-PH',{minimumFractionDigits:2})}</td>
            <td class="num">100%</td>
          </tr>
        </tbody>
      </table>
      <h3 style="font-size:12px;margin-bottom:6px;">Transaction Detail</h3>
      <table>
        <thead><tr><th>Date</th><th>Category</th><th>Description</th><th>Recorded By</th><th class="num">Amount</th></tr></thead>
        <tbody>
          ${(data?.expenses||[]).map(e => `<tr>
            <td>${e.date}</td><td>${e.category}</td>
            <td>${e.description||'—'}</td>
            <td>${e.created_by_name||'—'}</td>
            <td class="num">₱${(e.amount||0).toLocaleString('en-PH',{minimumFractionDigits:2})}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      </body></html>
    `);
    win.document.close();
    win.print();
  };

  const topCat = data?.categories?.[0];

  return (
    <div className="space-y-4" data-testid="expense-report-tab">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Calendar size={14} className="text-slate-400" />
          <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="h-8 text-xs w-36" data-testid="expense-date-from" />
          <span className="text-slate-400 text-xs">to</span>
          <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="h-8 text-xs w-36" data-testid="expense-date-to" />
        </div>
        {canViewAllBranches && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="w-40 h-8 text-xs" data-testid="expense-branch-filter">
              <SelectValue placeholder="All Branches" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Branches</SelectItem>
              {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Select value={catFilter} onValueChange={setCatFilter}>
          <SelectTrigger className="w-44 h-8 text-xs" data-testid="expense-cat-filter">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {(data?.categories || []).map(c => <SelectItem key={c.category} value={c.category}>{c.category}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button size="sm" onClick={load} disabled={loading} className="bg-[#1A4D2E] hover:bg-[#15402A] text-white" data-testid="expense-run-btn">
          <Filter size={13} className="mr-1.5" /> Run Report
        </Button>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw size={13} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant={view === 'summary' ? 'default' : 'outline'} onClick={() => setView('summary')} data-testid="expense-summary-view">Summary</Button>
          <Button size="sm" variant={view === 'detail' ? 'default' : 'outline'} onClick={() => setView('detail')} data-testid="expense-detail-view">Detail</Button>
          {canExport && <PrintButton onClick={handlePrint} />}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Total Expenses" value={formatPHP(data?.grand_total || 0)} accent="red"
          sub={`${data?.date_from} → ${data?.date_to}`} />
        <KpiCard label="Transactions" value={data?.expenses?.length || 0} accent="slate" />
        <KpiCard label="Categories" value={data?.categories?.length || 0} accent="slate" />
        <KpiCard label="Largest Category"
          value={topCat ? topCat.category : '—'}
          sub={topCat ? formatPHP(topCat.total) : ''}
          accent="amber" />
      </div>

      {view === 'summary' && (
        <>
          {/* Category Breakdown */}
          <Card>
            <CardHeader className="py-3 px-4 bg-slate-50 border-b">
              <CardTitle className="text-sm font-semibold text-slate-700">Expenses by Category</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table data-testid="expense-category-table">
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Count</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead className="text-right">% of Total</TableHead>
                    <TableHead className="w-32">Share</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data?.categories || []).map(cat => {
                    const pct = data?.grand_total > 0 ? (cat.total / data.grand_total) * 100 : 0;
                    return (
                      <TableRow key={cat.category}>
                        <TableCell className="font-medium">{cat.category}</TableCell>
                        <TableCell className="text-right text-sm">{cat.count}</TableCell>
                        <TableCell className="text-right font-mono text-sm font-semibold">{formatPHP(cat.total)}</TableCell>
                        <TableCell className="text-right text-sm text-slate-500">{pct.toFixed(1)}%</TableCell>
                        <TableCell>
                          <div className="w-full bg-slate-100 rounded-full h-1.5">
                            <div className="bg-[#1A4D2E] h-1.5 rounded-full" style={{ width: `${pct}%` }} />
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {(data?.categories?.length || 0) > 0 && (
                    <TableRow className="bg-slate-100 font-bold border-t-2 border-slate-300">
                      <TableCell>TOTAL</TableCell>
                      <TableCell className="text-right font-mono">{(data?.expenses || []).length}</TableCell>
                      <TableCell className="text-right font-mono">{formatPHP(data?.grand_total || 0)}</TableCell>
                      <TableCell className="text-right">100%</TableCell>
                      <TableCell />
                    </TableRow>
                  )}
                  {!loading && (data?.categories?.length || 0) === 0 && (
                    <TableRow><TableCell colSpan={5} className="text-center text-slate-400 py-10">No expenses found for the selected period.</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* Daily Breakdown */}
          {(data?.daily?.length || 0) > 0 && (
            <Card>
              <CardHeader className="py-3 px-4 bg-slate-50 border-b">
                <CardTitle className="text-sm font-semibold text-slate-700">Daily Expenses</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50">
                      <TableHead>Date</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(data?.daily || []).map(d => (
                      <TableRow key={d.date}>
                        <TableCell>{d.date}</TableCell>
                        <TableCell className="text-right text-sm">{d.count}</TableCell>
                        <TableCell className="text-right font-mono text-sm font-semibold">{formatPHP(d.total)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {view === 'detail' && (
        <Card>
          <CardHeader className="py-3 px-4 bg-slate-50 border-b">
            <CardTitle className="text-sm font-semibold text-slate-700">
              Expense Transactions ({data?.expenses?.length || 0})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table data-testid="expense-detail-table">
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead>Date</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Employee</TableHead>
                  <TableHead>Recorded By</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.expenses || []).map((exp, i) => (
                  <TableRow key={exp.id || i}>
                    <TableCell className="text-sm">{exp.date}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">{exp.category}</Badge>
                    </TableCell>
                    <TableCell className="text-sm max-w-xs truncate">{exp.description || '—'}</TableCell>
                    <TableCell className="text-sm text-slate-500">{exp.employee_name || '—'}</TableCell>
                    <TableCell className="text-sm text-slate-500">{exp.created_by_name || '—'}</TableCell>
                    <TableCell className="text-right font-mono text-sm font-semibold">{formatPHP(exp.amount)}</TableCell>
                  </TableRow>
                ))}
                {!loading && (data?.expenses?.length || 0) === 0 && (
                  <TableRow><TableCell colSpan={6} className="text-center text-slate-400 py-10">No expenses found.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  CA SUMMARY REPORT TAB
// ─────────────────────────────────────────────────────────────────────────────
function CaSummaryReport({ branches, selectedBranchId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [branchFilter, setBranchFilter] = useState(selectedBranchId || 'all');
  const [monthFilter, setMonthFilter] = useState(new Date().toISOString().slice(0, 7));
  const { canViewAllBranches } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (branchFilter && branchFilter !== 'all') params.set('branch_id', branchFilter);
      if (monthFilter) params.set('month', monthFilter);
      const res = await api.get(`${BACKEND_URL}/api/employees/ca-report?${params}`);
      setData(res.data);
    } catch {
      toast.error('Failed to load CA report');
    } finally {
      setLoading(false);
    }
  }, [branchFilter, monthFilter]);

  useEffect(() => { load(); }, [load]);

  const getBranchName = (bid) => (branches || []).find(b => b.id === bid)?.name || bid || '—';

  return (
    <div className="space-y-4" data-testid="ca-summary-tab">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Input type="month" className="h-9 w-44" value={monthFilter} onChange={e => setMonthFilter(e.target.value)} data-testid="ca-month-filter" />
        {canViewAllBranches && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="h-9 w-48"><SelectValue placeholder="All branches" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Branches</SelectItem>
              {(branches || []).map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw size={13} className={`mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {/* Summary KPIs */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KpiCard label="Total Employees" value={data.total_employees} accent="slate" />
          <KpiCard label="Advances This Month" value={formatPHP(data.total_advances_this_month)} accent="amber" />
          <KpiCard label="Total Unpaid Balance" value={formatPHP(data.total_unpaid_balance)} sub="Pending salary deduction" accent="red" />
          <KpiCard label="Over-Limit Employees" value={data.over_limit_employees} sub={data.over_limit_employees > 0 ? 'Need payroll review' : 'All within limits'} accent={data.over_limit_employees > 0 ? 'red' : 'emerald'} />
        </div>
      )}

      {/* Employee Table */}
      {data && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <Table data-testid="ca-report-table">
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500">Employee</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500">Branch</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Monthly Limit</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">This Month</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-center">Usage</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Over-Limit</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Prev Month Overage</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Unpaid Balance</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500">Last Advance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.employees.map(emp => (
                  <TableRow key={emp.employee_id} className={emp.is_over_limit ? 'bg-red-50/50' : ''}>
                    <TableCell>
                      <div>
                        <p className="font-medium text-sm">{emp.name}</p>
                        {emp.position && <p className="text-xs text-slate-400">{emp.position}</p>}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">{getBranchName(emp.branch_id)}</TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {emp.monthly_ca_limit > 0 ? formatPHP(emp.monthly_ca_limit) : <span className="text-slate-300">No limit</span>}
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono font-semibold">
                      {emp.this_month_total > 0 ? (
                        <span className={emp.is_over_limit ? 'text-red-600' : ''}>{formatPHP(emp.this_month_total)}</span>
                      ) : <span className="text-slate-300">—</span>}
                      {emp.this_month_count > 0 && <p className="text-[10px] text-slate-400">{emp.this_month_count} advance{emp.this_month_count > 1 ? 's' : ''}</p>}
                    </TableCell>
                    <TableCell className="text-center">
                      {emp.usage_pct !== null ? (
                        <div className="flex flex-col items-center gap-0.5">
                          <div className="w-16 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                            <div className={`h-full rounded-full ${emp.usage_pct >= 100 ? 'bg-red-500' : emp.usage_pct >= 75 ? 'bg-amber-500' : 'bg-emerald-500'}`}
                              style={{ width: `${Math.min(100, emp.usage_pct)}%` }} />
                          </div>
                          <span className={`text-[10px] font-mono ${emp.usage_pct >= 100 ? 'text-red-600 font-bold' : 'text-slate-400'}`}>{emp.usage_pct}%</span>
                        </div>
                      ) : <span className="text-[10px] text-slate-300">N/A</span>}
                    </TableCell>
                    <TableCell className="text-right">
                      {emp.over_limit_count > 0 ? (
                        <Badge className="bg-red-100 text-red-700 text-[10px]">
                          <AlertTriangle size={9} className="mr-0.5" /> {emp.over_limit_count}x approved
                        </Badge>
                      ) : <span className="text-slate-300 text-xs">—</span>}
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {emp.prev_month_overage > 0 ? (
                        <span className="text-amber-600 font-semibold">{formatPHP(emp.prev_month_overage)}</span>
                      ) : <span className="text-slate-300">—</span>}
                    </TableCell>
                    <TableCell className="text-right text-sm font-mono">
                      {emp.unpaid_balance > 0 ? (
                        <span className="text-red-600 font-semibold">{formatPHP(emp.unpaid_balance)}</span>
                      ) : <span className="text-emerald-500 text-xs">Cleared</span>}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">{emp.last_advance_date || '—'}</TableCell>
                  </TableRow>
                ))}
                {!loading && data.employees.length === 0 && (
                  <TableRow><TableCell colSpan={9} className="text-center text-slate-400 py-10">No employees found</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-16">
          <RefreshCw size={20} className="animate-spin text-slate-400" />
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  DISCOUNT & PRICE OVERRIDE REPORT
// ─────────────────────────────────────────────────────────────────────────────
function DiscountAuditReport({ branches, selectedBranchId }) {
  const [branchId, setBranchId] = useState(selectedBranchId || '');
  const [dateFrom, setDateFrom] = useState(() => { const d = new Date(); d.setDate(1); return d.toISOString().slice(0, 10); });
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10));
  const [groupBy, setGroupBy] = useState('customer');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState(null);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { date_from: dateFrom, date_to: dateTo, group_by: groupBy };
      if (branchId) params.branch_id = branchId;
      const res = await api.get('/reports/discount-audit', { params });
      setData(res.data);
      setExpandedIdx(null);
    } catch { toast.error('Failed to load discount report'); }
    setLoading(false);
  }, [dateFrom, dateTo, groupBy, branchId]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-4" data-testid="discount-audit-tab">
      {/* Filters */}
      <Card className="border-slate-200">
        <CardContent className="p-3">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">From</label>
              <Input type="date" className="h-8 w-36 text-sm" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">To</label>
              <Input type="date" className="h-8 w-36 text-sm" value={dateTo} onChange={e => setDateTo(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Branch</label>
              <Select value={branchId || 'all'} onValueChange={v => setBranchId(v === 'all' ? '' : v)}>
                <SelectTrigger className="h-8 w-40 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Branches</SelectItem>
                  {(branches || []).map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Group By</label>
              <Select value={groupBy} onValueChange={setGroupBy}>
                <SelectTrigger className="h-8 w-36 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="customer">By Customer</SelectItem>
                  <SelectItem value="cashier">By Employee</SelectItem>
                  <SelectItem value="detail">All Details</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button variant="outline" size="sm" className="h-8" onClick={load} disabled={loading}>
              <RefreshCw size={13} className={loading ? 'animate-spin mr-1' : 'mr-1'} /> Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Summary Cards */}
      {data?.summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Card className="border-slate-200"><CardContent className="p-3">
            <p className="text-[10px] text-slate-500 uppercase font-medium">Total Discounts</p>
            <p className="text-lg font-bold text-red-600 font-mono">{formatPHP(data.summary.total_discount)}</p>
          </CardContent></Card>
          <Card className="border-slate-200"><CardContent className="p-3">
            <p className="text-[10px] text-slate-500 uppercase font-medium">Price Overrides</p>
            <p className="text-lg font-bold text-amber-600 font-mono">{formatPHP(data.summary.total_price_overrides)}</p>
          </CardContent></Card>
          <Card className="border-slate-200"><CardContent className="p-3">
            <p className="text-[10px] text-slate-500 uppercase font-medium">Transactions</p>
            <p className="text-lg font-bold text-slate-700">{data.summary.total_transactions}</p>
          </CardContent></Card>
          <Card className="border-slate-200"><CardContent className="p-3">
            <p className="text-[10px] text-slate-500 uppercase font-medium">Period</p>
            <p className="text-sm font-medium text-slate-700">{data.summary.period?.from} to {data.summary.period?.to}</p>
          </CardContent></Card>
        </div>
      )}

      {/* Grouped View */}
      {groupBy !== 'detail' && data?.groups && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="w-8"></TableHead>
                  <TableHead>{groupBy === 'customer' ? 'Customer' : 'Employee'}</TableHead>
                  <TableHead className="text-right">Transactions</TableHead>
                  <TableHead className="text-right text-red-700">Total Discount</TableHead>
                  <TableHead className="text-right text-amber-700">Price Override Diff</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.groups.length === 0 && (
                  <TableRow><TableCell colSpan={5} className="text-center text-slate-400 py-8">No discounts in this period</TableCell></TableRow>
                )}
                {data.groups.map((g, idx) => (
                  <Collapsible key={idx} open={expandedIdx === idx} onOpenChange={o => setExpandedIdx(o ? idx : null)} asChild>
                    <>
                      <CollapsibleTrigger asChild>
                        <TableRow className="cursor-pointer hover:bg-slate-50">
                          <TableCell>{expandedIdx === idx ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</TableCell>
                          <TableCell className="font-medium">{g.name || 'Walk-in'}</TableCell>
                          <TableCell className="text-right">{g.transaction_count}</TableCell>
                          <TableCell className="text-right font-mono text-red-600">{formatPHP(g.total_discount)}</TableCell>
                          <TableCell className="text-right font-mono text-amber-600">{formatPHP(g.total_price_diff)}</TableCell>
                        </TableRow>
                      </CollapsibleTrigger>
                      <CollapsibleContent asChild>
                        <TableRow>
                          <TableCell colSpan={5} className="bg-slate-50 px-8 py-2">
                            <div className="space-y-1">
                              {(g.invoices || []).map((inv, j) => (
                                <div key={j} className="flex items-center justify-between text-xs">
                                  <button onClick={() => { setSelectedInvoiceNumber(inv.invoice_number); setInvoiceModalOpen(true); }} className="font-mono text-blue-700 hover:underline">{inv.invoice_number}</button>
                                  <span className="text-slate-400">{inv.date}</span>
                                  <span className="text-red-600 font-mono">-{formatPHP(inv.total_discount)}</span>
                                  <span className="text-slate-600 font-mono">{formatPHP(inv.grand_total)}</span>
                                </div>
                              ))}
                            </div>
                          </TableCell>
                        </TableRow>
                      </CollapsibleContent>
                    </>
                  </Collapsible>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Detail View */}
      {groupBy === 'detail' && data?.rows && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <div className="overflow-auto max-h-[60vh]">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead>Date</TableHead>
                    <TableHead>Invoice</TableHead>
                    <TableHead>Customer</TableHead>
                    <TableHead>Cashier</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead className="text-right">Original</TableHead>
                    <TableHead className="text-right">Sold At</TableHead>
                    <TableHead className="text-right text-red-700">Discount</TableHead>
                    <TableHead>Type</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.rows.length === 0 && (
                    <TableRow><TableCell colSpan={9} className="text-center text-slate-400 py-8">No discounts in this period</TableCell></TableRow>
                  )}
                  {data.rows.map((r, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs">{r.date}</TableCell>
                      <TableCell className="font-mono text-xs text-blue-700"><button onClick={() => { setSelectedInvoiceNumber(r.invoice_number); setInvoiceModalOpen(true); }} className="hover:underline">{r.invoice_number}</button></TableCell>
                      <TableCell className="text-xs">{r.customer_id ? <button onClick={() => { setStmtCustomer({ id: r.customer_id, name: r.customer_name }); setStmtOpen(true); }} className="hover:text-[#1A4D2E] hover:underline">{r.customer_name}</button> : r.customer_name}</TableCell>
                      <TableCell className="text-xs">{r.cashier_name}</TableCell>
                      <TableCell className="text-xs font-medium">{r.product_name}</TableCell>
                      <TableCell className="text-right text-xs font-mono">{formatPHP(r.original_price)}</TableCell>
                      <TableCell className="text-right text-xs font-mono">{formatPHP(r.sold_price)}</TableCell>
                      <TableCell className="text-right text-xs font-mono text-red-600">{formatPHP(r.discount_amount)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[9px] ${
                          r.type === 'line_discount' ? 'text-red-600 border-red-200' :
                          r.type === 'price_override' ? 'text-amber-600 border-amber-200' :
                          r.type === 'overall_discount' ? 'text-purple-600 border-purple-200' :
                          'text-slate-500'
                        }`}>
                          {r.type === 'line_discount' ? 'Discount' : r.type === 'price_override' ? 'Price Change' : r.type === 'overall_discount' ? 'Overall' : r.type}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
      <InvoiceDetailModal compact open={invoiceModalOpen} onOpenChange={setInvoiceModalOpen} invoiceNumber={selectedInvoiceNumber} />
      <CustomerStatementModal open={stmtOpen} onOpenChange={setStmtOpen} customer={stmtCustomer} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  PRODUCT PROFIT REPORT TAB
// ─────────────────────────────────────────────────────────────────────────────
function ProductProfitReport({ branches, selectedBranchId, canExport }) {
  const today = new Date();
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().slice(0, 10);
  const todayStr = today.toISOString().slice(0, 10);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dateFrom, setDateFrom] = useState(firstOfMonth);
  const [dateTo, setDateTo] = useState(todayStr);
  const [branchFilter, setBranchFilter] = useState(selectedBranchId || 'all');
  const [sortKey, setSortKey] = useState('profit'); // profit | revenue | margin | qty
  const { canViewAllBranches } = useAuth();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
      if (branchFilter && branchFilter !== 'all') params.set('branch_id', branchFilter);
      const res = await api.get(`${BACKEND_URL}/api/reports/product-profit?${params}`);
      setData(res.data);
    } catch (e) {
      toast.error('Failed to load profit data');
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, branchFilter]);

  useEffect(() => { load(); }, [load]);

  const sorted = (data?.rows || []).slice().sort((a, b) => {
    if (sortKey === 'profit') return b.profit - a.profit;
    if (sortKey === 'revenue') return b.total_revenue - a.total_revenue;
    if (sortKey === 'margin') return b.margin_pct - a.margin_pct;
    if (sortKey === 'qty') return b.total_qty - a.total_qty;
    return 0;
  });

  const handlePrint = () => {
    if (!data) return;
    const win = window.open('', '_blank');
    const s = data.summary || {};
    win.document.write(`
      <html><head><title>Product Profit Report</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 12px; padding: 20px; }
        h2 { color: #1A4D2E; margin-bottom: 4px; }
        .sub { color: #666; margin-bottom: 16px; font-size: 11px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
        th { background: #1A4D2E; color: white; padding: 6px 10px; text-align: left; font-size: 11px; }
        td { padding: 5px 10px; border-bottom: 1px solid #e5e7eb; font-size: 11px; }
        tr:nth-child(even) td { background: #f9fafb; }
        .num { text-align: right; font-family: monospace; }
        .total-row td { font-weight: bold; background: #f1f5f9; border-top: 2px solid #94a3b8; }
        .loss { color: #dc2626; }
      </style></head><body>
      <h2>AgriBooks — Product Profit Report</h2>
      <div class="sub">${data.date_from} to ${data.date_to} | ${s.product_count || 0} products | Overall Margin: ${s.overall_margin_pct || 0}%</div>
      <table>
        <thead><tr>
          <th>Product</th>
          <th class="num">Qty</th>
          <th class="num">Revenue</th>
          <th class="num">Cost</th>
          <th class="num">Profit</th>
          <th class="num">Margin</th>
        </tr></thead>
        <tbody>
          ${sorted.map(r => `<tr>
            <td>${r.product_name}${r.is_repack ? ' (R)' : ''}</td>
            <td class="num">${r.total_qty}</td>
            <td class="num">${formatPHP(r.total_revenue)}</td>
            <td class="num">${formatPHP(r.total_cost)}</td>
            <td class="num ${r.profit < 0 ? 'loss' : ''}">${formatPHP(r.profit)}</td>
            <td class="num ${r.margin_pct < 0 ? 'loss' : ''}">${r.margin_pct.toFixed(1)}%</td>
          </tr>`).join('')}
          <tr class="total-row">
            <td>TOTAL</td>
            <td></td>
            <td class="num">${formatPHP(s.total_revenue || 0)}</td>
            <td class="num">${formatPHP(s.total_cost || 0)}</td>
            <td class="num ${s.total_profit < 0 ? 'loss' : ''}">${formatPHP(s.total_profit || 0)}</td>
            <td class="num">${s.overall_margin_pct || 0}%</td>
          </tr>
        </tbody>
      </table>
      </body></html>
    `);
    win.document.close();
    win.print();
  };

  const s = data?.summary || {};

  return (
    <div className="space-y-4" data-testid="profit-report-tab">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div><label className="text-[10px] text-slate-500 uppercase font-medium">From</label>
          <Input type="date" className="h-8 w-36 text-sm" value={dateFrom} onChange={e => setDateFrom(e.target.value)} data-testid="profit-date-from" /></div>
        <div><label className="text-[10px] text-slate-500 uppercase font-medium">To</label>
          <Input type="date" className="h-8 w-36 text-sm" value={dateTo} onChange={e => setDateTo(e.target.value)} data-testid="profit-date-to" /></div>
        {canViewAllBranches && (
          <Select value={branchFilter} onValueChange={setBranchFilter}>
            <SelectTrigger className="w-44 h-8 text-xs" data-testid="profit-branch-filter"><SelectValue placeholder="All Branches" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Branches</SelectItem>
              {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        <Button size="sm" onClick={load} disabled={loading} className="bg-[#1A4D2E] hover:bg-[#15402A] text-white" data-testid="profit-run-btn">
          <Filter size={13} className="mr-1.5" /> Run Report
        </Button>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          <RefreshCw size={13} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
        <div className="ml-auto">
          {canExport && <PrintButton onClick={handlePrint} />}
        </div>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <KpiCard label="Total Revenue" value={formatPHP(s.total_revenue || 0)} accent="emerald" sub={`${data?.date_from || ''} → ${data?.date_to || ''}`} />
        <KpiCard label="Total Cost" value={formatPHP(s.total_cost || 0)} accent="slate" />
        <KpiCard label="Gross Profit" value={formatPHP(s.total_profit || 0)} accent={s.total_profit >= 0 ? 'emerald' : 'red'} />
        <KpiCard label="Overall Margin" value={`${s.overall_margin_pct || 0}%`} accent="blue" sub={`${s.product_count || 0} products`} />
        <KpiCard label="Loss-Making" value={s.loss_making_count || 0} accent={s.loss_making_count > 0 ? 'red' : 'emerald'} sub={s.loss_making_count > 0 ? 'Need review' : 'All profitable'} />
      </div>

      {/* Sort */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500">Sort by:</span>
        {[
          { k: 'profit', l: 'Profit' }, { k: 'revenue', l: 'Revenue' },
          { k: 'margin', l: 'Margin %' }, { k: 'qty', l: 'Quantity' },
        ].map(s => (
          <Button key={s.k} size="sm" variant={sortKey === s.k ? 'default' : 'outline'}
            onClick={() => setSortKey(s.k)} className="h-7 text-xs" data-testid={`sort-${s.k}`}>{s.l}</Button>
        ))}
      </div>

      {/* Table */}
      <Card>
        <CardHeader className="py-3 px-4 bg-slate-50 border-b">
          <CardTitle className="text-sm font-semibold text-slate-700">Product Profitability Detail</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table data-testid="profit-table">
            <TableHeader>
              <TableRow className="bg-slate-50">
                <TableHead>Product</TableHead>
                <TableHead className="text-right">Qty Sold</TableHead>
                <TableHead className="text-right">Revenue</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Profit</TableHead>
                <TableHead className="text-right">Margin %</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((r, i) => (
                <TableRow key={r.product_id} className={r.profit < 0 ? 'bg-red-50/50' : ''}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {r.is_repack && <span className="w-4 border-l-2 border-b-2 border-slate-300 h-3 inline-block" />}
                      <span className="font-medium text-sm">{r.product_name}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">{r.total_qty}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatPHP(r.total_revenue)}</TableCell>
                  <TableCell className="text-right font-mono text-sm text-slate-500">{formatPHP(r.total_cost)}</TableCell>
                  <TableCell className={`text-right font-mono text-sm font-semibold ${r.profit < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                    {formatPHP(r.profit)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1.5">
                      <span className={`font-mono text-sm ${r.margin_pct < 0 ? 'text-red-600' : r.margin_pct < 15 ? 'text-amber-600' : 'text-emerald-600'}`}>
                        {r.margin_pct.toFixed(1)}%
                      </span>
                      <div className="w-12 bg-slate-100 rounded-full h-1.5">
                        <div className={`h-1.5 rounded-full ${r.margin_pct < 0 ? 'bg-red-400' : r.margin_pct < 15 ? 'bg-amber-400' : 'bg-emerald-400'}`}
                          style={{ width: `${Math.min(Math.max(r.margin_pct, 0), 100)}%` }} />
                      </div>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {sorted.length > 0 && (
                <TableRow className="bg-slate-100 font-bold border-t-2 border-slate-300">
                  <TableCell>TOTAL ({data?.summary?.product_count || 0} products)</TableCell>
                  <TableCell />
                  <TableCell className="text-right font-mono">{formatPHP(data?.summary?.total_revenue || 0)}</TableCell>
                  <TableCell className="text-right font-mono">{formatPHP(data?.summary?.total_cost || 0)}</TableCell>
                  <TableCell className={`text-right font-mono ${(data?.summary?.total_profit || 0) < 0 ? 'text-red-700' : 'text-emerald-700'}`}>
                    {formatPHP(data?.summary?.total_profit || 0)}
                  </TableCell>
                  <TableCell className="text-right font-mono">{data?.summary?.overall_margin_pct || 0}%</TableCell>
                </TableRow>
              )}
              {!loading && sorted.length === 0 && (
                <TableRow><TableCell colSpan={6} className="text-center text-slate-400 py-10">No sales data found for this period.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  MAIN PAGE
// ─────────────────────────────────────────────────────────────────────────────
export default function ReportsPage() {
  const { branches, selectedBranchId, hasPerm } = useAuth();
  const canExport = hasPerm('reports', 'export');
  const canViewProfit = hasPerm('reports', 'view_profit');

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-[#1A4D2E] flex items-center justify-center">
          <BarChart3 size={20} className="text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Reports</h1>
          <p className="text-xs text-slate-500">AR Aging · Sales · Expenses · CA Summary · Discounts{canViewProfit ? ' · Profit' : ''}</p>
        </div>
      </div>

      <Tabs defaultValue="ar-aging" className="space-y-4">
        <TabsList className="bg-slate-100">
          <TabsTrigger value="ar-aging" data-testid="tab-ar-aging" className="text-sm">
            <AlertCircle size={14} className="mr-1.5" /> AR Aging
          </TabsTrigger>
          <TabsTrigger value="sales" data-testid="tab-sales" className="text-sm">
            <TrendingUp size={14} className="mr-1.5" /> Sales Report
          </TabsTrigger>
          <TabsTrigger value="expenses" data-testid="tab-expenses" className="text-sm">
            <DollarSign size={14} className="mr-1.5" /> Expense Report
          </TabsTrigger>
          <TabsTrigger value="ca-summary" data-testid="tab-ca-summary" className="text-sm">
            <UserCheck size={14} className="mr-1.5" /> CA Summary
          </TabsTrigger>
          <TabsTrigger value="discounts" data-testid="tab-discounts" className="text-sm">
            <Percent size={14} className="mr-1.5" /> Discounts
          </TabsTrigger>
          {canViewProfit && (
            <TabsTrigger value="profit" data-testid="tab-profit" className="text-sm">
              <TrendingDown size={14} className="mr-1.5" /> Profit
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="ar-aging">
          <ArAgingReport branches={branches || []} selectedBranchId={selectedBranchId} canExport={canExport} />
        </TabsContent>

        <TabsContent value="sales">
          <SalesReport branches={branches || []} selectedBranchId={selectedBranchId} canExport={canExport} />
        </TabsContent>

        <TabsContent value="expenses">
          <ExpenseReport branches={branches || []} selectedBranchId={selectedBranchId} canExport={canExport} />
        </TabsContent>

        <TabsContent value="ca-summary">
          <CaSummaryReport branches={branches || []} selectedBranchId={selectedBranchId} />
        </TabsContent>

        <TabsContent value="discounts">
          <DiscountAuditReport branches={branches || []} selectedBranchId={selectedBranchId} />
        </TabsContent>

        {canViewProfit && (
          <TabsContent value="profit">
            <ProductProfitReport branches={branches || []} selectedBranchId={selectedBranchId} canExport={canExport} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
