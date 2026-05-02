import { useEffect, useState, useMemo } from 'react';
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
  ArrowLeft, Shield, Check, X, AlertTriangle, Building2, Package, ArrowRight, Clock, Loader2,
} from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';

export default function ApproveTransferPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [retails, setRetails] = useState({}); // product_id -> retail
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api.get(`/branch-transfers/${id}`)
      .then(r => {
        setOrder(r.data);
        const initial = {};
        (r.data.items || []).forEach(it => {
          initial[it.product_id] = String(it.branch_retail || '');
        });
        setRetails(initial);
      })
      .catch(e => toast.error(e.response?.data?.detail || 'Failed to load transfer'))
      .finally(() => setLoading(false));
  }, [id]);

  const totals = useMemo(() => {
    if (!order) return { capital: 0, transfer: 0, retail: 0, profit: 0 };
    const items = order.items || [];
    const capital = items.reduce((s, it) => s + (parseFloat(it.branch_capital) || 0) * (parseFloat(it.qty) || 0), 0);
    const transfer = items.reduce((s, it) => s + (parseFloat(it.transfer_capital) || 0) * (parseFloat(it.qty) || 0), 0);
    const retail = items.reduce((s, it) => {
      const r = parseFloat(retails[it.product_id]) || 0;
      return s + r * (parseFloat(it.qty) || 0);
    }, 0);
    return { capital, transfer, retail, profit: retail - transfer };
  }, [order, retails]);

  const allRetailsFilled = useMemo(() => {
    if (!order) return false;
    return (order.items || []).every(it => {
      const r = parseFloat(retails[it.product_id]) || 0;
      return r > 0;
    });
  }, [order, retails]);

  const handleApprove = async () => {
    if (!allRetailsFilled) {
      if (!window.confirm('Some rows have no retail price set. Approve and dispatch anyway?')) return;
    }
    setSubmitting(true);
    try {
      const items = (order.items || []).map(it => ({
        product_id: it.product_id,
        branch_retail: parseFloat(retails[it.product_id]) || 0,
      }));
      await api.post(`/branch-transfers/${id}/approve`, { items, notes });
      toast.success('Approved & dispatched. Manager has been notified.');
      navigate('/branch-transfers?subtab=in_transit');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to approve');
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
      navigate('/branch-transfers?subtab=returned');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to reject');
    } finally {
      setSubmitting(false);
    }
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

  if (!isAdmin) {
    return (
      <div className="p-12 text-center" data-testid="approve-not-admin">
        <Shield size={32} className="mx-auto mb-3 text-amber-500" />
        <p className="text-slate-700 font-semibold">Admin access required</p>
        <p className="text-slate-500 text-sm mt-1">Only admins can approve branch transfers.</p>
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
    <div className="p-4 md:p-8 max-w-5xl mx-auto" data-testid="approve-transfer-page">
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
        </CardContent>
      </Card>

      {/* Items table */}
      <Card className="mb-4">
        <CardHeader className="pb-3">
          <CardTitle className="text-base" style={{ fontFamily: 'Manrope' }}>
            Items <span className="text-slate-400 font-normal">({order.items?.length || 0})</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="text-left px-4 py-2.5">Product</th>
                  <th className="text-right px-3 py-2.5">Qty</th>
                  <th className="text-right px-3 py-2.5">Branch Capital</th>
                  <th className="text-right px-3 py-2.5">Transfer Capital</th>
                  <th className="text-right px-3 py-2.5 bg-amber-50 text-amber-800">Branch Retail *</th>
                  <th className="text-right px-3 py-2.5">Margin / unit</th>
                </tr>
              </thead>
              <tbody>
                {(order.items || []).map((it, idx) => {
                  const r = parseFloat(retails[it.product_id]) || 0;
                  const tc = parseFloat(it.transfer_capital) || 0;
                  const margin = r - tc;
                  const marginColor = r === 0 ? 'text-slate-400' : margin <= 0 ? 'text-red-600' : margin < (order.min_margin || 20) ? 'text-amber-600' : 'text-emerald-600';
                  return (
                    <tr key={it.product_id || idx} className="border-t border-slate-100">
                      <td className="px-4 py-2.5">
                        <div className="font-semibold text-slate-800">{it.product_name}</div>
                        <div className="text-[10px] text-slate-400 font-mono">{it.sku}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono">{it.qty}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-600">{formatPHP(it.branch_capital)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-blue-700">{formatPHP(it.transfer_capital)}</td>
                      <td className="px-3 py-2.5 bg-amber-50/40">
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          inputMode="decimal"
                          value={retails[it.product_id] || ''}
                          onChange={e => setRetails({ ...retails, [it.product_id]: e.target.value })}
                          onWheel={e => e.currentTarget.blur()}
                          className="h-9 text-right font-mono font-bold border-amber-300 focus:border-amber-500"
                          placeholder="0.00"
                          data-testid={`retail-input-${it.product_id}`}
                        />
                      </td>
                      <td className={`px-3 py-2.5 text-right font-mono font-semibold ${marginColor}`}>
                        {r === 0 ? '—' : formatPHP(margin)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Approval note */}
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

      {/* Totals + actions */}
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
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Branch Retail</p>
            <p className="font-mono font-bold text-emerald-700">{formatPHP(totals.retail)}</p>
          </div>
          <div className="text-right border-l border-slate-200 pl-6">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Profit at Target</p>
            <p className={`font-mono font-bold ${totals.profit >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>{formatPHP(totals.profit)}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
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
            onClick={handleApprove}
            disabled={submitting}
            className="bg-emerald-600 hover:bg-emerald-700 text-white"
            data-testid="approve-btn"
          >
            {submitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
            Approve & Dispatch
          </Button>
        </div>
      </div>

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
