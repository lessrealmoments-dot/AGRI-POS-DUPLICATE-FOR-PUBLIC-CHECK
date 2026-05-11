/**
 * HistoricalCreditDialog — Phase 4 Cleanup presentational extraction.
 *
 * Pure render of the Historical Credit / Notebook AR commit dialog that
 * previously lived inline in `UnifiedSalesPage.js` (lines ~5825–6026).
 *
 * All state lives in `useHistoricalCredit` and is consumed via the `hc`
 * prop. The dialog does NOT own any state, does NOT call any API
 * directly, and does NOT know anything about Quick / Detailed mode.
 * The TOTP approval gate is enforced server-side; this component only
 * collects the typed code.
 *
 * Behavior preserved verbatim from the inline version:
 *   • Open state, close gating while `hc.committing` is true.
 *   • Customer-owes snapshot highlight rule (>50% of current balance OR
 *     >₱5,000 increment turns the panel red).
 *   • Count-sheet stopper banner + override checkbox; toggling the
 *     checkbox invalidates the preview via `hc.setPreview(null)` so the
 *     user must re-run preview after changing inventory intent.
 *   • Commit button gated on: preview present, approvalCode trimmed,
 *     reason >= 20 chars, not currently committing.
 *   • Enter key in TOTP input fires `hc.commit()`.
 */
import React from 'react';
import { AlertTriangle, ShieldAlert } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { formatPHP } from '../lib/utils';
import { localTodayStr } from '../lib/dateFormat';

