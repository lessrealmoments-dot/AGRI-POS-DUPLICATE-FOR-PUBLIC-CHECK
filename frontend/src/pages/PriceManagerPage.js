import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Checkbox } from '../components/ui/checkbox';
import { Label } from '../components/ui/label';
import {
  ChevronDown, ChevronRight, CheckCircle2, AlertTriangle, XCircle,
  Search, Plus, Save, RefreshCw, Zap, X, Package, TrendingUp
} from 'lucide-react';
import { toast } from 'sonner';

// ── Helpers ──────────────────────────────────────────────────────────────────

const formatPHP = (v) => `₱${(parseFloat(v) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Compute price status for a single (price, capital) pair.
 * Returns: 'error' | 'warning' | 'ok' | 'no_capital' | 'no_price'
 */
function priceStatus(price, capital) {
  const p = parseFloat(price) || 0;
  const c = parseFloat(capital) || 0;
  if (c <= 0) return 'no_capital';
  if (p <= 0) return 'no_price';
  if (p < c) return 'error';
  const markup = p - c;
  const pct = c > 0 ? markup / c : 0;
  if (markup < 20 && pct < 0.05) return 'error';
  if (markup < 20) return 'warning';
  return 'ok';
}

/**
 * Worst status across all scheme/branch combinations for a product entry.
 */
function productCardStatus(entry, schemeKeys) {
  if (!entry.branchData?.length) return 'no_capital';
  let worst = 'ok';
  const rank = { error: 3, warning: 2, no_price: 1, no_capital: 1, ok: 0 };

  for (const bd of entry.branchData) {
    const cap = parseFloat(bd.capital) || 0;
    if (cap <= 0) { worst = 'no_capital'; continue; }
    for (const key of schemeKeys) {
      const pendingPrice = entry.pending?.[bd.branch_id]?.prices?.[key];
      const price = pendingPrice !== undefined ? pendingPrice : (parseFloat(bd.prices?.[key]) || 0);
      const s = priceStatus(price, cap);
      if (rank[s] > rank[worst]) worst = s;
    }
  }
  return worst;
}

function StatusDot({ status, size = 'sm' }) {
  const cls = size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3';
  if (status === 'error') return <span className={`${cls} rounded-full bg-red-500 inline-block shrink-0`} title="Price issue" />;
  if (status === 'warning') return <span className={`${cls} rounded-full bg-amber-400 inline-block shrink-0`} title="Low margin warning" />;
  if (status === 'no_capital') return <span className={`${cls} rounded-full bg-slate-400 inline-block shrink-0`} title="No capital set" />;
  if (status === 'no_price') return <span className={`${cls} rounded-full bg-blue-400 inline-block shrink-0`} title="No price set" />;
  return <span className={`${cls} rounded-full bg-emerald-500 inline-block shrink-0`} title="OK" />;
}

function PriceInput({ value, capital, onChange, placeholder = '0.00', readOnly = false }) {
  const s = priceStatus(value, capital);
  const borderClass = s === 'error' ? 'border-red-400 bg-red-50' : s === 'warning' ? 'border-amber-400 bg-amber-50/50' : '';
  return (
    <Input
      type="number"
      value={value === '' || value === undefined ? '' : value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      readOnly={readOnly}
      className={`h-7 text-xs w-24 px-2 ${borderClass} ${readOnly ? 'bg-slate-50 text-slate-400' : ''}`}
      step="0.01"
      min="0"
    />
  );
}

// ── Branch Row (price editing) ────────────────────────────────────────────────

function BranchRow({ bd, entry, schemeKeys, isCapitalTab, onChange, onCapitalChange }) {
  const pendingPrices = entry.pending?.[bd.branch_id]?.prices || {};
  const pendingCap = entry.pending?.[bd.branch_id]?.capital;
  const displayCap = pendingCap !== undefined ? pendingCap : bd.capital;
  const isGlobal = bd.branch_id === '__global__';

  return (
    <tr className="border-t border-slate-100 hover:bg-slate-50/50">
      <td className="py-1.5 px-3 text-xs font-medium text-slate-600 w-36">
        {isGlobal ? <span className="text-slate-400 italic">Global Default</span> : bd.branch_name}
        {bd.is_override && !isGlobal && <span className="ml-1 text-[9px] text-blue-500 font-normal">(override)</span>}
      </td>

      {/* Capital column */}
      <td className="py-1.5 px-2 text-xs text-slate-500 w-28">
        {isCapitalTab ? (
          <Input
            type="number"
            value={pendingCap !== undefined ? pendingCap : (bd.capital || '')}
            onChange={e => onCapitalChange(bd.branch_id, e.target.value)}
            placeholder="0.00"
            className="h-7 text-xs w-24 px-2"
            step="0.01"
            min="0"
          />
        ) : (
          <span className={!displayCap ? 'text-slate-300' : ''}>
            {displayCap ? formatPHP(displayCap) : '—'}
          </span>
        )}
      </td>

      {/* Method badge (capital tab only) */}
      {isCapitalTab && (
        <td className="py-1.5 px-2 w-24">
          <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium
            ${bd.capital_method === 'manual' ? 'bg-purple-100 text-purple-700' :
              bd.capital_method === 'last_purchase' ? 'bg-blue-100 text-blue-700' :
              bd.capital_method === 'moving_average' ? 'bg-teal-100 text-teal-700' : 'bg-slate-100 text-slate-500'}`}>
            {bd.capital_method || 'manual'}
          </span>
        </td>
      )}

      {/* Price scheme columns */}
      {schemeKeys.map(key => {
        const currentPrice = parseFloat(bd.prices?.[key]) || 0;
        const pending = pendingPrices[key];
        const displayVal = pending !== undefined ? pending : '';
        const cap = parseFloat(displayCap) || 0;
        const s = priceStatus(pending !== undefined ? pending : currentPrice, cap);
        return (
          <td key={key} className="py-1.5 px-2">
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-slate-300 w-14 text-right tabular-nums">
                {currentPrice > 0 ? formatPHP(currentPrice) : '—'}
              </span>
              <span className="text-slate-200">→</span>
              <div className="relative">
                <PriceInput
                  value={displayVal}
                  capital={cap}
                  onChange={v => onChange(bd.branch_id, key, v)}
                  placeholder={currentPrice > 0 ? String(currentPrice) : '0.00'}
                />
                {s !== 'ok' && s !== 'no_price' && pending !== undefined && (
                  <StatusDot status={s} size="sm" />
                )}
              </div>
            </div>
          </td>
        );
      })}
    </tr>
  );
}

