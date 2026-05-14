import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from './ui/alert-dialog';
import { Send, Clock, CheckCircle2, AlertTriangle } from 'lucide-react';

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';
const MAX_SENDS = 3;

/**
 * Pickup-Ready SMS button — surfaced inside Terminal Actions on invoice docs.
 *
 * Server enforces the real limits (3 lifetime sends, 5-min cooldown). This
 * component just hydrates current state on mount and renders friendly UI:
 *   - Live countdown of the cooldown timer
 *   - Remaining-send badge
 *   - "Are you sure" confirmation modal (prevents accidental taps)
 *   - Disabled + reason text once the lifetime cap is hit
 */
export default function PickupSmsButton({ invoiceId, terminalToken }) {
  const [status, setStatus] = useState(null);   // {sent_count, remaining, retry_after_seconds, has_customer}
  const [loading, setLoading] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [secsLeft, setSecsLeft] = useState(0);

  const authHeaders = terminalToken
    ? { Authorization: `Bearer ${terminalToken}` }
    : (() => {
        const t = localStorage.getItem('token');
        return t ? { Authorization: `Bearer ${t}` } : {};
      })();

  // ── Hydrate current rate-limit state on mount ─────────────────────────────
  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(
        `${BACKEND}/api/invoices/${invoiceId}/pickup-sms-status`,
        { headers: authHeaders },
      );
      setStatus(res.data);
      setSecsLeft(res.data?.retry_after_seconds || 0);
    } catch (e) {
      // 404 or auth — silently disable button
      setStatus(null);
    }
  }, [invoiceId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  // ── Live cooldown ticker ──────────────────────────────────────────────────
  useEffect(() => {
    if (secsLeft <= 0) return;
    const t = setInterval(() => setSecsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [secsLeft]);

  const handleSend = async () => {
    setConfirmOpen(false);
    setLoading(true);
    try {
      const res = await axios.post(
        `${BACKEND}/api/invoices/${invoiceId}/send-pickup-sms`,
        {},
        { headers: authHeaders },
      );
      const remaining = res.data.remaining;
      setStatus({
        sent_count: res.data.sent_count,
        remaining,
        max_sends: MAX_SENDS,
        retry_after_seconds: remaining > 0 ? 5 * 60 : 0,
        has_customer: true,
      });
      setSecsLeft(remaining > 0 ? 5 * 60 : 0);
      toast.success(
        remaining > 0
          ? `Pickup SMS sent. ${remaining} send/s remaining.`
          : `Pickup SMS sent. Limit reached (${MAX_SENDS}/${MAX_SENDS}).`,
      );
    } catch (e) {
      const d = e.response?.data?.detail;
      const msg = typeof d === 'object' ? d.message : (d || 'Failed to send SMS');
      toast.error(msg);
      // Hydrate latest state so cooldown reflects server truth
      fetchStatus();
    } finally {
      setLoading(false);
    }
  };

  // ── Render guards ─────────────────────────────────────────────────────────
  if (!status) return null;  // still loading or no permission
  if (!status.has_customer) return null;  // walk-in / no customer to SMS

  const inCooldown = secsLeft > 0;
  const limitReached = status.remaining <= 0;
  const disabled = loading || inCooldown || limitReached;

  // Human countdown m:ss
  const mm = Math.floor(secsLeft / 60);
  const ss = String(secsLeft % 60).padStart(2, '0');

  return (
    <>
      <div className="border-t border-amber-100 -mx-5 px-5 pt-3 mt-2">
        <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-3 text-center">
          ──── PICKUP NOTIFICATIONS ────
        </p>
      </div>
      <Button
        className="w-full h-11 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
        onClick={() => setConfirmOpen(true)}
        disabled={disabled}
        data-testid="terminal-send-pickup-sms-btn"
      >
        {loading ? (
          <>Sending…</>
        ) : limitReached ? (
          <><AlertTriangle size={14} /> SMS Limit Reached ({MAX_SENDS}/{MAX_SENDS})</>
        ) : inCooldown ? (
          <><Clock size={14} /> Wait {mm}:{ss} to send again</>
        ) : (
          <><Send size={14} /> Send Pickup Ready SMS</>
        )}
      </Button>
      <div className="flex items-center justify-between text-xs text-slate-500 -mt-1">
        <span>Notify customer their order is ready for pickup</span>
        {!limitReached && (
          <Badge className="bg-emerald-50 text-emerald-700 border border-emerald-200" data-testid="pickup-sms-remaining">
            {status.remaining}/{MAX_SENDS} left
          </Badge>
        )}
        {limitReached && (
          <Badge className="bg-slate-100 text-slate-500 border border-slate-200">
            <CheckCircle2 size={11} className="mr-1" /> Sent {MAX_SENDS}×
          </Badge>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent data-testid="pickup-sms-confirm-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Send pickup-ready SMS?</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure the items are <strong>physically ready</strong> for the customer to collect?
              <br /><br />
              The customer will get an SMS saying their order is ready. After sending, you must wait
              <strong> 5 minutes</strong> before another reminder, and only{' '}
              <strong>{status.remaining} send{status.remaining === 1 ? '' : 's'}</strong> remain on this invoice.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="pickup-sms-cancel-btn">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleSend}
              className="bg-emerald-600 hover:bg-emerald-700"
              data-testid="pickup-sms-confirm-btn"
            >
              Yes, items are ready — send SMS
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
