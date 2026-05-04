import React, { useState, useMemo } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { ScrollArea } from './ui/scroll-area';
import {
  AlertTriangle, FileEdit, CheckCircle2,
  RefreshCw, Printer, ShieldCheck, Package
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from './CalcInput';

export default function TerminalUpdateReceiptModal({
  invoice,
  onSuccess,
  onClose,
}) {
  const [step, setStep] = useState(1); // 1=configure, 2=PIN, 3=success
  const [quantities, setQuantities] = useState(() => {
    return invoice.items.reduce((acc, item, idx) => {
      acc[idx] = {
        original_qty: item.quantity,
        actual_qty: item.quantity,
        product_id: item.product_id || '',
        product_name: item.product_name,
        rate: item.rate,
        unit: item.unit || 'unit',
      };
      return acc;
    }, {});
  });
  const [notes, setNotes] = useState('');
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [correctionResult, setCorrectionResult] = useState(null);
  const [reprintChoice, setReprintChoice] = useState(null);

  const updateQuantity = (itemIndex, actualQty) => {
    setQuantities(prev => ({
      ...prev,
      [itemIndex]: { ...prev[itemIndex], actual_qty: actualQty },
    }));
  };

  // ── Business logic preserved exactly ──
  const calculations = useMemo(() => {
    let refundAmount = 0;
    const itemsWithDifference = [];

    Object.entries(quantities).forEach(([idx, q]) => {
      const original = parseFloat(q.original_qty) || 0;
      const actual = parseFloat(q.actual_qty) || 0;
      const diff = original - actual;

      if (diff > 0) {
        refundAmount += diff * parseFloat(q.rate);
        itemsWithDifference.push({
          index: idx,
          product_name: q.product_name,
          quantity: diff,
          unit: q.unit,
          amount: diff * parseFloat(q.rate),
        });
      }
    });

    const hasChanges = itemsWithDifference.length > 0;
    const newSubtotal = Object.values(quantities).reduce((sum, q) => {
      return sum + (parseFloat(q.actual_qty) * parseFloat(q.rate));
    }, 0);

    return {
      refundAmount,
      itemsWithDifference,
      hasChanges,
      newSubtotal,
      newGrandTotal: newSubtotal - (invoice.overall_discount || 0) + (invoice.freight || 0),
    };
  }, [quantities, invoice]);

  const handleSubmit = async () => {
    if (!pin) { setPinError('PIN is required'); return; }
    setSubmitting(true);
    setPinError('');
    try {
      const payload = {
        items: Object.values(quantities).map(q => ({
          product_id: q.product_id,
          product_name: q.product_name,
          original_qty: parseFloat(q.original_qty),
          actual_qty: parseFloat(q.actual_qty),
          rate: parseFloat(q.rate),
          unit: q.unit,
        })),
        manager_pin: pin,
        reprint_receipt: reprintChoice === 'yes',
        notes,
      };
      const res = await api.post(`/invoices/${invoice.id}/correct-incomplete-stock`, payload);
      setCorrectionResult(res.data);
      setStep(3);
      toast.success('Receipt corrected successfully');
    } catch (e) {
      const detail = e.response?.data?.detail;
      setPinError(typeof detail === 'string' ? detail : detail?.message || 'Failed to correct receipt');
      toast.error('Correction failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-hidden flex flex-col p-0" data-testid="terminal-update-receipt-modal">
        {/* Step 1: Configure Quantities */}
        {step === 1 && (
          <>
            <DialogHeader className="px-6 py-4 bg-amber-50 border-b border-amber-100">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center">
                  <FileEdit size={20} className="text-amber-600" />
                </div>
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-amber-900">Update for Incomplete Stock</DialogTitle>
                  <DialogDescription className="text-xs text-amber-600">Invoice: {invoice.invoice_number}</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <ScrollArea className="flex-1 max-h-[55vh]">
              <div className="p-6 space-y-6">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                  <p className="text-sm text-blue-800 flex items-start gap-2">
                    <Package size={16} className="mt-0.5 flex-shrink-0" />
                    <span>
                      <strong>Important:</strong> This will update the original receipt to reflect what was actually given to the customer.
                      Items not given will be returned to inventory and money refunded from cashier.
                    </span>
                  </p>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-3">Enter Actual Quantities Given</h3>
                  <div className="space-y-3">
                    {invoice.items.map((item, idx) => {
                      const q = quantities[idx];
                      const diff = (parseFloat(q.original_qty) || 0) - (parseFloat(q.actual_qty) || 0);
                      const hasDifference = diff > 0;

                      return (
                        <div key={idx}
                          className={`rounded-xl border-2 p-4 transition-all ${hasDifference ? 'border-amber-400 bg-amber-50' : 'border-slate-200 bg-slate-50'}`}
                          data-testid={`update-item-row-${idx}`}>
                          <div className="flex items-start justify-between mb-3">
                            <div>
                              <p className="font-semibold text-slate-800">{item.product_name}</p>
                              <p className="text-xs text-slate-500">{formatPHP(item.rate)} per {item.unit || 'unit'}</p>
                            </div>
                            {hasDifference && (
                              <Badge className="bg-amber-100 text-amber-700 text-xs gap-1">
                                <AlertTriangle size={11} /> {diff} short
                              </Badge>
                            )}
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="text-xs text-slate-500 block mb-1">Original</label>
                              <Input value={q.original_qty} disabled className="h-10 bg-white" />
                            </div>
                            <div>
                              <label className="text-xs text-slate-500 block mb-1">Actually Given</label>
                              <CalcInput value={q.actual_qty}
 onChange={(v) => updateQuantity(idx, v)}
 className={`h-10 ${hasDifference ? 'border-amber-400' : ''}`}
 data-testid={`update-actual-qty-${idx}`} />
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-700 mb-2 block">Notes (optional)</label>
                  <Input
                    placeholder="e.g., Stock ran out for fertilizer"
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    className="h-11"
                  />
                </div>

                {calculations.hasChanges && (
                  <div className="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 space-y-2" data-testid="update-summary">
                    <p className="text-xs font-bold text-amber-700 uppercase tracking-wider">Refund Summary</p>
                    <div className="space-y-1">
                      {calculations.itemsWithDifference.map((it, i) => (
                        <div key={i} className="flex justify-between text-sm">
                          <span className="text-amber-800">{it.product_name} ({it.quantity} {it.unit})</span>
                          <span className="font-mono text-amber-900">{formatPHP(it.amount)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="pt-2 border-t border-amber-300 flex justify-between">
                      <span className="font-bold text-amber-900">Total Refund:</span>
                      <span className="text-xl font-bold text-amber-900">{formatPHP(calculations.refundAmount)}</span>
                    </div>
                    <div className="text-xs text-amber-700 bg-amber-100/50 rounded px-2 py-1">
                      New receipt total: {formatPHP(calculations.newGrandTotal)}
                    </div>
                  </div>
                )}
              </div>
            </ScrollArea>

            <div className="px-6 py-4 bg-slate-50 border-t flex items-center justify-between">
              <Button variant="outline" onClick={onClose} data-testid="update-cancel-btn">Cancel</Button>
              <Button
                className="bg-amber-600 hover:bg-amber-700 text-white"
                onClick={() => setStep(2)}
                disabled={!calculations.hasChanges}
                data-testid="update-continue-btn">
                {calculations.hasChanges ? `Continue: Refund ${formatPHP(calculations.refundAmount)}` : 'No changes to apply'}
              </Button>
            </div>
          </>
        )}

        {/* Step 2: PIN Confirmation */}
        {step === 2 && (
          <>
            <DialogHeader className="px-6 py-4 bg-amber-50 border-b border-amber-100">
              <div className="flex items-center gap-3">
                <ShieldCheck size={20} className="text-amber-600" />
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-amber-900">Confirm Receipt Correction</DialogTitle>
                  <DialogDescription className="text-xs text-amber-600">Financial transaction — Re-enter PIN to authorize</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className="p-6 space-y-4">
              <div className="rounded-lg bg-slate-50 p-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600">Items Being Corrected</span>
                  <span className="font-semibold">{calculations.itemsWithDifference.length}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600">Stock Returning to Shelf</span>
                  <span className="font-semibold">
                    {calculations.itemsWithDifference.reduce((sum, i) => sum + i.quantity, 0)} items
                  </span>
                </div>
                <div className="flex justify-between text-sm pt-2 border-t border-slate-200">
                  <span className="font-bold text-slate-700">Refund from Cashier</span>
                  <span className="font-bold text-amber-700">{formatPHP(calculations.refundAmount)}</span>
                </div>
              </div>

              {/* Reprint choice */}
              <div>
                <p className="text-sm font-semibold text-slate-700 mb-2">Print updated receipt for customer?</p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setReprintChoice('yes')}
                    className={`rounded-lg border-2 p-3 text-sm transition-all ${reprintChoice === 'yes' ? 'border-emerald-500 bg-emerald-50 text-emerald-800' : 'border-slate-200 hover:border-slate-300'}`}
                    data-testid="update-reprint-yes">
                    <Printer size={14} className="inline mr-1.5" />
                    Yes, print updated
                  </button>
                  <button
                    onClick={() => setReprintChoice('no')}
                    className={`rounded-lg border-2 p-3 text-sm transition-all ${reprintChoice === 'no' ? 'border-slate-500 bg-slate-50 text-slate-800' : 'border-slate-200 hover:border-slate-300'}`}
                    data-testid="update-reprint-no">
                    No, just correct records
                  </button>
                </div>
              </div>

              <div>
                <label className="text-xs font-semibold text-slate-700 mb-2 block">Manager PIN</label>
                <Input
                  type="password"
                  inputMode="numeric"
                  maxLength={6}
                  value={pin}
                  onChange={(e) => { setPin(e.target.value); setPinError(''); }}
                  placeholder="Enter PIN"
                  className="h-12 text-center text-xl tracking-widest"
                  autoFocus
                  data-testid="update-pin-input"
                />
                {pinError && <p className="text-xs text-red-600 mt-1.5 flex items-center gap-1"><AlertTriangle size={11} /> {pinError}</p>}
              </div>

              <div className="flex items-center justify-between gap-2">
                <Button variant="outline" onClick={() => setStep(1)} disabled={submitting} data-testid="update-back-btn">Back</Button>
                <Button
                  className="bg-amber-600 hover:bg-amber-700 text-white flex-1"
                  onClick={handleSubmit}
                  disabled={submitting || !pin || !reprintChoice}
                  data-testid="update-confirm-btn">
                  {submitting ? <><RefreshCw size={14} className="mr-2 animate-spin" />Processing...</> : <><CheckCircle2 size={14} className="mr-2" />Confirm Correction</>}
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Step 3: Success */}
        {step === 3 && (
          <>
            <DialogHeader className="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
              <div className="flex items-center gap-3">
                <CheckCircle2 size={24} className="text-emerald-600" />
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-emerald-900">Receipt Corrected</DialogTitle>
                  <DialogDescription className="text-xs text-emerald-600">
                    Correction ID: {correctionResult?.correction_id?.slice(0, 8)}…
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className="p-6 space-y-4">
              <div className="text-center">
                <div className="w-16 h-16 rounded-full bg-emerald-100 mx-auto mb-3 flex items-center justify-center">
                  <FileEdit size={32} className="text-emerald-600" />
                </div>
                <p className="text-lg font-semibold text-slate-800 mb-1">{formatPHP(calculations.refundAmount)} Refunded</p>
                <p className="text-sm text-slate-500">
                  {correctionResult?.items_returned || 0} item{correctionResult?.items_returned !== 1 ? 's' : ''} returned to inventory
                </p>
              </div>

              <div className="rounded-lg border border-slate-200 p-4 space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-600">Original Total:</span>
                  <span className="line-through text-slate-400">{formatPHP(invoice.grand_total)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">New Total:</span>
                  <span className="font-bold text-emerald-600">{formatPHP(calculations.newGrandTotal)}</span>
                </div>
                {invoice.customer_id && (
                  <div className="pt-2 border-t text-xs text-slate-500">✓ Customer notified via SMS</div>
                )}
              </div>

              {reprintChoice === 'yes' && (
                <div className="rounded-lg border-2 border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-sm font-semibold text-emerald-800 mb-2 flex items-center gap-2">
                    <Printer size={14} /> Ready to Print Updated Receipt
                  </p>
                  <Button
                    variant="outline"
                    className="w-full border-emerald-600 text-emerald-700 hover:bg-emerald-100"
                    onClick={() => toast.info('Receipt printing will be implemented')}>
                    <Printer size={14} className="mr-2" /> Print Updated Receipt
                  </Button>
                </div>
              )}

              <Button
                className="w-full bg-emerald-600 hover:bg-emerald-700 text-white h-11"
                onClick={() => { onSuccess(correctionResult); onClose(); }}
                data-testid="update-done-btn">
                Done
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
