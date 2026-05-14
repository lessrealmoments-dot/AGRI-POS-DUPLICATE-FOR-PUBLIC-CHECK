/**
 * TerminalHistoryOverlay — full-screen sheet launched from the POS Terminal
 * Mode Selector. Hosts Sales History + Purchase History views behind a
 * single PIN unlock gate.
 *
 * Row clicks navigate to `/doc/{doc_code}` so we reuse the existing
 * DocViewerPage modal that the search function already opens — same
 * 58mm / Full-page reprint UX, same "Back to Terminal" affordance. No
 * duplicate viewers, no SDK drift.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X, FileText, Truck, ShieldCheck } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from 'sonner';
import POSHistoryUnlockGate from './POSHistoryUnlockGate';
import POSSalesHistoryView from './POSSalesHistoryView';
import POSPurchaseHistoryView from './POSPurchaseHistoryView';

export default function TerminalHistoryOverlay({
  api,
  open,
  onClose,
  branch,                // { id, name }
  isOnline,
  pinSession,
  startPinSession,
}) {
  const [view, setView] = useState('sales'); // 'sales' | 'purchase'
  const navigate = useNavigate();

  if (!open) return null;

  // Same handler shape for both lists — navigate to the existing
  // DocViewerPage which already implements 58mm/Full-page reprint,
  // payment history, "Back to Terminal" header, etc.
  const openDoc = (row) => {
    const code = row?.doc_code;
    if (!code) {
      toast.error('No QR code on this record yet — open it from its main page to generate one.');
      return;
    }
    // Close the overlay so when the user taps "Back to Terminal" inside
    // the doc viewer they land back on the live terminal cart, fresh.
    onClose && onClose();
    navigate(`/doc/${code}`);
  };

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
              onSelectInvoice={openDoc}
            />
          ) : (
            <POSPurchaseHistoryView
              api={api}
              currentBranch={branch}
              isOnline={isOnline}
              onSelectPO={openDoc}
            />
          )}
        </POSHistoryUnlockGate>
      </div>
    </div>
  );
}
