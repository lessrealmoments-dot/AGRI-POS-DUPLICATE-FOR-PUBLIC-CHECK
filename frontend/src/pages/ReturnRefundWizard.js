import { useState, useRef, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Separator } from '../components/ui/separator';
import {
  RotateCcw, Search, Check, X, AlertTriangle, Package,
  ChevronRight, ChevronLeft, Printer, RefreshCw,
  Trash2, ShoppingBag, Banknote, FileText, ArrowDown, Upload
} from 'lucide-react';
import { toast } from 'sonner';
import UploadQRDialog from '../components/UploadQRDialog';
import { useUnsavedChangesGuard } from '../lib/useUnsavedChangesGuard';
import CalcInput from '../components/CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const RETURN_REASONS = [
  'Defective / Not Working',
  'Expired Product',
  'Wrong Product Delivered',
  'Damaged Packaging',
  'Customer Changed Mind',
  'Duplicate Order',
  'Other',
];

const CONDITION_OPTIONS = [
  { value: 'sellable', label: 'Sellable', desc: 'Unopened, good condition — can return to shelf', color: 'emerald' },
  { value: 'damaged', label: 'Damaged', desc: 'Packaging damaged, integrity questionable', color: 'amber' },
  { value: 'expired', label: 'Expired', desc: 'Product past expiry date', color: 'red' },
  { value: 'defective', label: 'Defective', desc: 'Product does not work / contaminated', color: 'red' },
];

const STEPS = [
  { num: 1, label: 'Customer & Reason' },
  { num: 2, label: 'Products' },
  { num: 3, label: 'Condition' },
  { num: 4, label: 'Inventory Action' },
  { num: 5, label: 'Refund Amount' },
  { num: 6, label: 'Confirm & Tender' },
];

function StepIndicator({ current }) {
  return (
    <div className="flex items-center justify-between mb-6">
      {STEPS.map((s, i) => (
        <div key={s.num} className="flex items-center">
          <div className={`flex flex-col items-center ${current >= s.num ? '' : 'opacity-40'}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-colors ${
              current === s.num ? 'bg-[#1A4D2E] text-white ring-2 ring-[#1A4D2E]/30' :
              current > s.num ? 'bg-emerald-500 text-white' : 'bg-slate-200 text-slate-500'
            }`}>
              {current > s.num ? <Check size={14} /> : s.num}
            </div>
            <p className="text-[10px] text-slate-500 mt-1 text-center w-16 leading-tight">{s.label}</p>
          </div>
          {i < STEPS.length - 1 && (
            <div className={`flex-1 h-0.5 mx-2 mt-[-14px] ${current > s.num ? 'bg-emerald-400' : 'bg-slate-200'}`} />
          )}
        </div>
      ))}
    </div>
  );
}

