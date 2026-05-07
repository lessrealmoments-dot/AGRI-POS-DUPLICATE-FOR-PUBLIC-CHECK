/**
 * Iter 253 — Z-Report SMS Share Page (mobile-first, public, token-only)
 *
 * Tap the SMS link → /zr/<token> renders this page.
 * No app login required. Single-tap PDF download. Auto-revokes if anomaly
 * detected on the backend (e.g. SMS forwarded to many recipients).
 */
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { Download, Lock, Calendar, Building2, AlertTriangle } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL || '';
const formatPHP = (n) => `₱${Number(n || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function ZReportSharePage() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let canc = false;
    async function load() {
      try {
        setLoading(true);
        const res = await axios.get(`${API}/api/zreport-share/${token}`);
        if (!canc) setData(res.data);
      } catch (e) {
        const msg = e?.response?.data?.detail || e.message || 'Failed to load report';
        if (!canc) setError(msg);
      } finally {
        if (!canc) setLoading(false);
      }
    }
    load();
    return () => { canc = true; };
  }, [token]);

  const downloadPdf = async () => {
    setDownloading(true);
    try {
      const res = await axios.get(`${API}/api/zreport-share/${token}/pdf`, {
        responseType: 'blob',
      });
      const cd = res.headers['content-disposition'] || '';
      const m = cd.match(/filename="([^"]+)"/);
      const filename = m ? m[1] : `Z-Report ${data?.branch_name || ''} ${data?.date || ''}.pdf`;
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || 'Download failed';
      setError(msg);
    } finally {
      setDownloading(false);
    }
  };

  // Loading
  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6" data-testid="zr-share-loading">
        <div className="text-center text-slate-500 text-sm">
          <div className="w-10 h-10 border-4 border-emerald-300 border-t-emerald-600 rounded-full animate-spin mx-auto mb-3" />
          Loading Z-Report…
        </div>
      </div>
    );
  }

  // Error / revoked / expired
  if (error || !data) {
    const isRevoked = /revoked|expired/i.test(error || '');
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6" data-testid="zr-share-error">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 max-w-md w-full text-center">
          <div className={`w-14 h-14 rounded-full flex items-center justify-center mx-auto mb-3 ${isRevoked ? 'bg-amber-100' : 'bg-red-100'}`}>
            {isRevoked ? <Lock className="text-amber-600" size={28} /> : <AlertTriangle className="text-red-600" size={28} />}
          </div>
          <h1 className="text-lg font-bold text-slate-800 mb-1">
            {isRevoked ? 'Link No Longer Available' : 'Could Not Load Report'}
          </h1>
          <p className="text-sm text-slate-500 leading-relaxed">{error || 'The Z-Report could not be loaded.'}</p>
          <p className="text-[11px] text-slate-400 mt-4">
            If you need access, ask the owner or admin to issue a new link from the Audit Center.
          </p>
        </div>
      </div>
    );
  }

  const c = data.closing || {};
  const isOpen = data.is_open;

  return (
    <div className="min-h-screen bg-slate-50 pb-32" data-testid="zr-share-page">
      {/* Header */}
      <div className="bg-gradient-to-br from-emerald-700 to-emerald-900 text-white px-5 py-6 shadow-md">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-1.5 text-emerald-200 text-[11px] uppercase tracking-wider font-semibold">
            <Building2 size={11} /> {data.company_name || 'Z-Report'}
          </div>
          <h1 className="text-xl font-black mt-0.5 leading-tight">{data.branch_name}</h1>
          <div className="flex items-center gap-1.5 text-emerald-100 text-sm mt-0.5">
            <Calendar size={13} /> {data.date}
          </div>
          {data.recipient_name && (
            <div className="text-[10px] text-emerald-200 mt-2 italic">
              Confidential — Sent to {data.recipient_name}
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="max-w-2xl mx-auto px-4 py-5 space-y-4">
        {isOpen && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs text-amber-800">
            <p className="font-semibold">⚠ Closing not yet sealed</p>
            <p className="mt-0.5">This day has not been finalized in the Closing Wizard. The PDF below shows the live preview.</p>
          </div>
        )}

        {/* Top KPI tiles */}
        <div className="grid grid-cols-2 gap-3" data-testid="zr-share-kpis">
          <Tile label="Total Sales" value={formatPHP(c.total_sales || c.total_cash_sales || 0)} accent="emerald" />
          <Tile label="Cash Drawer Actual" value={formatPHP(c.actual_cash || 0)} accent="blue" />
          <Tile label="Expected Cash" value={formatPHP(c.expected_counter || 0)} accent="violet" />
          <Tile label="Variance" value={formatPHP(c.over_short || 0)} accent={Math.abs(c.over_short || 0) < 1 ? 'emerald' : 'red'} />
        </div>

        {/* Cash flow detail */}
        <Section title="Cash Flow Today">
          <Row label="Cash Sales" value={formatPHP(c.total_cash_sales || 0)} />
          <Row label="Split Cash Portion" value={formatPHP(c.total_split_cash || 0)} />
          <Row label="AR / Credit Payments Today" value={formatPHP(c.total_cash_ar || c.total_ar_received || 0)} highlight />
          {(c.total_ar_same_day || 0) > 0 && (
            <Row label="↳ Same-Day Credit Payments" value={formatPHP(c.total_ar_same_day)} sub />
          )}
          {(c.total_ar_older || 0) > 0 && (
            <Row label="↳ Older Credit Payments" value={formatPHP(c.total_ar_older)} sub />
          )}
          {(c.total_digital_ar || 0) > 0 && (
            <Row label="AR Digital (e-wallet)" value={formatPHP(c.total_digital_ar)} sub muted />
          )}
          <Row label="Cashier Expenses" value={`-${formatPHP(c.total_cashier_expenses || 0)}`} negative />
          <Row label="Net Fund Transfers" value={formatPHP(c.net_fund_transfers || 0)} />
          <Row label="Starting Float" value={formatPHP(c.starting_float || 0)} />
          <div className="border-t border-dashed border-slate-300 my-2" />
          <Row label="EXPECTED CASH" value={formatPHP(c.expected_counter || 0)} bold />
          <Row label="ACTUAL CASH" value={formatPHP(c.actual_cash || 0)} bold />
          <Row label="OVER / SHORT" value={formatPHP(c.over_short || 0)}
               bold negative={(c.over_short || 0) < 0} />
        </Section>

        {/* Customer Credit */}
        {(c.total_credit_today || 0) > 0 && (
          <Section title="Customer Credit Generated Today">
            <Row label="Total Credit Outstanding" value={formatPHP(c.total_credit_today || 0)} />
            <Row label="Total Invoice Value" value={formatPHP(c.total_credit_invoice_value || 0)} sub muted />
            {(c.credit_sales_today || []).slice(0, 10).map((inv, i) => (
              <div key={i} className="flex items-start justify-between text-xs py-1.5 border-t border-slate-100">
                <div className="flex-1 min-w-0">
                  <p className="font-mono text-slate-700">{inv.invoice_number}</p>
                  <p className="text-slate-500 truncate">{inv.customer_name}</p>
                </div>
                <div className="text-right">
                  <p className="font-mono font-semibold text-amber-700">{formatPHP(inv.balance)}</p>
                  <p className="text-[10px] text-slate-400">{inv.status}</p>
                </div>
              </div>
            ))}
          </Section>
        )}

        {/* Expenses */}
        {(c.expenses || []).length > 0 && (
          <Section title="Expenses Today">
            {c.expenses.slice(0, 20).map((e, i) => (
              <div key={i} className="flex items-start justify-between text-xs py-1.5 border-t border-slate-100">
                <div className="flex-1 min-w-0">
                  <p className="text-slate-700 truncate">{e.description || e.category}</p>
                  {e.fund_source && <p className="text-[10px] text-slate-400">{e.fund_source}</p>}
                </div>
                <span className="font-mono font-semibold text-red-600">-{formatPHP(e.amount)}</span>
              </div>
            ))}
            <div className="border-t border-dashed border-slate-300 my-2" />
            <Row label="Total Expenses" value={formatPHP(c.total_expenses || 0)} bold negative />
          </Section>
        )}

        <p className="text-[10px] text-slate-400 text-center pt-2">
          Closed by {c.closed_by_name || c.finalized_by_name || '—'} · {c.closed_at_iso ? new Date(c.closed_at_iso).toLocaleString('en-PH') : ''}
        </p>
        <p className="text-[10px] text-slate-400 text-center">
          Link expires {data.expires_at ? new Date(data.expires_at).toLocaleDateString('en-PH') : 'soon'}.
        </p>
      </div>

      {/* Sticky download bar */}
      <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-slate-200 shadow-lg p-3 z-50">
        <div className="max-w-2xl mx-auto">
          <button
            onClick={downloadPdf}
            disabled={downloading}
            className="w-full bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 text-white font-bold py-3.5 rounded-xl flex items-center justify-center gap-2 disabled:opacity-50 transition-colors"
            data-testid="zr-share-download"
          >
            <Download size={18} />
            {downloading ? 'Preparing PDF…' : 'Download Detailed PDF'}
          </button>
          <p className="text-[10px] text-slate-400 text-center mt-1.5">
            File: <span className="font-mono">Z-Report {data.branch_name} {data.date}.pdf</span>
          </p>
        </div>
      </div>
    </div>
  );
}

function Tile({ label, value, accent }) {
  const colors = {
    emerald: 'bg-emerald-50 border-emerald-200 text-emerald-700',
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    violet: 'bg-violet-50 border-violet-200 text-violet-700',
    red: 'bg-red-50 border-red-200 text-red-700',
  };
  return (
    <div className={`rounded-xl border p-3 ${colors[accent] || colors.emerald}`}>
      <p className="text-[10px] uppercase tracking-wider font-bold opacity-70">{label}</p>
      <p className="font-mono text-base font-extrabold mt-0.5 leading-tight">{value}</p>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">{title}</h3>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({ label, value, sub, bold, negative, muted, highlight }) {
  return (
    <div className={`flex justify-between text-sm ${sub ? 'pl-4 text-xs' : ''} ${bold ? 'font-bold' : ''}`}>
      <span className={muted ? 'text-slate-400' : highlight ? 'text-indigo-700' : 'text-slate-600'}>{label}</span>
      <span className={`font-mono ${negative ? 'text-red-600' : highlight ? 'text-indigo-700 font-semibold' : muted ? 'text-slate-400' : 'text-slate-800'}`}>{value}</span>
    </div>
  );
}
