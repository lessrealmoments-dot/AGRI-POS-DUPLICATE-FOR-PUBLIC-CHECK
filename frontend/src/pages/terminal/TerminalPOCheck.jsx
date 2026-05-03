import { useState, useEffect, useCallback } from 'react';
import { ClipboardCheck, Search, RefreshCw, Check, AlertTriangle, Loader2, Package, Lock, Send } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { toast } from 'sonner';
import { formatPHP, fmtDate } from '../../lib/utils';

const STATUS_COLORS = {
  draft: 'bg-slate-100 text-slate-600',
  ordered: 'bg-blue-100 text-blue-700',
  sent_to_terminal: 'bg-amber-100 text-amber-700',
  received: 'bg-emerald-100 text-emerald-700',
  cancelled: 'bg-red-100 text-red-600',
};

const STATUS_LABELS = {
  sent_to_terminal: 'For Checking',
  ordered: 'Ordered',
  received: 'Received',
};

export default function TerminalPOCheck({ api, session, isOnline, onRefreshRef }) {
  const [pos, setPOs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedPO, setSelectedPO] = useState(null);
  const [items, setItems] = useState([]);
  const [saving, setSaving] = useState(false);
  const [notes, setNotes] = useState('');
  const [showPull, setShowPull] = useState(false);
  const [availablePOs, setAvailablePOs] = useState([]);
  const [pullLoading, setPullLoading] = useState(false);
  const [pullTarget, setPullTarget] = useState(null);
  const [pin, setPin] = useState('');
  const [pulling, setPulling] = useState(false);

  const loadPOs = useCallback(async () => {
    if (!isOnline) { toast('PO Check requires internet', { duration: 2000 }); return; }
    setLoading(true);
    try {
      // Fetch POs sent to terminal for this branch
      const res = await api.get('/purchase-orders', {
        params: { branch_id: session.branchId, status: 'sent_to_terminal' }
      });
      const list = res.data.purchase_orders || res.data || [];
      setPOs(Array.isArray(list) ? list : []);
    } catch {
      toast.error('Failed to load POs');
    }
    setLoading(false);
  }, [api, session.branchId, isOnline]);

  useEffect(() => { if (isOnline) loadPOs(); }, [isOnline, loadPOs]);

  // Expose refresh callback to parent (TerminalShell) for WebSocket notifications
  useEffect(() => {
    if (onRefreshRef) onRefreshRef.current = loadPOs;
  }, [onRefreshRef, loadPOs]);

  // Pull from PC
  const loadAvailablePOs = async () => {
    setPullLoading(true);
    try {
      const res = await api.get('/terminal/available-pos', { params: { branch_id: session.branchId } });
      setAvailablePOs(Array.isArray(res.data) ? res.data : []);
    } catch { toast.error('Failed to load available POs'); }
    setPullLoading(false);
  };

  const handlePull = async () => {
    if (!pullTarget || !pin) return;
    setPulling(true);
    try {
      await api.post('/terminal/pull-po', { po_id: pullTarget.id, pin });
      toast.success(`${pullTarget.po_number} pulled to terminal`);
      setPullTarget(null); setPin(''); setShowPull(false);
      loadPOs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Pull failed');
    }
    setPulling(false);
  };

  const openPO = (po) => {
    setSelectedPO(po);
    setNotes('');
    setItems((po.items || []).map(item => ({
      ...item,
      qty_received: item.quantity,
      has_variance: false,
    })));
  };

  const updateReceivedQty = (idx, val) => {
    setItems(prev => prev.map((item, i) => {
      if (i !== idx) return item;
      const qty = parseFloat(val) || 0;
      return { ...item, qty_received: qty, has_variance: qty !== item.quantity };
    }));
  };

  const hasVariance = items.some(i => i.has_variance);
  const totalReceived = items.reduce((s, i) => s + (i.qty_received * (i.unit_price || 0)), 0);

  const finalizePO = async () => {
    if (!selectedPO) return;
    setSaving(true);
    try {
      const payload = {
        items: items.map(i => ({
          product_id: i.product_id,
          product_name: i.product_name,
          qty_received: i.qty_received,
          unit_price: i.unit_price,
          unit: i.unit,
        })),
        terminal_id: session.terminalId,
        notes,
      };

      const res = await api.post(`/purchase-orders/${selectedPO.id}/terminal-finalize`, payload);
      const variances = res.data.variances || 0;

      if (variances > 0) {
        toast.success(`PO verified with ${variances} variance(s) — sent back to PC`, { duration: 4000 });
      } else {
        toast.success(`PO verified — all quantities match!`, { duration: 3000 });
      }

      setSelectedPO(null);
      loadPOs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to finalize PO');
    }
    setSaving(false);
  };

  const filteredPOs = search
    ? pos.filter(p => (p.po_number || '').toLowerCase().includes(search.toLowerCase()) ||
        (p.vendor || '').toLowerCase().includes(search.toLowerCase()))
    : pos;

  return (
    <div className="flex flex-col h-full" data-testid="terminal-po-check">
      {/* Header */}
      <div className="p-3 bg-white border-b border-slate-200">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-base font-bold text-slate-800">PO Check</h2>
            <p className="text-[10px] text-slate-400">Verify quantities sent from PC</p>
          </div>
          <Button variant="outline" size="sm" onClick={loadPOs} disabled={loading || !isOnline} data-testid="po-refresh-btn">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </Button>
          <Button variant="outline" size="sm" onClick={() => { setShowPull(true); loadAvailablePOs(); }}
            disabled={!isOnline} className="ml-1.5 text-blue-600 border-blue-200" data-testid="pull-po-btn">
            <Package size={14} className="mr-1" /> Pull
          </Button>
        </div>
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search PO # or vendor..."
          className="h-9"
          data-testid="po-search-input"
        />
      </div>

      {/* PO List */}
      <div className="flex-1 overflow-auto p-3">
        {!isOnline ? (
          <div className="text-center py-12 text-slate-400">
            <AlertTriangle size={28} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">PO Check requires internet connection</p>
          </div>
        ) : loading ? (
          <div className="text-center py-12">
            <Loader2 size={24} className="animate-spin mx-auto text-slate-400" />
          </div>
        ) : filteredPOs.length === 0 ? (
          <div className="text-center py-12 text-slate-400">
            <ClipboardCheck size={28} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">No POs sent for checking</p>
            <p className="text-xs text-slate-400 mt-1">Send a PO from your computer to check it here</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredPOs.map(po => (
              <button
                key={po.id}
                onClick={() => openPO(po)}
                className="w-full bg-white rounded-xl border border-amber-200 p-3 text-left hover:border-emerald-300 transition-colors"
                data-testid={`po-item-${po.id}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <Lock size={12} className="text-amber-500" />
                    <span className="text-sm font-bold text-slate-800">{po.po_number || 'No #'}</span>
                  </div>
                  <Badge className={STATUS_COLORS[po.status] || 'bg-slate-100'}>
                    {STATUS_LABELS[po.status] || po.status}
                  </Badge>
                </div>
                <p className="text-xs text-slate-500">{po.vendor || 'Unknown vendor'}</p>
                <div className="flex items-center justify-between mt-2">
                  <span className="text-xs text-slate-400">{(po.items || []).length} items</span>
                  <span className="text-sm font-bold text-[#1A4D2E]">{formatPHP(po.grand_total || 0)}</span>
                </div>
                {po.sent_to_terminal_at && (
                  <p className="text-[10px] text-amber-600 mt-1">
                    Sent by {po.sent_to_terminal_by || 'admin'} · {fmtDate(po.sent_to_terminal_at)}
                  </p>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* PO Detail Dialog */}
      <Dialog open={!!selectedPO} onOpenChange={(open) => { if (!open) setSelectedPO(null); }}>
        <DialogContent className="max-w-lg mx-auto max-h-[90vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Package size={18} />
              {selectedPO?.po_number || 'PO Check'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>Vendor: {selectedPO?.vendor || '—'}</span>
              <span>Expected: {formatPHP(selectedPO?.grand_total || 0)}</span>
            </div>

            {/* Items checklist */}
            <div className="space-y-2" data-testid="po-items-list">
              {items.map((item, idx) => (
                <div key={idx} className={`p-3 rounded-xl border ${item.has_variance ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-white'}`}>
                  <div className="flex items-center justify-between mb-1.5">
                    <p className="text-sm font-medium text-slate-800 truncate max-w-[60%]">{item.product_name}</p>
                    <p className="text-xs text-slate-500">{formatPHP(item.unit_price || 0)}/ea</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-xs text-slate-500">
                      Ordered: <strong>{item.quantity}</strong> {item.unit || ''}
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-1.5">
                        <label className="text-xs text-slate-500 whitespace-nowrap">Received:</label>
                        <Input
                          type="number"
                          value={item.qty_received}
                          onChange={e => updateReceivedQty(idx, e.target.value)}
                          className="h-8 text-sm w-20"
                          data-testid={`po-qty-${idx}`}
                        />
                      </div>
                    </div>
                    {item.has_variance ? (
                      <AlertTriangle size={16} className="text-amber-500 flex-shrink-0" />
                    ) : (
                      <Check size={16} className="text-emerald-500 flex-shrink-0" />
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Totals */}
            <div className="bg-slate-50 rounded-lg p-2.5 flex items-center justify-between text-sm">
              <span className="text-slate-500">Total (received)</span>
              <span className={`font-bold ${hasVariance ? 'text-amber-600' : 'text-[#1A4D2E]'}`}>
                {formatPHP(totalReceived)}
              </span>
            </div>

            {hasVariance && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-2 text-xs text-amber-700">
                Some quantities don't match the order. The PO will be updated with your received quantities and sent back to the PC.
              </div>
            )}

            {/* Notes */}
            <div>
              <label className="text-xs text-slate-500 mb-1 block">Notes (optional)</label>
              <Input
                value={notes}
                onChange={e => setNotes(e.target.value)}
                placeholder="e.g., 2 bags damaged, returned to driver"
                className="h-9 text-sm"
                data-testid="po-notes-input"
              />
            </div>

            <Button
              onClick={finalizePO}
              disabled={saving}
              className="w-full bg-[#1A4D2E] hover:bg-[#15412a] text-white h-11"
              data-testid="finalize-po-btn"
            >
              {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : <Send size={16} className="mr-2" />}
              {saving ? 'Sending...' : 'Finalize & Send to PC'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Pull from PC Dialog */}
      <Dialog open={showPull} onOpenChange={v => { setShowPull(v); if (!v) { setPullTarget(null); setPin(''); } }}>
        <DialogContent className="max-w-md max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-base font-bold" style={{ fontFamily: 'Manrope' }}>
              {pullTarget ? 'Enter PIN to Pull' : 'Pull PO from PC'}
            </DialogTitle>
          </DialogHeader>

          {!pullTarget ? (
            <div className="flex-1 overflow-auto space-y-2">
              {pullLoading ? (
                <div className="text-center py-8"><Loader2 size={20} className="animate-spin mx-auto text-slate-400" /></div>
              ) : availablePOs.length === 0 ? (
                <div className="text-center py-8 text-slate-400">
                  <Package size={24} className="mx-auto mb-2 opacity-40" />
                  <p className="text-sm">No POs available to pull</p>
                  <p className="text-xs mt-1">POs need to be in Draft/Ordered status on the PC</p>
                </div>
              ) : availablePOs.map(po => (
                <button key={po.id} onClick={() => setPullTarget(po)}
                  className="w-full bg-white rounded-xl border border-slate-200 p-3 text-left hover:border-blue-300 transition-colors"
                  data-testid={`available-po-${po.id}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-slate-800">{po.po_number}</span>
                    <Badge className="text-[10px] bg-blue-100 text-blue-700">{po.status}</Badge>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">{po.vendor || 'Unknown vendor'}</p>
                  <p className="text-xs text-slate-400 mt-1">{po.item_count} items · {po.purchase_date || fmtDate(po.created_at)}</p>
                </button>
              ))}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
                <p className="text-sm font-semibold text-blue-800">{pullTarget.po_number}</p>
                <p className="text-xs text-blue-600">{pullTarget.vendor} · {pullTarget.item_count} items</p>
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
                  data-testid="confirm-pull-btn">
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
