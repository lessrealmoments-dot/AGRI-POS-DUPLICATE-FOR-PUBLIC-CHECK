import { useState, useEffect, useRef, useCallback } from 'react';
import { api, hashPinOffline } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import {
  Search, Plus, Minus, Trash2, ShoppingCart, Lock, Eye, EyeOff,
  Package, X, Calculator, ScanBarcode, ArrowRight, Check, AlertCircle
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from './CalcInput';

const STORAGE_KEY_LIST = 'kiosk_order_list';
const STORAGE_KEY_BUDGET = 'kiosk_budget';

export default function BudgetChecker({ onUnlock, branchId }) {
  // ── State ─────────────────────────────────────────────────────────
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [orderList, setOrderList] = useState(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY_LIST) || '[]'); } catch { return []; }
  });
  const [budget, setBudget] = useState(() => {
    try { return parseFloat(localStorage.getItem(STORAGE_KEY_BUDGET)) || 0; } catch { return 0; }
  });
  const [budgetInput, setBudgetInput] = useState(() => {
    const v = parseFloat(localStorage.getItem(STORAGE_KEY_BUDGET)) || 0;
    return v > 0 ? v.toString() : '';
  });

  // Cost reveal
  const [costRevealed, setCostRevealed] = useState(false);
  const [showCostPin, setShowCostPin] = useState(false);
  const [costPin, setCostPin] = useState('');
  const [costPinError, setCostPinError] = useState('');

  // Unlock
  const [showUnlock, setShowUnlock] = useState(false);
  const [unlockPin, setUnlockPin] = useState('');
  const [unlockError, setUnlockError] = useState('');
  const [unlockLoading, setUnlockLoading] = useState(false);

  const searchRef = useRef(null);
  const debounceRef = useRef(null);

  // ── Persist ───────────────────────────────────────────────────────
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_LIST, JSON.stringify(orderList));
  }, [orderList]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_BUDGET, budget.toString());
  }, [budget]);

  // ── Keyboard: Ctrl+Shift+U → unlock ──────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'U' || e.key === 'u')) {
        e.preventDefault();
        setShowUnlock(true);
        setTimeout(() => document.getElementById('kiosk-unlock-pin')?.focus(), 100);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // ── Auto-focus search on mount ────────────────────────────────────
  useEffect(() => {
    setTimeout(() => searchRef.current?.focus(), 300);
  }, []);

  // ── Search ────────────────────────────────────────────────────────
  const doSearch = useCallback(async (q) => {
    if (!q || q.length < 1) { setResults([]); return; }
    setSearching(true);
    try {
      const res = await api.get('/products/search-detail', {
        params: { q, branch_id: branchId || '' }
      });
      setResults(res.data || []);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }, [branchId]);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (!query) { setResults([]); return; }
    debounceRef.current = setTimeout(() => doSearch(query), 250);
    return () => clearTimeout(debounceRef.current);
  }, [query, doSearch]);

  // ── Barcode: Enter → exact lookup → auto-add ─────────────────────
  const handleSearchKeyDown = async (e) => {
    if (e.key === 'Enter' && query.trim()) {
      e.preventDefault();
      // Try exact barcode lookup first
      try {
        const res = await api.get(`/products/barcode-lookup/${encodeURIComponent(query.trim())}`, {
          params: { branch_id: branchId || '' }
        });
        if (res.data?.id) {
          addToList(res.data);
          setQuery('');
          setResults([]);
          searchRef.current?.focus();
          return;
        }
      } catch { /* not a barcode, fall through */ }
      // If results exist, add the first one
      if (results.length > 0) {
        addToList(results[0]);
        setQuery('');
        setResults([]);
        searchRef.current?.focus();
      }
    }
    if (e.key === 'Escape') {
      setQuery('');
      setResults([]);
    }
  };

  // ── Order List ────────────────────────────────────────────────────
  const addToList = (product) => {
    const price = product.prices?.retail || 0;
    setOrderList(prev => {
      const idx = prev.findIndex(i => i.id === product.id);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = { ...updated[idx], quantity: updated[idx].quantity + 1 };
        return updated;
      }
      return [...prev, {
        id: product.id,
        name: product.name,
        sku: product.sku || '',
        unit: product.unit || '',
        price,
        cost_price: product.cost_price || 0,
        available: product.available || 0,
        quantity: 1,
      }];
    });
  };

  const updateQty = (id, delta) => {
    setOrderList(prev => prev.map(item =>
      item.id === id ? { ...item, quantity: Math.max(1, item.quantity + delta) } : item
    ));
  };

  const setQty = (id, val) => {
    const n = parseInt(val) || 1;
    setOrderList(prev => prev.map(item =>
      item.id === id ? { ...item, quantity: Math.max(1, n) } : item
    ));
  };

  const removeItem = (id) => {
    setOrderList(prev => prev.filter(item => item.id !== id));
  };

  const clearList = () => {
    setOrderList([]);
    setBudget(0);
    setBudgetInput('');
  };

  // ── Budget ────────────────────────────────────────────────────────
  const handleBudgetChange = (val) => {
    setBudgetInput(val);
    const n = parseFloat(val) || 0;
    setBudget(n);
  };

  // ── Computed ──────────────────────────────────────────────────────
  const grandTotal = orderList.reduce((sum, item) => sum + item.quantity * item.price, 0);
  const totalItems = orderList.reduce((sum, item) => sum + item.quantity, 0);
  const remaining = budget - grandTotal;
  const hasBudget = budget > 0;

  // ── Cost Reveal ───────────────────────────────────────────────────
  const handleCostReveal = async () => {
    if (!costPin) return;
    setCostPinError('');
    try {
      const res = await api.post('/auth/verify-manager-pin', {
        pin: costPin, action_key: 'kiosk_cost_reveal'
      });
      if (res.data?.valid) {
        setCostRevealed(true);
        setShowCostPin(false);
        // Cache PIN hash for offline use
        hashPinOffline(costPin).then(h => localStorage.setItem('kiosk_pin_cache', h));
        setCostPin('');
      } else {
        setCostPinError('Invalid PIN');
      }
    } catch {
      setCostPinError('Invalid PIN');
    }
  };

  // ── Unlock ────────────────────────────────────────────────────────
  const handleUnlock = async () => {
    if (!unlockPin) return;
    setUnlockError('');
    setUnlockLoading(true);
    try {
      const res = await api.post('/auth/verify-manager-pin', {
        pin: unlockPin, action_key: 'kiosk_unlock'
      });
      if (res.data?.valid) {
        // Cache PIN hash for offline use
        hashPinOffline(unlockPin).then(h => localStorage.setItem('kiosk_pin_cache', h));
        setShowUnlock(false);
        setUnlockPin('');
        onUnlock();
      } else {
        setUnlockError('Invalid PIN or TOTP code');
      }
    } catch {
      setUnlockError('Verification failed');
    } finally {
      setUnlockLoading(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div
      className="fixed inset-0 z-[9999] flex flex-col overflow-hidden"
      style={{ background: 'linear-gradient(145deg, #022c22 0%, #064e3b 40%, #047857 100%)' }}
      data-testid="budget-checker-screen"
    >
      {/* ─── Header ──────────────────────────────────────────────── */}
      <header className="shrink-0 px-6 pt-6 pb-4 lg:px-10 lg:pt-8">
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <div className="w-10 h-10 rounded-xl bg-white/15 backdrop-blur flex items-center justify-center">
                <Calculator size={22} className="text-emerald-300" />
              </div>
              <h1
                className="text-2xl lg:text-3xl font-bold text-white tracking-tight"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                Price & Budget Checker
              </h1>
            </div>
            <p className="text-emerald-200/70 text-sm lg:text-base ml-[52px]">
              Check product price, quantity, and estimate your total before ordering.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!costRevealed ? (
              <button
                data-testid="kiosk-see-cost-btn"
                onClick={() => { setShowCostPin(true); setCostPin(''); setCostPinError(''); }}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-emerald-200 text-sm font-medium transition-all border border-white/10"
              >
                <EyeOff size={16} />
                <span className="hidden sm:inline">See Cost</span>
              </button>
            ) : (
              <button
                data-testid="kiosk-hide-cost-btn"
                onClick={() => setCostRevealed(false)}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-sm font-medium border border-amber-400/30 transition-all cursor-pointer"
              >
                <Eye size={16} />
                <span className="hidden sm:inline">Hide Cost</span>
              </button>
            )}
            {/* kiosk indicator removed */}
          </div>
        </div>

        {/* ─── Search Bar ────────────────────────────────────────── */}
        <div className="relative max-w-3xl">
          <div className="absolute left-4 top-1/2 -translate-y-1/2 flex items-center gap-2 text-emerald-400/60 pointer-events-none">
            <Search size={22} />
          </div>
          <input
            ref={searchRef}
            data-testid="kiosk-search-input"
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleSearchKeyDown}
            placeholder="Type product name or scan barcode..."
            className="w-full h-14 pl-14 pr-14 text-lg bg-white/95 backdrop-blur rounded-xl border-2 border-emerald-400/30 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-400/20 outline-none text-slate-800 placeholder-slate-400 shadow-lg transition-all"
            autoComplete="off"
          />
          {query && (
            <button
              onClick={() => { setQuery(''); setResults([]); searchRef.current?.focus(); }}
              className="absolute right-4 top-1/2 -translate-y-1/2 p-1 rounded-full hover:bg-slate-100 text-slate-400"
            >
              <X size={18} />
            </button>
          )}
          {searching && (
            <div className="absolute right-12 top-1/2 -translate-y-1/2">
              <div className="w-5 h-5 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
            </div>
          )}
        </div>
      </header>

      {/* ─── Main Content ────────────────────────────────────────── */}
      <main className="flex-1 flex gap-4 px-6 pb-4 lg:px-10 lg:pb-6 min-h-0">
        {/* Left: Search Results */}
        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold uppercase tracking-widest text-emerald-300/50">
              {results.length > 0 ? `${results.length} Products Found` : query ? 'Searching...' : 'Search Results'}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar">
            {results.length === 0 && !query && (
              <div className="flex flex-col items-center justify-center h-full text-center opacity-60">
                <ScanBarcode size={56} className="text-emerald-400/40 mb-4" />
                <p className="text-emerald-200/60 text-lg font-medium mb-1">Ready to scan</p>
                <p className="text-emerald-300/40 text-sm">Type a product name or scan a barcode to begin</p>
              </div>
            )}
            {results.length === 0 && query && !searching && (
              <div className="flex flex-col items-center justify-center h-48 text-center">
                <Package size={40} className="text-emerald-400/30 mb-3" />
                <p className="text-emerald-200/50 text-base">No products found for "{query}"</p>
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {results.map(p => {
                const inList = orderList.find(i => i.id === p.id);
                return (
                  <div
                    key={p.id}
                    data-testid={`kiosk-product-${p.id}`}
                    className="bg-white/95 backdrop-blur rounded-xl p-4 shadow-md border border-white/50 hover:shadow-lg hover:border-emerald-300/50 transition-all group"
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="min-w-0">
                        <h3 className="font-semibold text-slate-800 text-base truncate" title={p.name}>{p.name}</h3>
                        <span className="text-xs text-slate-400 font-mono">{p.sku}</span>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-xl font-bold text-emerald-700">{formatPHP(p.prices?.retail)}</div>
                        <span className="text-[11px] text-slate-400">per {p.unit || 'unit'}</span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between mt-3">
                      <div className="flex items-center gap-3 text-xs text-slate-500">
                        <span className={`font-medium ${(p.available || 0) <= 0 ? 'text-red-500' : 'text-slate-600'}`}>
                          Stock: {(p.available || 0).toFixed(0)} {p.unit || ''}
                        </span>
                        {costRevealed && (
                          <span className="text-amber-600 font-medium">
                            Cost: {formatPHP(p.cost_price)}
                          </span>
                        )}
                      </div>
                      <button
                        data-testid={`kiosk-add-${p.id}`}
                        onClick={() => addToList(p)}
                        className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
                          inList
                            ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200'
                            : 'bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm'
                        }`}
                      >
                        {inList ? (
                          <>
                            <Check size={16} />
                            <span>Added ({inList.quantity})</span>
                          </>
                        ) : (
                          <>
                            <Plus size={16} />
                            <span>Add</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right: Order List + Budget */}
        <div className="w-[380px] lg:w-[440px] flex flex-col min-h-0 shrink-0">
          <div className="flex-1 flex flex-col bg-white/95 backdrop-blur rounded-2xl shadow-xl border border-white/50 overflow-hidden">
            {/* Order Header */}
            <div className="shrink-0 px-5 py-4 border-b border-slate-100 bg-slate-50/80">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <ShoppingCart size={18} className="text-emerald-600" />
                  <span className="font-semibold text-slate-800" style={{ fontFamily: 'Manrope' }}>
                    Your List
                  </span>
                  {orderList.length > 0 && (
                    <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">
                      {totalItems} item{totalItems !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
                {orderList.length > 0 && (
                  <button
                    data-testid="kiosk-clear-list"
                    onClick={clearList}
                    className="text-xs text-red-500 hover:text-red-700 font-medium flex items-center gap-1 transition-colors"
                  >
                    <Trash2 size={13} />
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Order Items */}
            <div className="flex-1 overflow-y-auto px-3 py-2 custom-scrollbar">
              {orderList.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center py-12 opacity-60">
                  <ShoppingCart size={36} className="text-slate-300 mb-3" />
                  <p className="text-slate-400 text-sm">Your list is empty</p>
                  <p className="text-slate-300 text-xs mt-1">Search & add products to estimate your budget</p>
                </div>
              ) : (
                <div className="space-y-1">
                  {orderList.map((item, idx) => (
                    <div
                      key={item.id}
                      data-testid={`kiosk-item-${item.id}`}
                      className="flex items-center gap-2 px-3 py-2.5 rounded-lg hover:bg-slate-50 group transition-colors"
                    >
                      <span className="text-xs text-slate-300 w-5 shrink-0 font-mono">{idx + 1}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-800 truncate">{item.name}</p>
                        <div className="flex items-center gap-2 text-xs text-slate-400">
                          <span>{formatPHP(item.price)}</span>
                          {costRevealed && (
                            <span className="text-amber-500">C: {formatPHP(item.cost_price)}</span>
                          )}
                        </div>
                      </div>
                      {/* Qty controls */}
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          data-testid={`kiosk-qty-minus-${item.id}`}
                          onClick={() => updateQty(item.id, -1)}
                          className="w-7 h-7 rounded-md bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 transition-colors"
                        >
                          <Minus size={14} />
                        </button>
                        <CalcInput data-testid={`kiosk-qty-input-${item.id}`}
 value={item.quantity}
 onChange={(v) => setQty(item.id, v)}
 className="w-10 h-7 text-center text-sm font-semibold border border-slate-200 rounded-md bg-white focus:ring-1 focus:ring-emerald-400 outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
                        <button
                          data-testid={`kiosk-qty-plus-${item.id}`}
                          onClick={() => updateQty(item.id, 1)}
                          className="w-7 h-7 rounded-md bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 transition-colors"
                        >
                          <Plus size={14} />
                        </button>
                      </div>
                      {/* Subtotal */}
                      <span className="text-sm font-semibold text-slate-700 w-24 text-right shrink-0">
                        {formatPHP(item.price * item.quantity)}
                      </span>
                      {/* Remove */}
                      <button
                        data-testid={`kiosk-remove-${item.id}`}
                        onClick={() => removeItem(item.id)}
                        className="w-7 h-7 rounded-md hover:bg-red-50 flex items-center justify-center text-slate-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-all shrink-0"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* ─── Totals + Budget ──────────────────────────────── */}
            <div className="shrink-0 border-t border-slate-100">
              {/* Grand Total */}
              <div className="px-5 py-4 bg-emerald-50/80">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-500">Grand Total</span>
                  <span
                    className="text-2xl font-bold text-emerald-800 tracking-tight"
                    data-testid="kiosk-grand-total"
                    style={{ fontFamily: 'Manrope' }}
                  >
                    {formatPHP(grandTotal)}
                  </span>
                </div>
              </div>

              {/* Budget Check */}
              <div className="px-5 py-4 bg-white border-t border-slate-100">
                <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 block">
                  Your Budget
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-medium">₱</span>
                  <CalcInput data-testid="kiosk-budget-input"
 value={budgetInput}
 onChange={(v) => handleBudgetChange(v)}
 placeholder="Enter your budget..."
 className="w-full h-11 pl-8 pr-4 text-base font-semibold border-2 border-slate-200 rounded-xl focus:border-emerald-500 focus:ring-2 focus:ring-emerald-200 outline-none transition-all bg-white text-slate-800 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
                </div>
                {hasBudget && (
                  <div className={`mt-3 flex items-center justify-between px-3 py-2.5 rounded-lg ${
                    remaining >= 0
                      ? 'bg-emerald-50 border border-emerald-200'
                      : 'bg-red-50 border border-red-200'
                  }`}>
                    <span className={`text-sm font-medium ${remaining >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                      {remaining >= 0 ? 'Remaining' : 'Over Budget'}
                    </span>
                    <span
                      className={`text-lg font-bold ${remaining >= 0 ? 'text-emerald-700' : 'text-red-700'}`}
                      data-testid="kiosk-remaining"
                    >
                      {remaining >= 0 ? formatPHP(remaining) : `-${formatPHP(Math.abs(remaining))}`}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* ─── Footer ──────────────────────────────────────────────── */}
      <footer className="shrink-0 px-6 py-3 lg:px-10 flex items-center justify-between">
        <div className="flex items-center gap-4 text-emerald-400/40 text-xs">
          <span className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 rounded bg-white/10 text-emerald-300/60 text-[10px] font-mono">Enter</kbd>
            <span>Quick add</span>
          </span>
          <span className="flex items-center gap-1.5">
            <kbd className="px-1.5 py-0.5 rounded bg-white/10 text-emerald-300/60 text-[10px] font-mono">Esc</kbd>
            <span>Clear search</span>
          </span>
        </div>
        <div className="flex items-center gap-2 text-emerald-400/30 text-xs">
          <span>Powered by</span>
          <span className="font-semibold text-emerald-300/50" style={{ fontFamily: 'Manrope' }}>AgriBooks</span>
        </div>
      </footer>

      {/* ─── Cost PIN Dialog ─────────────────────────────────────── */}
      {showCostPin && (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
            data-testid="kiosk-cost-pin-dialog"
          >
            <div className="px-6 pt-6 pb-4">
              <div className="flex items-center gap-3 mb-1">
                <div className="w-10 h-10 rounded-xl bg-amber-100 flex items-center justify-center">
                  <Eye size={20} className="text-amber-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-slate-800 text-lg" style={{ fontFamily: 'Manrope' }}>
                    Reveal Cost Prices
                  </h3>
                  <p className="text-sm text-slate-400">Manager or Admin PIN required</p>
                </div>
              </div>
            </div>
            <div className="px-6 pb-4">
              <input
                id="kiosk-cost-pin"
                data-testid="kiosk-cost-pin-input"
                type="password"
                value={costPin}
                onChange={e => { setCostPin(e.target.value); setCostPinError(''); }}
                onKeyDown={e => e.key === 'Enter' && handleCostReveal()}
                placeholder="Enter PIN..."
                className="w-full h-12 px-4 text-center text-xl font-mono tracking-[0.3em] border-2 border-slate-200 rounded-xl focus:border-amber-500 focus:ring-2 focus:ring-amber-200 outline-none transition-all"
                autoFocus
              />
              {costPinError && (
                <div className="flex items-center gap-2 mt-2 text-red-500 text-sm">
                  <AlertCircle size={14} />
                  <span>{costPinError}</span>
                </div>
              )}
            </div>
            <div className="px-6 pb-6 flex gap-3">
              <button
                onClick={() => { setShowCostPin(false); setCostPin(''); setCostPinError(''); }}
                className="flex-1 h-11 rounded-xl border-2 border-slate-200 text-slate-600 font-medium hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                data-testid="kiosk-cost-pin-submit"
                onClick={handleCostReveal}
                className="flex-1 h-11 rounded-xl bg-amber-500 hover:bg-amber-600 text-white font-semibold transition-colors shadow-sm"
              >
                Reveal
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Unlock Dialog ───────────────────────────────────────── */}
      {showUnlock && (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
            data-testid="kiosk-unlock-dialog"
          >
            <div className="px-6 pt-6 pb-4">
              <div className="flex items-center gap-3 mb-1">
                <div className="w-10 h-10 rounded-xl bg-emerald-100 flex items-center justify-center">
                  <Lock size={20} className="text-emerald-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-slate-800 text-lg" style={{ fontFamily: 'Manrope' }}>
                    Unlock AgriBooks
                  </h3>
                  <p className="text-sm text-slate-400">Enter Manager PIN, Admin PIN, or TOTP</p>
                </div>
              </div>
            </div>
            <div className="px-6 pb-4">
              <input
                id="kiosk-unlock-pin"
                data-testid="kiosk-unlock-pin-input"
                type="password"
                value={unlockPin}
                onChange={e => { setUnlockPin(e.target.value); setUnlockError(''); }}
                onKeyDown={e => e.key === 'Enter' && handleUnlock()}
                placeholder="Enter PIN..."
                className="w-full h-12 px-4 text-center text-xl font-mono tracking-[0.3em] border-2 border-slate-200 rounded-xl focus:border-emerald-500 focus:ring-2 focus:ring-emerald-200 outline-none transition-all"
                autoFocus
              />
              {unlockError && (
                <div className="flex items-center gap-2 mt-2 text-red-500 text-sm">
                  <AlertCircle size={14} />
                  <span>{unlockError}</span>
                </div>
              )}
            </div>
            <div className="px-6 pb-6 flex gap-3">
              <button
                onClick={() => { setShowUnlock(false); setUnlockPin(''); setUnlockError(''); }}
                className="flex-1 h-11 rounded-xl border-2 border-slate-200 text-slate-600 font-medium hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                data-testid="kiosk-unlock-submit"
                onClick={handleUnlock}
                disabled={unlockLoading}
                className="flex-1 h-11 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-semibold transition-colors shadow-sm disabled:opacity-50"
              >
                {unlockLoading ? 'Verifying...' : 'Unlock'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Scrollbar styles ────────────────────────────────────── */}
      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius: 999px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.2); }
      `}</style>
    </div>
  );
}
