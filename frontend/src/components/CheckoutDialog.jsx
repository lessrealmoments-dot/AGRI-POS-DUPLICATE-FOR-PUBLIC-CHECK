/**
 * CheckoutDialog — Phase 4 Cleanup presentational extraction.
 *
 * Pure render of the Payment dialog that previously lived inline in
 * `UnifiedSalesPage.js` (lines ~4158–4486).
 *
 * State is OWNED BY THE PAGE for this pass. The dialog only reads from
 * props and invokes the matching setters / callbacks. A future pass
 * (`useCheckoutState`) will collapse the prop surface into a single
 * `cs` object the same way `useHistoricalCredit` does for HC.
 *
 * Boundary discipline:
 *   • No state, no `useEffect`, no API call, no axios import.
 *   • The Page pre-computes `confirmDisabled` so the dialog stays pure.
 *   • The Page owns `onConfirm` (= `handleCreditSale`) which contains the
 *     HC-gate / processSale / Credit-Approval routing.
 *   • Behavior + every existing `data-testid` is preserved verbatim from
 *     the inline version.
 *
 * NOT moved (intentional, per the approved slice):
 *   • Credit Approval dialog (sibling on the page).
 *   • `openCheckout` pre-flight (capital floor, price match, custEdited).
 *   • `processSale` (payload build + API + offline + receipt).
 *   • PaymentTabs decomposition — kept inline here until `useCheckoutState`.
 */
import React from 'react';
import { AlertTriangle, Search, Shield, X } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Tabs, TabsList, TabsTrigger } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Button } from './ui/button';
import CalcInput from './CalcInput';
import { formatPHP } from '../lib/utils';

