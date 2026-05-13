/**
 * HistoricalSupplierPODialog — Admin-only modal for managing pre-system
 * supplier debt that was NEVER encoded into AgriBooks at cut-over.
 *
 * Phase 3.2. Backend: `/api/historical-supplier-pos`.
 *
 * Two tabs:
 *   • List  — outstanding / partial / paid / voided entries with quick
 *             Pay + Void actions.
 *   • Add   — new entry form (vendor, date pre-cutover, amount, PIN).
 *
 * Auth UX:
 *   • Visible only to admin / owner / super_admin roles (caller is
 *     responsible for the role gate).
 *   • Every mutation (add / pay / void) requires the **admin PIN** or
 *     a **TOTP** code — manager PINs are rejected server-side, surfaced
 *     here as a clear "Managers cannot do this" error.
 *
 * Props:
 *   open               — boolean
 *   onOpenChange(bool)
 *   defaultBranchId    — preselects the branch in the Add form
 *   onChange()         — fires after any successful mutation so the
 *                        parent (AP widget) can refresh
 */
import React, { useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { formatPHP } from '../lib/utils';
import {
  RefreshCw, ShieldAlert, Plus, FileText, Wallet, Ban, CheckCircle2,
} from 'lucide-react';
import { toast } from 'sonner';

const STATUS_STYLE = {
  outstanding: 'bg-amber-100 text-amber-800 border-amber-200',
  partial:     'bg-blue-100 text-blue-800 border-blue-200',
  paid:        'bg-emerald-100 text-emerald-700 border-emerald-200',
  voided:      'bg-slate-200 text-slate-600 border-slate-300',
};

const PAYMENT_METHODS = [
  { v: 'Cash',   fund: 'cashier' },
  { v: 'GCash',  fund: 'digital' },
  { v: 'Bank',   fund: 'bank' },
  { v: 'Maya',   fund: 'digital' },
  { v: 'Other',  fund: 'cashier' },
];

const todayMinusOne = () => {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
};

const errMsg = (e, fallback = 'Request failed.') => {
  const d = e?.response?.data?.detail;
  if (!d) return e?.message || fallback;
  if (typeof d === 'string') return d;
  return d?.message || fallback;
};


export default function HistoricalSupplierPODialog({
  open, onOpenChange, defaultBranchId = '', onChange,
}) {
  const [tab, setTab] = useState('list');
  const [rows, setRows] = useState([]);
  const [outstandingTotal, setOutstandingTotal] = useState(0);
  const [loadingList, setLoadingList] = useState(false);
  const [branches, setBranches] = useState([]);
  const [filterStatus, setFilterStatus] = useState('all');

  // Add-form state
  const [form, setForm] = useState({
    supplier_name: '',
    reference_number: '',
    branch_id: defaultBranchId,
    pre_system_date: todayMinusOne(),
    amount: '',
    description: '',
    pin: '',
  });
  const [submitting, setSubmitting] = useState(false);

  // Pay/Void dialog state
  const [actionRow, setActionRow] = useState(null);
  const [actionMode, setActionMode] = useState(null); // 'pay' | 'void'
  const [actionForm, setActionForm] = useState({});
  const [actionBusy, setActionBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    api.get('/branches').then((res) => setBranches(res.data || [])).catch(() => {});
  }, [open]);

  const loadList = React.useCallback(async () => {
    if (!open) return;
    setLoadingList(true);
    try {
      const params = filterStatus !== 'all' ? { status: filterStatus } : {};
      const res = await api.get('/historical-supplier-pos', { params });
      setRows(res.data?.rows || []);
      setOutstandingTotal(res.data?.outstanding_total || 0);
    } catch (e) {
      toast.error(errMsg(e, 'Could not load entries.'));
    } finally {
      setLoadingList(false);
    }
  }, [open, filterStatus]);

  useEffect(() => { loadList(); }, [loadList]);

  const submitAdd = async () => {
    if (submitting) return;
    if (!form.supplier_name.trim()) return toast.error('Supplier name is required.');
    if (!form.branch_id) return toast.error('Choose a branch.');
    if (!form.pre_system_date) return toast.error('Pre-system date is required.');
    if (!form.amount || parseFloat(form.amount) <= 0) return toast.error('Amount must be > 0.');
    if (!form.pin.trim()) return toast.error('Admin PIN or TOTP is required.');
    setSubmitting(true);
    try {
      await api.post('/historical-supplier-pos', {
        ...form,
        amount: parseFloat(form.amount),
      });
      toast.success('Historical PO recorded.');
      setForm({
        supplier_name: '', reference_number: '', branch_id: form.branch_id,
        pre_system_date: todayMinusOne(), amount: '', description: '', pin: '',
      });
      setTab('list');
      loadList();
      if (onChange) onChange();
    } catch (e) {
      toast.error(errMsg(e, 'Could not save.'));
    } finally {
      setSubmitting(false);
    }
  };

  const openPay = (row) => {
    setActionRow(row);
    setActionMode('pay');
    setActionForm({
      amount: row.balance.toString(),
      payment_method: 'Cash',
      reference: '',
      note: '',
      pin: '',
    });
  };
  const openVoid = (row) => {
    setActionRow(row);
    setActionMode('void');
    setActionForm({ reason: '', pin: '' });
  };
  const closeAction = () => {
    setActionRow(null); setActionMode(null); setActionForm({});
  };

  const submitAction = async () => {
    if (actionBusy || !actionRow) return;
    setActionBusy(true);
    try {
      if (actionMode === 'pay') {
        if (!actionForm.amount || parseFloat(actionForm.amount) <= 0) {
          setActionBusy(false);
          return toast.error('Amount must be > 0.');
        }
        if (!actionForm.pin.trim()) {
          setActionBusy(false);
          return toast.error('Admin PIN or TOTP is required.');
        }
        const fund = (PAYMENT_METHODS.find(p => p.v === actionForm.payment_method) || {}).fund || 'cashier';
        await api.post(`/historical-supplier-pos/${actionRow.id}/pay`, {
          ...actionForm,
          fund_source: fund,
          amount: parseFloat(actionForm.amount),
        });
        toast.success(`Payment of ${formatPHP(parseFloat(actionForm.amount))} recorded.`);
      } else if (actionMode === 'void') {
        if ((actionForm.reason || '').trim().length < 10) {
          setActionBusy(false);
          return toast.error('Reason must be at least 10 characters.');
        }
        if (!actionForm.pin.trim()) {
          setActionBusy(false);
          return toast.error('Admin PIN or TOTP is required.');
        }
        await api.post(`/historical-supplier-pos/${actionRow.id}/void`, actionForm);
        toast.success('Entry voided.');
      }
      closeAction();
      loadList();
      if (onChange) onChange();
    } catch (e) {
      toast.error(errMsg(e, 'Action failed.'));
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className="max-w-4xl max-h-[90vh] overflow-y-auto"
          data-testid="historical-supplier-po-dialog"
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText size={18} className="text-violet-700" />
              Old Supplier POs (Pre-System Carry-Forward)
            </DialogTitle>
            <DialogDescription>
              Record supplier debts that existed BEFORE you started using
              AgriBooks. These show on your AP dashboard so you don&apos;t
              miss payments, but stay <strong>out of the current-period
              expense reports</strong> so your sales-vs-expense view stays
              honest. Admin-only — manager PINs are not accepted.
            </DialogDescription>
          </DialogHeader>

          <Tabs value={tab} onValueChange={setTab} className="mt-2">
            <TabsList>
              <TabsTrigger value="list" data-testid="hspo-tab-list">
                Entries ({rows.length})
              </TabsTrigger>
              <TabsTrigger value="add" data-testid="hspo-tab-add">
                <Plus size={14} className="mr-1" /> Add Entry
              </TabsTrigger>
            </TabsList>

            {/* ── LIST TAB ─────────────────────────────────────────── */}
            <TabsContent value="list" className="mt-3 space-y-3">
              <div className="flex items-center justify-between text-sm">
                <div>
                  <span className="text-slate-500">Outstanding total: </span>
                  <span className="font-mono font-semibold text-amber-700"
                    data-testid="hspo-outstanding-total">
                    {formatPHP(outstandingTotal)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-slate-500">Filter:</Label>
                  <select
                    className="border border-slate-300 rounded px-2 py-1 text-xs"
                    value={filterStatus}
                    onChange={(e) => setFilterStatus(e.target.value)}
                    data-testid="hspo-filter-status">
                    <option value="all">All</option>
                    <option value="outstanding">Outstanding</option>
                    <option value="partial">Partial</option>
                    <option value="paid">Paid</option>
                    <option value="voided">Voided</option>
                  </select>
                  <Button size="sm" variant="outline" onClick={loadList}
                    data-testid="hspo-refresh">
                    <RefreshCw size={12} />
                  </Button>
                </div>
              </div>

              {loadingList ? (
                <div className="py-10 flex justify-center"><RefreshCw size={18} className="animate-spin text-slate-400" /></div>
              ) : rows.length === 0 ? (
                <div className="py-10 text-center text-sm text-slate-500"
                  data-testid="hspo-empty">
                  No historical PO entries yet. Click <strong>Add Entry</strong> to record one.
                </div>
              ) : (
                <div className="border border-slate-200 rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                      <tr>
                        <th className="text-left px-3 py-2">Supplier</th>
                        <th className="text-left px-3 py-2">Ref</th>
                        <th className="text-left px-3 py-2">Date</th>
                        <th className="text-right px-3 py-2">Amount</th>
                        <th className="text-right px-3 py-2">Balance</th>
                        <th className="text-center px-3 py-2">Status</th>
                        <th className="text-right px-3 py-2">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.id} className="border-t border-slate-100"
                          data-testid={`hspo-row-${r.id}`}>
                          <td className="px-3 py-2 font-medium text-slate-800">
                            {r.supplier_name}
                            <div className="text-[10px] text-slate-400">{r.branch_name}</div>
                          </td>
                          <td className="px-3 py-2 font-mono text-slate-600">{r.reference_number || '—'}</td>
                          <td className="px-3 py-2 text-slate-600">{r.pre_system_date}</td>
                          <td className="px-3 py-2 text-right font-mono text-slate-700">{formatPHP(r.amount)}</td>
                          <td className="px-3 py-2 text-right font-mono font-semibold text-slate-900">{formatPHP(r.balance)}</td>
                          <td className="px-3 py-2 text-center">
                            <Badge className={`text-[10px] border ${STATUS_STYLE[r.status] || 'bg-slate-100 text-slate-600'}`}>
                              {r.status}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-right">
                            <div className="flex justify-end gap-1">
                              {(r.status === 'outstanding' || r.status === 'partial') && (
                                <Button size="sm" variant="outline"
                                  onClick={() => openPay(r)}
                                  className="h-7 px-2 text-[10px]"
                                  data-testid={`hspo-pay-${r.id}`}>
                                  <Wallet size={10} className="mr-1" /> Pay
                                </Button>
                              )}
                              {r.status !== 'paid' && r.status !== 'voided' && (
                                <Button size="sm" variant="outline"
                                  onClick={() => openVoid(r)}
                                  className="h-7 px-2 text-[10px] text-rose-700 border-rose-200 hover:bg-rose-50"
                                  data-testid={`hspo-void-${r.id}`}>
                                  <Ban size={10} className="mr-1" /> Void
                                </Button>
                              )}
                              {r.status === 'paid' && (
                                <span className="text-[10px] text-emerald-700 flex items-center gap-1">
                                  <CheckCircle2 size={10} /> Settled
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabsContent>

            {/* ── ADD TAB ──────────────────────────────────────────── */}
            <TabsContent value="add" className="mt-3 space-y-3">
              <div className="bg-violet-50 border border-violet-200 rounded-md p-3 text-xs text-violet-800 flex gap-2"
                data-testid="hspo-add-banner">
                <ShieldAlert size={14} className="mt-0.5 flex-shrink-0" />
                <div>
                  <strong>Pre-cutover only.</strong> Use this ONLY for debts
                  from before your AgriBooks start date. Today&apos;s purchases
                  belong on a regular Purchase Order. Each save requires the
                  admin PIN or TOTP — manager PINs are rejected.
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">Supplier Name</Label>
                  <Input
                    value={form.supplier_name}
                    onChange={(e) => setForm({ ...form, supplier_name: e.target.value })}
                    placeholder="Acme Trading"
                    data-testid="hspo-add-supplier"
                  />
                </div>
                <div>
                  <Label className="text-xs">Reference / Invoice #</Label>
                  <Input
                    value={form.reference_number}
                    onChange={(e) => setForm({ ...form, reference_number: e.target.value })}
                    placeholder="(optional) supplier's PO/invoice no."
                    data-testid="hspo-add-ref"
                  />
                </div>
                <div>
                  <Label className="text-xs">Branch</Label>
                  <select
                    className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
                    value={form.branch_id}
                    onChange={(e) => setForm({ ...form, branch_id: e.target.value })}
                    data-testid="hspo-add-branch">
                    <option value="">Select branch…</option>
                    {branches.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label className="text-xs">Pre-system Date</Label>
                  <Input
                    type="date"
                    value={form.pre_system_date}
                    onChange={(e) => setForm({ ...form, pre_system_date: e.target.value })}
                    max={todayMinusOne()}
                    data-testid="hspo-add-date"
                  />
                </div>
                <div>
                  <Label className="text-xs">Amount (₱)</Label>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={form.amount}
                    onChange={(e) => setForm({ ...form, amount: e.target.value })}
                    placeholder="10000.00"
                    className="font-mono text-right"
                    data-testid="hspo-add-amount"
                  />
                </div>
                <div className="col-span-1">
                  <Label className="text-xs">Admin PIN or TOTP</Label>
                  <Input
                    type="password"
                    autoComplete="off"
                    value={form.pin}
                    onChange={(e) => setForm({ ...form, pin: e.target.value })}
                    placeholder="Manager PINs are rejected"
                    className="font-mono text-center tracking-widest"
                    data-testid="hspo-add-pin"
                  />
                </div>
              </div>

              <div>
                <Label className="text-xs">Description / Notes</Label>
                <Input
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="(optional) what was bought, terms, attached proof…"
                  data-testid="hspo-add-description"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => onOpenChange(false)}
                  data-testid="hspo-add-cancel">Cancel</Button>
                <Button
                  onClick={submitAdd}
                  disabled={submitting}
                  className="bg-violet-700 hover:bg-violet-800 text-white"
                  data-testid="hspo-add-submit">
                  {submitting
                    ? <><RefreshCw size={12} className="animate-spin mr-2" /> Saving…</>
                    : 'Save Historical PO'}
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>

      {/* ── Pay / Void mini-dialog ───────────────────────────────────── */}
      <Dialog open={!!actionMode} onOpenChange={(v) => { if (!v) closeAction(); }}>
        <DialogContent className="max-w-md" data-testid={`hspo-action-dialog-${actionMode || ''}`}>
          <DialogHeader>
            <DialogTitle>
              {actionMode === 'pay' ? 'Pay Historical PO' : 'Void Historical PO'}
            </DialogTitle>
            <DialogDescription>
              {actionRow && (
                <span>
                  {actionRow.supplier_name} — Balance{' '}
                  <strong>{formatPHP(actionRow.balance)}</strong>
                </span>
              )}
            </DialogDescription>
          </DialogHeader>

          {actionMode === 'pay' && (
            <div className="space-y-3">
              <div>
                <Label className="text-xs">Payment Amount (₱)</Label>
                <Input type="number" min={0} step="0.01"
                  value={actionForm.amount || ''}
                  onChange={(e) => setActionForm({ ...actionForm, amount: e.target.value })}
                  className="font-mono text-right"
                  data-testid="hspo-pay-amount" />
              </div>
              <div>
                <Label className="text-xs">Payment Method</Label>
                <select
                  className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
                  value={actionForm.payment_method || 'Cash'}
                  onChange={(e) => setActionForm({ ...actionForm, payment_method: e.target.value })}
                  data-testid="hspo-pay-method">
                  {PAYMENT_METHODS.map(p => <option key={p.v} value={p.v}>{p.v}</option>)}
                </select>
              </div>
              <div>
                <Label className="text-xs">Reference (optional)</Label>
                <Input value={actionForm.reference || ''}
                  onChange={(e) => setActionForm({ ...actionForm, reference: e.target.value })}
                  placeholder="Receipt / OR / bank ref"
                  data-testid="hspo-pay-ref" />
              </div>
              <div>
                <Label className="text-xs">Admin PIN or TOTP</Label>
                <Input type="password" autoComplete="off"
                  value={actionForm.pin || ''}
                  onChange={(e) => setActionForm({ ...actionForm, pin: e.target.value })}
                  className="font-mono text-center tracking-widest"
                  data-testid="hspo-pay-pin" />
              </div>
            </div>
          )}

          {actionMode === 'void' && (
            <div className="space-y-3">
              <div>
                <Label className="text-xs">Reason (min 10 chars)</Label>
                <Input value={actionForm.reason || ''}
                  onChange={(e) => setActionForm({ ...actionForm, reason: e.target.value })}
                  placeholder="Data-entry error / duplicate / paid before encoding"
                  data-testid="hspo-void-reason" />
              </div>
              <div>
                <Label className="text-xs">Admin PIN or TOTP</Label>
                <Input type="password" autoComplete="off"
                  value={actionForm.pin || ''}
                  onChange={(e) => setActionForm({ ...actionForm, pin: e.target.value })}
                  className="font-mono text-center tracking-widest"
                  data-testid="hspo-void-pin" />
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={closeAction}
              data-testid="hspo-action-cancel">Cancel</Button>
            <Button
              onClick={submitAction}
              disabled={actionBusy}
              className={`${actionMode === 'void' ? 'bg-rose-600 hover:bg-rose-700' : 'bg-violet-700 hover:bg-violet-800'} text-white`}
              data-testid="hspo-action-submit">
              {actionBusy
                ? <><RefreshCw size={12} className="animate-spin mr-2" /> Working…</>
                : actionMode === 'pay' ? 'Record Payment' : 'Void Entry'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
