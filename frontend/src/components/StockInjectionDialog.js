import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Badge } from './ui/badge';
import { AlertTriangle, Building2, Package, Plus, Minus, Equal, Loader2, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';
import CalcInput from './CalcInput';

const REASON_LABELS = {
  opening_balance:  'Opening Balance (new branch seed)',
  count_variance:   'Physical Count Variance',
  damaged_recovery: 'Damaged Goods Recovery',
  promo_stock:      'Promo / Free Stock from Vendor',
  vendor_return:    'Vendor Return (not via PO)',
  other:            'Other (explain below)',
};

/**
 * StockInjectionDialog — Admin-only direct inventory override.
 *
 * Does NOT touch moving_avg_cost, last_purchase_cost or branch_prices.
 * Logs to `stock_injections` audit + `movements` ledger as type='injection'.
 *
 * Flow (per Iter 217b user spec):
 *   1. Pick branch FIRST (big red banner appears once chosen)
 *   2. Pick mode: add | deduct | set
 *   3. Enter quantity, reason type, reason note (≥10 chars)
 *   4. Submit → backend enforces admin role
 */
export default function StockInjectionDialog({ open, onOpenChange, product, onDone }) {
  const [branches, setBranches] = useState([]);
  const [branchId, setBranchId] = useState('');
  const [mode, setMode] = useState('add');
  const [quantity, setQuantity] = useState('');
  const [reasonType, setReasonType] = useState('opening_balance');
  const [reasonNote, setReasonNote] = useState('');
  const [currentStock, setCurrentStock] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset when dialog opens
  useEffect(() => {
    if (open) {
      setBranchId(''); setMode('add'); setQuantity('');
      setReasonType('opening_balance'); setReasonNote(''); setCurrentStock(null);
    }
  }, [open]);

  // Load branches
  useEffect(() => {
    if (!open) return;
    api.get('/branches').then(r => setBranches(r.data || [])).catch(() => setBranches([]));
  }, [open]);

  // Load current stock when branch picked
  useEffect(() => {
    if (!branchId || !product?.id) { setCurrentStock(null); return; }
    api.get(`/inventory?product_id=${product.id}&branch_id=${branchId}`)
      .then(r => {
        const row = Array.isArray(r.data) ? r.data.find(x => x.product_id === product.id && x.branch_id === branchId) : null;
        setCurrentStock(row ? parseFloat(row.quantity || 0) : 0);
      })
      .catch(() => setCurrentStock(0));
  }, [branchId, product?.id]);

  const selectedBranch = branches.find(b => b.id === branchId);
  const qtyNum = parseFloat(quantity) || 0;
  const projected = currentStock === null ? null :
    mode === 'add' ? currentStock + qtyNum :
    mode === 'deduct' ? currentStock - qtyNum :
    qtyNum; // set
  const diff = currentStock === null ? 0 : projected - currentStock;

  const canSubmit = !!branchId && !!product?.id && qtyNum >= 0
    && (mode !== 'deduct' || currentStock === null || qtyNum <= currentStock)
    && reasonNote.trim().length >= 10 && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const res = await api.post('/inventory/admin-inject', {
        product_id: product.id,
        branch_id: branchId,
        mode,
        quantity: qtyNum,
        reason_type: reasonType,
        reason_note: reasonNote.trim(),
      });
      toast.success(
        `Stock ${mode === 'set' ? 'set to' : mode === 'add' ? '+' : '-'} ` +
        `${mode === 'set' ? res.data.new_quantity : qtyNum} for ${product.name} at ${selectedBranch?.name}`,
        { description: `New total: ${res.data.new_quantity}` }
      );
      onOpenChange(false);
      onDone?.(res.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Injection failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (!product) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl" data-testid="stock-injection-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <ShieldAlert size={18} className="text-red-600" />
            Admin Stock Injection
          </DialogTitle>
          <DialogDescription className="text-xs">
            Directly adjust inventory without touching moving-avg cost or current retail.
            Full audit trail — logged to Stock Movements and Audit Pulse.
          </DialogDescription>
        </DialogHeader>

        {/* Product chip */}
        <div className="flex items-center gap-2 text-sm bg-slate-50 px-3 py-2 rounded-lg">
          <Package size={14} className="text-slate-500" />
          <span className="font-semibold text-slate-800">{product.name}</span>
          <span className="text-[10px] font-mono text-slate-400">{product.sku}</span>
        </div>

        {/* Step 1 — Branch Picker (FIRST per user spec) */}
        <div className="space-y-1.5">
          <Label className="text-xs font-semibold text-slate-700">1. Pick Branch *</Label>
          <Select value={branchId} onValueChange={setBranchId}>
            <SelectTrigger data-testid="injection-branch-select" className={!branchId ? 'border-amber-400' : ''}>
              <SelectValue placeholder="Choose the branch to adjust…" />
            </SelectTrigger>
            <SelectContent>
              {branches.map(b => (
                <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Big red warning banner once branch picked */}
        {selectedBranch && (
          <div className="flex items-start gap-2 bg-red-50 border-2 border-red-300 rounded-lg px-3 py-2.5"
               data-testid="injection-red-banner">
            <AlertTriangle size={18} className="text-red-600 mt-0.5 shrink-0" />
            <div className="text-sm">
              <p className="font-bold text-red-800">
                ⚠️ You are editing inventory for <span className="underline">{selectedBranch.name}</span>
              </p>
              <p className="text-xs text-red-700 mt-0.5">
                This bypasses the normal PO / Branch Transfer flow. Audit trail will show YOU as the injector.
              </p>
              {currentStock !== null && (
                <p className="text-xs text-red-900 mt-1.5">
                  Current stock at this branch: <span className="font-mono font-bold">{currentStock}</span>
                </p>
              )}
            </div>
          </div>
        )}

        {/* Step 2 — Mode */}
        {branchId && (
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-slate-700">2. Action *</Label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { k: 'add',    label: 'Add',    icon: Plus,  cls: 'border-emerald-300 text-emerald-700 hover:bg-emerald-50' },
                { k: 'deduct', label: 'Deduct', icon: Minus, cls: 'border-red-300 text-red-700 hover:bg-red-50' },
                { k: 'set',    label: 'Set Total', icon: Equal, cls: 'border-amber-300 text-amber-700 hover:bg-amber-50' },
              ].map(({ k, label, icon: Icon, cls }) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setMode(k)}
                  className={`flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg border-2 text-xs font-semibold transition ${mode === k ? cls.replace('hover:', '') + ' bg-opacity-50' : 'border-slate-200 text-slate-500'}`}
                  data-testid={`injection-mode-${k}`}
                >
                  <Icon size={12} /> {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 3 — Quantity */}
        {branchId && (
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-slate-700">
              3. {mode === 'set' ? 'New Total Quantity' : 'Quantity to ' + (mode === 'add' ? 'Add' : 'Deduct')} *
            </Label>
            <CalcInput
              value={quantity}
              onChange={setQuantity}
              className="font-mono text-lg"
              placeholder="0"
              data-testid="injection-qty"
            />
            {projected !== null && qtyNum > 0 && (
              <p className="text-xs text-slate-600 font-mono">
                Projected: <b>{currentStock} → {projected}</b>
                <span className={`ml-2 ${diff >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                  ({diff >= 0 ? '+' : ''}{diff})
                </span>
              </p>
            )}
            {mode === 'deduct' && qtyNum > (currentStock || 0) && (
              <p className="text-xs text-red-600 font-semibold">
                Cannot deduct more than current stock ({currentStock || 0})
              </p>
            )}
          </div>
        )}

        {/* Step 4 — Reason */}
        {branchId && (
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-slate-700">4. Reason *</Label>
            <Select value={reasonType} onValueChange={setReasonType}>
              <SelectTrigger data-testid="injection-reason-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(REASON_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              value={reasonNote}
              onChange={e => setReasonNote(e.target.value)}
              placeholder="Describe this injection in detail (≥ 10 chars). Auditor will read this."
              rows={2}
              className="text-xs mt-1.5"
              data-testid="injection-reason-note"
            />
            <p className="text-[10px] text-slate-400">{reasonNote.trim().length} / 10 min chars</p>
          </div>
        )}

        <div className="flex justify-between items-center pt-2 border-t">
          <Badge className="bg-slate-100 text-slate-600 text-[10px]">
            <Building2 size={10} className="mr-1" /> Moving avg & retail NOT changed
          </Badge>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>Cancel</Button>
            <Button
              onClick={submit}
              disabled={!canSubmit}
              className="bg-red-600 hover:bg-red-700 text-white"
              data-testid="injection-submit-btn"
            >
              {submitting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : <ShieldAlert size={14} className="mr-1.5" />}
              Inject Stock
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
