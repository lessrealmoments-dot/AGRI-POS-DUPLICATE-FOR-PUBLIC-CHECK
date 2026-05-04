/**
 * AuthDialog — Unified PIN / TOTP / Password authorization dialog.
 *
 * Replaces VerifyPinDialog (C1) and TotpVerifyDialog (C2) as a single source of truth.
 * Both C1 and C2 are now thin wrappers around this component.
 *
 * Props:
 *   open          – boolean
 *   onClose       – fn()  called when dialog should close
 *   mode          – "pin" | "totp" | "either"
 *                   "pin"    → single PIN input (like old VerifyPinDialog)
 *                   "totp"   → mode tabs: Owner PIN / Authenticator / Password (like old TotpVerifyDialog)
 *                   "either" → mode tabs (same as totp)
 *   docType       – optional: "invoice" | "purchase_order" | "expense" | "branch_transfer"
 *   docId         – optional: document UUID
 *   docLabel      – optional: human-readable doc number for display
 *   context       – optional: string description of action being authorized (for audit)
 *   title         – optional: dialog title override
 *   description   – optional: subtitle text
 *   onVerified    – fn(result) called on success
 *   showDiscrepancy – boolean (default true for pin mode with docType)
 *
 * Endpoint routing:
 *   docType + docId present → POST /api/verify/{docType}/{docId}
 *   otherwise              → POST /api/auth/verify-admin-action
 */
