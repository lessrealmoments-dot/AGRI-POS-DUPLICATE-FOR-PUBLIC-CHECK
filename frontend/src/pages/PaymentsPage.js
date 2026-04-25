import { useState, useEffect, useCallback, useRef, Fragment } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { Separator } from '../components/ui/separator';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { ScrollArea } from '../components/ui/scroll-area';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import {
  Search, AlertTriangle, Percent, Receipt, Clock,
  Info, Zap, Edit3, Banknote, CreditCard, FileText, RefreshCw,
  Building2, Smartphone, X, Tag, Users, ArrowDownAZ, ArrowDown01
} from 'lucide-react';
import { toast } from 'sonner';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import { invalidateBalanceCache } from '../components/CustomerBalanceBadge';

const METHODS = [
  { value: 'Cash', label: 'Cash', icon: Banknote },
  { value: 'Check', label: 'Check', icon: Receipt },
  { value: 'Bank Transfer', label: 'Bank', icon: Building2 },
  { value: 'GCash', label: 'GCash', icon: Smartphone },
  { value: 'Maya', label: 'Maya', icon: CreditCard },
];

const TYPE_CONFIG = {
  penalty_charge: { label: 'Penalty', cls: 'bg-red-100 text-red-700 border-red-200', priority: 1 },
  interest_charge: { label: 'Interest', cls: 'bg-amber-100 text-amber-700 border-amber-200', priority: 2 },
  farm_expense: { label: 'Farm', cls: 'bg-green-100 text-green-700 border-green-200', priority: 3 },
  cash_advance: { label: 'Customer Cash Out', cls: 'bg-purple-100 text-purple-700 border-purple-200', priority: 3 },
};
const getTypeConfig = (t) => TYPE_CONFIG[t] || { label: 'Invoice', cls: 'bg-slate-100 text-slate-700 border-slate-200', priority: 3 };
const isDiscountable = (t) => t === 'interest_charge' || t === 'penalty_charge';

function round2(n) { return Math.round(n * 100) / 100; }

