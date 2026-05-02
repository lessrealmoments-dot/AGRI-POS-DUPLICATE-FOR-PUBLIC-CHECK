import { useState, useEffect, useRef } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Badge } from './ui/badge';
import { Search, Package, ArrowUp, ArrowDown, PlusCircle, AlertTriangle, WifiOff } from 'lucide-react';
import { getProducts, getInventoryItem, getBranchPrice } from '../lib/offlineDB';

// Bounded Levenshtein with early bail-out — same heuristic used by Quick mode
// so the typo-tolerance feels identical when we fall back offline.
function levAtMost(a, b, maxDist) {
  if (a === b) return 0;
  const la = a.length, lb = b.length;
  if (Math.abs(la - lb) > maxDist) return maxDist + 1;
  if (la === 0) return lb;
  if (lb === 0) return la;
  let prev = new Array(lb + 1);
  let curr = new Array(lb + 1);
  for (let j = 0; j <= lb; j++) prev[j] = j;
  for (let i = 1; i <= la; i++) {
    curr[0] = i;
    let rowMin = curr[0];
    for (let j = 1; j <= lb; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
      if (curr[j] < rowMin) rowMin = curr[j];
    }
    if (rowMin > maxDist) return maxDist + 1;
    [prev, curr] = [curr, prev];
  }
  return prev[lb];
}

/** Local-only product search — runs against the IndexedDB cache when the
 *  API fails (offline OR `navigator.onLine` mistakenly reports true while
 *  there is no actual connection). Mirrors the Quick-mode token + fuzzy
 *  rules so the cashier's experience is identical between modes. */
async function searchProductsLocal(query, branchId) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return { results: [], fuzzyHint: null };
  const tokens = q.split(/[\s\-/,]+/).filter(Boolean);
  if (!tokens.length) return { results: [], fuzzyHint: null };
  const all = await getProducts();
  const isShortNumeric = (t) => /^[0-9]{1,3}$/.test(t);
  const tokenMatches = (t, haystack, nameWords) => {
    if (isShortNumeric(t)) return nameWords.some(w => w.startsWith(t));
    return haystack.includes(t);
  };

  // Strict pass
  const strict = [];
  for (const p of all) {
    if (p.active === false) continue;
    const name = (p.name || '').toLowerCase();
    const sku = (p.sku || '').toLowerCase();
    const barcode = (p.barcode || '').toLowerCase();
    const haystack = `${name} ${sku} ${barcode}`;
    const nameWords = name.split(/[\s\-/,]+/).filter(Boolean);
    if (!tokens.every(t => tokenMatches(t, haystack, nameWords))) continue;
    let rank = 2;
    if (name.startsWith(q)) rank = 0;
    else if (name.includes(q)) rank = 1;
    strict.push({ p, rank, len: name.length });
  }
  strict.sort((a, b) => a.rank - b.rank || a.len - b.len);

  // Fuzzy fallback when strict produces nothing
  let fuzzy = [];
  let fuzzyHit = false;
  if (strict.length === 0) {
    const fuzzable = tokens.filter(t => t.length >= 4 && !/^[0-9]+$/.test(t));
    const exactReq = tokens.filter(t => !fuzzable.includes(t));
    if (fuzzable.length > 0) {
      const limit = (t) => (t.length >= 8 ? 2 : 1);
      for (const p of all.slice(0, 200)) {
        if (p.active === false) continue;
        const name = (p.name || '').toLowerCase();
        const sku = (p.sku || '').toLowerCase();
        const barcode = (p.barcode || '').toLowerCase();
        const haystack = `${name} ${sku} ${barcode}`;
        const nameWords = name.split(/[\s\-/,]+/).filter(Boolean);
        if (!exactReq.every(t => tokenMatches(t, haystack, nameWords))) continue;
        const words = haystack.split(/[\s\-/,]+/).filter(Boolean);
        let edits = 0, ok = true;
        for (const t of fuzzable) {
          if (haystack.includes(t)) continue;
          const cap = limit(t);
          let best = Infinity;
          for (const w of words) {
            if (Math.abs(w.length - t.length) > cap) continue;
            const d = levAtMost(t, w, cap);
            if (d < best) best = d;
            if (best === 0) break;
          }
          if (best > cap) { ok = false; break; }
          edits += best;
        }
        if (ok) fuzzy.push({ p, edits, len: name.length });
      }
      fuzzy.sort((a, b) => a.edits - b.edits || a.len - b.len);
      if (fuzzy.length > 0) fuzzyHit = true;
    }
  }

  const picks = (strict.length ? strict : fuzzy).slice(0, 10).map(x => x.p);

  // Enrich each pick with branch price + inventory from cache
  const enriched = await Promise.all(picks.map(async p => {
    const inv = await getInventoryItem(p.id);
    const bp = branchId ? await getBranchPrice(p.id) : null;
    const prices = bp?.prices ? { ...(p.prices || {}), ...bp.prices } : (p.prices || {});
    const cost = bp?.cost_price ?? p.cost_price;
    return {
      ...p,
      prices,
      cost_price: cost,
      effective_capital: cost,
      available: inv?.quantity ?? 0,
      reserved: 0,
      coming: 0,
      branch_set_scheme_keys: bp?.prices ? Object.keys(bp.prices) : [],
    };
  }));

  return {
    results: enriched,
    fuzzyHint: fuzzyHit ? { query, count: fuzzy.length } : null,
  };
}

