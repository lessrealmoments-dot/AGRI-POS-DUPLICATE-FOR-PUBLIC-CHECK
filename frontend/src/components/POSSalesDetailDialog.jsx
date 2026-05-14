/**
 * POSSalesDetailDialog — terminal-scoped read-only invoice viewer + reprint.
 *
 * Uses the terminal's own `api` instance so requests carry the terminal
 * token + branch scope, NOT the AuthContext user. Mirrors the columns the
 * cashier already knows from the QR doc page so the layout is familiar.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import {
  Printer, RefreshCw, FileText, Calendar, User as UserIcon,
} from 'lucide-react';
import { formatPHP, fmtDateTime, fmtDate } from '../lib/utils';
import PrintEngine from '../lib/PrintEngine';
import { toast } from 'sonner';

export default function POSSalesDetailDialog({ api, invoiceId, open, onClose, businessInfo }) {
  const [inv, setInv] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!invoiceId) return;
    setLoading(true);
    try {
      const res = await api.get(`/invoices/${invoiceId}`);
      setInv(res.data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to load invoice');
    } finally {
      setLoading(false);
    }
  }, [api, invoiceId]);

  useEffect(() => {
    if (open && invoiceId) load();
    if (!open) setInv(null);
  }, [open, invoiceId, load]);

  const handlePrint = (format = 'full_page') => {
    if (!inv) return;
    try {
      const docType = PrintEngine.getDocType ? PrintEngine.getDocType(inv) : 'order_slip';
      PrintEngine.print({
        type: docType,
        data: inv,
        format,
        businessInfo: businessInfo || {},
        docCode: inv.doc_code || '',
      });
    } catch {
      toast.error('Failed to open print preview');
    }
  };

  const items = inv?.items || [];
  const voided = inv?.status === 'voided';

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose && onClose(); }}>
      <DialogContent
        className="max-w-3xl max-h-[90vh] overflow-y-auto"
        data-testid="pos-sales-detail-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <FileText className="text-[#1A4D2E]" size={18} />
            Invoice Detail
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="text-center py-12">
            <RefreshCw size={20} className="animate-spin mx-auto text-slate-400" />
            <p className="text-xs text-slate-400 mt-2">Loading…</p>
          </div>
        ) : !inv ? (
          <div className="text-center py-12 text-slate-400 text-sm">
            <FileText size={28} className="mx-auto mb-2 opacity-40" />
            Could not load this invoice.
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3 pb-3 border-b border-slate-100">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-lg font-bold text-blue-700">{inv.invoice_number}</span>
                  {voided
                    ? <span className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase bg-slate-200 text-slate-500">Voided</span>
                    : <span className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase bg-emerald-100 text-emerald-700">
                        {inv.payment_type || 'cash'}
                      </span>}
                  {inv.balance > 0 && !voided && (
                    <Badge variant="outline" className="text-[10px] text-amber-700 border-amber-300">
                      bal {formatPHP(inv.balance)}
                    </Badge>
                  )}
                </div>
                <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                  <span className="inline-flex items-center gap-1">
                    <Calendar size={11} />{fmtDate(inv.invoice_date || inv.order_date || inv.created_at)}
                  </span>
                  {inv.customer_name && (
                    <span className="inline-flex items-center gap-1">
                      <UserIcon size={11} />{inv.customer_name}
                    </span>
                  )}
                  {inv.cashier_name && <span className="text-slate-400">· {inv.cashier_name}</span>}
                </div>
                {voided && inv.void_reason && (
                  <div className="mt-1 text-[11px] text-rose-600 bg-rose-50 border border-rose-200 px-2 py-1 rounded">
                    Voided: {inv.void_reason}
                    {inv.void_authorized_by && <> · by {inv.void_authorized_by}</>}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => handlePrint('full_page')} data-testid="pos-sales-detail-print-btn">
                  <Printer size={14} className="mr-1.5" /> Print
                </Button>
              </div>
            </div>

            <div className="border border-slate-200 rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50">
                  <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 w-10 text-center">#</th>
                    <th className="px-3 py-2">Product</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Unit Price</th>
                    <th className="px-3 py-2 text-right">Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => {
                    const qty = parseFloat(it.quantity || it.qty) || 0;
                    const rate = parseFloat(it.rate || it.unit_price || it.price) || 0;
                    const sub = parseFloat(it.total) || qty * rate;
                    return (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-3 py-1.5 text-center text-[11px] text-slate-400">{i + 1}</td>
                        <td className="px-3 py-1.5">
                          <div className="font-medium text-slate-800">{it.product_name || ''}</div>
                          {it.description && <div className="text-[10px] text-slate-400">{it.description}</div>}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">{qty}{it.unit ? ' ' + it.unit : ''}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{formatPHP(rate)}</td>
                        <td className="px-3 py-1.5 text-right font-mono font-semibold">{formatPHP(sub)}</td>
                      </tr>
                    );
                  })}
                  {items.length === 0 && (
                    <tr><td colSpan={5} className="px-3 py-6 text-center text-xs text-slate-400">No line items</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end">
              <div className="w-72 text-sm space-y-1">
                {inv.subtotal !== undefined && (
                  <div className="flex justify-between text-slate-600">
                    <span>Subtotal</span>
                    <span className="font-mono">{formatPHP(inv.subtotal)}</span>
                  </div>
                )}
                {inv.freight > 0 && (
                  <div className="flex justify-between text-slate-600">
                    <span>Freight</span><span className="font-mono">{formatPHP(inv.freight)}</span>
                  </div>
                )}
                {inv.overall_discount_amount > 0 && (
                  <div className="flex justify-between text-slate-600">
                    <span>Discount</span>
                    <span className="font-mono text-emerald-700">- {formatPHP(inv.overall_discount_amount)}</span>
                  </div>
                )}
                <div className="flex justify-between border-t border-slate-200 pt-1 mt-1 font-bold text-base">
                  <span>Grand Total</span>
                  <span className={`font-mono ${voided ? 'line-through text-slate-400' : 'text-slate-900'}`}>
                    {formatPHP(inv.grand_total)}
                  </span>
                </div>
                {inv.amount_paid > 0 && (
                  <div className="flex justify-between text-emerald-700 text-xs">
                    <span>Paid</span><span className="font-mono">{formatPHP(inv.amount_paid)}</span>
                  </div>
                )}
                {inv.balance > 0 && !voided && (
                  <div className="flex justify-between text-amber-700 text-sm font-semibold pt-1">
                    <span>Balance</span><span className="font-mono">{formatPHP(inv.balance)}</span>
                  </div>
                )}
              </div>
            </div>

            <div className="text-[10px] text-slate-400 flex flex-wrap gap-x-3 gap-y-1 pt-3 border-t border-slate-100">
              {inv.created_at && <span>Created: {fmtDateTime(inv.created_at)}</span>}
              {inv.doc_code && <span className="font-mono">· {inv.doc_code}</span>}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
