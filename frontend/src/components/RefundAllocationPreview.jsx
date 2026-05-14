/**
 * RefundAllocationPreview — shows how a refund will be routed to AR, digital
 * channels, and cash before the cashier confirms a Return/Correction.
 *
 * Renders nothing until refundAmount > 0. Mirrors what the backend
 * `compute_refund_allocation` will compute on submit, so the preview is the
 * exact disbursement the cashier should expect.
 */
import React, { useMemo } from 'react';
import { formatPHP } from '../lib/utils';
import { computeRefundAllocation } from '../lib/refundAllocator';
import { Banknote, Smartphone, FileText, AlertTriangle } from 'lucide-react';

export default function RefundAllocationPreview({ invoice, refundAmount }) {
  const allocation = useMemo(
    () => computeRefundAllocation(invoice || {}, refundAmount),
    [invoice, refundAmount]
  );

  if (!refundAmount || refundAmount <= 0) return null;

  const { ar_reduction, cash_refund, digital_refunds, remaining_unallocated, summary } = allocation;
  const digitalTotal = digital_refunds.reduce((s, d) => s + d.amount, 0);

  return (
    <div
      className="rounded-xl border-2 border-blue-200 bg-blue-50/60 overflow-hidden"
      data-testid="refund-allocation-preview"
    >
      <div className="px-4 py-2.5 bg-blue-100/70 border-b border-blue-200 flex items-center gap-2">
        <span className="text-xs font-bold text-blue-800 uppercase tracking-wide">
          Refund Routing
        </span>
        <span className="text-[10px] text-blue-700 ml-auto">
          Total to refund: <span className="font-mono font-bold">{formatPHP(refundAmount)}</span>
        </span>
      </div>
      <div className="px-4 py-3 space-y-2">
        {/* Channel-by-channel breakdown */}
        {ar_reduction > 0 && (
          <div className="flex items-center justify-between text-sm" data-testid="alloc-ar-row">
            <span className="flex items-center gap-1.5 text-emerald-700">
              <FileText size={13} />
              Reduce open balance (no money moves)
            </span>
            <span className="font-mono font-semibold text-emerald-700">
              {formatPHP(ar_reduction)}
            </span>
          </div>
        )}

        {digital_refunds.map((d, i) => (
          <div
            key={`${d.method}-${i}`}
            className="flex items-center justify-between text-sm"
            data-testid={`alloc-digital-row-${i}`}
          >
            <span className="flex items-center gap-1.5 text-blue-700">
              <Smartphone size={13} />
              Reverse {d.method || d.platform || 'Digital'}
              {d.ref_number ? <span className="text-xs text-blue-500 ml-1">· {d.ref_number}</span> : null}
            </span>
            <span className="font-mono font-semibold text-blue-700">
              {formatPHP(d.amount)}
            </span>
          </div>
        ))}

        {cash_refund > 0 && (
          <div className="flex items-center justify-between text-sm" data-testid="alloc-cash-row">
            <span className="flex items-center gap-1.5 text-amber-700">
              <Banknote size={13} />
              Refund from cashier drawer
            </span>
            <span className="font-mono font-semibold text-amber-700">
              {formatPHP(cash_refund)}
            </span>
          </div>
        )}

        {remaining_unallocated > 0 && (
          <div
            className="flex items-start justify-between text-sm bg-rose-50 border border-rose-200 rounded-md px-2 py-1.5"
            data-testid="alloc-unallocated-row"
          >
            <span className="flex items-center gap-1.5 text-rose-700">
              <AlertTriangle size={13} />
              Cannot be allocated (exceeds paid amount)
            </span>
            <span className="font-mono font-semibold text-rose-700">
              {formatPHP(remaining_unallocated)}
            </span>
          </div>
        )}

        {ar_reduction === 0 && cash_refund === 0 && digital_refunds.length === 0 && (
          <p className="text-xs text-slate-500 italic" data-testid="alloc-empty-row">
            Nothing to disburse for this refund amount.
          </p>
        )}

        {/* Context strip — original payment breakdown */}
        <div className="pt-2 mt-1 border-t border-blue-200/70 text-[11px] text-slate-500 flex flex-wrap gap-x-3 gap-y-1">
          <span>
            Originally — Cash: <b className="font-mono text-slate-700">{formatPHP(summary.cash_paid)}</b>
          </span>
          <span>
            Digital: <b className="font-mono text-slate-700">{formatPHP(summary.digital_paid)}</b>
          </span>
          <span>
            Open AR: <b className="font-mono text-slate-700">{formatPHP(summary.open_balance)}</b>
          </span>
        </div>
      </div>
    </div>
  );
}