export default function HistoricalCreditDialog({
  hc,
  customer,
  branch,
  orderDate,
  daysBack,
  itemsCount,
  grandTotal,
}) {
  return (
    <Dialog
      open={hc.dialogOpen}
      onOpenChange={(o) => {
        if (!hc.committing) {
          if (o) hc.setDialogOpen(true);
          else hc.closeDialog();
        }
      }}
    >
      <DialogContent data-testid="historical-credit-dialog" className="max-w-xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-800" style={{ fontFamily: 'Manrope' }}>
            <ShieldAlert className="text-amber-600" /> Historical Credit / Notebook AR
          </DialogTitle>
          <DialogDescription>
            You are about to create a backdated CREDIT transaction for AR
            reconstruction. This will not be treated as cash collected
            today and will not alter old closed Z-reports.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* Snapshot */}
          <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5 space-y-1 text-[12px]">
            <div className="flex justify-between">
              <span className="text-amber-700">Customer</span>
              <span className="font-medium text-amber-900">{customer?.name || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-amber-700">Branch</span>
              <span className="font-medium text-amber-900">{branch?.name || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-amber-700">Transaction date</span>
              <span className="font-medium text-amber-900">{orderDate} ({daysBack} days back)</span>
            </div>
            <div className="flex justify-between">
              <span className="text-amber-700">Will be encoded today as</span>
              <span className="font-medium text-amber-900">{localTodayStr()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-amber-700">Items</span>
              <span className="font-medium text-amber-900">{itemsCount}</span>
            </div>
            <div className="flex justify-between border-t border-amber-200 pt-1 mt-1">
              <span className="text-amber-700 font-semibold">Grand total (AR)</span>
              <span className="font-bold text-amber-900">{formatPHP(grandTotal)}</span>
            </div>
          </div>

          {/* Preview button */}
          {!hc.preview && (
            <div className="flex justify-end">
              <Button
                data-testid="historical-credit-preview-btn"
                size="sm"
                variant="outline"
                className="border-amber-400 text-amber-800 hover:bg-amber-100"
                disabled={hc.previewLoading || hc.reason.trim().length < 20}
                onClick={hc.runPreview}
              >
                {hc.previewLoading ? 'Previewing…' : 'Run Preview'}
              </Button>
            </div>
          )}

          {hc.previewError && (
            <p className="text-[11px] text-red-600 bg-red-50 border border-red-200 rounded p-2" data-testid="historical-credit-preview-error">
              {hc.previewError}
            </p>
          )}

          {/* Preview panel */}
          {hc.preview && (
            <div className="space-y-2">
              {/* Customer Owes Total Snapshot */}
              <div
                data-testid="historical-credit-customer-owes-snapshot"
                className={`rounded-md border p-2.5 text-[12px] space-y-1 ${
                  (hc.preview.customer?.projected_balance || 0)
                    - (hc.preview.customer?.current_balance || 0)
                    > Math.max(5000, (hc.preview.customer?.current_balance || 0) * 0.5)
                    ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-slate-50'
                }`}
              >
                <p className="text-[11px] font-semibold text-slate-700 uppercase tracking-wide">Customer Owes</p>
                <div className="flex justify-between">
                  <span className="text-slate-600">Current balance</span>
                  <span className="font-mono">{formatPHP(hc.preview.customer?.current_balance || 0)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">+ Historical credit</span>
                  <span className="font-mono text-amber-700">+ {formatPHP(hc.preview.grand_total || 0)}</span>
                </div>
                <div className="flex justify-between border-t pt-1 font-bold">
                  <span>Projected balance</span>
                  <span className="font-mono">{formatPHP(hc.preview.customer?.projected_balance || 0)}</span>
                </div>
              </div>

              {/* Count-sheet stopper */}
              {hc.preview.inventory_action === 'skipped_count_sheet_lock' && (
                <div className="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-[11px] text-amber-800 space-y-1.5" data-testid="historical-credit-count-stopper">
                  <p className="font-semibold flex items-center gap-1">
                    <AlertTriangle size={12} /> Inventory will NOT be deducted
                  </p>
                  <p>
                    This date is on or before the latest approved count
                    sheet ({hc.preview.count_sheet_stopper?.latest_count_date || 'n/a'}).
                    Inventory will not be deducted unless Admin / Owner
                    explicitly allows it. Otherwise this is recorded as
                    AR-only reconstruction.
                  </p>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      data-testid="historical-credit-allow-inv-checkbox"
                      checked={hc.allowInv}
                      onChange={e => {
                        hc.setAllowInv(e.target.checked);
                        // Re-preview to refresh inventory_action
                        hc.setPreview(null);
                      }}
                    />
                    <span>Override and deduct inventory anyway (with audit)</span>
                  </label>
                </div>
              )}

              {/* Closed-day note */}
              <div className="rounded-md border border-slate-200 bg-slate-50 p-2 text-[11px] text-slate-700" data-testid="historical-credit-report-effect">
                <p>
                  Old closed Z-reports: <strong>not modified</strong>. Today's
                  encoded-today report: <strong>will appear</strong>.
                  Today's cash collected: <strong>not changed</strong>.
                </p>
              </div>

              {/* TOTP input + commit */}
              <div className="rounded-md border-2 border-amber-400 bg-amber-50 p-3 space-y-2">
                <Label className="text-[11px] font-bold text-amber-900 uppercase tracking-wide">
                  Owner / Admin Authenticator (TOTP) Code <span className="text-red-600">*</span>
                </Label>
                <p className="text-[10px] text-amber-700 leading-snug">
                  Settings → Security → Authenticator App. Manager PIN and
                  static admin PIN are <strong>not</strong> accepted for
                  Historical Credit. Code is verified server-side.
                </p>
                <Input
                  data-testid="historical-credit-approval-code-input"
                  type="password"
                  inputMode="numeric"
                  autoComplete="off"
                  autoFocus
                  placeholder="6-digit TOTP"
                  value={hc.approvalCode}
                  onChange={e => hc.setApprovalCode(e.target.value.replace(/[^0-9]/g, '').slice(0, 8))}
                  className="text-center text-2xl tracking-widest h-12 bg-white"
                  onKeyDown={e => {
                    if (e.key === 'Enter' && hc.approvalCode && !hc.committing) {
                      hc.commit();
                    }
                  }}
                />
                {hc.commitError && (
                  <p className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded p-1.5" data-testid="historical-credit-commit-error">
                    {hc.commitError}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2 border-t">
          <Button
            variant="ghost"
            size="sm"
            data-testid="historical-credit-cancel-btn"
            disabled={hc.committing}
            onClick={() => hc.closeDialog()}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            data-testid="historical-credit-commit-btn"
            className="bg-amber-600 hover:bg-amber-700 text-white"
            disabled={
              !hc.preview
              || !hc.approvalCode.trim()
              || hc.reason.trim().length < 20
              || hc.committing
            }
            onClick={hc.commit}
          >
            {hc.committing ? 'Committing…' : 'Commit Historical Credit'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