// ── Repack Row ────────────────────────────────────────────────────────────────

function RepackRow({ repack, parentEntry, schemeKeys, isCapitalTab, onChange }) {
  const pendingPrices = parentEntry.pending?.[`__repack__${repack.id}`]?.prices || {};
  return (
    <tr className="border-t border-dashed border-slate-100 bg-slate-50/30">
      <td className="py-1 px-3 text-[10px] text-slate-400 pl-6">
        ↳ {repack.name} <span className="text-slate-300">({repack.units_per_parent}x repack)</span>
      </td>
      <td className="py-1 px-2 text-[10px] text-slate-400">
        {repack.derived_capital ? formatPHP(repack.derived_capital) : '—'}
        <span className="text-slate-300 ml-1">derived</span>
      </td>
      {isCapitalTab && <td />}
      {schemeKeys.map(key => {
        const currentPrice = parseFloat(repack.prices?.[key]) || 0;
        const pending = pendingPrices[key];
        const cap = repack.derived_capital || 0;
        return (
          <td key={key} className="py-1 px-2">
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-slate-300 w-14 text-right tabular-nums">
                {currentPrice > 0 ? formatPHP(currentPrice) : '—'}
              </span>
              <span className="text-slate-200">→</span>
              <PriceInput
                value={pending !== undefined ? pending : ''}
                capital={cap}
                onChange={v => onChange(`__repack__${repack.id}`, key, v)}
                placeholder={currentPrice > 0 ? String(currentPrice) : '0.00'}
              />
            </div>
          </td>
        );
      })}
    </tr>
  );
}

// ── Product Card ──────────────────────────────────────────────────────────────

