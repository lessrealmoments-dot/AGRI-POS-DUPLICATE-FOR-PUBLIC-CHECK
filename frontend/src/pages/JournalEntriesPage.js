import { useState, useEffect, useCallback } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Textarea } from '../components/ui/textarea';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { ScrollArea } from '../components/ui/scroll-area';
import {
  BookOpen, Plus, Search, AlertTriangle, Check, RefreshCw, Ban,
  FileText, Calendar, Shield, HelpCircle, ArrowRight
} from 'lucide-react';
import { toast } from 'sonner';
import { useUnsavedChangesGuard } from '../lib/useUnsavedChangesGuard';

const ENTRY_TYPES = [
  { value: 'sale_adjustment', label: 'Sale Adjustment', desc: 'A sale happened but wasn\'t recorded in the system', color: 'bg-emerald-100 text-emerald-700' },
  { value: 'expense_adjustment', label: 'Expense Adjustment', desc: 'An expense was missed or recorded with the wrong amount', color: 'bg-red-100 text-red-700' },
  { value: 'inventory_adjustment', label: 'Inventory Adjustment', desc: 'Stock level needs correction (damage, loss, or count error)', color: 'bg-amber-100 text-amber-700' },
  { value: 'price_correction', label: 'Price Correction', desc: 'A product was sold at the wrong price', color: 'bg-blue-100 text-blue-700' },
  { value: 'fund_correction', label: 'Fund Correction', desc: 'Cash drawer or safe balance needs adjustment', color: 'bg-purple-100 text-purple-700' },
  { value: 'general', label: 'General Entry', desc: 'Any other adjustment not covered above', color: 'bg-slate-100 text-slate-700' },
];

const STATUS_COLORS = {
  posted: 'bg-emerald-100 text-emerald-700',
  voided: 'bg-red-100 text-red-700',
  draft: 'bg-slate-100 text-slate-600',
};

// Guided templates — pre-fills lines for beginners
const TEMPLATES = {
  sale_adjustment: {
    hint: 'A cash sale was made but not entered in the POS. This records the revenue and the cash.',
    lines: [
      { account_code: '1000', account_name: 'Cash - Cashier Drawer', debit: '', credit: '', memo: 'Cash received from unrecorded sale' },
      { account_code: '4000', account_name: 'Sales Revenue', debit: '', credit: '', memo: 'Revenue from unrecorded sale' },
    ]
  },
  expense_adjustment: {
    hint: 'An expense happened (cash was spent) but wasn\'t recorded. This records the expense and reduces cash.',
    lines: [
      { account_code: '5100', account_name: 'Operating Expenses', debit: '', credit: '', memo: 'Missed expense' },
      { account_code: '1000', account_name: 'Cash - Cashier Drawer', debit: '', credit: '', memo: 'Cash paid for expense' },
    ]
  },
  inventory_adjustment: {
    hint: 'Physical count doesn\'t match system. This writes off the missing inventory value.',
    lines: [
      { account_code: '5500', account_name: 'Inventory Loss / Write-off', debit: '', credit: '', memo: 'Loss from inventory shortage' },
      { account_code: '1200', account_name: 'Inventory', debit: '', credit: '', memo: 'Reduce inventory to match physical count' },
    ]
  },
  price_correction: {
    hint: 'Product was sold at the wrong price. This adjusts the revenue to the correct amount.',
    lines: [
      { account_code: '4000', account_name: 'Sales Revenue', debit: '', credit: '', memo: 'Reverse incorrect amount' },
      { account_code: '1000', account_name: 'Cash - Cashier Drawer', debit: '', credit: '', memo: 'Adjust cash for price difference' },
    ]
  },
  fund_correction: {
    hint: 'Cash in drawer doesn\'t match what the system says. This corrects the balance.',
    lines: [
      { account_code: '1000', account_name: 'Cash - Cashier Drawer', debit: '', credit: '', memo: 'Adjust cashier balance' },
      { account_code: '5900', account_name: 'Miscellaneous Expense', debit: '', credit: '', memo: 'Unaccounted difference' },
    ]
  },
  general: {
    hint: 'Custom entry — you choose the accounts and amounts. Make sure debits = credits.',
    lines: [
      { account_code: '', account_name: '', debit: '', credit: '', memo: '' },
      { account_code: '', account_name: '', debit: '', credit: '', memo: '' },
    ]
  },
};

