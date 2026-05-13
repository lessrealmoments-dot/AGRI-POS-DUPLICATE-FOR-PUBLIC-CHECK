/**
 * ConfirmQuantitiesDialog — Phase 1 — Stock Request Confirmation Layer.
 *
 * Lets the supply branch manager (or admin/owner) confirm per-line
 * `approved_qty` for a stock request BEFORE a BTO is created. NO stock
 * movement happens here — confirmation only adjusts intent and writes
 * an append-only audit row server-side.
 *
 * Props:
 *   open                 — boolean
 *   onOpenChange(bool)
 *   request              — the incoming-request PO dict (must include
 *                          { id, po_number, items, branch_id (requester),
 *                            supply_branch_id, approval_status?, ... })
 *   requestingBranchName — display string for "from"
 *   supplyBranchName     — display string for "to"
 *   onConfirmed(result)  — called with the server response
 */
import React, { useState, useEffect, useMemo } from 'react';
import { api } from '../contexts/AuthContext';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { ShieldCheck, AlertTriangle, ArrowRight, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const fmtQty = (v) => {
  const n = parseFloat(v);
  if (isNaN(n)) return '0';
  return parseFloat(n.toFixed(3)).toString();
};

export default function ConfirmQuantitiesDialog({
  open,
  onOpenChange,
  request,
  requestingBranchName = '',
  supplyBranchName = '',
  onConfirmed,
}) {
  const [lines, setLines] = useState([]);
  const [globalNote, setGlobalNote] = useState('');
  const [pin, setPin] = useState('');
  const [saving, setSaving] = useState(false);

  // Reset state every time the dialog opens with a new request.
  useEffect(() => {
    if (!open || !request) return;
    const seed = (request.items || []).map((it) => {
      const requested = parseFloat(it.quantity || 0);
      const existing = it.approved_qty;
      const hasExisting = existing !== null && existing !== undefined && existing !== '';
      return {
        product_id: it.product_id || '',
        product_name: it.product_name || '',
        unit: it.unit || '',
        requested_qty: requested,
        approved_qty: hasExisting ? String(existing) : String(requested),
        approved_note: it.approved_note || '',
      };
    });
    setLines(seed);
    setGlobalNote(request.approval_note || '');
    setPin('');
  }, [open, request?.id]); // eslint-disable-line

  const summary = useMemo(() => {
    let full = 0, partial = 0, excess = 0, declined = 0;
    let excessMissingNote = 0;
    for (const l of lines) {
      const a = parseFloat(l.approved_qty || 0);
      const r = parseFloat(l.requested_qty || 0);
      if (a === 0) declined += 1;
      else if (a < r) partial += 1;
      else if (a > r) {
        excess += 1;
        if (!l.approved_note.trim()) excessMissingNote += 1;
      } else full += 1;
    }
    return { full, partial, excess, declined, excessMissingNote };
  }, [lines]);

  const blocked = summary.excessMissingNote > 0 && !globalNote.trim();
  const disableSubmit = (
    saving || !pin.trim() || lines.length === 0 || blocked
  );

  const setLine = (idx, patch) => {
    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)));
  };

  const handleSubmit = async () => {
    if (disableSubmit) return;
    setSaving(true);
    try {
      const body = {
        pin: pin.trim(),
        approval_note: globalNote.trim(),
        items: lines.map((l) => ({
          product_id: l.product_id,
          approved_qty: parseFloat(l.approved_qty || 0),
          approved_note: l.approved_note.trim(),
        })),
      };
      const res = await api.post(
        `/purchase-orders/${request.id}/confirm-request`,
        body,
      );
      toast.success(`Quantities confirmed (${res.data.approval_status}).`);
      if (onConfirmed) onConfirmed(res.data);
      onOpenChange(false);
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || 'Failed to confirm.';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  if (!request) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-3xl max-h-[90vh] overflow-y-auto"
        data-testid="confirm-quantities-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck size={18} className="text-violet-700" />
            Confirm Quantities — {request.po_number}
          </DialogTitle>
          <DialogDescription>
            Decide what to ship. The original requested quantity is locked;
            only your approved quantity moves forward into the Branch
            Transfer.
          </DialogDescription>
        </DialogHeader>

        <div className="text-sm text-slate-600 flex items-center gap-2 mt-1">
          <span className="font-semibold text-slate-800">{requestingBranchName || 'Requester'}</span>
          <ArrowRight size={14} className="text-slate-400" />
          <span className="font-semibold text-slate-800">{supplyBranchName || 'Supply'}</span>
        </div>

        <div className="rounded-md border border-slate-200 mt-3 overflow-hidden">
          <div className="grid grid-cols-12 gap-2 px-3 py-2 bg-slate-50 text-xs font-semibold text-slate-600">
            <div className="col-span-5">Product</div>
            <div className="col-span-2 text-right">Requested</div>
            <div className="col-span-2 text-right">Approved</div>
            <div className="col-span-3">Per-line note</div>
          </div>
          {lines.map((l, idx) => {
            const a = parseFloat(l.approved_qty || 0);
            const r = parseFloat(l.requested_qty || 0);
            const isExcess = a > r;
            const isPartial = a < r && a > 0;
            const isDeclined = a === 0;
            const needsNote = isExcess && !l.approved_note.trim() && !globalNote.trim();
            return (
              <div
                key={l.product_id || idx}
                className="grid grid-cols-12 gap-2 px-3 py-2 border-t border-slate-100 items-center"
                data-testid={`confirm-line-${idx}`}
              >
                <div className="col-span-5">
                  <div className="text-sm font-medium text-slate-800">{l.product_name}</div>
                  <div className="text-xs text-slate-400">{l.unit}</div>
                </div>
                <div className="col-span-2 text-right">
                  <span className="font-mono text-sm text-slate-700">{fmtQty(r)}</span>
                </div>
                <div className="col-span-2">
                  <Input
                    type="number"
                    min={0}
                    step="any"
                    value={l.approved_qty}
                    onChange={(e) => setLine(idx, { approved_qty: e.target.value })}
                    className={`h-9 text-right font-mono ${needsNote ? 'border-amber-400' : ''}`}
                    data-testid={`approved-qty-${idx}`}
                  />
                  <div className="mt-1 flex justify-end gap-1">
                    {isExcess && (
                      <Badge className="text-[10px] bg-amber-100 text-amber-700">excess</Badge>
                    )}
                    {isPartial && (
                      <Badge className="text-[10px] bg-yellow-100 text-yellow-700">partial</Badge>
                    )}
                    {isDeclined && (
                      <Badge className="text-[10px] bg-rose-100 text-rose-700">declined</Badge>
                    )}
                    {!isExcess && !isPartial && !isDeclined && (
                      <Badge className="text-[10px] bg-emerald-100 text-emerald-700">full</Badge>
                    )}
                  </div>
                </div>
                <div className="col-span-3">
                  <Input
                    type="text"
                    placeholder={isExcess ? 'Required (excess)' : 'Optional'}
                    value={l.approved_note}
                    onChange={(e) => setLine(idx, { approved_note: e.target.value })}
                    className={`h-9 text-xs ${needsNote ? 'border-amber-400' : ''}`}
                    data-testid={`approved-note-${idx}`}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {summary.excess > 0 && (
          <div className="mt-3 text-xs flex items-start gap-2 text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
            <AlertTriangle size={14} className="mt-0.5" />
            <div>
              <p className="font-semibold">Approving more than requested</p>
              <p>
                {summary.excess} line(s) approved above the requested quantity.
                Add a note (per-line or below) to document the business
                reason — strategic top-up, owner override, branch critically
                low, etc.
              </p>
            </div>
          </div>
        )}

        <div className="mt-3">
          <Label className="text-xs">Overall approval note (optional)</Label>
          <Input
            type="text"
            value={globalNote}
            onChange={(e) => setGlobalNote(e.target.value)}
            placeholder="e.g. Owner-approved top-up for delivery timing"
            className="mt-1"
            data-testid="approval-note"
          />
        </div>

        <div className="mt-3 grid grid-cols-12 gap-3 items-end">
          <div className="col-span-7">
            <Label className="text-xs">
              PIN / TOTP (Supply branch manager, admin, or owner)
            </Label>
            <Input
              type="password"
              autoComplete="off"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="Manager PIN or 6-digit TOTP"
              className="mt-1 font-mono"
              data-testid="confirm-pin"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !disableSubmit) handleSubmit();
              }}
            />
          </div>
          <div className="col-span-5 flex justify-end gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
              data-testid="confirm-cancel-btn"
            >
              Cancel
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={disableSubmit}
              className="bg-violet-700 hover:bg-violet-800 text-white"
              data-testid="confirm-submit-btn"
            >
              {saving ? (
                <>
                  <RefreshCw size={14} className="animate-spin mr-2" />
                  Confirming…
                </>
              ) : (
                <>
                  <ShieldCheck size={14} className="mr-2" />
                  Confirm Quantities
                </>
              )}
            </Button>
          </div>
        </div>

        {blocked && (
          <p className="mt-2 text-xs text-rose-700" data-testid="confirm-blocked-hint">
            A note is required on at least one excess line (or overall) before confirmation.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
