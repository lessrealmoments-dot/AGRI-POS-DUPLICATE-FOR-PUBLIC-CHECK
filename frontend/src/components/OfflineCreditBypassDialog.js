/**
 * OfflineCreditBypassDialog — Phase 2 of Offline POS Robustness.
 *
 * When the Terminal is offline and a credit/partial sale needs authorization,
 * we cannot reach /api/signatures/session. Instead, the cashier collects a
 * Manager PIN (verified locally via the cached bcrypt hash) plus a written
 * reason. On sync replay, the backend retroactively creates a signature_session
 * with status=bypassed for full audit trail.
 *
 * UX intentionally mirrors the inline-signature manager-bypass panel so the
 * cashier learns one workflow, regardless of online state.
 */
import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { ShieldAlert, AlertTriangle, WifiOff, CheckCircle2 } from 'lucide-react';
import { formatPHP } from '../lib/utils';
import { verifyOfflinePin } from '../lib/offlineAuth';

export default function OfflineCreditBypassDialog({
  open,
  onOpenChange,
  invoice,         // { customer_name, balance, items, branch_name, credit_type }
  onConfirm,       // ({ method, by_name, reason, at }) => void
}) {
  const [pin, setPin] = useState('');
  const [reason, setReason] = useState('');
  const [err, setErr] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setPin('');
      setReason('');
      setErr('');
      setSubmitting(false);
    }
  }, [open]);

  const submit = async () => {
    setErr('');
    if (!pin.trim()) {
      setErr('Manager PIN is required');
      return;
    }
    if (!reason.trim() || reason.trim().length < 4) {
      setErr('A short reason is required (e.g. "Customer in a hurry, signed paper slip")');
      return;
    }
    setSubmitting(true);
    const result = await verifyOfflinePin(pin.trim());
    setSubmitting(false);
    if (!result.ok) {
      setErr(result.reason || 'Invalid PIN');
      return;
    }
    onConfirm?.({
      method: result.method || 'admin_pin',
      by_id: result.verifier_id || '',
      by_name: result.verifier_name || 'Manager',
      reason: reason.trim(),
      at: new Date().toISOString(),
      // C-1: PIN is verified server-side at sync time. Carry the typed
      // PIN in the envelope so the backend can re-verify via
      // verify_pin_for_action() and tag the sale pin_resync_failed if wrong.
      pin: pin.trim(),
      deferred_verification: result.deferred_verification === true,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="offline-credit-bypass-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base" style={{ fontFamily: 'Manrope' }}>
            <ShieldAlert size={18} className="text-amber-600" /> Offline Credit Authorization
          </DialogTitle>
          <DialogDescription className="text-xs">
            <span className="inline-flex items-center gap-1 text-amber-700 font-semibold">
              <WifiOff size={11} /> Offline mode
            </span>
            {' · '}
            {invoice?.customer_name} · {formatPHP(invoice?.balance || 0)}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-900">
            <p className="font-semibold mb-1">No internet — signature capture unavailable.</p>
            <p className="text-[11px] leading-relaxed">
              Enter <strong>Manager PIN</strong> to authorize this credit sale. The system
              will retroactively log the bypass for audit when the device reconnects.
            </p>
          </div>

          <div>
            <label className="text-[11px] text-slate-500 font-semibold uppercase tracking-wide">
              Manager PIN
            </label>
            <Input
              type="password"
              inputMode="numeric"
              value={pin}
              onChange={(e) => { setPin(e.target.value); setErr(''); }}
              placeholder="Enter Admin PIN"
              className="h-10 text-center font-mono"
              data-testid="offline-bypass-pin-input"
              autoFocus
            />
            <p className="text-[10px] text-slate-400 mt-1">
              Admin PIN or branch Manager PIN. The bypass + reason is logged for audit on next sync.
            </p>
          </div>

          <div>
            <label className="text-[11px] text-slate-500 font-semibold uppercase tracking-wide">
              Reason (required)
            </label>
            <Input
              value={reason}
              onChange={(e) => { setReason(e.target.value); setErr(''); }}
              placeholder="e.g. Customer in a hurry, signed paper slip"
              className="h-10 text-xs"
              data-testid="offline-bypass-reason-input"
            />
          </div>

          {err && (
            <div className="text-[11px] text-red-600 flex items-center gap-1 bg-red-50 px-2 py-1.5 rounded">
              <AlertTriangle size={11} /> {err}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button variant="outline" className="flex-1" onClick={() => onOpenChange?.(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button
              className="flex-1 bg-amber-600 hover:bg-amber-700 text-white gap-2"
              onClick={submit}
              disabled={submitting || !pin || !reason}
              data-testid="offline-bypass-confirm-btn"
            >
              <CheckCircle2 size={14} /> {submitting ? 'Verifying...' : 'Authorize Sale'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
