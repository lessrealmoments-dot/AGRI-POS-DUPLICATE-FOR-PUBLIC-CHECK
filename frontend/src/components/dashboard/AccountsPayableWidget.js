import { useState, useEffect, useCallback } from 'react';
import { api, useAuth } from '../../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { formatPHP } from '../../lib/utils';
import ReviewDetailDialog from '../ReviewDetailDialog';
import HistoricalSupplierPODialog from '../HistoricalSupplierPODialog';
import { FileText, AlertTriangle, Clock, ChevronRight, RefreshCw, CheckCircle2, Camera, Archive } from 'lucide-react';

export default function AccountsPayableWidget({ branchId }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedPO, setSelectedPO] = useState(null);
  const [historicalOpen, setHistoricalOpen] = useState(false);
  const isAdmin = ['admin', 'owner', 'super_admin'].includes((user?.role || '').toLowerCase());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (branchId && branchId !== 'all') params.branch_id = branchId;
      const res = await api.get('/dashboard/accounts-payable', { params });
      setData(res.data);
    } catch {}
    setLoading(false);
  }, [branchId]);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <Card className="border-slate-200 h-full" data-testid="ap-widget">
      <CardContent className="flex items-center justify-center h-40"><RefreshCw size={16} className="animate-spin text-slate-400" /></CardContent>
    </Card>
  );

  const d = data || {};
  const hasOverdue = d.overdue_count > 0;

  const ReviewBadge = ({ status }) => {
    if (status === 'reviewed') return (
      <span title="Receipts reviewed" className="flex items-center gap-0.5 text-[9px] text-emerald-600">
        <CheckCircle2 size={9} /> OK
      </span>
    );
    return (
      <span title="Receipts not yet reviewed" className="flex items-center gap-0.5 text-[9px] text-amber-500">
        <Camera size={9} /> Review
      </span>
    );
  };

  const PORow = ({ po, variant }) => {
    const isHistorical = po.kind === 'historical';
    return (
    <div className={`flex items-center justify-between text-xs rounded px-2.5 py-1.5 ${
      variant === 'overdue' ? 'bg-red-50' : 'bg-amber-50'
    }`}>
      <div className="flex-1 min-w-0">
        <button className="font-semibold font-mono text-blue-600 hover:underline cursor-pointer"
          onClick={() => isHistorical ? setHistoricalOpen(true) : setSelectedPO(po.po_id)}
          data-testid={`ap-po-${po.po_id}`}>
          {po.po_number}
        </button>
        {isHistorical && (
          <Badge className="ml-1 text-[9px] bg-violet-100 text-violet-700 border-violet-200"
            data-testid={`ap-po-historical-${po.po_id}`}>
            pre-system
          </Badge>
        )}
        <p className="text-slate-500 text-[10px] truncate">{po.vendor}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {!isHistorical && <ReviewBadge status={po.receipt_review_status} />}
        <div className="text-right">
          <p className={`font-bold ${variant === 'overdue' ? 'text-red-700' : 'text-amber-700'}`}>{formatPHP(po.balance)}</p>
          <p className={`text-[10px] ${variant === 'overdue' ? 'text-red-500' : 'text-amber-500'}`}>
            {isHistorical
              ? 'carry-forward'
              : variant === 'overdue' ? `${Math.abs(po.days_left)}d overdue` : po.days_left === 0 ? 'Due today' : `${po.days_left}d left`}
          </p>
        </div>
      </div>
    </div>
    );
  };

  return (
    <>
      <Card className={`border-slate-200 h-full ${hasOverdue ? 'border-red-200' : ''}`} data-testid="ap-widget">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <FileText size={15} className="text-red-600" /> Accounts Payable
            </CardTitle>
            <div className="flex items-center gap-1">
              {isAdmin && (
                <Button variant="ghost" size="sm" className="h-7 text-xs text-violet-700 hover:bg-violet-50"
                  onClick={() => setHistoricalOpen(true)}
                  data-testid="ap-add-historical-btn"
                  title="Record a pre-system supplier PO">
                  <Archive size={12} className="mr-1" /> Old POs
                </Button>
              )}
              <Button variant="ghost" size="sm" className="h-7 text-xs text-slate-500" onClick={() => navigate('/pay-supplier')}>
                Pay <ChevronRight size={12} />
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="text-center py-2">
            <p className="text-2xl font-bold font-mono text-red-700" style={{ fontFamily: 'Manrope' }}>{formatPHP(d.total_payable || 0)}</p>
            <p className="text-[10px] text-slate-400">Total outstanding</p>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className={`rounded-lg p-2.5 text-center ${hasOverdue ? 'bg-red-50 border border-red-200' : 'bg-slate-50'}`}>
              <p className={`text-sm font-bold font-mono ${hasOverdue ? 'text-red-700' : 'text-slate-500'}`}>{formatPHP(d.overdue_total || 0)}</p>
              <p className="text-[10px] text-slate-500 flex items-center justify-center gap-1">
                {hasOverdue && <AlertTriangle size={9} className="text-red-500" />}
                Overdue ({d.overdue_count || 0})
              </p>
            </div>
            <div className="rounded-lg p-2.5 text-center bg-amber-50 border border-amber-200">
              <p className="text-sm font-bold font-mono text-amber-700">{formatPHP(d.due_this_week_total || 0)}</p>
              <p className="text-[10px] text-slate-500 flex items-center justify-center gap-1">
                <Clock size={9} className="text-amber-500" /> This Week
              </p>
            </div>
            <div className="rounded-lg p-2.5 text-center bg-slate-50">
              <p className="text-sm font-bold font-mono text-slate-700">{formatPHP(d.upcoming_total || 0)}</p>
              <p className="text-[10px] text-slate-500">Upcoming ({d.upcoming_count || 0})</p>
            </div>
          </div>

          {hasOverdue && (
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-red-600 uppercase">Overdue</p>
              {(d.overdue || []).slice(0, 3).map(po => <PORow key={po.po_id} po={po} variant="overdue" />)}
            </div>
          )}

          {(d.due_this_week || []).length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-amber-600 uppercase">Due This Week</p>
              {(d.due_this_week || []).slice(0, 3).map(po => <PORow key={po.po_id} po={po} variant="due" />)}
            </div>
          )}

          {(d.upcoming_count || 0) > 0 && !hasOverdue && (d.due_this_week || []).length === 0 && (
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-slate-500 uppercase">Upcoming</p>
              {/* Show a count summary only if no overdue/this-week items */}
              <p className="text-xs text-slate-500 py-1">{d.upcoming_count} payment{d.upcoming_count > 1 ? 's' : ''} upcoming</p>
            </div>
          )}

          {d.total_payable === 0 && (
            <p className="text-xs text-emerald-600 text-center py-3">All supplier payments are up to date</p>
          )}

          {/* Receipt review hint */}
          {(d.total_payable || 0) > 0 && (
            <p className="text-[9px] text-slate-400 text-center border-t border-slate-100 pt-2">
              Click any PO to review receipts &amp; verify before payment
            </p>
          )}
        </CardContent>
      </Card>

      <ReviewDetailDialog
        open={!!selectedPO}
        onClose={() => { setSelectedPO(null); load(); }}
        recordType="purchase_order"
        recordId={selectedPO}
        showReviewAction={true}
        showPayAction={true}
        onReviewed={() => { setSelectedPO(null); load(); }}
      />

      {isAdmin && (
        <HistoricalSupplierPODialog
          open={historicalOpen}
          onOpenChange={setHistoricalOpen}
          defaultBranchId={branchId && branchId !== 'all' ? branchId : ''}
          onChange={load}
        />
      )}
    </>
  );
}
