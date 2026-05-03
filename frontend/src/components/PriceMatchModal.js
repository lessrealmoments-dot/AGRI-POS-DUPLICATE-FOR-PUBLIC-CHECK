import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Tag, AlertTriangle, ShieldCheck, User, Globe } from 'lucide-react';
import { formatPHP } from '../lib/utils';

const REASONS = [
  { key: 'competitor_match',    label: 'Competitor price match' },
  { key: 'loyal_customer',      label: 'Bulk / Loyal customer' },
  { key: 'promotional_offer',   label: 'Promotional offer' },
  { key: 'old_stock_clearance', label: 'Damaged / Old stock clearance' },
  { key: 'other',               label: 'Other (specify)' },
];

/**
 * PriceMatchModal — collects ONE reason + scope choice + PIN for the whole
 * receipt (every changed line shares the decision).
 *
 * Scope:
 *   • update_branch  (default): new prices are saved to branch_prices and
 *     apply to every future sale at this branch — true Price Match.
 *   • customer_only            : new prices apply ONLY to this sale. The
 *     branch catalog stays untouched. Still PIN-gated, still audit-logged
 *     under price_change_log with customer_only=true.
 *
 * Props:
 *   open: boolean
 *   priceChanges: [{ product_id, product_name, old_price, new_price, scheme }]
 *   schemeName: human-readable scheme name (e.g., "Retail")
 *   branchName: branch name for context
 *   customerName: shown when scope = customer_only
 *   onConfirm({ price_changes, pin }): called when user confirms
 *   onCancel(): called when user dismisses
 *   submitting: boolean — disable while parent processes
 *   error: optional error string from parent (e.g. invalid PIN response)
 */