function ProductCard({ entry, schemeKeys, isCapitalTab, onUpdate, onRemove, onToggleReviewed, isExpanded, onToggleExpand }) {
  const cardStatus = productCardStatus(entry, schemeKeys);
  const reviewed = entry.reviewed;

  const borderColor =
    reviewed ? 'border-emerald-300 bg-emerald-50/20' :
    cardStatus === 'error' ? 'border-red-200 bg-red-50/10' :
    cardStatus === 'warning' ? 'border-amber-200 bg-amber-50/10' :
    cardStatus === 'no_capital' ? 'border-slate-200' :
    'border-emerald-100';

  const handlePriceChange = (branchId, schemeKey, value) => {
    onUpdate(entry.id, branchId, { prices: { [schemeKey]: value === '' ? undefined : parseFloat(value) } });
  };
  const handleCapitalChange = (branchId, value) => {
    onUpdate(entry.id, branchId, { capital: value === '' ? undefined : parseFloat(value) });
  };

  return (
    <Card className={`border ${borderColor} transition-colors`} data-testid={`product-card-${entry.id}`}>
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none"
        onClick={onToggleExpand}
      >
        <StatusDot status={reviewed ? 'ok' : cardStatus} size="sm" />
        {isExpanded ? <ChevronDown size={14} className="text-slate-400 shrink-0" /> : <ChevronRight size={14} className="text-slate-400 shrink-0" />}
        <div className="flex-1 min-w-0">
          <span className="text-sm font-semibold text-slate-800">{entry.name}</span>
          <span className="ml-2 text-xs text-slate-400">{entry.sku}</span>
          {entry.category && <span className="ml-2 text-[10px] bg-slate-100 text-slate-500 px-1.5 rounded">{entry.category}</span>}
          {entry.repacks?.length > 0 && (
            <span className="ml-2 text-[10px] text-slate-400">{entry.repacks.length} repack{entry.repacks.length > 1 ? 's' : ''}</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {reviewed && (
            <span className="flex items-center gap-1 text-[10px] text-emerald-600 font-medium">
              <CheckCircle2 size={12} /> Reviewed
            </span>
          )}
          <Button
            variant="ghost" size="sm"
            className={`h-6 px-2 text-[10px] ${reviewed ? 'text-emerald-600' : 'text-slate-400'}`}
            onClick={e => { e.stopPropagation(); onToggleReviewed(entry.id); }}
            data-testid={`review-btn-${entry.id}`}
          >
            <CheckCircle2 size={12} className="mr-0.5" />
            {reviewed ? 'Approved' : 'Approve'}
          </Button>
          <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-slate-300 hover:text-red-500"
            onClick={e => { e.stopPropagation(); onRemove(entry.id); }}>
            <X size={13} />
          </Button>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-slate-100 overflow-x-auto">
          <table className="w-full text-xs min-w-max">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-100">
                <th className="text-left py-1.5 px-3 font-medium text-slate-500 w-36">Branch</th>
                <th className="text-left py-1.5 px-2 font-medium text-slate-500 w-28">Capital</th>
                {isCapitalTab && <th className="text-left py-1.5 px-2 font-medium text-slate-500 w-24">Method</th>}
                {schemeKeys.map(k => (
                  <th key={k} className="text-left py-1.5 px-2 font-medium text-slate-500 capitalize">
                    {k} <span className="text-slate-300 font-normal">(cur → new)</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entry.branchData?.map(bd => (
                <BranchRow
                  key={bd.branch_id}
                  bd={bd}
                  entry={entry}
                  schemeKeys={schemeKeys}
                  isCapitalTab={isCapitalTab}
                  onChange={handlePriceChange}
                  onCapitalChange={handleCapitalChange}
                />
              ))}
              {entry.repacks?.map(rp => (
                <RepackRow
                  key={rp.id}
                  repack={rp}
                  parentEntry={entry}
                  schemeKeys={schemeKeys}
                  isCapitalTab={isCapitalTab}
                  onChange={handlePriceChange}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ── Smart Category Fill Bar ───────────────────────────────────────────────────

function SmartFillBar({ categories, schemeKeys, onApply }) {
  const [cat, setCat] = useState('');
  const [scheme, setScheme] = useState(schemeKeys[0] || '');
  const [basis, setBasis] = useState('capital');
  const [amount, setAmount] = useState('');
  const [mode, setMode] = useState('flat'); // 'flat' | 'percent'

  return (
    <Card className="border-slate-200 bg-slate-50/50">
      <CardHeader className="py-2 px-4">
        <CardTitle className="text-xs font-semibold text-slate-600 flex items-center gap-1.5">
          <Zap size={13} className="text-amber-500" /> Smart Category Fill
        </CardTitle>
      </CardHeader>
      <CardContent className="py-2 px-4">
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <Label className="text-[10px] text-slate-400">Category</Label>
            <Select value={cat} onValueChange={setCat}>
              <SelectTrigger className="h-7 text-xs w-36 mt-0.5"><SelectValue placeholder="All" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All Categories</SelectItem>
                {categories.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[10px] text-slate-400">Scheme</Label>
            <Select value={scheme} onValueChange={setScheme}>
              <SelectTrigger className="h-7 text-xs w-28 mt-0.5"><SelectValue /></SelectTrigger>
              <SelectContent>
                {schemeKeys.map(k => <SelectItem key={k} value={k}>{k}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[10px] text-slate-400">Based on</Label>
            <Select value={basis} onValueChange={setBasis}>
              <SelectTrigger className="h-7 text-xs w-28 mt-0.5"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="capital">Capital</SelectItem>
                {schemeKeys.map(k => <SelectItem key={k} value={k}>{k} price</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[10px] text-slate-400">Mode</Label>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger className="h-7 text-xs w-24 mt-0.5"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="flat">+ ₱ flat</SelectItem>
                <SelectItem value="percent">+ % markup</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[10px] text-slate-400">{mode === 'flat' ? 'Amount (₱)' : 'Percent (%)'}</Label>
            <Input type="number" value={amount} onChange={e => setAmount(e.target.value)}
              className="h-7 text-xs w-20 mt-0.5" placeholder="0" min="0" step="0.5" />
          </div>
          <Button size="sm" className="h-7 text-xs bg-amber-500 hover:bg-amber-600 text-white"
            onClick={() => { if (!amount || !scheme) return; onApply({ category: cat || '__all__', scheme, basis, amount: parseFloat(amount), mode }); }}
            data-testid="smart-fill-apply-btn">
            <Zap size={11} className="mr-1" /> Pre-fill Suggestions
          </Button>
        </div>
        <p className="text-[10px] text-slate-400 mt-1.5">Suggestions are pre-filled only — you can still edit or ignore them before saving.</p>
      </CardContent>
    </Card>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function PriceManagerPage() {
  const { branches } = useAuth();
  const [activeTab, setActiveTab] = useState('prices');
  const [schemes, setSchemes] = useState([]);
  const [selectedSchemes, setSelectedSchemes] = useState([]);
  const [categories, setCategories] = useState([]);
  const [auditSummary, setAuditSummary] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);

  // Working product list (shared between tabs, but pending changes are separate)
  const [products, setProducts] = useState([]);  // [{id, name, sku, category, branchData, repacks, pending, reviewed}]
  const [expandedSet, setExpandedSet] = useState(new Set());
  const [reviewedSet, setReviewedSet] = useState(new Set());

  // Search
  const [searchQ, setSearchQ] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const searchRef = useRef(null);

  const [saving, setSaving] = useState(false);

  // ── Load schemes & categories ──────────────────────────────────────────────
  useEffect(() => {
    Promise.all([
      api.get('/price-schemes'),
      api.get('/products/categories'),
    ]).then(([sr, cr]) => {
      const s = sr.data || [];
      setSchemes(s);
      setSelectedSchemes(s.slice(0, 3).map(x => x.key));
      setCategories(cr.data || []);
    }).catch(() => toast.error('Failed to load schemes'));
  }, []);

  // ── Load audit summary ─────────────────────────────────────────────────────
  const loadAuditSummary = useCallback(async () => {
    setAuditLoading(true);
    try {
      const res = await api.get('/products/price-audit-summary');
      setAuditSummary(res.data);
    } catch { toast.error('Failed to load audit summary'); }
    setAuditLoading(false);
  }, []);

  useEffect(() => { if (activeTab === 'capital') loadAuditSummary(); }, [activeTab, loadAuditSummary]);

  // ── Build branch data for a product ───────────────────────────────────────
  const buildProductEntry = useCallback(async (product) => {
    // Load all branch overrides for this product
    const bpRes = await api.get('/branch-prices', { params: { product_id: product.id } });
    const overrides = bpRes.data || [];
    const overrideMap = {};
    overrides.forEach(o => { overrideMap[o.branch_id] = o; });

    // Build global row
    const globalRow = {
      branch_id: '__global__',
      branch_name: 'Global Default',
      capital: parseFloat(product.cost_price) || 0,
      capital_method: product.capital_method || 'manual',
      prices: { ...(product.prices || {}) },
      is_override: false,
    };

    // Build per-branch rows
    const branchRows = (branches || []).map(b => {
      const ov = overrideMap[b.id];
      return {
        branch_id: b.id,
        branch_name: b.name,
        capital: ov?.cost_price != null ? parseFloat(ov.cost_price) : parseFloat(product.cost_price) || 0,
        capital_method: ov ? 'branch_override' : (product.capital_method || 'manual'),
        prices: ov?.prices ? { ...(product.prices || {}), ...ov.prices } : { ...(product.prices || {}) },
        is_override: !!ov,
      };
    });

    // Repacks
    let repacks = [];
    try {
      const rpRes = await api.get(`/products/${product.id}/repacks`);
      repacks = (rpRes.data || []).map(rp => ({
        id: rp.id,
        name: rp.name,
        units_per_parent: rp.units_per_parent || 1,
        prices: rp.prices || {},
        derived_capital: product.cost_price > 0
          ? Math.round((parseFloat(product.cost_price) / Math.max(rp.units_per_parent || 1, 1)) * 100) / 100
          : 0,
      }));
    } catch { /* no repacks */ }

    return {
      id: product.id,
      name: product.name,
      sku: product.sku || '',
      category: product.category || '',
      branchData: [globalRow, ...branchRows],
      repacks,
      pending: {},
      reviewed: false,
    };
  }, [branches]);

  // ── Search products ────────────────────────────────────────────────────────
  const doSearch = useCallback(async () => {
    if (!searchQ.trim() || searchQ.length < 2) { setSearchResults([]); return; }
    setSearching(true);
    try {
      const res = await api.get('/products', { params: { search: searchQ, is_repack: false } });
      const existing = new Set(products.map(p => p.id));
      const productList = Array.isArray(res.data) ? res.data : (res.data?.products || []);
      setSearchResults(productList.filter(p => !p.is_repack && !existing.has(p.id)).slice(0, 15));
    } catch { toast.error('Search failed'); }
    setSearching(false);
  }, [searchQ, products]);

  useEffect(() => {
    const t = setTimeout(doSearch, 300);
    return () => clearTimeout(t);
  }, [doSearch]);

  const addProduct = useCallback(async (product) => {
    if (products.find(p => p.id === product.id)) { toast.info('Already in list'); return; }
    try {
      const entry = await buildProductEntry(product);
      setProducts(prev => [...prev, entry]);
      setExpandedSet(prev => new Set([...prev, product.id]));
      setSearchResults([]);
      setSearchQ('');
    } catch { toast.error('Failed to load product data'); }
  }, [products, buildProductEntry]);

  // ── Load audit group (missing capital or low margin) ──────────────────────
  const loadAuditGroup = async (group) => {
    const ids = auditSummary?.[group]?.products?.slice(0, 30) || [];
    if (!ids.length) { toast.info('No products in this group'); return; }
    let added = 0;
    for (const p of ids) {
      if (products.find(x => x.id === p.id)) continue;
      try {
        const fullRes = await api.get(`/products/${p.id}`);
        const entry = await buildProductEntry(fullRes.data);
        setProducts(prev => [...prev, entry]);
        added++;
      } catch { /* skip */ }
    }
    toast.success(`Loaded ${added} products`);
  };

  // ── Pending change handler ─────────────────────────────────────────────────
  const handleUpdate = useCallback((productId, branchId, changes) => {
    setProducts(prev => prev.map(p => {
      if (p.id !== productId) return p;
      const branchPending = p.pending[branchId] || {};
      const newBranchPending = {
        ...branchPending,
        ...(changes.capital !== undefined ? { capital: changes.capital } : {}),
        prices: {
          ...(branchPending.prices || {}),
          ...(changes.prices || {}),
        },
      };
      // Clean undefined
      Object.keys(newBranchPending.prices).forEach(k => {
        if (newBranchPending.prices[k] === undefined) delete newBranchPending.prices[k];
      });
      return { ...p, pending: { ...p.pending, [branchId]: newBranchPending } };
    }));
  }, []);

  const toggleReviewed = useCallback((productId) => {
    setProducts(prev => prev.map(p => p.id === productId ? { ...p, reviewed: !p.reviewed } : p));
    setReviewedSet(prev => {
      const next = new Set(prev);
      if (next.has(productId)) next.delete(productId); else next.add(productId);
      return next;
    });
  }, []);

  const removeProduct = useCallback((productId) => {
    setProducts(prev => prev.filter(p => p.id !== productId));
    setExpandedSet(prev => { const s = new Set(prev); s.delete(productId); return s; });
  }, []);

  const toggleExpand = useCallback((productId) => {
    setExpandedSet(prev => {
      const s = new Set(prev);
      if (s.has(productId)) s.delete(productId); else s.add(productId);
      return s;
    });
  }, []);

  // ── Smart category fill ────────────────────────────────────────────────────
  const handleSmartFill = useCallback(({ category, scheme, basis, amount, mode }) => {
    setProducts(prev => prev.map(entry => {
      if (category !== '__all__' && entry.category !== category) return entry;
      const newPending = { ...entry.pending };
      for (const bd of entry.branchData || []) {
        const baseVal = basis === 'capital'
          ? parseFloat(bd.capital) || 0
          : parseFloat(bd.prices?.[basis]) || 0;
        if (baseVal <= 0) continue;
        const suggestion = mode === 'percent'
          ? Math.round((baseVal * (1 + amount / 100)) * 100) / 100
          : Math.round((baseVal + amount) * 100) / 100;
        newPending[bd.branch_id] = {
          ...(newPending[bd.branch_id] || {}),
          prices: { ...((newPending[bd.branch_id] || {}).prices || {}), [scheme]: suggestion },
        };
      }
      return { ...entry, pending: newPending };
    }));
    toast.success('Suggestions pre-filled — review before saving');
  }, []);

  // ── Save ──────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    // Collect all changes
    const globalItems = [];    // {product_id, prices}
    const branchItems = [];    // {product_id, branch_id, prices, cost_price, set_manual}
    const repackItems = [];    // {product_id, prices}

    for (const entry of products) {
      for (const [branchId, changes] of Object.entries(entry.pending || {})) {
        const hasPrice = changes.prices && Object.keys(changes.prices).length > 0;
        const hasCap = changes.capital !== undefined && changes.capital !== null;
        if (!hasPrice && !hasCap) continue;

        if (branchId === '__global__') {
          if (hasPrice) globalItems.push({ product_id: entry.id, prices: changes.prices });
          if (hasCap) {
            // Global capital → update product.cost_price + capital_method=manual
            await api.put(`/products/${entry.id}`, { cost_price: changes.capital, capital_method: 'manual' }).catch(() => {});
          }
        } else if (branchId.startsWith('__repack__')) {
          const repackId = branchId.replace('__repack__', '');
          if (hasPrice) repackItems.push({ product_id: repackId, prices: changes.prices });
        } else {
          branchItems.push({
            product_id: entry.id,
            branch_id: branchId,
            prices: hasPrice ? changes.prices : undefined,
            cost_price: hasCap ? changes.capital : undefined,
            set_manual: hasCap,
          });
        }
      }
    }

    if (!globalItems.length && !branchItems.length && !repackItems.length) {
      toast.info('No changes to save');
      return;
    }

    setSaving(true);
    try {
      const calls = [];
      if (globalItems.length) calls.push(api.post('/products/bulk-price-update', { items: globalItems }));
      if (repackItems.length) calls.push(api.post('/products/bulk-price-update', { items: repackItems }));
      if (branchItems.length) calls.push(api.post('/branch-prices/bulk-update', { items: branchItems }));
      await Promise.all(calls);

      const total = globalItems.length + branchItems.length + repackItems.length;
      toast.success(`Saved ${total} price update${total !== 1 ? 's' : ''}`);

      // Clear pending & mark as reviewed
      setProducts(prev => prev.map(p => ({ ...p, pending: {}, reviewed: true })));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
    }
    setSaving(false);
  };

  // ── Pending count ─────────────────────────────────────────────────────────
  const pendingCount = products.reduce((acc, p) => {
    return acc + Object.values(p.pending || {}).filter(v =>
      (v.prices && Object.keys(v.prices).length > 0) || v.capital !== undefined
    ).length;
  }, 0);

  const schemeKeys = selectedSchemes;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5 animate-fadeIn" data-testid="price-manager-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Price Manager</h1>
        <p className="text-sm text-slate-500 mt-1">Bulk-edit selling prices and capital across branches</p>
      </div>

      <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v); setProducts([]); setExpandedSet(new Set()); }}>
        <TabsList>
          <TabsTrigger value="prices" data-testid="pm-tab-prices">Price Manager</TabsTrigger>
          <TabsTrigger value="capital" data-testid="pm-tab-capital">Capital &amp; Price Setup</TabsTrigger>
        </TabsList>

        {/* ── Shared Controls (Scheme Selector + Search) ─────────────────── */}
        <div className="mt-4 space-y-3">
          {/* Scheme Selector */}
          <Card className="border-slate-200">
            <CardContent className="py-3 px-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xs font-medium text-slate-600">Show price schemes:</span>
                {schemes.map(s => (
                  <label key={s.key} className="flex items-center gap-1.5 cursor-pointer">
                    <Checkbox
                      checked={selectedSchemes.includes(s.key)}
                      onCheckedChange={checked => {
                        setSelectedSchemes(prev =>
                          checked ? [...prev, s.key] : prev.filter(k => k !== s.key)
                        );
                      }}
                      data-testid={`scheme-check-${s.key}`}
                    />
                    <span className="text-xs capitalize">{s.name || s.key}</span>
                  </label>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Product Search */}
          <div className="relative" ref={searchRef}>
            <div className="flex items-center gap-2">
              <div className="relative flex-1 max-w-sm">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  data-testid="pm-product-search"
                  value={searchQ}
                  onChange={e => setSearchQ(e.target.value)}
                  placeholder="Search product to add…"
                  className="pl-8 h-8 text-sm"
                />
                {searching && <RefreshCw size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 animate-spin" />}
              </div>
              {products.length > 0 && (
                <span className="text-xs text-slate-400">{products.length} product{products.length !== 1 ? 's' : ''} in list</span>
              )}
            </div>
            {searchResults.length > 0 && (
              <Card className="absolute top-9 left-0 z-50 w-80 max-h-60 overflow-y-auto shadow-lg border">
                <CardContent className="p-1">
                  {searchResults.map(p => (
                    <button key={p.id} onClick={() => addProduct(p)}
                      className="w-full text-left flex items-center gap-2 px-3 py-2 hover:bg-slate-50 rounded text-sm"
                      data-testid={`search-result-${p.id}`}>
                      <Plus size={13} className="text-slate-400 shrink-0" />
                      <div>
                        <div className="font-medium text-slate-800">{p.name}</div>
                        <div className="text-xs text-slate-400">{p.sku} · {p.category}</div>
                      </div>
                    </button>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        {/* ── Tab: Price Manager ────────────────────────────────────────────── */}
        <TabsContent value="prices" className="space-y-3 mt-4">
          {schemeKeys.length > 0 && (
            <SmartFillBar categories={categories} schemeKeys={schemeKeys} onApply={handleSmartFill} />
          )}

          {products.length === 0 ? (
            <Card className="border-dashed border-slate-200">
              <CardContent className="py-12 flex flex-col items-center gap-3 text-slate-400">
                <TrendingUp size={36} className="text-slate-200" />
                <p className="text-sm">Search for products above to start editing prices</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2">
              {products.map(entry => (
                <ProductCard
                  key={entry.id}
                  entry={entry}
                  schemeKeys={schemeKeys}
                  isCapitalTab={false}
                  onUpdate={handleUpdate}
                  onRemove={removeProduct}
                  onToggleReviewed={toggleReviewed}
                  isExpanded={expandedSet.has(entry.id)}
                  onToggleExpand={() => toggleExpand(entry.id)}
                />
              ))}
            </div>
          )}
        </TabsContent>

        {/* ── Tab: Capital & Price Setup ────────────────────────────────────── */}
        <TabsContent value="capital" className="space-y-3 mt-4">
          {/* Smart Prompts */}
          {auditSummary && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Card className={`border ${auditSummary.missing_capital.count > 0 ? 'border-slate-300 bg-slate-50' : 'border-slate-100'}`}>
                <CardContent className="py-3 px-4 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
                      <Package size={14} className="text-slate-400" />
                      {auditSummary.missing_capital.count > 0
                        ? `${auditSummary.missing_capital.count} products have no capital`
                        : 'All products have capital set'}
                    </p>
                    <p className="text-xs text-slate-400 mt-0.5">Products with ₱0 cost price</p>
                  </div>
                  {auditSummary.missing_capital.count > 0 && (
                    <Button size="sm" variant="outline" className="shrink-0 text-xs h-7"
                      onClick={() => loadAuditGroup('missing_capital')}
                      data-testid="load-missing-capital-btn">
                      Load first 30
                    </Button>
                  )}
                </CardContent>
              </Card>

              <Card className={`border ${auditSummary.low_margin.count > 0 ? 'border-amber-200 bg-amber-50/30' : 'border-slate-100'}`}>
                <CardContent className="py-3 px-4 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
                      <AlertTriangle size={14} className={auditSummary.low_margin.count > 0 ? 'text-amber-500' : 'text-slate-400'} />
                      {auditSummary.low_margin.count > 0
                        ? `${auditSummary.low_margin.count} products need price review`
                        : 'No low-margin issues found'}
                    </p>
                    <p className="text-xs text-slate-400 mt-0.5">Markup &lt; ₱20 and &lt; 5% margin</p>
                  </div>
                  {auditSummary.low_margin.count > 0 && (
                    <Button size="sm" variant="outline" className="shrink-0 text-xs h-7 border-amber-300 text-amber-700"
                      onClick={() => loadAuditGroup('low_margin')}
                      data-testid="load-low-margin-btn">
                      Load first 30
                    </Button>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
          {auditLoading && (
            <div className="flex items-center gap-2 text-xs text-slate-400 py-2">
              <RefreshCw size={13} className="animate-spin" /> Analyzing products…
            </div>
          )}

          {products.length === 0 ? (
            <Card className="border-dashed border-slate-200">
              <CardContent className="py-12 flex flex-col items-center gap-3 text-slate-400">
                <Package size={36} className="text-slate-200" />
                <p className="text-sm">Load from audit above or search for a product to set capital & prices</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2">
              {products.map(entry => (
                <ProductCard
                  key={entry.id}
                  entry={entry}
                  schemeKeys={schemeKeys}
                  isCapitalTab={true}
                  onUpdate={handleUpdate}
                  onRemove={removeProduct}
                  onToggleReviewed={toggleReviewed}
                  isExpanded={expandedSet.has(entry.id)}
                  onToggleExpand={() => toggleExpand(entry.id)}
                />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ── Sticky Save Bar ───────────────────────────────────────────────── */}
      {products.length > 0 && (
        <div className="sticky bottom-4 flex justify-end gap-3 pt-2">
          <div className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-2.5 shadow-lg">
            {pendingCount > 0 && (
              <span className="text-xs text-slate-500">
                <span className="font-semibold text-slate-800">{pendingCount}</span> unsaved change{pendingCount !== 1 ? 's' : ''}
              </span>
            )}
            <Button
              data-testid="pm-save-btn"
              onClick={handleSave}
              disabled={saving || pendingCount === 0}
              className="bg-[#1A4D2E] hover:bg-[#14532d] text-white h-8 text-sm"
            >
              {saving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Save size={13} className="mr-1.5" />}
              {saving ? 'Saving…' : 'Save All Changes'}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
