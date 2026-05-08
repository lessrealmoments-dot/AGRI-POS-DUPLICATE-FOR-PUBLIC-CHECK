import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import {
  Lock, FileText, Building2, ArrowRight, CreditCard, CheckCircle2,
  AlertTriangle, Printer, Image, Smartphone, Package, ChevronDown,
  ShieldCheck, RefreshCw, Search, Boxes, Banknote, Wifi, Camera, X, MapPin, ArrowLeft, RotateCcw, FileEdit
} from 'lucide-react';
import { formatDateTime } from '../lib/dateFormat';
import axios from 'axios';
import { toast } from 'sonner';
import PrintEngine from '../lib/PrintEngine';
import PrintBridge from '../lib/PrintBridge';
import TerminalReturnRefundModal from '../components/TerminalReturnRefundModal';
import TerminalUpdateReceiptModal from '../components/TerminalUpdateReceiptModal';
import CalcInput from '../components/CalcInput';

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';
const php = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtDateTime = (d) => { try { return formatDateTime(d); } catch { return d || ''; } };

// ── Shared: parse lockout error from 429 or 403 response ────────────────────
function parsePinError(e) {
  const detail = e.response?.data?.detail;
  if (e.response?.status === 429 && detail?.locked) {
    return { locked: true, retryAfter: detail.retry_after || 900, message: detail.message };
  }
  if (detail && typeof detail === 'object') {
    return { locked: false, message: detail.message || 'Invalid PIN', attemptsRemaining: detail.attempts_remaining, warn: detail.warn };
  }
  return { locked: false, message: typeof detail === 'string' ? detail : 'Invalid PIN' };
}

// Safely extract a string message from any API error response
function extractDetail(e, fallback = 'Something went wrong') {
  const d = e?.response?.data?.detail;
  if (!d) return fallback;
  if (typeof d === 'string') return d;
  if (typeof d === 'object') return d.message || fallback;
  return fallback;
}

// ── Lockout countdown display ─────────────────────────────────────────────────
function LockoutBanner({ retryAfter, onExpired }) {
  const [secs, setSecs] = React.useState(retryAfter);
  React.useEffect(() => {
    if (secs <= 0) { onExpired?.(); return; }
    const t = setTimeout(() => setSecs(s => s - 1), 1000);
    return () => clearTimeout(t);
  }, [secs, onExpired]);
  const m = Math.floor(secs / 60), s = secs % 60;
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-4 space-y-1 text-center" data-testid="lockout-banner">
      <div className="flex items-center justify-center gap-2 text-red-700 font-semibold text-sm">
        <Lock size={15} className="text-red-600" /> Document Temporarily Locked
      </div>
      <p className="text-xs text-red-500">Too many failed PIN attempts. Unlocks in</p>
      <p className="text-2xl font-bold font-mono text-red-700">{String(m).padStart(2,'0')}:{String(s).padStart(2,'0')}</p>
      <p className="text-[10px] text-red-400">All admins have been notified.</p>
    </div>
  );
}

