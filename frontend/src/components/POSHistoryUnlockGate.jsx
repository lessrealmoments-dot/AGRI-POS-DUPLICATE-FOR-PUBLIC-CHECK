/**
 * POSHistoryUnlockGate — PIN gate for the POS Sales / Purchase History views.
 *
 * Security model (per user requirement — no auto-unlock for any role):
 *   • Always requires explicit entry of one of:
 *       - Org Owner / Admin PIN           (org-wide)
 *       - Authenticator (TOTP) 6-digit    (org-wide)
 *       - Manager PIN                     (only if that manager is assigned
 *                                          to the current terminal branch)
 *   • All three are verified server-side by `verify_pin_for_action`
 *     (action_key='pos_view_history') with the terminal's current branch_id
 *     passed in — managers from OTHER branches are rejected by the backend
 *     even if their PIN happens to match.
 *   • The accepted methods can be tightened/loosened per-org via the standard
 *     PIN Policy admin screen (action key `pos_view_history`).
 *   • Once unlocked, the verification is cached in `pinSession` (≤30 min idle)
 *     so the user doesn't get re-prompted when switching between Sales History
 *     and Purchase History tabs in the same session.
 */
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Lock, Unlock, ShieldCheck, RefreshCw, AlertTriangle } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { toast } from 'sonner';

export default function POSHistoryUnlockGate({
  api,
  label,
  branch,
  pinSession,
  startPinSession,
  children,
}) {
  const sessionWarm = !!pinSession && (Date.now() - (pinSession.at || 0) < 30 * 60 * 1000);
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setError('');
      setCode('');
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const submit = useCallback(async () => {
    const codeStr = code.trim();
    if (!codeStr) {
      setError('Enter your PIN or Authenticator code');
      return;
    }
    setVerifying(true);
    setError('');
    try {
      // Single-shot verification: server checks Owner PIN, TOTP, then Manager
      // PIN (branch-restricted) in order — whichever matches first wins.
      const res = await api.post('/auth/verify-manager-pin', {
        pin: codeStr,
        action_key: 'pos_view_history',
        branch_id: branch?.id || undefined,
        context: {
          branch_id: branch?.id || undefined,
          type: 'pos_history_unlock',
          label: label || 'History',
        },
      });
      if (res.data?.valid) {
        startPinSession(codeStr, res.data.role || 'manager_pin', res.data.manager_name || '');
        setOpen(false);
        toast.success(`History unlocked${res.data.manager_name ? ` — ${res.data.manager_name}` : ''}`);
      } else {
        setError(res.data?.detail || 'PIN/TOTP did not match. Managers must be assigned to this branch.');
      }
    } catch (e) {
      setError(e?.response?.data?.detail || 'Verification failed. Try again.');
    } finally {
      setVerifying(false);
    }
  }, [code, branch?.id, label, startPinSession]);

  // ── Unlocked: render children with a small status strip ────────────────
  if (sessionWarm) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between text-[11px] text-slate-500 px-1">
          <span className="inline-flex items-center gap-1.5">
            <Unlock size={12} className="text-emerald-600" />
            <span data-testid="pos-history-unlock-state">
              History unlocked
              {pinSession?.name ? ` · ${pinSession.name}` : ''}
              {pinSession?.method ? ` · ${pinSession.method.replace('_', ' ')}` : ''}
            </span>
            {branch?.name && <span className="ml-1 text-slate-400">· {branch.name}</span>}
          </span>
        </div>
        {children}
      </div>
    );
  }

  // ── Locked card + dialog ───────────────────────────────────────────────
  return (
    <>
      <Card data-testid="pos-history-gate">
        <CardContent className="p-8 text-center space-y-3">
          <Lock className="w-10 h-10 text-slate-300 mx-auto" />
          <div className="text-sm font-medium text-slate-700">{label || 'History'} is locked</div>
          <p className="text-[11px] text-slate-400 max-w-md mx-auto leading-snug">
            Enter your Org Admin PIN, your Authenticator (TOTP) code, or — if you're the
            branch manager assigned to <strong>{branch?.name || 'this terminal'}</strong> —
            your Manager PIN. Managers from other branches will be rejected.
          </p>
          <Button
            size="sm"
            onClick={() => setOpen(true)}
            data-testid="pos-history-unlock-btn"
            className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
          >
            <ShieldCheck className="w-4 h-4 mr-1.5" />
            Unlock History
          </Button>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={(v) => { if (!verifying) setOpen(v); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <ShieldCheck className="text-[#1A4D2E]" size={18} />
              Unlock {label || 'History'}
            </DialogTitle>
            <DialogDescription className="text-[11px] leading-snug">
              Branch: <strong>{branch?.name || '—'}</strong>.
              Owner PIN and TOTP work anywhere; Manager PIN only works for this branch's manager.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                PIN or 6-digit Authenticator code
              </Label>
              <Input
                ref={inputRef}
                type="password"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => { setCode(e.target.value); setError(''); }}
                onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
                placeholder="••••"
                className="mt-1 h-10 text-lg font-mono tracking-widest text-center"
                data-testid="pos-history-pin-input"
                disabled={verifying}
              />
            </div>
            {error && (
              <div className="flex items-start gap-1.5 text-[11px] text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1.5">
                <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => setOpen(false)}
                disabled={verifying}
              >
                Cancel
              </Button>
              <Button
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                onClick={submit}
                disabled={verifying || !code.trim()}
                data-testid="pos-history-pin-submit"
              >
                {verifying
                  ? <><RefreshCw size={14} className="mr-1.5 animate-spin" /> Verifying…</>
                  : <>Unlock</>}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
