/**
 * HistoricalCreditBanner — Phase 4 Cleanup presentational extraction.
 *
 * Pure render of the two Historical Credit / Notebook AR banners that
 * previously lived inline in `UnifiedSalesPage.js` (lines ~3566–3646).
 *
 * Renders:
 *   • Red `[data-testid='backdated-non-credit-block']` when `blocked` is true.
 *   • Amber `[data-testid='historical-credit-banner']` when `enabled` is true.
 *   • Nothing when both are false.
 *
 * All state (`hc.reason`, `hc.proofUrl`, `hc.notebookRef`) and all setters
 * are owned by `useHistoricalCredit`. This component is pure presentation
 * and does NOT call any API, hold any state, or know anything about the
 * Quick / Detailed mode or the `mainTab` gate (caller decides whether to
 * mount the component).
 */
import React from 'react';
import { AlertTriangle, ShieldAlert } from 'lucide-react';
import { Card, CardContent } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';

export default function HistoricalCreditBanner({ enabled, blocked, daysBack, hc }) {
  if (!enabled && !blocked) return null;

  return (
    <>
      {blocked && (
        <div className="px-1 pb-3" data-testid="backdated-non-credit-block">
          <Card className="border-2 border-red-300 bg-red-50">
            <CardContent className="p-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="text-red-600 shrink-0 mt-0.5" size={18} />
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
        <div className="px-1 pb-3" data-testid="historical-credit-banner">
          <Card className="border-2 border-amber-400 bg-amber-50">
            <CardContent className="p-3 space-y-2">
              <div className="flex items-start gap-2">
                <ShieldAlert className="text-amber-600 shrink-0 mt-0.5" size={20} />
                <div className="space-y-1">
                  <p className="text-sm font-bold text-amber-900 tracking-tight">
                    BACKDATED CREDIT / NOTEBOOK AR MODE — {daysBack} days back
                  </p>
                  <p className="text-[11px] text-amber-800 leading-snug">
                    You are encoding an old credit transaction. This will be
                    recorded as historical credit / AR reconstruction. It will
                    not be treated as cash collected today and will not modify
                    old closed Z-reports. Company Owner / Admin Authenticator
                    App (TOTP) approval is required.
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pt-1">
                <div className="md:col-span-2">
                  <Label className="text-[11px] font-semibold text-amber-900">
                    Reason (min 20 characters) <span className="text-red-600">*</span>
                  </Label>
                  <textarea
                    data-testid="historical-credit-reason-input"
                    value={hc.reason}
                    onChange={e => hc.setReason(e.target.value)}
                    placeholder="Notebook AR carry-forward verified against ledger page 12, customer countersigned 2026-02-04."
                    className="w-full text-[12px] rounded border border-amber-300 bg-white px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-amber-400 min-h-[60px] mt-0.5"
                  />
                  <p className={`text-[10px] mt-0.5 ${hc.reason.trim().length >= 20 ? 'text-emerald-700' : 'text-amber-700'}`}>
                    {hc.reason.trim().length} / 20 minimum
                  </p>
                </div>
                <div>
                  <Label className="text-[11px] font-semibold text-amber-900">
                    Proof URL (optional)
                  </Label>
                  <Input
                    data-testid="historical-credit-proof-url-input"
                    value={hc.proofUrl}
                    onChange={e => hc.setProofUrl(e.target.value)}
                    placeholder="https://… (photo of notebook page)"
                    className="h-8 text-[12px] mt-0.5 bg-white"
                  />
                </div>
                <div>
                  <Label className="text-[11px] font-semibold text-amber-900">
                    Notebook reference (optional)
                  </Label>
                  <Input
                    data-testid="historical-credit-notebook-ref-input"
                    value={hc.notebookRef}
                    onChange={e => hc.setNotebookRef(e.target.value)}
                    placeholder="Ledger 2025 — Page 12, Row 4"
                    className="h-8 text-[12px] mt-0.5 bg-white"
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
