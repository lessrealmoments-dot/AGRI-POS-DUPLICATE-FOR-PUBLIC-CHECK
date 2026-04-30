/**
 * OverageReserveCard — compact, read-mostly summary of the Cash Overage Reserve
 * pool and the parallel Shortage Deficit counter. Lives on the Wallets page so
 * owners see the "fourth wallet" right next to Cashier/Safe/Capital.
 *
 * Provides a click-through to the full Audit Center → Reserve tab where the
 * full ledger and apply/claw-back actions live.
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { ShieldCheck, RefreshCw, ArrowRight, AlertTriangle } from 'lucide-react';

const fmt = (n) => '₱' + (parseFloat(n) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 });

export default function OverageReserveCard({ branchId = null, compact = false }) {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/reserve/summary', {
        params: branchId ? { branch_id: branchId } : {},
      });
      setData(res.data);
    } catch { /* ignore */ }
    setLoading(false);
  }, [branchId]);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <Card className="border-slate-200" data-testid="reserve-card">
      <CardContent className="flex items-center justify-center h-24">
        <RefreshCw size={14} className="animate-spin text-slate-400" />
      </CardContent>
    </Card>
  );

  if (!data) return null;

  const { reserve_total, deficit_total, net_pool } = data.totals || {};
  const reserve = reserve_total || 0;
  const deficit = deficit_total || 0;
  const net = net_pool || 0;

  return (
    <Card className="border-slate-200" data-testid="reserve-card">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <ShieldCheck size={14} className="text-[#1A4D2E]" /> Overage Reserve
          </CardTitle>
          <button onClick={() => navigate('/audit?tab=reserve')}
            className="text-[10px] text-[#1A4D2E] hover:underline flex items-center gap-1"
            data-testid="reserve-card-open-audit">
            Manage <ArrowRight size={10} />
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2.5">
        <div className="grid grid-cols-3 gap-2">
          <div className="p-2 rounded-lg bg-emerald-50 border border-emerald-100">
            <p className="text-[9px] text-emerald-600 uppercase font-semibold tracking-wider">Reserve</p>
            <p className="text-sm font-bold text-emerald-700 font-mono leading-tight">{fmt(reserve)}</p>
          </div>
          <div className="p-2 rounded-lg bg-red-50 border border-red-100">
            <p className="text-[9px] text-red-600 uppercase font-semibold tracking-wider">Shortage</p>
            <p className="text-sm font-bold text-red-700 font-mono leading-tight">{fmt(deficit)}</p>
          </div>
          <div className={`p-2 rounded-lg border ${net >= 0 ? 'bg-blue-50 border-blue-100' : 'bg-amber-50 border-amber-100'}`}>
            <p className={`text-[9px] uppercase font-semibold tracking-wider ${net >= 0 ? 'text-blue-600' : 'text-amber-600'}`}>Net</p>
            <p className={`text-sm font-bold font-mono leading-tight ${net >= 0 ? 'text-blue-700' : 'text-amber-700'}`}>{net >= 0 ? '+' : ''}{fmt(net)}</p>
          </div>
        </div>
        {!compact && (
          <p className="text-[10px] text-slate-500 leading-snug flex items-start gap-1">
            <AlertTriangle size={11} className="text-slate-400 shrink-0 mt-0.5" />
            <span>
              Auto-pooled from each daily close. Apply during audit to offset inventory variance / capital losses.
              Every entry is fully traceable in the Audit Center → Reserve tab.
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
