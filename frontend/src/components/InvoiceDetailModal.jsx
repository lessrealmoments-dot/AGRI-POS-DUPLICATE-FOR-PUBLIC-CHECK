import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../contexts/AuthContext';
import { formatPHP, fmtDateTime } from '../lib/utils';
import { Input } from './ui/input';
import { X, RefreshCw, RotateCcw, Ban, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

/**
 * Reusable invoice / receipt detail modal.
 *
 * Props:
 *   - invoiceNumber: string (required) — e.g. "INV-2025-0001"
 *   - open: boolean
 *   - onClose: () => void
 *   - onChanged: () => void — fired after a successful void so the parent can refresh
 */
export default function InvoiceDetailModal({ invoiceNumber, open, onClose, onChanged }) {
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState(null);
  const [loading, setLoading] = useState(false);

  // Void dialog state
  const [voidDialog, setVoidDialog] = useState(false);
  const [voidReason, setVoidReason] = useState('');
  const [voidPin, setVoidPin] = useState('');
  const [voidSaving, setVoidSaving] = useState(false);
  const [reopenAfterVoid, setReopenAfterVoid] = useState(false);

  const fetchInvoice = useCallback(async () => {
    if (!invoiceNumber) return;
    setLoading(true);
    try {
      const res = await api.get(`/invoices/by-number/${encodeURIComponent(invoiceNumber)}`);
      setInvoice(res.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Invoice not found');
      onClose?.();
    }
    setLoading(false);
  }, [invoiceNumber, onClose]);

  useEffect(() => {
    if (open && invoiceNumber) {
      setInvoice(null);
      setVoidDialog(false);
      setVoidReason('');
      setVoidPin('');
      setReopenAfterVoid(false);
      fetchInvoice();
    }
  }, [open, invoiceNumber, fetchInvoice]);

  if (!open) return null;

  const handleRefund = () => {
    if (!invoice) return;
    onClose?.();
    navigate(`/returns?invoice=${encodeURIComponent(invoice.invoice_number)}`);
  };

  const openVoidDialog = (alsoReopen) => {
    setReopenAfterVoid(!!alsoReopen);
    setVoidReason('');
    setVoidPin('');
    setVoidDialog(true);
  };

  const handleVoid = async () => {
    if (!voidReason.trim()) { toast.error('Please enter a reason'); return; }
    if (!voidPin) { toast.error('Manager PIN required'); return; }
    setVoidSaving(true);
    try {
      const res = await api.post(`/invoices/${invoice.id}/void`, {
        reason: voidReason,
        manager_pin: voidPin,
      });
      toast.success(`${invoice.invoice_number} voided — authorized by ${res.data.authorized_by}`);
      setVoidDialog(false);
      onChanged?.();

      if (reopenAfterVoid) {
        // Stash the snapshot so UnifiedSalesPage can pre-fill a new sale from it
        try {
          sessionStorage.setItem('reopen_sale_snapshot', JSON.stringify(res.data.snapshot));
        } catch { /* ignore quota */ }
        onClose?.();
        navigate('/sales-new');
        return;
      }

      // Not reopening — just refresh the invoice in place so the modal shows VOIDED state
      onClose?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Void failed');
    }
    setVoidSaving(false);
  };

  const isVoided = invoice?.status === 'voided';

  return (
    <>
      <div
        className="fixed inset-0 flex items-center justify-center p-4"
        style={{ backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 9999 }}
        onClick={e => { if (e.target === e.currentTarget) onClose?.(); }}
        data-testid="invoice-detail-modal"
      >
        <div className="bg-white rounded-2xl shadow-2xl w-full overflow-y-auto" style={{ maxWidth: '520px', maxHeight: '90vh' }}>
          <div className="p-5">
            {loading || !invoice ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={20} className="animate-spin text-slate-400" />
                <span className="ml-2 text-sm text-slate-500">Loading receipt…</span>
              </div>
            ) : (
              <>
                {/* Header */}
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-lg font-mono text-blue-700" data-testid="invoice-number">
                        {invoice.invoice_number}
                      </span>
                      {isVoided ? (
                        <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-slate-200 text-slate-500">VOIDED</span>
                      ) : invoice.payment_type === 'cash' || !invoice.customer_id ? (
                        <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-700">Walk-in / Cash</span>
                      ) : (
                        <span className="text-[11px] font-bold px-2 py-0.5 rounded bg-amber-100 text-amber-700">Credit Sale</span>
                      )}
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {invoice.invoice_date || invoice.order_date} · {invoice.cashier_name || 'Unknown cashier'}
                    </p>
                    {invoice.customer_name && invoice.customer_name !== 'Walk-in' && (
                      <p className="text-sm font-semibold text-slate-700 mt-0.5">{invoice.customer_name}</p>
                    )}
                    {isVoided && (
                      <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2">
                        <p className="text-xs text-red-700 font-semibold">Voided: {invoice.void_reason}</p>
                        <p className="text-[10px] text-red-500">
                          By {invoice.void_authorized_by} · {fmtDateTime(invoice.voided_at)}
                        </p>
                      </div>
                    )}
                    {invoice.interest_accrued > 0 && (
                      <div className="mt-1 rounded-lg bg-amber-50 border border-amber-200 px-3 py-1.5">
                        <p className="text-xs text-amber-700">
                          Interest accrued: <b>{formatPHP(invoice.interest_accrued)}</b> · Rate: {invoice.interest_rate}%/mo
                        </p>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => onClose?.()}
                    className="w-7 h-7 rounded-full bg-slate-100 hover:bg-slate-200 flex items-center justify-center"
                    data-testid="invoice-modal-close"
                  >
                    <X size={14} className="text-slate-500" />
                  </button>
                </div>

                {/* Items */}
                <div className="rounded-xl border border-slate-200 overflow-hidden mb-4">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium text-slate-500">Item</th>
                        <th className="text-right px-3 py-2 font-medium text-slate-500 w-12">Qty</th>
                        <th className="text-right px-3 py-2 font-medium text-slate-500 w-20">Price</th>
                        <th className="text-right px-3 py-2 font-medium text-slate-500 w-20">Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(invoice.items || []).map((item, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="px-3 py-2">
                            <p className="font-medium">{item.product_name || item.name}</p>
                            {item.description && <p className="text-[10px] text-slate-400">{item.description}</p>}
                          </td>
                          <td className="px-3 py-2 text-right font-mono">{item.quantity}</td>
                          <td className="px-3 py-2 text-right font-mono">{formatPHP(item.rate || item.price || 0)}</td>
                          <td className="px-3 py-2 text-right font-mono font-semibold">{formatPHP(item.total || 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Totals */}
                <div className="space-y-1 mb-4">
                  {invoice.freight > 0 && (
                    <div className="flex justify-between text-xs text-slate-500">
                      <span>Freight</span><span className="font-mono">{formatPHP(invoice.freight)}</span>
                    </div>
                  )}
                  {invoice.overall_discount > 0 && (
                    <div className="flex justify-between text-xs text-emerald-600">
                      <span>Discount</span><span className="font-mono">-{formatPHP(invoice.overall_discount)}</span>
                    </div>
                  )}
                  <div className="flex justify-between text-sm font-bold border-t border-slate-200 pt-1.5 mt-1.5">
                    <span>Grand Total</span>
                    <span className="font-mono text-[#1A4D2E]">{formatPHP(invoice.grand_total)}</span>
                  </div>
                  <div className="flex justify-between text-xs text-slate-500">
                    <span>Amount Paid</span>
                    <span className="font-mono text-emerald-700">{formatPHP(invoice.amount_paid)}</span>
                  </div>
                  {invoice.balance > 0 && (
                    <div className="flex justify-between text-sm font-semibold text-amber-700">
                      <span>Balance Due</span><span className="font-mono">{formatPHP(invoice.balance)}</span>
                    </div>
                  )}
                </div>

                {/* Action buttons — only if not voided */}
                {!isVoided && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <button
                      onClick={handleRefund}
                      className="py-2.5 rounded-xl border border-amber-200 text-amber-700 hover:bg-amber-50 text-sm font-medium transition-colors flex items-center justify-center gap-2"
                      data-testid="invoice-refund-btn"
                    >
                      <RotateCcw size={14} /> Refund
                    </button>
                    <button
                      onClick={() => openVoidDialog(false)}
                      className="py-2.5 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 text-sm font-medium transition-colors flex items-center justify-center gap-2"
                      data-testid="invoice-void-btn"
                    >
                      <Ban size={14} /> Void
                    </button>
                    <button
                      onClick={() => openVoidDialog(true)}
                      className="py-2.5 rounded-xl border border-red-200 text-red-700 hover:bg-red-50 text-sm font-medium transition-colors flex items-center justify-center gap-2"
                      data-testid="invoice-void-reopen-btn"
                    >
                      <RefreshCw size={14} /> Void &amp; Re-open
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Void confirmation dialog */}
      {voidDialog && invoice && (
        <div
          className="fixed inset-0 flex items-center justify-center p-4"
          style={{ backgroundColor: 'rgba(0,0,0,0.7)', zIndex: 99999 }}
          onClick={e => { if (e.target === e.currentTarget) { setVoidDialog(false); } }}
          data-testid="invoice-void-dialog"
        >
          <div className="bg-white rounded-2xl shadow-2xl w-full p-5" style={{ maxWidth: '400px' }}>
            <p className="font-bold text-slate-800 mb-0.5">
              {reopenAfterVoid ? 'Void & Reopen Sale' : 'Void Sale'}
            </p>
            <p className="text-xs text-slate-500 mb-4">
              {invoice.invoice_number} · {formatPHP(invoice.grand_total)}
            </p>

            <div className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2.5 mb-4 text-xs text-amber-800">
              This will: reverse inventory, reverse cashflow
              {invoice.balance > 0 ? ', and reverse customer AR balance.' : '.'}
              {reopenAfterVoid && (
                <>
                  <br />
                  After voiding you will be taken to a new sale draft pre-filled with these items.
                </>
              )}
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium text-slate-600 block mb-1">Reason *</label>
                <textarea
                  value={voidReason}
                  onChange={e => setVoidReason(e.target.value)}
                  placeholder="e.g. Wrong item entered, customer cancelled..."
                  rows={2}
                  className="w-full border border-slate-200 rounded-xl px-3 py-2 text-sm focus:outline-none resize-none focus:ring-2 focus:ring-red-200"
                  data-testid="invoice-void-reason"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 block mb-1">Manager PIN *</label>
                <Input
                  type="password" autoComplete="new-password"
                  value={voidPin}
                  onChange={e => setVoidPin(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleVoid()}
                  placeholder="Enter manager PIN"
                  className="h-9"
                  autoFocus
                  data-testid="invoice-void-pin"
                />
              </div>
            </div>

            <div className="flex gap-2 mt-4">
              <button
                onClick={() => setVoidDialog(false)}
                className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-600 hover:bg-slate-50"
                data-testid="invoice-void-cancel"
              >
                Cancel
              </button>
              <button
                onClick={handleVoid}
                disabled={voidSaving || !voidReason || !voidPin}
                className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-700 text-white text-sm font-semibold disabled:opacity-50 flex items-center justify-center gap-2"
                data-testid="invoice-void-confirm"
              >
                {voidSaving ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {reopenAfterVoid ? 'Void & Reopen' : 'Void'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
