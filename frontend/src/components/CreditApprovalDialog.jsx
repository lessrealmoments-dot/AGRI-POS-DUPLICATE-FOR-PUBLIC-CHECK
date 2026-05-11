/**
 * CreditApprovalDialog — Phase 4 Cleanup presentational extraction.
 *
 * Pure render of the "Authorization Required" dialog that previously
 * lived inline in `UnifiedSalesPage.js` (lines ~4279–4380).
 *
 * Behavior preserved verbatim:
 *   • Red "Credit Limit Exceeded" panel when `creditCheckResult.allowed === false`.
 *   • Amber "requires authorization" panel when `creditCheckResult.allowed === true`.
 *   • PIN/TOTP single input that accepts Admin PIN, Manager PIN, or TOTP.
 *   • 3-method chip row (informational).
 *   • `credit-pin-session-hint` shown when `pinSessionWarm=true` AND `pin`
 *     is non-empty (matches the inline check: `managerPin && isPinSessionWarm()`).
 *   • Offline bypass reason input (with copy) when `showOfflineReason=true`.
 *   • Enter key on PIN input triggers `onVerify` when `pin` is non-empty.
 *   • Cancel button calls `onCancel` (page clears pin+reason+closes).
 *   • Authorize button disabled until `pin` is non-empty AND `!saving`.
 *
 * Boundary discipline:
 *   • No state, no effects, no API call.
 *   • Submit handler (`verifyManagerPin`) stays page-owned; this component
 *     just calls `onVerify` when the user clicks Authorize or hits Enter.
 *   • All gate booleans (`pinSessionWarm`, `showOfflineReason`) are
 *     pre-computed by the page so the dialog never reads `paymentType`,
 *     `selectedCustomer`, `isOnline`, or `pinSessionRef`.
 */
import React from 'react';
import { AlertTriangle, CheckCircle2, Shield, Unlock, WifiOff } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Separator } from './ui/separator';
import { formatPHP } from '../lib/utils';

export default function CreditApprovalDialog({
  open,
  onOpenChange,
  creditCheckResult,
  balanceDue,
  pin,
  setPin,
  pinSessionWarm,
  reason,
  setReason,
  showOfflineReason,
  onVerify,
  onCancel,
  saving,
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <Shield className="text-amber-500" /> Authorization Required
          </DialogTitle>
          <DialogDescription>
            Credit/Partial sales require PIN or TOTP authorization
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Credit check result */}
          {creditCheckResult && !creditCheckResult.allowed && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm font-medium text-red-700 flex items-center gap-1">
                <AlertTriangle size={14} /> Credit Limit Exceeded
              </p>
              <div className="mt-2 space-y-1 text-xs text-red-600">
                <div className="flex justify-between"><span>Current Balance:</span><span>{formatPHP(creditCheckResult.currentBalance)}</span></div>
                <div className="flex justify-between"><span>This Sale:</span><span>{formatPHP(balanceDue)}</span></div>
                <div className="flex justify-between font-medium"><span>New Total:</span><span>{formatPHP(creditCheckResult.newTotal)}</span></div>
                <div className="flex justify-between"><span>Credit Limit:</span><span>{formatPHP(creditCheckResult.creditLimit)}</span></div>
                <Separator className="my-1" />
                <div className="flex justify-between font-bold text-red-700">
                  <span>Exceeded By:</span><span>{formatPHP(creditCheckResult.exceededBy)}</span>
                </div>
              </div>
            </div>
          )}

          {creditCheckResult?.allowed && (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
              <p className="text-sm text-amber-700">
                This credit sale of <strong>{formatPHP(balanceDue)}</strong> requires authorization.
              </p>
            </div>
          )}

          {/* PIN / TOTP Input — supports all configured methods */}
          <div>
            <Label>Authorization Code</Label>
            <p className="text-[10px] text-slate-400 mb-2">
              Enter Admin PIN, Manager PIN, or TOTP code from Authenticator app
            </p>
            {pinSessionWarm && (
              <p className="text-[10px] text-emerald-600 mb-1 flex items-center gap-1 font-medium" data-testid="credit-pin-session-hint">
                <Unlock size={10} /> PIN auto-filled from active session
              </p>
            )}
            <Input
              data-testid="manager-pin"
              type="password" autoComplete="new-password"
              value={pin}
              onChange={e => setPin(e.target.value)}
              placeholder="PIN or 6-digit TOTP code"
              className="text-center text-2xl tracking-widest h-14"
              onKeyDown={e => e.key === 'Enter' && pin && onVerify()}
            />
            <div className="flex flex-wrap gap-1.5 mt-2">
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-50 border border-emerald-200 text-emerald-700 font-medium">Admin PIN</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-50 border border-blue-200 text-blue-700 font-medium">Manager PIN</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-50 border border-purple-200 text-purple-700 font-medium">TOTP (Authenticator)</span>
            </div>
          </div>

          {/* Offline credit-sale: required reason for audit log */}
          {showOfflineReason && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
              <p className="text-[11px] font-semibold text-amber-800 flex items-center gap-1">
                <WifiOff size={11} /> Offline mode — Admin PIN or branch Manager PIN
              </p>
              <Label className="text-[11px]">Reason for offline credit (required)</Label>
              <Input
                data-testid="offline-bypass-reason"
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="e.g. Customer in a hurry, signed paper slip"
                className="h-9 text-xs bg-white"
              />
              <p className="text-[10px] text-amber-700 leading-relaxed">
                No signature can be captured offline. The bypass + reason is logged for audit when the device reconnects.
              </p>
            </div>
          )}

          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              data-testid="verify-pin"
              className="flex-1 bg-amber-500 hover:bg-amber-600 text-white"
              onClick={onVerify}
              disabled={!pin || saving}
            >
              <CheckCircle2 size={16} className="mr-2" /> Authorize Sale
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
