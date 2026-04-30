/**
 * ReserveTab — Audit Center → Reserve.
 * Shows the Cash Overage Reserve pool + Shortage Deficit counter per branch,
 * with the full ledger and admin actions:
 *   - Apply reserve to offset audit findings (inventory / cash / other)
 *   - Net reserve against deficit (manual approval)
 *   - Claw-back a mistaken auto-credit
 *   - Backfill historical daily closings (one-tap setup)
 *
 * All write actions require a manager/admin PIN.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { ShieldCheck, AlertTriangle, RefreshCw, RotateCcw, Wallet, History, ArrowRight } from 'lucide-react';
import { toast } from 'sonner';

const fmt = (n) => '₱' + (parseFloat(n) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 });

// ── Apply / Net / Claw-back modal ──────────────────────────────────────────
function PinActionModal({ open, onClose, onSubmit, title, description, defaultAmount, maxAmount,
                         showAppliedTo = false, showAmount = true, busy }) {
  const [amount, setAmount] = useState(defaultAmount || '');
  const [appliedTo, setAppliedTo] = useState('inventory_variance');
  const [reason, setReason] = useState('');
  const [pin, setPin] = useState('');

  useEffect(() => {
    if (open) {
      setAmount(defaultAmount || '');
      setAppliedTo('inventory_variance');
      setReason('');
      setPin('');
    }
  }, [open, defaultAmount]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 9999 }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full p-5" style={{ maxWidth: '440px' }}>
        <p className="font-bold text-slate-800">{title}</p>
        {description && <p className="text-xs text-slate-500 mt-1 mb-4">{description}</p>}

        {showAmount && (
          <div className="mb-3">
            <Label className="text-xs">Amount {maxAmount != null && <span className="text-slate-400">(max {fmt(maxAmount)})</span>}</Label>
            <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
              className="mt-1" data-testid="reserve-action-amount" />
          </div>
        )}

        {showAppliedTo && (
          <div className="mb-3">
            <Label className="text-xs">Apply To</Label>
            <Select value={appliedTo} onValueChange={setAppliedTo}>
              <SelectTrigger className="mt-1" data-testid="reserve-action-applied-to"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="inventory_variance">Inventory variance / shrinkage</SelectItem>
                <SelectItem value="cash_discrepancy">Cash discrepancy</SelectItem>
                <SelectItem value="other">Other (explain in reason)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="mb-3">
          <Label className="text-xs">Reason / Note</Label>
          <Input value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Audit Feb 2026 — applied to cover ₱1,250 inventory shortage"
            className="mt-1" data-testid="reserve-action-reason" />
        </div>

        <div className="mb-4">
          <Label className="text-xs">Manager / Admin PIN</Label>
          <Input type="password" inputMode="numeric" maxLength={6} value={pin}
            onChange={(e) => setPin(e.target.value)} className="mt-1" data-testid="reserve-action-pin" />
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-600 hover:bg-slate-50">Cancel</button>
          <button
            onClick={() => onSubmit({ amount: parseFloat(amount), applied_to: appliedTo, reason, pin })}
            disabled={busy || (!showAmount ? false : !(parseFloat(amount) > 0)) || !reason || !pin}
            className="flex-1 py-2.5 rounded-xl text-sm font-semibold text-white bg-[#1A4D2E] hover:bg-[#14532d] disabled:opacity-50"
            data-testid="reserve-action-submit">
            {busy ? 'Saving…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Type → label / colour helpers ──────────────────────────────────────────
const TYPE_META = {
  auto_credit:    { label: 'Auto credit',     tone: 'emerald' },
  apply_audit:    { label: 'Applied (audit)', tone: 'blue' },
  net_shortage:   { label: 'Netted',          tone: 'amber' },
  claw_back:      { label: 'Claw-back',       tone: 'red' },
  manual_adjust:  { label: 'Manual',          tone: 'slate' },
};

export default function ReserveTab({ branchId, onClose }) {
  const { branches, user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [summary, setSummary] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(true);
  const [poolFilter, setPoolFilter] = useState('all');     // all | reserve | deficit
  const [scopeBranch, setScopeBranch] = useState(branchId || 'all');
  const [actionDialog, setActionDialog] = useState(null);  // 'apply' | 'net' | 'claw' | null
  const [clawTarget, setClawTarget] = useState(null);
  const [busy, setBusy] = useState(false);

  const effectiveBranch = scopeBranch && scopeBranch !== 'all' ? scopeBranch : null;
  const branchSummary = useMemo(() => {
    if (!summary) return null;
    if (effectiveBranch) {
      return summary.branches.find((b) => b.branch_id === effectiveBranch) || null;
    }
    return null;
  }, [summary, effectiveBranch]);

  const totals = summary?.totals || {};

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sumRes, ledRes] = await Promise.all([
        api.get('/reserve/summary', { params: effectiveBranch ? { branch_id: effectiveBranch } : {} }),
        api.get('/reserve/ledger', {
          params: {
            ...(effectiveBranch ? { branch_id: effectiveBranch } : {}),
            ...(poolFilter !== 'all' ? { pool: poolFilter } : {}),
            limit: 80,
          },
        }),
      ]);
      setSummary(sumRes.data);
      setLedger(ledRes.data.entries || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load reserve data');
    }
    setLoading(false);
  }, [effectiveBranch, poolFilter]);

  useEffect(() => { load(); }, [load]);

  const submitApply = async ({ amount, applied_to, reason, pin }) => {
    setBusy(true);
    try {
      await api.post('/reserve/apply', {
        branch_id: effectiveBranch, amount, applied_to, reason, pin,
      });
      toast.success(`Reserve debited ${fmt(amount)} successfully`);
      setActionDialog(null);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Apply failed');
    }
    setBusy(false);
  };

  const submitNet = async ({ amount, reason, pin }) => {
    setBusy(true);
    try {
      await api.post('/reserve/net-shortage', {
        branch_id: effectiveBranch, amount, reason, pin,
      });
      toast.success(`Netted ${fmt(amount)} from reserve against deficit`);
      setActionDialog(null);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Net-shortage failed');
    }
    setBusy(false);
  };

  const submitClaw = async ({ reason, pin }) => {
    if (!clawTarget) return;
    setBusy(true);
    try {
      await api.post('/reserve/claw-back', { entry_id: clawTarget.id, reason, pin });
      toast.success('Entry reversed');
      setActionDialog(null);
      setClawTarget(null);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Claw-back failed');
    }
    setBusy(false);
  };

  const runBackfill = async () => {
    if (!isAdmin) return;
    if (!window.confirm('Backfill the reserve ledger from every historical daily close? This is idempotent — already-recorded entries will be skipped.')) return;
    setBusy(true);
    try {
      const res = await api.post('/reserve/backfill', effectiveBranch ? { branch_id: effectiveBranch } : {});
      toast.success(`Backfill complete — ${res.data.created} new entries from ${res.data.scanned} closings`);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Backfill failed');
    }
    setBusy(false);
  };

  // Per-branch UI uses branchSummary; org-wide uses totals
  const reserveBalance = branchSummary?.reserve_balance ?? totals.reserve_total ?? 0;
  const deficitBalance = branchSummary?.deficit_balance ?? totals.deficit_total ?? 0;
  const netPool = branchSummary?.net_pool ?? totals.net_pool ?? 0;

  return (
    <div className="space-y-4" data-testid="audit-reserve-tab">
      {/* Header — branch picker + scope summary */}
      <Card className="border-slate-200">
        <CardContent className="p-5">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-emerald-100 flex items-center justify-center">
                <ShieldCheck size={18} className="text-emerald-700" />
              </div>
              <div>
                <p className="font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Cash Overage Reserve</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Auto-pooled from each daily close. Apply to offset audit findings.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {isAdmin && (
                <Select value={scopeBranch} onValueChange={setScopeBranch}>
                  <SelectTrigger className="h-9 w-44 text-xs" data-testid="reserve-branch-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Org-wide (all branches)</SelectItem>
                    {(branches || []).map((b) => (
                      <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <Button variant="outline" size="sm" onClick={load} disabled={loading} className="h-9">
                <RefreshCw size={13} className={loading ? 'animate-spin mr-1.5' : 'mr-1.5'} /> Refresh
              </Button>
            </div>
          </div>

          {/* Balance triplet */}
          <div className="grid grid-cols-3 gap-3 mt-5">
            <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200">
              <p className="text-[10px] text-emerald-600 uppercase font-semibold tracking-wider">Reserve Pool</p>
              <p className="text-2xl font-bold text-emerald-700 font-mono" data-testid="reserve-balance">{fmt(reserveBalance)}</p>
              <p className="text-[10px] text-emerald-600 mt-0.5">Available to apply</p>
            </div>
            <div className="p-3 rounded-xl bg-red-50 border border-red-200">
              <p className="text-[10px] text-red-600 uppercase font-semibold tracking-wider">Shortage Deficit</p>
              <p className="text-2xl font-bold text-red-700 font-mono" data-testid="reserve-deficit">{fmt(deficitBalance)}</p>
              <p className="text-[10px] text-red-600 mt-0.5">Negative variance accumulated</p>
            </div>
            <div className={`p-3 rounded-xl border ${netPool >= 0 ? 'bg-blue-50 border-blue-200' : 'bg-amber-50 border-amber-200'}`}>
              <p className={`text-[10px] uppercase font-semibold tracking-wider ${netPool >= 0 ? 'text-blue-600' : 'text-amber-600'}`}>Net Pool</p>
              <p className={`text-2xl font-bold font-mono ${netPool >= 0 ? 'text-blue-700' : 'text-amber-700'}`}>
                {netPool >= 0 ? '+' : ''}{fmt(netPool)}
              </p>
              <p className={`text-[10px] mt-0.5 ${netPool >= 0 ? 'text-blue-600' : 'text-amber-600'}`}>Reserve − Deficit</p>
            </div>
          </div>

          {/* Action buttons (per-branch only) */}
          {effectiveBranch ? (
            <div className="flex gap-2 mt-4 flex-wrap">
              <Button onClick={() => setActionDialog('apply')} disabled={reserveBalance <= 0}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="reserve-apply-btn">
                <ArrowRight size={13} className="mr-1.5" /> Apply Reserve to Audit
              </Button>
              <Button variant="outline" onClick={() => setActionDialog('net')}
                disabled={reserveBalance <= 0 || deficitBalance <= 0} data-testid="reserve-net-btn">
                <Wallet size={13} className="mr-1.5" /> Net vs Deficit
              </Button>
              {isAdmin && (
                <Button variant="ghost" onClick={runBackfill} disabled={busy}
                  className="ml-auto text-slate-500 hover:text-slate-700" data-testid="reserve-backfill-btn">
                  <History size={13} className="mr-1.5" /> Backfill from Closings
                </Button>
              )}
            </div>
          ) : (
            <p className="text-xs text-slate-400 mt-4 text-center">
              Pick a branch above to apply reserve, net deficit, or backfill.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Ledger */}
      <Card className="border-slate-200">
        <CardContent className="p-4">
          <Tabs value={poolFilter} onValueChange={setPoolFilter}>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Ledger</p>
              <TabsList>
                <TabsTrigger value="all" data-testid="reserve-ledger-all">All</TabsTrigger>
                <TabsTrigger value="reserve" data-testid="reserve-ledger-reserve">Reserve</TabsTrigger>
                <TabsTrigger value="deficit" data-testid="reserve-ledger-deficit">Deficit</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value={poolFilter} className="mt-2">
              {loading ? (
                <div className="text-center py-8 text-slate-400 text-xs">
                  <RefreshCw size={16} className="animate-spin mx-auto mb-1" /> Loading…
                </div>
              ) : ledger.length === 0 ? (
                <div className="text-center py-8 text-slate-400 text-xs">
                  No ledger entries yet. {isAdmin && 'Run "Backfill from Closings" above to import historical variance.'}
                </div>
              ) : (
                <div className="space-y-1.5" data-testid="reserve-ledger">
                  {ledger.map((e) => {
                    const meta = TYPE_META[e.type] || { label: e.type, tone: 'slate' };
                    const isCredit = (e.amount || 0) > 0;
                    const reversed = e.reversed;
                    const tones = {
                      emerald: 'text-emerald-700 bg-emerald-50 border-emerald-200',
                      blue:    'text-blue-700 bg-blue-50 border-blue-200',
                      amber:   'text-amber-700 bg-amber-50 border-amber-200',
                      red:     'text-red-700 bg-red-50 border-red-200',
                      slate:   'text-slate-700 bg-slate-50 border-slate-200',
                    };
                    return (
                      <div key={e.id}
                        className={`flex items-start gap-3 p-2.5 rounded-lg border ${reversed ? 'opacity-50 line-through' : 'border-slate-200 bg-white'}`}>
                        <Badge className={`text-[10px] shrink-0 ${tones[meta.tone]}`}>{meta.label}</Badge>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-xs font-semibold text-slate-800">{e.source_ref}</span>
                            <Badge className="text-[9px] bg-slate-100 text-slate-600 capitalize">{e.pool}</Badge>
                            <span className="text-[10px] text-slate-400">{e.date}</span>
                          </div>
                          {e.note && <p className="text-[11px] text-slate-500 mt-0.5">{e.note}</p>}
                          {e.applied_to && (
                            <p className="text-[10px] text-blue-600 mt-0.5">
                              → {e.applied_to.replace('_', ' ')}
                            </p>
                          )}
                          {e.verifier_name && (
                            <p className="text-[10px] text-slate-400 mt-0.5">
                              Verified by {e.verifier_name} · by {e.created_by_name}
                            </p>
                          )}
                        </div>
                        <div className="text-right shrink-0">
                          <p className={`text-sm font-bold font-mono ${isCredit ? 'text-emerald-600' : 'text-red-600'}`}>
                            {isCredit ? '+' : ''}{fmt(e.amount)}
                          </p>
                          <p className="text-[10px] text-slate-400 font-mono">bal {fmt(e.balance_after)}</p>
                          {!reversed && e.type === 'auto_credit' && isAdmin && (
                            <button
                              onClick={() => { setClawTarget(e); setActionDialog('claw'); }}
                              className="text-[10px] text-red-500 hover:underline mt-1"
                              data-testid={`reserve-claw-${e.id}`}>
                              <RotateCcw size={9} className="inline mr-0.5" /> Claw back
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Modals */}
      <PinActionModal
        open={actionDialog === 'apply'} busy={busy}
        title="Apply Reserve to Audit"
        description="Debit the reserve pool to offset an audit finding. Requires manager / admin PIN."
        showAppliedTo showAmount maxAmount={reserveBalance}
        onClose={() => setActionDialog(null)}
        onSubmit={submitApply}
      />
      <PinActionModal
        open={actionDialog === 'net'} busy={busy}
        title="Net Reserve Against Deficit"
        description="Both pools shrink by the same amount. Useful when overage and shortage from different days should cancel out."
        showAmount maxAmount={Math.min(reserveBalance, deficitBalance)}
        onClose={() => setActionDialog(null)}
        onSubmit={submitNet}
      />
      <PinActionModal
        open={actionDialog === 'claw'} busy={busy}
        title="Claw Back Entry"
        description={clawTarget ? `Reverse ${fmt(clawTarget.amount)} from ${clawTarget.source_ref}. Used when the underlying close was re-keyed or the variance was misclassified.` : ''}
        showAmount={false}
        onClose={() => { setActionDialog(null); setClawTarget(null); }}
        onSubmit={submitClaw}
      />

      {onClose && (
        <div className="text-center">
          <button onClick={onClose} className="text-xs text-slate-400 hover:text-slate-600">
            <AlertTriangle size={11} className="inline mr-1" /> Close
          </button>
        </div>
      )}
    </div>
  );
}
