/**
 * SendToPrintModal — Universal "Send to Print" dialog.
 *
 * Behaviour (Option C):
 *   - Auto-selects the terminal assigned to the document's branch if only one exists.
 *   - Shows a picker when multiple terminals are available for that branch.
 *   - Always allows sending to OFFLINE terminals (job stays pending until reconnect).
 *
 * Props:
 *   open           boolean
 *   onOpenChange   fn(bool)
 *   documentType   string  e.g. "sales_receipt"
 *   documentName   string  e.g. "Sales Receipt #INV-001"
 *   documentId     string
 *   referenceNumber string
 *   branchId       string  — used to fetch matching terminals
 *   htmlContent    string  — full print-ready HTML to send
 *   metadata       object  — any extra data to attach
 */
import { useState, useEffect, useCallback } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Printer, Wifi, WifiOff, AlertTriangle, Clock, CheckCircle2, RefreshCw, Building2, Loader2 } from 'lucide-react';
import { api } from '../contexts/AuthContext';
import { toast } from 'sonner';

const DOC_TYPE_LABELS = {
  sales_receipt:    'Sales Receipt',
  purchase_order:   'Purchase Order',
  z_report:         'Z-Report',
  advance_z_report: 'Advance Z-Report',
  branch_transfer:  'Branch Transfer',
  expense_receipt:  'Expense Receipt',
  return_receipt:   'Return/Refund Receipt',
  statement:        'Customer Statement',
};

