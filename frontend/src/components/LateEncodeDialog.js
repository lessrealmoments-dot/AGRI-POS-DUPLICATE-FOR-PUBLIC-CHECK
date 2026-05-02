/**
 * LateEncodeDialog
 *
 * Appears when the cashier tries to save a credit / partial sale dated to a
 * day that's already closed. Collects the required reason + manager PIN,
 * then resolves to a `late_encode` payload that the backend expects on the
 * POST /api/sales body.
 *
 * Guardrails surfaced in the UI (server enforces them anyway):
 *   - Only enabled for credit / partial payments
 *   - 7-day backdate cap
 *   - Reason ≥ 10 chars
 *   - Manager/admin PIN
 *   - Warning copy makes clear this will show on the NEXT open Z-report,
 *     not amend the closed one
 */
import { useEffect, useMemo, useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Button } from './ui/button';
import { AlertTriangle, Clock } from 'lucide-react';

const daysBetween = (targetDate) => {
  try {
    const t = new Date(targetDate + 'T00:00:00');
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((today.getTime() - t.getTime()) / (1000 * 60 * 60 * 24));
  } catch { return 0; }
};

export default function LateEncodeDialog({ open, onClose, orderDate, paymentType, onConfirm, warmPin }) {
  const [reason, setReason] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) { setReason(''); setPin(warmPin || ''); setBusy(false); }
  }, [open, warmPin]);

  const daysBack = useMemo(() => daysBetween(orderDate), [orderDate]);
  const crossMonth = useMemo(() => {
    try {
      const t = new Date(orderDate + 'T00:00:00');
      const now = new Date();
      return t.getMonth() !== now.getMonth() || t.getFullYear() !== now.getFullYear();
    } catch { return false; }
  }, [orderDate]);

  const valid = reason.trim().length >= 10 && pin.length >= 4
    && daysBack > 0 && daysBack <= 7 && !crossMonth;
  const isCreditOrPartial = ['credit', 'partial'].includes((paymentType || '').toLowerCase());

  const submit = () => {
    if (!valid || busy) return;
    setBusy(true);
    onConfirm({ reason: reason.trim(), pin });
    // parent closes on success/fail; keep busy until dialog unmounts
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="sm:max-w-md" data-testid="late-encode-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Clock size={18} className="text-amber-600" />
            Encode for Past Closed Date
          </DialogTitle>
          <DialogDescription>
            This day is already closed. For forgotten <b>credit / partial</b> sales,
            you can still encode it with an audit trail.
          </DialogDescription>
        </DialogHeader>

        <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900 space-y-1.5">
          <div className="flex gap-2 items-start">
            <AlertTriangle size={13} className="shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">This sale WILL NOT modify the closed Z-report.</p>
              <p className="mt-0.5">It will appear on the <b>next open day's Z-report</b> in a dedicated
              "Late-Encoded Credits" section. The customer's AR aging will correctly start from the original
              sale date ({orderDate}).</p>
            </div>
          </div>
        </div>

        {!isCreditOrPartial && (
          <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800">
            Only <b>credit</b> or <b>partial</b> payments can be late-encoded. Cash and digital
            cannot be backdated to a closed day.
          </div>
        )}

        {daysBack > 7 && (
          <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800">
            That date is <b>{daysBack} days</b> back. Late-encode is limited to the last 7 days.
            For older corrections, use a journal adjustment with your accountant.
          </div>
        )}

        {crossMonth && (
          <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800">
            Cannot late-encode into a prior month (protects VAT filings).
          </div>
        )}

        <div className="space-y-3 pt-1">
          <div>
            <Label className="text-xs">Reason <span className="text-slate-400">(min 10 chars)</span></Label>
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Forgot to encode Juan's fertilizer credit last Monday"
              className="mt-1 text-sm"
              data-testid="late-encode-reason"
            />
            <p className="text-[10px] text-slate-400 mt-1">{reason.trim().length} / 10+ chars</p>
          </div>

          <div>
            <Label className="text-xs">Manager / Admin PIN</Label>
            <Input
              type="password" inputMode="numeric" maxLength={6}
              value={pin} onChange={(e) => setPin(e.target.value)}
              className="mt-1 text-sm"
              data-testid="late-encode-pin"
            />
          </div>
        </div>

        <div className="flex gap-2 pt-2">
          <Button variant="outline" className="flex-1" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
            onClick={submit}
            disabled={!valid || !isCreditOrPartial || busy}
            data-testid="late-encode-confirm"
          >
            {busy ? 'Encoding…' : 'Confirm Late Encode'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