export default function PriceMatchModal({
  open, priceChanges = [], schemeName = '', branchName = '', customerName = '',
  onConfirm, onCancel, submitting = false, error = '', warmPin = null,
}) {
  const [reason, setReason] = useState('competitor_match');
  const [reasonDetail, setReasonDetail] = useState('');
  // Scope: 'update_branch' (legacy default) | 'customer_only'
  const [scope, setScope] = useState('update_branch');
  const [pin, setPin] = useState('');
  const [localErr, setLocalErr] = useState('');

  // Auto-fill PIN from warm session when modal opens
  useEffect(() => {
    if (open && warmPin && !pin) setPin(warmPin);
  }, [open, warmPin]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const handleConfirm = () => {
    setLocalErr('');
    if (!pin.trim()) { setLocalErr('Manager / Admin PIN required'); return; }
    if (reason === 'other' && !reasonDetail.trim()) {
      setLocalErr('Please specify the reason in the Detail field');
      return;
    }
    const isCustomerOnly = scope === 'customer_only';
    // Apply the same reason / scope to every changed line — single decision
    // for the whole receipt.
    const payload = priceChanges.map(pc => ({
      product_id: pc.product_id,
      scheme: pc.scheme,
      old_price: pc.old_price,
      new_price: pc.new_price,
      reason,
      reason_detail: reasonDetail.trim(),
      customer_only: isCustomerOnly,
    }));
    onConfirm({ price_changes: payload, pin: pin.trim() });
  };

  const totalDelta = priceChanges.reduce((s, pc) => s + (pc.new_price - pc.old_price), 0);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !submitting) onCancel(); }}>
      <DialogContent className="max-w-xl" data-testid="price-match-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-700">
            <Tag size={18} /> Confirm Price Change
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-500">
            {priceChanges.length} item{priceChanges.length === 1 ? '' : 's'} on this receipt
            {' · '}{schemeName} scheme · {branchName || 'this branch'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Changed lines — read-only summary */}
          <div className="space-y-1 max-h-[28vh] overflow-y-auto pr-1 border border-slate-100 rounded p-2 bg-slate-50/60">
            {priceChanges.map((pc) => (
              <div key={pc.product_id} className="flex items-center justify-between text-xs py-0.5"
                   data-testid={`price-match-line-${pc.product_id}`}>
                <span className="font-medium text-slate-800 truncate flex-1 mr-2" title={pc.product_name}>
                  {pc.product_name}
                </span>
                <span className="font-mono text-slate-500 shrink-0">
                  {pc.old_price > 0 ? (
                    <>
                      <span className="line-through">{formatPHP(pc.old_price)}</span>
                      {' → '}
                      <span className="font-bold text-amber-700">{formatPHP(pc.new_price)}</span>
                      <span className={`ml-2 ${pc.new_price < pc.old_price ? 'text-red-600' : 'text-emerald-600'}`}>
                        ({pc.new_price < pc.old_price ? '-' : '+'}{formatPHP(Math.abs(pc.new_price - pc.old_price))})
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-[10px] uppercase tracking-wider text-amber-600 mr-1.5">first-time pricing</span>
                      <span className="font-bold text-amber-700">{formatPHP(pc.new_price)}</span>
                    </>
                  )}
                </span>
              </div>
            ))}
            <div className="flex justify-between items-center text-[11px] pt-1.5 mt-1 border-t border-slate-200">
              <span className="text-slate-600">{priceChanges.length} item(s) · Net change</span>
              <span className={`font-mono font-bold ${totalDelta < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {totalDelta < 0 ? '-' : '+'}{formatPHP(Math.abs(totalDelta))}
              </span>
            </div>
          </div>

          {/* Single reason for the whole receipt */}
          <div className="grid grid-cols-1 sm:grid-cols-5 gap-2">
            <div className="sm:col-span-2">
              <Label className="text-[10px] uppercase text-slate-500">Reason for change</Label>
              <select
                value={reason}
                onChange={e => setReason(e.target.value)}
                className="w-full h-9 px-2 text-xs border border-slate-200 rounded focus:outline-none focus:border-[#1A4D2E] bg-white"
                data-testid="price-match-reason"
              >
                {REASONS.map(r => (
                  <option key={r.key} value={r.key}>{r.label}</option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-3">
              <Label className="text-[10px] uppercase text-slate-500">
                Detail {reason === 'other' ? '(required)' : '(optional)'}
              </Label>
              <Input
                className="h-9 text-xs"
                placeholder={
                  reason === 'competitor_match' ? 'e.g., Robinsons same SKU at ₱950' :
                  reason === 'other' ? 'Please specify' : 'Optional note'
                }
                value={reasonDetail}
                onChange={e => setReasonDetail(e.target.value)}
                data-testid="price-match-reason-detail"
              />
            </div>
          </div>

          {/* Scope choice — UPDATE branch price vs CUSTOMER ONLY */}
          <div>
            <Label className="text-[10px] uppercase text-slate-500 block mb-1.5">Apply to</Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setScope('update_branch')}
                data-testid="price-match-scope-update-branch"
                className={`text-left p-3 rounded border-2 transition-all ${
                  scope === 'update_branch'
                    ? 'border-amber-500 bg-amber-50/70 shadow-sm'
                    : 'border-slate-200 hover:border-slate-300 bg-white'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Globe size={14} className="text-amber-600" />
                  <span className="text-sm font-semibold text-slate-800">Update branch price</span>
                </div>
                <p className="text-[10px] text-slate-500 leading-snug">
                  Permanent — applies to <b>all future sales</b> at {branchName || 'this branch'}.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setScope('customer_only')}
                data-testid="price-match-scope-customer-only"
                className={`text-left p-3 rounded border-2 transition-all ${
                  scope === 'customer_only'
                    ? 'border-blue-500 bg-blue-50/70 shadow-sm'
                    : 'border-slate-200 hover:border-slate-300 bg-white'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <User size={14} className="text-blue-600" />
                  <span className="text-sm font-semibold text-slate-800">Skip change for now</span>
                </div>
                <p className="text-[10px] text-slate-500 leading-snug">
                  This sale only{customerName ? ` (${customerName})` : ''}. Branch catalog stays the same.
                </p>
              </button>
            </div>
          </div>

          {/* Audit reminder */}
          <div className="bg-amber-50 border border-amber-200 rounded p-2 flex items-start gap-2">
            <AlertTriangle size={14} className="text-amber-600 mt-0.5 shrink-0" />
            <p className="text-[11px] text-amber-700 leading-relaxed">
              Both options are logged with reason + approver under <span className="italic">Reports → Price Changes</span>.
              {!navigator.onLine && (
                <span className="block mt-1 text-amber-800 font-medium">
                  ⚠ Offline — PIN is verified locally. The price change will sync to the server when your connection is back.
                </span>
              )}
            </p>
          </div>

          {/* PIN */}
          <div>
            <Label className="text-xs flex items-center gap-1 text-slate-700">
              <ShieldCheck size={13} /> Manager / Admin PIN
            </Label>
            {warmPin && pin === warmPin && (
              <p className="text-[10px] text-emerald-600 mt-0.5 flex items-center gap-1 font-medium">
                <ShieldCheck size={10} /> PIN auto-filled from active session
              </p>
            )}
            <Input
              type="password"
              inputMode="numeric"
              autoFocus
              value={pin}
              onChange={e => setPin(e.target.value)}
              className="h-9 mt-1 font-mono"
              placeholder="Enter PIN"
              data-testid="price-match-pin-input"
              onKeyDown={e => { if (e.key === 'Enter') handleConfirm(); }}
            />
            <p className="text-[10px] text-slate-500 mt-1">
              TOTP, Manager PIN, or Admin PIN accepted (per Settings → Security).
            </p>
          </div>

          {(error || localErr) && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1.5">
              {localErr || error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onCancel} disabled={submitting} data-testid="price-match-cancel">
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={submitting || !pin.trim()}
            className={scope === 'customer_only' ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-amber-600 hover:bg-amber-700 text-white'}
            data-testid="price-match-confirm"
          >
            {submitting ? 'Verifying…' : scope === 'customer_only' ? 'Apply for this sale only' : 'Update branch price'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