export default function CheckoutDialog({
  // Open / close
  open,
  onOpenChange,

  // Customer surface
  selectedCustomer,
  setSelectedCustomer,
  custSearch,
  setCustSearch,
  customers,
  canViewBalance,

  // Totals
  grandTotal,
  change,

  // Payment state
  paymentType,
  setPaymentType,
  amountTendered,
  setAmountTendered,
  partialPayment,
  setPartialPayment,
  splitCash,
  setSplitCash,
  splitDigital,
  setSplitDigital,
  digitalPlatform,
  setDigitalPlatform,
  digitalRefNumber,
  setDigitalRefNumber,
  digitalSender,
  setDigitalSender,

  // Release mode
  releaseMode,
  setReleaseMode,

  // Confirm
  onConfirm,
  saving,
  confirmDisabled,

  // HC flags (read-only — used by the confirm-button LABEL only)
  isHistoricalCreditMode,
  // HC state — exposed so when isHistoricalCreditMode is active and the
  // reason is too short, the user can fill it INSIDE the dialog rather
  // than being silently blocked by a disabled button.
  hc,
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Manrope' }}>Payment</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto max-h-[calc(85vh-120px)] pr-1">
          {/* Customer display / quick picker */}
          {selectedCustomer ? (
            <div className="bg-slate-50 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-slate-500">Customer</p>
                  <p className="font-medium">{selectedCustomer.name}</p>
                </div>
                <button
                  onClick={() => { setSelectedCustomer(null); setCustSearch(''); }}
                  className="text-xs text-slate-400 hover:text-red-500 transition-colors px-2 py-1 rounded"
                  data-testid="clear-customer-btn"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="flex gap-4 mt-1 text-xs text-slate-500">
                {canViewBalance && <span>Balance: <span className={selectedCustomer.balance > 0 ? 'text-red-600 font-medium' : ''}>{formatPHP(selectedCustomer.balance || 0)}</span></span>}
                {canViewBalance && <span>Limit: {formatPHP(selectedCustomer.credit_limit || 0)}</span>}
              </div>
            </div>
          ) : (
            <div className="bg-slate-50 rounded-lg p-3">
              <p className="text-sm text-slate-500 mb-1.5">Customer</p>
              <div className="relative" data-testid="checkout-customer-picker">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  data-testid="checkout-customer-search"
                  value={custSearch}
                  onChange={e => {
                    setCustSearch(e.target.value);
                    const match = customers.find(c => c.name.toLowerCase() === e.target.value.toLowerCase());
                    if (match) setSelectedCustomer(match);
                    else setSelectedCustomer(null);
                  }}
                  placeholder="Search customer or type Walk-in..."
                  className="pl-8 h-9 text-sm"
                />
                {custSearch && !selectedCustomer && (
                  <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-36 overflow-y-auto">
                    {customers
                      .filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase()))
                      .slice(0, 6)
                      .map(c => (
                        <button
                          key={c.id}
                          onClick={() => { setSelectedCustomer(c); setCustSearch(c.name); }}
                          className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex justify-between items-center"
                          data-testid={`checkout-cust-${c.id}`}
                        >
                          <span className="font-medium truncate">{c.name}</span>
                          <span className="text-[10px] text-slate-400 shrink-0 ml-2">
                            {canViewBalance && c.balance > 0 ? `AR: ${formatPHP(c.balance)}` : ''}
                          </span>
                        </button>
                      ))
                    }
                    {customers.filter(c => c.name.toLowerCase().includes(custSearch.toLowerCase())).length === 0 && (
                      <p className="px-3 py-2 text-xs text-slate-400">No matching customers</p>
                    )}
                  </div>
                )}
              </div>
              {!custSearch && <p className="text-xs text-slate-400 mt-1">Walk-in customer (no AR)</p>}
            </div>
          )}

          {/* Total */}
          <div className="text-center py-4">
            <p className="text-sm text-slate-500">Total Amount</p>
            <p className="text-3xl font-bold text-[#1A4D2E]" style={{ fontFamily: 'Manrope' }}>{formatPHP(grandTotal)}</p>
          </div>

          {/* Payment Type */}
          <div className="space-y-2">
            <Label className="text-sm">Payment Type</Label>
            <Tabs value={paymentType} onValueChange={v => { setPaymentType(v); setDigitalRefNumber(''); setDigitalSender(''); setSplitCash(''); setSplitDigital(''); }}>
              <TabsList className="grid grid-cols-5 w-full">
                <TabsTrigger value="cash" data-testid="pay-cash">Cash</TabsTrigger>
                <TabsTrigger value="digital" data-testid="pay-digital">Digital</TabsTrigger>
                <TabsTrigger value="split" data-testid="pay-split">Split</TabsTrigger>
                <TabsTrigger value="partial" data-testid="pay-partial">Partial</TabsTrigger>
                <TabsTrigger value="credit" data-testid="pay-credit">Credit</TabsTrigger>
              </TabsList>
            </Tabs>
            {!selectedCustomer && (paymentType === 'partial' || paymentType === 'credit') && (
              <p className="text-xs text-amber-600 flex items-center gap-1">
                <AlertTriangle size={12} /> Select a customer first — {paymentType === 'credit' ? 'credit' : 'partial'} balance goes to Accounts Receivable
              </p>
            )}
          </div>

          {/* Payment inputs */}
          {paymentType === 'cash' && (
            <div>
              <Label>Amount Tendered</Label>
              <CalcInput
                data-testid="amount-tendered"
                value={String(amountTendered || '')}
                onChange={(v) => setAmountTendered(parseFloat(v) || 0)}
                className="text-lg h-12"
              />
              {change > 0 && (
                <p className="text-right mt-2 text-lg font-bold text-emerald-600">Change: {formatPHP(change)}</p>
              )}
            </div>
          )}

          {paymentType === 'digital' && (
            <div className="space-y-3 rounded-xl bg-blue-50 border border-blue-200 p-3">
              <div className="flex items-center gap-2 mb-1">
                <div className="w-6 h-6 rounded-full bg-blue-500 flex items-center justify-center">
                  <span className="text-white text-[10px] font-bold">₱</span>
                </div>
                <span className="text-sm font-semibold text-blue-800">Digital Payment → Digital Wallet</span>
              </div>
              <div>
                <Label className="text-xs text-blue-700">Platform *</Label>
                <select
                  value={digitalPlatform}
                  onChange={e => setDigitalPlatform(e.target.value)}
                  className="w-full mt-1 h-9 rounded-lg border border-blue-200 bg-white px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                  data-testid="digital-platform"
                >
                  {['GCash', 'Maya', 'PayMaya', 'Bank Transfer', 'Instapay', 'Pesonet', 'ShopeePay', 'GrabPay', 'Coins.ph', 'SeaBank', 'Other'].map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label className="text-xs text-blue-700">Reference / Transaction # *</Label>
                <Input
                  value={digitalRefNumber}
                  onChange={e => setDigitalRefNumber(e.target.value)}
                  placeholder="e.g. GC2026XXXXXXXX"
                  className="mt-1 h-9 border-blue-200 focus:ring-blue-300"
                  data-testid="digital-ref-number"
                />
              </div>
              <div>
                <Label className="text-xs text-blue-700">Sender Name / Number (optional)</Label>
                <Input
                  value={digitalSender}
                  onChange={e => setDigitalSender(e.target.value)}
                  placeholder="e.g. Juan Dela Cruz / 09XX-XXX-XXXX"
                  className="mt-1 h-9 border-blue-200 focus:ring-blue-300"
                  data-testid="digital-sender"
                />
              </div>
              <div className="flex items-center gap-2 text-[10px] text-blue-600 bg-blue-100 rounded-lg px-2.5 py-1.5">
                <span>After sale: QR code will appear to upload the {digitalPlatform} receipt screenshot</span>
              </div>
              <p className="text-lg font-bold text-blue-800 text-center">{formatPHP(grandTotal)}</p>
            </div>
          )}

          {paymentType === 'split' && (
            <div className="space-y-3 rounded-xl bg-gradient-to-br from-emerald-50 to-blue-50 border border-slate-200 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-semibold text-slate-700">Split: Cash + {digitalPlatform}</span>
                <span className="text-xs text-slate-400">Total: {formatPHP(grandTotal)}</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <Label className="text-xs text-emerald-700">Cash Amount</Label>
                  <CalcInput value={splitCash} data-testid="split-cash"
                    onChange={(v) => { setSplitCash(v); setSplitDigital(String(Math.max(0, grandTotal - (parseFloat(v)||0)))); }}
                    placeholder="0.00" className="mt-1 h-9 border-emerald-200" />
                </div>
                <div>
                  <Label className="text-xs text-blue-700">{digitalPlatform} Amount</Label>
                  <CalcInput value={splitDigital} data-testid="split-digital"
                    onChange={(v) => { setSplitDigital(v); setSplitCash(String(Math.max(0, grandTotal - (parseFloat(v)||0)))); }}
                    placeholder="0.00" className="mt-1 h-9 border-blue-200" />
                </div>
              </div>
              {(parseFloat(splitCash||0) + parseFloat(splitDigital||0)) !== grandTotal && (
                <p className="text-xs text-amber-600 text-center">
                  Cash + Digital must equal {formatPHP(grandTotal)}
                  {' '} (currently {formatPHP(parseFloat(splitCash||0) + parseFloat(splitDigital||0))})
                </p>
              )}
              <div>
                <Label className="text-xs text-blue-700">Platform *</Label>
                <select value={digitalPlatform} onChange={e => setDigitalPlatform(e.target.value)}
                  className="w-full mt-1 h-9 rounded-lg border border-blue-200 bg-white px-2.5 text-sm focus:outline-none">
                  {['GCash', 'Maya', 'PayMaya', 'Bank Transfer', 'Instapay', 'Pesonet', 'ShopeePay', 'GrabPay', 'Other'].map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label className="text-xs text-blue-700">Digital Ref # *</Label>
                <Input value={digitalRefNumber} onChange={e => setDigitalRefNumber(e.target.value)}
                  placeholder="e.g. GC2026XXXXXXXX" className="mt-1 h-9 border-blue-200" data-testid="split-ref-number" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Sender (optional)</Label>
                <Input value={digitalSender} onChange={e => setDigitalSender(e.target.value)}
                  placeholder="Name / number" className="mt-1 h-9" />
              </div>
              <div className="flex items-center gap-2 text-[10px] text-blue-600 bg-blue-100 rounded-lg px-2.5 py-1.5">
                <span>After sale: QR code will appear to upload the {digitalPlatform} payment screenshot</span>
              </div>
            </div>
          )}
          {paymentType === 'partial' && (
            <div>
              {selectedCustomer ? (
                <>
                  <Label>Amount Paid Now</Label>
                  <CalcInput
                    data-testid="partial-amount"
                    value={String(partialPayment || '')}
                    onChange={(v) => setPartialPayment(Math.min(parseFloat(v) || 0, grandTotal))}
                    className="text-lg h-12"
                  />
                  <div className="flex justify-between mt-2 p-2 bg-amber-50 rounded-lg">
                    <span className="text-sm text-amber-700">Balance (to AR)</span>
                    <span className="font-bold text-amber-700">{formatPHP(grandTotal - partialPayment)}</span>
                  </div>
                </>
              ) : (
                <div className="p-3 bg-amber-50 rounded-lg">
                  <p className="text-sm text-amber-700 font-medium">Select a customer above</p>
                  <p className="text-xs text-amber-600 mt-1">Partial payment balance goes to Accounts Receivable and must be assigned to a customer</p>
                </div>
              )}
            </div>
          )}

          {paymentType === 'credit' && (
            <div className="p-3 bg-red-50 rounded-lg">
              <p className="text-sm text-red-700 font-medium">Full Credit Sale</p>
              {selectedCustomer ? (
                <p className="text-xs text-red-600 mt-1">
                  {formatPHP(grandTotal)} will be added to {selectedCustomer.name}'s receivables
                </p>
              ) : (
                <p className="text-xs text-red-600 mt-1">
                  Select a customer above — credit balance must be assigned to an account
                </p>
              )}
              <p className="text-xs text-slate-500 mt-2 flex items-center gap-1">
                <Shield size={12} /> Requires manager approval
              </p>
            </div>
          )}

          {/* Stock Release Mode */}
          <div className="space-y-2 pt-1">
            <Label className="text-sm font-semibold text-slate-700">Stock Release</Label>
            <div className="grid grid-cols-2 gap-2">
              <button
                data-testid="release-mode-full"
                onClick={() => setReleaseMode('full')}
                className={`flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all ${
                  releaseMode === 'full'
                    ? 'border-[#1A4D2E] bg-emerald-50'
                    : 'border-slate-200 bg-slate-50 hover:border-slate-300'
                }`}
              >
                <span className={`text-sm font-semibold ${releaseMode === 'full' ? 'text-[#1A4D2E]' : 'text-slate-700'}`}>
                  Full Release
                </span>
                <span className="text-[11px] text-slate-500 mt-0.5">All items given now</span>
              </button>
              <button
                data-testid="release-mode-partial"
                onClick={() => setReleaseMode('partial')}
                className={`flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all ${
                  releaseMode === 'partial'
                    ? 'border-amber-500 bg-amber-50'
                    : 'border-slate-200 bg-slate-50 hover:border-slate-300'
                }`}
              >
                <span className={`text-sm font-semibold ${releaseMode === 'partial' ? 'text-amber-700' : 'text-slate-700'}`}>
                  Partial Release
                </span>
                <span className="text-[11px] text-slate-500 mt-0.5">Items released via QR</span>
              </button>
            </div>
            {releaseMode === 'partial' && (
              <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 flex items-start gap-1.5">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                Stock stays reserved until each batch is scanned and released. Use the invoice QR code to release items.
              </p>
            )}
          </div>

          {/* Historical Credit / Notebook AR — inline reason field.
              Mirrors HistoricalCreditBanner so the user can satisfy the
              20-char minimum without closing the dialog. Two-way bound to
              the same `hc.reason` so the underlying banner reflects edits. */}
          {isHistoricalCreditMode && hc && (
            <div
              data-testid="hc-inline-reason-block"
              className="rounded-lg border-2 border-amber-300 bg-amber-50 p-3 space-y-2"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="text-amber-600 shrink-0 mt-0.5" size={16} />
                <div className="text-[12px] text-amber-900 leading-snug">
                  <strong>Backdated credit — reason required.</strong>{' '}
                  This will not affect today's cash and requires Owner / Admin
                  TOTP approval on the next step.
                </div>
              </div>
              <textarea
                data-testid="hc-inline-reason-input"
                value={hc.reason}
                onChange={e => hc.setReason(e.target.value)}
                placeholder="Notebook AR carry-forward verified against ledger page 12, customer countersigned 2026-02-04."
                className="w-full text-[12px] rounded border border-amber-300 bg-white px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-amber-400 min-h-[64px]"
              />
              <p className={`text-[11px] font-medium ${
                hc.reason.trim().length >= 20 ? 'text-emerald-700' : 'text-amber-700'
              }`}>
                {hc.reason.trim().length} / 20 minimum
                {hc.reason.trim().length < 20 && ' — keep typing to enable Continue'}
              </p>
            </div>
          )}

          {/* Action buttons — checkout confirm */}
          <div className="flex gap-2 pt-2">
            <Button variant="outline" className="flex-1" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button
              data-testid="confirm-payment"
              className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
              onClick={onConfirm}
              disabled={confirmDisabled}
              title={
                isHistoricalCreditMode && hc && hc.reason.trim().length < 20
                  ? `Reason must be at least 20 characters (currently ${hc.reason.trim().length})`
                  : undefined
              }
            >
              {saving ? 'Processing...' : (
                isHistoricalCreditMode ? 'Continue → Historical Credit / Notebook AR' :
                paymentType === 'cash' ? 'Complete Sale' :
                paymentType === 'digital' ? `Complete — ${digitalPlatform}` :
                paymentType === 'split' ? `Split: ₱${parseFloat(splitCash||0).toFixed(0)} Cash + ₱${parseFloat(splitDigital||0).toFixed(0)} ${digitalPlatform}` :
                'Confirm & Create Invoice'
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
