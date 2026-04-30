import { useCallback, useEffect, useState } from 'react';
import { api } from '../../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { ShieldCheck, AlertTriangle, ArrowRight, RefreshCw, TrendingUp } from 'lucide-react';

const PERIODS = [
  { value: 7,  label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
];

// A tiny 0-100 arc/dial drawn with SVG. Kept inline so the widget has no extra deps.
function ScoreDial({ value, invert = false, label, subLabel }) {
  const v = Math.max(0, Math.min(100, value || 0));
  // colour: for "Health" (invert=false) higher=green; for "Risk" (invert=true) higher=red
  const pick = (low, mid, high) => invert
    ? (v <= 20 ? low : v <= 50 ? mid : high)
    : (v >= 85 ? low : v >= 50 ? mid : high);
  const colour = pick('#059669', '#d97706', '#dc2626');
  const circumference = 2 * Math.PI * 34;
  const offset = circumference * (1 - v / 100);
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative w-16 h-16 shrink-0">
        <svg viewBox="0 0 80 80" className="w-full h-full -rotate-90">
          <circle cx="40" cy="40" r="34" stroke="#f1f5f9" strokeWidth="8" fill="none" />
          <circle cx="40" cy="40" r="34" stroke={colour} strokeWidth="8" fill="none"
            strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 0.6s ease-out' }} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-bold font-mono leading-none" style={{ color: colour }}>{v}</span>
          <span className="text-[8px] text-slate-400 font-medium mt-0.5">/100</span>
        </div>
      </div>
      <div className="min-w-0">
        <p className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">{label}</p>
        <p className="text-sm font-bold leading-tight" style={{ color: colour }}>{subLabel || '—'}</p>
      </div>
    </div>
  );
}

function MiniStat({ label, value, hint, tone = 'slate' }) {
  const tones = {
    slate: 'text-slate-700',
    emerald: 'text-emerald-700',
    amber: 'text-amber-700',
    red: 'text-red-600',
  };
  return (
    <div className="p-1.5 rounded-lg bg-slate-50">
      <p className="text-[9px] uppercase tracking-wide text-slate-400 font-semibold">{label}</p>
      <p className={`text-sm font-bold font-mono ${tones[tone]}`}>{value}</p>
      {hint && <p className="text-[9px] text-slate-400 leading-tight">{hint}</p>}
    </div>
  );
}

export default function AuditPulseWidget({ branchId = null }) {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [days, setDays] = useState(30);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true); else setRefreshing(true);
    try {
      const res = await api.get('/audit/pulse', {
        params: { days, ...(branchId ? { branch_id: branchId } : {}) },
      });
      setData(res.data);
    } catch { /* silent — widget will show fallback */ }
    setLoading(false);
    setRefreshing(false);
  }, [days, branchId]);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <Card className="border-slate-200 h-full" data-testid="audit-pulse-widget">
      <CardContent className="flex items-center justify-center h-40">
        <RefreshCw size={16} className="animate-spin text-slate-400" />
      </CardContent>
    </Card>
  );

  if (!data) return (
    <Card className="border-slate-200 h-full" data-testid="audit-pulse-widget">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold flex items-center gap-2">
          <ShieldCheck size={14} className="text-[#1A4D2E]" /> Audit Pulse
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-slate-400 py-4 text-center">No data yet</p>
      </CardContent>
    </Card>
  );

  const k = data.kpis || {};
  const marginTone = (k.gross_margin_pct || 0) >= 15 ? 'emerald' : (k.gross_margin_pct || 0) >= 5 ? 'amber' : 'red';
  const voidTone = (k.void_rate_pct || 0) <= 1 ? 'emerald' : (k.void_rate_pct || 0) <= 3 ? 'amber' : 'red';
  const dsoTone = (k.dso_days || 0) <= 30 ? 'emerald' : (k.dso_days || 0) <= 60 ? 'amber' : 'red';

  return (
    <Card className="border-slate-200 h-full" data-testid="audit-pulse-widget">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <ShieldCheck size={14} className="text-[#1A4D2E]" /> Audit Pulse
          </CardTitle>
          <div className="flex items-center gap-1.5">
            <Select value={String(days)} onValueChange={(v) => setDays(parseInt(v, 10))}>
              <SelectTrigger className="h-7 w-28 text-xs" data-testid="audit-pulse-period">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PERIODS.map(p => <SelectItem key={p.value} value={String(p.value)}>{p.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <button onClick={() => load(true)} className="text-slate-400 hover:text-slate-600 p-1" title="Refresh">
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Dual score dials */}
        <div className="grid grid-cols-2 gap-3" data-testid="audit-pulse-scores">
          <ScoreDial
            value={data.health_score}
            label="Health"
            subLabel={data.health_label}
          />
          <ScoreDial
            value={data.fraud_risk_score}
            invert
            label="Fraud Risk"
            subLabel={data.fraud_risk_label}
          />
        </div>

        {/* Top risk factors (only if any > 0) */}
        {data.top_risk_factors?.length > 0 && (
          <div className="space-y-1" data-testid="audit-pulse-risks">
            <p className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Top Red Flags</p>
            {data.top_risk_factors.map((r, i) => {
              const pct = r.max > 0 ? (r.points / r.max) * 100 : 0;
              const hot = pct >= 75;
              return (
                <div key={i} className="flex items-center gap-2">
                  <AlertTriangle size={11} className={hot ? 'text-red-500 shrink-0' : 'text-amber-500 shrink-0'} />
                  <span className="text-xs text-slate-700 flex-1 truncate">{r.factor}</span>
                  <span className="text-[10px] font-mono text-slate-500">{r.value}</span>
                  <span className={`text-[10px] font-bold font-mono ${hot ? 'text-red-600' : 'text-amber-600'}`}>
                    {r.points}/{r.max}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Mini KPI strip */}
        <div className="grid grid-cols-3 gap-1.5 pt-1 border-t border-slate-100">
          <MiniStat
            label="Margin"
            value={`${(k.gross_margin_pct || 0).toFixed(1)}%`}
            hint={`${k.total_txns || 0} txns`}
            tone={marginTone}
          />
          <MiniStat
            label="Void Rate"
            value={`${(k.void_rate_pct || 0).toFixed(1)}%`}
            hint={`${k.voided_count || 0} voided`}
            tone={voidTone}
          />
          <MiniStat
            label="DSO"
            value={`${(k.dso_days || 0).toFixed(0)}d`}
            hint="AR days"
            tone={dsoTone}
          />
        </div>

        {/* Drill-through */}
        <button onClick={() => navigate('/audit')}
          className="text-[10px] text-[#1A4D2E] hover:underline flex items-center gap-1 pt-1"
          data-testid="audit-pulse-view-full">
          <TrendingUp size={10} /> Open full Audit Center <ArrowRight size={10} />
        </button>
      </CardContent>
    </Card>
  );
}
