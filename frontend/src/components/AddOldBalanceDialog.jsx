/**
 * AddOldBalanceDialog — lightweight UI for stamping a previously-unencoded
 * notebook AR balance onto an existing customer.
 *
 * Use cases this addresses:
 *   1. Customer opened with the wrong opening balance (or none) and you
 *      later discover legacy debt from a notebook.
 *   2. Opening balance was already set but more old credit was found.
 *
 * This dialog DOES NOT introduce a new backend pathway. It posts to the
 * existing `/api/historical-credit` endpoint with a single-line lump-sum
 * item ("Notebook AR — opening / additional"). The server-side guarantees
 * still apply:
 *   • admin/owner role required
 *   • Authenticator App (TOTP) code required (manager PIN rejected)
 *   • transaction_date must be ≥ 8 days in the past (soft floor — recent
 *     credit should use the normal POS path, not this one)
 *   • reason ≥ 20 chars
 *   • inserts an invoice with source = "historical_credit_encoding",
 *     increments customer.balance, writes late_encode_log + security_events
 */
import React, { useEffect, useMemo, useState } from 'react';
import { ShieldAlert, AlertTriangle } from 'lucide-react';
import { api } from '../contexts/AuthContext';
import { toast } from 'sonner';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';
import { formatPHP } from '../lib/utils';

const MIN_REASON_LEN = 20;
const SOFT_FLOOR_DAYS = 8;   // server-side soft floor is 7 days; we surface 8 to keep one-day buffer

