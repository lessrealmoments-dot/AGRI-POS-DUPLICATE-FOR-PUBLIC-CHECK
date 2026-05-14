/**
 * TerminalHistoryOverlay — full-screen sheet launched from the POS Terminal
 * Mode Selector. Hosts Sales History + Purchase History views behind a
 * single PIN unlock gate, and routes detail dialogs through to the
 * terminal's own `api` instance (so branch + token scoping match the
 * terminal pairing, not the AuthContext user).
 *
 * Mounted by TerminalShell.jsx; toggled via the "History" entry in the
 * floating mode menu (lower-left). When closed, the unlock state is
 * preserved via pinSession so re-opening within 30 min won't re-prompt.
 */
import React, { useState } from 'react';
import { X, FileText, Truck, ShieldCheck } from 'lucide-react';
import { Button } from './ui/button';
import POSHistoryUnlockGate from './POSHistoryUnlockGate';
import POSSalesHistoryView from './POSSalesHistoryView';
import POSPurchaseHistoryView from './POSPurchaseHistoryView';
import POSSalesDetailDialog from './POSSalesDetailDialog';
import POSPurchaseDetailDialog from './POSPurchaseDetailDialog';

export default function TerminalHistoryOverlay({
  api,
  open,
  onClose,
  branch,                // { id, name }
  isOnline,
  businessInfo,
  pinSession,
  startPinSession,
}) {
  const [view, setView] = useState('sales'); // 'sales' | 'purchase'
  const [openInvoiceId, setOpenInvoiceId] = useState(null);
  const [openPOId, setOpenPOId] = useState(null);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 bg-white flex flex-col safe-area-top safe-area-bottom"
      data-testid="terminal-history-overlay"
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-200 bg-white">
        <ShieldCheck className="text-[#1A4D2E]" size={18} />
        <h2 className="text-base font-semibold text-slate-800 flex-1" style={{ fontFamily: 'Manrope' }}>
          Transaction History
          <span className="ml-2 text-[11px] font-normal text-slate-400">
            {branch?.name ? `· ${branch.name}` : ''}
          </span>
        </h2>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          data-testid="terminal-history-close"
          className="text-slate-500 hover:bg-slate-100"
        >
          <X size={18} />
        </Button>
      </div>

      {/* Toggle: Sales / Purchase */}
      <div className="px-4 pt-3">
        <div className="inline-flex items-center bg-slate-100 rounded-xl p-1 shadow-inner ring-1 ring-slate-200/40">
          <button
            onClick={() => setView('sales')}
            data-testid="terminal-history-tab-sales"
            className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition ${
              view === 'sales' ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]'
                               : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            <FileText size={14} /> Sales History
          </button>
          <button
            onClick={() => setView('purchase')}
            data-testid="terminal-history-tab-purchase"
            className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition ${
              view === 'purchase' ? 'bg-white shadow-sm ring-1 ring-slate-200/60 text-[#1A4D2E]'
                                  : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            <Truck size={14} /> Purchase History
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        <POSHistoryUnlockGate
          api={api}
          label={view === 'sales' ? 'Sales History' : 'Purchase History'}
          branch={branch}
          pinSession={pinSession}
          startPinSession={startPinSession}
        >
          {view === 'sales' ? (
            <POSSalesHistoryView
              api={api}
              currentBranch={branch}
              isOnline={isOnline}
              onSelectInvoice={(inv) => setOpenInvoiceId(inv.id)}
            />
          ) : (
            <POSPurchaseHistoryView
              api={api}
              currentBranch={branch}
              isOnline={isOnline}
              onSelectPO={(po) => setOpenPOId(po.id)}
            />
          )}
        </POSHistoryUnlockGate>
      </div>

      {/* Detail dialogs */}
      <POSSalesDetailDialog
        api={api}
        invoiceId={openInvoiceId}
        open={!!openInvoiceId}
        onClose={() => setOpenInvoiceId(null)}
        businessInfo={businessInfo}
      />
      <POSPurchaseDetailDialog
        api={api}
        poId={openPOId}
        open={!!openPOId}
        onClose={() => setOpenPOId(null)}
      />
    </div>
  );
}
