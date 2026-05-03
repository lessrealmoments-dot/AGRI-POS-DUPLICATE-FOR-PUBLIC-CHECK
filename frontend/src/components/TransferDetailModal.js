import { useNavigate } from 'react-router-dom';
import { formatPHP, fmtDateTime, fmtDate } from '../lib/utils';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { ScrollArea } from './ui/scroll-area';
import {
  AlertTriangle, ArrowRight, Clock, Package, ClipboardCheck, Printer, CheckCircle2
} from 'lucide-react';

const STATUS_COLORS = {
  draft: 'bg-slate-100 text-slate-600',
  sent: 'bg-blue-100 text-blue-700',
  sent_to_terminal: 'bg-amber-100 text-amber-700',
  received_pending: 'bg-yellow-100 text-yellow-700',
  received: 'bg-emerald-100 text-emerald-700',
  disputed: 'bg-red-100 text-red-700',
  cancelled: 'bg-red-100 text-red-600',
};

export default function TransferDetailModal({ transfer, open, onOpenChange, branches = [], onPrint }) {
  const navigate = useNavigate();
  if (!transfer) return null;

  const fromBranch = branches.find(b => b.id === transfer.from_branch_id)?.name || transfer.from_branch_name || '?';
  const toBranch = branches.find(b => b.id === transfer.to_branch_id)?.name || transfer.to_branch_name || '?';

  // ── Status Timeline ──
  const status = transfer.status;
  const timelineSteps = [
    { key: 'requested', label: 'Requested', done: true, date: fmtDate(transfer.created_at), by: transfer.created_by_name },
    { key: 'draft', label: 'Transfer Created', done: ['draft', 'sent', 'received_pending', 'received', 'disputed'].includes(status), date: fmtDate(transfer.created_at), by: transfer.created_by_name },
    { key: 'sent', label: status === 'sent_to_terminal' ? 'On Terminal' : 'Sent',
      done: ['sent', 'sent_to_terminal', 'received_pending', 'received', 'disputed'].includes(status), date: fmtDate(transfer.sent_at),
      variant: status === 'sent_to_terminal' ? 'warning' : undefined },
    { key: 'received', label: status === 'received_pending' ? 'Pending Review' : status === 'disputed' ? 'Disputed' : 'Received',
      done: ['received_pending', 'received', 'disputed'].includes(status),
      date: fmtDate(transfer.received_at) || fmtDate(transfer.pending_receipt_at),
      by: transfer.received_by_name || transfer.pending_receipt_by_name,
      variant: status === 'disputed' ? 'error' : status === 'received_pending' ? 'warning' : 'success' },
    { key: 'settled', label: 'Settled', done: status === 'received' && !transfer.has_shortage, date: fmtDate(transfer.received_at) },
  ];
  const filteredSteps = transfer.request_po_id ? timelineSteps : timelineSteps.filter(s => s.key !== 'requested');
  const currentIdx = filteredSteps.findIndex(s => !s.done) - 1;

  // ── Dispute / Variance History ──
  const disputeEvents = [];
  if (transfer.pending_receipt_at) {
    disputeEvents.push({
      type: 'counted', label: 'First Count Submitted', by: transfer.pending_receipt_by_name,
      at: transfer.pending_receipt_at, note: transfer.receive_notes, icon: 'count',
      shortages: transfer.shortages, excesses: transfer.excesses,
    });
  }
  if (transfer.dispute_note || transfer.disputed_at) {
    disputeEvents.push({
      type: 'disputed', label: 'Disputed by Source', by: transfer.disputed_by_name,
      at: transfer.disputed_at, note: transfer.dispute_note, icon: 'dispute',
    });
  }
  if (transfer.disputed_at && transfer.received_at && transfer.received_at > transfer.disputed_at) {
    disputeEvents.push({
      type: 're-counted', label: 'Re-count Submitted',
      by: transfer.received_by_name || transfer.pending_receipt_by_name,
      at: transfer.received_at, icon: 'recount',
    });
  }
  if (transfer.accepted_at) {
    disputeEvents.push({
      type: 'accepted',
      label: transfer.incident_ticket_number ? 'Accepted + Incident Created' : 'Variance Accepted',
      by: transfer.accepted_by_name, at: transfer.accepted_at, note: transfer.accept_note,
      icon: 'accept', ticket: transfer.incident_ticket_number,
    });
  }
  const showDisputeHistory = transfer.dispute_note || transfer.has_shortage || transfer.has_excess || transfer.pending_receipt_at;

  // ── Reconciliation view (for received/received_pending) ──
  const isReconciliation = transfer.status === 'received' || transfer.status === 'received_pending';
  const items = transfer.pending_items || transfer.items || [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col" data-testid="transfer-detail-modal">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Manrope' }}>
            {transfer.order_number} — {fromBranch}
            <span className="text-slate-400 mx-1">&rarr;</span>
            {toBranch}
            <Badge className={`ml-2 text-[10px] ${STATUS_COLORS[status]}`}>{status}</Badge>
            {transfer.has_shortage && (
              <Badge className="ml-2 text-[10px] bg-red-100 text-red-700">Shortage</Badge>
            )}
            {transfer.incident_ticket_number && (
              <Badge className="ml-2 text-[10px] bg-amber-100 text-amber-700">{transfer.incident_ticket_number}</Badge>
            )}
          </DialogTitle>
        </DialogHeader>

        {/* ── Status Timeline ── */}
        <div className="flex items-center gap-0 px-2 py-2 mb-1 bg-slate-50 rounded-lg overflow-x-auto" data-testid="transfer-detail-timeline">
          {filteredSteps.map((step, i) => {
            const isActive = i === currentIdx || (i === filteredSteps.length - 1 && step.done);
            const variantColors = { error: 'bg-red-500', warning: 'bg-amber-500', success: 'bg-emerald-500' };
            const dotColor = step.done ? (step.variant ? variantColors[step.variant] : 'bg-emerald-500') : 'bg-slate-300';
            const lineColor = step.done ? 'bg-emerald-400' : 'bg-slate-200';
            return (
              <div key={step.key} className="flex items-center flex-1 min-w-0">
                <div className="flex flex-col items-center">
                  <div className={`w-3 h-3 rounded-full ${dotColor} ${isActive ? 'ring-2 ring-offset-1 ring-emerald-300' : ''}`} />
                  <p className={`text-[10px] mt-1 text-center leading-tight whitespace-nowrap ${step.done ? 'text-slate-700 font-semibold' : 'text-slate-400'}`}>{step.label}</p>
                  {step.done && step.date && <p className="text-[9px] text-slate-400">{step.date}</p>}
                </div>
                {i < filteredSteps.length - 1 && (
                  <div className={`flex-1 h-0.5 mx-1 ${lineColor} rounded`} />
                )}
              </div>
            );
          })}
        </div>

        {/* Request reference */}
        {transfer.request_po_id && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded-lg text-xs">
            <Package size={13} className="text-blue-600" />
            <span className="text-blue-700">From stock request: <b>{transfer.request_po_number}</b></span>
          </div>
        )}

        {/* Incident ticket link */}
        {transfer.incident_ticket_number && (
          <div className="flex items-center justify-between px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs cursor-pointer hover:bg-amber-100 transition-colors"
            onClick={() => navigate('/incident-tickets')} data-testid="transfer-detail-ticket-link">
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} className="text-amber-600" />
              <span className="text-amber-800">Incident Ticket: <b>{transfer.incident_ticket_number}</b></span>
            </div>
            <ArrowRight size={14} className="text-amber-500" />
          </div>
        )}

        {/* ── Dispute / Variance History Timeline ── */}
        {showDisputeHistory && disputeEvents.length > 0 && (
          <div className="bg-slate-50 rounded-lg border border-slate-200 p-3 space-y-0" data-testid="transfer-detail-dispute-history">
            <p className="text-[11px] font-semibold text-slate-600 mb-2 flex items-center gap-1.5">
              <Clock size={12} className="text-slate-400" /> Variance History
            </p>
            <div className="relative pl-4 space-y-3">
              <div className="absolute left-[7px] top-1 bottom-1 w-px bg-slate-300" />
              {disputeEvents.map((ev, i) => {
                const colors = { count: 'bg-blue-500', dispute: 'bg-red-500', recount: 'bg-amber-500', accept: 'bg-emerald-500' };
                return (
                  <div key={i} className="relative">
                    <div className={`absolute -left-4 top-0.5 w-2.5 h-2.5 rounded-full ${colors[ev.icon] || 'bg-slate-400'} ring-2 ring-white`} />
                    <div className="ml-2">
                      <p className="text-xs font-semibold text-slate-700">{ev.label}</p>
                      <p className="text-[10px] text-slate-500">
                        {ev.by && <span>{ev.by} &middot; </span>}
                        {fmtDateTime(ev.at)}
                      </p>
                      {ev.note && <p className="text-[10px] text-slate-500 mt-0.5 italic">&quot;{ev.note}&quot;</p>}
                      {ev.ticket && (
                        <button onClick={() => navigate('/incident-tickets')} className="text-[10px] text-amber-700 font-medium mt-0.5 hover:underline flex items-center gap-1">
                          <AlertTriangle size={10} /> {ev.ticket}
                        </button>
                      )}
                      {ev.shortages && ev.shortages.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {ev.shortages.map((s, j) => (
                            <span key={j} className="text-[10px] bg-amber-100 text-amber-700 rounded px-1.5 py-0.5">
                              {s.product_name}: -{s.variance} {s.unit}
                            </span>
                          ))}
                        </div>
                      )}
                      {ev.excesses && ev.excesses.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {ev.excesses.map((s, j) => (
                            <span key={j} className="text-[10px] bg-blue-100 text-blue-700 rounded px-1.5 py-0.5">
                              {s.product_name}: +{Math.abs(s.variance)} {s.unit}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Invoice reference */}
        {transfer.invoice_number && (
          <div className="flex items-center justify-between px-3 py-1.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs">
            <span className="text-emerald-700 flex items-center gap-2">
              <ClipboardCheck size={13} className="text-emerald-600" />
              Invoice: <b>{transfer.invoice_number}</b>
              <span className="text-slate-400">|</span>
              Terms: Net 15
            </span>
          </div>
        )}

        {/* ── Items Table ── */}
        <ScrollArea className="flex-1">
          {isReconciliation ? (
            <div className="space-y-3">
              {transfer.status === 'received_pending' && (
                <div className="p-3 rounded-lg bg-amber-50 border border-amber-300 text-sm text-amber-800">
                  <p className="font-semibold flex items-center gap-1.5">
                    <AlertTriangle size={14} /> Pending Review
                  </p>
                  <p className="text-xs mt-1">
                    {toBranch} submitted received quantities with a variance.
                  </p>
                  {transfer.receive_notes && (
                    <p className="text-xs mt-1 text-amber-600">Receiver&apos;s note: &quot;{transfer.receive_notes}&quot;</p>
                  )}
                </div>
              )}
              <div className="text-xs text-slate-500 bg-slate-50 rounded px-3 py-2 flex justify-between">
                <span>
                  {transfer.status === 'received_pending'
                    ? <>Counted by: <b>{transfer.pending_receipt_by_name}</b> &middot; {fmtDateTime(transfer.pending_receipt_at)}</>
                    : <>Received by: <b>{transfer.received_by_name}</b> &middot; {fmtDate(transfer.received_at)}</>
                  }
                </span>
                {transfer.has_shortage && (
                  <span className="text-red-600 font-medium flex items-center gap-1">
                    <AlertTriangle size={12} /> {transfer.shortages?.length} product(s) short
                  </span>
                )}
              </div>
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white border-b">
                  <tr className="text-[10px] uppercase text-slate-500">
                    <th className="px-3 py-2 text-left">Product</th>
                    <th className="px-3 py-2 text-right">Ordered</th>
                    <th className="px-3 py-2 text-right">Received</th>
                    <th className="px-3 py-2 text-right font-semibold">Variance</th>
                    <th className="px-3 py-2 text-right">Capital/unit</th>
                    <th className="px-3 py-2 text-right text-red-600">Capital Loss</th>
                    <th className="px-3 py-2 text-right">Retail/unit</th>
                    <th className="px-3 py-2 text-right text-red-600">Retail Loss</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, i) => {
                    const qtyOrdered = item.qty_ordered ?? item.qty;
                    const qtyReceived = item.qty_received ?? item.qty;
                    const variance = qtyOrdered - qtyReceived;
                    const capLoss = variance * item.transfer_capital;
                    const retLoss = variance * item.branch_retail;
                    const hasShortage = variance > 0;
                    return (
                      <tr key={i} className={`border-b border-slate-50 ${hasShortage ? 'bg-red-50/40' : 'hover:bg-slate-50'}`}>
                        <td className="px-3 py-2">
                          <p className="font-medium">{item.product_name}</p>
                          <p className="text-[10px] text-slate-400 font-mono">{item.sku} &middot; {item.category}</p>
                        </td>
                        <td className="px-3 py-2 text-right font-mono">{qtyOrdered} {item.unit}</td>
                        <td className="px-3 py-2 text-right font-mono font-bold">{qtyReceived} {item.unit}</td>
                        <td className={`px-3 py-2 text-right font-mono font-bold ${variance > 0 ? 'text-red-600' : variance < 0 ? 'text-blue-600' : 'text-emerald-600'}`}>
                          {variance === 0 ? '\u2713 OK' : variance > 0 ? `-${variance}` : `+${Math.abs(variance)}`}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.transfer_capital)}</td>
                        <td className={`px-3 py-2 text-right font-mono font-bold ${capLoss > 0 ? 'text-red-600' : 'text-slate-300'}`}>
                          {capLoss > 0 ? `-${formatPHP(capLoss)}` : '\u2014'}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.branch_retail)}</td>
                        <td className={`px-3 py-2 text-right font-mono font-bold ${retLoss > 0 ? 'text-red-600' : 'text-slate-300'}`}>
                          {retLoss > 0 ? `-${formatPHP(retLoss)}` : '\u2014'}
                        </td>
                      </tr>
                    );
                  })}
                  {/* Totals row */}
                  {(() => {
                    const totalCapLoss = items.reduce((s, i) => {
                      const v = (i.qty_ordered ?? i.qty) - (i.qty_received ?? i.qty);
                      return s + (v > 0 ? v * i.transfer_capital : 0);
                    }, 0);
                    const totalRetLoss = items.reduce((s, i) => {
                      const v = (i.qty_ordered ?? i.qty) - (i.qty_received ?? i.qty);
                      return s + (v > 0 ? v * i.branch_retail : 0);
                    }, 0);
                    return (
                      <tr className="bg-slate-100 font-bold border-t-2 border-slate-300 text-sm">
                        <td className="px-3 py-2" colSpan={5}>Expected Losses (shortage)</td>
                        <td className="px-3 py-2 text-right font-mono text-red-700">{totalCapLoss > 0 ? `-${formatPHP(totalCapLoss)}` : '\u20B10.00'}</td>
                        <td className="px-3 py-2"></td>
                        <td className="px-3 py-2 text-right font-mono text-red-700">{totalRetLoss > 0 ? `-${formatPHP(totalRetLoss)}` : '\u20B10.00'}</td>
                      </tr>
                    );
                  })()}
                </tbody>
              </table>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white border-b">
                <tr className="text-xs uppercase text-slate-500">
                  <th className="px-3 py-2 text-left">Product</th>
                  <th className="px-3 py-2 text-center">Qty</th>
                  <th className="px-3 py-2 text-right">Branch Capital</th>
                  <th className="px-3 py-2 text-right">Transfer Capital</th>
                  <th className="px-3 py-2 text-right">Branch Retail</th>
                  <th className="px-3 py-2 text-right">Margin</th>
                </tr>
              </thead>
              <tbody>
                {(transfer.items || []).map((item, i) => {
                  const margin = item.branch_retail - item.transfer_capital;
                  return (
                    <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                      <td className="px-3 py-2">
                        <p className="font-medium">{item.product_name}</p>
                        <p className="text-xs text-slate-400 font-mono">{item.sku} &middot; {item.category}</p>
                      </td>
                      <td className="px-3 py-2 text-center font-mono">{item.qty} {item.unit}</td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(item.branch_capital)}</td>
                      <td className="px-3 py-2 text-right font-mono font-bold text-blue-700">{formatPHP(item.transfer_capital)}</td>
                      <td className="px-3 py-2 text-right font-mono font-bold text-emerald-700">{formatPHP(item.branch_retail)}</td>
                      <td className={`px-3 py-2 text-right font-mono font-bold ${margin >= 20 ? 'text-emerald-600' : 'text-red-500'}`}>
                        +{formatPHP(margin)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </ScrollArea>

        {/* Footer with print button */}
        <div className="pt-3 border-t flex justify-end">
          {onPrint && (
            <Button variant="outline" onClick={() => onPrint(transfer)} data-testid="transfer-detail-print-btn">
              <Printer size={14} className="mr-1.5" /> Print Transfer Order
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