export default function ReturnRefundWizard() {
  const { currentBranch, user } = useAuth();
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);
  const [completed, setCompleted] = useState(null);
  const [uploadQROpen, setUploadQROpen] = useState(false);

  // Step 1
  const [customerName, setCustomerName] = useState('');
  const [customerType, setCustomerType] = useState('walkin');
  const [reason, setReason] = useState('');
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [notes, setNotes] = useState('');

  // Step 2 — products
  const [returnItems, setReturnItems] = useState([]);

  // Unsaved-changes guard — fires when at least one return line has been
  // queued. Provider renders the dialog.
  useUnsavedChangesGuard({
    isDirty: (returnItems || []).length > 0,
    label: 'Return / Refund',
  });
  const [productSearch, setProductSearch] = useState('');
  const [productMatches, setProductMatches] = useState([]);
  const [activeSearchIdx, setActiveSearchIdx] = useState(-1);
  const searchTimer = useRef(null);

  // Steps 3+4 — condition & action per item
  const updateItem = (idx, updates) =>
    setReturnItems(prev => prev.map((it, i) => i === idx ? { ...it, ...updates } : it));

  // Step 5 — refund
  const [refundMethod, setRefundMethod] = useState('full');
  const [customRefund, setCustomRefund] = useState('');
  const [fundSource, setFundSource] = useState('cashier');
  const [fundBalances, setFundBalances] = useState({ cashier: 0, safe: 0 });

  const totalRefundSuggestion = returnItems.reduce((s, it) => {
    const price = it.refund_price_type === 'wholesale'
      ? (it.prices?.wholesale || it.prices?.retail || 0)
      : (it.prices?.retail || 0);
    return s + price * it.quantity;
  }, 0);

  const refundAmount = refundMethod === 'none' ? 0
    : refundMethod === 'partial' ? (parseFloat(customRefund) || 0)
    : totalRefundSuggestion;

  // ── Product search ─────────────────────────────────────────────────────
  const searchProducts = useCallback((q) => {
    setProductSearch(q);
    setActiveSearchIdx(-1);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!q || q.length < 1) { setProductMatches([]); return; }
    searchTimer.current = setTimeout(async () => {
      try {
        const res = await api.get(`${BACKEND_URL}/api/products`, {
          params: { search: q, limit: 8, branch_id: currentBranch?.id }
        });
        setProductMatches(res.data.products || []);
      } catch { setProductMatches([]); }
    }, 200);
  }, [currentBranch?.id]);

  const handleSearchKeyDown = (e) => {
    if (!productMatches.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveSearchIdx(i => Math.min(i + 1, productMatches.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveSearchIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && activeSearchIdx >= 0) { e.preventDefault(); addProduct(productMatches[activeSearchIdx]); }
    else if (e.key === 'Escape') { setProductMatches([]); setActiveSearchIdx(-1); }
  };

  const addProduct = (p) => {
    if (returnItems.find(it => it.product_id === p.id)) {
      toast.info(`${p.name} already in list — increase quantity instead`);
    } else {
      const isVet = (p.category || '').toLowerCase() === 'veterinary';
      setReturnItems(prev => [...prev, {
        product_id: p.id,
        product_name: p.name,
        sku: p.sku,
        category: p.category || '',
        unit: p.unit || '',
        quantity: 1,
        condition: '',
        inventory_action: isVet ? 'pullout' : '',
        cost_price: p.cost_price || 0,
        prices: p.prices || {},
        refund_price_type: 'retail',
        refund_price: p.prices?.retail || 0,
        is_vet: isVet,
      }]);
    }
    setProductSearch('');
    setProductMatches([]);
    setActiveSearchIdx(-1);
  };

  // ── Fetch fund balances before step 5 ─────────────────────────────────
  const goToStep5 = async () => {
    try {
      const res = await api.get(`${BACKEND_URL}/api/purchase-orders/fund-balances`, {
        params: { branch_id: currentBranch?.id }
      });
      setFundBalances({ cashier: res.data.cashier || 0, safe: res.data.safe || 0 });
    } catch {}
    setStep(5);
  };

  // ── Validate step transitions ──────────────────────────────────────────
  const canProceed = () => {
    if (step === 1) return reason.trim().length > 0;
    if (step === 2) return returnItems.length > 0;
    if (step === 3) return returnItems.every(it => it.condition !== '');
    if (step === 4) return returnItems.every(it => it.inventory_action !== '');
    if (step === 5) return true;
    return true;
  };

  const nextStep = () => {
    if (!canProceed()) { toast.error('Please complete all required fields before continuing'); return; }
    if (step === 4) { goToStep5(); return; }
    setStep(s => s + 1);
  };

  // ── Submit ─────────────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!currentBranch) { toast.error('Select a branch'); return; }
    if (refundAmount > 0) {
      const avail = fundSource === 'safe' ? fundBalances.safe : fundBalances.cashier;
      if (refundAmount > avail) {
        toast.error(`Insufficient ${fundSource} balance. Available: ${formatPHP(avail)}`);
        return;
      }
    }
    setSaving(true);
    try {
      const items = returnItems.map(it => {
        const price = it.refund_price_type === 'wholesale'
          ? (it.prices?.wholesale || it.prices?.retail || 0)
          : (it.prices?.retail || 0);
        return {
          ...it,
          refund_price: price,
        };
      });
      const res = await api.post(`${BACKEND_URL}/api/returns`, {
        branch_id: currentBranch.id,
        return_date: new Date().toISOString().slice(0, 10),
        customer_name: customerName || 'Walk-in',
        customer_type: customerType,
        reason,
        invoice_number: invoiceNumber,
        notes,
        items,
        refund_method: refundMethod,
        refund_amount: refundAmount,
        fund_source: fundSource,
      });
      setCompleted(res.data);
      setStep(6);
      toast.success(`Return ${res.data.rma_number} processed!`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to process return');
    }
    setSaving(false);
  };

  const resetWizard = () => {
    setStep(1); setCompleted(null); setUploadQROpen(false);
    setCustomerName(''); setCustomerType('walkin'); setReason(''); setInvoiceNumber(''); setNotes('');
    setReturnItems([]); setRefundMethod('full'); setCustomRefund(''); setFundSource('cashier');
  };

  const printReceipt = () => {
    if (!completed) return;
    const win = window.open('', '_blank');
    const php = (n) => '₱' + (parseFloat(n)||0).toLocaleString('en-PH', {minimumFractionDigits:2});
    win.document.write(`
      <html><head><title>${completed.rma_number}</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 12px; padding: 20px; max-width: 400px; margin: 0 auto; }
        h2 { color: #1A4D2E; margin-bottom: 4px; }
        .sub { color: #666; font-size: 11px; margin-bottom: 16px; }
        table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
        th { background: #1A4D2E; color: white; padding: 6px 8px; text-align: left; font-size: 10px; }
        td { padding: 4px 8px; border-bottom: 1px solid #eee; font-size: 11px; }
        .total { font-weight: bold; font-size: 14px; text-align: right; margin-top: 8px; }
        .note { font-size: 10px; color: #888; margin-top: 12px; text-align: center; }
      </style></head><body>
      <h2>AgriBooks — Return Receipt</h2>
      <div class="sub">RMA: ${completed.rma_number} · ${completed.return_date} · ${currentBranch?.name || ''}</div>
      <p><b>Customer:</b> ${completed.customer_name} &nbsp;|&nbsp; <b>Reason:</b> ${completed.reason}</p>
      ${completed.invoice_number ? `<p><b>Invoice:</b> ${completed.invoice_number}</p>` : ''}
      <table>
        <thead><tr><th>Product</th><th>Qty</th><th>Action</th><th>Refund</th></tr></thead>
        <tbody>
          ${(completed.items||[]).map(it => `<tr>
            <td>${it.product_name}</td>
            <td>${it.quantity} ${it.unit||''}</td>
            <td>${it.inventory_action === 'shelf' ? 'Back to Shelf' : 'Pull Out'}</td>
            <td>${php(it.refund_price * it.quantity)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      <div class="total">Refund: ${php(completed.refund_amount)} (${completed.fund_source||'cash'})</div>
      ${completed.has_pullout ? `<p style="color:red;font-size:10px;">⚠ Pull-out items logged as loss. Owner notified.</p>` : ''}
      <div class="note">Processed by ${completed.processed_by_name} — AgriBooks Business Management</div>
      </body></html>
    `);
    win.document.close();
    win.print();
  };

  if (!currentBranch) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        <p>Please select a branch to process returns.</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-5 animate-fadeIn p-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-[#1A4D2E] flex items-center justify-center">
          <RotateCcw size={20} className="text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>Return & Refund</h1>
          <p className="text-xs text-slate-500">Process customer stock returns with inventory and refund tracking</p>
        </div>
      </div>

      <Card className="border-slate-200">
        <CardContent className="p-6">
          <StepIndicator current={step} />

          {/* ── STEP 1: Customer & Reason ──────────────────────────────── */}
          {step === 1 && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>Step 1 — Customer & Reason</h2>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label className="text-xs text-slate-500">Customer Name (optional)</Label>
                  <Input className="mt-1 h-9" value={customerName} onChange={e => setCustomerName(e.target.value)}
                    placeholder="Walk-in or credit customer name" autoFocus />
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Customer Type</Label>
                  <Select value={customerType} onValueChange={setCustomerType}>
                    <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="walkin">Walk-in</SelectItem>
                      <SelectItem value="credit">Credit Customer</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Label className="text-xs text-slate-500">Return Reason <span className="text-red-500">*</span></Label>
                <Select value={reason} onValueChange={setReason}>
                  <SelectTrigger className="mt-1 h-9"><SelectValue placeholder="Select reason..." /></SelectTrigger>
                  <SelectContent>
                    {RETURN_REASONS.map(r => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label className="text-xs text-slate-500">Original Invoice # (if available)</Label>
                  <Input className="mt-1 h-9" value={invoiceNumber} onChange={e => setInvoiceNumber(e.target.value)}
                    placeholder="e.g. SI-20260222-0001" />
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Notes</Label>
                  <Input className="mt-1 h-9" value={notes} onChange={e => setNotes(e.target.value)}
                    placeholder="Additional notes..." />
                </div>
              </div>
            </div>
          )}

          {/* ── STEP 2: Products ───────────────────────────────────────── */}
          {step === 2 && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>Step 2 — Products Being Returned</h2>
              <div className="relative">
                <Label className="text-xs text-slate-500">Search & Add Product</Label>
                <div className="relative mt-1">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input
                    className="h-9 pl-8" value={productSearch}
                    onChange={e => searchProducts(e.target.value)}
                    onKeyDown={handleSearchKeyDown}
                    onBlur={() => setTimeout(() => { setProductMatches([]); setActiveSearchIdx(-1); }, 150)}
                    placeholder="Type product name or SKU..."
                    autoFocus autoComplete="new-password"
                  />
                </div>
                {productMatches.length > 0 && (
                  <div className="absolute z-50 w-full mt-0.5 bg-white border border-slate-200 rounded-lg shadow-xl max-h-48 overflow-y-auto">
                    {productMatches.map((p, idx) => (
                      <button key={p.id} onMouseDown={() => addProduct(p)}
                        className={`w-full text-left px-3 py-2 border-b border-slate-100 last:border-0 transition-colors ${idx === activeSearchIdx ? 'bg-emerald-50 border-l-[3px] border-l-emerald-700' : 'hover:bg-slate-50'}`}>
                        <span className="font-medium text-sm">{p.name}</span>
                        <span className="text-xs text-slate-400 ml-2">{p.sku} · {p.category}</span>
                        <span className="text-xs font-mono text-slate-600 ml-2">{formatPHP(p.prices?.retail || 0)}</span>
                        {(p.category||'').toLowerCase() === 'veterinary' && (
                          <Badge className="ml-2 text-[9px] bg-red-100 text-red-700">Auto Pull-Out</Badge>
                        )}
                      </button>
                    ))}
                    <div className="px-3 py-1 bg-slate-50 border-t text-[10px] text-slate-400 flex gap-3">
                      <span>↑↓ navigate</span><span>Enter to add</span>
                    </div>
                  </div>
                )}
              </div>

              {returnItems.length > 0 && (
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b">
                      <th className="text-left px-3 py-2 text-xs uppercase text-slate-500">Product</th>
                      <th className="text-center px-3 py-2 text-xs uppercase text-slate-500 w-24">Qty</th>
                      <th className="text-right px-3 py-2 text-xs uppercase text-slate-500">Retail Price</th>
                      <th className="w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {returnItems.map((it, idx) => (
                      <tr key={it.product_id} className="border-b border-slate-100">
                        <td className="px-3 py-2">
                          <p className="font-medium text-sm">{it.product_name}</p>
                          <p className="text-[10px] text-slate-400">{it.sku} · {it.category}</p>
                          {it.is_vet && <Badge className="text-[9px] bg-red-100 text-red-700 mt-0.5">Veterinary — will be pulled out</Badge>}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <CalcInput value={it.quantity}
 onChange={(v) => updateItem(idx, { quantity: parseInt(v) || 1 })}
 className="h-8 w-16 text-center font-mono mx-auto" />
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-sm">{formatPHP(it.prices?.retail || 0)}</td>
                        <td className="px-2 py-2">
                          <button onClick={() => setReturnItems(prev => prev.filter((_, i) => i !== idx))}
                            className="text-slate-300 hover:text-red-500"><Trash2 size={13} /></button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {returnItems.length === 0 && (
                <div className="text-center py-8 text-slate-400">
                  <Package size={32} className="mx-auto mb-2 opacity-40" />
                  <p className="text-sm">Search and add products to return</p>
                </div>
              )}
            </div>
          )}

          {/* ── STEP 3: Condition ──────────────────────────────────────── */}
          {step === 3 && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>Step 3 — Condition Assessment</h2>
              <p className="text-sm text-slate-500">Manager inspects each product and determines condition. Veterinary items are always pulled out.</p>
              {returnItems.map((it, idx) => (
                <Card key={it.product_id} className={`border-2 ${it.condition ? 'border-slate-200' : 'border-amber-300'}`}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <p className="font-semibold">{it.product_name}</p>
                        <p className="text-xs text-slate-400">{it.sku} · Qty: {it.quantity} {it.unit}</p>
                      </div>
                      {it.is_vet && <Badge className="bg-red-100 text-red-700 text-xs">Veterinary</Badge>}
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {CONDITION_OPTIONS.map(opt => {
                        const colors = {
                          emerald: 'border-emerald-400 bg-emerald-50 text-emerald-800',
                          amber: 'border-amber-400 bg-amber-50 text-amber-800',
                          red: 'border-red-400 bg-red-50 text-red-800',
                        };
                        const selected = it.condition === opt.value;
                        return (
                          <button key={opt.value}
                            onClick={() => {
                              const autoAction = (opt.value === 'sellable' && !it.is_vet) ? '' : 'pullout';
                              updateItem(idx, { condition: opt.value, inventory_action: autoAction });
                            }}
                            className={`p-2.5 rounded-lg border-2 text-left text-xs transition-all ${selected ? colors[opt.color] : 'border-slate-200 hover:border-slate-300'}`}>
                            <p className="font-semibold">{opt.label}</p>
                            <p className="opacity-70 mt-0.5 text-[10px] leading-tight">{opt.desc}</p>
                          </button>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {/* ── STEP 4: Inventory Action ───────────────────────────────── */}
          {step === 4 && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>Step 4 — Inventory Decision</h2>
              <p className="text-sm text-slate-500">Decide what happens to each item's stock. Veterinary items are always pulled out.</p>
              {returnItems.map((it, idx) => {
                const canReturnToShelf = it.condition === 'sellable' && !it.is_vet;
                // Vet and non-sellable = forced pullout
                if (!canReturnToShelf && it.inventory_action !== 'pullout') {
                  updateItem(idx, { inventory_action: 'pullout' });
                }
                return (
                  <Card key={it.product_id} className="border-slate-200">
                    <CardContent className="p-4">
                      <div className="flex items-center justify-between mb-3">
                        <div>
                          <p className="font-semibold">{it.product_name}</p>
                          <p className="text-xs text-slate-400">{it.quantity} {it.unit} · {it.condition}</p>
                        </div>
                        <Badge className={it.condition === 'sellable' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}>
                          {it.condition}
                        </Badge>
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <button
                          disabled={!canReturnToShelf}
                          onClick={() => updateItem(idx, { inventory_action: 'shelf' })}
                          className={`p-3 rounded-lg border-2 text-left transition-all ${
                            it.inventory_action === 'shelf'
                              ? 'border-emerald-500 bg-emerald-50'
                              : canReturnToShelf ? 'border-slate-200 hover:border-emerald-300' : 'border-slate-100 opacity-40 cursor-not-allowed bg-slate-50'
                          }`}>
                          <div className="flex items-center gap-2 mb-1">
                            <ShoppingBag size={16} className="text-emerald-600" />
                            <p className="font-semibold text-sm">Return to Shelf</p>
                          </div>
                          <p className="text-xs text-slate-500">Inventory +{it.quantity} {it.unit}</p>
                        </button>
                        <button
                          onClick={() => updateItem(idx, { inventory_action: 'pullout' })}
                          className={`p-3 rounded-lg border-2 text-left transition-all ${
                            it.inventory_action === 'pullout'
                              ? 'border-red-500 bg-red-50'
                              : 'border-slate-200 hover:border-red-300'
                          }`}>
                          <div className="flex items-center gap-2 mb-1">
                            <Trash2 size={16} className="text-red-600" />
                            <p className="font-semibold text-sm">Pull Out (Loss)</p>
                          </div>
                          <p className="text-xs text-slate-500">
                            Recorded as loss · {formatPHP(it.cost_price * it.quantity)} capital
                          </p>
                          <p className="text-[10px] text-red-500 mt-0.5">Owner notified · Audit logged</p>
                        </button>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}

          {/* ── STEP 5: Refund Amount ──────────────────────────────────── */}
          {step === 5 && (
            <div className="space-y-4">
              <h2 className="text-base font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>Step 5 — Refund Amount</h2>

              {/* Items with refund price selector */}
              <div className="space-y-2">
                {returnItems.map((it, idx) => (
                  <div key={it.product_id} className="flex items-center gap-3 p-3 rounded-lg bg-slate-50 border border-slate-200">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">{it.product_name}</p>
                      <p className="text-xs text-slate-400">{it.quantity} {it.unit}</p>
                    </div>
                    <div className="text-xs text-slate-500 text-right">
                      <p>Retail: {formatPHP(it.prices?.retail || 0)}</p>
                      <p>Wholesale: {formatPHP(it.prices?.wholesale || 0)}</p>
                    </div>
                    <Select value={it.refund_price_type || 'retail'}
                      onValueChange={v => updateItem(idx, { refund_price_type: v })}>
                      <SelectTrigger className="h-8 w-28 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="retail">@ Retail</SelectItem>
                        <SelectItem value="wholesale">@ Wholesale</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-sm font-mono font-bold text-[#1A4D2E] w-24 text-right">
                      {formatPHP((it.refund_price_type === 'wholesale' ? (it.prices?.wholesale || 0) : (it.prices?.retail || 0)) * it.quantity)}
                    </p>
                  </div>
                ))}
                <div className="flex justify-between text-sm font-bold pt-2 border-t">
                  <span>Suggested Total Refund</span>
                  <span className="font-mono text-[#1A4D2E]">{formatPHP(totalRefundSuggestion)}</span>
                </div>
              </div>

              {/* Refund method */}
              <div>
                <Label className="text-xs text-slate-500">Refund Method</Label>
                <div className="grid grid-cols-3 gap-2 mt-1">
                  {[
                    { v: 'full', label: 'Full Refund', desc: formatPHP(totalRefundSuggestion) },
                    { v: 'partial', label: 'Partial / Custom', desc: 'Enter amount' },
                    { v: 'none', label: 'No Refund', desc: 'Exchange only' },
                  ].map(opt => (
                    <button key={opt.v} onClick={() => setRefundMethod(opt.v)}
                      className={`p-2.5 rounded-lg border-2 text-left text-xs transition-all ${refundMethod === opt.v ? 'border-[#1A4D2E] bg-emerald-50' : 'border-slate-200 hover:border-slate-300'}`}>
                      <p className="font-semibold">{opt.label}</p>
                      <p className="text-slate-500 mt-0.5">{opt.desc}</p>
                    </button>
                  ))}
                </div>
                {refundMethod === 'partial' && (
                  <div className="mt-2">
                    <Label className="text-xs text-slate-500">Custom Amount</Label>
                    <CalcInput value={customRefund}
 onChange={(v) => setCustomRefund(v)}
 className="mt-1 h-9 font-mono text-lg" placeholder="0.00" autoFocus />
                  </div>
                )}
              </div>

              {/* Fund source */}
              {refundAmount > 0 && (
                <div>
                  <Label className="text-xs text-slate-500">Fund Source</Label>
                  <div className="grid grid-cols-2 gap-2 mt-1">
                    {[
                      { k: 'cashier', label: 'Cashier', bal: fundBalances.cashier },
                      { k: 'safe', label: 'Safe / Vault', bal: fundBalances.safe },
                    ].map(f => (
                      <button key={f.k} onClick={() => setFundSource(f.k)}
                        className={`p-3 rounded-lg border-2 text-left transition-all ${fundSource === f.k ? 'border-[#1A4D2E] bg-emerald-50' : 'border-slate-200 hover:border-slate-300'}`}>
                        <p className="text-xs font-medium text-slate-600">{f.label}</p>
                        <p className={`text-lg font-bold font-mono ${f.bal < refundAmount ? 'text-red-600' : 'text-[#1A4D2E]'}`}>
                          {formatPHP(f.bal)}
                        </p>
                        {f.bal < refundAmount && <p className="text-[10px] text-red-500">Insufficient</p>}
                        {f.bal >= refundAmount && fundSource === f.k && (
                          <p className="text-[10px] text-emerald-600">After: {formatPHP(f.bal - refundAmount)}</p>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── STEP 6: Confirm & Complete ──────────────────────────────── */}
          {step === 6 && completed && (
            <div className="space-y-4">
              <div className="text-center py-4">
                <div className="w-14 h-14 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-3">
                  <Check size={28} className="text-emerald-600" />
                </div>
                <h2 className="text-xl font-bold text-emerald-700" style={{ fontFamily: 'Manrope' }}>Return Processed!</h2>
                <p className="text-slate-500 text-sm mt-1">RMA Number: <b className="font-mono">{completed.rma_number}</b></p>
              </div>

              <Card className="border-slate-200">
                <CardContent className="p-4 space-y-2">
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div><span className="text-slate-400">Customer:</span> <b>{completed.customer_name}</b></div>
                    <div><span className="text-slate-400">Reason:</span> {completed.reason}</div>
                    <div><span className="text-slate-400">Date:</span> {completed.return_date}</div>
                    {completed.invoice_number && <div><span className="text-slate-400">Invoice:</span> {completed.invoice_number}</div>}
                  </div>
                  <Separator />
                  {completed.items?.map((it, i) => (
                    <div key={i} className="flex items-center justify-between text-sm py-0.5">
                      <span>{it.product_name} × {it.quantity}</span>
                      <div className="flex items-center gap-2">
                        <Badge className={`text-[9px] ${it.inventory_action === 'shelf' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>
                          {it.inventory_action === 'shelf' ? 'Back to Shelf' : 'Pull Out'}
                        </Badge>
                      </div>
                    </div>
                  ))}
                  <Separator />
                  <div className="flex justify-between font-bold text-lg">
                    <span>Refund Tendered</span>
                    <span className="font-mono text-[#1A4D2E]">{formatPHP(completed.refund_amount)}</span>
                  </div>
                  {completed.has_pullout && (
                    <div className="mt-2 p-2 rounded bg-red-50 border border-red-200 text-xs text-red-700">
                      <AlertTriangle size={12} className="inline mr-1" />
                      Pull-out loss of {formatPHP(completed.total_loss_value)} recorded. Owner notified.
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className="flex gap-3 justify-center">
                <Button variant="outline" onClick={printReceipt}>
                  <Printer size={14} className="mr-1.5" /> Print Receipt
                </Button>
                <Button variant="outline" onClick={() => setUploadQROpen(true)}
                  className="border-blue-300 text-blue-700 hover:bg-blue-50">
                  <Upload size={14} className="mr-1.5" /> Upload Product Photos
                </Button>
                <Button onClick={resetWizard} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  <RotateCcw size={14} className="mr-1.5" /> New Return
                </Button>
              </div>
              <UploadQRDialog
                open={uploadQROpen}
                onClose={(count) => { setUploadQROpen(false); if (count > 0) toast.success(`${count} photo(s) saved to return ${completed?.rma_number}`); }}
                recordType="return"
                recordId={completed?.id}
              />
            </div>
          )}

          {/* Navigation */}
          {step < 6 && (
            <div className="flex justify-between mt-6 pt-4 border-t border-slate-100">
              <Button variant="outline" onClick={() => setStep(s => s - 1)} disabled={step === 1}>
                <ChevronLeft size={14} className="mr-1.5" /> Back
              </Button>
              {step < 5 ? (
                <Button onClick={nextStep} disabled={!canProceed()}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  data-testid={`step-${step}-next`}>
                  Continue <ChevronRight size={14} className="ml-1.5" />
                </Button>
              ) : (
                <Button onClick={handleSubmit} disabled={saving}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  data-testid="process-return-btn">
                  {saving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                  Process Return & Refund
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