// ── Draft Adjust Panel — QR-based quantity editing for FOR PREPARATION orders ──
function DraftAdjustPanel({ basic, docCode, onUpdated }) {
  const php = v => `₱${(parseFloat(v)||0).toLocaleString('en-PH',{minimumFractionDigits:2})}`;
  const [stage, setStage] = React.useState(basic.qr_edit_disabled ? 'disabled' : 'idle');
  const [items, setItems] = React.useState(
    (basic.items || []).map(item => ({ ...item, actual_qty: item.qty }))
  );
  const [pin, setPin] = React.useState('');
  const [pinError, setPinError] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [lockout, setLockout] = React.useState(null);
  const [result, setResult] = React.useState(null);
  const pinRef = React.useRef(null);

  const changedItems = items.filter(it => it.actual_qty !== it.qty);
  const newTotal = items.reduce((s, it) => s + (it.actual_qty * (parseFloat(it.price)||0)), 0);

  const handleSave = async () => {
    if (!pin.trim()) { setPinError('PIN is required'); return; }
    setSaving(true); setPinError('');
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/update_draft`, {
        pin,
        items: items.map(it => ({ product_id: it.product_id, actual_qty: it.actual_qty })),
      });
      setResult(res.data);
      setStage('success');
      if (onUpdated) onUpdated(res.data);
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (e.response?.status === 429 && detail?.locked) {
        setLockout({ retryAfter: detail.retry_after });
        setStage('pin');
        setPinError('');
        return;
      }
      if (detail?.permanently_disabled) {
        setStage('disabled');
        return;
      }
      const msg = typeof detail === 'object' ? detail.message : (detail || 'PIN verification failed');
      const rem = detail?.attempts_remaining;
      setPinError(rem !== undefined ? `${msg} — ${rem} attempt${rem!==1?'s':''} remaining` : msg);
    } finally {
      setSaving(false);
      setPin('');
    }
  };

  if (stage === 'disabled') return (
    <div className="rounded-xl border-2 border-red-200 bg-red-50 p-5 space-y-2 text-center">
      <AlertTriangle size={28} className="mx-auto text-red-400" />
      <p className="font-semibold text-red-700 text-sm">QR Editing Disabled</p>
      <p className="text-xs text-red-500 leading-relaxed">
        This draft order's QR editing has been permanently disabled due to too many failed PIN attempts.
        Please edit this order through the web portal.
      </p>
    </div>
  );

  if (stage === 'idle') return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 overflow-hidden">
      <div className="px-5 py-3 bg-amber-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileEdit size={15} className="text-amber-700" />
          <span className="font-semibold text-amber-800 text-sm">For Preparation</span>
        </div>
        <span className="text-xs text-amber-600 italic">Not yet paid</span>
      </div>
      <div className="px-5 py-4 space-y-3">
        <p className="text-xs text-amber-700 leading-relaxed">
          This order is saved for preparation. Tap <strong>Adjust Quantities</strong> to update actual quantities before finalizing.
        </p>
        <button
          onClick={() => setStage('adjust')}
          className="w-full py-3 rounded-xl bg-amber-500 hover:bg-amber-600 active:scale-95 transition-all text-white font-semibold text-sm flex items-center justify-center gap-2"
          data-testid="adjust-qty-btn"
        >
          <FileEdit size={15} /> Adjust Quantities
        </button>
      </div>
    </div>
  );

  if (stage === 'adjust') return (
    <div className="rounded-xl border border-amber-200 bg-white overflow-hidden">
      <div className="px-5 py-3 bg-amber-100 flex items-center gap-2 border-b border-amber-200">
        <FileEdit size={15} className="text-amber-700" />
        <span className="font-semibold text-amber-800 text-sm">Adjust Quantities</span>
      </div>
      <div className="divide-y divide-slate-100">
        {items.map((item, i) => (
          <div key={i} className="px-5 py-3 flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-800 truncate">{item.name}</p>
              <p className="text-xs text-slate-400">Ordered: {item.qty} × {php(item.price)}</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-xs text-slate-400">Actual:</span>
              <input
                type="number"
                min="0"
                step="0.01"
                value={item.actual_qty}
                onChange={e => {
                  const copy = [...items];
                  copy[i] = { ...copy[i], actual_qty: parseFloat(e.target.value) || 0 };
                  setItems(copy);
                }}
                className="w-20 text-right text-sm font-semibold border border-slate-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-amber-400"
                data-testid={`actual-qty-${i}`}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="px-5 py-4 bg-slate-50 border-t border-slate-100 space-y-2">
        <div className="flex justify-between text-sm font-semibold">
          <span>Revised Total:</span>
          <span className="text-[#1A4D2E]">{php(newTotal)}</span>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setStage('idle')} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-600 text-sm font-medium hover:bg-slate-100 active:scale-95 transition-all">Cancel</button>
          <button onClick={() => setStage('preview')} className="flex-1 py-2.5 rounded-xl bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold active:scale-95 transition-all" data-testid="preview-changes-btn">
            Preview Changes
          </button>
        </div>
      </div>
    </div>
  );

  if (stage === 'preview') return (
    <div className="rounded-xl border border-amber-200 bg-white overflow-hidden">
      <div className="px-5 py-3 bg-amber-100 flex items-center gap-2 border-b border-amber-200">
        <CheckCircle2 size={15} className="text-amber-700" />
        <span className="font-semibold text-amber-800 text-sm">Review Changes</span>
      </div>
      <div className="divide-y divide-slate-100">
        {items.map((item, i) => {
          const changed = item.actual_qty !== item.qty;
          const diff = item.actual_qty - item.qty;
          return (
            <div key={i} className={`px-5 py-3 flex items-center justify-between ${changed ? 'bg-amber-50' : ''}`}>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-slate-800 truncate">{item.name}</p>
              </div>
              <div className="flex items-center gap-3 shrink-0 text-sm">
                <span className="text-slate-400">{item.qty}</span>
                <span className="text-slate-300">→</span>
                <span className={`font-bold ${changed ? (diff < 0 ? 'text-red-600' : 'text-emerald-600') : 'text-slate-500'}`}>{item.actual_qty}</span>
                {changed && <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${diff < 0 ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-600'}`}>{diff > 0 ? `+${diff}` : diff}</span>}
                {!changed && <span className="text-xs text-slate-300 italic">no change</span>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="px-5 py-4 bg-slate-50 border-t space-y-2">
        <div className="flex justify-between text-xs text-slate-500"><span>Original Total</span><span>{php(basic.grand_total)}</span></div>
        <div className="flex justify-between text-sm font-bold"><span>Revised Total</span><span className="text-[#1A4D2E]">{php(newTotal)}</span></div>
        {changedItems.length === 0 && <p className="text-xs text-slate-400 text-center italic">No quantities were changed.</p>}
        <div className="flex gap-2 pt-1">
          <button onClick={() => setStage('adjust')} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-600 text-sm font-medium hover:bg-slate-100 active:scale-95 transition-all">Back to Edit</button>
          <button onClick={() => { setStage('pin'); setTimeout(() => pinRef.current?.focus(), 100); }} className="flex-1 py-2.5 rounded-xl bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold active:scale-95 transition-all" data-testid="accept-changes-btn">
            Accept &amp; Save
          </button>
        </div>
      </div>
    </div>
  );

  if (stage === 'pin') return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
      <div className="px-5 py-3 bg-slate-50 flex items-center gap-2 border-b">
        <Lock size={15} className="text-slate-500" />
        <span className="font-semibold text-slate-700 text-sm">Confirm with PIN</span>
      </div>
      <div className="px-5 py-5 space-y-4">
        {lockout ? (
          <LockoutBanner retryAfter={lockout.retryAfter} onExpired={() => { setLockout(null); setPinError(''); }} />
        ) : (
          <>
            <p className="text-xs text-slate-500 text-center">Manager PIN · Admin PIN · or TOTP (6-digit)</p>
            <input
              ref={pinRef}
              type="password"
              inputMode="numeric"
              placeholder="Enter PIN"
              value={pin}
              onChange={e => { setPin(e.target.value); setPinError(''); }}
              onKeyDown={e => e.key === 'Enter' && !saving && handleSave()}
              className="w-full text-center text-xl font-bold tracking-[0.5em] border-2 border-slate-200 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400"
              data-testid="draft-pin-input"
            />
            {pinError && (
              <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                <AlertTriangle size={14} /> {pinError}
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={() => { setStage('preview'); setPin(''); setPinError(''); }} className="flex-1 py-2.5 rounded-xl border border-slate-300 text-slate-600 text-sm font-medium hover:bg-slate-100">Cancel</button>
              <button onClick={handleSave} disabled={saving || !pin.trim()} className="flex-1 py-2.5 rounded-xl bg-[#1A4D2E] hover:bg-[#14532d] text-white text-sm font-semibold disabled:opacity-50 active:scale-95 transition-all" data-testid="draft-pin-confirm-btn">
                {saving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );

  if (stage === 'success') return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 space-y-3 text-center">
      <CheckCircle2 size={36} className="mx-auto text-emerald-500" />
      <p className="font-bold text-emerald-700 text-base">Quantities Updated</p>
      {result && (
        <>
          <p className="text-xs text-emerald-600">Updated by <strong>{result.updated_by}</strong></p>
          <p className="text-xs text-emerald-600">at {result.updated_at?.slice(0,16).replace('T',' ')} UTC</p>
          <p className="text-sm font-bold text-[#1A4D2E]">New Total: {php(result.new_grand_total)}</p>
        </>
      )}
      <p className="text-xs text-emerald-500 italic">The cashier has been notified of these changes.</p>
    </div>
  );

  return null;
}


function WebPaymentSection({ basic, docCode, onPaymentRecorded, onReprintRequested }) {
  const METHODS = ['Cash', 'GCash', 'Maya', 'Bank Transfer', 'Check'];
  const php = v => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 })}`;

  const [stage, setStage] = React.useState('idle');
  const [balance, setBalance] = React.useState(basic.balance);
  const [authMethod, setAuthMethod] = React.useState(null); // 'totp' | 'staff_login'
  const [webAuthToken, setWebAuthToken] = React.useState('');
  const [staffName, setStaffName] = React.useState('');
  const [loginEmail, setLoginEmail] = React.useState('');
  const [loginPassword, setLoginPassword] = React.useState('');
  const [loginLoading, setLoginLoading] = React.useState(false);
  const [loginError, setLoginError] = React.useState('');
  const [amount, setAmount] = React.useState('');
  const [method, setMethod] = React.useState('Cash');
  const [reference, setReference] = React.useState('');
  const [totpCode, setTotpCode] = React.useState('');
  const [paySubmitting, setPaySubmitting] = React.useState(false);
  const [payError, setPayError] = React.useState('');
  const isDigital = method !== 'Cash' && method !== 'Check';

  const handleStaffLogin = async () => {
    setLoginLoading(true); setLoginError('');
    try {
      const res = await axios.post(`${BACKEND}/api/auth/login`, { email: loginEmail, password: loginPassword });
      const { token, user } = res.data;
      const role = user?.role || '';
      if (!['admin', 'owner', 'manager'].includes(role)) {
        toast.error("You don't have the necessary authority for this action");
        setLoginLoading(false);
        return;
      }
      setWebAuthToken(token);
      setStaffName(user?.full_name || user?.username || 'Staff');
      setAuthMethod('staff_login');
      setStage('form');
    } catch (e) {
      const d = e.response?.data?.detail;
      setLoginError(typeof d === 'string' ? d : 'Invalid credentials. Please try again.');
    }
    setLoginLoading(false);
  };

  const handlePaySubmit = async () => {
    const amt = parseFloat(amount);
    if (!amt || amt <= 0) { setPayError('Enter a valid amount'); return; }
    if (amt > balance + 0.01) { setPayError(`Amount exceeds balance ${php(balance)}`); return; }
    if (authMethod === 'totp' && (!totpCode || totpCode.length !== 6)) {
      setPayError('Enter your 6-digit time-based code'); return;
    }
    setPaySubmitting(true); setPayError('');
    const paymentRef = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    try {
      const payload = { amount: amt, method, reference, payment_ref: paymentRef };
      if (authMethod === 'staff_login') payload.web_auth_token = webAuthToken;
      else payload.pin = totpCode;
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/receive_payment`, payload);
      setBalance(res.data.new_balance);
      if (onPaymentRecorded) onPaymentRecorded(res.data);
      setStage('done');
    } catch (e) {
      const d = e.response?.data?.detail;
      setPayError(typeof d === 'string' ? d : (d?.message || 'Payment failed. Please try again.'));
    }
    setPaySubmitting(false);
  };

  if (!basic.available_actions?.includes('receive_payment') && stage === 'idle') return null;

  if (stage === 'done') return (
    <div className="bg-white rounded-xl border overflow-hidden" data-testid="web-payment-done">
      <div className="p-5 text-center space-y-3">
        <CheckCircle2 size={28} className="text-emerald-500 mx-auto" />
        <p className="text-sm font-semibold text-emerald-800">Payment Applied</p>
        <p className="text-xs text-emerald-600">Remaining balance: {php(balance)}</p>
        {onReprintRequested && (
          <div className="pt-1 space-y-2">
            <p className="text-xs text-slate-500">Print updated receipt?</p>
            <div className="flex gap-2 justify-center">
              <button onClick={onReprintRequested}
                className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#1A4D2E] text-white text-xs font-semibold active:scale-95 transition-all"
                data-testid="web-payment-reprint-btn">
                <Printer size={13} /> Reprint 58mm
              </button>
              <button onClick={() => setStage('idle')}
                className="px-4 py-2 rounded-xl border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50"
                data-testid="web-payment-skip-reprint-btn">
                Skip
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  if (stage === 'idle') return (
    <div className="bg-white rounded-xl border overflow-hidden" data-testid="web-payment-section">
      <button onClick={() => setStage('auth_choice')}
        className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors"
        data-testid="web-receive-payment-btn">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-emerald-50 flex items-center justify-center">
            <Banknote size={16} className="text-emerald-600" />
          </div>
          <div className="text-left">
            <p className="text-sm font-semibold text-slate-800">Apply Payment</p>
            <p className="text-xs text-slate-400">{php(balance)} due — requires TOTP or staff login</p>
          </div>
        </div>
        <ChevronDown size={16} className="text-slate-400" />
      </button>
    </div>
  );

  if (stage === 'auth_choice') return (
    <div className="bg-white rounded-xl border overflow-hidden" data-testid="web-auth-choice">
      <div className="px-5 py-3 bg-slate-50 border-b flex items-center gap-2">
        <Lock size={14} className="text-slate-600" />
        <span className="text-sm font-semibold text-slate-700">Verify Identity to Apply Payment</span>
      </div>
      <div className="p-5 space-y-3">
        <p className="text-xs text-slate-400">Payment requires a time-based code or a staff login (manager/admin only).</p>
        <button onClick={() => { setAuthMethod('totp'); setStage('form'); }}
          className="w-full flex items-center gap-3 p-3 rounded-xl border border-slate-200 hover:bg-slate-50 transition-colors text-left"
          data-testid="web-pay-totp-btn">
          <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
            <ShieldCheck size={16} className="text-blue-600" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-800">Time-Based Code (TOTP)</p>
            <p className="text-xs text-slate-400">6-digit code from authenticator app</p>
          </div>
        </button>
        <button onClick={() => setStage('staff_login')}
          className="w-full flex items-center gap-3 p-3 rounded-xl border border-slate-200 hover:bg-slate-50 transition-colors text-left"
          data-testid="web-pay-login-btn">
          <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center shrink-0">
            <Lock size={16} className="text-amber-600" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-800">Login as Manager / Admin</p>
            <p className="text-xs text-slate-400">Email and password required</p>
          </div>
        </button>
        <button onClick={() => setStage('idle')} className="text-xs text-slate-400 hover:underline w-full text-center pt-1">Cancel</button>
      </div>
    </div>
  );

  if (stage === 'staff_login') return (
    <div className="bg-white rounded-xl border overflow-hidden" data-testid="web-staff-login">
      <div className="px-5 py-3 bg-amber-50 border-b flex items-center gap-2">
        <Lock size={14} className="text-amber-600" />
        <span className="text-sm font-semibold text-amber-800">Manager / Admin Login</span>
      </div>
      <div className="p-5 space-y-3">
        <input type="email" autoComplete="off" value={loginEmail}
          onChange={e => { setLoginEmail(e.target.value); setLoginError(''); }}
          placeholder="Email address" data-testid="web-login-email"
          className="w-full h-11 rounded-lg border border-input bg-background px-3 text-sm" />
        <input type="password" autoComplete="new-password" value={loginPassword}
          onChange={e => { setLoginPassword(e.target.value); setLoginError(''); }}
          onKeyDown={e => e.key === 'Enter' && handleStaffLogin()}
          placeholder="Password" data-testid="web-login-password"
          className="w-full h-11 rounded-lg border border-input bg-background px-3 text-sm" />
        {loginError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{loginError}</p>}
        <div className="flex gap-2">
          <button onClick={() => { setStage('auth_choice'); setLoginEmail(''); setLoginPassword(''); setLoginError(''); }}
            className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Back</button>
          <Button className="flex-1 h-10 bg-[#1A4D2E] hover:bg-[#14532d] text-white text-sm"
            onClick={handleStaffLogin} disabled={loginLoading || !loginEmail || !loginPassword}
            data-testid="web-login-submit">
            {loginLoading ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : null}
            {loginLoading ? 'Verifying...' : 'Login'}
          </Button>
        </div>
      </div>
    </div>
  );

  // stage === 'form' — payment form (shared between TOTP and staff login paths)
  return (
    <div className="bg-white rounded-xl border overflow-hidden" data-testid="web-payment-form">
      <div className="px-5 py-3 bg-emerald-50 border-b flex items-center gap-2">
        <ShieldCheck size={14} className="text-emerald-600" />
        <span className="text-xs font-semibold text-emerald-700">
          {authMethod === 'staff_login' ? `Authorized: ${staffName}` : 'TOTP required at submission'}
        </span>
      </div>
      <div className="p-5 space-y-3">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Record Payment</p>
        <div className="flex gap-2 flex-wrap">
          {METHODS.map(m => (
            <button key={m} onClick={() => setMethod(m)} data-testid={`web-method-${m.toLowerCase().replace(' ', '-')}`}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${method === m ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300'}`}>
              {m}
            </button>
          ))}
        </div>
        <div>
          <label className="text-xs text-slate-500 mb-1 block">Amount</label>
          <CalcInput autoComplete="off"
 value={amount} onChange={(v) => setAmount(v)}
 placeholder={`Max ${php(balance)}`}
 className="w-full h-11 text-center text-xl font-bold font-mono rounded-lg border border-input bg-background px-3"
 data-testid="web-payment-amount" autoFocus />
          <button onClick={() => setAmount(String(balance))} className="text-xs text-emerald-600 hover:underline mt-1">Full balance</button>
        </div>
        {isDigital && (
          <div>
            <label className="text-xs text-slate-500 mb-1 block">Reference No. <span className="text-slate-300">(optional)</span></label>
            <input type="text" autoComplete="off" value={reference} onChange={e => setReference(e.target.value)}
              placeholder="Transaction ref number" data-testid="web-payment-reference"
              className="w-full h-9 rounded-lg border border-input bg-background px-3 text-sm" />
          </div>
        )}
        {authMethod === 'totp' && (
          <div>
            <label className="text-xs font-semibold text-blue-700 mb-1 block">Time-Based Code (TOTP)</label>
            <CalcInput autoComplete="off"
 value={totpCode} maxLength={6}
 onChange={(v) => { setTotpCode(v.replace(/\D/g, '')); setPayError(''); }}
 placeholder="6-digit code" data-testid="web-totp-input"
 className="w-full h-11 text-center text-2xl font-mono tracking-widest rounded-lg border border-input bg-background px-3" />
          </div>
        )}
        {payError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{payError}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => { setStage(authMethod === 'staff_login' ? 'staff_login' : 'auth_choice'); setPayError(''); setAmount(''); setTotpCode(''); }}
            className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Back</button>
          <Button className="flex-1 h-10 bg-emerald-700 hover:bg-emerald-800 text-white"
            onClick={handlePaySubmit} disabled={paySubmitting || !amount}
            data-testid="web-pay-submit">
            {paySubmitting ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Banknote size={14} className="mr-1.5" />}
            {paySubmitting ? 'Processing...' : `Pay ${amount ? php(parseFloat(amount) || 0) : ''}`}
          </Button>
        </div>
      </div>
    </div>
  );
}


// ── Tier 1 print data — maps public view fields to PrintEngine-compatible format ──
// Used for terminal Tier-1 reprint (no PIN needed) and post-action reprint prompts.
// Fixes: correct status for getDocType, all required field names, safe fallbacks.
function buildTier1PrintData(basic) {
  if (!basic) return {};
  if (basic.doc_type === 'invoice') {
    return {
      invoice_number: basic.number,
      customer_name: basic.customer_name,
      order_date: basic.order_date || basic.date,
      created_at: basic.order_date || basic.date,
      items: (basic.items || []).map(i => ({
        product_name: i.name,
        quantity: i.qty,
        rate: i.price,
        total: i.total,
        // Per-line discount from public /doc/view — must flow into the
        // PrintEngine so the 58 mm receipt + Tier-1 reprint show the same
        // line discounts the web view does. Fixes the silent-drop bug
        // where the grand total was correct but discounts were invisible.
        discount_amount: parseFloat(i.discount_amount || 0) || 0,
        discount_value: parseFloat(i.discount_value || 0) || 0,
        discount_type: i.discount_type || 'amount',
      })),
      subtotal: basic.subtotal || basic.grand_total,
      overall_discount: basic.discount || 0,
      grand_total: basic.grand_total,
      amount_paid: basic.amount_paid,
      balance: basic.balance,
      payment_method: basic.payment_method,
      payment_type: basic.payment_type,
      // Explicit raw status so getDocType picks the right template
      status: (parseFloat(basic.balance) > 0) ? 'partial' : 'paid',
    };
  }
  if (basic.doc_type === 'purchase_order') {
    return {
      po_number: basic.number,
      purchase_date: basic.date,
      vendor: basic.supplier_name,
      status: basic.raw_status || '',
      items: (basic.items || []).map(i => ({
        product_name: i.name, quantity: i.qty, unit_price: i.price, total: i.total,
      })),
      subtotal: basic.grand_total,
      grand_total: basic.grand_total,
      payment_status: basic.payment_status || 'unpaid',
    };
  }
  if (basic.doc_type === 'branch_transfer') {
    return {
      order_number: basic.number,
      created_at: basic.date,
      from_branch_name: basic.from_branch,
      to_branch_name: basic.to_branch,
      status: basic.raw_status || basic.status || '',
      items: (basic.items || []).map(i => ({
        product_name: i.name, qty: i.qty, transfer_capital: i.price, branch_retail: 0,
      })),
    };
  }
  return basic;
}


function getTerminalSession() {
  try { const s = localStorage.getItem('agrismart_terminal'); return s ? JSON.parse(s) : null; } catch { return null; }
}

// ── Terminal Required Banner — shown when actions need a paired terminal ──
function TerminalRequiredBanner() {
  return (
    <div className="bg-slate-50 border-2 border-dashed border-slate-300 rounded-xl p-5 text-center" data-testid="terminal-required-banner">
      <div className="w-10 h-10 rounded-full bg-slate-200 flex items-center justify-center mx-auto mb-3">
        <Smartphone size={18} className="text-slate-500" />
      </div>
      <p className="text-sm font-semibold text-slate-700">Actions require an AgriSmart Terminal</p>
      <p className="text-xs text-slate-400 mt-1">Stock releases, payments, and receiving can only be done from a paired terminal device</p>
    </div>
  );
}

// ── Unified Stock Release Manager (PIN-gated: history + form + confirmation) ──
function StockReleaseManager({ basic, docCode, onStatusChange, terminalId, deviceId = '', onReprintRequested }) {
  // States: 'locked' | 'verifying' | 'unlocked' | 'confirming' | 'done'
  const [state, setState] = useState('locked');
  const [lockout, setLockout] = useState(null); // { retryAfter }
  const [attemptsRemaining, setAttemptsRemaining] = useState(null);
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [verifierName, setVerifierName] = useState('');
  const [verifiedPin, setVerifiedPin] = useState(''); // keep for re-use in release
  const [verifying, setVerifying] = useState(false);

  const [releaseItems, setReleaseItems] = useState(() =>
    (basic.reservations || []).map(r => ({
      ...r, input_qty: r.sold_qty_remaining > 0 ? String(r.sold_qty_remaining) : '0',
    }))
  );
  const [releaseError, setReleaseError] = useState('');
  const [releasing, setReleasing] = useState(false);
  const [confirmItems, setConfirmItems] = useState([]); // items pending confirmation
  const [releaseResult, setReleaseResult] = useState(null);
  const [releases, setReleases] = useState(basic.stock_releases || []);
  const [reservations, setReservations] = useState(basic.reservations || []);
  const canRelease = (basic.stock_release_status !== 'fully_released') && (basic.stock_release_status !== 'expired') && (basic.status !== 'voided');

  const totalOrdered = reservations.reduce((s, r) => s + (r.sold_qty_ordered || 0), 0);
  const totalReleased = releases.reduce((s, r) => s + (r.total_qty_released || 0), 0);

  const setQty = (idx, val) => setReleaseItems(prev => prev.map((it, i) => i === idx ? { ...it, input_qty: val } : it));

  // Step 1: Verify PIN to unlock
  const handleVerifyPin = async () => {
    if (!pin) { setPinError('PIN is required'); return; }
    setVerifying(true); setPinError('');
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/verify_pin`, { pin });
      setVerifierName(res.data.verifier_name);
      setVerifiedPin(pin);
      setState('unlocked');
      setPin('');
      setLockout(null); setAttemptsRemaining(null);
    } catch (e) {
      const parsed = parsePinError(e);
      if (parsed.locked) {
        setLockout({ retryAfter: parsed.retryAfter });
        setState('locked');
      } else {
        setPinError(parsed.message);
        if (parsed.attemptsRemaining != null) setAttemptsRemaining(parsed.attemptsRemaining);
      }
    }
    setVerifying(false);
  };

  // Step 2: Build confirmation list
  const handlePrepareRelease = () => {
    const toRelease = releaseItems.filter(it => parseFloat(it.input_qty || 0) > 0 && it.sold_qty_remaining > 0);
    if (!toRelease.length) { setReleaseError('Enter at least one quantity to release'); return; }
    for (const it of toRelease) {
      if (parseFloat(it.input_qty) > it.sold_qty_remaining + 0.001) {
        setReleaseError(`Cannot release more than remaining for ${it.sold_product_name}`); return;
      }
    }
    setReleaseError('');
    setConfirmItems(toRelease);
    setState('confirming');
  };

  // Step 3: Execute release
  const handleConfirmRelease = async () => {
    setReleasing(true); setReleaseError('');
    const releaseRef = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    const items = confirmItems.map(it => ({ sold_product_id: it.sold_product_id, qty_release: parseFloat(it.input_qty) }));
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/release_stocks`, {
        pin: verifiedPin, release_ref: releaseRef, items, terminal_id: terminalId, device_id: deviceId,
      });
      setReleaseResult(res.data);
      setReleases(prev => [...prev, {
        release_number: res.data.release_number,
        released_at: new Date().toISOString(),
        released_by_name: res.data.authorized_by,
        pin_method: 'manager_pin',
        items: res.data.items_released,
        total_qty_released: res.data.items_released.reduce((s, i) => s + i.qty_released, 0),
        remaining_after: res.data.remaining_qty,
        fully_released: res.data.fully_released,
      }]);
      // Update reservations display
      setReservations(prev => prev.map(r => {
        const released = items.find(i => i.sold_product_id === r.sold_product_id);
        if (!released) return r;
        return { ...r, sold_qty_released: r.sold_qty_released + released.qty_release, sold_qty_remaining: r.sold_qty_remaining - released.qty_release };
      }));
      setReleaseItems(prev => prev.map(it => {
        const released = items.find(i => i.sold_product_id === it.sold_product_id);
        if (!released) return it;
        const newRemaining = it.sold_qty_remaining - released.qty_release;
        return { ...it, sold_qty_remaining: Math.max(0, newRemaining), sold_qty_released: it.sold_qty_released + released.qty_release, input_qty: newRemaining > 0 ? String(newRemaining) : '0' };
      }));
      if (onStatusChange) onStatusChange(res.data.stock_release_status);
      setState('done');
    } catch (e) { setReleaseError(e.response?.data?.detail || 'Release failed'); setState('unlocked'); }
    setReleasing(false);
  };

  const releaseStatusLabel = (s) => ({
    'not_released': 'Unreleased', 'partially_released': 'Partially Released',
    'fully_released': 'Fully Released', 'expired': 'Expired',
  }[s] || s);
  const releaseStatusColor = (s) => ({
    'not_released': 'bg-amber-100 text-amber-700', 'partially_released': 'bg-blue-100 text-blue-700',
    'fully_released': 'bg-emerald-100 text-emerald-700', 'expired': 'bg-slate-200 text-slate-500',
  }[s] || 'bg-slate-100 text-slate-600');

  // ── Locked button ─────────────────────────────────────────────────────────
  if (state === 'locked') return (
    <div className="space-y-3">
      {lockout && (
        <LockoutBanner retryAfter={lockout.retryAfter} onExpired={() => setLockout(null)} />
      )}
      {!lockout && (
        <button onClick={() => setState('verifying')} data-testid="manage-releases-btn"
          className="w-full bg-white rounded-xl border border-slate-200 px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-amber-50 flex items-center justify-center">
              <Lock size={16} className="text-amber-600" />
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-slate-800">
                {canRelease ? 'Release Stocks / View History' : 'View Release History'}
              </p>
              <p className="text-xs text-slate-400">
                <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium mr-1 ${releaseStatusColor(basic.stock_release_status)}`}>
                  {releaseStatusLabel(basic.stock_release_status)}
                </span>
                {releases.length > 0 && `${releases.length} batch${releases.length !== 1 ? 'es' : ''} · ${totalReleased} of ${totalOrdered} units released`}
              </p>
            </div>
          </div>
          <ChevronDown size={16} className="text-slate-400" />
        </button>
      )}
    </div>
  );

  // ── PIN prompt ────────────────────────────────────────────────────────────
  if (state === 'verifying') return (
    <div className="bg-white rounded-xl border-2 border-amber-200 p-5 space-y-4" data-testid="release-pin-prompt">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-amber-50 flex items-center justify-center shrink-0">
          <Lock size={16} className="text-amber-600" />
        </div>
        <div>
          <p className="text-sm font-semibold text-slate-800">Authorization Required</p>
          <p className="text-xs text-slate-400">Branch Manager PIN, Admin PIN, or Time-Based PIN (TOTP)</p>
        </div>
      </div>
      <Input
        type="password"
        autoComplete="one-time-code"
        value={pin}
        onChange={e => { setPin(e.target.value); setPinError(''); }}
        onKeyDown={e => e.key === 'Enter' && handleVerifyPin()}
        placeholder="Enter PIN"
        className="h-12 text-center text-xl font-mono tracking-widest"
        autoFocus
        data-testid="release-access-pin"
      />
      {pinError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{pinError}</p>}
      {attemptsRemaining != null && attemptsRemaining <= 4 && (
        <p className="text-amber-600 text-xs flex items-center gap-1">
          <AlertTriangle size={11} /> {attemptsRemaining} attempt{attemptsRemaining !== 1 ? 's' : ''} remaining before lockout
        </p>
      )}
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" onClick={() => { setState('locked'); setPin(''); setPinError(''); setAttemptsRemaining(null); }}>Cancel</Button>
        <Button className="flex-1 bg-amber-600 hover:bg-amber-700 text-white" onClick={handleVerifyPin} disabled={verifying || !pin} data-testid="access-confirm-btn">
          {verifying ? <RefreshCw size={14} className="animate-spin mr-2" /> : <Lock size={14} className="mr-2" />}Access
        </Button>
      </div>
    </div>
  );

  // ── Confirmation step ─────────────────────────────────────────────────────
  if (state === 'confirming') return (
    <div className="bg-white rounded-xl border-2 border-amber-300 overflow-hidden" data-testid="release-confirm-panel">
      <div className="px-5 py-3 bg-amber-50 flex items-center gap-2">
        <Boxes size={16} className="text-amber-600" />
        <span className="text-sm font-semibold text-amber-800">Confirm Stock Release</span>
      </div>
      <div className="p-5 space-y-4">
        <p className="text-sm text-slate-600">You are about to release the following items:</p>
        <div className="rounded-lg border border-slate-200 overflow-hidden">
          <div className="bg-slate-50 px-4 py-2 flex justify-between text-xs font-semibold text-slate-500 uppercase tracking-wide">
            <span>Product</span><span>Qty to Release</span>
          </div>
          {confirmItems.map((it, i) => (
            <div key={i} className={`px-4 py-3 flex justify-between items-center ${i > 0 ? 'border-t border-slate-100' : ''}`}>
              <span className="text-sm font-medium text-slate-800">{it.sold_product_name}</span>
              <span className="text-sm font-bold text-amber-700">{parseFloat(it.input_qty)} {it.sold_unit}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400 flex items-center gap-1">
          <ShieldCheck size={11} className="text-emerald-500" />
          Authorized by <span className="font-medium text-slate-600 ml-1">{verifierName}</span>
        </p>
        {releaseError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{releaseError}</p>}
        <div className="flex gap-2">
          <Button variant="outline" className="flex-1" onClick={() => setState('unlocked')}>Back</Button>
          <Button className="flex-1 bg-amber-600 hover:bg-amber-700 text-white font-semibold" onClick={handleConfirmRelease} disabled={releasing} data-testid="final-confirm-release-btn">
            {releasing ? <RefreshCw size={14} className="animate-spin mr-2" /> : <CheckCircle2 size={14} className="mr-2" />}Yes, Release
          </Button>
        </div>
      </div>
    </div>
  );

  // ── Unlocked: history + form ──────────────────────────────────────────────
  return (
    <div className="bg-white rounded-xl border-2 border-amber-100 overflow-hidden" data-testid="release-manager-unlocked">
      {/* Header */}
      <div className="px-5 py-3 bg-amber-50 flex items-center gap-2">
        <Boxes size={16} className="text-amber-600" />
        <span className="text-sm font-semibold text-amber-800">Stock Releases</span>
        <span className={`ml-2 text-[10px] px-2 py-0.5 rounded-full font-medium ${releaseStatusColor(basic.stock_release_status)}`}>
          {releaseStatusLabel(basic.stock_release_status)}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <ShieldCheck size={12} className="text-emerald-500" />
          <span className="text-xs text-emerald-600">{verifierName}</span>
        </div>
      </div>

      <div className="p-5 space-y-5">
        {/* ── Release History ── */}
        {releases.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Release History</p>
            <div className="relative space-y-0">
              <div className="absolute left-[15px] top-2 bottom-2 w-px bg-slate-200" />
              {releases.map((r, idx) => (
                <div key={idx} className="relative flex gap-3 pb-3 last:pb-0" data-testid={`release-event-${r.release_number}`}>
                  <div className="relative z-10 w-8 h-8 rounded-full bg-emerald-100 border-2 border-emerald-300 flex items-center justify-center shrink-0">
                    <span className="text-[10px] font-bold text-emerald-700">#{r.release_number}</span>
                  </div>
                  <div className="flex-1 bg-slate-50 rounded-lg p-3 min-w-0">
                    <div className="flex items-start justify-between gap-2 flex-wrap">
                      <p className="text-sm font-semibold text-slate-800">Release #{r.release_number}</p>
                      <p className="text-[11px] text-slate-400 shrink-0">{fmtDateTime(r.released_at)}</p>
                    </div>
                    <div className="mt-1.5 space-y-1">
                      {r.items.map((it, i) => (
                        <div key={i} className="flex justify-between text-xs">
                          <span className="text-slate-600 truncate">{it.product_name}</span>
                          <span className="font-semibold text-slate-800 ml-2 shrink-0">{it.qty_released} {it.unit}</span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-2 pt-1.5 border-t border-slate-200 flex items-center justify-between text-[11px]">
                      <span className="text-slate-400">
                        By <span className="font-medium text-slate-600">{r.released_by_name}</span>
                        <span className="mx-1 text-slate-300">·</span>
                        <span className="capitalize">{(r.pin_method || '').replace('_', ' ')}</span>
                      </span>
                      <span className={r.remaining_after > 0 ? 'text-amber-600 font-medium' : 'text-emerald-600 font-medium'}>
                        {r.remaining_after > 0 ? `${r.remaining_after} remaining` : 'All released'}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 bg-slate-50 rounded-lg p-3 flex justify-between text-sm">
              <span className="text-slate-500">Total released</span>
              <span className="font-bold">{totalReleased} / {totalOrdered} units</span>
            </div>
          </div>
        )}

        {releases.length === 0 && !canRelease && (
          <p className="text-slate-400 text-sm text-center py-4">No releases recorded yet</p>
        )}

        {/* ── Release form (only if not fully released) ── */}
        {canRelease && (
          <div className={releases.length > 0 ? 'border-t border-slate-100 pt-5' : ''}>
            {state === 'done' && releaseResult ? (
              <div className="text-center space-y-3 py-2">
                <CheckCircle2 size={32} className="text-emerald-500 mx-auto" />
                <p className="font-semibold text-emerald-700">Release #{releaseResult.release_number} recorded!</p>
                {releaseResult.fully_released
                  ? <p className="text-sm text-emerald-600">All stock fully released.</p>
                  : <p className="text-sm text-amber-600">{releaseResult.remaining_qty} units still pending.</p>
                }
                {onReprintRequested && (
                  <div className="pt-1 space-y-2">
                    <p className="text-xs text-slate-500">Print receipt?</p>
                    <div className="flex gap-2 justify-center">
                      <button onClick={onReprintRequested}
                        className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#1A4D2E] text-white text-xs font-semibold active:scale-95 transition-all"
                        data-testid="release-reprint-btn">
                        <Printer size={13} /> Reprint 58mm
                      </button>
                      <button onClick={() => { if (!releaseResult.fully_released) { setState('unlocked'); setReleaseResult(null); } }}
                        className="px-4 py-2 rounded-xl border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50"
                        data-testid="release-skip-reprint-btn">
                        {releaseResult.fully_released ? 'Done' : 'Release More'}
                      </button>
                    </div>
                  </div>
                )}
                {!onReprintRequested && !releaseResult.fully_released && (
                  <Button variant="outline" size="sm" className="mt-2" onClick={() => { setState('unlocked'); setReleaseResult(null); }}>
                    Release More
                  </Button>
                )}
              </div>
            ) : (
              <>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
                  {releases.length > 0 ? 'Next Release' : 'Release Items'}
                </p>
                <div className="space-y-3">
                  {releaseItems.map((it, idx) => {
                    const done = it.sold_qty_remaining <= 0;
                    return (
                      <div key={it.sold_product_id} className={`rounded-lg border p-3 ${done ? 'bg-slate-50 opacity-50' : 'bg-white border-slate-200'}`}>
                        <div className="flex items-center justify-between gap-2 mb-2">
                          <div>
                            <p className="text-sm font-semibold text-slate-800">{it.sold_product_name}</p>
                            <p className="text-xs text-slate-400">
                              Ordered: {it.sold_qty_ordered} · Released: {it.sold_qty_released} · Remaining: {it.sold_qty_remaining} {it.sold_unit}
                            </p>
                          </div>
                          {done && <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />}
                        </div>
                        {!done && (
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-slate-500 shrink-0">Release now:</span>
                            <CalcInput autoComplete="off"
 value={it.input_qty}
 onChange={(v) => setQty(idx, v)}
 className="h-9 w-28 text-center font-semibold rounded-md border border-input bg-background text-sm px-3"
 data-testid={`release-qty-${it.sold_product_id}`} />
                            <span className="text-xs text-slate-400">{it.sold_unit}</span>
                            <button onClick={() => setQty(idx, String(it.sold_qty_remaining))}
                              className="text-xs text-blue-600 hover:underline ml-auto shrink-0">All</button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {releaseError && <p className="text-red-500 text-xs flex items-center gap-1 mt-3"><AlertTriangle size={12} />{releaseError}</p>}
                <Button
                  className="w-full mt-4 h-11 bg-amber-600 hover:bg-amber-700 text-white font-semibold"
                  onClick={handlePrepareRelease}
                  data-testid="prepare-release-btn"
                >
                  <Boxes size={14} className="mr-2" /> Review & Release
                </Button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}




// ── Receive Payment Panel (lives inside Tier 2 — PIN already verified) ────────
function ReceivePaymentPanel({ basic, docCode, storedPin, onPaymentRecorded, terminalId, deviceId = '', onReprintRequested }) {
  const [state, setState] = useState('idle'); // idle | form | confirming | done
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState('Cash');
  const [reference, setReference] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [balance, setBalance] = useState(basic.balance);

  // Upload proof state (digital only)
  const [uploadToken, setUploadToken] = useState(null);
  const [uploadSessionId, setUploadSessionId] = useState(null);
  const [proofFile, setProofFile] = useState(null); // { name, preview }
  const [uploading, setUploading] = useState(false);
  const fileInputRef = React.useRef(null);

  const METHODS = ['Cash', 'GCash', 'Maya', 'Bank Transfer', 'Check'];
  const isDigital = method !== 'Cash' && method !== 'Check';

  // Generate upload token when digital method selected
  React.useEffect(() => {
    if (isDigital && !uploadToken && storedPin && state === 'form') {
      axios.post(`${BACKEND}/api/qr-actions/${docCode}/generate-upload-token`, { pin: storedPin })
        .then(res => { setUploadToken(res.data.token); setUploadSessionId(res.data.session_id); })
        .catch(() => {}); // non-critical — payment still works without proof
    }
  }, [isDigital, state]); // eslint-disable-line

  const handleFileCapture = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !uploadToken) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('files', file);
      await axios.post(`${BACKEND}/api/uploads/upload/${uploadToken}`, formData);
      setProofFile({ name: file.name, preview: URL.createObjectURL(file) });
    } catch { setError('Photo upload failed — you can still submit without it'); }
    setUploading(false);
    e.target.value = '';
  };

  const handleConfirm = () => {
    const amt = parseFloat(amount);
    if (!amt || amt <= 0) { setError('Enter a valid amount'); return; }
    if (amt > balance + 0.01) { setError(`Amount exceeds balance ₱${balance.toLocaleString('en-PH', { minimumFractionDigits: 2 })}`); return; }
    if (isDigital && !proofFile) { setError('Please attach a screenshot of the transfer before proceeding'); return; }
    setError(''); setState('confirming');
  };

  const handleSubmit = async () => {
    setSubmitting(true); setError('');
    const paymentRef = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/receive_payment`, {
        pin: storedPin,
        amount: parseFloat(amount),
        method,
        reference,
        upload_session_id: uploadSessionId || undefined,
        payment_ref: paymentRef,
        terminal_id: terminalId,
        device_id: deviceId,
      });
      setResult(res.data);
      setBalance(res.data.new_balance);
      if (onPaymentRecorded) onPaymentRecorded(res.data);
      setState('done');
    } catch (e) { setError(extractDetail(e, 'Payment failed')); setState('form'); }
    setSubmitting(false);
  };

  const php = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 })}`;

  if (!basic.available_actions?.includes('receive_payment') && state === 'idle') return null;

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      {state === 'idle' && (
        <button onClick={() => setState('form')} data-testid="receive-payment-btn"
          className="w-full flex items-center justify-between p-3 rounded-xl border border-emerald-200 bg-emerald-50/50 hover:bg-emerald-50 transition-colors">
          <div className="flex items-center gap-2">
            <Banknote size={16} className="text-emerald-600" />
            <span className="text-sm font-semibold text-emerald-800">Receive Payment</span>
            <span className="text-xs text-emerald-600 font-mono ml-1">{php(balance)} due</span>
          </div>
          <ChevronDown size={14} className="text-emerald-400" />
        </button>
      )}

      {state === 'form' && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Record Payment</p>
          <div className="flex gap-2 flex-wrap">
            {METHODS.map(m => (
              <button key={m} onClick={() => { setMethod(m); setProofFile(null); }} data-testid={`method-${m.toLowerCase().replace(' ', '-')}`}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${method === m ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300'}`}>
                {m}
              </button>
            ))}
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1 block">Amount</label>
            <CalcInput autoComplete="off"
 value={amount} onChange={(v) => setAmount(v)}
 placeholder={`Max ${php(balance)}`}
 className="w-full h-11 text-center text-xl font-bold font-mono rounded-lg border border-input bg-background px-3"
 data-testid="payment-amount-input" autoFocus />
            <button onClick={() => setAmount(String(balance))} className="text-xs text-emerald-600 hover:underline mt-1">Full balance</button>
          </div>
          {isDigital && (
            <div>
              <label className="text-xs text-slate-500 mb-1 block">Reference / Ref No. <span className="text-slate-300">(optional)</span></label>
              <input type="text" autoComplete="off" value={reference} onChange={e => setReference(e.target.value)}
                placeholder="Transaction ref number" data-testid="payment-reference-input"
                className="w-full h-9 rounded-lg border border-input bg-background px-3 text-sm" />
            </div>
          )}
          {/* Transfer receipt photo — REQUIRED for digital payments */}
          {isDigital && (
            <div>
              <label className="text-xs font-medium text-amber-700 mb-1.5 flex items-center gap-1">
                <Camera size={11} /> Transfer Receipt Screenshot <span className="text-red-500 ml-0.5">*</span>
              </label>
              {proofFile ? (
                <div className="flex items-center gap-3 p-2 rounded-lg border border-emerald-200 bg-emerald-50">
                  <img src={proofFile.preview} alt="proof" className="w-14 h-14 object-cover rounded-lg border border-emerald-200 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-emerald-700 truncate">{proofFile.name}</p>
                    <p className="text-[10px] text-emerald-500 flex items-center gap-1"><CheckCircle2 size={10} /> Attached</p>
                  </div>
                  <button onClick={() => setProofFile(null)} className="shrink-0 w-7 h-7 rounded-full bg-white border border-slate-200 flex items-center justify-center hover:bg-red-50">
                    <X size={12} className="text-slate-400" />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading || !uploadToken}
                  data-testid="upload-proof-btn"
                  className="w-full h-14 rounded-xl border-2 border-dashed border-amber-300 bg-amber-50 flex items-center justify-center gap-2 text-sm text-amber-700 font-medium hover:border-amber-400 hover:bg-amber-100 transition-all disabled:opacity-40">
                  {uploading
                    ? <><RefreshCw size={14} className="animate-spin" /> Uploading...</>
                    : <><Camera size={16} /> Tap to attach transfer screenshot</>}
                </button>
              )}
              {!proofFile && <p className="text-[10px] text-amber-600 mt-1">Required — attach a screenshot of the transfer confirmation before proceeding.</p>}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={handleFileCapture}
                data-testid="proof-file-input"
              />
            </div>
          )}
          {error && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{error}</p>}
          <div className="flex gap-2">
            <button onClick={() => setState('idle')} className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Cancel</button>
            <button onClick={handleConfirm} data-testid="payment-confirm-btn"
              className="flex-1 h-10 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold">
              Continue
            </button>
          </div>
        </div>
      )}

      {state === 'confirming' && (
        <div className="space-y-3">
          <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2">
            <p className="text-sm font-semibold text-emerald-800">Confirm Payment</p>
            <div className="flex justify-between text-sm"><span className="text-slate-500">Amount</span><span className="font-bold text-emerald-700">{php(amount)}</span></div>
            <div className="flex justify-between text-sm"><span className="text-slate-500">Method</span><span className="font-medium">{method}</span></div>
            {reference && <div className="flex justify-between text-sm"><span className="text-slate-500">Ref #</span><span className="font-mono text-xs">{reference}</span></div>}
            {proofFile && (
              <div className="flex items-center gap-2 text-xs text-emerald-600 pt-1 border-t border-emerald-100 mt-1">
                <Camera size={11} /> Transfer receipt attached
              </div>
            )}
            <div className="flex justify-between text-sm border-t border-emerald-200 pt-2 mt-2">
              <span className="text-slate-500">New Balance</span>
              <span className={`font-bold ${parseFloat(amount) >= balance ? 'text-emerald-600' : 'text-amber-600'}`}>
                {php(Math.max(0, balance - parseFloat(amount)))}
              </span>
            </div>
          </div>
          {error && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{error}</p>}
          <div className="flex gap-2">
            <button onClick={() => setState('form')} className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Back</button>
            <button onClick={handleSubmit} disabled={submitting} data-testid="payment-submit-btn"
              className="flex-1 h-10 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold disabled:opacity-60">
              {submitting ? <RefreshCw size={14} className="animate-spin mx-auto" /> : 'Confirm Payment'}
            </button>
          </div>
        </div>
      )}

      {state === 'done' && result && (
        <div className="text-center space-y-3 py-3">
          <CheckCircle2 size={32} className="text-emerald-500 mx-auto" />
          <p className="font-semibold text-emerald-700">{result.message}</p>
          <p className="text-xs text-slate-400">Authorized by {result.authorized_by}</p>
          {onReprintRequested && (
            <div className="pt-1 space-y-2">
              <p className="text-xs text-slate-500">Print updated receipt?</p>
              <div className="flex gap-2 justify-center">
                <button onClick={onReprintRequested}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#1A4D2E] text-white text-xs font-semibold active:scale-95 transition-all"
                  data-testid="payment-reprint-btn">
                  <Printer size={13} /> Reprint 58mm
                </button>
                <button onClick={() => { setState('idle'); setAmount(''); setReference(''); setProofFile(null); setUploadToken(null); setUploadSessionId(null); }}
                  className="px-4 py-2 rounded-xl border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50"
                  data-testid="payment-skip-reprint-btn">
                  Skip
                </button>
              </div>
            </div>
          )}
          {!onReprintRequested && result.new_balance > 0 && (
            <button onClick={() => { setState('idle'); setAmount(''); setReference(''); setProofFile(null); setUploadToken(null); setUploadSessionId(null); }}
              className="text-xs text-emerald-600 hover:underline mt-1">Record another payment</button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Transfer Receive Panel (top-level PIN-gated panel for branch transfers) ───
function TransferReceivePanel({ basic, docCode, onReceived, terminalId, deviceId = '' }) {
  const [state, setState] = useState('locked'); // locked | verifying | unlocked | confirming | done
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [verifierName, setVerifierName] = useState('');
  const [verifiedPin, setVerifiedPin] = useState('');
  const [lockout, setLockout] = useState(null);
  const [attemptsRemaining, setAttemptsRemaining] = useState(null);
  const [receiveItems, setReceiveItems] = useState(() =>
    (basic.items || []).map(i => ({ ...i, input_qty: String(i.qty) }))
  );
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const php = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 })}`;
  const setQty = (idx, val) => setReceiveItems(prev => prev.map((it, i) => i === idx ? { ...it, input_qty: val } : it));

  const variances = receiveItems.map(it => ({
    ...it,
    received: parseFloat(it.input_qty) || 0,
    diff: (parseFloat(it.input_qty) || 0) - it.qty,
  }));
  const hasVariance = variances.some(v => Math.abs(v.diff) > 0.001);

  const handleVerifyPin = async () => {
    if (!pin) { setPinError('PIN is required'); return; }
    setVerifying(true); setPinError('');
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/verify_pin`, { pin });
      setVerifierName(res.data.verifier_name);
      setVerifiedPin(pin);
      setState('unlocked');
      setPin('');
      setLockout(null); setAttemptsRemaining(null);
    } catch (e) {
      const parsed = parsePinError(e);
      if (parsed.locked) {
        setLockout({ retryAfter: parsed.retryAfter });
        setState('locked');
      } else {
        setPinError(parsed.message);
        if (parsed.attemptsRemaining != null) setAttemptsRemaining(parsed.attemptsRemaining);
      }
    }
    setVerifying(false);
  };

  const handleConfirm = async () => {
    setSubmitting(true); setError('');
    const transferRef = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`;
    const items = receiveItems.map(it => ({
      product_id: it.product_id || it.name,
      qty_received: parseFloat(it.input_qty) || 0,
    }));
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${docCode}/transfer_receive`, {
        pin: verifiedPin, items, notes, transfer_ref: transferRef, terminal_id: terminalId, device_id: deviceId,
      });
      setResult(res.data);
      if (onReceived) onReceived(res.data);
      setState('done');
    } catch (e) { setError(extractDetail(e, 'Receive failed')); setState('unlocked'); }
    setSubmitting(false);
  };

  const handlePrepare = () => {
    const bad = receiveItems.find(it => parseFloat(it.input_qty) < 0);
    if (bad) { setError('Quantities cannot be negative'); return; }
    setError(''); setState('confirming');
  };

  if (state === 'locked') return (
    <div className="space-y-3">
      {lockout && (
        <LockoutBanner retryAfter={lockout.retryAfter} onExpired={() => setLockout(null)} />
      )}
      {!lockout && (
        <button onClick={() => setState('verifying')} data-testid="transfer-receive-btn"
          className="w-full bg-white rounded-xl border border-slate-200 px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-50 flex items-center justify-center">
              <Package size={16} className="text-emerald-600" />
            </div>
            <div className="text-left">
              <p className="text-sm font-semibold text-slate-800">Receive Stocks</p>
              <p className="text-xs text-slate-400">{basic.items?.length || 0} item{basic.items?.length !== 1 ? 's' : ''} · PIN required</p>
            </div>
          </div>
          <ChevronDown size={16} className="text-slate-400" />
        </button>
      )}
    </div>
  );

  if (state === 'verifying') return (
    <div className="bg-white rounded-xl border-2 border-emerald-200 p-5 space-y-4" data-testid="transfer-pin-prompt">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-emerald-50 flex items-center justify-center shrink-0">
          <Lock size={16} className="text-emerald-600" />
        </div>
        <div>
          <p className="text-sm font-semibold text-slate-800">Manager Authorization Required</p>
          <p className="text-xs text-slate-400">Manager PIN, Admin PIN, or TOTP</p>
        </div>
      </div>
      <input type="password" autoComplete="one-time-code" value={pin}
        onChange={e => { setPin(e.target.value); setPinError(''); }}
        onKeyDown={e => e.key === 'Enter' && handleVerifyPin()}
        placeholder="Enter PIN" autoFocus
        className="w-full h-12 text-center text-xl font-mono tracking-widest rounded-lg border border-input bg-background px-3"
        data-testid="transfer-access-pin" />
      {pinError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{pinError}</p>}
      {attemptsRemaining != null && attemptsRemaining <= 4 && (
        <p className="text-amber-600 text-xs flex items-center gap-1">
          <AlertTriangle size={11} /> {attemptsRemaining} attempt{attemptsRemaining !== 1 ? 's' : ''} remaining before lockout
        </p>
      )}
      <div className="flex gap-2">
        <button onClick={() => { setState('locked'); setPin(''); setPinError(''); setAttemptsRemaining(null); }}
          className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Cancel</button>
        <button onClick={handleVerifyPin} disabled={verifying || !pin}
          className="flex-1 h-10 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold disabled:opacity-60"
          data-testid="transfer-access-confirm-btn">
          {verifying ? <RefreshCw size={14} className="animate-spin mx-auto" /> : 'Unlock'}
        </button>
      </div>
    </div>
  );

  if (state === 'confirming') return (
    <div className="bg-white rounded-xl border-2 border-emerald-300 overflow-hidden" data-testid="transfer-confirm-panel">
      <div className="px-5 py-3 bg-emerald-50 flex items-center gap-2">
        <Package size={16} className="text-emerald-600" />
        <span className="text-sm font-semibold text-emerald-800">Confirm Receipt</span>
        {hasVariance && <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Variance detected</span>}
      </div>
      <div className="p-5 space-y-4">
        <div className="rounded-lg border border-slate-200 overflow-hidden">
          <div className="bg-slate-50 px-4 py-2 grid grid-cols-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
            <span>Product</span><span className="text-center">Ordered</span><span className="text-right">Received</span>
          </div>
          {variances.map((it, i) => (
            <div key={i} className={`px-4 py-2.5 grid grid-cols-3 items-center ${i > 0 ? 'border-t border-slate-100' : ''}`}>
              <span className="text-sm font-medium text-slate-800 truncate pr-2">{it.name}</span>
              <span className="text-sm text-slate-500 text-center">{it.qty}</span>
              <span className={`text-sm font-bold text-right ${Math.abs(it.diff) > 0.001 ? (it.diff < 0 ? 'text-red-600' : 'text-amber-600') : 'text-emerald-600'}`}>
                {it.received}
                {Math.abs(it.diff) > 0.001 && <span className="text-[10px] ml-1">({it.diff > 0 ? '+' : ''}{it.diff})</span>}
              </span>
            </div>
          ))}
        </div>
        {hasVariance && (
          <div className="bg-amber-50 rounded-lg p-3 text-xs text-amber-700">
            Variance will be sent to the source branch for review. Inventory moves only on exact match.
          </div>
        )}
        <p className="text-xs text-slate-400 flex items-center gap-1">
          <ShieldCheck size={11} className="text-emerald-500" />
          Authorized by <span className="font-medium text-slate-600 ml-1">{verifierName}</span>
        </p>
        {error && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{error}</p>}
        <div className="flex gap-2">
          <button onClick={() => setState('unlocked')} className="flex-1 h-10 rounded-lg border border-slate-200 text-slate-600 text-sm hover:bg-slate-50">Back</button>
          <button onClick={handleConfirm} disabled={submitting} data-testid="transfer-final-confirm-btn"
            className="flex-1 h-10 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold disabled:opacity-60">
            {submitting ? <RefreshCw size={14} className="animate-spin mx-auto" /> : 'Confirm Receive'}
          </button>
        </div>
      </div>
    </div>
  );

  if (state === 'done') return (
    <div className="bg-white rounded-xl border border-slate-200 p-6 text-center space-y-3" data-testid="transfer-receive-done">
      <CheckCircle2 size={36} className={`mx-auto ${result?.status === 'received_pending' ? 'text-amber-500' : 'text-emerald-500'}`} />
      <p className="font-semibold text-slate-800">
        {result?.status === 'received_pending' ? 'Receipt submitted — pending source review' : 'Stocks received successfully!'}
      </p>
      <p className="text-sm text-slate-500">{result?.message || ''}</p>
      {result?.has_variance && (
        <div className="text-xs text-amber-600 bg-amber-50 rounded-lg p-2">
          Source branch has been notified about the variance.
        </div>
      )}
    </div>
  );

  // Unlocked — items form
  return (
    <div className="bg-white rounded-xl border-2 border-emerald-100 overflow-hidden" data-testid="transfer-receive-unlocked">
      <div className="px-5 py-3 bg-emerald-50 flex items-center gap-2">
        <Package size={16} className="text-emerald-600" />
        <span className="text-sm font-semibold text-emerald-800">Enter Received Quantities</span>
        <div className="ml-auto flex items-center gap-1">
          <ShieldCheck size={12} className="text-emerald-500" />
          <span className="text-xs text-emerald-600">{verifierName}</span>
        </div>
      </div>
      <div className="p-5 space-y-4">
        <div className="space-y-2">
          {receiveItems.map((it, idx) => (
            <div key={idx} className="rounded-lg border border-slate-200 p-3 bg-white">
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-semibold text-slate-800">{it.name}</p>
                <span className="text-xs text-slate-400">Ordered: <span className="font-semibold text-slate-600">{it.qty}</span></span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 shrink-0">Received:</span>
                <CalcInput autoComplete="off"
 value={it.input_qty}
 onChange={(v) => setQty(idx, v)}
 className="h-9 w-28 text-center font-semibold rounded-md border border-input bg-background text-sm px-3"
 data-testid={`receive-qty-${it.product_id || idx}`} />
                <button onClick={() => setQty(idx, String(it.qty))} className="text-xs text-emerald-600 hover:underline ml-auto shrink-0">All</button>
              </div>
            </div>
          ))}
        </div>
        <div>
          <label className="text-xs text-slate-500 mb-1 block">Notes (optional)</label>
          <input type="text" value={notes} onChange={e => setNotes(e.target.value)} autoComplete="off"
            placeholder="Any notes about this receipt..."
            className="w-full h-9 rounded-lg border border-input bg-background px-3 text-sm" />
        </div>
        {error && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{error}</p>}
        <button onClick={handlePrepare} data-testid="prepare-receive-btn"
          className="w-full h-11 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm">
          <Package size={14} className="inline mr-2" />Review & Receive
        </button>
      </div>
    </div>
  );
}