export default function JournalEntriesPage() {
  const { currentBranch, user } = useAuth();
  const today = new Date().toISOString().slice(0, 10);

  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');

  // Create dialog
  const [createOpen, setCreateOpen] = useState(false);
  const [step, setStep] = useState(1); // 1=type, 2=details, 3=review
  const [entryType, setEntryType] = useState('');
  const [effectiveDate, setEffectiveDate] = useState(today);
  const [memo, setMemo] = useState('');
  const [refNumber, setRefNumber] = useState('');
  const [lines, setLines] = useState([]);
  const [pin, setPin] = useState('');
  const [saving, setSaving] = useState(false);
  const [accounts, setAccounts] = useState([]);

  // Unsaved-changes guard — fires when the user has typed any content
  // into the create-entry form. We deliberately DON'T gate on
  // `createOpen`: a radix Dialog's overlay eats the first sidebar click
  // and closes itself, so by the time our document-level click capture
  // runs, `createOpen` would already be false. Keying isDirty purely on
  // form content lets the second sidebar click still fire the leave
  // guard. Form state is reset only after a successful submit.
  useUnsavedChangesGuard({
    isDirty: !saving && (
      !!entryType || !!memo || !!refNumber || (lines && lines.length > 0)
    ),
    label: 'Journal Entry',
  });

  // Void dialog
  const [voidDialog, setVoidDialog] = useState(null);
  const [voidReason, setVoidReason] = useState('');
  const [voidPin, setVoidPin] = useState('');
  const [voiding, setVoiding] = useState(false);

  // Detail view
  const [detailEntry, setDetailEntry] = useState(null);

  const loadEntries = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '100' });
      if (filterType !== 'all') params.set('entry_type', filterType);
      const res = await api.get(`/journal-entries?${params}`);
      setEntries(res.data.entries || []);
    } catch { toast.error('Failed to load journal entries'); }
    setLoading(false);
  }, [filterType]);

  useEffect(() => { loadEntries(); }, [loadEntries]);

  useEffect(() => {
    api.get('/journal-entries/accounts').then(res => setAccounts(res.data.accounts || [])).catch(() => {});
  }, []);

  const filtered = entries.filter(e => {
    if (!search) return true;
    const s = search.toLowerCase();
    return e.je_number?.toLowerCase().includes(s) ||
      e.memo?.toLowerCase().includes(s) ||
      e.entry_type_label?.toLowerCase().includes(s) ||
      e.reference_number?.toLowerCase().includes(s);
  });

  const openCreate = () => {
    setCreateOpen(true);
    setStep(1);
    setEntryType('');
    setEffectiveDate(today);
    setMemo('');
    setRefNumber('');
    setLines([]);
    setPin('');
  };

  const selectType = (type) => {
    setEntryType(type);
    const template = TEMPLATES[type];
    setLines(template.lines.map(l => ({ ...l })));
    setStep(2);
  };

  const updateLine = (idx, field, value) => {
    setLines(prev => prev.map((l, i) => i === idx ? { ...l, [field]: value } : l));
  };

  const addLine = () => {
    setLines(prev => [...prev, { account_code: '', account_name: '', debit: '', credit: '', memo: '' }]);
  };

  const removeLine = (idx) => {
    if (lines.length <= 2) return;
    setLines(prev => prev.filter((_, i) => i !== idx));
  };

  const totalDebit = lines.reduce((s, l) => s + (parseFloat(l.debit) || 0), 0);
  const totalCredit = lines.reduce((s, l) => s + (parseFloat(l.credit) || 0), 0);
  const isBalanced = Math.abs(totalDebit - totalCredit) < 0.01 && totalDebit > 0;

  const handleCreate = async () => {
    if (!isBalanced) { toast.error('Entry must be balanced (debits = credits)'); return; }
    if (!memo.trim()) { toast.error('Please explain why this entry is needed'); return; }
    if (!pin) { toast.error('Manager PIN required'); return; }
    setSaving(true);
    try {
      const res = await api.post('/journal-entries', {
        entry_type: entryType,
        effective_date: effectiveDate,
        memo,
        reference_number: refNumber,
        lines: lines.map(l => ({
          account_code: l.account_code,
          account_name: l.account_name,
          debit: parseFloat(l.debit) || 0,
          credit: parseFloat(l.credit) || 0,
          memo: l.memo,
        })),
        pin,
      });
      toast.success(`Journal Entry ${res.data.je_number} created`);
      setCreateOpen(false);
      loadEntries();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to create'); }
    setSaving(false);
  };

  const handleVoid = async () => {
    if (!voidReason || !voidPin) { toast.error('Reason and PIN required'); return; }
    setVoiding(true);
    try {
      await api.post(`/journal-entries/${voidDialog.id}/void`, { reason: voidReason, pin: voidPin });
      toast.success('Journal entry voided');
      setVoidDialog(null);
      setVoidReason('');
      setVoidPin('');
      loadEntries();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setVoiding(false);
  };

  const typeInfo = ENTRY_TYPES.find(t => t.value === entryType);
  const template = TEMPLATES[entryType];

  return (
    <div className="p-6 space-y-5 max-w-6xl mx-auto" data-testid="journal-entries-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-[#1A4D2E] flex items-center justify-center">
            <BookOpen size={20} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Journal Entries</h1>
            <p className="text-xs text-slate-500">Post-close adjustments — correct records without altering closed transactions</p>
          </div>
        </div>
        <Button onClick={openCreate} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="new-je-btn">
          <Plus size={14} className="mr-1.5" /> New Entry
        </Button>
      </div>

      {/* Help banner */}
      <Card className="border-blue-200 bg-blue-50/50">
        <CardContent className="p-4 flex gap-3 items-start">
          <HelpCircle size={18} className="text-blue-500 shrink-0 mt-0.5" />
          <div className="text-xs text-blue-800 space-y-1">
            <p className="font-semibold">When do I need a Journal Entry?</p>
            <p>Use journal entries when something happened AFTER the day was closed — a sale wasn't recorded, an expense was missed, or inventory needs correction. Journal entries adjust the books without changing the original closed records, keeping your audit trail clean.</p>
          </div>
        </CardContent>
      </Card>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input placeholder="Search entries..." value={search} onChange={e => setSearch(e.target.value)}
            className="pl-9 h-9" data-testid="je-search" />
        </div>
        <Select value={filterType} onValueChange={setFilterType}>
          <SelectTrigger className="w-48 h-9"><SelectValue placeholder="All types" /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Types</SelectItem>
            {ENTRY_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={loadEntries} disabled={loading}>
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </Button>
      </div>

      {/* Entries table */}
      <Card className="border-slate-200">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50">
                <TableHead className="text-xs">JE #</TableHead>
                <TableHead className="text-xs">Date</TableHead>
                <TableHead className="text-xs">Type</TableHead>
                <TableHead className="text-xs">Memo</TableHead>
                <TableHead className="text-xs text-right">Amount</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs">By</TableHead>
                <TableHead className="text-xs w-20"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Loading...</TableCell></TableRow>}
              {!loading && filtered.length === 0 && (
                <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">No journal entries yet. Click "New Entry" to create one.</TableCell></TableRow>
              )}
              {filtered.map(e => (
                <TableRow key={e.id} className="hover:bg-slate-50 cursor-pointer" onClick={() => setDetailEntry(e)} data-testid={`je-row-${e.id}`}>
                  <TableCell className="font-mono text-xs text-blue-600 font-semibold">{e.je_number}</TableCell>
                  <TableCell className="text-xs text-slate-500">{e.effective_date}</TableCell>
                  <TableCell><Badge className={`text-[10px] ${ENTRY_TYPES.find(t => t.value === e.entry_type)?.color || 'bg-slate-100'}`}>{e.entry_type_label}</Badge></TableCell>
                  <TableCell className="text-sm max-w-[250px] truncate">{e.memo}</TableCell>
                  <TableCell className="text-right font-mono font-semibold">{formatPHP(e.total_amount)}</TableCell>
                  <TableCell><Badge className={`text-[10px] ${STATUS_COLORS[e.status] || ''}`}>{e.status}</Badge></TableCell>
                  <TableCell className="text-xs text-slate-500">{e.created_by_name}</TableCell>
                  <TableCell>
                    {!e.voided && (
                      <Button variant="ghost" size="sm" className="text-red-500 h-7 px-2"
                        onClick={(ev) => { ev.stopPropagation(); setVoidDialog(e); setVoidReason(''); setVoidPin(''); }}>
                        <Ban size={12} />
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* ── CREATE DIALOG ─────────────────────────────────────────────── */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <BookOpen size={18} className="text-[#1A4D2E]" />
              {step === 1 ? 'What needs correcting?' : step === 2 ? 'Entry Details' : 'Review & Submit'}
            </DialogTitle>
          </DialogHeader>

          {/* Step 1: Choose type */}
          {step === 1 && (
            <div className="space-y-2 mt-2">
              <p className="text-sm text-slate-500 mb-3">Select the type of adjustment you need to make:</p>
              {ENTRY_TYPES.map(t => (
                <button key={t.value} onClick={() => selectType(t.value)}
                  className="w-full text-left p-3 rounded-lg border border-slate-200 hover:border-[#1A4D2E] hover:bg-emerald-50/30 transition-colors"
                  data-testid={`je-type-${t.value}`}>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-semibold text-sm text-slate-800">{t.label}</p>
                      <p className="text-xs text-slate-500 mt-0.5">{t.desc}</p>
                    </div>
                    <ArrowRight size={16} className="text-slate-300" />
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Step 2: Details */}
          {step === 2 && (
            <div className="space-y-4 mt-2">
              {/* Type badge + hint */}
              <div className="flex items-center gap-2">
                <Badge className={typeInfo?.color}>{typeInfo?.label}</Badge>
                <button onClick={() => setStep(1)} className="text-xs text-blue-600 hover:underline">Change type</button>
              </div>
              {template?.hint && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800 flex gap-2">
                  <HelpCircle size={14} className="shrink-0 mt-0.5 text-blue-500" />
                  <span>{template.hint}</span>
                </div>
              )}

              {/* Date + Memo */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label className="text-xs text-slate-500">Effective Date (when did this happen?)</Label>
                  <Input type="date" value={effectiveDate} onChange={e => setEffectiveDate(e.target.value)} className="mt-1 h-9" />
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Reference # (optional — link to invoice/PO)</Label>
                  <Input value={refNumber} onChange={e => setRefNumber(e.target.value)} placeholder="e.g., INV-2026-001" className="mt-1 h-9" />
                </div>
              </div>
              <div>
                <Label className="text-xs text-slate-500">Why is this entry needed? (required)</Label>
                <Textarea value={memo} onChange={e => setMemo(e.target.value)} placeholder="Explain what happened and why this correction is needed..."
                  className="mt-1 min-h-[60px]" data-testid="je-memo" />
              </div>

              {/* Lines */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <Label className="text-xs text-slate-500 font-semibold">Account Lines (Debit = Credit)</Label>
                  <Button variant="outline" size="sm" onClick={addLine} className="h-7 text-xs">
                    <Plus size={12} className="mr-1" /> Add Line
                  </Button>
                </div>
                <div className="space-y-2">
                  {lines.map((l, i) => (
                    <div key={i} className="grid grid-cols-12 gap-2 items-center p-2 bg-slate-50 rounded-lg border border-slate-200">
                      <div className="col-span-4">
                        <Select value={l.account_code} onValueChange={v => {
                          const acc = accounts.find(a => a.code === v);
                          updateLine(i, 'account_code', v);
                          updateLine(i, 'account_name', acc?.name || '');
                        }}>
                          <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Account..." /></SelectTrigger>
                          <SelectContent>
                            {accounts.map(a => <SelectItem key={a.code} value={a.code}>{a.code} - {a.name}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="col-span-2">
                        <Input type="number" min={0} step={0.01} placeholder="Debit" value={l.debit}
                          onChange={e => { updateLine(i, 'debit', e.target.value); if (e.target.value) updateLine(i, 'credit', ''); }}
                          className="h-8 text-xs font-mono" />
                      </div>
                      <div className="col-span-2">
                        <Input type="number" min={0} step={0.01} placeholder="Credit" value={l.credit}
                          onChange={e => { updateLine(i, 'credit', e.target.value); if (e.target.value) updateLine(i, 'debit', ''); }}
                          className="h-8 text-xs font-mono" />
                      </div>
                      <div className="col-span-3">
                        <Input placeholder="Note..." value={l.memo} onChange={e => updateLine(i, 'memo', e.target.value)}
                          className="h-8 text-xs" />
                      </div>
                      <div className="col-span-1 flex justify-center">
                        {lines.length > 2 && (
                          <button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600 text-xs">&times;</button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                {/* Balance indicator */}
                <div className={`mt-2 p-2 rounded-lg text-xs font-semibold flex items-center justify-between ${isBalanced ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
                  <span>Debit: {formatPHP(totalDebit)}</span>
                  <span>{isBalanced ? <Check size={14} /> : <AlertTriangle size={14} />}</span>
                  <span>Credit: {formatPHP(totalCredit)}</span>
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <Button variant="outline" className="flex-1" onClick={() => setStep(1)}>Back</Button>
                <Button className="flex-1 bg-[#1A4D2E] text-white" disabled={!isBalanced || !memo.trim()}
                  onClick={() => setStep(3)} data-testid="je-next-review">
                  Review <ArrowRight size={14} className="ml-1" />
                </Button>
              </div>
            </div>
          )}

          {/* Step 3: Review & PIN */}
          {step === 3 && (
            <div className="space-y-4 mt-2">
              <div className="bg-slate-50 rounded-lg p-4 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-slate-500">Type</span><Badge className={typeInfo?.color}>{typeInfo?.label}</Badge></div>
                <div className="flex justify-between"><span className="text-slate-500">Effective Date</span><span className="font-mono">{effectiveDate}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Amount</span><span className="font-bold font-mono">{formatPHP(totalDebit)}</span></div>
                {refNumber && <div className="flex justify-between"><span className="text-slate-500">Reference</span><span className="font-mono">{refNumber}</span></div>}
                <Separator />
                <p className="text-slate-700">{memo}</p>
                <Separator />
                <p className="text-xs text-slate-500 font-semibold">Lines:</p>
                {lines.map((l, i) => (
                  <div key={i} className="flex justify-between text-xs py-1">
                    <span>{l.account_name || l.account_code}</span>
                    <span className="font-mono">
                      {parseFloat(l.debit) > 0 ? `DR ${formatPHP(l.debit)}` : `CR ${formatPHP(l.credit)}`}
                    </span>
                  </div>
                ))}
              </div>

              <div>
                <Label className="text-xs text-slate-500">Manager PIN (required to authorize)</Label>
                <Input type="password" autoComplete="new-password" value={pin} onChange={e => setPin(e.target.value)}
                  placeholder="Enter manager PIN" className="mt-1" autoFocus
                  onKeyDown={e => e.key === 'Enter' && handleCreate()}
                  data-testid="je-pin" />
              </div>

              <div className="flex gap-2 pt-1">
                <Button variant="outline" className="flex-1" onClick={() => setStep(2)}>Back</Button>
                <Button onClick={handleCreate} disabled={saving || !pin}
                  className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  data-testid="je-submit">
                  {saving ? <RefreshCw size={13} className="animate-spin mr-1" /> : <Shield size={14} className="mr-1" />}
                  Post Journal Entry
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── DETAIL DIALOG ─────────────────────────────────────────────── */}
      {detailEntry && (
        <Dialog open={!!detailEntry} onOpenChange={() => setDetailEntry(null)}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                <FileText size={16} className="text-[#1A4D2E]" />
                {detailEntry.je_number}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div><Label className="text-xs text-slate-500">Type</Label><Badge className={ENTRY_TYPES.find(t => t.value === detailEntry.entry_type)?.color}>{detailEntry.entry_type_label}</Badge></div>
                <div><Label className="text-xs text-slate-500">Status</Label><Badge className={STATUS_COLORS[detailEntry.status]}>{detailEntry.status}</Badge></div>
                <div><Label className="text-xs text-slate-500">Effective Date</Label><p className="font-mono">{detailEntry.effective_date}</p></div>
                <div><Label className="text-xs text-slate-500">Posted Date</Label><p className="font-mono">{detailEntry.posted_date}</p></div>
                <div><Label className="text-xs text-slate-500">Amount</Label><p className="font-bold">{formatPHP(detailEntry.total_amount)}</p></div>
                <div><Label className="text-xs text-slate-500">Authorized By</Label><p>{detailEntry.authorized_by_name} ({detailEntry.authorized_method})</p></div>
              </div>
              {detailEntry.reference_number && (
                <div><Label className="text-xs text-slate-500">Reference #</Label><p className="font-mono">{detailEntry.reference_number}</p></div>
              )}
              <Separator />
              <div><Label className="text-xs text-slate-500">Reason / Memo</Label><p className="text-slate-700 mt-1">{detailEntry.memo}</p></div>
              <Separator />
              <Label className="text-xs text-slate-500">Account Lines</Label>
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs">Account</TableHead>
                    <TableHead className="text-xs text-right">Debit</TableHead>
                    <TableHead className="text-xs text-right">Credit</TableHead>
                    <TableHead className="text-xs">Note</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(detailEntry.lines || []).map((l, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs">{l.account_code} - {l.account_name}</TableCell>
                      <TableCell className="text-right font-mono text-xs">{l.debit > 0 ? formatPHP(l.debit) : ''}</TableCell>
                      <TableCell className="text-right font-mono text-xs">{l.credit > 0 ? formatPHP(l.credit) : ''}</TableCell>
                      <TableCell className="text-xs text-slate-400">{l.memo}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {detailEntry.voided && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
                  <p className="font-semibold">Voided</p>
                  <p>Reason: {detailEntry.void_reason}</p>
                  <p>By: {detailEntry.voided_by} on {fmtDate(detailEntry.voided_at)}</p>
                </div>
              )}
              <p className="text-[10px] text-slate-400">Created by {detailEntry.created_by_name} on {fmtDate(detailEntry.created_at)}</p>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* ── VOID DIALOG ───────────────────────────────────────────────── */}
      {voidDialog && (
        <Dialog open={!!voidDialog} onOpenChange={() => setVoidDialog(null)}>
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle className="text-red-700">Void {voidDialog.je_number}</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <p className="text-sm text-slate-600">This will mark the journal entry as voided. The original record is kept for audit trail.</p>
              <div>
                <Label className="text-xs">Reason for voiding</Label>
                <Textarea value={voidReason} onChange={e => setVoidReason(e.target.value)} className="mt-1" placeholder="Why void this entry?" />
              </div>
              <div>
                <Label className="text-xs">Manager PIN</Label>
                <Input type="password" autoComplete="new-password" value={voidPin} onChange={e => setVoidPin(e.target.value)} className="mt-1" placeholder="PIN"
                  onKeyDown={e => e.key === 'Enter' && handleVoid()} />
              </div>
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => setVoidDialog(null)}>Cancel</Button>
                <Button onClick={handleVoid} disabled={voiding || !voidReason || !voidPin}
                  className="flex-1 bg-red-600 text-white hover:bg-red-700">
                  {voiding ? <RefreshCw size={13} className="animate-spin mr-1" /> : <Ban size={14} className="mr-1" />}
                  Void Entry
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
