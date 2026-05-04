/**
 * BranchCapitalWizard
 * ===================
 * "Quick-Fill Capital" modal.
 *
 * Step 1: Choose source + target branch
 * Step 2: Per-category rule table — flat ₱ or % add-on
 *         Each row shows: category | product count | cost range (reference) | add-on type | add-on value
 * Step 3: Results — X applied, Y skipped (with expandable skipped-items report)
 *
 * KEY RULE: add-on is per category but applied PER PRODUCT.
 *   Enertone cost ₱920  +  Veterinary add-on ₱15  =  ₱935 at target branch
 *   Volplex cost  ₱885  +  Veterinary add-on ₱15  =  ₱900 at target branch
 *   (NOT the category average — every product keeps its own individual cost)
 */

import { useState, useEffect, useCallback } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import { Zap, ChevronRight, CheckCircle, AlertTriangle, ChevronDown, ChevronUp, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';
import CalcInput from './CalcInput';

const STEP = { SOURCE: 'source', RULES: 'rules', RESULT: 'result' };

export function BranchCapitalWizard({ open, onClose, targetBranch }) {
  const { branches } = useAuth();
  const [step, setStep] = useState(STEP.SOURCE);
  const [sourceBranchId, setSourceBranchId] = useState('');
  const [categoryData, setCategoryData] = useState([]); // from /capital-summary
  const [rules, setRules] = useState([]);               // per-category rule rows
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [showSkipped, setShowSkipped] = useState(false);

  // Reset when opened
  useEffect(() => {
    if (open) {
      setStep(STEP.SOURCE);
      setSourceBranchId('');
      setCategoryData([]);
      setRules([]);
      setResult(null);
      setShowSkipped(false);
    }
  }, [open]);

  // Load category summary when source branch is selected
  const loadCategories = useCallback(async (branchId) => {
    if (!branchId) return;
    setLoading(true);
    try {
      const res = await api.get('/branch-prices/capital-summary', { params: { source_branch_id: branchId } });
      setCategoryData(res.data);
      // Init rules — all categories deselected, flat, 0 add-on
      setRules(res.data.map(cat => ({
        category: cat.category,
        product_count: cat.product_count,
        min_cost: cat.min_cost,
        max_cost: cat.max_cost,
        avg_cost: cat.avg_cost,
        selected: false,
        add_on_type: 'flat',
        add_on_value: 0,
      })));
      setStep(STEP.RULES);
    } catch (e) {
      toast.error('Failed to load category data');
    }
    setLoading(false);
  }, []);

  const updateRule = (category, updates) => {
    setRules(prev => prev.map(r => r.category === category ? { ...r, ...updates } : r));
  };

  const toggleAll = (checked) => {
    setRules(prev => prev.map(r => ({ ...r, selected: checked })));
  };

  const selectedCount = rules.filter(r => r.selected).length;
  const totalProducts = rules.filter(r => r.selected).reduce((s, r) => s + r.product_count, 0);

  // Preview: compute what a sample product would cost
  const previewCost = (rule) => {
    if (!rule.selected) return null;
    const sample = rule.avg_cost;
    if (rule.add_on_type === 'percent') {
      return sample * (1 + rule.add_on_value / 100);
    }
    return sample + rule.add_on_value;
  };

  const handleApply = async () => {
    if (selectedCount === 0) { toast.error('Select at least one category'); return; }
    setLoading(true);
    try {
      const res = await api.post('/branch-prices/quick-fill', {
        source_branch_id: sourceBranchId,
        target_branch_id: targetBranch.id,
        category_rules: rules.map(r => ({
          category: r.category,
          selected: r.selected,
          add_on_type: r.add_on_type,
          add_on_value: r.add_on_value,
        })),
      });
      setResult(res.data);
      setStep(STEP.RESULT);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Quick-fill failed');
    }
    setLoading(false);
  };

  const sourceBranch = branches.find(b => b.id === sourceBranchId);

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
            <Zap size={18} className="text-amber-500" />
            Branch Capital Quick-Fill
            {targetBranch && (
              <Badge className="bg-blue-100 text-blue-700 border-0 font-normal ml-1">
                → {targetBranch.name}
              </Badge>
            )}
          </DialogTitle>
          <p className="text-xs text-slate-500 mt-1">
            Copy capital costs from a source branch and apply a per-category add-on to each product individually.
          </p>
        </DialogHeader>

        <div className="flex-1 overflow-auto">

          {/* ── STEP 1: Choose source branch ── */}
          {step === STEP.SOURCE && (
            <div className="space-y-5 py-3">
              <div className="p-4 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-700">
                <strong>How it works:</strong> For each product in the selected categories,
                the new capital = <em>that product's cost at the source branch</em> + your add-on.
                Products that already have a custom cost at <strong>{targetBranch?.name}</strong> are skipped and reported.
              </div>
              <div>
                <label className="text-sm font-medium mb-2 block">
                  Source branch to copy capitals from <span className="text-red-500">*</span>
                </label>
                <Select value={sourceBranchId} onValueChange={setSourceBranchId}>
                  <SelectTrigger className="w-full" data-testid="source-branch-select">
                    <SelectValue placeholder="Select source branch..." />
                  </SelectTrigger>
                  <SelectContent>
                    {branches.filter(b => b.id !== targetBranch?.id).map(b => (
                      <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {sourceBranchId && (
                <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm flex items-start gap-2">
                  <CheckCircle size={15} className="text-emerald-600 mt-0.5 shrink-0" />
                  <div>
                    <strong className="text-emerald-800">{sourceBranch?.name}</strong>
                    <span className="text-emerald-600"> → </span>
                    <strong className="text-emerald-800">{targetBranch?.name}</strong>
                    <p className="text-emerald-600 text-xs mt-0.5">
                      Capitals will be copied from {sourceBranch?.name} with your per-category add-on applied.
                    </p>
                  </div>
                </div>
              )}
              <div className="flex justify-end">
                <Button onClick={() => loadCategories(sourceBranchId)}
                  disabled={!sourceBranchId || loading}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  {loading ? 'Loading...' : <>Next: Set Add-ons <ChevronRight size={15} className="ml-1" /></>}
                </Button>
              </div>
            </div>
          )}

          {/* ── STEP 2: Category rules table ── */}
          {step === STEP.RULES && (
            <div className="space-y-4 py-2">
              {/* Selection summary */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <input type="checkbox"
                    checked={selectedCount === rules.length && rules.length > 0}
                    onChange={e => toggleAll(e.target.checked)}
                    className="rounded border-slate-300 cursor-pointer" />
                  <span className="text-sm text-slate-600">
                    {selectedCount > 0
                      ? <><strong>{selectedCount}</strong> categories · <strong>{totalProducts}</strong> products will be updated</>
                      : 'Check categories to include'}
                  </span>
                </div>
                <Button variant="ghost" size="sm" className="text-xs text-slate-400"
                  onClick={() => { setStep(STEP.SOURCE); }}>← Back</Button>
              </div>

              <div className="overflow-auto rounded-lg border border-slate-200">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b-2 border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <th className="px-3 py-2 w-8"></th>
                      <th className="px-3 py-2 text-left font-semibold">Category</th>
                      <th className="px-3 py-2 text-center font-semibold">Products</th>
                      <th className="px-3 py-2 text-center font-semibold">
                        Cost Range
                        <span className="text-[10px] normal-case tracking-normal font-normal block text-slate-400">at {sourceBranch?.name} (reference only)</span>
                      </th>
                      <th className="px-3 py-2 text-center font-semibold">Add-on Type</th>
                      <th className="px-3 py-2 text-center font-semibold">Add-on Value</th>
                      <th className="px-3 py-2 text-center font-semibold">
                        Preview
                        <span className="text-[10px] normal-case font-normal block text-slate-400">avg cost + add-on</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map(rule => {
                      const preview = previewCost(rule);
                      return (
                        <tr key={rule.category}
                          className={`border-b border-slate-100 transition-colors ${rule.selected ? 'bg-amber-50/40' : 'opacity-60'}`}>
                          <td className="px-3 py-2 text-center">
                            <input type="checkbox" checked={rule.selected}
                              onChange={e => updateRule(rule.category, { selected: e.target.checked })}
                              className="rounded border-slate-300 cursor-pointer" />
                          </td>
                          <td className="px-3 py-2 font-medium">{rule.category}</td>
                          <td className="px-3 py-2 text-center text-slate-500">{rule.product_count}</td>
                          <td className="px-3 py-2 text-center text-xs text-slate-500 font-mono">
                            {rule.min_cost > 0
                              ? <>{formatPHP(rule.min_cost)} – {formatPHP(rule.max_cost)}</>
                              : <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-3 py-2">
                            <Select value={rule.add_on_type}
                              onValueChange={v => updateRule(rule.category, { add_on_type: v })}
                              disabled={!rule.selected}>
                              <SelectTrigger className="h-7 text-xs w-24">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="flat">Flat ₱</SelectItem>
                                <SelectItem value="percent">Percent %</SelectItem>
                              </SelectContent>
                            </Select>
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-1">
                              <span className="text-xs text-slate-400">
                                {rule.add_on_type === 'flat' ? '₱' : '+'}
                              </span>
                              <CalcInput value={rule.add_on_value}
 onChange={(v) => updateRule(rule.category, { add_on_value: parseFloat(v) || 0 })}
 disabled={!rule.selected}
 className="h-7 w-20 text-right text-xs font-mono"
 data-testid={`addon-${rule.category}`} />
                              {rule.add_on_type === 'percent' && <span className="text-xs text-slate-400">%</span>}
                            </div>
                          </td>
                          <td className="px-3 py-2 text-center font-mono text-xs">
                            {preview != null && rule.selected ? (
                              <span className="text-emerald-700 font-semibold">{formatPHP(preview)}</span>
                            ) : <span className="text-slate-300">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-xs text-slate-500">
                <strong className="text-slate-700">Note:</strong> Cost Range and Preview are based on category averages for reference only.
                The actual calculation is <strong>per product</strong>:
                each product's own capital at {sourceBranch?.name} + your add-on = new capital at {targetBranch?.name}.
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={onClose}>Cancel</Button>
                <Button onClick={handleApply} disabled={loading || selectedCount === 0}
                  className="bg-amber-600 hover:bg-amber-700 text-white min-w-40"
                  data-testid="apply-quick-fill-btn">
                  {loading
                    ? <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />Applying...</>
                    : <><Zap size={14} className="mr-1.5" />Apply to {totalProducts} Products</>}
                </Button>
              </div>
            </div>
          )}

          {/* ── STEP 3: Results ── */}
          {step === STEP.RESULT && result && (
            <div className="space-y-4 py-2">
              {/* Summary cards */}
              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 rounded-lg bg-emerald-50 border border-emerald-200 flex items-center gap-3">
                  <CheckCircle size={28} className="text-emerald-600 shrink-0" />
                  <div>
                    <div className="text-2xl font-bold text-emerald-800">{result.applied}</div>
                    <div className="text-xs text-emerald-600">Capitals set at {targetBranch?.name}</div>
                  </div>
                </div>
                <div className={`p-4 rounded-lg border flex items-center gap-3 ${result.skipped > 0 ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-200'}`}>
                  <AlertTriangle size={28} className={result.skipped > 0 ? 'text-amber-500 shrink-0' : 'text-slate-300 shrink-0'} />
                  <div>
                    <div className={`text-2xl font-bold ${result.skipped > 0 ? 'text-amber-700' : 'text-slate-400'}`}>{result.skipped}</div>
                    <div className="text-xs text-slate-500">Skipped (already had custom cost)</div>
                  </div>
                </div>
              </div>

              {/* Skipped items report */}
              {result.skipped > 0 && (
                <div className="rounded-lg border border-amber-200 overflow-hidden">
                  <button onClick={() => setShowSkipped(s => !s)}
                    className="w-full flex items-center justify-between px-4 py-3 bg-amber-50 text-sm font-medium text-amber-800 hover:bg-amber-100 transition-colors">
                    <span>Skipped Products Report ({result.skipped_items.length})</span>
                    {showSkipped ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>
                  {showSkipped && (
                    <div className="max-h-60 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="bg-amber-50/50 border-b border-amber-200">
                            <th className="px-3 py-2 text-left font-medium text-amber-700">Product</th>
                            <th className="px-3 py-2 text-left font-medium text-amber-700">Category</th>
                            <th className="px-3 py-2 text-right font-medium text-amber-700">Source Cost</th>
                            <th className="px-3 py-2 text-right font-medium text-amber-700">Existing Cost</th>
                            <th className="px-3 py-2 text-left font-medium text-amber-700">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.skipped_items.map((item, i) => (
                            <tr key={i} className="border-b border-amber-100 last:border-0 hover:bg-amber-50/30">
                              <td className="px-3 py-2">
                                <div className="font-medium text-slate-700">{item.name}</div>
                                <div className="text-slate-400 font-mono">{item.sku}</div>
                              </td>
                              <td className="px-3 py-2 text-slate-500">{item.category}</td>
                              <td className="px-3 py-2 text-right font-mono">{formatPHP(item.source_cost)}</td>
                              <td className="px-3 py-2 text-right font-mono font-semibold text-slate-700">{formatPHP(item.existing_cost)}</td>
                              <td className="px-3 py-2 text-slate-400 italic">{item.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => { setStep(STEP.SOURCE); setResult(null); }}>
                  <RotateCcw size={13} className="mr-1.5" /> Run Again
                </Button>
                <Button onClick={onClose} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  Done
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
