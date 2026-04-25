/**
 * SignaturePage — Public mobile signing page.
 * Customer opens via QR code, reviews credit summary, draws signature, submits.
 * No authentication required.
 */
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import SignaturePad from 'signature_pad';
import { CheckCircle, AlertCircle, RotateCcw } from 'lucide-react';
import { Button } from '../components/ui/button';

const API = process.env.REACT_APP_BACKEND_URL;

const formatPHP = (n) => `₱${parseFloat(n || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function SignaturePage() {
  const { token } = useParams();
  const canvasRef = useRef(null);
  const padRef = useRef(null);

  const [session, setSession] = useState(null);
  const [status, setStatus] = useState('loading'); // loading | pending | signed | expired | error
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const res = await axios.get(`${API}/api/signatures/view/${token}`);
        const data = res.data;
        if (data.status === 'expired') {
          setStatus('expired');
        } else if (data.status !== 'pending') {
          setStatus(data.status);
          setMessage(data.message || 'This request has already been completed.');
        } else {
          setSession(data);
          setStatus('pending');
        }
      } catch {
        setStatus('error');
      }
    };
    load();
  }, [token]);

  useEffect(() => {
    if (status === 'pending' && canvasRef.current && !padRef.current) {
      const canvas = canvasRef.current;
      // Set canvas size to match display size
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      canvas.width = canvas.offsetWidth * ratio;
      canvas.height = canvas.offsetHeight * ratio;
      canvas.getContext('2d').scale(ratio, ratio);
      padRef.current = new SignaturePad(canvas, {
        backgroundColor: 'rgb(255,255,255)',
        penColor: 'rgb(15, 23, 42)',
      });
    }
  }, [status]);

  const handleClear = () => {
    if (padRef.current) padRef.current.clear();
  };

  const handleSubmit = async () => {
    if (!padRef.current || padRef.current.isEmpty()) {
      setMessage('Please draw your signature before submitting.');
      return;
    }
    setSubmitting(true);
    setMessage('');
    try {
      const signatureData = padRef.current.toDataURL('image/png');
      await axios.post(`${API}/api/signatures/submit/${token}`, {
        signature: signatureData,
        signed_at: new Date().toISOString(),
        user_agent: navigator.userAgent,
      });
      setStatus('signed');
      setMessage('Signature submitted successfully!');
    } catch (err) {
      const detail = err.response?.data?.detail || 'Submission failed. Please try again.';
      setMessage(detail);
    } finally {
      setSubmitting(false);
    }
  };

  const ctx = session?.credit_context || {};

  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-slate-400 text-sm">Loading...</div>
      </div>
    );
  }

  if (status === 'signed') {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 bg-emerald-100 rounded-full flex items-center justify-center mb-4">
          <CheckCircle className="text-emerald-600" size={32} />
        </div>
        <h1 className="text-xl font-bold text-slate-800 mb-2">Signature Received</h1>
        <p className="text-slate-500 text-sm">
          Your signature has been recorded. Thank you!
        </p>
        <p className="text-slate-400 text-xs mt-4">You may close this page.</p>
      </div>
    );
  }

  if (status === 'bypassed') {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 bg-blue-100 rounded-full flex items-center justify-center mb-4">
          <CheckCircle className="text-blue-600" size={32} />
        </div>
        <h1 className="text-xl font-bold text-slate-800 mb-2">Authorization Complete</h1>
        <p className="text-slate-500 text-sm">{message || 'This transaction has been authorized by staff.'}</p>
      </div>
    );
  }

  if (status === 'expired') {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 bg-amber-100 rounded-full flex items-center justify-center mb-4">
          <AlertCircle className="text-amber-600" size={32} />
        </div>
        <h1 className="text-xl font-bold text-slate-800 mb-2">Link Expired</h1>
        <p className="text-slate-500 text-sm">This signing link has expired (5-minute limit).</p>
        <p className="text-slate-400 text-xs mt-2">Please ask staff to generate a new signing request.</p>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mb-4">
          <AlertCircle className="text-red-500" size={32} />
        </div>
        <h1 className="text-xl font-bold text-slate-800 mb-2">Not Found</h1>
        <p className="text-slate-500 text-sm">Signing request not found or already completed.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col" style={{ maxWidth: 480, margin: '0 auto' }}>
      {/* Header */}
      <div className="bg-slate-800 text-white px-5 py-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">AgriBooks — Credit Authorization</p>
        <h1 className="text-lg font-bold">Signature Required</h1>
      </div>

      {/* Credit Summary */}
      <div className="px-5 py-4 bg-white border-b border-slate-200 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">Customer</span>
          <span className="font-semibold text-slate-800">{ctx.customer_name || '—'}</span>
        </div>
        {ctx.invoice_number && (
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Invoice</span>
            <span className="font-mono text-xs text-slate-700">{ctx.invoice_number}</span>
          </div>
        )}
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">Type</span>
          <span className="font-medium text-slate-700 capitalize">
            {(ctx.credit_type || '').replace(/_/g, ' ') || 'Credit'}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">Date</span>
          <span className="text-slate-700">{ctx.date || new Date().toLocaleDateString()}</span>
        </div>
      </div>

      {/* Items Purchased */}
      {Array.isArray(ctx.items) && ctx.items.length > 0 && (
        <div className="px-5 py-3 bg-white border-b border-slate-200">
          <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-2 font-semibold">Items Purchased</p>
          <div className="divide-y divide-slate-100">
            {ctx.items.map((it, i) => (
              <div key={i} className="py-1.5 flex items-baseline justify-between gap-2 text-xs">
                <div className="flex-1 min-w-0">
                  <p className="text-slate-800 truncate">{it.product_name || it.name}</p>
                  <p className="text-[10px] text-slate-400">
                    {it.quantity} {it.unit || ''} × {formatPHP(it.rate || it.price || 0)}
                  </p>
                </div>
                <span className="font-mono text-slate-700 shrink-0">
                  {formatPHP((it.total != null) ? it.total : (parseFloat(it.quantity || 0) * parseFloat(it.rate || it.price || 0)))}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Totals */}
      <div className="px-5 py-3 bg-slate-100 border-b border-slate-200 space-y-1">
        {ctx.subtotal != null && (
          <div className="flex justify-between text-xs">
            <span className="text-slate-500">Subtotal</span>
            <span className="font-mono text-slate-700">{formatPHP(ctx.subtotal)}</span>
          </div>
        )}
        {ctx.discount > 0 && (
          <div className="flex justify-between text-xs">
            <span className="text-slate-500">Discount</span>
            <span className="font-mono text-blue-600">-{formatPHP(ctx.discount)}</span>
          </div>
        )}
        {ctx.partial_paid > 0 && (
          <div className="flex justify-between text-xs">
            <span className="text-slate-500">Paid Now</span>
            <span className="font-mono text-emerald-600">-{formatPHP(ctx.partial_paid)}</span>
          </div>
        )}
        <div className="flex justify-between items-baseline pt-1 border-t border-slate-200">
          <span className="text-sm font-bold text-slate-700">Total Credit Amount</span>
          <span className="font-bold text-lg text-slate-900">{formatPHP(ctx.amount)}</span>
        </div>
        {ctx.description && (
          <p className="text-[10px] text-slate-500 italic mt-1">{ctx.description}</p>
        )}
      </div>

      {/* Instructions */}
      <div className="px-5 pt-4 pb-2">
        <p className="text-xs text-slate-500 mb-2">
          By signing below, you authorize the above credit transaction and agree to the terms.
        </p>
        <div className="flex items-center justify-between mb-1">
          <p className="text-xs font-medium text-slate-700">Draw your signature:</p>
          <button
            onClick={handleClear}
            className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-1"
          >
            <RotateCcw size={12} /> Clear
          </button>
        </div>
      </div>

      {/* Signature Canvas */}
      <div className="px-5 pb-3">
        <div className="border-2 border-dashed border-slate-300 rounded-xl overflow-hidden bg-white"
          style={{ height: 180 }}>
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: '100%', display: 'block', touchAction: 'none' }}
          />
        </div>
        <div className="flex items-center justify-center mt-1">
          <div className="h-px bg-slate-300 w-1/3" />
          <p className="text-[10px] text-slate-400 mx-2">SIGNATURE</p>
          <div className="h-px bg-slate-300 w-1/3" />
        </div>
      </div>

      {/* Timestamp */}
      <div className="px-5 pb-2">
        <p className="text-[11px] text-slate-400">
          Signed on: {new Date().toLocaleString('en-PH', { dateStyle: 'long', timeStyle: 'short' })}
        </p>
      </div>

      {/* Error message */}
      {message && (
        <div className="mx-5 mb-2 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700">
          {message}
        </div>
      )}

      {/* Submit */}
      <div className="px-5 pb-6 mt-auto">
        <Button
          data-testid="submit-signature-btn"
          className="w-full h-12 bg-slate-800 hover:bg-slate-700 text-white font-semibold rounded-xl"
          onClick={handleSubmit}
          disabled={submitting}
        >
          {submitting ? 'Submitting...' : 'Approve & Submit'}
        </Button>
        <p className="text-[10px] text-slate-400 text-center mt-2">
          This signature is legally binding and will be stored securely.
        </p>
      </div>
    </div>
  );
}