export default function PaymentsPage() {
  const { currentBranch } = useAuth();

  // ── Left panel: customer list ──
  const [custList, setCustList] = useState([]);
  const [listSearch, setListSearch] = useState('');
  const [showAll, setShowAll] = useState(false);       // false = with-balance only
  const [sortBy, setSortBy] = useState('balance');      // 'balance' | 'name'

  // ── Selected customer ──
  const [selectedCustomer, setSelectedCustomer] = useState(null);

  // Invoices
  const [invoices, setInvoices] = useState([]);
  const [rowAmounts, setRowAmounts] = useState({});
  const [rowDiscounts, setRowDiscounts] = useState({});
  const [discountModes, setDiscountModes] = useState({});

  // Payment header
  const [payDate, setPayDate] = useState(new Date().toISOString().slice(0, 10));
  const [payMethod, setPayMethod] = useState('Cash');
  const [payRef, setPayRef] = useState('');
  const [payMemo, setPayMemo] = useState('');

  // Interest/penalty
  const [penaltyRate, setPenaltyRate] = useState(5);
  const [chargesPreview, setChargesPreview] = useState(null);
  const [generatingCharge, setGeneratingCharge] = useState(null);
  const [interestRateInput, setInterestRateInput] = useState('');
  const interestPreviewTimer = useRef(null);

  // No-rate prompt
  const [ratePromptOpen, setRatePromptOpen] = useState(false);
  const [ratePromptInput, setRatePromptInput] = useState('');
  const [ratePromptSaving, setRatePromptSaving] = useState(false);

  // Dialogs
  const [historyOpen, setHistoryOpen] = useState(false);
  const [statementOpen, setStatementOpen] = useState(false);
  const [payHistory, setPayHistory] = useState([]);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState(null);

  const [processing, setProcessing] = useState(false);

  // ── Load customer list (receivables summary) ──
  const loadCustList = useCallback(async () => {
    try {
      const params = { include_zero: showAll };
      if (currentBranch?.id) params.branch_id = currentBranch.id;
      const res = await api.get('/customers/receivables-summary', { params });
      setCustList(res.data || []);
    } catch { toast.error('Failed to load customer list'); }
  }, [currentBranch, showAll]);

  useEffect(() => { loadCustList(); }, [loadCustList]);

  const loadInvoices = useCallback(async (custId) => {
    try {
      const res = await api.get(`/customers/${custId}/invoices`);
      setInvoices(res.data || []);
      setRowAmounts({});
      setRowDiscounts({});
      setDiscountModes({});
      return res.data || [];
    } catch { setInvoices([]); return []; }
  }, []);

  const loadChargesPreview = useCallback(async (custId, rateOverride) => {
    try {
      const params = { as_of_date: payDate };
      if (rateOverride !== undefined && rateOverride > 0) params.rate_override = rateOverride;
      const res = await api.get(`/customers/${custId}/charges-preview`, { params });
      setChargesPreview(res.data);
    } catch { setChargesPreview(null); }
  }, [payDate]);

  const selectCustomer = (c) => {
    setSelectedCustomer(c);
    setRowAmounts({});
    setRowDiscounts({});
    setDiscountModes({});
    setPayRef('');
    setPayMemo('');
    setInterestRateInput(c.interest_rate > 0 ? String(c.interest_rate) : '');
    setRatePromptOpen(false);
    setRatePromptInput('');
    // Just load invoices + preview interest inline (no DB INT invoice creation)
    loadAndPromptIfNoRate(c);
    loadChargesPreview(c.id, c.interest_rate > 0 ? c.interest_rate : undefined);
  };

  // Load invoices and prompt for rate if customer has overdue but no rate set.
  // No INT invoice is created here — interest is computed inline only.
  const loadAndPromptIfNoRate = useCallback(async (customer) => {
    const rate = parseFloat(customer.interest_rate) || 0;
    const invRes = await loadInvoices(customer.id);
    if (rate <= 0) {
      const today = new Date().toISOString().split('T')[0];
      const loaded = invRes || [];
      const hasOverdue = loaded.some(inv =>
        inv.due_date && inv.due_date < today &&
        inv.sale_type !== 'interest_charge' && inv.sale_type !== 'penalty_charge'
      );
      if (hasOverdue) setRatePromptOpen(true);
    }
  }, [loadInvoices]);

  const clearCustomer = () => {
    setSelectedCustomer(null);
    setInvoices([]);
    setRowAmounts({});
    setRowDiscounts({});
    setDiscountModes({});
    setChargesPreview(null);
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (selectedCustomer) {
      const rate = parseFloat(interestRateInput) || 0;
      loadChargesPreview(selectedCustomer.id, rate > 0 ? rate : undefined);
    }
  }, [payDate, selectedCustomer]);

  useEffect(() => {
    if (!selectedCustomer) return;
    if (interestPreviewTimer.current) clearTimeout(interestPreviewTimer.current);
    interestPreviewTimer.current = setTimeout(() => {
      const rate = parseFloat(interestRateInput) || 0;
      loadChargesPreview(selectedCustomer.id, rate > 0 ? rate : undefined);
    }, 400);
    return () => { if (interestPreviewTimer.current) clearTimeout(interestPreviewTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interestRateInput]);

  // ── Calculations ──
  const getDiscountAmount = (inv) => {
    const mode = discountModes[inv.id] || 'amount';
    const val = parseFloat(rowDiscounts[inv.id] || 0);
    if (isNaN(val) || val <= 0 || !isDiscountable(inv.sale_type)) return 0;
    if (mode === 'percent') return Math.min(round2(inv.balance * val / 100), inv.balance);
    return Math.min(val, inv.balance);
  };

  const totalApplied = invoices.reduce((s, inv) => {
    const v = parseFloat(rowAmounts[inv.id] || 0);
    return s + (isNaN(v) ? 0 : v);
  }, 0);

  const totalDiscount = invoices.reduce((s, inv) => s + getDiscountAmount(inv), 0);
  const totalOpen = invoices.reduce((s, i) => s + (i.balance || 0), 0);
  const hasUnsavedAmounts = Object.values(rowAmounts).some(v => parseFloat(v) > 0) || totalDiscount > 0;

  // ── Inline interest map: invoice_id → preview row ──
  const interestByInvoice = (chargesPreview?.interest_preview || []).reduce((acc, p) => {
    acc[p.invoice_id] = p; return acc;
  }, {});

  // ── Account Summary (live) ──
  // Outstanding Principal = open balances of regular invoices (excludes already-issued INT/penalty)
  // Accrued Interest Charges = open INT/penalty invoice balances + computed (but not-yet-issued) interest
  const principalOpen = invoices
    .filter(i => i.sale_type !== 'interest_charge' && i.sale_type !== 'penalty_charge')
    .reduce((s, i) => s + (i.balance || 0), 0);
  const interestOpenInvoiced = invoices
    .filter(i => i.sale_type === 'interest_charge' || i.sale_type === 'penalty_charge')
    .reduce((s, i) => s + (i.balance || 0), 0);
  const interestComputedInline = chargesPreview?.total_interest || 0;
  const accruedInterestTotal = round2(interestOpenInvoiced + interestComputedInline);
  const totalAmountDue = round2(principalOpen + accruedInterestTotal);

  // ── Auto-apply ──
  const autoApply = (totalAmt) => {
    const amt = parseFloat(totalAmt) || 0;
    if (amt <= 0) { setRowAmounts({}); return; }
    let remaining = amt;
    const newAmounts = {};
    for (const inv of invoices) {
      if (remaining <= 0) break;
      const disc = getDiscountAmount(inv);
      const effectiveBal = Math.max(0, inv.balance - disc);
      const apply = Math.min(remaining, effectiveBal);
      if (apply > 0) { newAmounts[inv.id] = apply.toFixed(2); remaining = round2(remaining - apply); }
    }
    setRowAmounts(newAmounts);
  };

  // ── Set rate from prompt (saves rate + refreshes inline preview only) ──
  const handleSetRateFromPrompt = async () => {
    const rate = parseFloat(ratePromptInput) || 0;
    if (rate <= 0) { toast.error('Enter a valid interest rate'); return; }
    setRatePromptSaving(true);
    try {
      await api.put(`/customers/${selectedCustomer.id}`, { interest_rate: rate });
      setSelectedCustomer(prev => ({ ...prev, interest_rate: rate }));
      setInterestRateInput(String(rate));
      toast.success(`Rate ${rate}%/mo saved. Interest will be computed inline.`);
      setRatePromptOpen(false);
      setRatePromptInput('');
      await loadChargesPreview(selectedCustomer.id, rate);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to set rate');
    } finally {
      setRatePromptSaving(false);
    }
  };

  // ── Generate Interest manually (force=true, bypasses 30-day guard) ──
  const handleGenerateInterest = async () => {
    const rate = parseFloat(interestRateInput) || 0;
    if (rate <= 0) { toast.error('Enter an interest rate (% per month) first'); return; }
    setGeneratingCharge('interest');
    try {
      const res = await api.post(`/customers/${selectedCustomer.id}/generate-interest`, {
        as_of_date: payDate, rate_override: rate, force: true,
      });
      if (res.data.total_interest > 0) {
        toast.success(`Interest invoice ${res.data.invoice_number} created — ${formatPHP(res.data.total_interest)}`);
        await loadInvoices(selectedCustomer.id);
        await loadChargesPreview(selectedCustomer.id, rate);
      } else {
        toast(`No interest to generate — ${res.data.message}`, { description: `Grace: ${res.data.grace_period} days` });
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to generate interest'); }
    setGeneratingCharge(null);
  };

  // ── Generate Penalty ──
  const handleGeneratePenalty = async () => {
    setGeneratingCharge('penalty');
    try {
      const res = await api.post(`/customers/${selectedCustomer.id}/generate-penalty`, { penalty_rate: penaltyRate, as_of_date: payDate });
      if (res.data.total_penalty > 0) {
        toast.success(`Penalty invoice ${res.data.invoice_number} created — ${formatPHP(res.data.total_penalty)}`);
        await loadInvoices(selectedCustomer.id);
        await loadChargesPreview(selectedCustomer.id);
      } else {
        toast(`No penalty applicable — ${res.data.message}`);
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to generate penalty'); }
    setGeneratingCharge(null);
  };

  // ── Apply Payment ──
  // Before applying: if customer has overdue invoices and a rate, generate the INT invoice
  // (force=true) so that interest is collected at this payment. autoApply ordering puts
  // interest first so the staff still sees the same applied total.
  const handleApplyPayment = async () => {
    const totalEntered = invoices.reduce((s, inv) => s + (parseFloat(rowAmounts[inv.id] || 0)), 0);
    const totalDisc = invoices.reduce((s, inv) => s + getDiscountAmount(inv), 0);
    if (totalEntered <= 0 && totalDisc <= 0) { toast.error('Enter payment amounts for at least one invoice'); return; }

    setProcessing(true);
    try {
      // Auto-generate INT invoice at pay-time (only if rate set + computed interest > 0)
      const rate = parseFloat(interestRateInput) || parseFloat(selectedCustomer.interest_rate) || 0;
      const computedInt = chargesPreview?.total_interest || 0;
      let intInvoiceCreated = null;
      if (rate > 0 && computedInt > 0.005) {
        try {
          const intRes = await api.post(`/customers/${selectedCustomer.id}/generate-interest`, {
            as_of_date: payDate, rate_override: rate, force: true,
          });
          if (intRes.data.total_interest > 0 && intRes.data.invoice_number) {
            intInvoiceCreated = intRes.data;
            // Reload invoices so the new INT invoice appears for allocation
            const fresh = await loadInvoices(selectedCustomer.id);
            // Auto-apply: route entered amount through the now-updated invoice list
            // (interest invoice will be paid first because of sort order)
            const totalToApply = totalEntered + (intInvoiceCreated ? intInvoiceCreated.total_interest : 0);
            let remaining = totalToApply;
            const newAmounts = {};
            for (const inv of fresh) {
              if (remaining <= 0) break;
              const apply = Math.min(remaining, inv.balance);
              if (apply > 0) { newAmounts[inv.id] = apply.toFixed(2); remaining = round2(remaining - apply); }
            }
            setRowAmounts(newAmounts);
            // Build allocations from the freshly loaded invoices
            const allocations = fresh
              .map(inv => ({ invoice_id: inv.id, amount: parseFloat(newAmounts[inv.id] || 0), discount: 0 }))
              .filter(a => a.amount > 0);
            const res = await api.post(`/customers/${selectedCustomer.id}/receive-payment`, {
              allocations, method: payMethod, reference: payRef, date: payDate,
              branch_id: currentBranch?.id, memo: payMemo,
            });
            toast.success(
              `${formatPHP(res.data.total_applied)} applied to ${res.data.applied_invoices.length} invoice(s) — incl. ${formatPHP(intInvoiceCreated.total_interest)} interest (${intInvoiceCreated.invoice_number})`,
              { description: `Deposited to ${res.data.deposited_to}` }
            );
            setRowAmounts({});
            setRowDiscounts({});
            setDiscountModes({});
            setPayRef('');
            setPayMemo('');
            await loadInvoices(selectedCustomer.id);
            await loadChargesPreview(selectedCustomer.id, rate);
            invalidateBalanceCache();
            await loadCustList();
            const refreshed = (await api.get('/customers/receivables-summary', {
              params: { include_zero: showAll, ...(currentBranch?.id ? { branch_id: currentBranch.id } : {}) }
            }).then(r => r.data || []).catch(() => [])).find(c => c.id === selectedCustomer.id);
            if (refreshed) setSelectedCustomer(refreshed);
            setProcessing(false);
            return;
          }
        } catch (e) {
          // If INT generation fails (e.g., 30-day guard hit), proceed with normal payment
          console.warn('INT generation skipped:', e.response?.data?.detail);
        }
      }

      // Standard path — no INT invoice was created (or none needed)
      const allocations = invoices
        .map(inv => ({
          invoice_id: inv.id,
          amount: parseFloat(rowAmounts[inv.id] || 0),
          discount: getDiscountAmount(inv),
        }))
        .filter(a => a.amount > 0 || a.discount > 0);

      if (allocations.length === 0) { toast.error('Enter payment amounts for at least one invoice'); setProcessing(false); return; }

      const res = await api.post(`/customers/${selectedCustomer.id}/receive-payment`, {
        allocations, method: payMethod, reference: payRef, date: payDate,
        branch_id: currentBranch?.id, memo: payMemo,
      });
      const parts = [`${formatPHP(res.data.total_applied)} applied`];
      if (res.data.total_discounted > 0) parts.push(`${formatPHP(res.data.total_discounted)} discounted`);
      parts.push(`to ${res.data.applied_invoices.length} invoice(s)`);
      toast.success(parts.join(' ') + ` — deposited to ${res.data.deposited_to}`);
      setRowAmounts({});
      setRowDiscounts({});
      setDiscountModes({});
      setPayRef('');
      setPayMemo('');
      await loadInvoices(selectedCustomer.id);
      await loadChargesPreview(selectedCustomer.id);
      invalidateBalanceCache();
      await loadCustList();
      const refreshed = (await api.get('/customers/receivables-summary', {
        params: { include_zero: showAll, ...(currentBranch?.id ? { branch_id: currentBranch.id } : {}) }
      }).then(r => r.data || []).catch(() => [])).find(c => c.id === selectedCustomer.id);
      if (refreshed) setSelectedCustomer(refreshed);
    } catch (e) { toast.error(e.response?.data?.detail || 'Payment failed'); }
    setProcessing(false);
  };

  const loadHistory = async () => {
    try {
      const res = await api.get(`/customers/${selectedCustomer.id}/payment-history`);
      setPayHistory(res.data);
      setHistoryOpen(true);
    } catch { toast.error('Failed to load history'); }
  };

  const getDaysOverdue = (dueDate) => {
    if (!dueDate) return 0;
    return Math.max(0, Math.floor((new Date(payDate) - new Date(dueDate)) / 86400000));
  };

  // ── Left panel: filtered + sorted ──
  const filteredList = (() => {
    let list = [...custList];
    if (listSearch) {
      const q = listSearch.toLowerCase();
      list = list.filter(c => c.name.toLowerCase().includes(q) || c.phone?.includes(listSearch));
    }
    if (sortBy === 'name') {
      list.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      list.sort((a, b) => b.balance - a.balance);
    }
    return list;
  })();

  return (
    <div className="flex h-[calc(100vh-120px)] animate-fadeIn bg-white" data-testid="payments-page">

      {/* ══════════ LEFT: Customer List ══════════ */}
      <div className="w-72 shrink-0 flex flex-col border-r border-slate-200" data-testid="customer-list-panel">
        {/* Search + controls */}
        <div className="p-3 border-b border-slate-100 space-y-2">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input value={listSearch} onChange={e => setListSearch(e.target.value)}
              placeholder="Filter customers..." className="pl-8 h-8 text-sm"
              data-testid="customer-list-search" />
          </div>
          <div className="flex items-center justify-between gap-1">
            {/* Filter toggle: With Balance / All */}
            <div className="flex rounded-md border border-slate-200 overflow-hidden" data-testid="balance-filter-toggle">
              <button
                onClick={() => setShowAll(false)}
                className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                  !showAll ? 'bg-[#1A4D2E] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                }`}
                data-testid="filter-with-balance">
                With Balance
              </button>
              <button
                onClick={() => setShowAll(true)}
                className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                  showAll ? 'bg-[#1A4D2E] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                }`}
                data-testid="filter-all">
                All
              </button>
            </div>
            {/* Sort toggle */}
            <div className="flex rounded-md border border-slate-200 overflow-hidden" data-testid="sort-toggle">
              <button
                onClick={() => setSortBy('balance')}
                title="Sort by balance (highest first)"
                className={`px-1.5 py-1 transition-colors ${
                  sortBy === 'balance' ? 'bg-[#1A4D2E] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                }`}
                data-testid="sort-by-balance">
                <ArrowDown01 size={13} />
              </button>
              <button
                onClick={() => setSortBy('name')}
                title="Sort by name (A-Z)"
                className={`px-1.5 py-1 transition-colors ${
                  sortBy === 'name' ? 'bg-[#1A4D2E] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                }`}
                data-testid="sort-by-name">
                <ArrowDownAZ size={13} />
              </button>
            </div>
          </div>
          {/* Summary count */}
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>{filteredList.length} customer{filteredList.length !== 1 ? 's' : ''}</span>
            <span className="font-mono font-semibold text-red-500">
              {formatPHP(filteredList.reduce((s, c) => s + c.balance, 0))}
            </span>
          </div>
        </div>

        {/* Customer rows */}
        <ScrollArea className="flex-1">
          {filteredList.length === 0 && (
            <p className="text-center text-sm text-slate-400 py-8">
              {showAll ? 'No customers found' : 'No customers with balance'}
            </p>
          )}
          {filteredList.map(c => {
            const isSelected = selectedCustomer?.id === c.id;
            return (
              <button key={c.id} onClick={() => selectCustomer(c)}
                className={`w-full text-left px-3 py-2.5 border-b border-slate-50 hover:bg-slate-50 transition-colors ${
                  isSelected ? 'bg-[#1A4D2E]/5 border-l-2 border-l-[#1A4D2E]' : ''
                }`}
                data-testid={`customer-row-${c.id}`}>
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-slate-800 truncate max-w-[140px]">{c.name}</p>
                  <span className={`text-xs font-bold font-mono ml-1 shrink-0 ${c.balance > 0 ? 'text-red-600' : 'text-slate-400'}`}>
                    {formatPHP(c.balance)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[10px] text-slate-400">
                    {c.invoice_count} inv{c.invoice_count !== 1 ? 's' : ''}
                  </span>
                  {c.overdue_balance > 0 && (
                    <Badge className="text-[9px] bg-red-100 text-red-700 px-1 py-0 h-4">
                      {formatPHP(c.overdue_balance)} DUE
                    </Badge>
                  )}
                  {c.interest_rate > 0 && (
                    <span className="text-[9px] text-amber-500">{c.interest_rate}%</span>
                  )}
                </div>
              </button>
            );
          })}
        </ScrollArea>
      </div>

      {/* ══════════ RIGHT: Payment Form ══════════ */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* ── Compact Header (~110px) ── */}
        <div className="border-b border-slate-200 px-5 py-3 shrink-0 bg-white">
          {/* Row 1: Title + Received From + Total Amount Due */}
          <div className="flex items-center gap-4 mb-2.5">
            <h1 className="text-base font-bold tracking-tight shrink-0" style={{ fontFamily: 'Manrope' }} data-testid="payments-title">
              Customer Payment
            </h1>
            <div className="flex-1 relative max-w-md">
              <Users size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                value={selectedCustomer ? selectedCustomer.name : ''}
                readOnly
                placeholder="Select a customer from the left panel"
                className="pl-8 pr-8 h-9 font-medium bg-white cursor-default"
                data-testid="payment-customer-display"
              />
              {selectedCustomer && (
                <button onClick={clearCustomer} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                  <X size={14} />
                </button>
              )}
            </div>
            {selectedCustomer && (
              <div className="text-right ml-auto" data-testid="customer-balance-display">
                <p className="text-[9px] text-slate-400 uppercase tracking-wide leading-tight">Total Amount Due</p>
                <p className="text-xl font-bold text-red-600 font-mono leading-tight" style={{ fontFamily: 'Manrope' }}>
                  {formatPHP(totalAmountDue)}
                </p>
              </div>
            )}
          </div>

          {/* Row 2: Payment Amt + Date + Ref + Payment Methods */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1.5">
              <Label className="text-[10px] text-slate-400 uppercase">Payment Amt</Label>
              <Input type="number" placeholder="0.00" className="h-9 w-32 text-base font-bold font-mono" data-testid="receive-amount"
                onChange={e => autoApply(e.target.value)} />
            </div>
            <div className="flex items-center gap-1.5">
              <Label className="text-[10px] text-slate-400 uppercase">Date</Label>
              <Input type="date" value={payDate} onChange={e => setPayDate(e.target.value)} className="h-9 w-36" data-testid="payment-date" />
            </div>
            <div className="flex items-center gap-1.5">
              <Label className="text-[10px] text-slate-400 uppercase">Ref #</Label>
              <Input value={payRef} onChange={e => setPayRef(e.target.value)} placeholder="Check #, OR#..." className="h-9 w-32" data-testid="payment-ref" />
            </div>
            <Separator orientation="vertical" className="h-7 hidden sm:block" />
            <div className="flex gap-1 ml-auto" data-testid="payment-methods">
              {METHODS.map(m => {
                const Icon = m.icon;
                const active = payMethod === m.value;
                return (
                  <button key={m.value} onClick={() => setPayMethod(m.value)} data-testid={`pay-method-${m.value}`}
                    className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md border text-xs transition-all ${
                      active ? 'bg-[#1A4D2E] text-white border-[#1A4D2E] shadow-sm' : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                    }`}>
                    <Icon size={13} />
                    <span className="text-[11px] font-medium">{m.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* ═══════════ MAIN CONTENT ═══════════ */}
        {!selectedCustomer ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <Users size={48} className="mx-auto text-slate-200 mb-3" />
              <p className="text-slate-400 text-sm">Select a customer from the left or search to receive payment</p>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col gap-3 mt-3 overflow-hidden min-h-0 px-4">

            {/* ── No interest rate prompt ── */}
            {ratePromptOpen && (
              <div className="shrink-0 flex items-start gap-3 p-3 bg-amber-50 border border-amber-300 rounded-xl" data-testid="no-rate-prompt">
                <AlertTriangle size={16} className="text-amber-500 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-amber-800">No interest rate on file for {selectedCustomer?.name}</p>
                  <p className="text-[11px] text-amber-700 mt-0.5">This account has overdue invoices. Set a monthly rate to auto-compute charges now.</p>
                  <div className="flex items-center gap-2 mt-2 flex-wrap">
                    <div className="flex items-center gap-1 bg-white border border-amber-300 rounded-md px-2 py-1">
                      <input
                        type="number" min="0" step="0.5"
                        value={ratePromptInput}
                        onChange={e => setRatePromptInput(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleSetRateFromPrompt()}
                        placeholder="e.g. 3"
                        className="w-14 text-sm text-center border-0 outline-none font-bold text-amber-700 bg-transparent"
                        data-testid="rate-prompt-input"
                        autoFocus
                      />
                      <span className="text-xs text-amber-600 font-medium">%/mo</span>
                    </div>
                    <Button size="sm" onClick={handleSetRateFromPrompt}
                      disabled={ratePromptSaving || !(parseFloat(ratePromptInput) > 0)}
                      className="bg-amber-500 hover:bg-amber-600 text-white h-7 text-xs gap-1"
                      data-testid="rate-prompt-apply-btn">
                      <Zap size={11} /> {ratePromptSaving ? 'Saving...' : 'Set & Apply'}
                    </Button>
                    <button onClick={() => setRatePromptOpen(false)}
                      className="text-amber-500 hover:text-amber-700 ml-auto"
                      data-testid="rate-prompt-dismiss">
                      <X size={14} />
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* ── Outstanding Transactions Table ── */}
            <Card className="border-slate-200 flex-1 min-h-0 flex flex-col">
              <div className="flex items-center justify-between px-4 py-2 border-b border-slate-100 gap-3 flex-wrap">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-sm font-semibold" style={{ fontFamily: 'Manrope' }}>Outstanding Transactions</span>
                  {invoices.length > 0 && (
                    <button onClick={() => autoApply(totalAmountDue)} className="text-xs text-[#1A4D2E] hover:underline flex items-center gap-1 font-medium" data-testid="auto-apply-all-btn">
                      <Zap size={11} /> Auto-apply all
                    </button>
                  )}
                  {/* Inline interest rate field — saved to customer if not already set */}
                  <div className="flex items-center gap-1 bg-amber-50 border border-amber-200 rounded-md px-2 py-0.5">
                    <Percent size={10} className="text-amber-600" />
                    <Input type="number" min="0" step="0.5" value={interestRateInput}
                      onChange={e => setInterestRateInput(e.target.value)}
                      placeholder="Rate"
                      className="w-12 h-6 text-xs text-center border-0 bg-transparent p-0 font-bold text-amber-700"
                      data-testid="interest-rate-input" />
                    <span className="text-[10px] text-amber-600 font-medium">%/mo</span>
                  </div>
                  {chargesPreview?.total_interest > 0 && (
                    <Badge className="text-[9px] bg-amber-100 text-amber-700 gap-1">
                      <Info size={9} /> {formatPHP(chargesPreview.total_interest)} accrued (preview)
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="sm" className="text-xs gap-1" onClick={() => setStatementOpen(true)}
                    disabled={invoices.length === 0} data-testid="view-statement-btn">
                    <FileText size={12} /> Statement
                  </Button>
                  <Button variant="ghost" size="sm" className="text-xs gap-1 text-amber-600 hover:text-amber-700"
                    onClick={handleGenerateInterest}
                    disabled={!!generatingCharge || !(parseFloat(interestRateInput) > 0)}
                    title="Force-generate interest invoice now (bypass 30-day guard)"
                    data-testid="generate-interest-btn">
                    <RefreshCw size={12} /> {generatingCharge === 'interest' ? 'Generating...' : 'Force INT'}
                  </Button>
                  <Button variant="ghost" size="sm" className="text-xs gap-1 text-red-600 hover:text-red-700"
                    onClick={handleGeneratePenalty} disabled={!!generatingCharge}
                    title={`Apply ${penaltyRate}% penalty on overdue invoices`}
                    data-testid="generate-penalty-btn">
                    <AlertTriangle size={12} /> {generatingCharge === 'penalty' ? 'Applying...' : `Penalty ${penaltyRate}%`}
                  </Button>
                  <Button variant="ghost" size="sm" className="text-xs gap-1" onClick={loadHistory} data-testid="payment-history-btn">
                    <Clock size={12} /> History
                  </Button>
                </div>
              </div>
              {invoices.length === 0 ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center"><Receipt size={36} className="mx-auto mb-2 opacity-20 text-slate-300" /><p className="text-sm text-slate-400">No open invoices</p></div>
                </div>
              ) : (
                <ScrollArea className="flex-1">
                  <table className="w-full text-sm" data-testid="invoices-table">
                    <thead className="bg-slate-50 border-b border-slate-200 sticky top-0 z-10">
                      <tr>
                        <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Date</th>
                        <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Number</th>
                        <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Type</th>
                        <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Orig. Amt</th>
                        <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Amt. Due</th>
                        <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase w-28">Discount</th>
                        <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase w-32">Payment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoices.map((inv) => {
                        const tc = getTypeConfig(inv.sale_type);
                        const daysOver = getDaysOverdue(inv.due_date);
                        const graceP = selectedCustomer?.grace_period || 7;
                        const isOverdue = daysOver > graceP && inv.balance > 0;
                        const rowAmt = rowAmounts[inv.id] || '';
                        const isApplied = parseFloat(rowAmt) > 0;
                        const canDiscount = isDiscountable(inv.sale_type);
                        const discAmt = getDiscountAmount(inv);
                        const mode = discountModes[inv.id] || 'amount';

                        return (
                          <Fragment key={inv.id}>
                          <tr className={`border-b border-slate-100 ${isApplied ? 'bg-emerald-50/40' : discAmt > 0 ? 'bg-blue-50/30' : 'hover:bg-slate-50/50'} transition-colors`}>
                            <td className="px-3 py-2 text-xs text-slate-500">{inv.order_date}</td>
                            <td className="px-3 py-2">
                              <button className="font-mono text-xs text-blue-600 hover:underline flex items-center gap-1"
                                onClick={() => { setSelectedInvoiceId(inv.id); setInvoiceModalOpen(true); }} data-testid={`inv-link-${inv.id}`}>
                                {inv.invoice_number}
                                {inv.edited && <Edit3 size={9} className="text-orange-400" />}
                              </button>
                              {isOverdue && <Badge className="text-[8px] bg-red-100 text-red-700 mt-0.5">{daysOver}d overdue</Badge>}
                            </td>
                            <td className="px-3 py-2"><Badge variant="outline" className={`text-[9px] ${tc.cls}`}>{tc.label}</Badge></td>
                            <td className="px-3 py-2 text-right text-xs font-mono">{formatPHP(inv.grand_total)}</td>
                            <td className="px-3 py-2 text-right font-semibold text-sm font-mono">{formatPHP(inv.balance)}</td>
                            <td className="px-3 py-2 text-right">
                              {canDiscount ? (
                                <div className="flex items-center gap-0.5 justify-end">
                                  <button onClick={() => setDiscountModes(p => ({ ...p, [inv.id]: mode === 'amount' ? 'percent' : 'amount' }))}
                                    className="text-[9px] text-blue-500 hover:text-blue-700 font-medium w-5 text-center shrink-0" title="Toggle % / fixed">
                                    {mode === 'percent' ? '%' : '₱'}
                                  </button>
                                  <Input type="number" min="0" step="0.01" placeholder="0.00"
                                    className="h-7 w-20 text-right text-xs border-blue-200 bg-blue-50/50"
                                    value={rowDiscounts[inv.id] || ''}
                                    onChange={e => setRowDiscounts(p => ({ ...p, [inv.id]: e.target.value }))}
                                    onFocus={e => e.target.select()}
                                    data-testid={`discount-row-${inv.id}`} />
                                  {discAmt > 0 && <span className="text-[9px] text-blue-600 ml-0.5">-{formatPHP(discAmt)}</span>}
                                </div>
                              ) : <span className="text-xs text-slate-300">—</span>}
                            </td>
                            <td className="px-3 py-2 text-right">
                              <Input type="number" min="0" max={inv.balance - discAmt} step="0.01" value={rowAmt} placeholder="0.00"
                                className={`h-8 w-28 text-right text-sm ml-auto font-mono ${isApplied ? 'border-emerald-400 bg-emerald-50' : 'border-slate-200'}`}
                                onChange={e => setRowAmounts(p => ({ ...p, [inv.id]: e.target.value }))}
                                onFocus={e => e.target.select()}
                                data-testid={`payment-row-${inv.id}`} />
                            </td>
                          </tr>
                          {/* Inline interest sub-row — shown when this invoice has accrued interest (no DB invoice yet) */}
                          {interestByInvoice[inv.id] && (
                            <tr className="bg-amber-50/40 border-b border-amber-100" data-testid={`inline-interest-${inv.id}`}>
                              <td className="px-3 py-1" />
                              <td colSpan={2} className="px-3 py-1 text-[10px] text-amber-700 italic">
                                ↳ Interest accrued ({interestByInvoice[inv.id].days_for_interest}d × {interestByInvoice[inv.id].rate}%/mo)
                              </td>
                              <td className="px-3 py-1 text-right text-[10px] text-amber-600 font-mono">
                                ₱{interestByInvoice[inv.id].principal.toFixed(2)} × {interestByInvoice[inv.id].rate}%
                              </td>
                              <td className="px-3 py-1 text-right text-[11px] text-amber-700 font-mono font-semibold">
                                +{formatPHP(interestByInvoice[inv.id].interest_amount)}
                              </td>
                              <td colSpan={2} className="px-3 py-1 text-right text-[9px] text-amber-500 italic">computed live</td>
                            </tr>
                          )}
                          </Fragment>
                        );
                      })}
                      {/* Totals row */}
                      <tr className="bg-slate-50 border-t-2 border-slate-200 font-semibold">
                        <td colSpan={3} className="px-3 py-2 text-right text-xs text-slate-500 uppercase">Totals</td>
                        <td className="px-3 py-2 text-right text-xs font-mono">{formatPHP(invoices.reduce((s, i) => s + (i.grand_total || 0), 0))}</td>
                        <td className="px-3 py-2 text-right text-sm font-mono">{formatPHP(totalOpen)}</td>
                        <td className="px-3 py-2 text-right text-xs font-mono text-blue-600">{totalDiscount > 0 ? `-${formatPHP(totalDiscount)}` : '—'}</td>
                        <td className="px-3 py-2 text-right text-sm font-mono text-emerald-700">{formatPHP(totalApplied)}</td>
                      </tr>
                    </tbody>
                  </table>
                </ScrollArea>
              )}
            </Card>

            {/* ═══════════ FOOTER: Account Summary + Actions ═══════════ */}
            <div className="pb-3 pt-1 shrink-0">
              <div className="flex items-end justify-between gap-4 flex-wrap">
                {/* Left: Memo */}
                <div className="flex-1 min-w-[200px] max-w-sm">
                  <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Memo</Label>
                  <Input value={payMemo} onChange={e => setPayMemo(e.target.value)}
                    placeholder="Optional note for this payment" className="h-9 mt-1" data-testid="payment-memo" />
                </div>

                {/* Center: Account Summary card */}
                <div className="bg-white border border-slate-300 rounded-lg p-3 min-w-[280px]" data-testid="account-summary">
                  <p className="text-[10px] font-bold text-slate-700 uppercase tracking-wide mb-1.5" style={{ fontFamily: 'Manrope' }}>
                    Account Summary
                  </p>
                  <div className="space-y-0.5">
                    <div className="flex justify-between text-xs">
                      <span className="text-slate-600">Outstanding Principal</span>
                      <span className="font-mono font-medium" data-testid="summary-principal">{formatPHP(principalOpen)}</span>
                    </div>
                    <div className="flex justify-between text-xs">
                      <span className="text-amber-700">Accrued Interest Charges</span>
                      <span className="font-mono font-medium text-amber-700" data-testid="summary-interest">{formatPHP(accruedInterestTotal)}</span>
                    </div>
                    <Separator className="my-1" />
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs font-bold text-slate-800">Total Amount Due</span>
                      <span className="font-mono font-bold text-base text-red-600" data-testid="summary-total">{formatPHP(totalAmountDue)}</span>
                    </div>
                    {(totalApplied > 0 || totalDiscount > 0) && (
                      <>
                        <Separator className="my-1" />
                        <div className="flex justify-between text-[11px]">
                          <span className="text-emerald-700">Applied Payment</span>
                          <span className="font-mono font-semibold text-emerald-700">{formatPHP(totalApplied)}</span>
                        </div>
                        {totalDiscount > 0 && (
                          <div className="flex justify-between text-[11px]">
                            <span className="text-blue-600 flex items-center gap-1"><Tag size={9} /> Discount</span>
                            <span className="font-mono font-semibold text-blue-600">{formatPHP(totalDiscount)}</span>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  <p className="text-[9px] text-slate-400 italic mt-1 leading-tight">
                    <Info size={8} className="inline mr-0.5" /> Interest is applied first. Payment covers oldest invoices.
                  </p>
                </div>

                {/* Right: Actions */}
                <div className="flex flex-col gap-2">
                  <Button onClick={handleApplyPayment} disabled={processing || !hasUnsavedAmounts}
                    className="h-10 px-6 bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="apply-payment-btn">
                    {processing ? 'Processing...' : 'Save & Apply'}
                  </Button>
                  <Button variant="outline" size="sm" className="text-xs" onClick={() => { setRowAmounts({}); setRowDiscounts({}); setDiscountModes({}); }}>
                    Clear
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Payment History Dialog ── */}
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Payment History — {selectedCustomer?.name}</DialogTitle>
            <DialogDescription>All payments received from this customer</DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[420px]">
            <Table>
              <TableHeader><TableRow className="bg-slate-50">
                <TableHead className="text-xs">Date</TableHead>
                <TableHead className="text-xs">Invoice #</TableHead>
                <TableHead className="text-xs">Type</TableHead>
                <TableHead className="text-xs">Method</TableHead>
                <TableHead className="text-xs">Reference</TableHead>
                <TableHead className="text-xs text-right">Amount</TableHead>
                <TableHead className="text-xs">By</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {payHistory.length === 0 && (
                  <TableRow><TableCell colSpan={7} className="text-center py-6 text-slate-400">No payment history</TableCell></TableRow>
                )}
                {payHistory.map((p, i) => (
                  <TableRow key={i} className={p.method === 'Discount' ? 'bg-blue-50/40' : ''}>
                    <TableCell className="text-xs">{p.date}</TableCell>
                    <TableCell className="font-mono text-xs">{p.invoice_number}</TableCell>
                    <TableCell><Badge variant="outline" className={`text-[9px] ${getTypeConfig(p.sale_type).cls}`}>{getTypeConfig(p.sale_type).label}</Badge></TableCell>
                    <TableCell className="text-xs">
                      {p.method === 'Discount' ? <Badge className="text-[9px] bg-blue-100 text-blue-700">Discount</Badge> : p.method}
                    </TableCell>
                    <TableCell className="text-xs text-slate-400">{p.reference || '—'}</TableCell>
                    <TableCell className="text-right font-medium text-sm">{formatPHP(p.amount)}</TableCell>
                    <TableCell className="text-xs text-slate-400">{p.recorded_by}</TableCell>
                  </TableRow>
                ))}
                {payHistory.length > 0 && (
                  <TableRow className="bg-slate-50">
                    <TableCell colSpan={5} className="text-right text-xs font-semibold text-slate-500">Total Received</TableCell>
                    <TableCell className="text-right font-bold">{formatPHP(payHistory.reduce((s, p) => s + (p.amount || 0), 0))}</TableCell>
                    <TableCell />
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      {/* ── Statement / All Open Invoices Dialog (QuickBooks-style) ── */}
      <Dialog open={statementOpen} onOpenChange={setStatementOpen}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>
              Open Invoices Statement — {selectedCustomer?.name}
            </DialogTitle>
            <DialogDescription>
              Original receipt amount, partial payments, computed interest, and current balance for each open invoice.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-[520px]">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs">Date</TableHead>
                  <TableHead className="text-xs">Invoice #</TableHead>
                  <TableHead className="text-xs">Type</TableHead>
                  <TableHead className="text-xs">Due</TableHead>
                  <TableHead className="text-xs text-right">Original Amount</TableHead>
                  <TableHead className="text-xs text-right">Payments / Adj.</TableHead>
                  <TableHead className="text-xs text-right">Accrued Interest</TableHead>
                  <TableHead className="text-xs text-right">Amount Due</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invoices.length === 0 && (
                  <TableRow><TableCell colSpan={8} className="text-center py-6 text-slate-400">No open invoices</TableCell></TableRow>
                )}
                {invoices.map((inv) => {
                  const tc = getTypeConfig(inv.sale_type);
                  const paid = round2((inv.grand_total || 0) - (inv.balance || 0));
                  const intRow = interestByInvoice[inv.id];
                  const daysOver = getDaysOverdue(inv.due_date);
                  return (
                    <TableRow key={inv.id} data-testid={`stmt-row-${inv.id}`}>
                      <TableCell className="text-xs">{inv.order_date}</TableCell>
                      <TableCell className="font-mono text-xs">{inv.invoice_number}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[9px] ${tc.cls}`}>{tc.label}</Badge>
                      </TableCell>
                      <TableCell className="text-xs">
                        {inv.due_date || '—'}
                        {daysOver > 0 && (
                          <span className="text-[9px] text-red-500 ml-1">({daysOver}d over)</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right text-xs font-mono">{formatPHP(inv.grand_total || 0)}</TableCell>
                      <TableCell className="text-right text-xs font-mono text-emerald-600">
                        {paid > 0 ? `−${formatPHP(paid)}` : '—'}
                      </TableCell>
                      <TableCell className="text-right text-xs font-mono text-amber-700">
                        {intRow ? `+${formatPHP(intRow.interest_amount)}` : '—'}
                      </TableCell>
                      <TableCell className="text-right font-mono font-semibold text-sm">
                        {formatPHP(round2((inv.balance || 0) + (intRow ? intRow.interest_amount : 0)))}
                      </TableCell>
                    </TableRow>
                  );
                })}
                {invoices.length > 0 && (
                  <>
                    <TableRow className="bg-slate-50 border-t-2 border-slate-300">
                      <TableCell colSpan={4} className="text-right text-xs font-semibold text-slate-700 uppercase">Subtotals</TableCell>
                      <TableCell className="text-right font-mono text-xs font-semibold">
                        {formatPHP(invoices.reduce((s, i) => s + (i.grand_total || 0), 0))}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs font-semibold text-emerald-600">
                        −{formatPHP(invoices.reduce((s, i) => s + ((i.grand_total || 0) - (i.balance || 0)), 0))}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs font-semibold text-amber-700">
                        +{formatPHP(interestComputedInline)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm font-bold text-red-600" data-testid="stmt-total-due">
                        {formatPHP(totalAmountDue)}
                      </TableCell>
                    </TableRow>
                  </>
                )}
              </TableBody>
            </Table>
          </ScrollArea>
          <div className="flex items-center justify-between pt-2 border-t border-slate-100 text-[11px] text-slate-500">
            <span>
              <Info size={11} className="inline mr-1" />
              Accrued interest is computed live and is only billed when payment is recorded.
            </span>
            <Button size="sm" variant="outline" onClick={() => setStatementOpen(false)} data-testid="stmt-close-btn">
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Invoice Detail Modal */}
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        saleId={selectedInvoiceId}
        onUpdated={() => { if (selectedCustomer) { loadInvoices(selectedCustomer.id); loadChargesPreview(selectedCustomer.id); } }}
      />
    </div>
  );
}
