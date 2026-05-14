/**
 * POSPurchaseDetailDialog — read-only PO detail viewer + reprint trigger.
 *
 * Mirrors the InvoiceDetailModal pattern but for `db.purchase_orders` rows.
 * Used by the Purchase History view inside the POS terminal so the cashier
 * can drill into any PO without leaving the terminal.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import {
  Printer, RefreshCw, ExternalLink, Truck, Calendar, Building2, FileText,
} from 'lucide-react';
import { formatPHP, fmtDateTime, fmtDate } from '../lib/utils';
import PrintEngine from '../lib/PrintEngine';
import { toast } from 'sonner';

const STATUS_BADGE = {
  draft:     'bg-slate-200 text-slate-600',
  open:      'bg-blue-100 text-blue-700',
  partial:   'bg-amber-100 text-amber-700',
  received:  'bg-emerald-100 text-emerald-700',
  cancelled: 'bg-rose-100 text-rose-600',
};

export default function POSPurchaseDetailDialog({ api, poId, open, onClose }) {
  const [po, setPO] = useState(null);
  const [loading, setLoading] = useState(false);
  const [businessInfo, setBusinessInfo] = useState(null);

  const load = useCallback(async () => {
    if (!poId) return;
    setLoading(true);
    try {
      const [poRes, bizRes] = await Promise.all([
        api.get(`/purchase-orders/${poId}`),
        api.get('/settings/business-info').catch(() => ({ data: {} })),
      ]);
      setPO(poRes.data);
      setBusinessInfo(bizRes.data);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to load PO');
    } finally {
      setLoading(false);
    }
  }, [poId]);

  useEffect(() => {
    if (open && poId) load();
    if (!open) setPO(null);
  }, [open, poId, load]);

  const handlePrint = (format = 'full_page') => {
    if (!po) return;
    try {
      PrintEngine.print({
        type: 'purchase_order',
        data: po,
        format,
        businessInfo: businessInfo || {},
        docCode: po.doc_code || '',
      });
    } catch (e) {
      toast.error('Failed to open print preview');
    }
  };

  const items = po?.items || [];
  const status = (po?.status || '').toLowerCase();
  const cancelled = status === 'cancelled';

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose && onClose(); }}>
      <DialogContent
        className="max-w-3xl max-h-[90vh] overflow-y-auto"
        data-testid="pos-purchase-detail-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <Truck className="text-[#1A4D2E]" size={18} />
            Purchase Order Detail
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="text-center py-12">
            <RefreshCw size={20} className="animate-spin mx-auto text-slate-400" />
            <p className="text-xs text-slate-400 mt-2">Loading…</p>
          </div>
        ) : !po ? (
          <div className="text-center py-12 text-slate-400 text-sm">
            <FileText size={28} className="mx-auto mb-2 opacity-40" />
            Could not load this purchase order.
          </div>
        ) : (
          <div className="space-y-4">
            {/* Header strip */}
            <div className="flex flex-wrap items-start justify-between gap-3 pb-3 border-b border-slate-100">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-lg font-bold text-blue-700">{po.po_number}</span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase ${STATUS_BADGE[status] || STATUS_BADGE.open}`}>
                    {status || 'open'}
                  </span>
                  {po.po_type && (
                    <Badge variant="outline" className="text-[10px] uppercase">{po.po_type}</Badge>
                  )}
                </div>
                <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                  <span className="inline-flex items-center gap-1">
                    <Calendar size={11} />{fmtDate(po.date || po.created_at)}
                  </span>
                  {po.vendor && (
                    <span className="inline-flex items-center gap-1">
                      <Building2 size={11} />{po.vendor}
                    </span>
                  )}
                  {po.created_by_name && (
                    <span className="text-slate-400">· {po.created_by_name}</span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline" size="sm"
                  onClick={() => handlePrint('full_page')}
                  data-testid="pos-purchase-detail-print-btn"
                >
                  <Printer size={14} className="mr-1.5" /> Print
                </Button>
                {po.doc_code && (
                  <Button
                    variant="outline" size="sm"
                    onClick={() => window.open(`/doc/${po.doc_code}`, '_blank')}
                  >
                    <ExternalLink size={14} className="mr-1.5" /> Open
                  </Button>
                )}
              </div>
            </div>

            {/* Items table */}
            <div className="border border-slate-200 rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50">
                  <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 w-10 text-center">#</th>
                    <th className="px-3 py-2">Product</th>
                    <th className="px-3 py-2 text-right">Qty</th>
                    <th className="px-3 py-2 text-right">Unit Cost</th>
                    <th className="px-3 py-2 text-right">Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => {
                    const qty = parseFloat(it.qty || it.quantity) || 0;
                    const cost = parseFloat(it.unit_cost || it.cost) || 0;
                    const sub = parseFloat(it.subtotal) || qty * cost;
                    return (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-3 py-1.5 text-center text-[11px] text-slate-400">{i + 1}</td>
                        <td className="px-3 py-1.5">
                          <div className="font-medium text-slate-800">{it.product_name || it.name || ''}</div>
                          {it.unit && <div className="text-[10px] text-slate-400">{it.unit}</div>}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono">{qty}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{formatPHP(cost)}</td>
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

            {/* Totals */}
            <div className="flex justify-end">
              <div className="w-72 text-sm space-y-1">
                {po.subtotal !== undefined && (
                  <div className="flex justify-between text-slate-600">
                    <span>Subtotal</span>
                    <span className="font-mono">{formatPHP(po.subtotal)}</span>
                  </div>
                )}
                {po.freight > 0 && (
                  <div className="flex justify-between text-slate-600">
                    <span>Freight</span>
                    <span className="font-mono">{formatPHP(po.freight)}</span>
                  </div>
                )}
                {po.overall_discount_amount > 0 && (
                  <div className="flex justify-between text-slate-600">
                    <span>Discount</span>
                    <span className="font-mono text-emerald-700">- {formatPHP(po.overall_discount_amount)}</span>
                  </div>
                )}
                {po.vat_amount > 0 && (
                  <div className="flex justify-between text-slate-600">
                    <span>VAT</span>
                    <span className="font-mono">{formatPHP(po.vat_amount)}</span>
                  </div>
                )}
                <div className="flex justify-between border-t border-slate-200 pt-1 mt-1 font-bold text-base">
                  <span>Grand Total</span>
                  <span className={`font-mono ${cancelled ? 'line-through text-slate-400' : 'text-slate-900'}`}>
                    {formatPHP(po.grand_total)}
                  </span>
                </div>
                {po.balance > 0 && !cancelled && (
                  <div className="flex justify-between text-sm text-amber-600 font-semibold pt-1">
                    <span>Outstanding</span>
                    <span className="font-mono">{formatPHP(po.balance)}</span>
                  </div>
                )}
                {(po.balance <= 0 && !cancelled) && (
                  <div className="text-right text-[11px] text-emerald-600 font-semibold">PAID IN FULL</div>
                )}
              </div>
            </div>

            {/* Footer meta */}
            <div className="text-[10px] text-slate-400 flex flex-wrap gap-x-3 gap-y-1 pt-3 border-t border-slate-100">
              {po.created_at && <span>Created: {fmtDateTime(po.created_at)}</span>}
              {po.received_at && <span>Received: {fmtDateTime(po.received_at)}</span>}
              {po.received_by_name && <span>by {po.received_by_name}</span>}
              {po.cancelled_at && <span className="text-rose-500">Cancelled: {fmtDateTime(po.cancelled_at)}</span>}
              {po.doc_code && <span className="font-mono">· {po.doc_code}</span>}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