import { useState, useRef, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  ShieldCheck, ShieldAlert, Shield, Lock, RefreshCw,
  KeyRound, Hash, Eye, EyeOff, X
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from './CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function AuthDialog({
  open,
  onClose,
  mode = 'pin',
  docType,
  docId,
  docLabel,
  context = '',
  title,
  description,
  onVerified,
  showDiscrepancy: showDiscrepancyProp,
}) {
  // Determine if this is a doc-verification flow or admin-action flow
  const isDocVerify = !!(docType && docId);
  const isMultiMode = mode === 'totp' || mode === 'either';
  const allowDiscrepancy = showDiscrepancyProp !== undefined
    ? showDiscrepancyProp
    : (mode === 'pin' && isDocVerify);

  // Resolve title
  const resolvedTitle = title || (isDocVerify ? 'Verify Transaction' : 'Admin Authorization Required');

  // ── State ──────────────────────────────────────────────────────────────
  const [pin, setPin] = useState('');
  const [showPin, setShowPin] = useState(false);
  const [loading, setLoading] = useState(false);

  // Multi-mode tab state (for totp/either modes)
  const [activeMode, setActiveMode] = useState('pin'); // 'pin' | 'totp' | 'password'

  // Discrepancy state (pin mode only)
  const [hasDiscrepancy, setHasDiscrepancy] = useState(false);
  const [discrepancyNote, setDiscrepancyNote] = useState('');
  const [itemDescription, setItemDescription] = useState('');
  const [expectedQty, setExpectedQty] = useState('');
  const [foundQty, setFoundQty] = useState('');
  const [unit, setUnit] = useState('');
  const [unitCost, setUnitCost] = useState('');

  const pinRef = useRef(null);

  // Focus PIN input on open
  useEffect(() => {
    if (open) {
      const t = setTimeout(() => { pinRef.current?.focus(); }, 120);
      return () => clearTimeout(t);
    }
  }, [open, activeMode]);

  const reset = () => {
    setPin(''); setShowPin(false); setLoading(false);
    setActiveMode('pin');
    setHasDiscrepancy(false); setDiscrepancyNote('');
    setItemDescription(''); setExpectedQty('');
    setFoundQty(''); setUnit(''); setUnitCost('');
  };

  const handleClose = () => { reset(); onClose?.(); };

  // ── Discrepancy value impact ──────────────────────────────────────────
  const valueImpact = hasDiscrepancy && expectedQty !== '' && foundQty !== '' && unitCost !== ''
    ? ((parseFloat(foundQty) - parseFloat(expectedQty)) * parseFloat(unitCost || 0)).toFixed(2)
    : null;

  // ── Submit ─────────────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!pin) { toast.error(isMultiMode ? 'Enter a code' : 'Please enter your PIN'); return; }
    if (isMultiMode && activeMode === 'totp' && pin.length !== 6) { toast.error('Enter 6-digit code'); return; }
    if (hasDiscrepancy && !discrepancyNote) { toast.error('Please describe the discrepancy'); return; }

    setLoading(true);
    try {
      if (isDocVerify && !isMultiMode) {
        // Doc verification flow (old C1 path)
        const payload = {
          pin,
          has_discrepancy: hasDiscrepancy,
          discrepancy_note: discrepancyNote,
          item_description: itemDescription,
          expected_qty: expectedQty !== '' ? parseFloat(expectedQty) : null,
          found_qty: foundQty !== '' ? parseFloat(foundQty) : null,
          unit,
          unit_cost: parseFloat(unitCost || 0),
        };
        const res = await api.post(`${BACKEND_URL}/api/verify/${docType}/${docId}`, payload);
        toast.success(`Verified by ${res.data.verified_by}`);
        onVerified?.(res.data);
        handleClose();
      } else {
        // Admin action flow (old C2 path)
        const res = await api.post('/auth/verify-admin-action', {
          mode: isMultiMode ? activeMode : 'pin',
          code: pin,
          context,
        });
        if (res.data.valid) {
          onVerified?.({ manager_name: res.data.manager_name, mode_used: res.data.mode_used });
          handleClose();
        } else {
          toast.error(res.data.error || 'Invalid — try again');
        }
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Verification failed');
    }
    setLoading(false);
  };

  if (!open) return null;

  // ══════════════════════════════════════════════════════════════════════
  // PIN-ONLY MODE (mode="pin") — matches old VerifyPinDialog layout
  // ══════════════════════════════════════════════════════════════════════
  if (!isMultiMode) {
    return (
      <div
        className="fixed inset-0 flex items-center justify-center p-4"
        style={{ backgroundColor: 'rgba(0,0,0,0.65)', zIndex: 9999 }}
        onClick={e => { if (e.target === e.currentTarget) handleClose(); }}
        data-testid="auth-dialog-pin"
      >
        <div className="relative bg-white rounded-2xl shadow-2xl w-full overflow-y-auto" style={{ maxWidth: '420px', maxHeight: '90vh' }}>
          <button onClick={handleClose} className="absolute top-3 right-3 z-10 w-7 h-7 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center">
            <X size={14} className="text-slate-500" />
          </button>

          <div className="p-5">
            {/* Header */}
            <div className="flex items-center gap-2 mb-4">
              <span className="inline-flex w-8 h-8 rounded-lg bg-[#1A4D2E] items-center justify-center shrink-0">
                <ShieldCheck size={15} className="text-white" />
              </span>
              <div>
                <p className="font-semibold text-slate-800 text-sm leading-tight">{resolvedTitle}</p>
                <p className="text-[11px] text-slate-400 truncate">{description || docLabel}</p>
              </div>
            </div>

            {/* PIN Input */}
            <div className="mb-4">
              <label className="text-xs font-medium text-slate-600 mb-1 block">Authorization PIN</label>
              <p className="text-[10px] text-slate-400 mb-2">Enter Admin PIN, TOTP code (6-digit), or your Auditor PIN</p>
              <div className="relative">
                <input
                  type={showPin ? 'text' : 'password'}
                  ref={pinRef}
                  value={pin}
                  onChange={e => setPin(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSubmit()}
                  placeholder="Enter PIN..."
                  data-testid="auth-pin-input"
                  className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm pr-10 focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/30"
                />
                <button
                  onClick={() => setShowPin(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showPin ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            {/* Discrepancy Toggle */}
            {allowDiscrepancy && (
              <>
                <div className="mb-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <div
                      onClick={() => setHasDiscrepancy(v => !v)}
                      className={`w-9 h-5 rounded-full transition-colors relative ${hasDiscrepancy ? 'bg-amber-500' : 'bg-slate-200'}`}
                    >
                      <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${hasDiscrepancy ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </div>
                    <span className="text-sm text-slate-700 font-medium">Flag a discrepancy</span>
                    <ShieldAlert size={14} className={hasDiscrepancy ? 'text-amber-500' : 'text-slate-300'} />
                  </label>
                  <p className="text-[10px] text-slate-400 mt-1 ml-11">Physical evidence doesn't match the system record</p>
                </div>

                {/* Discrepancy Details */}
                {hasDiscrepancy && (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 mb-4 flex flex-col gap-3">
                    <div>
                      <label className="text-[11px] font-medium text-amber-800 mb-1 block">Item / Description *</label>
                      <input type="text" value={itemDescription} onChange={e => setItemDescription(e.target.value)}
                        placeholder="e.g. Rice 50kg bags"
                        className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white" />
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <label className="text-[11px] font-medium text-amber-800 mb-1 block">Expected</label>
                        <CalcInput value={expectedQty} onChange={(v) => setExpectedQty(v)} placeholder="15"
 className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white" />
                      </div>
                      <div>
                        <label className="text-[11px] font-medium text-amber-800 mb-1 block">Found</label>
                        <CalcInput value={foundQty} onChange={(v) => setFoundQty(v)} placeholder="5"
 className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white" />
                      </div>
                      <div>
                        <label className="text-[11px] font-medium text-amber-800 mb-1 block">Unit</label>
                        <input type="text" value={unit} onChange={e => setUnit(e.target.value)} placeholder="bags"
                          className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white" />
                      </div>
                    </div>
                    <div>
                      <label className="text-[11px] font-medium text-amber-800 mb-1 block">Unit Cost (&#8369;)</label>
                      <CalcInput value={unitCost} onChange={(v) => setUnitCost(v)} placeholder="500.00"
 className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white" />
                    </div>
                    {valueImpact !== null && (
                      <div className={`text-xs font-bold rounded-lg px-3 py-1.5 text-center ${parseFloat(valueImpact) < 0 ? 'bg-red-100 text-red-700' : parseFloat(valueImpact) > 0 ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'}`}>
                        Value Impact: {parseFloat(valueImpact) > 0 ? '+' : ''}&#8369;{parseFloat(valueImpact).toLocaleString('en-PH', { minimumFractionDigits: 2 })}
                      </div>
                    )}
                    <div>
                      <label className="text-[11px] font-medium text-amber-800 mb-1 block">Note / Explanation *</label>
                      <textarea value={discrepancyNote} onChange={e => setDiscrepancyNote(e.target.value)}
                        placeholder="e.g. Physical DR shows 5 bags, system encoded 15 — possible encoding error"
                        rows={2} className="w-full border border-amber-200 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none bg-white resize-none" />
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Action buttons */}
            <div className="flex gap-2">
              <button onClick={handleClose}
                className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 transition-colors"
                data-testid="auth-cancel-btn">
                Cancel
              </button>
              <button onClick={handleSubmit} disabled={loading || !pin}
                className={`flex-1 py-2.5 rounded-xl font-medium text-sm transition-colors flex items-center justify-center gap-1.5
                  ${hasDiscrepancy ? 'bg-amber-500 hover:bg-amber-600 text-white' : 'bg-[#1A4D2E] hover:bg-[#14532d] text-white'}
                  disabled:opacity-50`}
                data-testid="auth-submit-btn">
                {loading ? (
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                ) : hasDiscrepancy ? (
                  <><ShieldAlert size={14} /> Flag & Verify</>
                ) : (
                  <><ShieldCheck size={14} /> Verify Transaction</>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════
  // MULTI-MODE (mode="totp" | "either") — matches old TotpVerifyDialog
  // ══════════════════════════════════════════════════════════════════════
  const MODE_TABS = [
    { key: 'pin',      icon: <Hash size={13} />,      label: 'Owner PIN' },
    { key: 'totp',     icon: <Shield size={13} />,    label: 'Authenticator' },
    { key: 'password', icon: <KeyRound size={13} />,  label: 'Password' },
  ];

  return (
    <Dialog open={open} onOpenChange={v => { if (!v) handleClose(); }}>
      <DialogContent className="sm:max-w-sm" data-testid="auth-dialog-totp">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <Shield size={18} className="text-amber-600" /> {resolvedTitle}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 mt-2">
          {/* Mode selector tabs */}
          <div className="flex rounded-lg border border-slate-200 overflow-hidden text-xs">
            {MODE_TABS.map(tab => (
              <button key={tab.key}
                onClick={() => { setActiveMode(tab.key); setPin(''); }}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 font-medium transition-colors ${
                  activeMode === tab.key
                    ? 'bg-amber-50 text-amber-700 border-b-2 border-amber-500'
                    : 'text-slate-500 hover:bg-slate-50'
                }`}
                data-testid={`auth-mode-${tab.key}`}>
                {tab.icon}{tab.label}
              </button>
            ))}
          </div>

          {/* PIN mode */}
          {activeMode === 'pin' && (
            <div className="space-y-3">
              <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                Enter the <strong>Owner PIN</strong> set in Settings &rarr; Audit Setup.
                For in-person approvals only — do not share with workers.
              </div>
              <div>
                <Label>Owner PIN</Label>
                <Input data-testid="auth-owner-pin-input" type="password" autoComplete="new-password"
                  inputMode="numeric" ref={pinRef} value={pin}
                  onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
                  placeholder="••••" className="text-center text-2xl tracking-[0.4em] font-mono h-12 mt-1"
                  maxLength={8} onKeyDown={e => e.key === 'Enter' && handleSubmit()} />
              </div>
            </div>
          )}

          {/* TOTP mode */}
          {activeMode === 'totp' && (
            <div className="space-y-3">
              <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
                Call the admin and ask them to open <strong>Google Authenticator</strong> and
                read you the current 6-digit code. It expires in 30 seconds and cannot be reused.
              </div>
              <div>
                <Label>Authenticator Code</Label>
                <Input data-testid="auth-totp-code-input" ref={pinRef} value={pin}
                  onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  placeholder="000000" className="text-center text-2xl tracking-[0.4em] font-mono h-12 mt-1"
                  maxLength={6} onKeyDown={e => e.key === 'Enter' && handleSubmit()} />
              </div>
              <p className="text-[10px] text-slate-400">Admin must have set up TOTP in Settings &rarr; Security first.</p>
            </div>
          )}

          {/* Password mode */}
          {activeMode === 'password' && (
            <div className="space-y-3">
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-700">
                Enter the admin&apos;s full <strong>login password</strong>. Use only as a last resort.
              </div>
              <div>
                <Label>Admin Password</Label>
                <Input data-testid="auth-password-input" type="password" autoComplete="new-password"
                  ref={pinRef} value={pin} onChange={e => setPin(e.target.value)}
                  placeholder="Admin login password" className="mt-1"
                  onKeyDown={e => e.key === 'Enter' && handleSubmit()} />
              </div>
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button variant="outline" className="flex-1" onClick={handleClose} data-testid="auth-cancel-btn">
              Cancel
            </Button>
            <Button data-testid="auth-submit-btn"
              className="flex-1 bg-amber-600 hover:bg-amber-700 text-white"
              onClick={handleSubmit}
              disabled={
                loading ||
                (activeMode === 'totp' && pin.length !== 6) ||
                ((activeMode === 'pin' || activeMode === 'password') && !pin)
              }>
              {loading
                ? <RefreshCw size={14} className="animate-spin mr-2" />
                : <Lock size={14} className="mr-2" />}
              Authorize
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
