/**
 * RequestSignatureDialog — Web POS signature flow.
 *
 * Cashier creates a credit sale, then this dialog opens:
 *   1. POST /api/signatures/session with credit_context (items, totals, etc.)
 *   2. Show QR code with /sign/{token} URL — customer scans with phone
 *   3. Poll /api/signatures/status/{token} every 2s
 *   4. On signed: show captured signature image + Print Receipt button
 *   5. Manager-PIN bypass available when customer can't sign
 */

import { useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import SignaturePad from 'signature_pad';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { ScrollArea } from './ui/scroll-area';
import { CheckCircle2, AlertTriangle, Smartphone, ShieldAlert, RefreshCw, Printer, Clock, X, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

const POLL_MS = 2000;
const BACKEND = process.env.REACT_APP_BACKEND_URL || '';

export default function RequestSignatureDialog({
  open,
  onOpenChange,
  invoice,            // see UnifiedSalesPage for fields
  onSigned,
  onPrintReceipt,
  onConfirmSale,      // pre-invoice: called with sig data when user clicks Submit Sale
  preInvoice = false, // when true, dialog is shown BEFORE invoice creation; "Print" becomes "Submit Sale"
  mode = 'qr',        // 'qr' (web POS) | 'inline' (terminal)
  apiInstance = null, // optional: pass terminal's own axios instance to override AuthContext api
}) {
  const [sessionToken, setSessionToken] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [signingUrl, setSigningUrl] = useState('');
  const [status, setStatus] = useState('creating'); // creating | pending | signed | bypassed | expired | error
  const [signatureUrl, setSignatureUrl] = useState('');
  const [bypassMethod, setBypassMethod] = useState('');
  const [showBypass, setShowBypass] = useState(false);
  const [bypassPin, setBypassPin] = useState('');
  const [bypassReason, setBypassReason] = useState('');
  const [bypassErr, setBypassErr] = useState('');
  const [bypassing, setBypassing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [lastStatus, setLastStatus] = useState(null);
  const pollRef = useRef(null);
  const canvasRef = useRef(null);
  const padRef = useRef(null);

  // Use injected api instance (terminal) or fall back to AuthContext api (web POS)
  const http = apiInstance || api;

  // ── Build absolute signing URL ──
  const buildAbsoluteUrl = (relPath) => {
    try { return new URL(relPath, window.location.origin).href; } catch { return relPath; }
  };

  // ── Create session on open ──
  useEffect(() => {
    if (!open || !invoice) return;
    let cancelled = false;
    setStatus('creating');
    setSignatureUrl('');
    setBypassMethod('');
    setShowBypass(false);
    setBypassPin('');
    setBypassReason('');
    setBypassErr('');
    setSessionToken('');

    const credit_type = invoice.credit_type || (invoice.partial_paid > 0 ? 'partial' : 'by_term');
    const description = invoice.description || `${credit_type === 'charged_to_crop' ? 'Charged-to-Crop credit' : 'Term credit'}${invoice.partial_paid > 0 ? ' (partial payment)' : ''}`;

    const payload = {
      linked_record_type: 'invoice',
      linked_record_id: invoice.id || '',
      branch_id: invoice.branch_id || '',
      credit_context: {
        customer_name: invoice.customer_name || 'Walk-in',
        amount: parseFloat(invoice.balance || 0),
        credit_type,
        date: invoice.order_date || new Date().toISOString().slice(0, 10),
        branch_name: invoice.branch_name || '',
        description,
        invoice_number: invoice.invoice_number || '',
        items: (invoice.items || []).map(it => ({
          product_name: it.product_name || it.name || '',
          quantity: parseFloat(it.quantity || 0),
          unit: it.unit || 'unit',
          rate: parseFloat(it.rate || it.price || 0),
          total: parseFloat(it.total != null ? it.total : (parseFloat(it.quantity || 0) * parseFloat(it.rate || it.price || 0))),
        })),
        subtotal: invoice.subtotal,
        discount: invoice.discount,
        partial_paid: invoice.partial_paid,
      },
    };

    http.post('/signatures/session', payload)
      .then(r => {
        if (cancelled) return;
        setSessionToken(r.data.token);
        setSessionId(r.data.id);
        setSigningUrl(buildAbsoluteUrl(r.data.signing_url));
        setStatus('pending');
      })
      .catch(e => {
        if (!cancelled) {
          setStatus('error');
          toast.error(e.response?.data?.detail || 'Could not create signing session');
        }
      });
    return () => { cancelled = true; };
  }, [open, invoice]);

  // ── Poll for status while pending (qr mode only — inline submits directly) ──
  useEffect(() => {
    if (!open || !sessionToken || status !== 'pending' || mode !== 'qr') return;
    let stopped = false;
    const tick = async () => {
      try {
        const r = await http.get(`/signatures/status/${sessionToken}`);
        if (stopped) return;
        setLastStatus(r.data);
        if (r.data.expired && r.data.status === 'pending') {
          setStatus('expired');
          return;
        }
        if (r.data.status === 'signed') {
          setSignatureUrl(r.data.signature_url || '');
          setStatus('signed');
          onSigned?.(r.data);
        } else if (r.data.status === 'bypassed') {
          setBypassMethod(r.data.bypass_method || 'pin');
          setStatus('bypassed');
          onSigned?.(r.data);
        }
      } catch { /* ignore network blip */ }
    };
    pollRef.current = setInterval(tick, POLL_MS);
    return () => { stopped = true; if (pollRef.current) clearInterval(pollRef.current); };
  }, [open, sessionToken, status, onSigned, mode]);

  // ── Inline signature pad (terminal mode) ──
  useEffect(() => {
    if (mode !== 'inline' || status !== 'pending' || !canvasRef.current) return;
    const canvas = canvasRef.current;
    // Guard: canvas must have rendered dimensions (Android WebView can report 0 briefly)
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    if (!w || !h) return;
    // Cap devicePixelRatio to 2 — higher values cause OOM on low-memory Android devices
    const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2);
    canvas.width = w * ratio;
    canvas.height = h * ratio;
    const ctx = canvas.getContext('2d');
    if (!ctx) return; // Safety: getContext can return null on some Android WebViews
    ctx.scale(ratio, ratio);
    padRef.current = new SignaturePad(canvas, {
      backgroundColor: 'rgb(255, 255, 255)',
      penColor: 'rgb(0, 0, 0)',
      minWidth: 0.6,
      maxWidth: 2.0,
      throttle: 16,
    });
    return () => { try { padRef.current?.off?.(); } catch { /* noop */ } padRef.current = null; };
  }, [mode, status]);

  const clearPad = () => { padRef.current?.clear(); };

  const submitInlineSignature = async () => {
    if (!padRef.current || padRef.current.isEmpty()) {
      toast.error('Please sign before submitting');
      return;
    }
    setSubmitting(true);
    try {
      const dataUrl = padRef.current.toDataURL('image/png');
      // Submit via public endpoint (token-based)
      await fetch(`${BACKEND}/api/signatures/submit/${sessionToken}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          signature: dataUrl,
          signed_at: new Date().toISOString(),
          user_agent: navigator.userAgent,
        }),
      }).then(r => { if (!r.ok) throw new Error('submit failed'); return r.json(); });
      // Now fetch status to get presigned signature URL
      const status_r = await http.get(`/signatures/status/${sessionToken}`);
      setLastStatus(status_r.data);
      setSignatureUrl(status_r.data.signature_url || dataUrl);
      setStatus('signed');
      onSigned?.(status_r.data);
      toast.success('Signature captured');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to submit signature');
    } finally {
      setSubmitting(false);
    }
  };

  // ── Manager-PIN bypass ──
  const handleBypass = async () => {
    if (!bypassPin) { setBypassErr('Manager PIN is required'); return; }
    setBypassing(true);
    setBypassErr('');
    try {
      await http.post(`/signatures/bypass/${sessionId}`, {
        pin: bypassPin, reason: bypassReason || 'Customer unable to sign',
      });
      toast.success('Authorized via Manager PIN');
      // Status poll will catch it; force-set for snappy UX
      setBypassMethod('pin');
      setStatus('bypassed');
      // Fetch status to get verification_token
      try {
        const status_r = await http.get(`/signatures/status/${sessionToken}`);
        setLastStatus(status_r.data);
        onSigned?.(status_r.data);
      } catch {
        onSigned?.({ status: 'bypassed', bypass_method: 'pin' });
      }
    } catch (e) {
      setBypassErr(e.response?.data?.detail || 'Bypass failed');
    } finally {
      setBypassing(false);
    }
  };

  // ── Print + close (or Submit Sale in preInvoice mode) ──
  const handlePrintAndClose = () => {
    const sigPayload = {
      signature_url: signatureUrl,
      bypass_method: bypassMethod,
      signed_at: lastStatus?.signed_at || lastStatus?.bypassed_at || new Date().toISOString(),
      verification_token: lastStatus?.verification_token || '',
      session_id: sessionId,
    };
    if (preInvoice) {
      // Don't print — invoice doesn't exist yet. Caller will create it now.
      onConfirmSale?.(sigPayload);
    } else {
      onPrintReceipt?.(sigPayload);
    }
    onOpenChange(false);
  };

  const isDone = status === 'signed' || status === 'bypassed';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="request-signature-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base" style={{ fontFamily: 'Manrope' }}>
            {isDone
              ? <CheckCircle2 size={18} className="text-emerald-600" />
              : <Smartphone size={18} className="text-[#1A4D2E]" />}
            {isDone ? 'Customer Authorized' : 'Customer Signature Required'}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {invoice?.invoice_number} · {invoice?.customer_name} · {formatPHP(invoice?.balance || 0)}
          </DialogDescription>
        </DialogHeader>

        {status === 'creating' && (
          <div className="py-10 text-center text-sm text-slate-400 flex items-center justify-center gap-2">
            <RefreshCw size={14} className="animate-spin" /> Creating signing session...
          </div>
        )}

        {status === 'pending' && mode === 'qr' && (
          <div className="space-y-4">
            <div className="flex flex-col items-center bg-white border-2 border-slate-200 rounded-xl p-4">
              <p className="text-[11px] text-slate-500 mb-2 text-center">
                Ask the customer to scan this QR code with their phone camera
              </p>
              <div className="bg-white p-2 rounded-lg" data-testid="signature-qr-code">
                <QRCodeSVG value={signingUrl} size={180} level="M" includeMargin={false} />
              </div>
              <p className="text-[10px] text-slate-400 mt-2 break-all px-4 text-center">
                or visit: <span className="font-mono">{signingUrl}</span>
              </p>
              <div className="flex items-center gap-1 text-[10px] text-amber-600 mt-2">
                <Clock size={10} /> Link valid for 5 minutes
              </div>
            </div>

            {!showBypass ? (
              <button
                onClick={() => setShowBypass(true)}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-3 rounded-lg border-2 border-amber-400 bg-amber-50 hover:bg-amber-100 text-amber-800 text-sm font-semibold transition-colors shadow-sm"
                data-testid="show-bypass-btn"
              >
                <ShieldAlert size={15} /> Customer can't sign? Skip with Manager PIN
              </button>
            ) : (
              <BypassPanel
                bypassPin={bypassPin} setBypassPin={setBypassPin}
                bypassReason={bypassReason} setBypassReason={setBypassReason}
                bypassErr={bypassErr} setBypassErr={setBypassErr}
                bypassing={bypassing} onBypass={handleBypass}
                onCancel={() => setShowBypass(false)}
              />
            )}

            <Button variant="ghost" size="sm" className="w-full text-xs gap-1" onClick={() => onOpenChange(false)} data-testid="sig-cancel-btn">
              <X size={12} /> {preInvoice ? 'Cancel sale' : 'Cancel — sale will proceed without signature'}
            </Button>
          </div>
        )}

        {/* ── Inline (Terminal) mode: review items + sign on the same screen ── */}
        {status === 'pending' && mode === 'inline' && (
          <div className="space-y-3">
            <ScrollArea className="max-h-[26vh] border border-slate-200 rounded-lg p-2 bg-slate-50">
              <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1 font-semibold">Items</p>
              {(invoice?.items || []).map((it, i) => (
                <div key={i} className="py-1 flex items-baseline justify-between gap-2 text-xs border-b border-slate-100 last:border-0">
                  <div className="flex-1 min-w-0">
                    <p className="text-slate-800 truncate">{it.product_name || it.name}</p>
                    <p className="text-[10px] text-slate-400">
                      {it.quantity} {it.unit || ''} × {formatPHP(it.rate || it.price || 0)}
                    </p>
                  </div>
                  <span className="font-mono text-slate-700 shrink-0">
                    {formatPHP(it.total != null ? it.total : (parseFloat(it.quantity || 0) * parseFloat(it.rate || it.price || 0)))}
                  </span>
                </div>
              ))}
            </ScrollArea>
            <div className="flex justify-between items-baseline px-2">
              <span className="text-xs font-semibold text-slate-700">Credit Amount</span>
              <span className="font-bold text-base text-red-600">{formatPHP(invoice?.balance || 0)}</span>
            </div>

            <div className="bg-white border-2 border-slate-300 rounded-lg overflow-hidden" data-testid="terminal-sig-pad-wrapper">
              <p className="text-[10px] text-slate-500 px-2 pt-1.5 text-center">Sign below — customer authorization required</p>
              <canvas ref={canvasRef} className="w-full h-32 touch-none cursor-crosshair" data-testid="terminal-sig-pad" />
              <button onClick={clearPad} className="w-full text-[10px] text-slate-500 hover:text-slate-700 py-1 border-t border-slate-200 flex items-center justify-center gap-1">
                <RotateCcw size={10} /> Clear & redraw
              </button>
            </div>

            <div className="flex gap-2">
              {!showBypass && (
                <Button
                  variant="outline"
                  size="sm"
                  className="text-sm border-2 border-amber-400 bg-amber-50 hover:bg-amber-100 text-amber-800 font-semibold h-10"
                  onClick={() => setShowBypass(true)}
                  data-testid="show-bypass-btn"
                >
                  <ShieldAlert size={14} className="mr-1" /> Skip (PIN)
                </Button>
              )}
              <Button
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white h-10"
                onClick={submitInlineSignature}
                disabled={submitting}
                data-testid="terminal-sig-submit-btn">
                {submitting ? 'Saving...' : 'Submit Signature'}
              </Button>
            </div>

            {showBypass && (
              <BypassPanel
                bypassPin={bypassPin} setBypassPin={setBypassPin}
                bypassReason={bypassReason} setBypassReason={setBypassReason}
                bypassErr={bypassErr} setBypassErr={setBypassErr}
                bypassing={bypassing} onBypass={handleBypass}
                onCancel={() => setShowBypass(false)}
              />
            )}
          </div>
        )}

        {status === 'signed' && (
          <div className="space-y-3" data-testid="signature-captured-view">
            <div className="bg-emerald-50 border-2 border-emerald-200 rounded-xl p-4 text-center">
              <CheckCircle2 size={32} className="text-emerald-600 mx-auto mb-2" />
              <p className="text-sm font-semibold text-emerald-800">Customer Signed ✓</p>
              <p className="text-[11px] text-emerald-700">Signature captured and stored.</p>
            </div>

            {signatureUrl && (
              <div className="border border-slate-200 rounded-lg p-2 bg-white">
                <p className="text-[10px] text-slate-400 mb-1 text-center">CAPTURED SIGNATURE</p>
                <img src={signatureUrl} alt="Customer signature" className="w-full h-24 object-contain" data-testid="captured-signature-img" />
              </div>
            )}

            <Button className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white h-10 gap-2" onClick={handlePrintAndClose} data-testid="print-after-sign-btn">
              {preInvoice ? (
                <span className="inline-flex items-center gap-2"><CheckCircle2 size={14} /> Submit Sale</span>
              ) : (
                <span className="inline-flex items-center gap-2"><Printer size={14} /> Print Receipt</span>
              )}
            </Button>
            <Button variant="ghost" size="sm" className="w-full text-xs" onClick={() => onOpenChange(false)} data-testid="sig-close-skip-btn">
              {preInvoice ? 'Cancel sale' : 'Close (skip print)'}
            </Button>
          </div>
        )}

        {status === 'bypassed' && (
          <div className="space-y-3" data-testid="signature-bypassed-view">
            <div className="bg-amber-50 border-2 border-amber-200 rounded-xl p-4 text-center">
              <ShieldAlert size={32} className="text-amber-600 mx-auto mb-2" />
              <p className="text-sm font-semibold text-amber-800">Authorized via Manager PIN</p>
              <p className="text-[11px] text-amber-700">No signature captured. Manager bypass recorded for audit.</p>
            </div>
            <Button className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white h-10 gap-2" onClick={handlePrintAndClose}>
              {preInvoice ? (
                <span className="inline-flex items-center gap-2"><CheckCircle2 size={14} /> Submit Sale</span>
              ) : (
                <span className="inline-flex items-center gap-2"><Printer size={14} /> Print Receipt</span>
              )}
            </Button>
            <Button variant="ghost" size="sm" className="w-full text-xs" onClick={() => onOpenChange(false)}>
              {preInvoice ? 'Cancel sale' : 'Close (skip print)'}
            </Button>
          </div>
        )}

        {status === 'expired' && (
          <div className="space-y-3 text-center py-4">
            <Clock size={32} className="text-amber-500 mx-auto" />
            <p className="text-sm font-semibold text-slate-700">Signing link expired</p>
            <p className="text-xs text-slate-500">The 5-minute link has expired. Generate a new one or use Manager bypass.</p>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </div>
        )}

        {status === 'error' && (
          <div className="space-y-3 text-center py-4">
            <AlertTriangle size={32} className="text-red-500 mx-auto" />
            <p className="text-sm font-semibold text-slate-700">Could not start signing</p>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Reusable bypass UI (shared between qr and inline modes) ──
function BypassPanel({ bypassPin, setBypassPin, bypassReason, setBypassReason, bypassErr, setBypassErr, bypassing, onBypass, onCancel }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
      <p className="text-xs font-semibold text-amber-700 flex items-center gap-1">
        <ShieldAlert size={12} /> Manager PIN Bypass
      </p>
      <Input
        type="password"
        inputMode="numeric"
        value={bypassPin}
        onChange={(e) => { setBypassPin(e.target.value); setBypassErr(''); }}
        placeholder="Enter Manager PIN"
        className="h-9 text-center font-mono"
        data-testid="bypass-pin-input"
      />
      <Input
        value={bypassReason}
        onChange={(e) => setBypassReason(e.target.value)}
        placeholder="Reason (e.g. Customer rushed off)"
        className="h-9 text-xs"
        data-testid="bypass-reason-input"
      />
      {bypassErr && <p className="text-[11px] text-red-600 flex items-center gap-1"><AlertTriangle size={10} /> {bypassErr}</p>}
      <div className="flex gap-2">
        <Button variant="outline" size="sm" className="flex-1 text-xs" onClick={onCancel} disabled={bypassing}>Cancel</Button>
        <Button size="sm" className="flex-1 text-xs bg-amber-600 hover:bg-amber-700 text-white" onClick={onBypass} disabled={bypassing || !bypassPin} data-testid="confirm-bypass-btn">
          {bypassing ? 'Verifying...' : 'Authorize Bypass'}
        </Button>
      </div>
    </div>
  );
}
