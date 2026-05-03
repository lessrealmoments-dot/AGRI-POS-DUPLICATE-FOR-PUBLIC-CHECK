import { useEffect, useState, useMemo, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Textarea } from '../components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import {
  ArrowLeft, Shield, Check, X, AlertTriangle, Building2, ArrowRight, Clock, Loader2, KeyRound,
} from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';

// 3-min warm PIN cache per ApproveTransferPage session.
const PIN_TTL_MS = 3 * 60 * 1000;

export default function ApproveTransferPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [insights, setInsights] = useState({}); // product_id -> { current_target_retail, target_moving_capital, target_last_purchase_cost, target_stock, current_source_retail }
  const [retails, setRetails] = useState({}); // product_id -> string retail
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [pinOpen, setPinOpen] = useState(false);
  const [pinValue, setPinValue] = useState('');
  const [pinPurpose, setPinPurpose] = useState('approve'); // 'approve' only for now — reject needs no PIN

  // Warm PIN cache
  const pinCacheRef = useRef({ pin: '', expires_at: 0 });
  const isPinWarm = () => pinCacheRef.current.pin && Date.now() < pinCacheRef.current.expires_at;
  const cachePin = (pin) => {
    pinCacheRef.current = { pin, expires_at: Date.now() + PIN_TTL_MS };
  };

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([
      api.get(`/branch-transfers/${id}`),
      api.get(`/branch-transfers/${id}/approval-insights`).catch(() => ({ data: { insights: {} } })),
    ])
      .then(([ord, ins]) => {
        setOrder(ord.data);
        setInsights(ins.data.insights || {});
        const initial = {};
        (ord.data.items || []).forEach(it => {
          // Pre-fill retail if the draft already had one, otherwise leave blank
          // so the "inherit target retail" placeholder nudges the admin.
          initial[it.product_id] = it.branch_retail > 0 ? String(it.branch_retail) : '';
        });
        setRetails(initial);
      })
      .catch(e => toast.error(e.response?.data?.detail || 'Failed to load transfer'))
      .finally(() => setLoading(false));
  }, [id]);

  const totals = useMemo(() => {
    if (!order) return { capital: 0, transfer: 0, retail: 0, profit: 0, blank_count: 0 };
    const items = order.items || [];
    const capital = items.reduce((s, it) => s + (parseFloat(it.branch_capital) || 0) * (parseFloat(it.qty) || 0), 0);
    const transfer = items.reduce((s, it) => s + (parseFloat(it.transfer_capital) || 0) * (parseFloat(it.qty) || 0), 0);
    let blank_count = 0;
    const retail = items.reduce((s, it) => {
      const entered = parseFloat(retails[it.product_id]);
      let effective = entered;
      if (!entered || entered <= 0) {
        blank_count += 1;
        // Use insight's current target retail as the effective price
        effective = parseFloat(insights[it.product_id]?.current_target_retail || 0);
      }
      return s + (effective || 0) * (parseFloat(it.qty) || 0);
    }, 0);
    return { capital, transfer, retail, profit: retail - transfer, blank_count };
  }, [order, retails, insights]);

  // Open PIN dialog (or reuse warm PIN) then call approve()
  const requestApprove = () => {
    if (totals.blank_count > 0) {
      const ok = window.confirm(
        `${totals.blank_count} row(s) have no retail set — they will inherit the target branch's ` +
        `CURRENT retail price. Continue?`
      );
      if (!ok) return;
    }
    if (isPinWarm()) {
      doApprove(pinCacheRef.current.pin);
      return;
    }
    setPinPurpose('approve');
    setPinValue('');
    setPinOpen(true);
  };

  const doApprove = async (pin) => {
    setSubmitting(true);
    try {
      const items = (order.items || []).map(it => ({
        product_id: it.product_id,
        branch_retail: parseFloat(retails[it.product_id]) || 0, // 0 = blank → backend inherits
      }));
      await api.post(`/branch-transfers/${id}/approve`, { items, notes, pin });
      cachePin(pin); // warm for 3 minutes
      toast.success('Approved & dispatched. Manager notified by SMS.');
      setPinOpen(false);
      navigate('/branch-transfers?tab=history');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to approve');
      if (e.response?.status === 403) {
        // Invalidate warm cache on auth failure
        pinCacheRef.current = { pin: '', expires_at: 0 };
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (rejectReason.trim().length < 4) {
      toast.error('Reason must be at least 4 characters');
      return;
    }
    setSubmitting(true);
    try {
      await api.post(`/branch-transfers/${id}/reject`, { reason: rejectReason.trim() });
      toast.success('Transfer rejected. Manager has been notified.');
      setRejectOpen(false);
      navigate('/branch-transfers?tab=history');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to reject');
    } finally {
      setSubmitting(false);
    }
  };

  const handlePinSubmit = (e) => {
    e?.preventDefault?.();
    const pin = pinValue.trim();
    if (!pin) { toast.error('Enter PIN'); return; }
    if (pinPurpose === 'approve') doApprove(pin);
  };

  if (loading) {
    return (
      <div className="p-12 text-center text-slate-400">
        <Loader2 size={28} className="animate-spin mx-auto mb-3" />
        <p>Loading transfer for review...</p>
      </div>
    );
  }

  if (!order) {
    return (
      <div className="p-12 text-center text-slate-400">
        <AlertTriangle size={28} className="mx-auto mb-3" />
        <p>Transfer not found.</p>
        <Button variant="outline" onClick={() => navigate('/branch-transfers')} className="mt-4">Back to Transfers</Button>
      </div>
    );
  }

  // Permission gate — admin OR a manager with branch_transfers.approve
  const canApprove = user?.role === 'admin'
    || !!user?.permissions?.branch_transfers?.approve;

  if (!canApprove) {
    return (
      <div className="p-12 text-center" data-testid="approve-not-authorized">
        <Shield size={32} className="mx-auto mb-3 text-amber-500" />
        <p className="text-slate-700 font-semibold">Not authorized to approve transfers</p>
        <p className="text-slate-500 text-sm mt-1">
          Ask an admin to enable <span className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">Branch Transfers → Approve Pending Transfer</span> in your User Permissions.
        </p>
        <Button variant="outline" onClick={() => navigate('/branch-transfers')} className="mt-4">Back to Transfers</Button>
      </div>
    );
  }

  if (order.status !== 'pending_approval') {
    return (
      <div className="p-12 text-center" data-testid="approve-not-pending">
        <Clock size={32} className="mx-auto mb-3 text-slate-500" />
        <p className="text-slate-700 font-semibold">This transfer is not awaiting approval</p>
        <p className="text-slate-500 text-sm mt-1">Current status: <Badge>{order.status}</Badge></p>
        <Button variant="outline" onClick={() => navigate('/branch-transfers')} className="mt-4">Back to Transfers</Button>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-8 max-w-6xl mx-auto" data-testid="approve-transfer-page">
      <Button variant="ghost" size="sm" onClick={() => navigate('/branch-transfers')} className="mb-4 text-slate-500" data-testid="back-btn">
        <ArrowLeft size={14} className="mr-1.5" /> Back to Transfers
      </Button>

      <Card className="border-amber-200 border-l-4 border-l-amber-500 mb-4">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-xl" style={{ fontFamily: 'Manrope' }}>
                <Shield size={20} className="text-amber-600" /> Review & Approve Transfer
              </CardTitle>
              <p className="text-slate-500 text-sm mt-1">
                <span className="font-mono font-semibold">{order.order_number}</span>
                {' · '}submitted by <span className="font-semibold">{order.created_by_name}</span>
                {' · '}{order.created_at?.slice(0, 16).replace('T', ' ')}
              </p>
            </div>
            <Badge className="bg-amber-100 text-amber-800 px-3 py-1">Pending Approval</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3 text-base">
            <div className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg">
              <Building2 size={16} className="text-slate-500" />
              <span className="font-semibold text-slate-700">{order.from_branch_name || order.from_branch_id}</span>
            </div>
            <ArrowRight size={20} className="text-slate-400" />
            <div className="flex items-center gap-2 px-3 py-2 bg-emerald-50 rounded-lg">
              <Building2 size={16} className="text-emerald-600" />
              <span className="font-semibold text-emerald-800">{order.to_branch_name || order.to_branch_id}</span>
            </div>
          </div>
          <p className="text-xs text-slate-500 mt-3">
            💡 Leave Branch Retail <b>blank</b> to inherit the target branch's <b>current retail price</b>. Insights on the right show what each row will become.
          </p>
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader className="pb-3">
          <CardTitle className="text-base" style={{ fontFamily: 'Manrope' }}>
            Items <span className="text-slate-400 font-normal">({order.items?.length || 0})</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="text-left px-4 py-2.5">Product</th>
                  <th className="text-right px-2 py-2.5">Qty</th>
                  <th className="text-right px-2 py-2.5" title="Source branch: what we book it at">From Cap</th>
                  <th className="text-right px-2 py-2.5 text-blue-700" title="Transfer price to destination">Xfer</th>
                  <th className="text-right px-2 py-2.5" title="Target branch moving-avg cost">Tgt Moving</th>
                  <th className="text-right px-2 py-2.5" title="Target branch last purchase cost">Tgt Last Buy</th>
                  <th className="text-right px-2 py-2.5 text-slate-600" title="Target branch's current retail — will be used if admin leaves input blank">Current Tgt Retail</th>
                  <th className="text-right px-2 py-2.5 bg-amber-50 text-amber-800 min-w-[110px]">New Retail</th>
                  <th className="text-right px-2 py-2.5">Margin</th>
                </tr>
              </thead>
              <tbody>
                {(order.items || []).map((it, idx) => {
                  const pid = it.product_id;
                  const ins = insights[pid] || {};
                  const entered = parseFloat(retails[pid]);
                  const effectiveRetail = (entered > 0) ? entered : parseFloat(ins.current_target_retail || 0);
                  const tc = parseFloat(it.transfer_capital) || 0;
                  const margin = effectiveRetail - tc;
                  const marginColor = effectiveRetail === 0 ? 'text-slate-400' : margin <= 0 ? 'text-red-600' : margin < (order.min_margin || 20) ? 'text-amber-600' : 'text-emerald-600';
                  const willInherit = !(entered > 0);
                  return (
                    <tr key={pid || idx} className="border-t border-slate-100">
                      <td className="px-4 py-2.5">
                        <div className="font-semibold text-slate-800">{it.product_name}</div>
                        <div className="text-[10px] text-slate-400 font-mono">{it.sku}</div>
                        {ins.target_stock !== undefined && (
                          <div className="text-[10px] text-emerald-600 mt-0.5">
                            {ins.target_stock} in tgt stock
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-right font-mono">{it.qty}</td>
                      <td className="px-2 py-2.5 text-right font-mono text-slate-600">{formatPHP(it.branch_capital)}</td>
                      <td className="px-2 py-2.5 text-right font-mono text-blue-700">{formatPHP(it.transfer_capital)}</td>
                      <td className="px-2 py-2.5 text-right font-mono text-slate-500">
                        {ins.target_moving_capital > 0 ? formatPHP(ins.target_moving_capital) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2.5 text-right font-mono text-slate-500">
                        {ins.target_last_purchase_cost > 0 ? formatPHP(ins.target_last_purchase_cost) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2.5 text-right font-mono text-slate-700">
                        {ins.current_target_retail > 0 ? formatPHP(ins.current_target_retail) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-2 py-2.5 bg-amber-50/40">
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          inputMode="decimal"
                          value={retails[pid] || ''}
                          onChange={e => setRetails({ ...retails, [pid]: e.target.value })}
                          onWheel={e => e.currentTarget.blur()}
                          className={`h-9 text-right font-mono font-bold border-amber-300 focus:border-amber-500 ${willInherit ? 'placeholder:text-amber-700 placeholder:opacity-60' : ''}`}
                          placeholder={ins.current_target_retail > 0 ? `${ins.current_target_retail.toFixed(2)} (keep)` : 'blank'}
                          data-testid={`retail-input-${pid}`}
                        />
                      </td>
                      <td className={`px-2 py-2.5 text-right font-mono font-semibold ${marginColor}`}>
                        {effectiveRetail === 0 ? '—' : formatPHP(margin)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardContent className="p-4">
          <Label className="text-xs text-slate-500">Approval Note (optional)</Label>
          <Textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="Optional note for the manager / audit trail"
            className="mt-1.5"
            rows={2}
            data-testid="approval-note"
          />
        </CardContent>
      </Card>

      <div className="bg-white border-2 border-slate-200 rounded-xl p-5 flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-6 text-sm">
          <div className="text-right">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Our Cost</p>
            <p className="font-mono font-bold text-slate-700">{formatPHP(totals.capital)}</p>
          </div>
          <ArrowRight size={14} className="text-slate-400" />
          <div className="text-right">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Transfer Price</p>
            <p className="font-mono font-bold text-blue-700">{formatPHP(totals.transfer)}</p>
          </div>
          <ArrowRight size={14} className="text-slate-400" />
          <div className="text-right">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Effective Retail</p>
            <p className="font-mono font-bold text-emerald-700">{formatPHP(totals.retail)}</p>
            {totals.blank_count > 0 && (
              <p className="text-[10px] text-amber-700 font-semibold mt-0.5">
                {totals.blank_count} row(s) will inherit target retail
              </p>
            )}
          </div>
          <div className="text-right border-l border-slate-200 pl-6">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Profit at Target</p>
            <p className={`font-mono font-bold ${totals.profit >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>{formatPHP(totals.profit)}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isPinWarm() && (
            <Badge className="bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px] flex items-center gap-1">
              <KeyRound size={10} /> PIN warm 3 min
            </Badge>
          )}
          <Button
            variant="outline"
            onClick={() => setRejectOpen(true)}
            disabled={submitting}
            className="border-rose-300 text-rose-700 hover:bg-rose-50"
            data-testid="reject-btn"
          >
            <X size={14} className="mr-1.5" /> Return / Reject
          </Button>
          <Button
            onClick={requestApprove}
            disabled={submitting}
            className="bg-emerald-600 hover:bg-emerald-700 text-white"
            data-testid="approve-btn"
          >
            {submitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
            Approve & Dispatch
          </Button>
        </div>
      </div>

      {/* PIN prompt */}
      <Dialog open={pinOpen} onOpenChange={setPinOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <KeyRound size={18} className="text-amber-600" /> PIN Required
            </DialogTitle>
            <DialogDescription>
              Enter your Admin PIN, or an authorized manager's PIN, to approve this transfer. PIN will stay valid for 3 minutes.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handlePinSubmit} className="space-y-3">
            <Input
              type="password"
              inputMode="numeric"
              autoFocus
              value={pinValue}
              onChange={e => setPinValue(e.target.value)}
              placeholder="••••••"
              className="text-center tracking-[0.5em] font-mono text-lg"
              data-testid="pin-input"
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setPinOpen(false)} disabled={submitting}>Cancel</Button>
              <Button type="submit" disabled={submitting || !pinValue.trim()}
                className="bg-emerald-600 hover:bg-emerald-700 text-white" data-testid="pin-confirm-btn">
                {submitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                Approve
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Reject dialog */}
      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <X size={18} className="text-rose-600" /> Return Transfer
            </DialogTitle>
            <DialogDescription>
              The manager will get an SMS with this reason and can edit + resubmit.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Label className="text-xs text-slate-500">Reason (required, ≥ 4 chars)</Label>
            <Textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              placeholder="e.g. transfer capital is too low for current market price"
              rows={3}
              data-testid="reject-reason"
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setRejectOpen(false)} disabled={submitting}>Cancel</Button>
              <Button onClick={handleReject} disabled={submitting || rejectReason.trim().length < 4}
                className="bg-rose-600 hover:bg-rose-700 text-white" data-testid="confirm-reject-btn">
                {submitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <X size={14} className="mr-1.5" />}
                Confirm Return
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