function isoToday() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}
function isoDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function AddOldBalanceDialog({ open, onOpenChange, customer, onCommitted }) {
  const [amount, setAmount]               = useState('');
  const [txnDate, setTxnDate]             = useState(isoDaysAgo(30));
  const [reason, setReason]               = useState('');
  const [notebookRef, setNotebookRef]     = useState('');
  const [approvalCode, setApprovalCode]   = useState('');
  const [committing, setCommitting]       = useState(false);

  // Reset on open/close
  useEffect(() => {
    if (open) {
      setAmount('');
      setTxnDate(isoDaysAgo(30));
      setReason('');
      setNotebookRef('');
      setApprovalCode('');
      setCommitting(false);
    }
  }, [open]);

  const amt = parseFloat(amount) || 0;
  const daysBack = useMemo(() => {
    if (!txnDate) return 0;
    const t = new Date(txnDate);
    const today = new Date(isoToday());
    return Math.floor((today - t) / 86400000);
  }, [txnDate]);

  const dateOk    = daysBack >= SOFT_FLOOR_DAYS;
  const amountOk  = amt > 0;
  const reasonOk  = reason.trim().length >= MIN_REASON_LEN;
  const codeOk    = approvalCode.trim().length >= 6;
  const canSubmit = amountOk && dateOk && reasonOk && codeOk && !committing;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setCommitting(true);
    try {
      const payload = {
        customer_id: customer.id,
        branch_id: customer.branch_id,
        transaction_date: txnDate,
        grand_total: amt,
        subtotal: amt,
        freight: 0,
        overall_discount: 0,
        reason: reason.trim(),
        notebook_reference: notebookRef.trim() || undefined,
        allow_inventory_deduction: false,   // never deduct inventory — pure AR reconstruction
        items: [{
          product_id: null,
          product_name: 'Notebook AR — opening / additional',
          quantity: 1,
          rate: amt,
          unit_price: amt,
          price: amt,
          total: amt,
          discount_amount: 0,
        }],
        approval_code: approvalCode.trim(),
      };
      await api.post('/historical-credit', payload);
      toast.success(`Added ${formatPHP(amt)} to ${customer.name}'s balance.`);
      onCommitted && onCommitted();
      onOpenChange(false);
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string'
        ? detail
        : (detail?.message || detail?.error || (detail?.errors && detail.errors.join('; ')) || 'Failed to add balance');
      toast.error(msg);
    } finally {
      setCommitting(false);
    }
  };

  if (!customer) return null;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!committing) onOpenChange(o); }}>
      <DialogContent data-testid="add-old-balance-dialog" className="max-w-md max-h-[92vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-800">
            <ShieldAlert className="text-amber-600" /> Add Old Balance
          </DialogTitle>
          <DialogDescription>
            Stamp a previously-unencoded notebook AR amount onto this customer.
            The amount becomes a backdated <strong>credit</strong> invoice — it
            increases the customer's balance, but is NOT counted as cash
            collected today and does NOT touch old closed Z-reports.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {/* Customer snapshot */}
          <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5 text-[12px] space-y-1">
            <div className="flex justify-between"><span className="text-amber-700">Customer</span>
              <span className="font-medium text-amber-900">{customer.name}</span></div>
            <div className="flex justify-between"><span className="text-amber-700">Current balance</span>
              <span className={`font-semibold ${customer.balance > 0 ? 'text-red-700' : 'text-emerald-700'}`}>
                {formatPHP(customer.balance || 0)}
              </span></div>
            <div className="flex justify-between"><span className="text-amber-700">Credit limit</span>
              <span className="font-medium text-amber-900">{formatPHP(customer.credit_limit || 0)}</span></div>
            {amountOk && (
              <div className="flex justify-between border-t border-amber-200 pt-1 mt-1">
                <span className="text-amber-700 font-semibold">New balance will be</span>
                <span className="font-bold text-amber-900">{formatPHP((customer.balance || 0) + amt)}</span>
              </div>
            )}
          </div>

          {/* Amount */}
          <div>
            <Label className="text-xs">Amount (₱) <span className="text-red-500">*</span></Label>
            <Input data-testid="aob-amount" type="number" inputMode="decimal" min="0" step="0.01"
              value={amount} onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00" className="h-10" />
          </div>

          {/* Transaction date */}
          <div>
            <Label className="text-xs">Transaction date (when the credit was originally given) <span className="text-red-500">*</span></Label>
            <Input data-testid="aob-date" type="date" value={txnDate} max={isoDaysAgo(SOFT_FLOOR_DAYS)}
              onChange={(e) => setTxnDate(e.target.value)} className="h-10" />
            {!dateOk && (
              <p className="text-[11px] text-red-600 mt-1 flex items-start gap-1">
                <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
                Must be at least {SOFT_FLOOR_DAYS} days ago.
                For credit from the last week, use the normal POS late-encode flow instead.
              </p>
            )}
            {dateOk && <p className="text-[11px] text-slate-500 mt-1">{daysBack} days ago</p>}
          </div>

          {/* Notebook reference */}
          <div>
            <Label className="text-xs">Notebook page / reference <span className="text-slate-400">(optional)</span></Label>
            <Input data-testid="aob-ref" value={notebookRef} onChange={(e) => setNotebookRef(e.target.value)}
              placeholder="e.g. Ledger 3, page 47" className="h-10" />
          </div>

          {/* Reason */}
          <div>
            <Label className="text-xs">Reason / explanation <span className="text-red-500">*</span> <span className="text-slate-400">(min {MIN_REASON_LEN} chars)</span></Label>
            <Textarea data-testid="aob-reason" rows={3} value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Customer's notebook page 47 shows ₱4,500 credit from June 2024 that was never encoded into the POS. Verified with customer's signature." />
            <p className={`text-[11px] mt-1 ${reasonOk ? 'text-emerald-600' : 'text-slate-500'}`}>
              {reason.trim().length} / {MIN_REASON_LEN} chars
            </p>
          </div>

          {/* TOTP approval */}
          <div className="rounded-md border border-slate-200 bg-slate-50 p-2.5">
            <Label className="text-xs">Authenticator Code (TOTP) <span className="text-red-500">*</span></Label>
            <Input data-testid="aob-totp" value={approvalCode}
              onChange={(e) => setApprovalCode(e.target.value.replace(/\s/g, ''))}
              onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) handleSubmit(); }}
              placeholder="6-digit code from your Authenticator App"
              className="h-10 font-mono tracking-widest" maxLength={10} />
            <p className="text-[11px] text-slate-500 mt-1">
              Owner / admin only. Manager PIN is rejected for this action.
              Get the code from your Authenticator App (Settings → Security).
            </p>
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={committing}>Cancel</Button>
          <Button data-testid="aob-commit" onClick={handleSubmit} disabled={!canSubmit}
            className="bg-amber-700 hover:bg-amber-800 text-white">
            {committing ? 'Adding…' : `Add ${formatPHP(amt)} to balance`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
