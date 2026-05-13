/**
 * HistoricalCreditBanner — Phase 4 Cleanup presentational extraction.
 *
 * Slimmed in iter 245+: the banner used to host the Reason / Proof URL /
 * Notebook Reference inputs at the top of the page, which forced the
 * cashier to scroll past them every time and silently disabled the
 * Checkout confirm button when the reason was too short. The inputs
 * now live inside `HistoricalCreditDialog` (collected AT checkout), so
 * this component is now a compact notice strip only.
 *
 * Renders:
 *   • Red `[data-testid='backdated-non-credit-block']` when `blocked`
 *     is true — operator picked cash/digital/split/partial on a date >7
 *     days back. Tells them to switch to Credit or change the date.
 *   • Amber `[data-testid='historical-credit-banner']` when `enabled`
 *     is true — operator is in valid Historical Credit / Notebook AR
 *     mode (backdated > 7d AND payment_type=credit AND privileged
 *     role). The notice only explains what will happen; the actual
 *     inputs are collected in the checkout dialog.
 *   • Nothing when both are false.
 */
import React from 'react';
import { AlertTriangle, ShieldAlert } from 'lucide-react';
import { Card, CardContent } from './ui/card';

export default function HistoricalCreditBanner({ enabled, blocked, daysBack /* hc kept for back-compat callers */ }) {
  if (!enabled && !blocked) return null;

  return (
    <>
      {blocked && (
        <div className="px-1 pb-2" data-testid="backdated-non-credit-block">
          <Card className="border-2 border-red-300 bg-red-50">
            <CardContent className="p-2.5">
              <div className="flex items-start gap-2">
                <AlertTriangle className="text-red-600 shrink-0 mt-0.5" size={16} />
                <div className="text-[12px] text-red-800 leading-snug">
                  <strong>Backdated transactions older than 7 days are allowed for CREDIT only.</strong>{' '}
                  Cash, digital, split, or paid transactions must be recorded on the actual payment date.
                  Switch payment type to <em>Credit</em>, or change the Sale Date back to today.
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {enabled && (
        <div className="px-1 pb-2" data-testid="historical-credit-banner">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-amber-300 bg-amber-50">
            <ShieldAlert className="text-amber-600 shrink-0" size={14} />
            <p className="text-[11px] text-amber-900 leading-snug">
              <strong>Backdated Credit / Notebook AR mode</strong> — {daysBack} days back.
              You'll be asked for a reason and Owner/Admin TOTP approval at checkout. Today's cash &amp; old Z-reports are not affected.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