export default function DocViewerPage() {
  const { code: codeParam } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [manualCode, setManualCode] = useState('');
  const code = codeParam;

  // Terminal branch context — passed as ?branch= when navigating from terminal
  const terminalBranchId = searchParams.get('branch') || '';

  const [basic, setBasic] = useState(null);
  const [loading, setLoading] = useState(!!code);
  const [error, setError] = useState('');
  const [releaseStatus, setReleaseStatus] = useState(null);
  const [businessInfo, setBusinessInfo] = useState({});

  const [showPinPrompt, setShowPinPrompt] = useState(false);
  const [pin, setPin] = useState('');
  const [pinLoading, setPinLoading] = useState(false);
  const [pinError, setPinError] = useState('');
  const [fullData, setFullData] = useState(null);
  const [unlockedPin, setUnlockedPin] = useState(''); // stored PIN reused for payment
  const [tier2Lockout, setTier2Lockout] = useState(null);
  const [tier2AttemptsRemaining, setTier2AttemptsRemaining] = useState(null);

  const [terminalSession] = useState(() => getTerminalSession());
  const isTerminal = !!terminalSession;
  // deviceId must be declared AFTER terminalSession to avoid TDZ
  const terminalDeviceId = searchParams.get('device') || terminalSession?.deviceId || '';
  const [terminalAction, setTerminalAction] = useState('');
  const [terminalPin, setTerminalPin] = useState('');
  const [terminalLoading, setTerminalLoading] = useState(false);
  const [terminalError, setTerminalError] = useState('');
  const [showReturnModal, setShowReturnModal] = useState(false);
  const [showUpdateReceiptModal, setShowUpdateReceiptModal] = useState(false);
  const [dayIsClosed, setDayIsClosed] = useState(false);

  // Cross-branch: TOTP-only gate for foreign branch documents
  const [crossBranchPin, setCrossBranchPin] = useState('');
  const [crossBranchVerifying, setCrossBranchVerifying] = useState(false);
  const [crossBranchError, setCrossBranchError] = useState('');
  const [crossBranchVerifiedPin, setCrossBranchVerifiedPin] = useState('');
  const [crossBranchVerifierName, setCrossBranchVerifierName] = useState('');

  // Detect if this document belongs to a different branch than the terminal
  const isForeignBranch = useMemo(() => {
    if (!terminalBranchId || !basic) return false;
    if (basic.doc_type === 'invoice' || basic.doc_type === 'purchase_order') {
      return !!(basic.branch_id && basic.branch_id !== terminalBranchId);
    }
    if (basic.doc_type === 'branch_transfer') {
      return !!(basic.to_branch_id && basic.to_branch_id !== terminalBranchId);
    }
    return false;
  }, [basic, terminalBranchId]);

  const crossBranchUnlocked = !!crossBranchVerifiedPin;

  // Fetch business info for printing (use terminal token if available)
  useEffect(() => {
    const headers = terminalSession?.token ? { Authorization: `Bearer ${terminalSession.token}` } : {};
    axios.get(`${BACKEND}/api/settings/business-info`, { headers })
      .then(r => setBusinessInfo(r.data || {}))
      .catch(() => {});
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    axios.get(`${BACKEND}/api/doc/view/${code?.toUpperCase()}`)
      .then(res => { 
        setBasic(res.data); 
        setReleaseStatus(res.data.stock_release_status); 
        setError(''); 
        
        // Check if day is closed (for invoice receipts)
        if (res.data.doc_type === 'invoice' && res.data.order_date && res.data.branch_id) {
          axios.get(`${BACKEND}/api/daily-close/last-close`, {
            params: { branch_id: res.data.branch_id }
          }).then(closeRes => {
            const lastCloseDate = closeRes.data?.last_close_date;
            setDayIsClosed(lastCloseDate && res.data.order_date <= lastCloseDate);
          }).catch(() => setDayIsClosed(false));
        }
      })
      .catch(e => setError(extractDetail(e, 'Document not found')))
      .finally(() => setLoading(false));
  }, [code]);

  const handleManualLookup = () => {
    const c = manualCode.trim().toUpperCase();
    if (c.length >= 6) navigate(`/doc/${c}`);
  };
  
  // Refresh basic document data
  const fetchBasic = () => {
    if (!code) return;
    axios.get(`${BACKEND}/api/doc/view/${code.toUpperCase()}`)
      .then(res => {
        setBasic(res.data);
        setReleaseStatus(res.data.stock_release_status);
      })
      .catch(e => console.error('Failed to refresh:', e));
  };

  // Reprint using PrintEngine (proper formatted receipt, not raw window.print)
  const handleReprint = async (format = 'thermal') => {
    if (!fullData?.document) return;
    const doc = fullData.document;
    let docType;
    if (basic.doc_type === 'invoice') docType = PrintEngine.getDocType(doc);
    else if (basic.doc_type === 'purchase_order') docType = 'purchase_order';
    else if (basic.doc_type === 'branch_transfer') docType = 'branch_transfer';
    else docType = 'order_slip';
    try {
      await PrintBridge.print({ type: docType, data: doc, format, businessInfo, docCode: code?.toUpperCase() });
    } catch (err) { toast.error(`Print failed: ${err.message || 'Unknown error'}`); }
  };

  // Tier 1 reprint — uses public view data (basic), available without PIN.
  // Always uses basic (never fullData.document) because basic is kept live-updated
  // after payments, whereas fullData.document is fetched once at PIN unlock time.
  const handleTier1Reprint = async (format = 'thermal', updatedBasic = null) => {
    const src = updatedBasic || basic;
    const printData = buildTier1PrintData(src);
    let docType;
    if (src.doc_type === 'invoice') docType = PrintEngine.getDocType(printData);
    else if (src.doc_type === 'purchase_order') docType = 'purchase_order';
    else docType = 'branch_transfer';
    try {
      await PrintBridge.print({ type: docType, data: printData, format, businessInfo, docCode: code?.toUpperCase() });
    } catch (err) { toast.error(`Print failed: ${err.message || 'Unknown error'}`); }
  };

  // Cross-branch TOTP verification — only 6-digit time-based code accepted
  const handleCrossBranchVerify = async () => {
    if (!crossBranchPin || crossBranchPin.length !== 6 || !/^\d+$/.test(crossBranchPin)) {
      setCrossBranchError('Enter your 6-digit TOTP (time-based code from authenticator app)');
      return;
    }
    setCrossBranchVerifying(true);
    setCrossBranchError('');
    try {
      const res = await axios.post(`${BACKEND}/api/qr-actions/${code?.toUpperCase()}/verify_pin`, { pin: crossBranchPin });
      setCrossBranchVerifiedPin(crossBranchPin);
      setCrossBranchVerifierName(res.data.verifier_name);
      setCrossBranchPin('');
    } catch (e) {
      const parsed = parsePinError(e);
      if (parsed.locked) {
        setCrossBranchError(`Document locked — try again in ${Math.ceil((parsed.retryAfter || 900) / 60)} min`);
      } else {
        setCrossBranchError('Invalid code. Only TOTP (6-digit time-based PIN) is accepted for cross-branch actions.');
      }
    }
    setCrossBranchVerifying(false);
  };

  const handleUnlockFull = async () => {
    if (!pin) return;
    setPinLoading(true); setPinError('');
    try {
      const res = await axios.post(`${BACKEND}/api/doc/lookup`, { code: code?.toUpperCase(), pin });
      setUnlockedPin(pin);
      setFullData(res.data); setShowPinPrompt(false); setPin('');
      setTier2Lockout(null); setTier2AttemptsRemaining(null);
    } catch (e) {
      const parsed = parsePinError(e);
      if (parsed.locked) {
        setTier2Lockout({ retryAfter: parsed.retryAfter });
        setShowPinPrompt(false);
      } else {
        setPinError(parsed.message);
        if (parsed.attemptsRemaining != null) setTier2AttemptsRemaining(parsed.attemptsRemaining);
      }
    }
    setPinLoading(false);
  };

  const handleTerminalPull = async () => {
    if (!terminalPin) return;
    setTerminalLoading(true); setTerminalError('');
    try {
      const headers = terminalSession?.token ? { Authorization: `Bearer ${terminalSession.token}` } : {};
      if (basic.doc_type === 'purchase_order')
        await axios.post(`${BACKEND}/api/terminal/pull-po`, { po_id: basic.doc_id, pin: terminalPin }, { headers });
      else if (basic.doc_type === 'branch_transfer')
        await axios.post(`${BACKEND}/api/terminal/pull-transfer`, { transfer_id: basic.doc_id, pin: terminalPin }, { headers });
      setTerminalAction('success');
    } catch (e) { setTerminalError(e.response?.data?.detail || 'Action failed'); }
    setTerminalLoading(false);
  };

  // ── No code — show entry form ─────────────────────────────────────────────
  if (!code) return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm space-y-5">
        <div className="text-center">
          <div className="w-14 h-14 rounded-2xl bg-[#1A4D2E] flex items-center justify-center mx-auto mb-4">
            <Search size={24} className="text-white" />
          </div>
          <h1 className="text-xl font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Find Document</h1>
          <p className="text-sm text-slate-500 mt-1">Enter the document code printed on your receipt</p>
        </div>
        <Input value={manualCode} onChange={e => setManualCode(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === 'Enter' && handleManualLookup()}
          placeholder="e.g. AB12CD34" className="h-12 text-center text-xl font-mono tracking-widest uppercase"
          maxLength={10} autoFocus data-testid="doc-code-input" />
        <Button className="w-full h-11 bg-[#1A4D2E] hover:bg-[#14532d] text-white font-semibold"
          onClick={handleManualLookup} disabled={manualCode.trim().length < 6} data-testid="doc-code-search-btn">
          Find Document
        </Button>
      </div>
    </div>
  );

  if (loading) return <div className="min-h-screen bg-slate-50 flex items-center justify-center"><RefreshCw size={24} className="animate-spin text-slate-400" /></div>;

  if (error) return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-4">
      {isTerminal && (
        <div className="fixed top-0 left-0 right-0 z-20 bg-[#1A4D2E] px-4 py-2.5 flex items-center justify-between shadow-sm">
          <button onClick={() => navigate('/terminal')} className="flex items-center gap-2 text-white/90 hover:text-white transition-colors" data-testid="back-to-terminal-btn-error">
            <ArrowLeft size={16} /><span className="text-sm font-semibold">Back to Terminal</span>
          </button>
          <span className="text-emerald-300 text-xs font-medium">{terminalSession?.branchName || 'Terminal'}</span>
        </div>
      )}
      <div className={`bg-white rounded-2xl shadow-lg p-8 text-center max-w-sm space-y-4 ${isTerminal ? 'mt-12' : ''}`}>
        <AlertTriangle size={40} className="text-amber-500 mx-auto" />
        <h2 className="text-lg font-bold text-slate-800">Document Not Found</h2>
        <p className="text-slate-500 text-sm">Code: <span className="font-mono font-bold">{code?.toUpperCase()}</span></p>
        <p className="text-slate-400 text-sm">{error}</p>
        <Button variant="outline" className="w-full" onClick={() => navigate('/doc')}>Try Another Code</Button>
      </div>
    </div>
  );

  if (!basic) return null;

  const statusColor = { 'Fully Paid': 'bg-emerald-100 text-emerald-700', 'Completed': 'bg-emerald-100 text-emerald-700', 'In Transit': 'bg-blue-100 text-blue-700', 'Draft': 'bg-slate-100 text-slate-600', 'On Terminal': 'bg-amber-100 text-amber-700', 'Pending Review': 'bg-yellow-100 text-yellow-700', 'Disputed': 'bg-red-100 text-red-700', 'Cancelled': 'bg-red-100 text-red-600', 'For Preparation': 'bg-amber-100 text-amber-800 border border-amber-300' };
  const sColor = Object.entries(statusColor).find(([k]) => basic.status?.includes(k))?.[1] || 'bg-slate-100 text-slate-600';
  const effectiveReleaseStatus = releaseStatus || basic.stock_release_status;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Back to Terminal bar — only shown when accessed from a paired terminal */}
      {isTerminal && (
        <div className="sticky top-0 z-20 bg-[#1A4D2E] px-4 py-2.5 flex items-center justify-between shadow-sm" data-testid="back-to-terminal-bar">
          <button onClick={() => navigate('/terminal')}
            className="flex items-center gap-2 text-white/90 hover:text-white transition-colors"
            data-testid="back-to-terminal-btn">
            <ArrowLeft size={16} />
            <span className="text-sm font-semibold">Back to Terminal</span>
          </button>
          <span className="text-emerald-300 text-xs font-medium">{terminalSession?.branchName || 'Terminal'}</span>
        </div>
      )}
      <div className="max-w-2xl mx-auto p-4 sm:p-6 space-y-4">

        {/* Tier 1: Basic View */}
        <div className="bg-white rounded-xl shadow-sm border overflow-hidden" data-testid="doc-basic-view">
          <div className="bg-[#1A4D2E] px-5 py-4">
            <p className="text-emerald-200 text-xs uppercase tracking-wider font-medium">
              {basic.doc_type === 'invoice'
                ? 'Sales Receipt'
                : basic.doc_type === 'purchase_order'
                  ? (basic.is_branch_request ? 'Stock Request' : 'Purchase Order')
                  : 'Branch Transfer'}
            </p>
            <h1 className="text-white text-xl font-bold mt-0.5" data-testid="doc-number">{basic.number}</h1>
            <div className="flex items-center justify-between mt-1">
              <p className="text-emerald-200/70 text-sm">{fmtDateTime(basic.date)}</p>
              {basic.release_mode === 'partial' && (
                <Badge className={`text-[10px] ${effectiveReleaseStatus === 'fully_released' ? 'bg-emerald-500 text-white' : effectiveReleaseStatus === 'partially_released' ? 'bg-blue-500 text-white' : effectiveReleaseStatus === 'expired' ? 'bg-slate-400 text-white' : 'bg-amber-400 text-white'}`}>
                  {effectiveReleaseStatus === 'fully_released' ? 'All Released' : effectiveReleaseStatus === 'partially_released' ? 'Partially Released' : effectiveReleaseStatus === 'expired' ? 'Expired' : 'Unreleased'}
                </Badge>
              )}
            </div>
          </div>
          <div className="px-5 py-4 border-b border-slate-100">
            <div className="flex items-center justify-between">
              <div>
                {basic.doc_type === 'invoice' && <p className="text-sm text-slate-500">Customer: <span className="font-semibold text-slate-800">{basic.customer_name}</span></p>}
                {basic.doc_type === 'invoice' && basic.branch_name && <p className="text-xs text-slate-400 mt-0.5">Branch: <span className="font-medium text-slate-600">{basic.branch_name}</span></p>}
                {basic.doc_type === 'purchase_order' && !basic.is_branch_request && <p className="text-sm text-slate-500">Supplier: <span className="font-semibold text-slate-800">{basic.supplier_name}</span></p>}
                {basic.doc_type === 'purchase_order' && !basic.is_branch_request && basic.branch_name && <p className="text-xs text-slate-400 mt-0.5">Branch: <span className="font-medium text-slate-600">{basic.branch_name}</span></p>}
                {basic.doc_type === 'purchase_order' && basic.is_branch_request && (
                  <div className="flex items-center gap-2 text-sm" data-testid="stock-request-route">
                    <span className="font-semibold text-slate-800">{basic.requesting_branch_name || basic.branch_name}</span>
                    <span className="text-[10px] text-slate-400 uppercase tracking-wider">requests from</span>
                    <span className="font-semibold text-slate-800">{basic.supply_branch_name || '—'}</span>
                  </div>
                )}
                {basic.doc_type === 'branch_transfer' && (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-semibold text-slate-800">{basic.from_branch}</span>
                    <ArrowRight size={14} className="text-slate-400" />
                    <span className="font-semibold text-slate-800">{basic.to_branch}</span>
                  </div>
                )}
              </div>
              <Badge className={`text-sm px-3 py-1 ${sColor}`} data-testid="doc-status">{basic.status}</Badge>
            </div>
            {basic.doc_type === 'purchase_order' && basic.is_branch_request && basic.notes && (
              <p className="text-xs text-slate-500 mt-2 italic" data-testid="stock-request-notes">📝 {basic.notes}</p>
            )}
            {basic.doc_type === 'purchase_order' && basic.is_branch_request && basic.fulfillment_started_at && (
              <p className="text-[11px] text-blue-600 mt-1" data-testid="stock-request-fulfillment">
                Being fulfilled by <strong>{basic.fulfillment_started_by}</strong> · log in to the app to view the generated transfer.
              </p>
            )}
          </div>
          <div className="divide-y divide-slate-50">
            {basic.items.map((item, i) => {
              const discAmt = parseFloat(item.discount_amount || 0) || 0;
              const discVal = parseFloat(item.discount_value || 0) || 0;
              const discType = item.discount_type || 'amount';
              const qty = parseFloat(item.qty || 0) || 0;
              const rate = parseFloat(item.price || 0) || 0;
              const grossLine = qty * rate;
              return (
                <div key={i} className="px-5 py-3 flex items-start justify-between" data-testid={`doc-item-${i}`}>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">{item.name}</p>
                    <p className="text-xs text-slate-400">Qty: {item.qty} × {php(item.price)}</p>
                    {discAmt > 0 && (
                      <p className="text-xs text-rose-600 mt-0.5" data-testid={`doc-item-discount-${i}`}>
                        Less {discType === 'percent'
                          ? `${discVal}%`
                          : (discVal > 0 ? `${php(discVal)}/unit` : 'discount')}
                        : −{php(discAmt)}
                      </p>
                    )}
                  </div>
                  <div className="shrink-0 ml-3 text-right">
                    {discAmt > 0 && (
                      <p className="text-[11px] text-slate-400 line-through font-mono" data-testid={`doc-item-gross-${i}`}>{php(grossLine)}</p>
                    )}
                    <span className="text-sm font-semibold font-mono text-slate-800">{php(item.total)}</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="px-5 py-4 bg-slate-50 border-t border-slate-100">
            <div className="flex items-center justify-between">
              <span className="text-base font-semibold text-slate-700">{basic.doc_type === 'branch_transfer' ? 'Transfer Total' : 'Grand Total'}</span>
              <span className="text-xl font-bold font-mono text-[#1A4D2E]" data-testid="doc-total">{php(basic.grand_total || basic.total)}</span>
            </div>
            {basic.balance > 0 && <p className="text-xs text-red-500 text-right mt-1 font-semibold">Balance due: {php(basic.balance)}</p>}
          </div>
          {/* Tier 1 Reprint — terminal only, no PIN needed */}
          {isTerminal && (
            <div className="px-5 py-3 border-t border-slate-100 flex gap-2">
              <button
                onClick={() => handleTier1Reprint('thermal')}
                className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-700 text-xs font-semibold hover:bg-slate-50 active:scale-95 transition-all"
                data-testid="tier1-reprint-thermal"
              >
                <Printer size={13} /> Reprint 58mm
              </button>
              <button
                onClick={() => handleTier1Reprint('full_page')}
                className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-700 text-xs font-semibold hover:bg-slate-50 active:scale-95 transition-all"
                data-testid="tier1-reprint-fullpage"
              >
                <FileText size={13} /> Full Page
              </button>
            </div>
          )}
        </div>

        {/* Draft Adjust Panel — shown for FOR PREPARATION invoices */}
        {basic.raw_status === 'for_preparation' && (
          <DraftAdjustPanel
            basic={basic}
            docCode={code?.toUpperCase()}
            onUpdated={(res) => setBasic(prev => ({
              ...prev,
              grand_total: res.new_grand_total,
              items: res.items.map(i => ({
                name: i.product_name || prev.items.find(p => p.product_id === i.product_id)?.name || '',
                product_id: i.product_id,
                qty: i.quantity,
                price: i.rate || i.unit_price || i.price || 0,
                total: i.total,
              })),
            }))}
          />
        )}

        {/* Iter 252 — Linked Offline Draft Finalization banner */}
        {(basic.linked_offline_receipt_number || basic.finalized_from_draft_offline) && (
          <div className="mt-4 rounded-xl border-2 border-emerald-300 bg-emerald-50 p-4" data-testid="linked-receipt-banner">
            <p className="text-xs font-bold uppercase tracking-wider text-emerald-700 mb-2">Linked Offline Receipt</p>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-[10px] uppercase text-slate-500 font-semibold">Official Invoice</p>
                <p className="font-mono font-bold text-slate-800">{basic.original_draft_invoice_number || basic.number}</p>
              </div>
              <div>
                <p className="text-[10px] uppercase text-slate-500 font-semibold">Offline Receipt (printed copy)</p>
                <p className="font-mono font-bold text-emerald-700">{basic.linked_offline_receipt_number || '—'}</p>
              </div>
            </div>
            <p className="text-[11px] text-emerald-700 mt-2 italic">
              This invoice was finalized while offline and synced afterward. Both numbers resolve to this same record.
            </p>
            {(basic.offline_items_diverged || basic.payment_diverged) && (
              <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-[11px] text-amber-800" data-testid="divergence-warning">
                <p className="font-bold mb-1">⚠ Divergence flagged for manager review</p>
                {basic.offline_items_diverged && <p>· Items differ from the original draft.</p>}
                {basic.payment_diverged && <p>· Payment total differs from the original draft.</p>}
                <p className="text-[10px] mt-1 text-amber-700">Offline copy was treated as source of truth (customer holds that receipt). Cross-check via Audit Center → Offline Reconciliation.</p>
              </div>
            )}
          </div>
        )}

        {/* Stock Release Manager (PIN-gated: history + form) — Terminal only */}
        {basic.release_mode === 'partial' && (!isForeignBranch || crossBranchUnlocked) && (
          isTerminal ? (
            <StockReleaseManager
              basic={basic}
              docCode={code?.toUpperCase()}
              onStatusChange={(s) => setReleaseStatus(s)}
              terminalId={terminalSession?.terminalId}
              deviceId={terminalDeviceId}
              onReprintRequested={() => handleTier1Reprint('thermal')}
            />
          ) : <TerminalRequiredBanner />
        )}

        {/* Transfer Receive Panel — Terminal only */}
        {basic.doc_type === 'branch_transfer' && basic.available_actions?.includes('transfer_receive') && (!isForeignBranch || crossBranchUnlocked) && (
          isTerminal ? (
            <TransferReceivePanel
              basic={basic}
              docCode={code?.toUpperCase()}
              onReceived={(r) => setBasic(prev => ({ ...prev, status: r.status === 'received_pending' ? 'Pending Review' : 'Completed', available_actions: [] }))}
              terminalId={terminalSession?.terminalId}
              deviceId={terminalDeviceId}
            />
          ) : <TerminalRequiredBanner />
        )}

        {/* Cross-Branch TOTP Gate — shown when terminal scans a foreign branch document */}
        {isForeignBranch && !crossBranchUnlocked && (
          <div className="bg-amber-50 border-2 border-amber-300 rounded-xl overflow-hidden" data-testid="cross-branch-gate">
            <div className="px-5 py-3 bg-amber-100 flex items-center gap-2">
              <MapPin size={15} className="text-amber-700" />
              <span className="text-sm font-semibold text-amber-800">Cross-Branch Document</span>
              <Badge className="text-[10px] bg-amber-200 text-amber-800 ml-auto">{basic.branch_name || 'Other Branch'}</Badge>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-xs text-amber-700">
                This document belongs to <span className="font-semibold">{basic.branch_name || 'another branch'}</span>.
                To take action (receive payment, release stock, etc.), you must verify with your <span className="font-semibold">TOTP time-based code</span>.
                This prevents PIN leakage and creates a traceable cross-branch audit entry.
              </p>
              <CalcInput autoComplete="off"
 value={crossBranchPin}
 maxLength={6}
 onChange={(v) => { setCrossBranchPin(v.replace(/\D/g, '')); setCrossBranchError(''); }}
 onKeyDown={e => e.key === 'Enter' && handleCrossBranchVerify()}
 placeholder="6-digit TOTP code"
 className="h-12 text-center text-xl font-mono tracking-widest"
 data-testid="cross-branch-totp-input" />
              {crossBranchError && (
                <p className="text-red-500 text-xs flex items-center gap-1">
                  <AlertTriangle size={12} />{crossBranchError}
                </p>
              )}
              <Button
                className="w-full h-11 bg-amber-600 hover:bg-amber-700 text-white font-semibold"
                onClick={handleCrossBranchVerify}
                disabled={crossBranchVerifying || crossBranchPin.length !== 6}
                data-testid="cross-branch-verify-btn"
              >
                {crossBranchVerifying ? <RefreshCw size={14} className="animate-spin mr-2" /> : <ShieldCheck size={14} className="mr-2" />}
                Verify TOTP to Unlock Actions
              </Button>
            </div>
          </div>
        )}

        {/* Cross-branch unlock confirmation */}
        {isForeignBranch && crossBranchUnlocked && (
          <div className="flex items-center gap-2 px-1 py-2" data-testid="cross-branch-unlocked">
            <ShieldCheck size={14} className="text-amber-600" />
            <span className="text-xs text-amber-700 font-medium">
              Cross-branch actions unlocked — authorized by <span className="font-bold">{crossBranchVerifierName}</span>
              {' · '}Acting on <span className="font-bold">{basic.branch_name}</span> document from your terminal
            </span>
          </div>
        )}

        {/* Tier 2: PIN Full Details */}
        {!fullData ? (
          <div className="bg-white rounded-xl border overflow-hidden" data-testid="tier2-locked">
            {tier2Lockout ? (
              <div className="p-5">
                <LockoutBanner retryAfter={tier2Lockout.retryAfter} onExpired={() => setTier2Lockout(null)} />
              </div>
            ) : !showPinPrompt ? (
              <button onClick={() => setShowPinPrompt(true)} className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors" data-testid="view-full-details-btn">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-amber-50 flex items-center justify-center"><Lock size={16} className="text-amber-600" /></div>
                  <div className="text-left">
                    <p className="text-sm font-semibold text-slate-800">View Full Details</p>
                    <p className="text-xs text-slate-400">Payment history, attached files, notes</p>
                  </div>
                </div>
                <ChevronDown size={16} className="text-slate-400" />
              </button>
            ) : (
              <div className="p-5 space-y-3">
                <div className="flex items-center gap-3 mb-1"><Lock size={16} className="text-amber-600" /><p className="text-sm font-semibold text-slate-800">Enter PIN to unlock full details and actions</p></div>
                <p className="text-xs text-slate-500 -mt-2 mb-2">Unlocks payment history, documents, and all available actions for this receipt</p>
                <Input data-testid="tier2-pin-input" type="password" value={pin}
                  onChange={e => { setPin(e.target.value); setPinError(''); }}
                  onKeyDown={e => e.key === 'Enter' && handleUnlockFull()}
                  placeholder="Manager PIN, Admin PIN, or TOTP"
                  autoComplete="new-password"
                  className="h-11 text-center text-lg font-mono tracking-widest" autoFocus />
                {pinError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{pinError}</p>}
                {tier2AttemptsRemaining != null && tier2AttemptsRemaining <= 4 && (
                  <p className="text-amber-600 text-xs flex items-center gap-1">
                    <AlertTriangle size={11} /> {tier2AttemptsRemaining} attempt{tier2AttemptsRemaining !== 1 ? 's' : ''} remaining before lockout
                  </p>
                )}
                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1 h-10" onClick={() => { setShowPinPrompt(false); setPin(''); setPinError(''); setTier2AttemptsRemaining(null); }}>Cancel</Button>
                  <Button className="flex-1 h-10 bg-[#1A4D2E] hover:bg-[#14532d] text-white" onClick={handleUnlockFull} disabled={pinLoading || !pin} data-testid="tier2-unlock-btn">
                    {pinLoading ? 'Verifying...' : 'Unlock'}
                  </Button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4" data-testid="tier2-unlocked">
            <div className="flex items-center gap-2 px-1"><ShieldCheck size={14} className="text-emerald-600" /><span className="text-xs text-emerald-600 font-medium">Full details unlocked</span></div>
            {fullData.doc_type === 'invoice' && fullData.payments?.length > 0 && (
              <div className="bg-white rounded-xl border p-4">
                <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2"><CreditCard size={14} /> Payment History</h3>
                <div className="space-y-2">
                  {fullData.payments.map((p, i) => (
                    <div key={i} className="flex items-center justify-between text-sm py-2 border-b border-slate-50 last:border-0">
                      <div><span className="font-semibold">{php(p.amount)}</span><span className="text-slate-400 ml-2">{p.method || 'Cash'}</span></div>
                      <span className="text-slate-400 text-xs">{fmtDateTime(p.recorded_at || p.paid_at || p.date)}</span>
                    </div>
                  ))}
                </div>
                {/* Receive Payment action (uses the already-verified Tier 2 PIN) — Terminal only */}
                {isTerminal ? (
                  <ReceivePaymentPanel
                    basic={basic}
                    docCode={code?.toUpperCase()}
                    storedPin={unlockedPin}
                    terminalId={terminalSession?.terminalId}
                    deviceId={terminalDeviceId}
                    onReprintRequested={() => handleTier1Reprint('thermal')}
                    onPaymentRecorded={(r) => {
                      setBasic(prev => ({ ...prev, balance: r.new_balance, available_actions: r.new_balance <= 0 ? prev.available_actions?.filter(a => a !== 'receive_payment') : prev.available_actions }));
                      // Keep fullData.document in sync so Tier 2 reprint buttons also show correct balance
                      setFullData(prev => prev ? ({
                        ...prev,
                        payments: [...(prev.payments || []), r.payment],
                        document: prev.document ? { ...prev.document, balance: Math.max(0, r.new_balance), amount_paid: (prev.document.amount_paid || 0) + r.payment.amount, status: r.new_balance <= 0 ? 'paid' : 'partial' } : prev.document,
                      }) : prev);
                    }}
                  />
                ) : null}
              </div>
            )}
            {/* Invoice with no prior payments but balance > 0 */}
            {fullData.doc_type === 'invoice' && !fullData.payments?.length && (
              <div className="bg-white rounded-xl border p-4">
                <h3 className="text-sm font-semibold text-slate-700 mb-1 flex items-center gap-2"><CreditCard size={14} /> Payments</h3>
                <p className="text-xs text-slate-400 mb-2">No payments recorded yet.</p>
                {isTerminal ? (
                  <ReceivePaymentPanel
                    basic={basic}
                    docCode={code?.toUpperCase()}
                    storedPin={unlockedPin}
                    terminalId={terminalSession?.terminalId}
                    deviceId={terminalDeviceId}
                    onReprintRequested={() => handleTier1Reprint('thermal')}
                    onPaymentRecorded={(r) => {
                      setBasic(prev => ({ ...prev, balance: r.new_balance, available_actions: r.new_balance <= 0 ? prev.available_actions?.filter(a => a !== 'receive_payment') : prev.available_actions }));
                      setFullData(prev => prev ? ({
                        ...prev,
                        payments: [r.payment],
                        document: prev.document ? { ...prev.document, balance: Math.max(0, r.new_balance), amount_paid: (prev.document.amount_paid || 0) + r.payment.amount, status: r.new_balance <= 0 ? 'paid' : 'partial' } : prev.document,
                      }) : prev);
                    }}
                  />
                ) : null}
              </div>
            )}
            {fullData.attached_files?.length > 0 && (
              <div className="bg-white rounded-xl border p-4">
                <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2"><Image size={14} /> Attached Documents</h3>
                <div className="grid grid-cols-3 gap-3">
                  {fullData.attached_files.map((f, i) => (
                    <a key={i} href={f.url || f.file_url} target="_blank" rel="noreferrer" className="block rounded-lg border overflow-hidden hover:shadow-md transition-shadow">
                      {(f.url || f.file_url || '').match(/\.(jpg|jpeg|png|webp)/i)
                        ? <img src={f.url || f.file_url} alt="" className="w-full h-20 object-cover" />
                        : <div className="h-20 flex items-center justify-center bg-slate-50"><FileText size={20} className="text-slate-300" /></div>}
                    </a>
                  ))}
                </div>
              </div>
            )}
            <div className="flex gap-2" data-testid="reprint-buttons">
              <Button variant="outline" className="flex-1 h-10" onClick={() => handleReprint('thermal')} data-testid="reprint-thermal-btn">
                <Printer size={14} className="mr-1.5" /> 58mm
              </Button>
              <Button variant="outline" className="flex-1 h-10" onClick={() => handleReprint('full_page')} data-testid="reprint-fullpage-btn">
                <Printer size={14} className="mr-1.5" /> Full Page
              </Button>
            </div>
          </div>
        )}

        {/* Non-terminal payment — Apply Payment via TOTP or Staff Login */}
        {!isTerminal && (
          <WebPaymentSection
            basic={basic}
            docCode={code?.toUpperCase()}
            onReprintRequested={() => handleTier1Reprint('thermal')}
            onPaymentRecorded={(r) => {
              setBasic(prev => ({ ...prev, balance: r.new_balance, available_actions: r.new_balance <= 0 ? prev.available_actions?.filter(a => a !== 'receive_payment') : prev.available_actions }));
            }}
          />
        )}

        {/* Tier 3: Terminal Actions */}
        {isTerminal && fullData && (
          <div className="bg-white rounded-xl border-2 border-amber-200 overflow-hidden" data-testid="terminal-actions">
            <div className="px-5 py-3 bg-amber-50 flex items-center gap-2">
              <Smartphone size={16} className="text-amber-600" />
              <span className="text-sm font-semibold text-amber-800">Terminal Actions</span>
              <Badge className="text-[10px] bg-amber-200 text-amber-800 ml-auto">{terminalSession.branchName || 'Paired'}</Badge>
            </div>
            {terminalAction === 'success' ? (
              <div className="p-5 text-center"><CheckCircle2 size={32} className="text-emerald-500 mx-auto mb-2" /><p className="text-sm font-semibold text-emerald-700">Action completed successfully</p></div>
            ) : (
              <div className="p-5 space-y-3">
                {/* Invoice/Sales Receipt Actions */}
                {basic.doc_type === 'invoice' && (
                  <>
                    <div className="border-t border-amber-100 -mx-5 px-5 pt-3 mt-2">
                      <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-3 text-center">
                        ──── CORRECTIONS ────
                      </p>
                    </div>
                    <Button 
                      className="w-full h-11 bg-red-600 hover:bg-red-700 text-white font-semibold flex items-center justify-center gap-2"
                      onClick={() => setShowReturnModal(true)}
                      data-testid="terminal-return-refund-btn"
                    >
                      <RotateCcw size={14} />
                      Return & Refund
                    </Button>
                    <p className="text-xs text-slate-400 text-center -mt-1">
                      Process customer returns and issue refunds
                    </p>
                    
                    {/* Update Receipt for Incomplete Stock - Only if day not closed */}
                    {!dayIsClosed && (
                      <>
                        <Button 
                          className="w-full h-11 bg-amber-600 hover:bg-amber-700 text-white font-semibold flex items-center justify-center gap-2"
                          onClick={() => setShowUpdateReceiptModal(true)}
                          data-testid="terminal-update-receipt-btn"
                        >
                          <FileEdit size={14} />
                          Update for Incomplete Stock
                        </Button>
                        <p className="text-xs text-slate-400 text-center -mt-1">
                          Correct receipt when items weren't given
                        </p>
                      </>
                    )}
                    
                    {dayIsClosed && (
                      <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                        <p className="text-xs text-slate-500 text-center">
                          <Lock size={12} className="inline mr-1" />
                          Day closed - Receipt updates disabled. Use Return & Refund instead.
                        </p>
                      </div>
                    )}
                  </>
                )}
                
                {/* Purchase Order Actions (cash POs only — not for branch stock requests) */}
                {basic.doc_type === 'purchase_order' && !basic.is_branch_request && ['Draft', 'Ordered', 'In Progress'].includes(basic.status) && (
                  <>
                    <p className="text-sm text-slate-600 mb-2"><Package size={14} className="inline mr-1.5 text-blue-500" />Pull this PO to your terminal for product checking</p>
                    <Input type="password" value={terminalPin} onChange={e => { setTerminalPin(e.target.value); setTerminalError(''); }} onKeyDown={e => e.key === 'Enter' && handleTerminalPull()} placeholder="Enter PIN to pull" className="h-11 text-center font-mono tracking-widest" autoComplete="new-password" data-testid="terminal-pin-input" />
                    {terminalError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{terminalError}</p>}
                    <Button className="w-full h-11 bg-blue-600 hover:bg-blue-700 text-white font-semibold" onClick={handleTerminalPull} disabled={terminalLoading || !terminalPin} data-testid="terminal-pull-btn">
                      {terminalLoading ? <RefreshCw size={14} className="animate-spin mr-2" /> : <Package size={14} className="mr-2" />}Pull PO to Terminal
                    </Button>
                  </>
                )}

                {/* Branch Stock Request — view-only notice + login prompt */}
                {basic.doc_type === 'purchase_order' && basic.is_branch_request && (
                  <div className="space-y-2" data-testid="stock-request-actions">
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                      <p className="text-sm text-amber-800 font-medium">📦 Stock Request</p>
                      <p className="text-xs text-amber-700 mt-1">
                        This is a view-only summary of a branch stock request.
                        To fulfill (generate the Branch Transfer), open the app and go to
                        <span className="font-semibold"> Branch Transfer → History → Requests</span>.
                      </p>
                    </div>
                    <Button className="w-full h-10 bg-[#1A4D2E] hover:bg-emerald-700 text-white font-semibold"
                      onClick={() => navigate('/branch-transfers?tab=history&subtab=requests')}
                      data-testid="open-stock-request-btn">
                      Open in Branch Transfer
                    </Button>
                  </div>
                )}
                
                {/* Branch Transfer Actions */}
                {basic.doc_type === 'branch_transfer' && basic.raw_status === 'sent' && (
                  <>
                    <p className="text-sm text-slate-600 mb-2"><Building2 size={14} className="inline mr-1.5 text-blue-500" />Pull this transfer to your terminal for receiving</p>
                    <Input type="password" value={terminalPin} onChange={e => { setTerminalPin(e.target.value); setTerminalError(''); }} onKeyDown={e => e.key === 'Enter' && handleTerminalPull()} placeholder="Enter PIN to pull" className="h-11 text-center font-mono tracking-widest" autoComplete="new-password" data-testid="terminal-pin-input" />
                    {terminalError && <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={12} />{terminalError}</p>}
                    <Button className="w-full h-11 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold" onClick={handleTerminalPull} disabled={terminalLoading || !terminalPin} data-testid="terminal-pull-btn">
                      {terminalLoading ? <RefreshCw size={14} className="animate-spin mr-2" /> : <Building2 size={14} className="mr-2" />}Pull Transfer to Terminal
                    </Button>
                  </>
                )}
                
                {/* No actions available */}
                {basic.doc_type !== 'invoice' &&
                 !(basic.doc_type === 'purchase_order' && !basic.is_branch_request && ['Draft', 'Ordered', 'In Progress'].includes(basic.status)) &&
                 !(basic.doc_type === 'purchase_order' && basic.is_branch_request) &&
                 !(basic.doc_type === 'branch_transfer' && basic.raw_status === 'sent') && (
                  <p className="text-sm text-slate-400 text-center py-2">No terminal actions available for this document's current status</p>
                )}
              </div>
            )}
          </div>
        )}

        <div className="text-center pb-4">
          <button onClick={() => navigate('/doc')} className="text-xs text-slate-400 hover:text-slate-600 underline">
            Enter document code manually
          </button>
        </div>
      </div>

      {/* Return & Refund Modal */}
      {showReturnModal && fullData && (
        <TerminalReturnRefundModal
          invoice={fullData.document || fullData}
          terminalSession={terminalSession}
          onSuccess={() => {
            // Refresh document data
            fetchBasic();
            setTerminalAction('success');
            setTimeout(() => setTerminalAction(''), 3000);
          }}
          onClose={() => setShowReturnModal(false)}
        />
      )}

      {/* Update Receipt Modal */}
      {showUpdateReceiptModal && fullData && (
        <TerminalUpdateReceiptModal
          invoice={fullData.document || fullData}
          onSuccess={(result) => {
            // Refresh document data
            setBasic(prev => ({
              ...prev,
              grand_total: result.invoice.grand_total,
              balance: result.invoice.balance,
              correction_applied: true
            }));
            setFullData(prev => prev ? ({
              ...prev,
              document: result.invoice
            }) : null);
            setTerminalAction('success');
            setTimeout(() => setTerminalAction(''), 3000);
            
            if (result.reprint) {
              toast.info('Receipt reprint ready');
            }
          }}
          onClose={() => setShowUpdateReceiptModal(false)}
        />
      )}
    </div>
  );
}
