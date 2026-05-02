import { useState, useEffect, useRef } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Badge } from './ui/badge';
import { Search, Package, ArrowUp, ArrowDown, PlusCircle, AlertTriangle } from 'lucide-react';

export default function SmartProductSearch({ onSelect, branchId, onCreateNew }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [noResults, setNoResults] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [fuzzyHint, setFuzzyHint] = useState(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);
  const debounceRef = useRef(null);

  // Position the dropdown using fixed coords to escape any overflow:hidden/auto ancestor
  const updateDropdownPos = () => {
    if (inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      setDropdownPos({ top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 320) });
    }
  };

  useEffect(() => {
    if (!open) return;
    updateDropdownPos();
    window.addEventListener('scroll', updateDropdownPos, true);
    window.addEventListener('resize', updateDropdownPos);
    return () => {
      window.removeEventListener('scroll', updateDropdownPos, true);
      window.removeEventListener('resize', updateDropdownPos);
    };
  }, [open]);

  useEffect(() => {
    if (!query || query.length < 1) { setResults([]); setOpen(false); setNoResults(false); setFuzzyHint(null); return; }
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await api.get('/products/search-detail', { params: { q: query, branch_id: branchId } });
        const data = res.data || [];
        setResults(data);
        // Backend attaches `_fuzzy_hint` to every item when fallback fired.
        // Read from the first result; null otherwise.
        setFuzzyHint(data.length > 0 && data[0]._fuzzy_hint ? data[0]._fuzzy_hint : null);
        setNoResults(data.length === 0 && query.length >= 2);
        updateDropdownPos();
        setOpen(true);
        // Pre-select the first result so Enter immediately picks the top match
        // and Arrow-Down centers visibly from the start.
        setActiveIndex(data.length > 0 ? 0 : -1);
      } catch { setResults([]); setNoResults(true); setFuzzyHint(null); }
    }, 200);
    return () => clearTimeout(debounceRef.current);
  }, [query, branchId]);

  const handleKeyDown = (e) => {
    if (!open || !results.length) {
      if (e.key === 'Escape') { setOpen(false); }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(prev => Math.min(prev + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(prev => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      selectProduct(results[activeIndex]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  const selectProduct = (product) => {
    if (product?.disabled_at_branch) {
      // Block selection — caller's onSelect won't be invoked.
      return;
    }
    onSelect(product);
    setQuery('');
    setResults([]);
    setOpen(false);
    setActiveIndex(-1);
    setFuzzyHint(null);
    inputRef.current?.focus();
  };

  // Keep the active row centered in the dropdown so similarly-named items
  // (e.g. "Galimax 1, 2, 3, 4, 5") never disappear behind the expanded
  // detail card. `block: 'center'` instead of 'nearest' guarantees the
  // highlight is always visible regardless of the active row's height.
  useEffect(() => {
    if (activeIndex >= 0 && dropdownRef.current) {
      const item = dropdownRef.current.children[activeIndex];
      item?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activeIndex]);

  return (
    <div className="relative" data-testid="smart-product-search">
      <div className="relative">
        <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          ref={inputRef}
          data-testid="product-search-input"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => { if (results.length) setOpen(true); }}
          onBlur={() => setTimeout(() => setOpen(false), 200)}
          placeholder="Type product name or scan barcode..."
          className="w-full h-9 pl-8 pr-3 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/30 focus:border-[#1A4D2E] bg-white"
          autoComplete="new-password"
        />
      </div>

      {open && (results.length > 0 || noResults) && (
        <div
          style={{ position: 'fixed', top: dropdownPos.top, left: dropdownPos.left, width: dropdownPos.width, zIndex: 9999 }}
          className="bg-white border border-slate-200 rounded-lg shadow-xl max-h-[400px] overflow-y-auto"
          data-testid="search-results-dropdown"
        >
          {/* Did-you-mean chip when fuzzy fallback fired */}
          {fuzzyHint && (
            <div
              data-testid="search-fuzzy-hint"
              className="px-3 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-xs text-amber-800 sticky top-0 z-10"
            >
              <AlertTriangle size={12} className="text-amber-600 shrink-0" />
              <span className="flex-1">
                No exact match for <strong>"{fuzzyHint.query}"</strong> — showing closest {fuzzyHint.count === 1 ? 'match' : 'matches'}.
              </span>
            </div>
          )}

          <div ref={dropdownRef}>
          {results.map((p, i) => {
            const isDisabled = !!p.disabled_at_branch;
            return (
            <div
              key={p.id}
              data-testid={`search-result-${p.id}`}
              onMouseDown={() => selectProduct(p)}
              onMouseEnter={() => !isDisabled && setActiveIndex(i)}
              className={`px-3 py-2.5 border-b border-slate-100 last:border-0 transition-colors ${
                isDisabled ? 'opacity-50 bg-slate-50/60 cursor-not-allowed'
                : (i === activeIndex ? 'bg-emerald-50 border-l-[3px] border-l-emerald-700 cursor-pointer' : 'hover:bg-slate-50 cursor-pointer')
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-medium text-sm truncate">{p.name}</span>
                  <span className="text-[10px] font-mono text-slate-400 shrink-0">{p.sku}</span>
                  {p.is_repack && <Badge className="text-[9px] bg-amber-100 text-amber-700 shrink-0">R</Badge>}
                  {isDisabled && (
                    <Badge className="text-[9px] bg-amber-100 text-amber-700 shrink-0 gap-1">
                      Disabled at branch
                    </Badge>
                  )}
                </div>
                <span className="text-sm font-bold text-[#1A4D2E] shrink-0 ml-2">{formatPHP(p.prices?.retail)}</span>
              </div>

              {i === activeIndex && (
                <div className="mt-2 p-2.5 rounded-md bg-slate-50 border border-slate-100 text-xs animate-fadeIn">
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <span className="text-slate-400 block">Retail</span>
                      <span className="font-bold text-[#1A4D2E]">{formatPHP(p.prices?.retail)}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">Capital</span>
                      <span className="font-bold">{formatPHP(p.cost_price)}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">Available</span>
                      <span className={`font-bold ${p.available <= 0 ? 'text-red-600' : ''}`}>{p.available?.toFixed(1)} {p.unit}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">Reserved / Coming</span>
                      <span className="font-bold">{p.reserved} / {p.coming}</span>
                    </div>
                  </div>
                  {p.is_repack && p.parent_name && (
                    <div className="mt-2 pt-2 border-t border-slate-200 flex items-center gap-2">
                      <Package size={12} className="text-amber-500" />
                      <span className="text-slate-500">Parent: <b>{p.parent_name}</b></span>
                      <span className="text-slate-400">Stock: <b>{p.parent_stock?.toFixed(1)} {p.parent_unit}</b></span>
                    </div>
                  )}
                </div>
              )}

              {i !== activeIndex && (
                <div className="flex gap-3 mt-0.5 text-[11px] text-slate-400">
                  <span>Capital: {formatPHP(p.cost_price)}</span>
                  <span>Avail: {p.available?.toFixed(0)} {p.unit}</span>
                  {p.reserved > 0 && <span className="text-amber-500">Rsv: {p.reserved}</span>}
                  {p.is_repack && <span className="text-amber-600">Parent: {p.parent_stock?.toFixed(0)} {p.parent_unit}</span>}
                </div>
              )}
            </div>
            );
          })}
          </div>

          {noResults && query.length >= 2 && (
            <div className="px-3 py-3 border-t border-slate-100">
              <p className="text-sm text-slate-500 mb-2">No product found for "<b>{query}</b>"</p>
              {onCreateNew ? (
                <button
                  data-testid="create-product-from-search"
                  onMouseDown={() => { onCreateNew(query); setOpen(false); setQuery(''); }}
                  className="flex items-center gap-2 w-full px-3 py-2 rounded-md bg-[#1A4D2E]/5 hover:bg-[#1A4D2E]/10 text-[#1A4D2E] text-sm font-medium transition-colors"
                >
                  <PlusCircle size={16} /> Create "{query}" as new product
                </button>
              ) : (
                <p className="text-xs text-slate-400">Product does not exist in the system</p>
              )}
            </div>
          )}
          {results.length > 0 && (
            <div className="px-3 py-1.5 bg-slate-50 text-[10px] text-slate-400 flex items-center gap-3 border-t sticky bottom-0">
              <span><ArrowUp size={10} className="inline" /><ArrowDown size={10} className="inline" /> navigate</span>
              <span>Enter to select</span>
              <span>Esc to close</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