export default function SendToPrintModal({
  open, onOpenChange,
  documentType = 'sales_receipt',
  documentName = '',
  documentId   = '',
  referenceNumber = '',
  branchId     = '',
  htmlContent  = '',
  metadata     = {},
}) {
  const [terminals, setTerminals]   = useState([]);
  const [selected, setSelected]     = useState(null);
  const [loading, setLoading]       = useState(false);
  const [sending, setSending]       = useState(false);
  const [sent, setSent]             = useState(false);
  const [result, setResult]         = useState(null);

  const fetchTerminals = useCallback(async () => {
    if (!branchId) return;
    setLoading(true);
    try {
      const res = await api.get(`/print/terminals/for-branch/${branchId}`);
      const list = res.data || [];
      setTerminals(list);
      // Auto-select: if only one terminal, pick it
      if (list.length === 1) setSelected(list[0].terminal_id);
      else if (list.length > 0) {
        // Auto-select first online terminal if any
        const online = list.find(t => t.is_online);
        if (online) setSelected(online.terminal_id);
        else setSelected(list[0].terminal_id);
      }
    } catch {
      toast.error('Could not fetch print terminals');
    }
    setLoading(false);
  }, [branchId]);

  useEffect(() => {
    if (open) {
      setSent(false);
      setResult(null);
      fetchTerminals();
    }
  }, [open, fetchTerminals]);

  const handleSend = async () => {
    if (!selected) { toast.error('Please select a terminal'); return; }
    if (!htmlContent) { toast.error('No print content available'); return; }

    setSending(true);
    try {
      const res = await api.post('/print/jobs', {
        terminal_id:      selected,
        branch_id:        branchId,
        document_type:    documentType,
        document_name:    documentName,
        document_id:      documentId,
        reference_number: referenceNumber,
        html_content:     htmlContent,
        metadata,
        priority:         'normal',
      });
      setResult(res.data);
      setSent(true);
      const msg = res.data.status === 'sent'
        ? 'Print job sent to terminal'
        : 'Job queued — terminal will print when it reconnects';
      toast.success(msg);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to send print job');
    }
    setSending(false);
  };

  const selectedTerminal = terminals.find(t => t.terminal_id === selected);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" data-testid="send-to-print-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Printer size={16} className="text-emerald-600" />
            Send to Print
          </DialogTitle>
        </DialogHeader>

        {/* Document info */}
        <div className="bg-slate-50 rounded-lg p-3 text-sm mb-1">
          <p className="font-semibold text-slate-800 truncate">{documentName || DOC_TYPE_LABELS[documentType] || documentType}</p>
          {referenceNumber && (
            <p className="text-xs text-slate-500 mt-0.5">Ref: {referenceNumber}</p>
          )}
          <Badge variant="secondary" className="mt-1.5 text-[10px]">
            {DOC_TYPE_LABELS[documentType] || documentType}
          </Badge>
        </div>

        {sent ? (
          <div className="text-center py-6 space-y-3" data-testid="print-sent-state">
            {result?.status === 'sent' ? (
              <>
                <CheckCircle2 size={36} className="text-emerald-500 mx-auto" />
                <p className="font-semibold text-slate-800">Print job sent!</p>
                <p className="text-xs text-slate-500">
                  The terminal will print your document shortly.
                </p>
              </>
            ) : (
              <>
                <Clock size={36} className="text-amber-500 mx-auto" />
                <p className="font-semibold text-slate-800">Job queued</p>
                <p className="text-xs text-slate-500">
                  The terminal is offline. Your document will print automatically when it reconnects.
                </p>
              </>
            )}
            <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </div>
        ) : (
          <>
            {/* Terminal picker */}
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-500 uppercase tracking-wider">
                Select Printer Terminal
              </p>

              {loading ? (
                <div className="flex items-center justify-center py-6 text-slate-400">
                  <Loader2 size={18} className="animate-spin mr-2" /> Loading terminals...
                </div>
              ) : terminals.length === 0 ? (
                <div className="flex items-center gap-2 p-3 bg-amber-50 rounded-lg text-sm text-amber-700 border border-amber-200">
                  <AlertTriangle size={15} />
                  No print terminals registered for this branch.
                  <br />Go to Print Center to set one up.
                </div>
              ) : (
                <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                  {terminals.map(t => (
                    <button
                      key={t.terminal_id}
                      data-testid={`terminal-option-${t.terminal_id}`}
                      onClick={() => setSelected(t.terminal_id)}
                      className={`w-full flex items-center gap-3 p-3 rounded-xl border text-left transition-all ${
                        selected === t.terminal_id
                          ? 'border-emerald-500 bg-emerald-50 ring-1 ring-emerald-300'
                          : 'border-slate-200 hover:border-slate-300 bg-white'
                      }`}
                    >
                      <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
                        t.is_online ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-100 text-slate-400'
                      }`}>
                        <Printer size={16} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-slate-800 truncate">
                          {t.user_name || t.code || 'Terminal'}
                        </p>
                        <p className="text-xs text-slate-500 flex items-center gap-1 mt-0.5">
                          <Building2 size={10} />
                          {t.branch_name || '—'}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <span className={`flex items-center gap-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full ${
                          t.is_online
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-slate-100 text-slate-500'
                        }`}>
                          {t.is_online ? <Wifi size={9} /> : <WifiOff size={9} />}
                          {t.is_online ? 'Online' : 'Offline'}
                        </span>
                        {t.pending_jobs > 0 && (
                          <span className="text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
                            {t.pending_jobs} pending
                          </span>
                        )}
                        {t.print_mode && (
                          <span className="text-[9px] text-slate-400 capitalize">
                            {t.print_mode} mode
                          </span>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Offline note */}
            {selectedTerminal && !selectedTerminal.is_online && (
              <div className="flex items-start gap-2 p-2.5 bg-amber-50 rounded-lg text-xs text-amber-700 border border-amber-200">
                <Clock size={13} className="mt-0.5 shrink-0" />
                Terminal is offline. Your job will be queued and delivered automatically when it comes back online.
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => onOpenChange(false)}
                data-testid="send-to-print-cancel"
              >
                Cancel
              </Button>
              <Button
                className="flex-1 bg-emerald-700 hover:bg-emerald-800 text-white"
                onClick={handleSend}
                disabled={sending || !selected || terminals.length === 0}
                data-testid="send-to-print-confirm"
              >
                {sending ? (
                  <><Loader2 size={14} className="animate-spin mr-1.5" /> Sending...</>
                ) : (
                  <><Printer size={14} className="mr-1.5" /> Send to Print</>
                )}
              </Button>
            </div>

            <button
              className="w-full text-xs text-slate-400 hover:text-slate-600 mt-1"
              onClick={fetchTerminals}
              data-testid="refresh-terminals-btn"
            >
              <RefreshCw size={10} className="inline mr-1" /> Refresh terminals
            </button>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
