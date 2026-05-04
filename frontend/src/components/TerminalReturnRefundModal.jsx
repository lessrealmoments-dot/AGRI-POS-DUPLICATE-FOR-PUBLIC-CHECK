import React, { useState, useMemo } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { ScrollArea } from './ui/scroll-area';
import {
  AlertTriangle, RotateCcw, Package, CheckCircle2,
  RefreshCw, Printer, ShieldCheck,
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from './CalcInput';

const CONDITIONS = [
  { value: 'sellable', label: 'Sellable', desc: 'Unopened, good condition', color: 'emerald', action: 'shelf' },
  { value: 'damaged', label: 'Damaged', desc: 'Packaging damaged', color: 'amber', action: 'pullout' },
  { value: 'expired', label: 'Expired', desc: 'Past expiry date', color: 'red', action: 'pullout' },
  { value: 'defective', label: 'Defective', desc: 'Does not work', color: 'red', action: 'pullout' },
];

const REASONS = [
  'Defective / Not Working',
  'Expired Product',
  'Wrong Product Delivered',
  'Damaged Packaging',
  'Customer Changed Mind',
  'Duplicate Order',
  'Other',
];

const conditionRing = (cond, selected) => {
  if (!selected) return 'border-slate-200 hover:border-slate-300';
  return {
    sellable: 'border-emerald-500 bg-emerald-50',
    damaged: 'border-amber-500 bg-amber-50',
    expired: 'border-red-500 bg-red-50',
    defective: 'border-red-500 bg-red-50',
  }[cond] || 'border-slate-300';
};
const conditionText = (cond, selected) => {
  if (!selected) return 'text-slate-700';
  return {
    sellable: 'text-emerald-700',
    damaged: 'text-amber-700',
    expired: 'text-red-700',
    defective: 'text-red-700',
  }[cond] || 'text-slate-700';
};

export default function TerminalReturnRefundModal({
  invoice,
  terminalSession,
  onSuccess,
  onClose,
}) {
  const [step, setStep] = useState(1); // 1=select items, 2=details, 3=PIN, 4=success
  const [selectedItems, setSelectedItems] = useState([]);
  const [reason, setReason] = useState('');
  const [notes, setNotes] = useState('');
  const [pin, setPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [rmaNumber, setRmaNumber] = useState('');

  const toggleItem = (itemIndex) => {
    setSelectedItems(prev => {
      const exists = prev.find(si => si.index === itemIndex);
      if (exists) return prev.filter(si => si.index !== itemIndex);
      const item = invoice.items[itemIndex];
      return [...prev, {
        index: itemIndex,
        product_id: item.product_id || '',
        product_name: item.product_name,
        sku: item.sku || '',
        category: item.category || '',
        unit: item.unit || 'unit',
        original_qty: item.quantity,
        return_qty: item.quantity,
        rate: item.rate,
        condition: 'sellable',
        inventory_action: 'shelf',
      }];
    });
  };

  const updateItem = (itemIndex, field, value) => {
    setSelectedItems(prev => prev.map(si => si.index === itemIndex ? { ...si, [field]: value } : si));
  };

  const setConditionWithAction = (itemIndex, condition) => {
    const conditionObj = CONDITIONS.find(c => c.value === condition);
    updateItem(itemIndex, 'condition', condition);
    if (conditionObj) updateItem(itemIndex, 'inventory_action', conditionObj.action);
  };

  // ── Business logic preserved exactly ──
  const refundAmount = useMemo(() => {
    return selectedItems.reduce((sum, si) => {
      const qty = parseFloat(si.return_qty) || 0;
      const rate = parseFloat(si.rate) || 0;
      return sum + (qty * rate);
    }, 0);
  }, [selectedItems]);

  const handleSubmit = async () => {
    if (!pin) { setPinError('PIN is required'); return; }
    setSubmitting(true);
    setPinError('');
    try {
      const payload = {
        branch_id: invoice.branch_id,
        return_date: new Date().toISOString().split('T')[0],
        customer_name: invoice.customer_name || 'Walk-in',
        customer_type: invoice.customer_id ? 'credit' : 'walkin',
        reason,
        invoice_number: invoice.invoice_number,
        notes,
        items: selectedItems.map(si => ({
          product_id: si.product_id,
          product_name: si.product_name,
          sku: si.sku,
          category: si.category,
          unit: si.unit,
          quantity: parseFloat(si.return_qty),
          condition: si.condition,
          inventory_action: si.inventory_action,
          refund_price: parseFloat(si.rate),
          cost_price: 0, // backend fills
        })),
        refund_method: 'full',
        refund_amount: refundAmount,
        fund_source: 'cashier',
        cashier_id: terminalSession?.userId || '',
        cashier_name: terminalSession?.userName || '',
        manager_pin: pin,
      };
      const res = await api.post('/returns', payload);
      setRmaNumber(res.data.rma_number);
      setStep(4);
      toast.success(`Return processed: ${res.data.rma_number}`);
    } catch (e) {
      const detail = e.response?.data?.detail;
      setPinError(typeof detail === 'string' ? detail : detail?.message || 'Failed to process return');
      toast.error('Return failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-hidden flex flex-col p-0" data-testid="terminal-return-refund-modal">
        {/* Step 1: Select Items */}
        {step === 1 && (
          <>
            <DialogHeader className="px-6 py-4 bg-red-50 border-b border-red-100">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-red-100 flex items-center justify-center">
                  <RotateCcw size={20} className="text-red-600" />
                </div>
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-red-900">Return & Refund</DialogTitle>
                  <DialogDescription className="text-xs text-red-600">Invoice: {invoice.invoice_number}</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <ScrollArea className="flex-1 max-h-[55vh]">
              <div className="p-6">
                <p className="text-sm text-slate-600 mb-4">
                  <Package size={14} className="inline mr-1.5" />
                  Select items to return ({invoice.items?.length || 0} items on receipt)
                </p>

                <div className="space-y-3">
                  {invoice.items?.map((item, idx) => {
                    const selected = selectedItems.find(si => si.index === idx);
                    return (
                      <div
                        key={idx}
                        className={`rounded-xl border-2 p-4 cursor-pointer transition-all ${selected ? 'border-red-400 bg-red-50' : 'border-slate-200 hover:border-slate-300'}`}
                        onClick={() => toggleItem(idx)}
                        data-testid={`return-item-${idx}`}>
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-start gap-3 flex-1">
                            <input
                              type="checkbox"
                              checked={!!selected}
                              onChange={() => toggleItem(idx)}
                              onClick={(e) => e.stopPropagation()}
                              className="mt-1 w-5 h-5 rounded border-slate-300 text-red-600 focus:ring-red-500"
                            />
                            <div className="flex-1">
                              <p className="font-semibold text-slate-800">{item.product_name}</p>
                              <p className="text-sm text-slate-500">
                                Qty: {item.quantity} {item.unit || 'unit'} × {formatPHP(item.rate)}
                              </p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="font-bold text-slate-800">{formatPHP(item.total || (item.quantity * item.rate))}</p>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {selectedItems.length === 0 && (
                  <div className="text-center py-8">
                    <AlertTriangle size={32} className="text-slate-300 mx-auto mb-2" />
                    <p className="text-sm text-slate-400">No items selected</p>
                  </div>
                )}
              </div>
            </ScrollArea>

            <div className="px-6 py-4 bg-slate-50 border-t flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-500">Selected Items</p>
                <p className="text-lg font-bold text-slate-800">{selectedItems.length} of {invoice.items?.length || 0}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={onClose} data-testid="return-cancel-btn">Cancel</Button>
                <Button
                  className="bg-red-600 hover:bg-red-700 text-white"
                  onClick={() => setStep(2)}
                  disabled={selectedItems.length === 0}
                  data-testid="return-next1-btn">
                  Next: Configure Return
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Step 2: Configure */}
        {step === 2 && (
          <>
            <DialogHeader className="px-6 py-4 bg-red-50 border-b border-red-100">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-red-100 flex items-center justify-center">
                  <RotateCcw size={20} className="text-red-600" />
                </div>
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-red-900">Configure Return Details</DialogTitle>
                  <DialogDescription className="text-xs text-red-600">{selectedItems.length} item{selectedItems.length !== 1 ? 's' : ''} selected</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <ScrollArea className="flex-1 max-h-[55vh]">
              <div className="p-6 space-y-6">
                <div>
                  <label className="text-sm font-semibold text-slate-700 mb-2 block">Return Reason *</label>
                  <select
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className="w-full h-11 rounded-lg border border-slate-300 px-3 text-sm"
                    data-testid="return-reason-select">
                    <option value="">Select reason...</option>
                    {REASONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>

                <div>
                  <label className="text-sm font-semibold text-slate-700 mb-2 block">Notes (optional)</label>
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Additional details about the return..."
                    className="w-full h-20 rounded-lg border border-slate-300 px-3 py-2 text-sm resize-none"
                  />
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-slate-700 mb-3">Configure Each Item</h3>
                  <div className="space-y-4">
                    {selectedItems.map((si) => (
                      <div key={si.index} className="rounded-xl border-2 border-slate-200 p-4 space-y-3" data-testid={`return-config-${si.index}`}>
                        <div className="flex items-center justify-between">
                          <p className="font-semibold text-slate-800">{si.product_name}</p>
                          <Badge className="bg-slate-100 text-slate-600 text-xs">Max: {si.original_qty} {si.unit}</Badge>
                        </div>

                        <div>
                          <label className="text-xs text-slate-500 mb-1 block">Return Quantity *</label>
                          <CalcInput value={si.return_qty}
 onChange={(v) => updateItem(si.index, 'return_qty', v)}
 className="h-9 text-center font-mono" />
                        </div>

                        <div>
                          <label className="text-xs text-slate-500 mb-1 block">Condition *</label>
                          <div className="grid grid-cols-2 gap-2">
                            {CONDITIONS.map(cond => {
                              const isSelected = si.condition === cond.value;
                              return (
                                <button
                                  key={cond.value}
                                  onClick={() => setConditionWithAction(si.index, cond.value)}
                                  className={`p-2 rounded-lg border-2 text-left transition-all ${conditionRing(cond.value, isSelected)}`}>
                                  <p className={`text-xs font-semibold ${conditionText(cond.value, isSelected)}`}>{cond.label}</p>
                                  <p className="text-[10px] text-slate-400">{cond.desc}</p>
                                </button>
                              );
                            })}
                          </div>
                        </div>

                        <div>
                          <label className="text-xs text-slate-500 mb-1 block">Inventory Action</label>
                          <div className="flex gap-2">
                            <button
                              onClick={() => updateItem(si.index, 'inventory_action', 'shelf')}
                              className={`flex-1 px-3 py-2 rounded-lg border-2 text-xs font-medium transition-all ${si.inventory_action === 'shelf' ? 'border-emerald-500 bg-emerald-50 text-emerald-700' : 'border-slate-200 text-slate-600'}`}>
                              ✓ Return to Shelf
                            </button>
                            <button
                              onClick={() => updateItem(si.index, 'inventory_action', 'pullout')}
                              className={`flex-1 px-3 py-2 rounded-lg border-2 text-xs font-medium transition-all ${si.inventory_action === 'pullout' ? 'border-red-500 bg-red-50 text-red-700' : 'border-slate-200 text-slate-600'}`}>
                              ✗ Pull Out (Loss)
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </ScrollArea>

            <div className="px-6 py-4 bg-slate-50 border-t">
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm text-slate-600">Total Refund Amount</p>
                <p className="text-2xl font-bold text-red-600">{formatPHP(refundAmount)}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setStep(1)} data-testid="return-back2-btn">Back</Button>
                <Button
                  className="flex-1 bg-red-600 hover:bg-red-700 text-white"
                  onClick={() => setStep(3)}
                  disabled={!reason || selectedItems.some(si => !si.return_qty || parseFloat(si.return_qty) <= 0)}
                  data-testid="return-next2-btn">
                  Next: Authorize Return
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Step 3: PIN */}
        {step === 3 && (
          <>
            <DialogHeader className="px-6 py-4 bg-red-50 border-b border-red-100">
              <div className="flex items-center gap-3">
                <ShieldCheck size={20} className="text-red-600" />
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-red-900">Confirm Return & Refund</DialogTitle>
                  <DialogDescription className="text-xs text-red-600">Financial transaction — Re-enter PIN to confirm</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className="p-6 space-y-4">
              <div className="rounded-lg bg-slate-50 p-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600">Items Returning</span>
                  <span className="font-semibold">{selectedItems.length}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600">Reason</span>
                  <span className="font-semibold text-right">{reason}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-600">Refund Amount</span>
                  <span className="text-xl font-bold text-red-600">{formatPHP(refundAmount)}</span>
                </div>
              </div>

              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                <p className="text-xs text-amber-800 flex items-center gap-1.5">
                  <AlertTriangle size={12} />
                  <span>This will refund money from cashier wallet and update inventory. Confirm with your PIN.</span>
                </p>
              </div>

              <div>
                <label className="text-xs text-slate-500 mb-1.5 block font-medium">Manager PIN, Admin PIN, or TOTP</label>
                <Input
                  type="password"
                  autoComplete="one-time-code"
                  value={pin}
                  onChange={(e) => { setPin(e.target.value); setPinError(''); }}
                  onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
                  placeholder="Re-enter PIN to confirm"
                  className="h-12 text-center text-xl font-mono tracking-widest"
                  autoFocus
                  data-testid="return-pin-input"
                />
                {pinError && <p className="text-red-500 text-xs flex items-center gap-1 mt-2"><AlertTriangle size={12} /> {pinError}</p>}
              </div>

              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => setStep(2)} disabled={submitting} data-testid="return-back3-btn">Back</Button>
                <Button
                  className="flex-1 bg-red-600 hover:bg-red-700 text-white"
                  onClick={handleSubmit}
                  disabled={submitting || !pin}
                  data-testid="return-confirm-btn">
                  {submitting ? <><RefreshCw size={14} className="animate-spin mr-2" />Processing...</> : <><CheckCircle2 size={14} className="mr-2" />Confirm Refund</>}
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Step 4: Success */}
        {step === 4 && (
          <>
            <DialogHeader className="px-6 py-4 bg-emerald-50 border-b border-emerald-100">
              <div className="flex items-center gap-3">
                <CheckCircle2 size={24} className="text-emerald-600" />
                <div className="text-left">
                  <DialogTitle className="text-lg font-bold text-emerald-900">Return Processed</DialogTitle>
                  <DialogDescription className="text-xs text-emerald-600">RMA: {rmaNumber}</DialogDescription>
                </div>
              </div>
            </DialogHeader>

            <div className="p-6 space-y-4">
              <div className="text-center">
                <div className="w-16 h-16 rounded-full bg-emerald-100 mx-auto mb-3 flex items-center justify-center">
                  <RotateCcw size={32} className="text-emerald-600" />
                </div>
                <p className="text-lg font-semibold text-slate-800 mb-1">{formatPHP(refundAmount)} Refunded</p>
                <p className="text-sm text-slate-500">{selectedItems.length} item{selectedItems.length !== 1 ? 's' : ''} returned</p>
              </div>

              <div className="rounded-lg border-2 border-slate-200 p-4">
                <p className="text-sm font-semibold text-slate-700 mb-2"><Printer size={14} className="inline mr-1.5" />Print Return Slip?</p>
                <p className="text-xs text-slate-500 mb-3">Generate a receipt for this return transaction</p>
                <Button variant="outline" className="w-full" onClick={() => toast.info('Return slip printing coming soon!')}>
                  <Printer size={14} className="mr-2" />Print Return Slip
                </Button>
              </div>

              <Button
                className="w-full bg-emerald-600 hover:bg-emerald-700 text-white h-11"
                onClick={() => { onSuccess(); onClose(); }}
                data-testid="return-done-btn">
                Done
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
