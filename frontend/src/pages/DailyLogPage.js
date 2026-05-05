import React, { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { ScrollArea } from '../components/ui/scroll-area';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import {
  ClipboardList, TrendingUp, Lock, Printer, Calendar,
  DollarSign, ArrowDown, ArrowUp, AlertTriangle, Plus, CheckCircle, FileWarning,
  Archive, Eye, RefreshCw, Building2
} from 'lucide-react';
import { toast } from 'sonner';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';
import CalcInput from '../components/CalcInput';
import { format24To12, formatDate, formatDateTime, formatTime, localTodayStr } from '../lib/dateFormat';

// ── Small helper components ───────────────────────────────────────────────────
function SectionCard({ title, children, accent = 'slate', note }) {
  const borders = { emerald: 'border-emerald-200', blue: 'border-blue-200', red: 'border-red-200', amber: 'border-amber-200', slate: 'border-slate-200', indigo: 'border-indigo-200' };
  const headers = { emerald: 'bg-emerald-50 text-emerald-800', blue: 'bg-blue-50 text-blue-800', red: 'bg-red-50 text-red-800', amber: 'bg-amber-50 text-amber-800', slate: 'bg-slate-50 text-slate-700', indigo: 'bg-indigo-50 text-indigo-800' };
  return (
    <Card className={`border ${borders[accent]}`}>
      <CardHeader className={`py-2.5 px-4 ${headers[accent]} border-b ${borders[accent]}`}>
        <CardTitle className="text-sm font-semibold flex items-center justify-between">
          <span>{title}</span>
          {note && <span className="text-[10px] font-normal opacity-70 italic">{note}</span>}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4">{children}</CardContent>
    </Card>
  );
}

function InfoRow({ label, value, bold, className = '' }) {
  return (
    <div className="flex justify-between items-center text-sm py-0.5">
      <span className="text-slate-500">{label}</span>
      <span className={`font-mono ${bold ? 'font-bold' : ''} ${className}`}>{value}</span>
    </div>
  );
}

// ── Closing Adjustments Panel ──────────────────────────────────────────────
// Shown inside the Z-Report viewer for any closed day. Admins see an inline
// form to shift the effective Over/Short (original expected/actual stay
// untouched per the audit-first design in backend/daily_operations.py).
// Non-admins see a read-only list of existing adjustments when present.
function ClosingAdjustmentsPanel({ closing, isAdmin, onAdjusted }) {
  const [rows, setRows] = React.useState(closing?.adjustments || []);
  const [showForm, setShowForm] = React.useState(false);
  const [amount, setAmount] = React.useState('');
  const [reason, setReason] = React.useState('');
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    setRows(closing?.adjustments || []);
  }, [closing]);

  const rawOS = parseFloat(closing?.over_short || 0);
  const adjTotal = rows.filter(r => !r.voided).reduce((s, r) => s + parseFloat(r.amount || 0), 0);
  const adjusted = rawOS + adjTotal;

  const reload = async () => {
    if (!closing?.id) return;
    try {
      const res = await api.get(`/daily-closings/${closing.id}/adjustments`);
      setRows(res.data?.adjustments || []);
      onAdjusted && onAdjusted();
    } catch { /* silent */ }
  };

  const submit = async () => {
    const amt = parseFloat(amount);
    if (!amt || Number.isNaN(amt)) { toast.error('Enter a non-zero amount'); return; }
    if ((reason || '').trim().length < 3) { toast.error('Reason is required (min 3 chars)'); return; }
    setSaving(true);
    try {
      await api.post(`/daily-closings/${closing.id}/adjustments`, { amount: amt, reason: reason.trim() });
      toast.success('Adjustment applied');
      setAmount(''); setReason(''); setShowForm(false);
      await reload();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to apply adjustment');
    }
    setSaving(false);
  };

  const voidRow = async (row) => {
    const why = window.prompt('Reason for voiding this adjustment?');
    if (!why || why.trim().length < 3) { toast.error('Void reason required (min 3 chars)'); return; }
    try {
      await api.post(`/daily-closings/${closing.id}/adjustments/${row.id}/void`, { reason: why.trim() });
      toast.success('Adjustment voided');
      await reload();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to void');
    }
  };

  // Hide panel entirely if not admin and there are no adjustments — keeps
  // the Z-Report clean for cashiers/managers on normal days.
  const nonVoided = rows.filter(r => !r.voided);
  if (!isAdmin && nonVoided.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-200 overflow-hidden" data-testid="closing-adjustments-panel">
      <div className="px-4 py-2 bg-amber-50 flex items-center justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-amber-700">
            Reconciliation Adjustments
          </p>
          <p className="text-[10px] text-slate-500">
            Admin-only override for correcting historical data. Original close values are never modified.
          </p>
        </div>
        {isAdmin && !showForm && (
          <Button
            size="sm" variant="outline"
            className="h-7 text-xs border-amber-300 text-amber-700 hover:bg-amber-100"
            onClick={() => setShowForm(true)}
            data-testid="add-closing-adjustment-btn"
          >
            + Add Adjustment
          </Button>
        )}
      </div>

      {/* Totals strip */}
      <div className="px-4 py-2 grid grid-cols-3 gap-2 bg-white border-b border-amber-100 text-xs">
        <div>
          <p className="text-slate-400">Raw Over/Short</p>
          <p className={`font-mono font-semibold ${rawOS >= 0 ? 'text-emerald-700' : 'text-red-600'}`}>
            {rawOS > 0 ? '+' : ''}{formatPHP(rawOS)}
          </p>
        </div>
        <div>
          <p className="text-slate-400">Adjustments</p>
          <p className={`font-mono font-semibold ${adjTotal === 0 ? 'text-slate-400' : adjTotal > 0 ? 'text-emerald-700' : 'text-red-600'}`}>
            {adjTotal > 0 ? '+' : ''}{adjTotal === 0 ? '—' : formatPHP(adjTotal)}
          </p>
        </div>
        <div>
          <p className="text-slate-400 font-semibold">Adjusted Over/Short</p>
          <p className={`font-mono font-bold ${adjusted >= 0 ? 'text-emerald-700' : 'text-red-600'}`} data-testid="adjusted-over-short-value">
            {adjusted > 0 ? '+' : ''}{formatPHP(adjusted)}
          </p>
        </div>
      </div>

      {showForm && (
        <div className="px-4 py-3 bg-amber-50/40 border-b border-amber-100 space-y-2">
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-1">
              <Label className="text-[11px] text-slate-500">Amount (signed)</Label>
              <Input
                type="number" step="0.01" value={amount}
                onChange={e => setAmount(e.target.value)}
                placeholder="e.g. 147354.40 or -500"
                data-testid="closing-adjustment-amount-input"
              />
              <p className="text-[10px] text-slate-400 mt-1">
                Positive = increases Over; negative = decreases.
              </p>
            </div>
            <div className="col-span-2">
              <Label className="text-[11px] text-slate-500">Reason (required)</Label>
              <Input
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="e.g. Pre-migration AR double-counted in initial sync"
                data-testid="closing-adjustment-reason-input"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button size="sm" variant="ghost" onClick={() => { setShowForm(false); setAmount(''); setReason(''); }}>
              Cancel
            </Button>
            <Button
              size="sm"
              className="bg-[#1A4D2E] text-white hover:bg-[#163f25]"
              onClick={submit}
              disabled={saving}
              data-testid="save-closing-adjustment-btn"
            >
              {saving ? 'Saving…' : 'Apply Adjustment'}
            </Button>
          </div>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="px-4 py-3 text-center text-xs text-slate-400">
          No adjustments on this day.
        </div>
      ) : (
        <div className="divide-y divide-amber-100">
          {rows.map((r, i) => (
            <div key={r.id || i} className={`px-4 py-2 flex items-center gap-3 ${r.voided ? 'opacity-50' : ''}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={`font-mono font-semibold text-sm ${parseFloat(r.amount) >= 0 ? 'text-emerald-700' : 'text-red-600'}`}>
                    {parseFloat(r.amount) > 0 ? '+' : ''}{formatPHP(parseFloat(r.amount || 0))}
                  </span>
                  {r.voided && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-200 text-slate-600 uppercase tracking-wider">Voided</span>
                  )}
                </div>
                <p className="text-xs text-slate-600 mt-0.5">{r.reason}</p>
                <p className="text-[10px] text-slate-400 mt-0.5">
                  by {r.created_by_name} · {formatDateTime(r.created_at)}
                  {r.voided && r.voided_reason && (
                    <> · voided by {r.voided_by_name}: "{r.voided_reason}"</>
                  )}
                </p>
              </div>
              {isAdmin && !r.voided && (
                <Button
                  size="sm" variant="ghost"
                  className="h-7 text-xs text-red-600 hover:bg-red-50"
                  onClick={() => voidRow(r)}
                  data-testid={`void-closing-adjustment-${r.id}`}
                >
                  Void
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ZReport({ data, branchName, onPrint, onDownloadPdf }) {
  if (!data) return null;
  const r2 = n => Math.round((parseFloat(n) || 0) * 100) / 100;
  return (
    <div id="printable-report" className="space-y-4 print:space-y-3">
      <div className="flex items-center justify-between print:hidden">
        <Card className="border-emerald-200 bg-emerald-50 flex-1 mr-3"><CardContent className="p-3">
          <p className="font-bold text-emerald-800">
            <CheckCircle size={14} className="inline mr-1" />
            Day closed by {data.closed_by_name} · {formatDateTime(data.closed_at)}
          </p>
        </CardContent></Card>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={onPrint} data-testid="z-normal-print-btn">
            <Printer size={14} className="mr-1" /> Print
          </Button>
          <Button variant="outline" size="sm" onClick={onDownloadPdf} data-testid="z-normal-download-btn">
            <ArrowDown size={14} className="mr-1" /> Download PDF
          </Button>
        </div>
      </div>

      {/* Print header */}
      <div className="hidden print:block border-b-2 border-black pb-3 mb-3">
        <h1 className="text-xl font-bold">DAILY CLOSE REPORT — {branchName}</h1>
        <p>Date: {data.date} · Closed by: {data.closed_by_name}</p>
      </div>

      <div className="grid md:grid-cols-2 gap-3">
        <SectionCard title="Opening">
          <InfoRow label="Safe Balance" value={formatPHP(data.safe_balance)} bold />
          <InfoRow label="Opening Float" value={formatPHP(data.starting_float)} bold className="text-emerald-700" />
        </SectionCard>
        <SectionCard title="Cash Reconciliation">
          <InfoRow label="Expected in Counter" value={formatPHP(data.expected_counter)} />
          <InfoRow label="Actual Cash Counted" value={formatPHP(data.actual_cash)} bold />
          <Separator className="my-1" />
          <InfoRow label={data.over_short >= 0 ? 'Cash Over' : 'Cash Short'}
            value={`${data.over_short >= 0 ? '+' : ''}${formatPHP(data.over_short)}`}
            bold className={data.over_short >= 0 ? 'text-emerald-600' : 'text-red-600'} />
          <InfoRow label="Transferred to Vault" value={formatPHP(data.cash_to_safe)} />
          <InfoRow label="Opening Float (Next Day)" value={formatPHP(data.cash_to_drawer)} bold className="text-emerald-600" />
        </SectionCard>
      </div>

      <SectionCard title={`Walk-in Sales — ${formatPHP(data.total_cash_sales)}`} accent="emerald">
        {Object.entries(data.sales_by_category || {}).map(([cat, total]) => (
          <div key={cat} className="flex justify-between text-sm py-1 border-b border-slate-100 last:border-0">
            <span>{cat}</span><span className="font-semibold font-mono">{formatPHP(total)}</span>
          </div>
        ))}
      </SectionCard>

      {/* New Credit Extended Today */}
      {((data.credit_sales_today?.length > 0) || (data.ar_credits_today?.length > 0)) && (
        <SectionCard title={`New Credit Extended Today — ${formatPHP(data.total_new_credit || 0)}`} accent="amber" note="Not counted as cash received — added to AR">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-amber-50 text-xs text-slate-500 border-b">
                <th className="px-3 py-1.5 text-left">Customer</th>
                <th className="px-3 py-1.5 text-left">Invoice</th>
                <th className="px-3 py-1.5 text-right">Amount</th>
                <th className="px-3 py-1.5 text-right">Balance</th>
                <th className="px-3 py-1.5 text-center">Type</th>
              </tr></thead>
              <tbody>
                {(data.credit_sales_today || []).map((c, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-1.5 font-medium">{c.customer_id ? <button onClick={() => openCustStmt(c.customer_id, c.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{c.customer_name}</button> : c.customer_name}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-blue-600"><button className="hover:underline hover:text-blue-800" onClick={() => { setSelectedInvoiceNumber(c.invoice_number); setInvoiceModalOpen(true); }} data-testid={`inv-link-credit-${i}`}>{c.invoice_number}</button></td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold text-amber-700">{formatPHP(c.grand_total)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-slate-500">{formatPHP(c.balance)}</td>
                    <td className="px-3 py-1.5 text-center"><Badge className="text-[9px] bg-amber-100 text-amber-700">Credit</Badge></td>
                  </tr>
                ))}
                {(data.ar_credits_today || []).map((c, i) => (
                  <tr key={`arc-${i}`} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-1.5">
                      <div className="font-medium">{c.customer_id ? <button onClick={() => openCustStmt(c.customer_id, c.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{c.customer_name}</button> : c.customer_name}</div>
                      {(c.description || c.items?.[0]?.product_name) && (
                        <div className="text-xs text-slate-400">{c.description || c.items?.[0]?.product_name}</div>
                      )}
                    </td>
                    <td className="px-3 py-1.5 font-mono text-xs text-blue-600"><button className="hover:underline hover:text-blue-800" onClick={() => { setSelectedInvoiceNumber(c.invoice_number); setInvoiceModalOpen(true); }} data-testid={`inv-link-arc-${i}`}>{c.invoice_number}</button></td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold text-blue-700">{formatPHP(c.grand_total)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-slate-500">—</td>
                    <td className="px-3 py-1.5 text-center">
                      <Badge className={`text-[9px] ${c.type === 'cash_advance' ? 'bg-blue-100 text-blue-700' : 'bg-purple-100 text-purple-700'}`}>
                        {c.type === 'cash_advance' ? 'Cash-out' : 'Farm'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {data.credit_collections?.length > 0 && (
        <SectionCard title={`AR Payments Received — ${formatPHP(data.total_ar_received)}`} accent="blue">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-slate-50 text-xs text-slate-500 border-b">
                <th className="px-3 py-1.5 text-left">Customer</th>
                <th className="px-3 py-1.5 text-right">Bal Before</th>
                <th className="px-3 py-1.5 text-right">Interest</th>
                <th className="px-3 py-1.5 text-right">Penalty</th>
                <th className="px-3 py-1.5 text-right">Cash Paid</th>
                <th className="px-3 py-1.5 text-right">Remaining</th>
              </tr></thead>
              <tbody>
                {data.credit_collections.map((p, i) => (
                  <tr key={i} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-1.5">{p.customer}<div className="text-[10px] text-slate-400 font-mono">{p.invoice}</div></td>
                    <td className="px-3 py-1.5 text-right font-mono">{formatPHP(p.balance_before)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-amber-600">{p.interest_paid > 0 ? formatPHP(p.interest_paid) : '—'}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-red-500">{p.penalty_paid > 0 ? formatPHP(p.penalty_paid) : '—'}</td>
                    <td className="px-3 py-1.5 text-right font-mono font-bold text-blue-700">{formatPHP(p.total_paid)}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-slate-500">{formatPHP(p.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* AR Running Total at Close */}
      {data.total_ar_at_close !== undefined && (
        <SectionCard title="AR Balance at Close" accent="indigo">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-slate-600">Total Outstanding Accounts Receivable at close of {data.date}</p>
              <p className="text-xs text-slate-400 mt-0.5">All unpaid credit invoices across all customers for this branch</p>
            </div>
            <p className="text-2xl font-bold text-indigo-700 font-mono">{formatPHP(data.total_ar_at_close)}</p>
          </div>
        </SectionCard>
      )}

      <SectionCard title={`Expenses — ${formatPHP(data.total_expenses)}`} accent="red">
        {(data.expenses || []).map((e, i) => (
          <div key={i} className="py-1.5 border-b border-slate-100 last:border-0">
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-[10px]">{e.category}</Badge>
                <span className="text-sm">{e.description || e.employee_name || ''}</span>
              </div>
              <span className="font-semibold text-red-600 font-mono">{formatPHP(e.amount)}</span>
            </div>
            {e.category === 'Employee Advance' && e.monthly_ca_total !== undefined && (
              <div className={`text-[10px] ml-1 mt-0.5 ${e.is_over_ca ? 'text-red-600' : 'text-amber-600'}`}>
                {e.employee_name || 'Employee'} — monthly CA total: <span className="font-semibold">{formatPHP(e.monthly_ca_total)}</span>
                {e.monthly_ca_limit > 0 && <span> / Limit: {formatPHP(e.monthly_ca_limit)}</span>}
                {e.is_over_ca && <span className="font-bold ml-1 text-red-700">OVER CA</span>}
                {e.manager_approved_by && <span className="ml-1 text-violet-600">(Approved: {e.manager_approved_by})</span>}
              </div>
            )}
          </div>
        ))}
      </SectionCard>
    </div>
  );
}

// ── Detailed Z-Report ────────────────────────────────────────────────────
// Mirrors the rich breakdown shown on Close Wizard → Step 7 (Preview) so the
// owner can audit discounts, price changes, AR collections by method, interest
// invoices issued, fund transfers, and per-category cashier/safe expenses —
// none of which fit into the compact `<ZReport>`.
//
// Data source: `/daily-close-preview` which recomputes from sales_log +
// invoices + fund_transfers for any date (also works for already-closed days).
// Read-only — no input fields, no close actions.
function ZReportDetailed({ preview, closing, branchName, onPrint, onDownloadPdf }) {
  if (!preview) return null;
  const total_ar_payments = (preview.ar_payments || [])
    .filter(p => p.fund_source !== 'discount')
    .reduce((s, p) => s + (p.amount_paid || 0), 0);
  const cashierExps = (preview.expenses || []).filter(e => e.fund_source !== 'safe');
  const safeExps = (preview.expenses || []).filter(e => e.fund_source === 'safe');
  return (
    <div id="printable-report" className="space-y-4 print:space-y-3">
      <div className="flex items-center justify-between print:hidden">
        <Card className="border-emerald-200 bg-emerald-50 flex-1 mr-3"><CardContent className="p-3">
          <p className="font-bold text-emerald-800">
            <CheckCircle size={14} className="inline mr-1" />
            DETAILED Z-Report · {closing?.date} · {branchName}
            {closing?.closed_by_name && <> · closed by {closing.closed_by_name}</>}
          </p>
        </CardContent></Card>
        <div className="flex gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={onPrint} data-testid="z-detailed-print-btn">
            <Printer size={14} className="mr-1" /> Print
          </Button>
          <Button variant="outline" size="sm" onClick={onDownloadPdf} data-testid="z-detailed-download-btn">
            <ArrowDown size={14} className="mr-1" /> Download PDF
          </Button>
        </div>
      </div>

      {/* Print header */}
      <div className="hidden print:block border-b-2 border-black pb-3 mb-3">
        <h1 className="text-xl font-bold">DETAILED DAILY CLOSE REPORT — {branchName}</h1>
        <p>Date: {closing?.date} · Closed by: {closing?.closed_by_name || '—'}</p>
      </div>

      {/* ── Cash Drawer Reconciliation ───────────────────────────────── */}
      <div className="rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-2 bg-slate-100 text-xs font-bold uppercase tracking-wider text-slate-600">Cash Drawer Reconciliation</div>
        <div className="p-4 space-y-1.5 text-sm">
          <div className="flex justify-between"><span className="text-slate-500">Opening Float</span><span className="font-mono">{formatPHP(preview.starting_float || 0)}</span></div>
          <div className="flex justify-between"><span className="text-emerald-600">+ Cash Sales</span><span className="font-mono font-semibold text-emerald-700">{formatPHP(preview.total_cash_sales || 0)}</span></div>
          {/* Iter 243.4 — Net sales (gross profit) right under cash sales so
              owners can appreciate margin earned today without leaving the report. */}
          {preview.net_sales_today && (preview.net_sales_today.gross_sales || 0) > 0 && (
            <div className="flex justify-between text-xs pl-3" data-testid="z-net-sales-row">
              <span className="text-emerald-500">↳ Net Sales (after COGS {formatPHP(preview.net_sales_today.cogs || 0)})</span>
              <span className="font-mono font-semibold text-emerald-600">{formatPHP(preview.net_sales_today.net_sales || 0)}</span>
            </div>
          )}
          {(preview.total_split_cash || 0) > 0 && (
            <div className="flex justify-between"><span className="text-teal-600">+ Split Cash Portion</span><span className="font-mono text-teal-700">{formatPHP(preview.total_split_cash || 0)}</span></div>
          )}
          {(preview.total_partial_cash || 0) > 0 && (
            <div className="flex justify-between"><span className="text-blue-600">+ Partial Cash Received</span><span className="font-mono text-blue-700">{formatPHP(preview.total_partial_cash || 0)}</span></div>
          )}
          <div className="flex justify-between"><span className="text-indigo-600">+ AR Cash Payments</span><span className="font-mono text-indigo-700">{formatPHP(preview.total_cash_ar ?? preview.total_ar_received ?? 0)}</span></div>
          {(preview.total_digital_ar || 0) > 0 && (
            <div className="flex justify-between text-xs"><span className="text-indigo-400 pl-3">AR Digital (e-wallet, not in drawer)</span><span className="font-mono text-indigo-400">{formatPHP(preview.total_digital_ar || 0)}</span></div>
          )}
          {(preview.ar_interest_collected || 0) > 0 && (
            <div className="flex justify-between text-xs"><span className="text-amber-500 pl-3">↳ of which interest collected</span><span className="font-mono text-amber-600">{formatPHP(preview.ar_interest_collected)}</span></div>
          )}
          {(preview.ar_discount_today || 0) > 0 && (
            <div className="flex justify-between text-xs"><span className="text-blue-500 pl-3">↳ discounts given on interest/penalty</span><span className="font-mono text-blue-600">-{formatPHP(preview.ar_discount_today)}</span></div>
          )}
          {(preview.capital_to_cashier || 0) > 0 && (
            <div className="flex justify-between"><span className="text-cyan-600">+ Capital Injection</span><span className="font-mono text-cyan-700">{formatPHP(preview.capital_to_cashier)}</span></div>
          )}
          {(preview.safe_to_cashier || 0) > 0 && (
            <div className="flex justify-between"><span className="text-cyan-600">+ Safe → Cashier</span><span className="font-mono text-cyan-700">{formatPHP(preview.safe_to_cashier)}</span></div>
          )}
          {(preview.cashier_to_safe || 0) > 0 && (
            <div className="flex justify-between"><span className="text-orange-600">- Cashier → Safe</span><span className="font-mono text-orange-600">{formatPHP(preview.cashier_to_safe)}</span></div>
          )}
          <Separator />
          <div className="flex justify-between font-semibold"><span className="text-green-700">= Total Cash In</span><span className="font-mono text-green-700">{formatPHP(preview.total_cash_in || 0)}</span></div>
          <div className="flex justify-between"><span className="text-red-600">- Cashier Expenses</span><span className="font-mono text-red-600">{formatPHP(preview.total_cashier_expenses ?? preview.total_expenses ?? 0)}</span></div>
          {/* Iter 243.4 — Employee cash advance running totals. Surfaces hidden
              risk: an employee can rack up advances buried in routine expenses,
              and the owner only finds out at salary day. Show today's amount AND
              the outstanding balance so the owner sees the full position. */}
          {(preview.employee_advances_today || []).length > 0 && (
            <div className="text-xs pl-3 space-y-0.5" data-testid="z-employee-advances">
              {(preview.employee_advances_today || []).map((emp, i) => (
                <div key={i} className="flex justify-between">
                  <span className="text-amber-600">↳ {emp.employee_name} advance today</span>
                  <span className="font-mono text-amber-700">
                    {formatPHP(emp.today_amount)}
                    {emp.outstanding_balance != null && (
                      <span className="text-amber-500 ml-1">(running: {formatPHP(emp.outstanding_balance)})</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
          {(preview.total_safe_expenses || 0) > 0 && (
            <div className="flex justify-between text-xs"><span className="text-red-400 pl-3">Safe expenses (not from drawer)</span><span className="font-mono text-red-400">{formatPHP(preview.total_safe_expenses || 0)}</span></div>
          )}
          <Separator />
          <div className="flex justify-between font-bold text-base"><span>Expected in Drawer</span><span className="font-mono">{formatPHP(preview.expected_counter || closing?.expected_cash || 0)}</span></div>
          <div className="flex justify-between font-bold text-base"><span>Actual Count</span><span className="font-mono">{formatPHP(closing?.actual_cash || 0)}</span></div>
          {closing && (
            <div className={`flex justify-between font-bold ${(closing.over_short || 0) >= 0 ? 'text-emerald-700' : 'text-red-600'}`}>
              <span>Over / Short</span>
              <span className="font-mono">{(closing.over_short || 0) > 0 ? '+' : ''}{formatPHP(closing.over_short || 0)}</span>
            </div>
          )}
          {/* Iter 243.4 — show BEFORE → AFTER allocation so the report reflects
              what actually ended up in the drawer & safe at close, not the
              pre-Step-6 snapshot. Only render for already-closed days where
              cash_to_safe / cash_to_drawer have been entered. */}
          {closing && ((closing.cash_to_safe || 0) > 0 || (closing.cash_to_drawer || 0) > 0) && (
            <>
              <Separator className="my-2" />
              <div className="bg-emerald-50 -mx-4 px-4 py-2 space-y-1 mt-1" data-testid="z-post-allocation">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700 mb-1">After Allocation</div>
                <div className="flex justify-between text-xs">
                  <span className="text-orange-600">→ Cash to Safe</span>
                  <span className="font-mono text-orange-700">{formatPHP(closing.cash_to_safe || 0)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-emerald-600">→ Drawer Carried Forward</span>
                  <span className="font-mono text-emerald-700">{formatPHP(closing.cash_to_drawer || 0)}</span>
                </div>
                <Separator className="my-1" />
                <div className="flex justify-between text-sm font-semibold">
                  <span>Final Drawer</span>
                  <span className="font-mono">{formatPHP(closing.cash_to_drawer || 0)}</span>
                </div>
                <div className="flex justify-between text-sm font-semibold">
                  <span>Final Safe</span>
                  <span className="font-mono">{formatPHP((closing.safe_balance || 0) + (closing.cash_to_safe || 0))}</span>
                  <span className="text-[10px] text-slate-400 ml-2">({formatPHP(closing.safe_balance || 0)} + {formatPHP(closing.cash_to_safe || 0)})</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── Discount Breakdown by Product ─────────────────────────────── */}
      {(preview.discount_breakdown?.products?.length > 0 || (preview.discount_breakdown?.total_overall_discount || 0) > 0) && (
        <div className="rounded-xl border border-rose-200 overflow-hidden">
          <div className="px-4 py-2 bg-rose-50 text-xs font-bold uppercase tracking-wider text-rose-700 flex items-center justify-between">
            <span>Discounts Given Today</span>
            <span className="font-mono">{formatPHP(preview.discount_breakdown?.total_discount || 0)}</span>
          </div>
          <div className="p-3 space-y-2">
            {(preview.discount_breakdown?.products || []).length > 0 && (
              <table className="w-full text-xs">
                <thead className="bg-rose-50/60">
                  <tr>
                    <th className="text-left px-2 py-1.5 text-rose-700 font-medium">Product</th>
                    <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-20">Units</th>
                    <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-28">Total Disc.</th>
                    <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-28">Avg / Unit</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.discount_breakdown.products.map((p, i) => (
                    <tr key={i} className="border-t border-rose-50">
                      <td className="px-2 py-1 text-slate-700">{p.product_name}</td>
                      <td className="px-2 py-1 text-right font-mono">{p.units_sold}</td>
                      <td className="px-2 py-1 text-right font-mono text-rose-600">{formatPHP(p.total_discount)}</td>
                      <td className="px-2 py-1 text-right font-mono text-rose-500">{formatPHP(p.avg_discount_per_unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {(preview.discount_breakdown?.total_overall_discount || 0) > 0 && (
              <div className="text-xs text-rose-700 flex items-center justify-between bg-rose-50 px-2 py-1 rounded">
                <span>Overall (invoice-level) discounts</span>
                <span className="font-mono">{formatPHP(preview.discount_breakdown.total_overall_discount)}</span>
              </div>
            )}
            <p className="text-[10px] text-slate-500 italic">
              {preview.discount_breakdown?.transaction_count || 0} transaction(s) had discounts.
            </p>
          </div>
        </div>
      )}

      {/* ── Price Changes Today ─ grouped by category, with capital refs ──
          Backend `_get_price_changes_today` enriches each row with category,
          `moving_average_cost` and `last_purchase_cost`, and returns rows
          pre-sorted by (category, product_name). We groupBy here so the
          owner can audit by department (Feeds, Meds, etc.) and immediately
          see how the override compares against capital. */}
      {(preview.price_changes_today?.count || 0) > 0 && (
        <div className="rounded-xl border border-amber-200 overflow-hidden">
          <div className="px-4 py-2 bg-amber-50 text-xs font-bold uppercase tracking-wider text-amber-700 flex items-center justify-between">
            <span>Permanent Price Changes Today</span>
            <span className="font-mono">{preview.price_changes_today.count} change(s)</span>
          </div>
          <div className="px-3 pt-2 pb-1 grid grid-cols-12 text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
            <div className="col-span-4">Product</div>
            <div className="col-span-2 text-right">Cap MA</div>
            <div className="col-span-2 text-right">Last Pur.</div>
            <div className="col-span-3 text-right">From → To</div>
            <div className="col-span-1 text-right">OK by</div>
          </div>
          {(() => {
            // Group rows by category for sub-header dividers.
            const rows = preview.price_changes_today.rows || [];
            const groups = [];
            let currentCat = null;
            let currentBucket = null;
            for (const r of rows) {
              const cat = r.category || 'Uncategorized';
              if (cat !== currentCat) {
                currentCat = cat;
                currentBucket = { cat, items: [] };
                groups.push(currentBucket);
              }
              currentBucket.items.push(r);
            }
            const hasGlobalFallback = rows.some(r => r.last_purchase_source === 'global');
            return (
              <>
                {groups.map((g, gi) => (
                  <div key={gi} className="border-t border-amber-100">
                    <div className="px-3 py-1 bg-amber-50/50 text-[10px] uppercase tracking-wider text-amber-800 font-bold flex items-center justify-between">
                      <span>{g.cat}</span>
                      <span className="font-mono text-amber-600">{g.items.length}</span>
                    </div>
                    <div className="px-3 py-1 text-xs">
                      {g.items.map((r, i) => {
                        const up = r.new_price > r.old_price;
                        const down = r.new_price < r.old_price;
                        const ma = parseFloat(r.moving_average_cost || 0);
                        const lp = parseFloat(r.last_purchase_cost || 0);
                        const lpGlobal = r.last_purchase_source === 'global';
                        return (
                          <div key={i} className="grid grid-cols-12 items-center border-b border-amber-50 last:border-0 py-1">
                            <div className="col-span-4 min-w-0">
                              <span className="font-medium text-slate-700 truncate block">{r.product_name}</span>
                              <span className="text-[10px] text-slate-400 font-mono">{r.invoice_number}</span>
                            </div>
                            <div className="col-span-2 text-right text-[11px] font-mono text-slate-500">
                              {ma > 0 ? formatPHP(ma) : '—'}
                            </div>
                            <div className={`col-span-2 text-right text-[11px] font-mono ${lpGlobal ? 'italic text-slate-400' : 'text-slate-500'}`}>
                              {lp > 0 ? (
                                <>
                                  {formatPHP(lp)}
                                  {lpGlobal && <span title="No branch purchase history — using global cost" className="ml-0.5 text-amber-500">*</span>}
                                </>
                              ) : '—'}
                            </div>
                            <div className="col-span-3 font-mono text-right">
                              <span className="line-through text-slate-400">{formatPHP(r.old_price)}</span>
                              {' → '}
                              <span className={`font-semibold ${down ? 'text-red-600' : up ? 'text-emerald-600' : 'text-slate-700'}`}>
                                {formatPHP(r.new_price)}
                              </span>
                            </div>
                            <div className="col-span-1 text-right text-[10px] text-slate-500 truncate">
                              {r.approver_name || '—'}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
                {hasGlobalFallback && (
                  <div className="px-3 py-1.5 bg-amber-50/30 border-t border-amber-100 text-[10px] italic text-slate-500">
                    <span className="text-amber-500">*</span> Last Purchase shown is the product's global cost — no branch purchase history yet.
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* ── AR Collections Detailed ──────────────────────────────────── */}
      {(preview.ar_payments || []).filter(p => p.fund_source !== 'discount').length > 0 && (
        <div className="rounded-xl border border-indigo-200 overflow-hidden">
          <div className="px-4 py-2 bg-indigo-50 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <p className="text-xs font-bold uppercase tracking-wider text-indigo-600">AR Collections Today</p>
              {preview.ar_payment_by_method && Object.entries(preview.ar_payment_by_method).map(([m, v]) => (
                <span key={m} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">
                  {m}: {formatPHP(v.total)}
                </span>
              ))}
            </div>
            <span className="font-bold font-mono text-indigo-700 text-sm">{formatPHP(total_ar_payments)}</span>
          </div>
          <div className="divide-y divide-indigo-100">
            {preview.ar_payments.filter(p => p.fund_source !== 'discount').map((p, i) => (
              <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                <div>
                  <span className="font-medium text-slate-800">{p.customer_name}</span>
                  <span className="text-xs text-blue-600 font-mono ml-2">{p.invoice_number}</span>
                  <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${p.fund_source === 'cashier' ? 'bg-emerald-100 text-emerald-700' : 'bg-violet-100 text-violet-700'}`}>
                    {p.method || (p.fund_source === 'cashier' ? 'Cash' : 'Digital')}
                  </span>
                  {p.interest_paid > 0 && (
                    <span className="ml-1 text-[10px] text-amber-600">INT: {formatPHP(p.interest_paid)}</span>
                  )}
                </div>
                <span className="font-mono font-semibold text-indigo-700">{formatPHP(p.amount_paid)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Interest & Penalty Invoices Issued Today ──────────────────── */}
      {(preview.interest_invoices_today || []).length > 0 && (
        <div className="rounded-xl border border-amber-200 overflow-hidden">
          <div className="px-4 py-2 bg-amber-50 flex items-center justify-between">
            <p className="text-xs font-bold uppercase tracking-wider text-amber-700">Interest & Penalty Invoices Issued Today</p>
            <span className="font-mono font-bold text-amber-700 text-sm">{formatPHP(preview.interest_invoices_today.reduce((s, i) => s + (i.grand_total || 0), 0))}</span>
          </div>
          <div className="divide-y divide-amber-100">
            {preview.interest_invoices_today.map((inv, i) => (
              <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                <div>
                  <span className="font-medium text-slate-700">{inv.customer_name}</span>
                  <span className="font-mono text-xs text-slate-400 ml-2">{inv.invoice_number}</span>
                  <span className={`ml-2 text-[9px] px-1.5 py-0.5 rounded font-medium ${inv.sale_type === 'penalty_charge' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                    {inv.sale_type === 'penalty_charge' ? 'Penalty' : inv.manual_interest ? 'Manual INT' : 'Interest'}
                  </span>
                </div>
                <div className="text-right">
                  <span className="font-mono font-semibold text-amber-700">{formatPHP(inv.grand_total)}</span>
                  {inv.amount_paid > 0 && (
                    <span className="text-[10px] text-emerald-600 ml-2">({formatPHP(inv.amount_paid)} collected)</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Fund Transfers ────────────────────────────────────────────── */}
      {(preview.fund_transfers_today || []).length > 0 && (
        <div className="rounded-xl border border-cyan-200 overflow-hidden">
          <div className="px-4 py-2 bg-cyan-50 flex items-center justify-between">
            <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">Fund Transfers</p>
            <span className="font-bold font-mono text-cyan-700 text-sm">{(preview.net_fund_transfers || 0) > 0 ? '+' : ''}{formatPHP(preview.net_fund_transfers || 0)}</span>
          </div>
          <div className="divide-y divide-cyan-100">
            {preview.fund_transfers_today.map((ft, i) => {
              const isCashierToSafe = ft.type === 'cashier_to_safe';
              const label = ft.type === 'capital_add'
                ? `Capital → ${ft.target_wallet === 'safe' ? 'Safe' : 'Cashier'}`
                : ft.type === 'safe_to_cashier' ? 'Safe → Cashier' : 'Cashier → Safe';
              return (
                <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                  <div>
                    <span className="font-medium text-slate-800">{label}</span>
                    {ft.note && <span className="text-xs text-slate-400 ml-2">{ft.note}</span>}
                    {ft.authorized_by && <span className="text-[10px] text-slate-400 ml-1">by {ft.authorized_by}</span>}
                  </div>
                  <span className={`font-mono font-semibold ${isCashierToSafe ? 'text-orange-600' : 'text-cyan-700'}`}>
                    {isCashierToSafe ? '-' : '+'}{formatPHP(ft.amount)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Cashier Expenses Detailed ─────────────────────────────────── */}
      {cashierExps.length > 0 && (
        <div className="rounded-xl border border-red-200 overflow-hidden">
          <div className="px-4 py-2 bg-red-50 flex items-center justify-between">
            <p className="text-xs font-bold uppercase tracking-wider text-red-600">Cashier Expenses (from drawer)</p>
            <span className="font-bold font-mono text-red-700 text-sm">{formatPHP(preview.total_cashier_expenses || 0)}</span>
          </div>
          <div className="divide-y divide-red-100">
            {cashierExps.map((e, i) => (
              <div key={i} className="px-4 py-2 text-sm flex items-center justify-between">
                <div>
                  <span className="font-medium text-slate-800">{e.description || e.category}</span>
                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-600 font-medium">{e.category}</span>
                  {e.employee_name && <span className="text-[11px] text-slate-500 ml-2">({e.employee_name})</span>}
                </div>
                <span className="font-mono font-semibold text-red-600">{formatPHP(e.amount)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Safe Expenses Detailed ────────────────────────────────────── */}
      {safeExps.length > 0 && (
        <div className="rounded-xl border border-amber-200 overflow-hidden">
          <div className="px-4 py-2 bg-amber-50 flex items-center justify-between">
            <p className="text-xs font-bold uppercase tracking-wider text-amber-700">Safe Expenses (from vault, not drawer)</p>
            <span className="font-bold font-mono text-amber-700 text-sm">{formatPHP(preview.total_safe_expenses || 0)}</span>
          </div>
          <div className="divide-y divide-amber-100">
            {safeExps.map((e, i) => (
              <div key={i} className="px-4 py-2 text-sm flex items-center justify-between">
                <div>
                  <span className="font-medium text-slate-800">{e.description || e.category}</span>
                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium">{e.category}</span>
                </div>
                <span className="font-mono font-semibold text-amber-700">{formatPHP(e.amount)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function DailyLogPage() {
  const { currentBranch, branches, hasPerm, user } = useAuth();
  const [date, setDate] = useState(localTodayStr());
  const [tab, setTab] = useState('log');
  const [logEntries, setLogEntries] = useState([]);
  const [cashEntries, setCashEntries] = useState([]);
  const [creditInvoices, setCreditInvoices] = useState([]);
  const [logSummary, setLogSummary] = useState(null);
  const [report, setReport] = useState(null);
  const [closing, setClosing] = useState(null);
  const [preview, setPreview] = useState(null);  // Z-Report preview
  const [expenseDialog, setExpenseDialog] = useState(false);
  const [expenseType, setExpenseType] = useState('other');
  const [expForm, setExpForm] = useState({ category: '', description: '', amount: 0, customer_id: '', tag: '', employee_id: '', employee_name: '' });
  const [customers, setCustomers] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [empDialog, setEmpDialog] = useState(false);
  const [empForm, setEmpForm] = useState({ name: '', position: '', phone: '' });
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const openCustStmt = (id, name) => { if (!id) return; setStmtCustomer({ id, name }); setStmtOpen(true); };

  // Close form state
  const [actualCash, setActualCash] = useState('');
  const [cashToSafe, setCashToSafe] = useState('');
  const [cashToDrawer, setCashToDrawer] = useState('');
  const [adminPin, setAdminPin] = useState('');
  const [varianceNotes, setVarianceNotes] = useState('');
  const [closing_loading, setClosingLoading] = useState(false);
  const [varianceHistory, setVarianceHistory] = useState([]);

  const fetchVarianceHistory = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get('/daily-variance-history', { params: { branch_id: currentBranch.id, limit: 60 } });
      setVarianceHistory(res.data.records || []);
    } catch {}
  }, [currentBranch]);

  // ── Z-Report Archive state ────────────────────────────────────────────────
  const [archiveRecords, setArchiveRecords] = useState([]);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveBranch, setArchiveBranch] = useState('all'); // 'all' or branch id
  const [archiveSearch, setArchiveSearch] = useState('');   // filter by date string
  const [zreportDialog, setZreportDialog] = useState(false);
  const [zreportData, setZreportData] = useState(null);
  const [zreportLoading, setZreportLoading] = useState(false);
  // Which Z-Report view is shown — normal (compact) or detailed (rich, with
  // discounts, price changes, AR collection methods, fund transfers, etc.).
  // Detail data is loaded lazily from /daily-close-preview the first time
  // the user flips to "Detailed" — keeps the initial dialog open fast.
  const [zreportMode, setZreportMode] = useState('normal');
  const [zreportDetail, setZreportDetail] = useState(null);
  const [zreportDetailLoading, setZreportDetailLoading] = useState(false);

  const fetchArchive = useCallback(async (branchId = 'all') => {
    setArchiveLoading(true);
    try {
      const params = { limit: 120 };
      if (branchId !== 'all') params.branch_id = branchId;
      const res = await api.get('/daily-variance-history', { params });
      setArchiveRecords(res.data.records || []);
    } catch { toast.error('Failed to load Z-report archive'); }
    setArchiveLoading(false);
  }, []);

  const openZreport = async (record) => {
    setZreportData(null);
    setZreportDetail(null);
    setZreportMode('normal');
    setZreportDialog(true);
    setZreportLoading(true);
    try {
      const res = await api.get(`/daily-close/${record.date}`, {
        params: { branch_id: record.branch_id }
      });
      setZreportData(res.data?.status === 'open' ? null : res.data);
    } catch { toast.error('Failed to load Z-report'); }
    setZreportLoading(false);
  };

  // ── Z-Report PDF helpers ─────────────────────────────────────────────────
  // We fetch the PDF as a blob using the axios-wired `api` instance (which
  // carries the JWT). `window.open(url)` and `<a download>` CAN'T attach
  // Authorization headers, which is why the previous "Download PDF" button
  // in the Close Wizard silently 401'd. These helpers cover both Print and
  // Download flows from any Z-Report view (Normal or Detailed) — the
  // `detailed` flag tells the backend which layout to render so Normal
  // prints/downloads the compact PDF and Detailed prints/downloads the
  // full Step-7-style breakdown.
  const fetchZReportBlob = useCallback(async (date, branchId, detailed = false) => {
    const res = await api.get('/reports/z-report-pdf', {
      params: { date, branch_id: branchId, detailed },
      responseType: 'blob',
    });
    return URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
  }, []);

  const printZReportPdf = useCallback(async (date, branchId, detailed = false) => {
    const toastId = toast.loading(`Preparing ${detailed ? 'detailed ' : ''}PDF for print…`);
    try {
      const objUrl = await fetchZReportBlob(date, branchId, detailed);
      const w = window.open(objUrl, '_blank');
      if (!w) {
        toast.error('Pop-up blocked — please allow pop-ups for this site to print.', { id: toastId });
        URL.revokeObjectURL(objUrl);
        return;
      }
      // Trigger the browser's native print dialog once the PDF is loaded.
      // Revoke the object URL after a delay so the print preview has time
      // to finish reading the blob.
      w.addEventListener('load', () => { try { w.focus(); w.print(); } catch { /* same-origin retries silently */ } });
      setTimeout(() => URL.revokeObjectURL(objUrl), 60_000);
      toast.dismiss(toastId);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Could not generate Z-Report PDF', { id: toastId });
    }
  }, [fetchZReportBlob]);

  const downloadZReportPdf = useCallback(async (date, branchId, branchName, detailed = false) => {
    const toastId = toast.loading(`Building ${detailed ? 'detailed ' : ''}PDF…`);
    try {
      const objUrl = await fetchZReportBlob(date, branchId, detailed);
      const a = document.createElement('a');
      a.href = objUrl;
      const safeBranch = (branchName || 'branch').replace(/\s+/g, '_');
      a.download = `ZReport${detailed ? '_DETAILED' : ''}_${safeBranch}_${date}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(objUrl), 2_000);
      toast.success(`${detailed ? 'Detailed ' : ''}PDF downloaded`, { id: toastId });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Could not download Z-Report PDF', { id: toastId });
    }
  }, [fetchZReportBlob]);

  // Lazy-loads the rich preview payload used by the "Detailed" Z-Report view.
  // Same endpoint Close Wizard Step 7 uses — recomputes from sales_log +
  // invoices + fund_transfers so it works on any historical closed day.
  const loadZreportDetail = useCallback(async () => {
    if (!zreportData || zreportDetail || zreportDetailLoading) return;
    setZreportDetailLoading(true);
    try {
      const res = await api.get('/daily-close-preview', {
        params: { date: zreportData.date, branch_id: zreportData.branch_id }
      });
      setZreportDetail(res.data || null);
    } catch { toast.error('Failed to load detailed breakdown'); }
    setZreportDetailLoading(false);
  }, [zreportData, zreportDetail, zreportDetailLoading]);

  function r2(n) { return Math.round((parseFloat(n) || 0) * 100) / 100; }

  // Computed values
  const expectedCounter = preview?.expected_counter || 0;
  const actualNum = r2(actualCash);
  const overShort = actualCash !== '' ? r2(actualNum - expectedCounter) : null;
  const cashToDrawerNum = r2(cashToDrawer);
  const cashToSafeNum = r2(cashToSafe);

  const fetchLog = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get('/daily-log', { params: { date, branch_id: currentBranch.id } });
      setLogEntries(res.data.entries || []);
      setCashEntries(res.data.cash_entries || []);
      setCreditInvoices(res.data.credit_invoices || []);
      setLogSummary(res.data.summary || null);
    } catch {}
  }, [date, currentBranch]);

  // Walk-in sales = all non-credit entries with sequential running total
  // Digital payments (GCash, Maya, etc.) are included in the total but tracked separately for reporting
  const CASH_METHODS = React.useMemo(() => new Set(['cash', 'check', 'cheque', 'credit', 'partial', 'split']), []);
  const walkinEntries = React.useMemo(() => {
    const filtered = logEntries.filter(e => (e.payment_method || 'cash').toLowerCase() !== 'credit');
    let running = 0;
    let digitalRunning = 0;
    return filtered.map(e => {
      const pm = (e.payment_method || 'cash').toLowerCase();
      const lt = parseFloat(e.line_total || 0);
      const isDigital = !CASH_METHODS.has(pm);
      running += lt;
      if (isDigital) digitalRunning += lt;
      return { ...e, walkin_running_total: Math.round(running * 100) / 100, _is_digital: isDigital, _digital_running: Math.round(digitalRunning * 100) / 100 };
    });
  }, [logEntries, CASH_METHODS]);
  const totalWalkinSales = walkinEntries.length > 0
    ? walkinEntries[walkinEntries.length - 1].walkin_running_total : 0;
  const totalDigitalSales = walkinEntries.length > 0
    ? walkinEntries[walkinEntries.length - 1]._digital_running : 0;

  const fetchReport = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get('/daily-report', { params: { date, branch_id: currentBranch.id } });
      setReport(res.data);
    } catch {}
  }, [date, currentBranch]);

  const fetchPreview = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get('/daily-close-preview', { params: { date, branch_id: currentBranch.id } });
      setPreview(res.data);
    } catch {}
  }, [date, currentBranch]);

  const fetchClosing = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get(`/daily-close/${date}`, { params: { branch_id: currentBranch.id } });
      setClosing(res.data);
    } catch {}
  }, [date, currentBranch]);

  useEffect(() => { fetchLog(); fetchReport(); fetchClosing(); fetchPreview(); }, [fetchLog, fetchReport, fetchClosing, fetchPreview]);
  useEffect(() => { if (tab === 'variance') fetchVarianceHistory(); }, [tab, fetchVarianceHistory]);
  useEffect(() => {
    if (tab === 'archive') {
      const bid = currentBranch ? currentBranch.id : 'all';
      setArchiveBranch(bid);
      fetchArchive(bid);
    }
  }, [tab]); // eslint-disable-line
  useEffect(() => {
    api.get('/customers', { params: { limit: 500 } }).then(r => setCustomers(r.data.customers)).catch(() => {});
    if (currentBranch) api.get('/employees', { params: { branch_id: currentBranch.id } }).then(r => setEmployees(r.data)).catch(() => {});
  }, [currentBranch]);

  const handleClose = async () => {
    if (actualCash === '') { toast.error('Enter the actual cash count first'); return; }
    if (!cashToDrawer && cashToDrawer !== 0) { toast.error('Enter how much stays in the register'); return; }
    if (r2(cashToDrawerNum + cashToSafeNum) > r2(actualNum) + 0.01) {
      toast.error('Transfer to Vault + Opening Float cannot exceed actual count'); return;
    }
    if (!window.confirm(`Close accounts for ${date}? This cannot be undone.`)) return;
    setClosingLoading(true);
    try {
      const res = await api.post('/daily-close', {
        date, branch_id: currentBranch.id,
        actual_cash: actualNum,
        cash_to_safe: cashToSafeNum,
        cash_to_drawer: cashToDrawerNum,
        admin_pin: adminPin,
        variance_notes: varianceNotes,
      });
      toast.success('Day closed successfully!');
      setClosing(res.data);
      setAdminPin('');
    } catch (e) { toast.error(e.response?.data?.detail || 'Error closing day'); }
    setClosingLoading(false);
  };

  const handleExpense = async () => {
    try {
      if (expenseType === 'farm') {
        await api.post('/expenses/farm', { ...expForm, branch_id: currentBranch?.id, date });
      } else if (expenseType === 'advance') {
        await api.post('/expenses/employee-advance', { ...expForm, branch_id: currentBranch?.id, date });
      } else {
        await api.post('/expenses', { ...expForm, branch_id: currentBranch?.id, date });
      }
      toast.success('Expense recorded');
      setExpenseDialog(false);
      fetchReport();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const handleAddEmployee = async () => {
    try {
      await api.post('/employees', { ...empForm, branch_id: currentBranch?.id });
      toast.success('Employee added');
      setEmpDialog(false);
      api.get('/employees', { params: { branch_id: currentBranch?.id } }).then(r => setEmployees(r.data));
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const isClosed = closing?.status === 'closed';

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="daily-log-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Daily Operations</h1>
          <p className="text-sm text-slate-500">{currentBranch?.name} &middot; Sales Log, Profit & Day Close</p>
        </div>
        <div className="flex items-center gap-3">
          <Input type="date" value={date} onChange={e => setDate(e.target.value)} className="w-40 h-9" data-testid="daily-date-picker" />
          {isClosed && <Badge className="bg-red-100 text-red-700"><Lock size={12} className="mr-1" /> Day Closed</Badge>}
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="log" data-testid="tab-log"><ClipboardList size={14} className="mr-1" /> Sales Log</TabsTrigger>
          <TabsTrigger value="profit" data-testid="tab-profit"><TrendingUp size={14} className="mr-1" /> Daily Profit</TabsTrigger>
          <TabsTrigger value="close" data-testid="tab-close"><Lock size={14} className="mr-1" /> Close Accounts</TabsTrigger>
          <TabsTrigger value="variance" data-testid="tab-variance"><FileWarning size={14} className="mr-1" /> Variance Log</TabsTrigger>
          <TabsTrigger value="archive" data-testid="tab-archive"><Archive size={14} className="mr-1" /> Z-Report Archive</TabsTrigger>
        </TabsList>

        {/* ═══ SEQUENTIAL SALES LOG — Notebook Style ═══════════════ */}
        <TabsContent value="log" className="mt-4 space-y-0 print:mt-0" data-testid="sales-log-tab">
          {/* Header bar */}
          <div className="flex items-center justify-between mb-4 print:hidden">
            <div>
              <p className="text-sm text-slate-500">
                <span className="font-medium text-slate-700">{walkinEntries.length}</span> walk-in sales
                {creditInvoices.length > 0 && <> · <span className="font-medium text-amber-600">{creditInvoices.length}</span> credit</>}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => window.print()} data-testid="print-log">
              <Printer size={14} className="mr-1" /> Print
            </Button>
          </div>

          {/* ── SECTION 1: CASH SALES ──────────────────────────────── */}
          <div className="border border-slate-200 rounded-xl overflow-hidden print:border-black print:rounded-none">
            {/* Section header */}
            <div className="bg-[#1A4D2E] text-white px-4 py-2.5 flex items-center justify-between print:bg-black">
              <div>
                <span className="font-bold text-sm tracking-wide uppercase">Non-Credit Sales</span>
                <span className="text-emerald-200 text-xs ml-3">{currentBranch?.name} · {date}</span>
              </div>
              {logSummary && (
                <span className="font-mono font-bold text-emerald-200 text-sm">
                  {walkinEntries.length} transactions
                </span>
              )}
            </div>

            {/* Walk-in sales table — all non-credit sales in sequential order */}
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-xs uppercase text-slate-500 tracking-wide">
                  <th className="px-3 py-2 text-center w-10 font-semibold">#</th>
                  <th className="px-3 py-2 text-left w-16 font-semibold">Time</th>
                  <th className="px-3 py-2 text-left font-semibold">Product</th>
                  <th className="px-3 py-2 text-left font-semibold">Customer</th>
                  <th className="px-3 py-2 text-left font-semibold">Invoice</th>
                  <th className="px-3 py-2 text-center w-16 font-semibold">Payment</th>
                  <th className="px-3 py-2 text-right w-12 font-semibold">Qty</th>
                  <th className="px-3 py-2 text-right w-24 font-semibold">Unit Price</th>
                  <th className="px-3 py-2 text-right w-20 font-semibold">Disc</th>
                  <th className="px-3 py-2 text-right w-24 font-semibold">Total</th>
                  <th className="px-3 py-2 text-right w-28 font-semibold text-[#1A4D2E]">Running Total</th>
                </tr>
              </thead>
              <tbody>
                {walkinEntries.length === 0 && (
                  <tr>
                    <td colSpan={11} className="text-center py-10 text-slate-400 italic">
                      No walk-in sales recorded for {date}
                    </td>
                  </tr>
                )}
                {walkinEntries.map((e, idx) => {
                  const pm = (e.payment_method || 'cash').toLowerCase();
                  // Split entries: show as "Cash + [Platform]" instead of "split"
                  let pmLabel = pm;
                  let pmColor = pm === 'cash' ? 'bg-emerald-100 text-emerald-700'
                    : pm === 'gcash' ? 'bg-blue-100 text-blue-700'
                    : pm === 'maya' ? 'bg-green-100 text-green-700'
                    : pm === 'partial' ? 'bg-teal-100 text-teal-700'
                    : 'bg-violet-100 text-violet-700';
                  if (pm === 'split') {
                    pmLabel = `Cash+${e.split_digital_platform || 'Digital'}`;
                    pmColor = 'bg-indigo-100 text-indigo-700';
                  }
                  return (
                    <tr key={e.id || idx}
                      className={`border-b border-slate-100 last:border-0 hover:bg-slate-50/50 transition-colors ${idx % 2 === 0 ? '' : 'bg-slate-50/30'}`}>
                      <td className="px-3 py-2 text-center font-mono text-xs text-slate-400 font-medium">{e.sequence}</td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-500">{e.time}</td>
                      <td className="px-3 py-2 font-medium text-slate-800">{e.product_name}</td>
                      <td className="px-3 py-2 text-slate-500 text-xs">{e.customer_id ? <button onClick={() => openCustStmt(e.customer_id, e.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{e.customer_name || 'Walk-in'}</button> : (e.customer_name || 'Walk-in')}</td>
                      <td className="px-3 py-2 font-mono text-xs"><button className="text-blue-600 hover:underline hover:text-blue-800" onClick={() => { setSelectedInvoiceNumber(e.invoice_number); setInvoiceModalOpen(true); }}>{e.invoice_number}</button></td>
                      <td className="px-3 py-2 text-center">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase ${pmColor}`}>{pmLabel}</span>
                      </td>
                      <td className="px-3 py-2 text-right text-slate-600">{e.quantity} <span className="text-slate-400 text-xs">{e.unit || ''}</span></td>
                      <td className="px-3 py-2 text-right font-mono">{formatPHP(e.unit_price)}</td>
                      <td className="px-3 py-2 text-right text-slate-400 text-xs">{e.discount > 0 ? formatPHP(e.discount) : '—'}</td>
                      <td className="px-3 py-2 text-right font-semibold font-mono">{formatPHP(e.line_total)}</td>
                      <td className="px-3 py-2 text-right font-bold font-mono text-[#1A4D2E]">{formatPHP(e.walkin_running_total)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Walk-in subtotals + grand total */}
            {logSummary && walkinEntries.length > 0 && (
              <div className="border-t-2 border-slate-200 bg-slate-50 px-4 py-3">
                <div className="flex flex-wrap gap-x-6 gap-y-1 mb-2">
                  {(() => {
                    const catTotals = {};
                    walkinEntries.forEach(e => {
                      const cat = e.category || 'General';
                      catTotals[cat] = (catTotals[cat] || 0) + parseFloat(e.line_total || 0);
                    });
                    return Object.entries(catTotals).sort(([,a],[,b]) => b - a).map(([cat, total]) => (
                      <div key={cat} className="flex items-center gap-2 text-xs">
                        <span className="text-slate-500">{cat}:</span>
                        <span className="font-semibold font-mono">{formatPHP(total)}</span>
                      </div>
                    ));
                  })()}
                </div>
                {/* Payment method breakdown */}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mb-2 border-t border-slate-200 pt-2">
                  {(() => {
                    const pmTotals = {};
                    walkinEntries.forEach(e => {
                      const pm = (e.payment_method || 'cash').toUpperCase();
                      pmTotals[pm] = (pmTotals[pm] || 0) + parseFloat(e.line_total || 0);
                    });
                    return Object.entries(pmTotals).sort(([,a],[,b]) => b - a).map(([pm, total]) => (
                      <div key={pm} className="flex items-center gap-1.5 text-xs">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                          pm === 'CASH' ? 'bg-emerald-100 text-emerald-700'
                          : pm === 'GCASH' ? 'bg-blue-100 text-blue-700'
                          : pm === 'MAYA' ? 'bg-green-100 text-green-700'
                          : pm === 'SPLIT' ? 'bg-indigo-100 text-indigo-700'
                          : pm === 'PARTIAL' ? 'bg-teal-100 text-teal-700'
                          : 'bg-violet-100 text-violet-700'
                        }`}>{pm}</span>
                        <span className="font-semibold font-mono">{formatPHP(total)}</span>
                      </div>
                    ));
                  })()}
                </div>
                <div className="flex justify-between items-center border-t border-slate-200 pt-2">
                  <span className="text-sm font-bold text-slate-700 uppercase tracking-wide">Total Sales</span>
                  <span className="font-mono font-bold text-lg text-[#1A4D2E]">{formatPHP(totalWalkinSales)}</span>
                </div>
                {totalDigitalSales > 0 && (
                  <div className="flex justify-between items-center pt-1 text-xs text-violet-500">
                    <span>(includes {formatPHP(totalDigitalSales)} digital)</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── SECTION 2: ACCOUNTS RECEIVABLE ─────────────────────── */}
          {creditInvoices.length > 0 && (
            <div className="border border-amber-200 rounded-xl overflow-hidden mt-4 print:border-black print:rounded-none print:mt-6">
              {/* AR section header */}
              <div className="bg-amber-600 text-white px-4 py-2.5 flex items-center justify-between print:bg-black">
                <div>
                  <span className="font-bold text-sm tracking-wide uppercase">Accounts Receivable — New Credit Today</span>
                  <span className="text-amber-100 text-xs ml-3">{creditInvoices.length} customer{creditInvoices.length !== 1 ? 's' : ''}</span>
                </div>
                <span className="font-mono font-bold text-amber-100 text-sm">
                  {formatPHP(logSummary?.total_credit || 0)}
                </span>
              </div>

              {/* Per-customer credit details */}
              <div className="divide-y divide-amber-100">
                {creditInvoices.map((inv, ci) => {
                  const items = inv.items || [];
                  const invoiceTotal = parseFloat(inv.grand_total || 0);
                  const amountPaid = parseFloat(inv.amount_paid || 0);
                  const balance = parseFloat(inv.balance || invoiceTotal - amountPaid);
                  return (
                    <div key={inv.id || ci} className="p-4">
                      {/* Customer header */}
                      <div className="flex items-start justify-between mb-2">
                        <div>
                          <span className="font-bold text-slate-800 text-base">{inv.customer_id ? <button onClick={() => openCustStmt(inv.customer_id, inv.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{inv.customer_name || 'Unknown Customer'}</button> : (inv.customer_name || 'Unknown Customer')}</span>
                          <div className="flex items-center gap-2 mt-0.5">
                            <button className="font-mono text-xs text-blue-600 hover:underline hover:text-blue-800" onClick={() => { setSelectedInvoiceNumber(inv.invoice_number); setInvoiceModalOpen(true); }}>{inv.invoice_number}</button>
                            <Badge className={`text-[9px] border-0 ${inv.payment_type === 'partial' ? 'bg-blue-100 text-blue-700' : 'bg-amber-100 text-amber-700'}`}>
                              {inv.payment_type === 'partial' ? 'PARTIAL PAYMENT' : 'FULL CREDIT'}
                            </Badge>
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="font-mono font-bold text-slate-800">{formatPHP(balance)}</div>
                          {amountPaid > 0 && (
                            <div className="text-xs text-slate-500">of {formatPHP(invoiceTotal)} · Paid: {formatPHP(amountPaid)}</div>
                          )}
                        </div>
                      </div>

                      {/* Items list — notebook style */}
                      <div className="ml-2">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-xs text-slate-400 border-b border-slate-100">
                              <th className="pb-1 text-left w-6 font-normal">#</th>
                              <th className="pb-1 text-left font-normal">Product</th>
                              <th className="pb-1 text-right w-16 font-normal">Qty</th>
                              <th className="pb-1 text-right w-24 font-normal">Unit Price</th>
                              <th className="pb-1 text-right w-20 font-normal">Disc</th>
                              <th className="pb-1 text-right w-24 font-normal">Amount</th>
                            </tr>
                          </thead>
                          <tbody>
                            {items.map((item, ii) => (
                              <tr key={ii} className="border-b border-slate-50 last:border-0">
                                <td className="py-1 text-slate-300 text-xs">{ii + 1}.</td>
                                <td className="py-1 font-medium text-slate-700">{item.product_name}</td>
                                <td className="py-1 text-right text-slate-600">{item.quantity}</td>
                                <td className="py-1 text-right font-mono">{formatPHP(item.rate || item.price || 0)}</td>
                                <td className="py-1 text-right text-slate-400 text-xs">
                                  {parseFloat(item.discount_amount || 0) > 0 ? formatPHP(item.discount_amount) : '—'}
                                </td>
                                <td className="py-1 text-right font-semibold font-mono">{formatPHP(item.total)}</td>
                              </tr>
                            ))}
                          </tbody>
                          <tfoot>
                            <tr className="border-t border-amber-200 bg-amber-50/50">
                              <td colSpan={5} className="pt-1.5 text-sm font-semibold text-amber-800">
                                Total Credit — {inv.customer_name}
                              </td>
                              <td className="pt-1.5 text-right font-bold font-mono text-amber-800">{formatPHP(balance)}</td>
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* AR grand total */}
              <div className="border-t-2 border-amber-200 bg-amber-50 px-4 py-3 flex justify-between items-center">
                <span className="text-sm font-bold text-amber-800 uppercase tracking-wide">Total New Credit Today</span>
                <span className="font-mono font-bold text-lg text-amber-700">{formatPHP(logSummary?.total_credit || 0)}</span>
              </div>
            </div>
          )}

          {/* ── SUMMARY STRIP ──────────────────────────────────────── */}
          {logSummary && (walkinEntries.length > 0 || creditInvoices.length > 0) && (
            <div className="mt-4 bg-slate-800 text-white rounded-xl px-5 py-4 flex flex-wrap gap-6 items-center justify-between print:bg-black">
              <div className="flex gap-6">
                <div>
                  <div className="text-xs text-slate-400 uppercase tracking-wide">Total Sales</div>
                  <div className="font-mono font-bold text-emerald-400">{formatPHP(totalWalkinSales)}</div>
                </div>
                {totalDigitalSales > 0 && (
                  <div>
                    <div className="text-xs text-slate-400 uppercase tracking-wide">Digital Portion</div>
                    <div className="font-mono font-bold text-violet-400">{formatPHP(totalDigitalSales)}</div>
                  </div>
                )}
                {logSummary.total_credit > 0 && (
                  <div>
                    <div className="text-xs text-slate-400 uppercase tracking-wide">New Credit</div>
                    <div className="font-mono font-bold text-amber-400">{formatPHP(logSummary.total_credit)}</div>
                  </div>
                )}
              </div>
              <div className="text-right">
                <div className="text-xs text-slate-400 uppercase tracking-wide">Total Sales Today</div>
                <div className="font-mono font-bold text-xl">{formatPHP(logSummary?.grand_total || 0)}</div>
              </div>
            </div>
          )}

          {/* Empty state */}
          {walkinEntries.length === 0 && creditInvoices.length === 0 && (
            <div className="text-center py-16 text-slate-400">
              <ClipboardList size={40} className="mx-auto mb-3 opacity-30" />
              <p>No sales recorded for {date}</p>
              <p className="text-xs mt-1">Sales will appear here as they are made</p>
            </div>
          )}
        </TabsContent>

        {/* DAILY PROFIT */}
        <TabsContent value="profit" className="mt-4 space-y-4">
          {report ? (
            <>
              {/* KPI row */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                {[
                  { label: 'Sales Today', value: report.new_sales_today, color: 'text-emerald-600', bg: 'bg-emerald-50', icon: ArrowUp },
                  { label: 'COGS', value: report.total_cogs, color: 'text-slate-600', bg: 'bg-slate-50', icon: ArrowDown },
                  { label: 'Net Profit', value: report.net_profit, color: report.net_profit >= 0 ? 'text-emerald-700' : 'text-red-700', bg: report.net_profit >= 0 ? 'bg-emerald-50' : 'bg-red-50', icon: DollarSign },
                ].map((kpi, i) => (
                  <Card key={i} className="border-slate-200"><CardContent className={`p-4 ${kpi.bg}`}>
                    <div className="flex items-center gap-1 mb-1"><kpi.icon size={14} className={kpi.color} /><span className="text-xs text-slate-500 uppercase">{kpi.label}</span></div>
                    <p className={`text-xl font-bold ${kpi.color}`} style={{ fontFamily: 'Manrope' }}>{formatPHP(kpi.value)}</p>
                  </CardContent></Card>
                ))}
              </div>

              {/* Formula explanation */}
              <div className="px-4 py-2 bg-slate-50 rounded-lg border border-slate-200 text-xs text-slate-500 flex items-center gap-2 flex-wrap">
                <span>Net Profit =</span>
                <span className="text-emerald-600 font-medium">Sales {formatPHP(report.new_sales_today)}</span>
                <span>−</span>
                <span className="text-slate-600 font-medium">COGS {formatPHP(report.total_cogs)}</span>
                <span>=</span>
                <span className={`font-bold ${report.net_profit >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>{formatPHP(report.net_profit)}</span>
              </div>

              {/* Sales by Category */}
              <Card className="border-slate-200">
                <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold" style={{ fontFamily: 'Manrope' }}>Sales by Category</CardTitle></CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {Object.entries(report.sales_by_category || {}).map(([cat, data]) => (
                      <div key={cat} className="flex justify-between items-center p-2 rounded bg-slate-50">
                        <span className="font-medium text-sm">{cat}</span>
                        <span className="font-bold">{formatPHP(data.total)}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Real Expenses (P&L) */}
              <Card className="border-slate-200">
                <CardHeader className="pb-2 flex flex-row items-center justify-between">
                  <div>
                    <CardTitle className="text-sm font-semibold" style={{ fontFamily: 'Manrope' }}>Operating Expenses</CardTitle>
                    <p className="text-[10px] text-slate-400 mt-0.5">Tracked for monthly review — not included in daily Net Profit</p>
                  </div>
                  {!isClosed && (
                    <div className="flex gap-1">
                      <Button size="sm" variant="outline" onClick={() => { setExpenseType('other'); setExpForm({ category: '', description: '', amount: 0 }); setExpenseDialog(true); }}><Plus size={12} className="mr-1" /> Expense</Button>
                    </div>
                  )}
                </CardHeader>
                <CardContent>
                  {report.expenses?.length ? report.expenses.map((e, i) => (
                    <div key={i} className="flex justify-between items-center p-2 rounded bg-slate-50 mb-1">
                      <div><Badge variant="outline" className="text-[10px] mr-2">{e.category}</Badge><span className="text-sm">{e.description}</span></div>
                      <span className="font-bold text-red-600">{formatPHP(e.amount)}</span>
                    </div>
                  )) : <p className="text-sm text-slate-400">No operational expenses today</p>}
                  {report.expenses?.length > 0 && (
                    <div className="flex justify-between font-bold text-sm pt-2 border-t border-slate-200 mt-2">
                      <span>Total Expenses</span>
                      <span className="text-red-600">{formatPHP(report.total_expenses)}</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Inventory Purchases — balance sheet, NOT P&L */}
              {(report.inventory_expenses?.length > 0) && (
                <Card className="border-orange-200 bg-orange-50/30">
                  <CardHeader className="pb-2">
                    <div>
                      <CardTitle className="text-sm font-semibold text-orange-800" style={{ fontFamily: 'Manrope' }}>
                        Inventory Purchases (PO/Supplier Payments)
                      </CardTitle>
                      <p className="text-[10px] text-orange-600 mt-0.5">
                        Cash → Inventory asset. NOT included in Net Profit — COGS covers cost when items are sold.
                      </p>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {report.inventory_expenses.map((e, i) => (
                      <div key={i} className="flex justify-between items-center p-2 rounded bg-white mb-1 border border-orange-100">
                        <div>
                          <Badge variant="outline" className="text-[10px] mr-2 border-orange-300 text-orange-700">{e.category}</Badge>
                          <span className="text-sm">{e.description}</span>
                          {e.fund_source === 'safe' && <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 py-0.5 rounded font-semibold">SAFE</span>}
                        </div>
                        <span className="font-bold text-orange-700">{formatPHP(e.amount)}</span>
                      </div>
                    ))}
                    <div className="flex justify-between font-bold text-sm pt-2 border-t border-orange-200 mt-2">
                      <span className="text-orange-800">Total Inventory Purchases</span>
                      <span className="text-orange-700">{formatPHP(report.total_inventory_expenses)}</span>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Credits Extended Today — AR section, NOT included in P&L */}
              {((report.credit_expenses?.length > 0) || (report.advance_expenses?.length > 0)) && (
                <Card className="border-blue-200 bg-blue-50/30">
                  <CardHeader className="pb-2 flex flex-row items-center justify-between">
                    <div>
                      <CardTitle className="text-sm font-semibold text-blue-800" style={{ fontFamily: 'Manrope' }}>
                        Credits Extended Today
                      </CardTitle>
                      <p className="text-[10px] text-blue-600 mt-0.5">
                        Money out that comes BACK — these are receivables/assets, NOT included in Net Profit
                      </p>
                    </div>
                    {!isClosed && (
                      <div className="flex gap-1">
                        <Button size="sm" variant="outline" className="border-blue-200 text-blue-700"
                          onClick={() => { setExpenseType('advance'); setExpForm({ amount: 0, employee_id: '', employee_name: '' }); setExpenseDialog(true); }}>
                          CA
                        </Button>
                        <Button size="sm" variant="outline" className="border-blue-200 text-blue-700"
                          onClick={() => { setExpenseType('farm'); setExpForm({ amount: 0, customer_id: '', tag: '' }); setExpenseDialog(true); }}>
                          Farm
                        </Button>
                      </div>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {/* Customer Credits (Cash Out + Farm Expense) */}
                    {report.credit_expenses?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-blue-700 mb-1 uppercase tracking-wide">Customer Credits (AR)</p>
                        {report.credit_expenses.map((e, i) => (
                          <div key={i} className="flex justify-between items-center p-2 rounded bg-white border border-blue-100 mb-1">
                            <div>
                              <Badge className="text-[10px] mr-2 bg-blue-100 text-blue-700 border-0">{e.category}</Badge>
                              <span className="text-sm">{e.description}</span>
                            </div>
                            <div className="text-right">
                              <span className="font-bold text-blue-700">{formatPHP(e.amount)}</span>
                              <p className="text-[10px] text-blue-400">Owed back</p>
                            </div>
                          </div>
                        ))}
                        <div className="flex justify-between text-sm font-medium text-blue-700 pt-1 border-t border-blue-200">
                          <span>Total Customer Credits</span>
                          <span>{formatPHP(report.total_credit_expenses)}</span>
                        </div>
                      </div>
                    )}
                    {/* Employee Advances */}
                    {report.advance_expenses?.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-blue-700 mb-1 uppercase tracking-wide">Employee Advances (Asset)</p>
                        {report.advance_expenses.map((e, i) => (
                          <div key={i} className={`p-2 rounded border mb-1 ${e.is_over_ca ? 'bg-red-50 border-red-200' : 'bg-white border-blue-100'}`}>
                            <div className="flex justify-between items-center">
                              <div>
                                <Badge className="text-[10px] mr-2 bg-indigo-100 text-indigo-700 border-0">{e.category}</Badge>
                                <span className="text-sm">{e.description || e.employee_name}</span>
                              </div>
                              <div className="text-right">
                                <span className="font-bold text-indigo-700">{formatPHP(e.amount)}</span>
                                <p className="text-[10px] text-indigo-400">Salary deduction</p>
                              </div>
                            </div>
                            {e.monthly_ca_total != null && (
                              <div className={`text-[10px] mt-1 ${e.is_over_ca ? 'text-red-600' : 'text-amber-600'}`}>
                                Monthly total: {formatPHP(e.monthly_ca_total)}
                                {e.monthly_ca_limit > 0 && <span> / Limit: {formatPHP(e.monthly_ca_limit)}</span>}
                                {e.is_over_ca && <span className="font-bold ml-1">OVER CA</span>}
                                {e.manager_approved_by && <span className="ml-1 text-violet-600">(Approved: {e.manager_approved_by})</span>}
                              </div>
                            )}
                          </div>
                        ))}
                        <div className="flex justify-between text-sm font-medium text-indigo-700 pt-1 border-t border-blue-200">
                          <span>Total Advances</span>
                          <span>{formatPHP(report.total_advance_expenses)}</span>
                        </div>
                      </div>
                    )}
                    {/* Total */}
                    <div className="flex justify-between font-bold text-sm pt-2 border-t-2 border-blue-200">
                      <span className="text-blue-800">Total Credits Extended</span>
                      <span className="text-blue-700">{formatPHP((report.total_credit_expenses || 0) + (report.total_advance_expenses || 0))}</span>
                    </div>
                  </CardContent>
                </Card>
              )}
            </>
          ) : <p className="text-center py-8 text-slate-400">Loading report...</p>}
        </TabsContent>

        {/* ═══ CLOSE ACCOUNTS / Z-REPORT ═══════════════════════════════ */}
        <TabsContent value="close" className="mt-4 space-y-0 print:mt-0">
          {isClosed ? (
            /* ── CLOSED: Show the Z-Report ── */
            <ZReport
              data={closing}
              branchName={currentBranch?.name}
              onPrint={() => printZReportPdf(closing.date, closing.branch_id || currentBranch?.id, false)}
              onDownloadPdf={() => downloadZReportPdf(closing.date, closing.branch_id || currentBranch?.id, currentBranch?.name, false)}
            />
          ) : (
            /* ── OPEN: Cash Reconciliation Form ── */
            <div className="space-y-4">
              {!preview && <p className="text-center py-8 text-slate-400">Loading day data...</p>}
              {preview && (
                <>
                  {/* ── OPENING ─────────────────────────────────── */}
                  <SectionCard title="Opening">
                    <div className="grid grid-cols-2 gap-3">
                      <InfoRow label="Total Amount in Safe" value={formatPHP(preview.safe_balance)} bold className="text-slate-500" />
                      <InfoRow label="Opening Float" value={formatPHP(preview.starting_float)} bold className="text-emerald-600" />
                    </div>
                  </SectionCard>

                  {/* ── CASH SALES BY CATEGORY ──────────────────── */}
                  <SectionCard title={`Sales Today — ${formatPHP(totalWalkinSales)}${totalDigitalSales > 0 ? ` (includes ${formatPHP(totalDigitalSales)} digital)` : ''}`} accent="emerald">
                    {walkinEntries.length > 0 ? (
                      <div className="space-y-1">
                        {(() => {
                          // Group by category
                          const cats = {};
                          walkinEntries.forEach(e => {
                            const cat = e.category || 'General';
                            cats[cat] = (cats[cat] || 0) + parseFloat(e.line_total || 0);
                          });
                          return Object.entries(cats).sort(([,a],[,b]) => b - a).map(([cat, total]) => (
                            <div key={cat} className="flex justify-between text-sm py-1 border-b border-slate-100 last:border-0">
                              <span className="text-slate-600">{cat}</span>
                              <span className="font-semibold font-mono">{formatPHP(total)}</span>
                            </div>
                          ));
                        })()}
                        {/* Payment method breakdown */}
                        <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 border-t border-slate-100">
                          {(() => {
                            const pms = {};
                            walkinEntries.forEach(e => {
                              const pm = (e.payment_method || 'cash').toUpperCase();
                              pms[pm] = (pms[pm] || 0) + parseFloat(e.line_total || 0);
                            });
                            return Object.entries(pms).sort(([,a],[,b]) => b - a).map(([pm, total]) => (
                              <span key={pm} className="text-xs text-slate-500">{pm}: <strong className="font-mono">{formatPHP(total)}</strong></span>
                            ));
                          })()}
                        </div>
                        <div className="flex justify-between font-bold text-sm pt-1 border-t border-slate-200">
                          <span>Total Sales</span>
                          <span className="text-emerald-700">{formatPHP(totalWalkinSales)}</span>
                        </div>
                        {totalDigitalSales > 0 && (
                          <div className="flex justify-between text-xs pt-1 text-violet-500">
                            <span>(includes {formatPHP(totalDigitalSales)} digital)</span>
                          </div>
                        )}
                      </div>
                    ) : <p className="text-sm text-slate-400">No walk-in sales today</p>}
                  </SectionCard>

                  {/* ── NEW CREDIT TODAY (info only) ─────────────── */}
                  {preview.credit_sales_today?.length > 0 && (
                    <SectionCard title={`New Credit Sales Today — ${formatPHP(preview.total_credit_today)}`} accent="amber" note="Info only — not counted as cash received">
                      <div className="space-y-1">
                        {preview.credit_sales_today.map((c, i) => (
                          <div key={i} className="flex items-center justify-between text-sm py-1 border-b border-slate-100 last:border-0">
                            <div>
                              <span className="font-medium">{c.customer_id ? <button onClick={() => openCustStmt(c.customer_id, c.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{c.customer_name}</button> : c.customer_name}</span>
                              <button className="text-blue-600 text-xs ml-2 hover:underline" onClick={() => { setSelectedInvoiceNumber(c.invoice_number); setInvoiceModalOpen(true); }}>{c.invoice_number}</button>
                            </div>
                            <div className="text-right">
                              <span className="font-mono text-amber-700">{formatPHP(c.balance)}</span>
                              {c.grand_total !== c.balance && (
                                <span className="text-[10px] text-slate-400 ml-1">(of {formatPHP(c.grand_total)})</span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </SectionCard>
                  )}

                  {/* ── AR PAYMENTS RECEIVED ─────────────────────── */}
                  <SectionCard title={`AR Payments Received Today — ${formatPHP(preview.total_ar_received)}`} accent="blue">
                    {preview.ar_payments?.length ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead><tr className="bg-slate-50 border-b border-slate-200 text-xs uppercase text-slate-500">
                            <th className="px-3 py-2 text-left">Customer</th>
                            <th className="px-3 py-2 text-right">Bal Before</th>
                            <th className="px-3 py-2 text-right">Interest</th>
                            <th className="px-3 py-2 text-right">Penalty</th>
                            <th className="px-3 py-2 text-right font-semibold text-blue-600">Cash Paid</th>
                            <th className="px-3 py-2 text-right">Remaining</th>
                          </tr></thead>
                          <tbody>
                            {preview.ar_payments.map((p, i) => (
                              <tr key={i} className="border-b border-slate-100 last:border-0">
                                <td className="px-3 py-2">
                                  <div className="font-medium">{p.customer_id ? <button onClick={() => openCustStmt(p.customer_id, p.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{p.customer_name}</button> : p.customer_name}</div>
                                  <button className="text-xs text-blue-600 font-mono hover:underline" onClick={() => { setSelectedInvoiceNumber(p.invoice_number); setInvoiceModalOpen(true); }}>{p.invoice_number}</button>
                                </td>
                                <td className="px-3 py-2 text-right font-mono">{formatPHP(p.balance_before)}</td>
                                <td className="px-3 py-2 text-right font-mono text-amber-600">{p.interest_paid > 0 ? formatPHP(p.interest_paid) : '—'}</td>
                                <td className="px-3 py-2 text-right font-mono text-red-500">{p.penalty_paid > 0 ? formatPHP(p.penalty_paid) : '—'}</td>
                                <td className="px-3 py-2 text-right font-mono font-bold text-blue-700">{formatPHP(p.amount_paid)}</td>
                                <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(p.remaining_balance)}</td>
                              </tr>
                            ))}
                          </tbody>
                          <tfoot>
                            <tr className="border-t-2 border-blue-200 bg-blue-50/50">
                              <td className="px-3 py-2 font-bold text-blue-800" colSpan={4}>Total AR Cash Received</td>
                              <td className="px-3 py-2 text-right font-bold text-blue-700 font-mono">{formatPHP(preview.total_ar_received)}</td>
                              <td></td>
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                    ) : <p className="text-sm text-slate-400">No AR payments received today</p>}
                  </SectionCard>

                  {/* ── EXPENSES ─────────────────────────────────── */}
                  <SectionCard title={`Expenses Today — ${formatPHP(preview.total_expenses)}`} accent="red">
                    {preview.expenses?.length ? (
                      <div className="space-y-1">
                        {preview.expenses.map((e, i) => (
                          <div key={i} className="py-1.5 border-b border-slate-100 last:border-0">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className="text-[10px]">{e.category}</Badge>
                                <span className="text-sm">{e.description || e.employee_name || ''}</span>
                              </div>
                              <span className="font-semibold text-red-600 font-mono">{formatPHP(e.amount)}</span>
                            </div>
                            {/* Employee CA monthly running total */}
                            {e.category === 'Employee Advance' && e.monthly_ca_total !== undefined && (
                              <div className={`text-[11px] ml-1 mt-0.5 ${e.is_over_ca ? 'text-red-600' : 'text-amber-600'}`}>
                                {e.employee_name || 'Employee'} — running CA total this month: <span className="font-semibold">{formatPHP(e.monthly_ca_total)}</span>
                                {e.monthly_ca_limit > 0 && <span> / Limit: {formatPHP(e.monthly_ca_limit)}</span>}
                                {e.is_over_ca && <span className="font-bold ml-1 text-red-700">OVER CA</span>}
                                {e.manager_approved_by && <span className="ml-1 text-violet-600">(Approved: {e.manager_approved_by})</span>}
                              </div>
                            )}
                          </div>
                        ))}
                        <div className="flex justify-between font-bold text-sm pt-1 border-t border-slate-200">
                          <span>Total Expenses</span>
                          <span className="text-red-600">{formatPHP(preview.total_expenses)}</span>
                        </div>
                      </div>
                    ) : <p className="text-sm text-slate-400">No expenses recorded today</p>}
                  </SectionCard>

                  {/* ── EXPECTED COUNTER ─────────────────────────── */}
                  <div className="bg-slate-800 text-white rounded-xl p-5">
                    <div className="text-xs uppercase tracking-wide text-slate-400 mb-1">Expected Money in Counter</div>
                    <div className="text-xs text-slate-400 mb-3">
                      Opening Float ({formatPHP(preview.starting_float)}) + Cash In ({formatPHP(preview.total_cash_in)}) − Cashier Expenses ({formatPHP(preview.total_cashier_expenses ?? preview.total_expenses)})
                    </div>
                    <div className="text-3xl font-bold" style={{ fontFamily: 'Manrope' }}>{formatPHP(expectedCounter)}</div>
                  </div>

                  {/* ── ACTUAL CASH COUNT ────────────────────────── */}
                  <SectionCard title="Cash Count & Reconciliation">
                    <div className="space-y-4">
                      <div>
                        <Label className="font-semibold mb-1.5 block">Actual Cash in Counter <span className="text-red-500">*</span></Label>
                        <CalcInput value={actualCash}
 onChange={(v) => {
 setActualCash(v);
 // Auto-fill cash to safe and drawer if not yet touched
 const a = parseFloat(v) || 0;
 if (!cashToDrawer && !cashToSafe) {
 setCashToSafe(String(r2(a - 2000 > 0 ? a - 2000 : 0)));
 setCashToDrawer(String(r2(Math.min(a, 2000))));
 }
 }}
 className="h-12 text-xl font-bold font-mono"
 placeholder="Enter actual cash counted"
 data-testid="actual-cash-input" />
                      </div>

                      {actualCash !== '' && (
                        <div className={`p-4 rounded-lg flex items-center justify-between ${overShort >= 0 ? 'bg-emerald-50 border border-emerald-200' : 'bg-red-50 border border-red-200'}`}>
                          <div>
                            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                              {overShort >= 0 ? 'Cash Over (extra)' : 'Cash Short (deficit)'}
                            </div>
                            <div className="text-xs text-slate-400">Actual {formatPHP(actualNum)} − Expected {formatPHP(expectedCounter)}</div>
                          </div>
                          <div className={`text-2xl font-bold font-mono ${overShort >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                            {overShort >= 0 ? '+' : ''}{formatPHP(overShort)}
                          </div>
                        </div>
                      )}

                      {/* Variance notes — shown when there IS a discrepancy */}
                      {actualCash !== '' && overShort !== null && overShort !== 0 && (
                        <div>
                          <Label className="mb-1.5 block font-semibold">
                            {overShort > 0
                              ? 'Explain the extra cash (for audit trail)'
                              : 'Explain the shortage (for audit trail)'}
                            {' '}<span className="text-slate-400 font-normal text-xs">— stored permanently</span>
                          </Label>
                          <Textarea
                            value={varianceNotes}
                            onChange={e => setVarianceNotes(e.target.value)}
                            placeholder={overShort > 0
                              ? 'e.g. "Unrecorded walk-in sale of pesticide" or "Customer rounded up payment"'
                              : 'e.g. "Gave change for large bill" or "Cashier error on item #12"'}
                            className="text-sm resize-none"
                            rows={2}
                            data-testid="variance-notes-input"
                          />
                        </div>
                      )}

                      <Separator />
                      <div className="text-sm font-semibold text-slate-700">Distribution of Actual Cash</div>
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <Label className="mb-1.5 block">Transfer to Vault</Label>
                          <CalcInput value={cashToSafe}
 onChange={(v) => {
 setCashToSafe(v);
 setCashToDrawer(String(r2(actualNum - (parseFloat(v) || 0))));
 }}
 className="h-10 font-mono" data-testid="cash-to-safe-input" />
                        </div>
                        <div>
                          <Label className="mb-1.5 block">
                            Stays as Opening Float (Next Day)
                            <span className="text-xs text-slate-400 font-normal ml-1">(= tomorrow's starting float)</span>
                          </Label>
                          <CalcInput value={cashToDrawer}
 onChange={(v) => {
 setCashToDrawer(v);
 setCashToSafe(String(r2(actualNum - (parseFloat(v) || 0))));
 }}
 className="h-10 font-mono font-bold bg-emerald-50 border-emerald-300" data-testid="cash-to-drawer-input" />
                          <p className="text-[10px] text-emerald-600 mt-0.5">This becomes the starting float for tomorrow</p>
                        </div>
                      </div>

                      {actualCash !== '' && cashToDrawer !== '' && (
                        <div className="text-xs text-slate-500 text-right">
                          Vault: {formatPHP(cashToSafeNum)} + Float: {formatPHP(cashToDrawerNum)} = {formatPHP(r2(cashToSafeNum + cashToDrawerNum))}
                          {Math.abs(r2(cashToSafeNum + cashToDrawerNum) - actualNum) > 0.01 && (
                            <span className="text-red-500 ml-2">⚠ Does not match actual cash</span>
                          )}
                        </div>
                      )}

                      <Separator />
                      <div>
                        <Label className="mb-1.5 block">Admin PIN to Confirm Close <span className="text-red-500">*</span></Label>
                        <Input type="password" autoComplete="new-password" value={adminPin} onChange={e => setAdminPin(e.target.value)}
                          placeholder="Enter admin PIN" maxLength={6} className="max-w-xs"
                          data-testid="admin-pin-close" />
                      </div>

                      <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
                        <AlertTriangle size={12} className="inline mr-1" />
                        Closing the day locks all transactions for {date}. New sales will roll to the next day.
                      </div>

                      <Button onClick={handleClose} disabled={closing_loading || actualCash === '' || !adminPin}
                        className="w-full h-12 bg-red-600 hover:bg-red-700 text-white text-base"
                        data-testid="close-day-btn">
                        {closing_loading
                          ? <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />Closing...</>
                          : <><Lock size={16} className="mr-2" /> Close Accounts for {date}</>}
                      </Button>
                    </div>
                  </SectionCard>
                </>
              )}
            </div>
          )}
        </TabsContent>

        {/* ═══ VARIANCE LOG ════════════════════════════════════════════ */}
        <TabsContent value="variance" className="mt-4 space-y-4" data-testid="variance-tab">
          <div>
            <h2 className="text-base font-semibold" style={{ fontFamily: 'Manrope' }}>Cash Variance Log</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Permanent record of daily over/short. Extra cash may indicate unrecorded sales.
              Short cash may indicate errors or missing expense records. Used during stock audits.
            </p>
          </div>

          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Branch</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Expected</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Actual</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Over / Short</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Notes</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Closed By</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {varianceHistory.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-10 text-slate-400">
                        <FileWarning size={32} className="mx-auto mb-2 opacity-30" />
                        No closed days yet. Variance records will appear here after each day close.
                      </TableCell>
                    </TableRow>
                  )}
                  {varianceHistory.map((r, i) => {
                    const os = r.over_short || 0;
                    return (
                      <TableRow key={r.id || i} className={os !== 0 ? (os > 0 ? 'bg-emerald-50/30' : 'bg-red-50/30') : ''}>
                        <TableCell className="font-mono text-sm font-medium">{r.date}</TableCell>
                        <TableCell className="text-sm text-slate-500">{r.branch_name}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{formatPHP(r.expected_counter)}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{formatPHP(r.actual_cash)}</TableCell>
                        <TableCell className="text-right">
                          <span className={`font-mono font-bold text-sm ${os > 0 ? 'text-emerald-600' : os < 0 ? 'text-red-600' : 'text-slate-400'}`}>
                            {os > 0 ? '+' : ''}{formatPHP(os)}
                          </span>
                          {os !== 0 && (
                            <Badge className={`ml-2 text-[9px] border-0 ${os > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                              {os > 0 ? 'OVER' : 'SHORT'}
                            </Badge>
                          )}
                          {os === 0 && <span className="text-slate-300 text-xs ml-1">Balanced</span>}
                        </TableCell>
                        <TableCell className="text-sm text-slate-600 italic max-w-[200px] truncate">
                          {r.variance_notes || <span className="text-slate-300">—</span>}
                        </TableCell>
                        <TableCell className="text-sm text-slate-500">{r.closed_by_name}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* Summary stats */}
          {varianceHistory.length > 0 && (
            <div className="grid grid-cols-3 gap-4">
              {(() => {
                const overDays = varianceHistory.filter(r => (r.over_short || 0) > 0);
                const shortDays = varianceHistory.filter(r => (r.over_short || 0) < 0);
                const totalOver = overDays.reduce((s, r) => s + (r.over_short || 0), 0);
                const totalShort = shortDays.reduce((s, r) => s + (r.over_short || 0), 0);
                return (<>
                  <Card className="border-emerald-200 bg-emerald-50">
                    <CardContent className="p-3">
                      <div className="text-xs text-emerald-600 font-medium">Days Over</div>
                      <div className="text-xl font-bold text-emerald-700">{overDays.length}</div>
                      <div className="text-xs text-emerald-500">Total +{formatPHP(totalOver)}</div>
                    </CardContent>
                  </Card>
                  <Card className="border-red-200 bg-red-50">
                    <CardContent className="p-3">
                      <div className="text-xs text-red-600 font-medium">Days Short</div>
                      <div className="text-xl font-bold text-red-700">{shortDays.length}</div>
                      <div className="text-xs text-red-500">Total {formatPHP(totalShort)}</div>
                    </CardContent>
                  </Card>
                  <Card className="border-slate-200">
                    <CardContent className="p-3">
                      <div className="text-xs text-slate-500 font-medium">Net Variance</div>
                      <div className={`text-xl font-bold ${(totalOver + totalShort) >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                        {formatPHP(totalOver + totalShort)}
                      </div>
                      <div className="text-xs text-slate-400">Across {varianceHistory.length} closed days</div>
                    </CardContent>
                  </Card>
                </>);
              })()}
            </div>
          )}
        </TabsContent>

        {/* ═══ Z-REPORT ARCHIVE ═══════════════════════════════════════ */}
        <TabsContent value="archive" className="mt-4 space-y-4" data-testid="archive-tab">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex-1 min-w-[180px]">
              <div className="relative">
                <Calendar size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  placeholder="Filter by date (e.g. 2026-02)"
                  value={archiveSearch}
                  onChange={e => setArchiveSearch(e.target.value)}
                  className="pl-8 h-9 text-sm"
                  data-testid="archive-date-filter"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Building2 size={14} className="text-slate-400 shrink-0" />
              <Select
                value={archiveBranch}
                onValueChange={v => { setArchiveBranch(v); fetchArchive(v); }}
              >
                <SelectTrigger className="h-9 w-44 text-sm" data-testid="archive-branch-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Branches</SelectItem>
                  {branches.map(b => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              variant="outline" size="sm"
              onClick={() => fetchArchive(archiveBranch)}
              disabled={archiveLoading}
              data-testid="archive-refresh-btn"
            >
              {archiveLoading
                ? <RefreshCw size={13} className="animate-spin" />
                : <RefreshCw size={13} />}
            </Button>
          </div>

          {/* Summary stats */}
          {archiveRecords.length > 0 && (() => {
            const filtered = archiveRecords.filter(r =>
              !archiveSearch || r.date.includes(archiveSearch) || (r.branch_name || '').toLowerCase().includes(archiveSearch.toLowerCase())
            );
            const totalSales   = filtered.reduce((s, r) => s + (r.total_cash_sales || 0), 0);
            const totalAR      = filtered.reduce((s, r) => s + (r.total_ar_received || 0), 0);
            const totalExp     = filtered.reduce((s, r) => s + (r.total_expenses || 0), 0);
            // Use adjusted over/short so any admin reconciliation overrides
            // (daily_closing_adjustments) are reflected in the KPI tile.
            // Falls back to raw over_short for closings that have no
            // adjustments (adjusted_over_short is absent on pre-feature rows).
            const totalOS = filtered.reduce((s, r) => s + (
              r.adjusted_over_short !== undefined ? r.adjusted_over_short : (r.over_short || 0)
            ), 0);
            return (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: 'Closed Days', value: filtered.length, mono: false, color: 'slate' },
                  { label: 'Total Walk-in Sales', value: formatPHP(totalSales), mono: true, color: 'emerald' },
                  { label: 'Total AR Collected', value: formatPHP(totalAR), mono: true, color: 'blue' },
                  { label: 'Net Over / Short', value: (totalOS >= 0 ? '+' : '') + formatPHP(totalOS), mono: true, color: totalOS >= 0 ? 'emerald' : 'red' },
                ].map(({ label, value, mono, color }) => (
                  <Card key={label} className="border-slate-200">
                    <CardContent className="p-3">
                      <p className={`text-xs text-${color}-500 uppercase font-medium mb-0.5`}>{label}</p>
                      <p className={`text-xl font-bold text-${color}-700 ${mono ? 'font-mono' : ''}`}>{value}</p>
                    </CardContent>
                  </Card>
                ))}
              </div>
            );
          })()}

          {/* Archive table */}
          <Card className="border-slate-200">
            <CardContent className="p-0">
              <ScrollArea className="h-[450px]">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50 sticky top-0">
                      <TableHead className="text-xs uppercase text-slate-500 font-medium">Date</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium">Branch</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium text-right">Walk-in Sales</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium text-right">AR Collected</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium text-right">Expenses</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium text-right">Over / Short</TableHead>
                      <TableHead className="text-xs uppercase text-slate-500 font-medium">Closed By</TableHead>
                      <TableHead className="w-20"></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {archiveLoading ? (
                      <TableRow>
                        <TableCell colSpan={8} className="text-center py-10 text-slate-400">
                          <RefreshCw size={18} className="animate-spin mx-auto" />
                        </TableCell>
                      </TableRow>
                    ) : (() => {
                      const filtered = archiveRecords.filter(r =>
                        !archiveSearch || r.date.includes(archiveSearch) || (r.branch_name || '').toLowerCase().includes(archiveSearch.toLowerCase())
                      );
                      if (filtered.length === 0) return (
                        <TableRow>
                          <TableCell colSpan={8} className="text-center py-10 text-slate-400">
                            {archiveRecords.length === 0 ? 'No closed days yet.' : 'No results match your filter.'}
                          </TableCell>
                        </TableRow>
                      );
                      return filtered.map((r, i) => {
                        const rawOs = r.over_short || 0;
                        const adjusted = r.adjusted_over_short !== undefined ? r.adjusted_over_short : rawOs;
                        const hasAdj = (r.adjustment_count || 0) > 0;
                        return (
                          <TableRow key={r.id || i} className="table-row-hover">
                            <TableCell className="font-mono text-sm font-semibold">{r.date}</TableCell>
                            <TableCell className="text-sm text-slate-600">{r.branch_name || '—'}</TableCell>
                            <TableCell className="text-right font-mono text-emerald-700">{formatPHP(r.total_cash_sales || 0)}</TableCell>
                            <TableCell className="text-right font-mono text-blue-700">{formatPHP(r.total_ar_received || 0)}</TableCell>
                            <TableCell className="text-right font-mono text-red-600">{formatPHP(r.total_expenses || 0)}</TableCell>
                            <TableCell className="text-right font-mono">
                              <div className="flex items-center justify-end gap-1.5">
                                {hasAdj && (
                                  <span
                                    title={`Admin adjustment of ${formatPHP(r.adjustment_total || 0)} applied. Raw was ${formatPHP(rawOs)}.`}
                                    className="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 border border-amber-200 uppercase tracking-wider"
                                    data-testid={`adj-badge-${r.date}`}
                                  >
                                    ADJ
                                  </span>
                                )}
                                <span className={adjusted > 0 ? 'text-emerald-600 font-semibold' : adjusted < 0 ? 'text-red-600 font-semibold' : 'text-slate-400'}>
                                  {adjusted > 0 ? '+' : ''}{formatPHP(adjusted)}
                                </span>
                              </div>
                              {hasAdj && (
                                <div className="text-[9px] text-slate-400 mt-0.5 font-mono">
                                  raw {rawOs > 0 ? '+' : ''}{formatPHP(rawOs)}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="text-sm text-slate-500">{r.closed_by_name}</TableCell>
                            <TableCell>
                              <Button
                                variant="outline" size="sm"
                                className="h-7 px-2 text-xs"
                                onClick={() => openZreport(r)}
                                data-testid={`view-zreport-${r.date}`}
                              >
                                <Eye size={12} className="mr-1" /> View
                              </Button>
                            </TableCell>
                          </TableRow>
                        );
                      });
                    })()}
                  </TableBody>
                </Table>
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── Z-Report Viewer Dialog ─────────────────────────────────── */}
      <Dialog open={zreportDialog} onOpenChange={setZreportDialog}>
        <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col">
          <DialogHeader className="shrink-0 print:hidden">
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Archive size={18} className="text-[#1A4D2E]" />
              {zreportData ? `Z-Report — ${zreportData.date} · ${zreportData.branch_name || ''}` : 'Loading Z-Report...'}
            </DialogTitle>
          </DialogHeader>
          {/* View toggle — Normal (compact) vs Detailed (rich breakdown with
              discounts, price changes, AR method split, fund transfers, per-
              category expense lines). Detail payload loads lazily the first
              time the owner flips to Detailed so opening the dialog stays
              instant. */}
          {zreportData && (
            <div className="flex items-center gap-2 px-1 pb-2 shrink-0 print:hidden" data-testid="z-report-mode-toggle">
              <Button
                size="sm"
                variant={zreportMode === 'normal' ? 'default' : 'outline'}
                onClick={() => setZreportMode('normal')}
                data-testid="z-report-mode-normal"
                className={zreportMode === 'normal' ? 'bg-[#1A4D2E] text-white hover:bg-[#163f25]' : ''}>
                Normal
              </Button>
              <Button
                size="sm"
                variant={zreportMode === 'detailed' ? 'default' : 'outline'}
                onClick={() => { setZreportMode('detailed'); loadZreportDetail(); }}
                data-testid="z-report-mode-detailed"
                className={zreportMode === 'detailed' ? 'bg-[#1A4D2E] text-white hover:bg-[#163f25]' : ''}>
                Detailed
              </Button>
              <span className="text-[11px] text-slate-400 ml-2">
                {zreportMode === 'detailed'
                  ? 'Discounts, price changes, AR by method, fund transfers, full expense list'
                  : 'Compact Z-Report (default)'}
              </span>
            </div>
          )}
          <ScrollArea className="flex-1 overflow-auto">
            <div className="pr-2 py-1">
              {zreportLoading ? (
                <div className="flex items-center justify-center py-12">
                  <RefreshCw size={22} className="animate-spin text-slate-400" />
                </div>
              ) : zreportData ? (
                <>
                  <ClosingAdjustmentsPanel
                    closing={zreportData}
                    isAdmin={user?.role === 'admin'}
                    onAdjusted={() => {
                      // Refetch so the panel + archive summary sync.
                      openZreport({ date: zreportData.date, branch_id: zreportData.branch_id });
                      loadArchive(zreportData.branch_id || 'all');
                    }}
                  />
                  <div className="h-3" />
                  {zreportMode === 'detailed' ? (
                    zreportDetailLoading ? (
                      <div className="flex items-center justify-center py-12">
                        <RefreshCw size={22} className="animate-spin text-slate-400" />
                        <span className="ml-2 text-sm text-slate-400">Loading detailed breakdown…</span>
                      </div>
                    ) : zreportDetail ? (
                      <ZReportDetailed
                        preview={zreportDetail}
                        closing={zreportData}
                        branchName={zreportData.branch_name || currentBranch?.name}
                        onPrint={() => printZReportPdf(zreportData.date, zreportData.branch_id, true)}
                        onDownloadPdf={() => downloadZReportPdf(zreportData.date, zreportData.branch_id, zreportData.branch_name || currentBranch?.name, true)}
                      />
                    ) : (
                      <p className="text-center py-8 text-slate-400">Could not load detailed breakdown.</p>
                    )
                  ) : (
                    <ZReport
                      data={zreportData}
                      branchName={zreportData.branch_name || currentBranch?.name}
                      onPrint={() => printZReportPdf(zreportData.date, zreportData.branch_id, false)}
                      onDownloadPdf={() => downloadZReportPdf(zreportData.date, zreportData.branch_id, zreportData.branch_name || currentBranch?.name, false)}
                    />
                  )}
                </>
              ) : (
                <p className="text-center py-8 text-slate-400">Could not load Z-report data.</p>
              )}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>
      <Dialog open={expenseDialog} onOpenChange={setExpenseDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>
            {expenseType === 'farm' ? 'Farm Expense' : expenseType === 'advance' ? 'Employee Cash Advance' : 'Record Expense'}
          </DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            {expenseType === 'other' && (
              <div><Label>Category</Label><Input value={expForm.category} onChange={e => setExpForm({ ...expForm, category: e.target.value })} placeholder="e.g. Utilities, Supplies" /></div>
            )}
            {expenseType === 'farm' && (
              <>
                <div><Label>Customer / Farm</Label>
                  <Select value={expForm.customer_id} onValueChange={v => setExpForm({ ...expForm, customer_id: v })}>
                    <SelectTrigger><SelectValue placeholder="Select customer" /></SelectTrigger>
                    <SelectContent>{customers.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div><Label>Tag (purpose)</Label><Input value={expForm.tag} onChange={e => setExpForm({ ...expForm, tag: e.target.value })} placeholder="e.g. Tilling, Harvesting, Spraying" /></div>
              </>
            )}
            {expenseType === 'advance' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Employee</Label>
                  <Button variant="ghost" size="sm" onClick={() => { setEmpForm({ name: '', position: '', phone: '' }); setEmpDialog(true); }} className="text-xs">+ Add Employee</Button>
                </div>
                <Select value={expForm.employee_id} onValueChange={v => {
                  const emp = employees.find(e => e.id === v);
                  setExpForm({ ...expForm, employee_id: v, employee_name: emp?.name || '' });
                }}>
                  <SelectTrigger><SelectValue placeholder="Select employee" /></SelectTrigger>
                  <SelectContent>{employees.map(e => (
                    <SelectItem key={e.id} value={e.id}>{e.name} {e.monthly_advance_total > 0 ? `(₱${e.monthly_advance_total} this month)` : ''}</SelectItem>
                  ))}</SelectContent>
                </Select>
              </div>
            )}
            <div><Label>Description</Label><Input value={expForm.description} onChange={e => setExpForm({ ...expForm, description: e.target.value })} /></div>
            <div><Label>Amount</Label><CalcInput value={expForm.amount} onChange={(v) => setExpForm({ ...expForm, amount: parseFloat(v) || 0 })} className="h-11 text-lg font-bold" /></div>
            <Button onClick={handleExpense} className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white">Save</Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Add Employee Dialog */}
      <Dialog open={empDialog} onOpenChange={setEmpDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle>Add Employee</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-2">
            <div><Label>Name</Label><Input value={empForm.name} onChange={e => setEmpForm({ ...empForm, name: e.target.value })} /></div>
            <div><Label>Position</Label><Input value={empForm.position} onChange={e => setEmpForm({ ...empForm, position: e.target.value })} /></div>
            <Button onClick={handleAddEmployee} className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white">Add</Button>
          </div>
        </DialogContent>
      </Dialog>
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
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
