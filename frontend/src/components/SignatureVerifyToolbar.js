import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { ShieldCheck, Search, CheckCircle2, AlertTriangle, ExternalLink, RefreshCw, XCircle, PenLine } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '../contexts/AuthContext';
import { formatDateTime } from '../lib/dateFormat';

/**
 * SignatureVerifyToolbar
 * Compact bar that accepts a printed v.XXXXXXXX token and resolves it
 * to the original digital signature + invoice metadata.
 *
 * Props:
 *   onOpenInvoice(invoiceNumber) — callback to open InvoiceDetailModal
 */
export default function SignatureVerifyToolbar({ onOpenInvoice }) {
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);   // verified session data
  const [error, setError] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const clean = (v) => v.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);

  const handleVerify = async () => {
    const tok = clean(token);
    if (tok.length !== 8) { toast.error('Token must be 8 characters (e.g. v.A1B2C3D4)'); return; }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.get(`/signatures/verify/${tok}`);
      setResult(res.data);
      setDialogOpen(true);
    } catch (e) {
      const msg = e.response?.data?.detail || 'Token not found';
      setError(msg);
      toast.error(msg);
    }
    setLoading(false);
  };

  const handleClose = () => {
    setDialogOpen(false);
    setToken('');
    setResult(null);
    setError(null);
  };

  const formatDate = (iso) => {
    if (!iso) return '—';
    return formatDateTime(iso);
  };

  return (
    <>
      {/* ── Compact Toolbar Bar ─────────────────────────────────────────── */}
      <div className="flex items-center gap-2 p-3 bg-slate-800/40 border border-slate-700/50 rounded-lg">
        <ShieldCheck size={15} className="text-slate-400 shrink-0" />
        <span className="text-xs text-slate-400 shrink-0 hidden sm:block">Verify Receipt Signature:</span>
        <div className="flex items-center gap-2 flex-1 max-w-xs">
          <div className="relative flex-1">
            <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-slate-500 font-mono pointer-events-none">v.</span>
            <Input
              data-testid="sig-verify-token-input"
              value={token.replace(/^v\./i, '')}
              onChange={e => { setToken(clean(e.target.value)); setError(null); }}
              onKeyDown={e => e.key === 'Enter' && handleVerify()}
              placeholder="XXXXXXXX"
              className="pl-7 h-7 text-xs font-mono bg-slate-900/50 border-slate-600 text-white placeholder:text-slate-600 w-36"
              maxLength={8}
            />
          </div>
          <Button
            data-testid="sig-verify-btn"
            size="sm"
            onClick={handleVerify}
            disabled={loading || clean(token).length !== 8}
            className="h-7 text-xs bg-slate-700 hover:bg-slate-600 text-white border-0 px-3"
          >
            {loading
              ? <RefreshCw size={12} className="animate-spin" />
              : <><Search size={12} className="mr-1" />Verify</>}
          </Button>
        </div>
        {error && (
          <span className="text-[10px] text-red-400 flex items-center gap-1">
            <XCircle size={11} /> {error}
          </span>
        )}
        <span className="text-[10px] text-slate-600 hidden md:block">
          Paste token from physical receipt to verify authenticity
        </span>
      </div>

      {/* ── Result Dialog ───────────────────────────────────────────────── */}
      <Dialog open={dialogOpen} onOpenChange={handleClose}>
        <DialogContent className="max-w-lg" data-testid="sig-verify-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-slate-800">
              <ShieldCheck size={18} className="text-emerald-600" />
              Signature Verification
            </DialogTitle>
          </DialogHeader>

          {result && (
            <div className="space-y-4">
              {/* ── Status Banner ────────────────────────────────────── */}
              <div className={`flex items-center gap-3 p-3 rounded-lg border ${
                result.status === 'signed'
                  ? 'bg-emerald-50 border-emerald-200'
                  : 'bg-amber-50 border-amber-200'
              }`}>
                {result.status === 'signed'
                  ? <CheckCircle2 size={24} className="text-emerald-600 shrink-0" />
                  : <AlertTriangle size={24} className="text-amber-600 shrink-0" />}
                <div>
                  <p className={`font-semibold text-sm ${result.status === 'signed' ? 'text-emerald-800' : 'text-amber-800'}`}>
                    {result.status === 'signed' ? 'Authentic — Customer Signed' : 'Authorized via Manager PIN'}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Token <span className="font-mono font-bold text-slate-700">v.{result.verification_token}</span> — verified ✓
                  </p>
                </div>
              </div>

              {/* ── Signature Image ──────────────────────────────────── */}
              {result.signature_url ? (
                <div className="border border-slate-200 rounded-lg p-3 bg-white">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-2 flex items-center gap-1.5">
                    <PenLine size={11} /> Customer Signature
                  </p>
                  <div className="bg-slate-50 rounded flex items-center justify-center p-2 min-h-[80px]">
                    <img
                      src={result.signature_url}
                      alt="Customer signature"
                      className="max-h-32 max-w-full object-contain"
                      data-testid="sig-verify-image"
                    />
                  </div>
                </div>
              ) : result.status === 'bypassed' ? (
                <div className="border border-amber-200 rounded-lg p-3 bg-amber-50/30">
                  <p className="text-xs text-amber-700 flex items-center gap-1.5">
                    <AlertTriangle size={13} />
                    Authorized via Manager PIN — no signature image
                  </p>
                  {result.bypass_by_name && (
                    <p className="text-xs text-slate-600 mt-1">Authorized by: <span className="font-semibold">{result.bypass_by_name}</span></p>
                  )}
                </div>
              ) : null}

              {/* ── Details Grid ────────────────────────────────────── */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="space-y-2.5">
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Signer</p>
                    <p className="text-sm font-medium text-slate-800 mt-0.5">
                      {result.signer_name || <span className="text-slate-400 italic">Not recorded</span>}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Date &amp; Time</p>
                    <p className="text-sm text-slate-800 mt-0.5">
                      {formatDate(result.signed_at || result.bypassed_at)}
                    </p>
                  </div>
                </div>
                <div className="space-y-2.5">
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Linked Invoice</p>
                    {result.linked_record_id ? (
                      <button
                        data-testid="sig-verify-open-invoice"
                        onClick={() => {
                          handleClose();
                          if (onOpenInvoice) onOpenInvoice(result.linked_record_id);
                        }}
                        className="text-sm text-[#1A4D2E] font-semibold hover:underline flex items-center gap-1 mt-0.5"
                      >
                        {result.linked_record_id}
                        <ExternalLink size={11} />
                      </button>
                    ) : (
                      <p className="text-sm text-slate-400 italic mt-0.5">Not linked</p>
                    )}
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Status</p>
                    <Badge className={`mt-0.5 text-[10px] ${
                      result.status === 'signed'
                        ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                        : 'bg-amber-100 text-amber-700 border-amber-200'
                    }`}>
                      {result.status === 'signed' ? 'Signed by Customer' : 'Manager PIN Bypass'}
                    </Badge>
                  </div>
                </div>
              </div>

              {/* ── Credit Context ───────────────────────────────────── */}
              {result.credit_context && (
                <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Credit Context</p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-600">
                    {result.credit_context.customer_name && (
                      <span><span className="text-slate-400">Customer:</span> {result.credit_context.customer_name}</span>
                    )}
                    {result.credit_context.credit_amount != null && (
                      <span><span className="text-slate-400">Amount:</span> ₱{parseFloat(result.credit_context.credit_amount).toLocaleString('en-PH', { minimumFractionDigits: 2 })}</span>
                    )}
                    {result.credit_context.due_date && (
                      <span><span className="text-slate-400">Due:</span> {result.credit_context.due_date}</span>
                    )}
                    {result.credit_context.credit_type && (
                      <span><span className="text-slate-400">Type:</span> {result.credit_context.credit_type}</span>
                    )}
                  </div>
                </div>
              )}

              {/* ── Tamper-Evident Footer ────────────────────────────── */}
              <div className="flex items-center justify-between pt-1 border-t border-slate-100">
                <p className="text-[10px] text-slate-400 font-mono">
                  Session: {result.session_id?.slice(0, 16)}…
                </p>
                <Badge variant="outline" className="text-[10px] border-emerald-300 text-emerald-700 font-mono">
                  <ShieldCheck size={10} className="mr-1" /> v.{result.verification_token}
                </Badge>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
