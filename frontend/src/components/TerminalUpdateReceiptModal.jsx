import React, { useState, useMemo } from 'react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import {
  X, AlertTriangle, FileEdit, CheckCircle2, 
  RefreshCw, Printer, ShieldCheck, Package
} from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';
const php = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function TerminalUpdateReceiptModal({ 
  invoice, 
  onSuccess, 
  onClose 
}) {
  const [step, setStep] = useState(1); // 1=configure, 2=PIN, 3=success
  const [quantities, setQuantities] = useState(() => {
    // Initialize with original quantities
    return invoice.items.reduce((acc, item, idx) => {
      acc[idx] = {
        original_qty: item.quantity,
        actual_qty: item.quantity, // Default to original
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

  // Update quantity for specific item
  const updateQuantity = (itemIndex, actualQty) => {
    setQuantities(prev => ({
      ...prev,
      [itemIndex]: {
        ...prev[itemIndex],
        actual_qty: actualQty
      }
    }));
  };

  // Calculate totals and differences
  const calculations = useMemo(() => {
    let refundAmount = 0;
    let itemsWithDifference = [];
    
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
          amount: diff * parseFloat(q.rate)
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

  // Submit correction
  const handleSubmit = async () => {
    if (!pin) {
      setPinError('PIN is required');
      return;
    }

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
          unit: q.unit
        })),
        manager_pin: pin,
        reprint_receipt: reprintChoice === 'yes',
        notes
      };

      const res = await axios.post(`${BACKEND}/api/invoices/${invoice.id}/correct-incomplete-stock`, payload);
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

  // Step 1: Configure Quantities
  if (step === 1) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div className="bg-white rounded-2xl max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col">
          {/* Header */}
          <div className="px-6 py-4 bg-amber-50 border-b border-amber-100 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center">
                <FileEdit size={20} className="text-amber-600" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-amber-900">Update for Incomplete Stock</h2>
                <p className="text-xs text-amber-600">Invoice: {invoice.invoice_number}</p>
              </div>
            </div>
            <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
              <X size={20} />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
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
              <h3 className="text-sm font-semibold text-slate-700 mb-3">
                Enter Actual Quantities Given
              </h3>
              <div className="space-y-3">
                {invoice.items.map((item, idx) => {
                  const q = quantities[idx];
                  const diff = (parseFloat(q.original_qty) || 0) - (parseFloat(q.actual_qty) || 0);
                  const hasDifference = diff > 0;
                  
                  return (
                    <div
                      key={idx}
                      className={`rounded-xl border-2 p-4 transition-all ${
                        hasDifference 
                          ? 'border-amber-400 bg-amber-50' 
                          : 'border-slate-200 bg-white'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-4 mb-3">
                        <div className="flex-1">
                          <p className="font-semibold text-slate-800">{item.product_name}</p>
                          <p className="text-sm text-slate-500">
                            Rate: {php(item.rate)} per {item.unit || 'unit'}
                          </p>
                        </div>
                        {hasDifference && (
                          <Badge className="bg-amber-200 text-amber-800 text-xs">
                            -{diff} {item.unit}
                          </Badge>
                        )}
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        {/* Original Quantity */}
                        <div>
                          <label className="text-xs text-slate-500 mb-1 block">Receipt Shows</label>
                          <div className="h-10 rounded-lg border-2 border-slate-300 bg-slate-50 px-3 flex items-center justify-center">
                            <span className="text-lg font-bold text-slate-700">
                              {q.original_qty} {item.unit}
                            </span>
                          </div>
                        </div>

                        {/* Actually Given */}
                        <div>
                          <label className="text-xs text-slate-500 mb-1 block">Actually Given *</label>
                          <Input
                            type="number"
                            step="0.01"
                            min="0"
                            max={q.original_qty}
                            value={q.actual_qty}
                            onChange={(e) => updateQuantity(idx, e.target.value)}
                            className="h-10 text-center text-lg font-bold"
                          />
                        </div>
                      </div>

                      {hasDifference && (
                        <div className="mt-3 pt-3 border-t border-amber-200">
                          <div className="flex justify-between text-sm">
                            <span className="text-amber-700">Will return to shelf:</span>
                            <span className="font-semibold text-amber-800">
                              {diff} {item.unit} ({php(diff * item.rate)})
                            </span>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Notes */}
            <div>
              <label className="text-sm font-semibold text-slate-700 mb-2 block">
                Reason for Correction (optional)
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="E.g., Items out of stock, customer only needed partial quantity..."
                className="w-full h-20 rounded-lg border border-slate-300 px-3 py-2 text-sm resize-none"
              />
            </div>

            {/* Summary */}
            {calculations.hasChanges && (
              <div className="rounded-xl bg-gradient-to-br from-amber-50 to-orange-50 border-2 border-amber-300 p-4 space-y-2">
                <h4 className="text-sm font-bold text-amber-900 mb-2">Correction Summary</h4>
                <div className="space-y-1">
                  {calculations.itemsWithDifference.map((item, idx) => (
                    <div key={idx} className="flex justify-between text-sm">
                      <span className="text-amber-800">{item.product_name}</span>
                      <span className="font-semibold text-amber-900">
                        -{item.quantity} {item.unit} · {php(item.amount)}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="pt-2 border-t border-amber-300 flex justify-between">
                  <span className="font-bold text-amber-900">Total Refund:</span>
                  <span className="text-xl font-bold text-amber-900">{php(calculations.refundAmount)}</span>
                </div>
                <div className="text-xs text-amber-700 bg-amber-100/50 rounded px-2 py-1">
                  New receipt total: {php(calculations.newGrandTotal)}
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="px-6 py-4 bg-slate-50 border-t flex items-center justify-between">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button 
              className="bg-amber-600 hover:bg-amber-700 text-white"
              onClick={() => setStep(2)}
              disabled={!calculations.hasChanges}
            >
              {calculations.hasChanges 
                ? `Continue: Refund ${php(calculations.refundAmount)}`
                : 'No changes to apply'
              }
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Step 2: PIN Confirmation + Reprint Choice
  if (step === 2) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div className="bg-white rounded-2xl max-w-md w-full overflow-hidden">
          {/* Header */}
          <div className="px-6 py-4 bg-amber-50 border-b border-amber-100">
            <div className="flex items-center gap-3">
              <ShieldCheck size={20} className="text-amber-600" />
              <div>
                <h2 className="text-lg font-bold text-amber-900">Confirm Receipt Correction</h2>
                <p className="text-xs text-amber-600">Financial transaction - Re-enter PIN to authorize</p>
              </div>
            </div>
          </div>

          {/* Content */}
          <div className="p-6 space-y-4">
            {/* Summary */}
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
              <div className="flex justify-between">
                <span className="text-slate-600">Refund Amount</span>
                <span className="text-xl font-bold text-amber-600">{php(calculations.refundAmount)}</span>
              </div>
              <div className="flex justify-between text-sm pt-2 border-t">
                <span className="text-slate-600">New Receipt Total</span>
                <span className="font-bold text-slate-800">{php(calculations.newGrandTotal)}</span>
              </div>
            </div>

            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-xs text-red-800 flex items-center gap-1.5">
                <AlertTriangle size={12} />
                <span>
                  This will permanently update the original receipt, refund money from cashier wallet, 
                  and update inventory. This action is logged for audit.
                </span>
              </p>
            </div>

            {/* Reprint Choice */}
            <div>
              <label className="text-sm font-semibold text-slate-700 mb-2 block">
                Would you like to reprint the updated receipt?
              </label>
              <div className="grid grid-cols-3 gap-2">
                <button
                  onClick={() => setReprintChoice('yes')}
                  className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-all ${
                    reprintChoice === 'yes'
                      ? 'border-emerald-500 bg-emerald-50 text-emerald-700'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300'
                  }`}
                >
                  Yes, Print
                </button>
                <button
                  onClick={() => setReprintChoice('no')}
                  className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-all ${
                    reprintChoice === 'no'
                      ? 'border-blue-500 bg-blue-50 text-blue-700'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300'
                  }`}
                >
                  No, Skip
                </button>
                <button
                  onClick={() => setReprintChoice('later')}
                  className={`px-3 py-2 rounded-lg border-2 text-sm font-medium transition-all ${
                    reprintChoice === 'later'
                      ? 'border-amber-500 bg-amber-50 text-amber-700'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300'
                  }`}
                >
                  Later
                </button>
              </div>
            </div>

            {/* PIN Input */}
            <div>
              <label className="text-xs text-slate-500 mb-1.5 block font-medium">
                Manager PIN, Admin PIN, or TOTP
              </label>
              <Input
                type="password"
                autoComplete="one-time-code"
                value={pin}
                onChange={(e) => { setPin(e.target.value); setPinError(''); }}
                onKeyDown={(e) => e.key === 'Enter' && reprintChoice && handleSubmit()}
                placeholder="Re-enter PIN to authorize"
                className="h-12 text-center text-xl font-mono tracking-widest"
                autoFocus
              />
              {pinError && (
                <p className="text-red-500 text-xs flex items-center gap-1 mt-2">
                  <AlertTriangle size={12} />{pinError}
                </p>
              )}
            </div>

            {/* Actions */}
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setStep(1)} disabled={submitting}>
                Back
              </Button>
              <Button 
                className="flex-1 bg-amber-600 hover:bg-amber-700 text-white"
                onClick={handleSubmit}
                disabled={submitting || !pin || !reprintChoice}
              >
                {submitting ? (
                  <>
                    <RefreshCw size={14} className="animate-spin mr-2" />
                    Processing...
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={14} className="mr-2" />
                    Confirm Correction
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Step 3: Success
  if (step === 3) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div className="bg-white rounded-2xl max-w-md w-full overflow-hidden">
          {/* Header */}
          <div className="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
            <div className="flex items-center gap-3">
              <CheckCircle2 size={24} className="text-emerald-600" />
              <div>
                <h2 className="text-lg font-bold text-emerald-900">Receipt Corrected</h2>
                <p className="text-xs text-emerald-600">
                  Correction ID: {correctionResult?.correction_id?.slice(0, 8)}...
                </p>
              </div>
            </div>
          </div>

          {/* Content */}
          <div className="p-6 space-y-4">
            <div className="text-center">
              <div className="w-16 h-16 rounded-full bg-emerald-100 mx-auto mb-3 flex items-center justify-center">
                <FileEdit size={32} className="text-emerald-600" />
              </div>
              <p className="text-lg font-semibold text-slate-800 mb-1">
                {php(calculations.refundAmount)} Refunded
              </p>
              <p className="text-sm text-slate-500">
                {correctionResult?.items_returned || 0} item{correctionResult?.items_returned !== 1 ? 's' : ''} returned to inventory
              </p>
            </div>

            {/* Details */}
            <div className="rounded-lg border border-slate-200 p-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-600">Original Total:</span>
                <span className="line-through text-slate-400">{php(invoice.grand_total)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">New Total:</span>
                <span className="font-bold text-emerald-600">{php(calculations.newGrandTotal)}</span>
              </div>
              {invoice.customer_id && (
                <div className="pt-2 border-t text-xs text-slate-500">
                  ✓ Customer notified via SMS
                </div>
              )}
            </div>

            {/* Reprint Action */}
            {reprintChoice === 'yes' && (
              <div className="rounded-lg border-2 border-emerald-200 bg-emerald-50 p-4">
                <p className="text-sm font-semibold text-emerald-800 mb-2 flex items-center gap-2">
                  <Printer size={14} />
                  Ready to Print Updated Receipt
                </p>
                <Button 
                  variant="outline" 
                  className="w-full border-emerald-600 text-emerald-700 hover:bg-emerald-100"
                  onClick={() => {
                    toast.info('Receipt printing will be implemented');
                  }}
                >
                  <Printer size={14} className="mr-2" />
                  Print Updated Receipt
                </Button>
              </div>
            )}

            {/* Done */}
            <Button 
              className="w-full bg-emerald-600 hover:bg-emerald-700 text-white h-11"
              onClick={() => {
                onSuccess(correctionResult);
                onClose();
              }}
            >
              Done
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return null;
}
