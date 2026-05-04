import { useState, useEffect, useCallback } from 'react';
import { ArrowLeftRight, Search, RefreshCw, Check, AlertTriangle, Loader2, Package, ArrowRight, Lock, Send, Info, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { toast } from 'sonner';
import { formatPHP, fmtDate } from '../../lib/utils';
import CalcInput from '../../components/CalcInput';

const STATUS_COLORS = {
  sent: 'bg-blue-100 text-blue-700',
  sent_to_terminal: 'bg-amber-100 text-amber-700',
  received_pending: 'bg-yellow-100 text-yellow-700',
  received: 'bg-emerald-100 text-emerald-700',
  disputed: 'bg-red-100 text-red-700',
};

const STATUS_LABELS = {
  sent: 'Sent',
  sent_to_terminal: 'For Checking',
  received_pending: 'Pending Review',
  received: 'Received',
  disputed: 'Disputed',
};

export default function TerminalTransfers({ api, session, isOnline, onRefreshRef }) {
  const [transfers, setTransfers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedTransfer, setSelectedTransfer] = useState(null);
  const [items, setItems] = useState([]);
  const [saving, setSaving] = useState(false);
  const [notes, setNotes] = useState('');
  const [result, setResult] = useState(null);
  const [showPull, setShowPull] = useState(false);
  const [availableTransfers, setAvailableTransfers] = useState([]);
  const [pullLoading, setPullLoading] = useState(false);
  const [pullTarget, setPullTarget] = useState(null);
  const [pin, setPin] = useState('');
  const [pulling, setPulling] = useState(false);

  const loadTransfers = useCallback(async () => {
    if (!isOnline) { toast('Transfers require internet', { duration: 2000 }); return; }
    setLoading(true);
    try {
      const res = await api.get('/branch-transfers', {
        params: { to_branch_id: session.branchId, status: 'sent_to_terminal' }
      });
      const list = res.data.orders || res.data || [];
      setTransfers(Array.isArray(list) ? list : []);
    } catch {
      toast.error('Failed to load transfers');
    }
    setLoading(false);
  }, [api, session.branchId, isOnline]);

  useEffect(() => { if (isOnline) loadTransfers(); }, [isOnline, loadTransfers]);

  useEffect(() => {
    if (onRefreshRef) onRefreshRef.current = loadTransfers;
  }, [onRefreshRef, loadTransfers]);

  // Pull from PC
  const loadAvailableTransfers = async () => {
    setPullLoading(true);
    try {
      const res = await api.get('/terminal/available-transfers', { params: { branch_id: session.branchId } });
      setAvailableTransfers(Array.isArray(res.data) ? res.data : []);
    } catch { toast.error('Failed to load available transfers'); }
    setPullLoading(false);
  };

  const handlePull = async () => {
    if (!pullTarget || !pin) return;
    setPulling(true);
    try {
      await api.post('/terminal/pull-transfer', { transfer_id: pullTarget.id, pin });
      toast.success(`${pullTarget.order_number} pulled to terminal`);
      setPullTarget(null); setPin(''); setShowPull(false);
      loadTransfers();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Pull failed');
    }
    setPulling(false);
  };

  const openTransfer = (transfer) => {
    setSelectedTransfer(transfer);
    setNotes('');
    setResult(null);
    setItems((transfer.items || []).map(item => ({
      ...item,
      qty_received: item.qty,
      has_variance: false,
    })));
  };

  const updateReceivedQty = (idx, val) => {
    setItems(prev => prev.map((item, i) => {
      if (i !== idx) return item;
      const qty = parseFloat(val) || 0;
      return { ...item, qty_received: qty, has_variance: qty !== item.qty };
    }));
  };

  const hasVariance = items.some(i => i.has_variance);

  // Compute variance summary
  const varianceSummary = items.reduce((acc, item) => {
    const diff = item.qty_received - item.qty;
    if (diff < 0) {
      acc.shortCount++;
      acc.shortCapital += Math.abs(diff) * (item.transfer_capital || 0);
      acc.shortRetail += Math.abs(diff) * (item.branch_retail || 0);
    } else if (diff > 0) {
      acc.excessCount++;
      acc.excessCapital += diff * (item.transfer_capital || 0);
      acc.excessRetail += diff * (item.branch_retail || 0);
    }
    return acc;
  }, { shortCount: 0, shortCapital: 0, shortRetail: 0, excessCount: 0, excessCapital: 0, excessRetail: 0 });

  const receiveTransfer = async () => {
    if (!selectedTransfer) return;
    setSaving(true);
    try {
      const payload = {
        items: items.map(i => ({
          product_id: i.product_id,
          qty: i.qty,
          qty_received: i.qty_received,
          transfer_capital: i.transfer_capital,
          branch_retail: i.branch_retail,
        })),
        terminal_id: session.terminalId,
        notes,
      };

      const res = await api.post(`/branch-transfers/${selectedTransfer.id}/terminal-receive`, payload);
      const data = res.data;

      if (data.status === 'received_pending') {
        setResult({
          type: 'variance',
          message: 'Variance submitted — source branch will review',
          detail: `${data.shortages?.length || 0} shortage(s), ${data.excesses?.length || 0} excess(es)`,
        });
        toast.success('Receipt submitted with variance — source branch notified', { duration: 5000 });
      } else {
        setResult({
          type: 'success',
          message: 'Transfer received — inventory updated!',
          detail: data.message || '',
        });
        toast.success('Transfer received!', { duration: 3000 });
      }

      loadTransfers();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to process transfer');
    }
    setSaving(false);
  };

  const filteredTransfers = search
    ? transfers.filter(t =>
        (t.order_number || '').toLowerCase().includes(search.toLowerCase()) ||
        (t.from_branch_name || '').toLowerCase().includes(search.toLowerCase()))
    : transfers;

  return (
    <div className="flex flex-col h-full" data-testid="terminal-transfers">
      {/* Header */}
      <div className="p-3 bg-white border-b border-slate-200">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-base font-bold text-slate-800">Incoming Transfers</h2>
            <p className="text-[10px] text-slate-400">Verify & receive items from other branches</p>
          </div>
          <Button variant="outline" size="sm" onClick={loadTransfers} disabled={loading || !isOnline} data-testid="transfer-refresh-btn">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
          <Button variant="outline" size="sm" onClick={() => { setShowPull(true); loadAvailableTransfers(); }}
            disabled={!isOnline} className="ml-1.5 text-blue-600 border-blue-200" data-testid="pull-transfer-btn">
            <Package size={14} className="mr-1" /> Pull
          </Button>
        </div>
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search transfer # or branch..."
          className="h-9"
          data-testid="transfer-search-input"
        />
      </div>

      {/* Transfer List */}
      <div className="flex-1 overflow-auto p-3">
        {!isOnline ? (
          <div className="text-center py-12 text-slate-400">
            <AlertTriangle size={28} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">Transfers require internet connection</p>
          </div>
        ) : loading ? (
          <div className="text-center py-12">
            <Loader2 size={24} className="animate-spin mx-auto text-slate-400" />
          </div>
        ) : filteredTransfers.length === 0 ? (
          <div className="text-center py-12 text-slate-400">
            <ArrowLeftRight size={28} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">No transfers to check</p>
            <p className="text-xs text-slate-400 mt-1">Transfers sent to terminal will appear here</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredTransfers.map(t => (
              <button
                key={t.id}
                onClick={() => openTransfer(t)}
                className="w-full bg-white rounded-xl border border-amber-200 p-3 text-left hover:border-emerald-300 transition-colors"
                data-testid={`transfer-item-${t.id}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <Lock size={12} className="text-amber-500" />
                    <span className="text-sm font-bold text-slate-800">{t.order_number || 'Transfer'}</span>
                  </div>
                  <Badge className={STATUS_COLORS[t.status] || 'bg-slate-100'}>
                    {STATUS_LABELS[t.status] || t.status}
                  </Badge>
                </div>
                <div className="flex items-center gap-1 text-xs text-slate-500">
                  <span>{t.from_branch_name || 'Source'}</span>
                  <ArrowRight size={12} />
                  <span>{t.to_branch_name || session.branchName}</span>
                </div>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs text-slate-400">{(t.items || []).length} items</span>
                  <span className="text-xs text-slate-400">{fmtDate(t.created_at) || ''}</span>
                </div>
                {t.sent_to_terminal_at && (
                  <p className="text-[10px] text-amber-600 mt-1">
                    Sent by {t.sent_to_terminal_by || 'admin'} · {fmtDate(t.sent_to_terminal_at)}
                  </p>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Transfer Detail Dialog */}
      <Dialog open={!!selectedTransfer} onOpenChange={(open) => { if (!open) { setSelectedTransfer(null); setResult(null); } }}>
        <DialogContent className="max-w-lg mx-auto max-h-[90vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Package size={18} />
              {selectedTransfer?.order_number || 'Receive Transfer'}
            </DialogTitle>
          </DialogHeader>

          {/* Result after receive */}
          {result ? (
            <div className="space-y-4">
              <div className={`p-4 rounded-xl border ${result.type === 'success' ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
                <div className="flex items-center gap-2 mb-2">
                  {result.type === 'success' ? (
                    <Check size={20} className="text-emerald-600" />
                  ) : (
                    <AlertTriangle size={20} className="text-amber-600" />
                  )}
                  <p className={`text-sm font-bold ${result.type === 'success' ? 'text-emerald-800' : 'text-amber-800'}`}>
                    {result.message}
                  </p>
                </div>
                <p className="text-xs text-slate-600">{result.detail}</p>
              </div>
              <Button
                onClick={() => { setSelectedTransfer(null); setResult(null); }}
                className="w-full bg-slate-800 text-white"
                data-testid="close-result-btn"
              >
                Close
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>From: <strong className="text-slate-700">{selectedTransfer?.from_branch_name || '—'}</strong></span>
              </div>

              {/* Explanation banner */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-2 text-xs text-blue-700">
                <Info size={12} className="inline mr-1" />
                Enter the actual quantity received for each item. If quantities don't match, the source branch will be notified.
              </div>

              {/* Items checklist */}
              <div className="space-y-2" data-testid="transfer-items-list">
                {items.map((item, idx) => {
                  const diff = item.qty_received - item.qty;
                  const isShort = diff < 0;
                  const isExcess = diff > 0;

                  return (
                    <div key={idx} className={`p-3 rounded-xl border ${
                      isShort ? 'border-amber-300 bg-amber-50' :
                      isExcess ? 'border-blue-300 bg-blue-50' :
                      'border-slate-200 bg-white'
                    }`}>
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-slate-800 truncate">{item.product_name}</p>
                          <p className="text-[10px] text-slate-400">{item.sku || ''} {item.category ? `· ${item.category}` : ''}</p>
                        </div>
                      </div>

                      {/* Pricing info */}
                      <div className="flex gap-3 text-[10px] text-slate-500 mb-2">
                        <span>Capital: <strong className="text-slate-700">{formatPHP(item.transfer_capital || 0)}</strong></span>
                        <span>Retail: <strong className="text-emerald-700">{formatPHP(item.branch_retail || 0)}</strong></span>
                      </div>

                      <div className="flex items-center gap-3">
                        <div className="text-xs text-slate-500">
                          Sent: <strong>{item.qty}</strong> {item.unit || ''}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-1.5">
                            <label className="text-xs text-slate-500 whitespace-nowrap">Received:</label>
                            <CalcInput value={item.qty_received}
 onChange={(v) => updateReceivedQty(idx, v)}
 className={`h-8 text-sm w-20 font-bold ${
 isShort ? 'border-amber-400 text-amber-800' :
 isExcess ? 'border-blue-400 text-blue-800' : ''
 }`}
 data-testid={`transfer-qty-${idx}`} />
                          </div>
                        </div>
                        {isShort && (
                          <span className="text-[10px] font-bold text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded-full whitespace-nowrap">
                            Short {Math.abs(diff)}
                          </span>
                        )}
                        {isExcess && (
                          <span className="text-[10px] font-bold text-blue-700 bg-blue-100 px-1.5 py-0.5 rounded-full whitespace-nowrap">
                            +{diff} extra
                          </span>
                        )}
                        {!isShort && !isExcess && (
                          <Check size={16} className="text-emerald-500 flex-shrink-0" />
                        )}
                      </div>

                      {/* Capital impact for variance */}
                      {(isShort || isExcess) && (
                        <div className={`mt-1.5 text-[10px] ${isShort ? 'text-amber-600' : 'text-blue-600'}`}>
                          Impact: {isShort ? '-' : '+'}{formatPHP(Math.abs(diff) * (item.transfer_capital || 0))} capital
                          {' / '}{isShort ? '-' : '+'}{formatPHP(Math.abs(diff) * (item.branch_retail || 0))} retail
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Variance summary */}
              {hasVariance && (
                <div className="space-y-2">
                  {varianceSummary.shortCount > 0 && (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-2.5">
                      <p className="text-xs font-bold text-amber-800">
                        Shortage: {varianceSummary.shortCount} product(s)
                      </p>
                      <p className="text-[10px] text-amber-600 mt-0.5">
                        -{formatPHP(varianceSummary.shortCapital)} capital / -{formatPHP(varianceSummary.shortRetail)} retail
                      </p>
                      <p className="text-[10px] text-amber-500 mt-0.5">
                        Source branch will be notified to accept or dispute
                      </p>
                    </div>
                  )}
                  {varianceSummary.excessCount > 0 && (
                    <div className="bg-blue-50 border border-blue-200 rounded-lg p-2.5">
                      <p className="text-xs font-bold text-blue-800">
                        Excess: {varianceSummary.excessCount} product(s)
                      </p>
                      <p className="text-[10px] text-blue-600 mt-0.5">
                        +{formatPHP(varianceSummary.excessCapital)} capital / +{formatPHP(varianceSummary.excessRetail)} retail
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* Notes */}
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Receiving Notes (optional)</label>
                <Input
                  value={notes}
                  onChange={e => setNotes(e.target.value)}
                  placeholder="e.g., 2 boxes damaged, 1 bag opened..."
                  className="h-9 text-sm"
                  data-testid="transfer-notes-input"
                />
              </div>

              <Button
                onClick={receiveTransfer}
                disabled={saving}
                className={`w-full h-11 text-white ${hasVariance ? 'bg-amber-600 hover:bg-amber-700' : 'bg-[#1A4D2E] hover:bg-[#15412a]'}`}
                data-testid="receive-transfer-btn"
              >
                {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : <Send size={16} className="mr-2" />}
                {saving ? 'Processing...' : hasVariance ? 'Submit with Variance' : 'Confirm Receipt'}
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Pull from PC Dialog */}
      <Dialog open={showPull} onOpenChange={v => { setShowPull(v); if (!v) { setPullTarget(null); setPin(''); } }}>
        <DialogContent className="max-w-md max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-base font-bold" style={{ fontFamily: 'Manrope' }}>
              {pullTarget ? 'Enter PIN to Pull' : 'Pull Transfer from PC'}
            </DialogTitle>
          </DialogHeader>

          {!pullTarget ? (
            <div className="flex-1 overflow-auto space-y-2">
              {pullLoading ? (
                <div className="text-center py-8"><Loader2 size={20} className="animate-spin mx-auto text-slate-400" /></div>
              ) : availableTransfers.length === 0 ? (
                <div className="text-center py-8 text-slate-400">
                  <Package size={24} className="mx-auto mb-2 opacity-40" />
                  <p className="text-sm">No transfers available to pull</p>
                  <p className="text-xs mt-1">Transfers need to be "Sent" (In Transit) on the PC</p>
                </div>
              ) : availableTransfers.map(t => (
                <button key={t.id} onClick={() => setPullTarget(t)}
                  className="w-full bg-white rounded-xl border border-slate-200 p-3 text-left hover:border-blue-300 transition-colors"
                  data-testid={`available-transfer-${t.id}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-slate-800">{t.order_number}</span>
                    <Badge className="text-[10px] bg-blue-100 text-blue-700">Sent</Badge>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">From: {t.from_branch_name}</p>
                  <p className="text-xs text-slate-400 mt-1">{t.item_count} items · {fmtDate(t.created_at)}</p>
                </button>
              ))}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
                <p className="text-sm font-semibold text-blue-800">{pullTarget.order_number}</p>
                <p className="text-xs text-blue-600">From: {pullTarget.from_branch_name} · {pullTarget.item_count} items</p>
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1.5 block font-medium">Manager / Admin PIN</label>
                <Input
                  type="password" autoComplete="off" inputMode="numeric"
                  value={pin} onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
                  placeholder="Enter PIN" className="h-11 text-center text-lg font-mono tracking-widest"
                  data-testid="pull-pin-input"
                />
                <p className="text-[10px] text-slate-400 mt-1">Uses the same PIN rules configured in Settings</p>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => { setPullTarget(null); setPin(''); }} className="flex-1">Back</Button>
                <Button onClick={handlePull} disabled={pulling || !pin} className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  data-testid="confirm-pull-transfer-btn">
                  {pulling ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <Package size={14} className="mr-1.5" />}
                  Pull to Terminal
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
