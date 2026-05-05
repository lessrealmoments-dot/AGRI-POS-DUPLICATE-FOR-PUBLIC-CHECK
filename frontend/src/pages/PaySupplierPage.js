import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import { Separator } from '../components/ui/separator';
import { ScrollArea } from '../components/ui/scroll-area';
import {
  Search, AlertTriangle, CheckCircle2, Receipt,
  Banknote, Building2, X,
  Zap, Shield, Lock, Upload, FileText
} from 'lucide-react';
import { localTodayStr } from '../lib/dateFormat';
import { toast } from 'sonner';
import UploadQRDialog from '../components/UploadQRDialog';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import CalcInput from '../components/CalcInput';

// payMethod is derived from fundSource — no separate method picker needed
const FUND_METHOD_MAP = {
  cashier: 'Cash',
  safe:    'Cash',
  bank:    'Check/Bank Transfer',
  digital: 'Digital Transfer',
};

function round2(n) { return Math.round(n * 100) / 100; }

export default function PaySupplierPage() {
  const { currentBranch } = useAuth();
  const today = localTodayStr();

  // Supplier list + selection
  const [suppliers, setSuppliers] = useState([]);
  const [listSearch, setListSearch] = useState('');       // left panel filter
  const [payToSearch, setPayToSearch] = useState('');     // header "Pay To" search
  const [payToDropdownOpen, setPayToDropdownOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const payToRef = useRef(null);

  // Checkbox-based PO selection: { [po_id]: amount }
  const [checkedPos, setCheckedPos] = useState({});
  const [budgetInput, setBudgetInput] = useState('');     // "Payment Amount" field

  // Payment header fields
  const [payDate, setPayDate] = useState(today);
  const [payRef, setPayRef] = useState('');
  const [payMemo, setPayMemo] = useState('');
  const [fundSource, setFundSource] = useState('cashier');
  const [payPin, setPayPin] = useState('');

  // payMethod is derived — no separate picker
  const payMethod = FUND_METHOD_MAP[fundSource] || 'Cash';

  // Fund balances
  const [walletBalances, setWalletBalances] = useState({ cashier: 0, safe: 0, bank: 0, digital: 0 });

  // Processing
  const [processing, setProcessing] = useState(false);

  // PO detail modal
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedPoNumber, setSelectedPoNumber] = useState(null);

  // Post-payment receipt upload (kept from before)
  const [paidBatch, setPaidBatch] = useState(null);
  const [batchUploadOpen, setBatchUploadOpen] = useState(false);
  const [batchCurrentPoId, setBatchCurrentPoId] = useState(null);
  const [oneReceiptMode, setOneReceiptMode] = useState(false);
  const [psUploadQROpen, setPsUploadQROpen] = useState(false);
  const [psUploadPOId, setPsUploadPOId] = useState(null);

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadSuppliers = useCallback(async () => {
    try {
      const params = currentBranch ? { branch_id: currentBranch.id } : {};
      const res = await api.get('/purchase-orders/payables-by-supplier', { params });
      setSuppliers(res.data || []);
    } catch { toast.error('Failed to load suppliers'); }
  }, [currentBranch]);

  const loadWallets = useCallback(async () => {
    if (!currentBranch?.id) return;
    try {
      const res = await api.get('/fund-wallets', { params: { branch_id: currentBranch.id } });
      const ws = res.data || [];
      setWalletBalances({
        cashier: ws.find(w => w.type === 'cashier')?.balance || 0,
        safe:    ws.find(w => w.type === 'safe')?.balance || 0,
        bank:    ws.find(w => w.type === 'bank')?.balance || 0,
        digital: ws.find(w => w.type === 'digital')?.balance || 0,
      });
    } catch {}
  }, [currentBranch]);

  useEffect(() => { loadSuppliers(); loadWallets(); }, [loadSuppliers, loadWallets]);

  // ── Supplier selection ────────────────────────────────────────────────────

  const selectSupplier = (sup) => {
    setSelected(sup);
    setPayToSearch(sup.vendor);
    setPayToDropdownOpen(false);
    setCheckedPos({});
    setBudgetInput('');
    setPayRef('');
    setPayMemo('');
    setPayPin('');
  };

  const clearSupplier = () => {
    setSelected(null);
    setPayToSearch('');
    setCheckedPos({});
    setBudgetInput('');
  };

  // ── Derived values ────────────────────────────────────────────────────────

  const totalApplied  = Object.values(checkedPos).reduce((s, v) => s + (parseFloat(v) || 0), 0);
  const budget        = parseFloat(budgetInput) || 0;
  const unusedBudget  = budget > 0 ? round2(Math.max(0, budget - totalApplied)) : null;
  const isBankOrDig   = fundSource === 'bank' || fundSource === 'digital';
  const currentWalBal = walletBalances[fundSource] ?? 0;
  const isShort       = totalApplied > currentWalBal && totalApplied > 0;

  // Overdue sum for a supplier (computed client-side)
  const getOverdue = (sup) =>
    sup.pos.reduce((s, po) => {
      const bal = po.balance ?? po.subtotal ?? 0;
      return (po.due_date && po.due_date < today && bal > 0) ? s + bal : s;
    }, 0);

  // ── Smart allocation ──────────────────────────────────────────────────────

  const sortedPos = (pos) =>
    [...pos].sort((a, b) => (a.due_date || '9999-12-31').localeCompare(b.due_date || '9999-12-31'));

  const autoAllocate = (budgetAmt, pos) => {
    const amt = parseFloat(budgetAmt) || 0;
    if (amt <= 0) { setCheckedPos({}); return; }
    let remaining = amt;
    const next = {};
    for (const po of sortedPos(pos)) {
      const bal = round2(po.balance ?? po.subtotal ?? 0);
      if (bal <= 0 || remaining <= 0) continue;
      const apply = round2(Math.min(remaining, bal));
      next[po.id] = apply;
      remaining = round2(remaining - apply);
    }
    setCheckedPos(next);
  };

  const handleBudgetChange = (val) => {
    setBudgetInput(val);
    if (selected) autoAllocate(val, selected.pos);
  };

  const togglePO = (po) => {
    const bal = round2(po.balance ?? po.subtotal ?? 0);
    if (checkedPos[po.id] !== undefined) {
      // Uncheck — release amount back to pool
      setCheckedPos(prev => { const next = { ...prev }; delete next[po.id]; return next; });
    } else {
      // Check — apply unused budget (or full balance if no budget set)
      const unused = budget > 0 ? round2(Math.max(0, budget - totalApplied)) : bal;
      const apply  = round2(Math.min(unused, bal));
      setCheckedPos(prev => ({ ...prev, [po.id]: apply }));
    }
  };

  const payAllDue = () => {
    if (!selected) return;
    const overduePOs = selected.pos.filter(po => po.due_date && po.due_date < today && (po.balance ?? po.subtotal ?? 0) > 0);
    const total = round2(overduePOs.reduce((s, po) => s + (po.balance ?? po.subtotal ?? 0), 0));
    setBudgetInput(total.toFixed(2));
    autoAllocate(total, selected.pos.filter(po => po.due_date && po.due_date < today));
  };

  const payAll = () => {
    if (!selected) return;
    const total = round2(selected.pos.reduce((s, po) => s + (po.balance ?? po.subtotal ?? 0), 0));
    setBudgetInput(total.toFixed(2));
    autoAllocate(total, selected.pos);
  };

  // ── Submit payment ────────────────────────────────────────────────────────

  const handlePayment = async () => {
    const allocations = Object.entries(checkedPos)
      .filter(([, amt]) => parseFloat(amt) > 0)
      .map(([po_id, amt]) => ({ po_id, amount: parseFloat(amt) }));

    if (allocations.length === 0) { toast.error('Select at least one PO to pay'); return; }
    if (!payPin.trim()) { toast.error('PIN or TOTP is required to authorize payment'); return; }
    if (!currentBranch?.id) { toast.error('Select a specific branch first'); return; }

    setProcessing(true);
    let totalPaid = 0, errors = [], paidPoIds = [];

    for (const alloc of allocations) {
      try {
        await api.post(`/purchase-orders/${alloc.po_id}/pay`, {
          amount: alloc.amount, fund_source: fundSource,
          method: payMethod, reference: payRef,
          payment_date: payDate, pin: payPin,
        });
        totalPaid += alloc.amount;
        paidPoIds.push(alloc.po_id);
      } catch (e) {
        const detail = e.response?.data?.detail;
        if (typeof detail === 'object' && detail?.type === 'insufficient_funds') {
          toast.error(detail.message); setProcessing(false); return;
        }
        errors.push(typeof detail === 'string' ? detail : 'Payment failed');
      }
    }

    if (errors.length) {
      toast.error(`${errors.length} payment(s) failed: ${errors[0]}`);
    } else {
      toast.success(`₱${totalPaid.toFixed(2)} paid to ${selected.vendor} from ${fundSource}`);
      const batchPos = allocations
        .filter(a => paidPoIds.includes(a.po_id))
        .map(a => ({ po_id: a.po_id, po_number: selected.pos.find(p => p.id === a.po_id)?.po_number || a.po_id, amount: a.amount, uploaded: false }));
      if (batchPos.length === 1) { setPsUploadPOId(batchPos[0].po_id); setPsUploadQROpen(true); }
      else { setPaidBatch({ vendor: selected.vendor, totalPaid, fundSource, pos: batchPos }); setBatchUploadOpen(true); }
    }

    setCheckedPos({});
    setBudgetInput('');
    setPayRef('');
    setPayMemo('');
    setPayPin('');
    await loadSuppliers();
    await loadWallets();
    const refreshed = (await api.get('/purchase-orders/payables-by-supplier', {
      params: currentBranch ? { branch_id: currentBranch.id } : {}
    }).then(r => r.data || []).catch(() => [])).find(s => s.vendor === selected.vendor);
    setSelected(refreshed || null);
    setProcessing(false);
  };

  // ── Filtered lists ────────────────────────────────────────────────────────

  const filteredList = listSearch
    ? suppliers.filter(s => s.vendor.toLowerCase().includes(listSearch.toLowerCase()))
    : suppliers;

  const payToSuggestions = payToSearch && !selected
    ? suppliers.filter(s => s.vendor.toLowerCase().includes(payToSearch.toLowerCase())).slice(0, 8)
    : [];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-[calc(100vh-120px)] animate-fadeIn bg-white" data-testid="pay-supplier-page">

      {/* ══════════ LEFT: Supplier List ══════════ */}
      <div className="w-64 shrink-0 flex flex-col border-r border-slate-200">
        <div className="p-3 border-b border-slate-100">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input value={listSearch} onChange={e => setListSearch(e.target.value)}
              placeholder="Filter suppliers..." className="pl-8 h-8 text-sm" />
          </div>
        </div>
        <ScrollArea className="flex-1">
          {filteredList.length === 0 && (
            <p className="text-center text-sm text-slate-400 py-8">No unpaid suppliers</p>
          )}
          {filteredList.map(s => {
            const overdue = getOverdue(s);
            const isSelected = selected?.vendor === s.vendor;
            return (
              <button key={s.vendor} onClick={() => selectSupplier(s)}
                className={`w-full text-left px-3 py-2.5 border-b border-slate-50 hover:bg-slate-50 transition-colors ${
                  isSelected ? 'bg-[#1A4D2E]/5 border-l-2 border-l-[#1A4D2E]' : ''
                }`}
                data-testid={`supplier-row-${s.vendor}`}>
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-slate-800 truncate max-w-[140px]">{s.vendor}</p>
                  <span className="text-xs font-bold font-mono text-red-600 ml-1 shrink-0">{formatPHP(s.total_owed)}</span>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[10px] text-slate-400">{s.pos.length} PO{s.pos.length !== 1 ? 's' : ''}</span>
                  {overdue > 0 && (
                    <Badge className="text-[9px] bg-red-100 text-red-700 px-1 py-0 h-4">
                      {formatPHP(overdue)} OVR
                    </Badge>
                  )}
                </div>
              </button>
            );
          })}
        </ScrollArea>
      </div>

      {/* ══════════ RIGHT: QB-Style Payment Form ══════════ */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* ── Header ── */}
        <div className="border-b border-slate-200 px-5 py-4 shrink-0 bg-white">
          <div className="flex items-start justify-between mb-4">
            <h1 className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>
              Pay Supplier
            </h1>
            {selected && (
              <div className="text-right">
                <p className="text-[10px] text-slate-400 uppercase tracking-wide">Total Owed</p>
                <p className="text-2xl font-bold text-red-600 font-mono" style={{ fontFamily: 'Manrope' }}>
                  {formatPHP(selected.total_owed)}
                </p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-y-3">
            {/* Pay To + Amount + Date + Ref — full width now (no method icons) */}
            <div className="space-y-2.5">
              {/* PAY TO */}
              <div className="flex items-center gap-3">
                <Label className="text-xs text-slate-500 w-20 shrink-0 uppercase tracking-wide">Pay To</Label>
                <div className="flex-1 relative" ref={payToRef}>
                  <div className="relative">
                    <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <Input
                      value={selected ? selected.vendor : payToSearch}
                      onChange={e => { setPayToSearch(e.target.value); setPayToDropdownOpen(true); if (selected) clearSupplier(); }}
                      onFocus={() => { if (!selected) setPayToDropdownOpen(true); }}
                      onBlur={() => setTimeout(() => setPayToDropdownOpen(false), 200)}
                      placeholder="Search or select a supplier..."
                      className="pl-8 h-9 font-medium"
                      data-testid="pay-to-search"
                    />
                    {selected && (
                      <button onClick={clearSupplier} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                        <X size={14} />
                      </button>
                    )}
                  </div>
                  {payToDropdownOpen && payToSuggestions.length > 0 && (
                    <div className="absolute z-50 top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                      {payToSuggestions.map(s => (
                        <button key={s.vendor} onMouseDown={() => selectSupplier(s)}
                          className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm border-b last:border-0 flex items-center justify-between"
                          data-testid={`pay-to-option-${s.vendor}`}>
                          <span className="font-medium">{s.vendor}</span>
                          <span className="text-xs font-bold text-red-600 font-mono">{formatPHP(s.total_owed)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* PAYMENT AMT + DATE + REF */}
              <div className="flex items-center gap-3 flex-wrap">
                <Label className="text-xs text-slate-500 w-20 shrink-0 uppercase tracking-wide">Payment</Label>
                <CalcInput placeholder="0.00" value={budgetInput}
 onChange={(v) => handleBudgetChange(v)}
 className="h-9 w-36 text-lg font-bold font-mono"
 data-testid="budget-input" />
                <Separator orientation="vertical" className="h-7 hidden sm:block" />
                <div className="flex items-center gap-1.5">
                  <Label className="text-[10px] text-slate-400 uppercase">Date</Label>
                  <Input type="date" value={payDate} onChange={e => setPayDate(e.target.value)} className="h-9 w-36" />
                </div>
                <div className="flex items-center gap-1.5">
                  <Label className="text-[10px] text-slate-400 uppercase">{fundSource === 'bank' ? 'Check # / Ref' : 'Ref #'}</Label>
                  <Input value={payRef} onChange={e => setPayRef(e.target.value)}
                    placeholder={fundSource === 'bank' ? 'Check # or bank ref' : 'Optional'}
                    className="h-9 w-36" data-testid="pay-ref-input" />
                </div>
              </div>
            </div>
          </div>

          {/* Fund source — compact row */}
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100">
            <Label className="text-[10px] text-slate-400 uppercase shrink-0">Pay From</Label>
            <div className="flex gap-1.5 flex-wrap">
              {[
                { key: 'cashier', label: 'Cashier',       lock: false },
                { key: 'safe',    label: 'Safe',           lock: false },
                { key: 'bank',    label: 'Check / Bank',   lock: true  },
                { key: 'digital', label: 'Digital',        lock: true  },
              ].map(f => {
                const bal = walletBalances[f.key] ?? 0;
                const active = fundSource === f.key;
                const insuff = active && isShort;
                return (
                  <button key={f.key} onClick={() => setFundSource(f.key)}
                    data-testid={`fund-${f.key}`}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs transition-all ${
                      active ? 'border-[#1A4D2E] bg-[#1A4D2E]/5 font-semibold' : 'border-slate-200 hover:border-slate-300'
                    } ${insuff ? 'border-red-400 bg-red-50' : ''}`}>
                    {f.lock && <Lock size={9} className="text-amber-500" />}
                    <span>{f.label}</span>
                    <span className={`font-mono text-[10px] ${insuff ? 'text-red-600' : 'text-slate-500'}`}>{formatPHP(bal)}</span>
                  </button>
                );
              })}
            </div>
            {isBankOrDig && (
              <span className="text-[10px] text-amber-600 flex items-center gap-1 ml-2">
                <Lock size={9} /> Admin/TOTP only
              </span>
            )}
          </div>
        </div>

        {/* ── No supplier selected ── */}
        {!selected ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <Building2 size={48} className="mx-auto text-slate-200 mb-3" />
              <p className="text-slate-400 text-sm">Select a supplier from the left or search above</p>
            </div>
          </div>
        ) : (
          <>
            {/* ── PO Table ── */}
            <Card className="mx-4 mt-3 border-slate-200 flex-1 min-h-0 flex flex-col overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2 border-b border-slate-100 shrink-0">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold" style={{ fontFamily: 'Manrope' }}>
                    Open Purchase Orders
                  </span>
                  {selected.pos.filter(po => po.due_date && po.due_date < today).length > 0 && (
                    <button onClick={payAllDue}
                      className="text-xs text-red-600 hover:underline flex items-center gap-1 font-medium"
                      data-testid="pay-all-due-btn">
                      <AlertTriangle size={11} /> Pay All Due
                    </button>
                  )}
                  <button onClick={payAll}
                    className="text-xs text-[#1A4D2E] hover:underline flex items-center gap-1 font-medium"
                    data-testid="pay-all-btn">
                    <Zap size={11} /> Pay All
                  </button>
                  {Object.keys(checkedPos).length > 0 && (
                    <button onClick={() => { setCheckedPos({}); setBudgetInput(''); }}
                      className="text-xs text-slate-400 hover:text-slate-600 hover:underline"
                      data-testid="clear-btn">
                      Clear
                    </button>
                  )}
                </div>
                {unusedBudget !== null && unusedBudget > 0 && (
                  <div className="flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1">
                    <Banknote size={11} />
                    <span>₱{unusedBudget.toFixed(2)} unused — click a PO to apply</span>
                  </div>
                )}
              </div>

              <ScrollArea className="flex-1">
                <table className="w-full text-sm" data-testid="po-table">
                  <thead className="bg-slate-50 border-b border-slate-200 sticky top-0 z-10">
                    <tr>
                      <th className="w-8 px-3 py-2"></th>
                      <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">PO #</th>
                      <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Due</th>
                      <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">DR #</th>
                      <th className="text-left px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Status</th>
                      <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Orig. Amt</th>
                      <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase">Balance</th>
                      <th className="text-right px-3 py-2 text-[10px] font-semibold text-slate-500 uppercase w-32">Paying</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPos(selected.pos).map(po => {
                      const bal = round2(po.balance ?? po.subtotal ?? 0);
                      const checked = checkedPos[po.id] !== undefined;
                      const amt = checkedPos[po.id] ?? 0;
                      const isPartial = checked && amt < bal - 0.005;
                      const isOverdue = po.due_date && po.due_date < today;
                      const isDueSoon = po.due_date && po.due_date >= today && po.due_date <= new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
                      const daysOverdue = isOverdue ? Math.floor((new Date(today) - new Date(po.due_date)) / 86400000) : 0;

                      return (
                        <tr key={po.id}
                          className={`border-b border-slate-100 transition-colors cursor-pointer ${
                            checked && !isPartial ? 'bg-emerald-50/50' :
                            isPartial ? 'bg-amber-50/50' :
                            'hover:bg-slate-50/50'
                          }`}
                          onClick={() => togglePO(po)}
                          data-testid={`po-row-${po.id}`}>

                          {/* Checkbox */}
                          <td className="px-3 py-2.5 text-center" onClick={e => { e.stopPropagation(); togglePO(po); }}>
                            <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition-all ${
                              checked ? (isPartial ? 'border-amber-500 bg-amber-500' : 'border-emerald-600 bg-emerald-600') : 'border-slate-300'
                            }`}>
                              {checked && <CheckCircle2 size={10} className="text-white" strokeWidth={3} />}
                            </div>
                          </td>

                          <td className="px-3 py-2.5">
                            <button className="font-mono text-xs text-blue-600 hover:underline"
                              onClick={e => { e.stopPropagation(); setSelectedPoNumber(po.po_number); setInvoiceModalOpen(true); }}
                              data-testid={`po-link-${po.id}`}>
                              {po.po_number}
                            </button>
                          </td>

                          <td className="px-3 py-2.5 text-xs">
                            {isOverdue ? (
                              <Badge className="text-[9px] bg-red-100 text-red-700 border-red-200">
                                {daysOverdue}d overdue
                              </Badge>
                            ) : isDueSoon ? (
                              <Badge className="text-[9px] bg-amber-100 text-amber-700 border-amber-200">
                                {po.due_date === today ? 'Today' : `Due ${po.due_date}`}
                              </Badge>
                            ) : (
                              <span className="text-slate-400">{po.due_date || '—'}</span>
                            )}
                          </td>

                          <td className="px-3 py-2.5 text-xs text-slate-400 font-mono">{po.dr_number || '—'}</td>

                          <td className="px-3 py-2.5">
                            <Badge className={`text-[9px] ${po.status === 'received' ? 'bg-emerald-100 text-emerald-700' : 'bg-blue-100 text-blue-700'}`}>
                              {po.status === 'received' ? 'Received' : po.status || 'Pending'}
                            </Badge>
                          </td>

                          <td className="px-3 py-2.5 text-right text-xs font-mono text-slate-500">
                            {formatPHP(po.grand_total ?? po.subtotal ?? 0)}
                          </td>

                          <td className="px-3 py-2.5 text-right font-semibold text-sm font-mono">
                            {formatPHP(bal)}
                          </td>

                          <td className="px-3 py-2.5 text-right" onClick={e => e.stopPropagation()}>
                            {checked ? (
                              <div className="flex flex-col items-end gap-0.5">
                                <CalcInput value={amt === 0 ? '' : amt}
 placeholder="0.00"
 className={`h-8 w-28 text-right text-sm font-mono ${isPartial ? 'border-amber-400 bg-amber-50' : 'border-emerald-400 bg-emerald-50'}`}
 onChange={(raw) => {
 const v = Math.min(parseFloat(raw) || 0, bal);
 setCheckedPos(prev => ({ ...prev, [po.id]: round2(v) }));
 }}
 data-testid={`pay-amount-${po.id}`} selectOnFocus />
                                {isPartial && <span className="text-[9px] text-amber-600 font-medium">Partial</span>}
                              </div>
                            ) : (
                              <span className="text-xs text-slate-300">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}

                    {/* Totals row */}
                    <tr className="bg-slate-50 border-t-2 border-slate-200 font-semibold">
                      <td colSpan={5} className="px-3 py-2 text-right text-xs text-slate-500 uppercase">Totals</td>
                      <td className="px-3 py-2 text-right text-xs font-mono">
                        {formatPHP(selected.pos.reduce((s, p) => s + (p.grand_total ?? p.subtotal ?? 0), 0))}
                      </td>
                      <td className="px-3 py-2 text-right text-sm font-mono">{formatPHP(selected.total_owed)}</td>
                      <td className="px-3 py-2 text-right text-sm font-mono text-emerald-700">
                        {totalApplied > 0 ? formatPHP(totalApplied) : '—'}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </ScrollArea>
            </Card>

            {/* ── QB-Style Footer ── */}
            <div className="px-4 pb-4 pt-3 shrink-0">
              <div className="flex items-end justify-between gap-6 flex-wrap">
                {/* Left: Memo */}
                <div className="flex-1 min-w-[180px] max-w-sm">
                  <Label className="text-[10px] text-slate-400 uppercase tracking-wide">Memo</Label>
                  <Input value={payMemo} onChange={e => setPayMemo(e.target.value)}
                    placeholder="Optional note (e.g. check #, batch reference)" className="h-9 mt-1" />
                </div>

                {/* Right: Summary + PIN + Action */}
                <div className="flex items-end gap-4">
                  {/* QB-style summary panel */}
                  <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 min-w-[230px]" data-testid="payment-summary">
                    <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-2">
                      Amounts for Selected POs
                    </p>
                    <div className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-500">POs Selected</span>
                        <span className="font-medium">{Object.keys(checkedPos).length} of {selected.pos.length}</span>
                      </div>
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-500">Total Owed (selected)</span>
                        <span className="font-mono font-medium">
                          {formatPHP(selected.pos.filter(p => checkedPos[p.id] !== undefined).reduce((s, p) => s + (p.balance ?? p.subtotal ?? 0), 0))}
                        </span>
                      </div>
                      <div className="flex justify-between text-xs">
                        <span className="text-slate-500">Applying</span>
                        <span className="font-mono font-bold text-[#1A4D2E]">{formatPHP(totalApplied)}</span>
                      </div>
                      {unusedBudget !== null && (
                        <div className="flex justify-between text-xs">
                          <span className="text-amber-600">Unused Budget</span>
                          <span className="font-mono font-medium text-amber-600">{formatPHP(unusedBudget)}</span>
                        </div>
                      )}
                      {isShort && (
                        <div className="flex items-center gap-1 text-[10px] text-red-600 pt-1">
                          <AlertTriangle size={10} />
                          Short ₱{round2(totalApplied - currentWalBal).toFixed(2)} in {fundSource}
                        </div>
                      )}
                      <Separator className="my-1" />
                      <div className="flex justify-between text-xs font-semibold">
                        <span className="text-slate-600">Remaining Balance</span>
                        <span className="font-mono text-red-600">
                          {formatPHP(round2(selected.total_owed - totalApplied))}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* PIN + Save */}
                  <div className="flex flex-col gap-1.5">
                    <div className="relative">
                      <Shield size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input type="password" autoComplete="new-password"
                        value={payPin} onChange={e => setPayPin(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handlePayment(); }}
                        placeholder={isBankOrDig ? 'Admin PIN / TOTP' : 'PIN / TOTP'}
                        className="h-10 pl-8 w-40 font-mono"
                        data-testid="pay-pin-input" />
                    </div>
                    <Button onClick={handlePayment} data-testid="save-payment-btn"
                      disabled={processing || totalApplied <= 0 || isShort || !payPin.trim()}
                      className="h-10 bg-[#1A4D2E] hover:bg-[#14532d] text-white disabled:opacity-40">
                      {processing ? 'Processing...' : `Save & Pay ${totalApplied > 0 ? formatPHP(totalApplied) : ''}`}
                    </Button>
                    <p className="text-[9px] text-slate-400 text-center">Receipt upload required after</p>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ══════════ Batch Receipt Upload Modal ══════════ */}
      {batchUploadOpen && paidBatch && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" data-testid="batch-upload-modal">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
            <div className="bg-[#1A4D2E] px-5 py-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white font-semibold text-sm" style={{ fontFamily: 'Manrope' }}>Upload Payment Receipts</p>
                  <p className="text-emerald-200 text-[11px] mt-0.5">{paidBatch.vendor} · ₱{paidBatch.totalPaid.toFixed(2)} from {paidBatch.fundSource}</p>
                </div>
                <div className="text-right">
                  <p className="text-white text-xs font-semibold">{paidBatch.pos.filter(p => p.uploaded).length} / {paidBatch.pos.length}</p>
                  <p className="text-emerald-300 text-[10px]">uploaded</p>
                </div>
              </div>
              <div className="mt-2 h-1.5 bg-emerald-900 rounded-full overflow-hidden">
                <div className="h-full bg-emerald-300 rounded-full transition-all duration-300"
                  style={{ width: `${(paidBatch.pos.filter(p => p.uploaded).length / paidBatch.pos.length) * 100}%` }} />
              </div>
            </div>
            <div className="p-4 space-y-2 max-h-72 overflow-y-auto">
              {/* Collection receipt toggle */}
              <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 mb-3">
                <div className="flex items-center gap-2">
                  <FileText size={13} className="text-slate-500" />
                  <div>
                    <p className="text-xs font-semibold text-slate-700">Collection Receipt</p>
                    <p className="text-[10px] text-slate-400">One receipt covers all POs</p>
                  </div>
                </div>
                <button onClick={() => setOneReceiptMode(v => !v)}
                  className={`relative w-10 h-5 rounded-full transition-colors shrink-0 ${oneReceiptMode ? 'bg-[#1A4D2E]' : 'bg-slate-300'}`}
                  data-testid="one-receipt-toggle">
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${oneReceiptMode ? 'translate-x-5' : 'translate-x-0.5'}`} />
                </button>
              </div>

              {oneReceiptMode ? (
                <div className="space-y-3">
                  <div className="bg-blue-50 border border-blue-200 rounded-xl px-3 py-2.5 text-[11px] text-blue-700">
                    Upload once — receipt linked to all {paidBatch.pos.length} POs automatically.
                  </div>
                  {paidBatch.pos.every(p => p.uploaded) ? (
                    <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-3">
                      <CheckCircle2 size={16} className="text-emerald-600 shrink-0" />
                      <div>
                        <p className="text-xs font-semibold text-emerald-800">Receipt shared to all {paidBatch.pos.length} POs</p>
                        <p className="text-[10px] text-emerald-600">{paidBatch.pos.map(p => p.po_number).join(', ')}</p>
                      </div>
                    </div>
                  ) : (
                    <Button size="sm" className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white h-10"
                      onClick={() => setBatchCurrentPoId(paidBatch.pos[0].po_id)} data-testid="one-receipt-upload-btn">
                      <Upload size={13} className="mr-2" /> Upload Collection Receipt
                    </Button>
                  )}
                  <div className="space-y-1">
                    {paidBatch.pos.map(po => (
                      <div key={po.po_id} className="flex items-center gap-2 text-[10px] text-slate-600 px-1">
                        {po.uploaded ? <CheckCircle2 size={10} className="text-emerald-500" /> : <div className="w-2 h-2 rounded-full bg-slate-300" />}
                        <span className="font-mono">{po.po_number}</span>
                        <span className="text-slate-400">₱{po.amount.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <>
                  <p className="text-xs text-slate-500 mb-1">Upload a receipt for each PO — required for audit trail.</p>
                  {paidBatch.pos.map((po, idx) => (
                    <div key={po.po_id}
                      className={`flex items-center justify-between rounded-xl border px-3 py-2.5 ${po.uploaded ? 'bg-emerald-50 border-emerald-200' : 'bg-white border-slate-200'}`}
                      data-testid={`batch-po-row-${po.po_id}`}>
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${po.uploaded ? 'bg-emerald-100' : 'bg-slate-100'}`}>
                          {po.uploaded ? <CheckCircle2 size={13} className="text-emerald-600" /> : <span className="text-[10px] font-bold text-slate-500">{idx + 1}</span>}
                        </div>
                        <div className="min-w-0">
                          <p className="text-xs font-semibold font-mono text-slate-700 truncate">{po.po_number}</p>
                          <p className="text-[10px] text-slate-400">₱{po.amount.toFixed(2)}</p>
                        </div>
                      </div>
                      {po.uploaded ? (
                        <div className="flex items-center gap-1 text-[10px] text-emerald-600"><CheckCircle2 size={11} /> Uploaded</div>
                      ) : (
                        <Button size="sm" variant="outline"
                          className="h-7 text-[10px] px-2.5 border-[#1A4D2E]/30 text-[#1A4D2E] hover:bg-[#1A4D2E] hover:text-white"
                          onClick={() => setBatchCurrentPoId(po.po_id)} data-testid={`batch-upload-btn-${po.po_id}`}>
                          <Upload size={10} className="mr-1" /> Upload
                        </Button>
                      )}
                    </div>
                  ))}
                </>
              )}
            </div>
            <div className="px-4 pb-4 pt-2 border-t border-slate-100">
              {paidBatch.pos.some(p => !p.uploaded) && (
                <p className="text-[10px] text-amber-600 flex items-center gap-1 mb-2">
                  <AlertTriangle size={10} /> {paidBatch.pos.filter(p => !p.uploaded).length} receipt(s) not yet uploaded
                </p>
              )}
              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="flex-1 text-xs"
                  onClick={() => { if (paidBatch.pos.some(p => !p.uploaded)) toast.info('Remember to upload receipts for audit trail'); setBatchUploadOpen(false); setPaidBatch(null); setOneReceiptMode(false); }}>
                  {paidBatch.pos.every(p => p.uploaded) ? 'Done' : 'Skip Remaining'}
                </Button>
                {paidBatch.pos.every(p => p.uploaded) && (
                  <Button size="sm" className="flex-1 text-xs bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                    onClick={() => { setBatchUploadOpen(false); setPaidBatch(null); setOneReceiptMode(false); }} data-testid="batch-upload-done-btn">
                    <CheckCircle2 size={12} className="mr-1" /> All Done
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* UploadQRDialog — single PO */}
      <UploadQRDialog open={psUploadQROpen}
        onClose={(count) => { setPsUploadQROpen(false); if (count > 0) toast.success(`${count} receipt photo(s) saved!`); }}
        recordType="purchase_order" recordId={psUploadPOId} />

      {/* UploadQRDialog — batch item */}
      <UploadQRDialog
        open={!!batchCurrentPoId}
        onClose={async (count) => {
          if (count > 0 && batchCurrentPoId && paidBatch) {
            if (oneReceiptMode) {
              const otherIds = paidBatch.pos.filter(p => p.po_id !== batchCurrentPoId).map(p => p.po_id);
              try {
                if (otherIds.length > 0) await api.post('/uploads/share-receipt', { source_record_id: batchCurrentPoId, target_record_ids: otherIds, record_type: 'purchase_order' });
                setPaidBatch(prev => prev ? { ...prev, pos: prev.pos.map(p => ({ ...p, uploaded: true })) } : prev);
                toast.success(`Receipt shared to all ${paidBatch.pos.length} POs`);
              } catch {
                toast.error('Uploaded but could not share to other POs — please upload individually');
                setPaidBatch(prev => prev ? { ...prev, pos: prev.pos.map(p => p.po_id === batchCurrentPoId ? { ...p, uploaded: true } : p) } : prev);
              }
            } else {
              const poNum = paidBatch.pos.find(p => p.po_id === batchCurrentPoId)?.po_number || 'PO';
              setPaidBatch(prev => prev ? { ...prev, pos: prev.pos.map(p => p.po_id === batchCurrentPoId ? { ...p, uploaded: true } : p) } : prev);
              toast.success(`Receipt saved for ${poNum}`);
            }
          }
          setBatchCurrentPoId(null);
        }}
        recordType="purchase_order" recordId={batchCurrentPoId} />

      <ReviewDetailDialog open={invoiceModalOpen} onOpenChange={setInvoiceModalOpen} poNumber={selectedPoNumber} onUpdated={loadSuppliers} showReviewAction={false} showPayAction={false} />
    </div>
  );
}