export default function SmartProductSearch({ onSelect, branchId, onCreateNew }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [noResults, setNoResults] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [fuzzyHint, setFuzzyHint] = useState(null);
  const [offlineFallback, setOfflineFallback] = useState(false);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0, maxHeight: 400, flipUp: false });
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);
  const debounceRef = useRef(null);

  // Position the dropdown using fixed coords to escape any overflow:hidden/auto ancestor.
  // Auto-flip: when there's < 320px below the input AND more space above, render
  // ABOVE the input instead. Keeps the result list visible whether the cashier
  // is typing on a row near the bottom of the screen or near the top.
  // Also clamps max-height to the available space so the list never spills
  // past the viewport edge.
  const updateDropdownPos = () => {
    if (!inputRef.current) return;
    const rect = inputRef.current.getBoundingClientRect();
    const vh = window.innerHeight;
    const spaceBelow = vh - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    // Flip up when there isn't comfortable room below AND we have more headroom up
    const flipUp = spaceBelow < 280 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(180, Math.min(400, flipUp ? spaceAbove : spaceBelow));
    setDropdownPos({
      top: flipUp ? null : rect.bottom + 4,
      bottom: flipUp ? vh - rect.top + 4 : null,
      left: rect.left,
      width: Math.max(rect.width, 320),
      maxHeight,
      flipUp,
    });
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
    if (!query || query.length < 1) { setResults([]); setOpen(false); setNoResults(false); setFuzzyHint(null); setOfflineFallback(false); return; }
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      // Try the API first. On ANY failure (offline, server error, browser
      // misreporting `navigator.onLine`) fall back to the local IndexedDB
      // cache so Order mode behaves identically to Quick mode without a
      // network. Same token + fuzzy rules.
      let data = null;
      let usedFallback = false;
      let fallbackHint = null;
      try {
        const res = await api.get('/products/search-detail', { params: { q: query, branch_id: branchId } });
        data = res.data || [];
      } catch {
        try {
          const local = await searchProductsLocal(query, branchId);
          data = local.results;
          fallbackHint = local.fuzzyHint;
          usedFallback = true;
        } catch {
          data = [];
        }
      }
      setResults(data);
      // Backend attaches `_fuzzy_hint` to every item when fallback fired.
      // Read from the first result; null otherwise. Local fallback uses
      // its own computed hint.
      setFuzzyHint(usedFallback
        ? fallbackHint
        : (data.length > 0 && data[0]._fuzzy_hint ? data[0]._fuzzy_hint : null));
      setOfflineFallback(usedFallback);
      setNoResults(data.length === 0 && query.length >= 2);
      updateDropdownPos();
      setOpen(true);
      setActiveIndex(data.length > 0 ? 0 : -1);
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
          style={{
            position: 'fixed',
            ...(dropdownPos.flipUp
              ? { bottom: dropdownPos.bottom }
              : { top: dropdownPos.top }),
            left: dropdownPos.left,
            width: dropdownPos.width,
            maxHeight: dropdownPos.maxHeight,
            zIndex: 9999,
          }}
          className="bg-white border border-slate-200 rounded-lg shadow-xl overflow-y-auto flex flex-col"
          data-testid="search-results-dropdown"
          data-flip={dropdownPos.flipUp ? 'up' : 'down'}
        >
          {/* Offline fallback chip — shows when results came from IndexedDB */}
          {offlineFallback && (
            <div
              data-testid="search-offline-chip"
              className="px-3 py-1.5 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-[11px] text-amber-800 sticky top-0 z-10"
            >
              <WifiOff size={11} className="text-amber-600 shrink-0" />
              <span>Offline — searching cached products. Stock & prices may not reflect the latest changes.</span>
            </div>
          )}

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
