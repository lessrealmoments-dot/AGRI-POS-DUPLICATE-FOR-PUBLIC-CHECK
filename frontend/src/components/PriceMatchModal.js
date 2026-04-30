import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Textarea } from './ui/textarea';
import { Tag, AlertTriangle, ShieldCheck } from 'lucide-react';
import { formatPHP } from '../lib/utils';

const REASONS = [
  { key: 'competitor_match',    label: 'Competitor price match' },
  { key: 'loyal_customer',      label: 'Bulk / Loyal customer' },
  { key: 'promotional_offer',   label: 'Promotional offer' },
  { key: 'old_stock_clearance', label: 'Damaged / Old stock clearance' },
  { key: 'other',               label: 'Other (specify)' },
];

/**
 * PriceMatchModal — collects per-line reason + manager/admin PIN to authorize
 * permanent branch-price changes triggered from the cart.
 *
 * Props:
 *   open: boolean
 *   priceChanges: [{ product_id, product_name, old_price, new_price, scheme }]
 *   schemeName: human-readable scheme name (e.g., "Retail")
 *   branchName: branch name for context
 *   onConfirm({ price_changes, pin }): called when user confirms with valid input
 *   onCancel(): called when user dismisses
 *   submitting: boolean — disable while parent processes
 *   error: optional error string from parent (e.g. invalid PIN response)
 */
export default function PriceMatchModal({
  open, priceChanges = [], schemeName = '', branchName = '',
  onConfirm, onCancel, submitting = false, error = '',
}) {
  // Default reason is 'competitor_match' — most common
  const [reasons, setReasons] = useState(() =>
    priceChanges.reduce((acc, pc) => ({ ...acc, [pc.product_id]: 'competitor_match' }), {})
  );
  const [details, setDetails] = useState({});
  const [pin, setPin] = useState('');
  const [localErr, setLocalErr] = useState('');

  if (!open) return null;

  const handleConfirm = () => {
    setLocalErr('');
    if (!pin.trim()) { setLocalErr('Manager / Admin PIN required'); return; }
    // Build payload — every line must have a reason
    const payload = priceChanges.map(pc => {
      const r = reasons[pc.product_id] || 'competitor_match';
      const d = (details[pc.product_id] || '').trim();
      if (r === 'other' && !d) {
        throw new Error(`Please specify a reason for "${pc.product_name}"`);
      }
      return {
        product_id: pc.product_id,
        scheme: pc.scheme,
        old_price: pc.old_price,
        new_price: pc.new_price,
        reason: r,
        reason_detail: d,
      };
    });
    try {
      onConfirm({ price_changes: payload, pin: pin.trim() });
    } catch (e) {
      setLocalErr(e.message || 'Validation failed');
    }
  };

  const totalDelta = priceChanges.reduce((s, pc) => s + (pc.new_price - pc.old_price), 0);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !submitting) onCancel(); }}>
      <DialogContent className="max-w-2xl" data-testid="price-match-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-700">
            <Tag size={18} /> Confirm Price Match — Permanent Change
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-500">
            These prices will be saved to <span className="font-semibold">{branchName || 'this branch'}</span>
            {' '}({schemeName} scheme) and apply to all future sales until changed again.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <div className="bg-amber-50 border border-amber-200 rounded p-2 flex items-start gap-2">
            <AlertTriangle size={14} className="text-amber-600 mt-0.5 shrink-0" />
            <p className="text-xs text-amber-700 leading-relaxed">
              Each price change is logged with reason + approver. View under
              <span className="italic"> Reports → Price Changes</span> to monitor competitor pricing trends.
            </p>
          </div>

          <div className="space-y-2 max-h-[40vh] overflow-y-auto pr-1">
            {priceChanges.map((pc) => {
              const reason = reasons[pc.product_id] || 'competitor_match';
              return (
                <div
                  key={pc.product_id}
                  className="border border-slate-200 rounded p-2.5 space-y-2"
                  data-testid={`price-match-line-${pc.product_id}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-slate-800 truncate" title={pc.product_name}>
                        {pc.product_name}
                      </p>
                      <p className="text-[11px] text-slate-500">
                        <span className="line-through">{formatPHP(pc.old_price)}</span>
                        {' → '}
                        <span className="font-bold text-amber-700">{formatPHP(pc.new_price)}</span>
                        <span className={`ml-2 ${pc.new_price < pc.old_price ? 'text-red-600' : 'text-emerald-600'}`}>
                          ({pc.new_price < pc.old_price ? '-' : '+'}
                          {formatPHP(Math.abs(pc.new_price - pc.old_price))},
                          {' '}
                          {pc.old_price > 0 ? `${(((pc.new_price - pc.old_price) / pc.old_price) * 100).toFixed(1)}%` : '—'})
                        </span>
                      </p>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-start">
                    <div className="sm:col-span-2">
                      <Label className="text-[10px] uppercase text-slate-500">Reason</Label>
                      <Select
                        value={reason}
                        onValueChange={(v) => setReasons({ ...reasons, [pc.product_id]: v })}
                      >
                        <SelectTrigger
                          className="h-8 text-xs"
                          data-testid={`price-match-reason-${pc.product_id}`}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {REASONS.map(r => (
                            <SelectItem key={r.key} value={r.key} className="text-xs">{r.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="sm:col-span-3">
                      <Label className="text-[10px] uppercase text-slate-500">
                        Detail {reason === 'other' ? '(required)' : '(optional)'}
                      </Label>
                      <Input
                        className="h-8 text-xs"
                        placeholder={
                          reason === 'competitor_match' ? 'e.g., Robinsons same SKU at ₱950' :
                          reason === 'other' ? 'Please specify' : 'Optional note'
                        }
                        value={details[pc.product_id] || ''}
                        onChange={e => setDetails({ ...details, [pc.product_id]: e.target.value })}
                        data-testid={`price-match-detail-${pc.product_id}`}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="bg-slate-50 border border-slate-200 rounded p-2 flex justify-between items-center text-xs">
            <span className="text-slate-600">{priceChanges.length} item(s) · Net change</span>
            <span className={`font-mono font-bold ${totalDelta < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
              {totalDelta < 0 ? '-' : '+'}{formatPHP(Math.abs(totalDelta))}
            </span>
          </div>

          <div>
            <Label className="text-xs flex items-center gap-1 text-slate-700">
              <ShieldCheck size={13} /> Manager / Admin PIN
            </Label>
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
            className="bg-amber-600 hover:bg-amber-700 text-white"
            data-testid="price-match-confirm"
          >
            {submitting ? 'Verifying…' : 'Confirm & Apply'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
