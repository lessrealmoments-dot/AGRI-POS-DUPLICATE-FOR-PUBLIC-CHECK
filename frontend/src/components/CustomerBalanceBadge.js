import { useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';

// ── In-memory cache so multiple pages on the same session don't re-fetch ──
let _cache = null;        // array of summary rows
let _cacheTime = 0;       // ms epoch
let _inFlight = null;     // promise during concurrent fetches
const CACHE_MS = 30_000;  // 30s — refresh fast enough that recent payments reflect

export const invalidateBalanceCache = () => { _cache = null; _cacheTime = 0; };

export function useCustomerBalances() {
  const [byId, setById] = useState(() => _toMap(_cache));
  const [loading, setLoading] = useState(!_cache);

  useEffect(() => {
    let cancelled = false;
    const now = Date.now();
    const fresh = _cache && now - _cacheTime < CACHE_MS;
    if (fresh) { setById(_toMap(_cache)); setLoading(false); return; }

    const fetcher = _inFlight || (
      _inFlight = api.get('/customers/receivables-summary')
        .then(r => { _cache = r.data || []; _cacheTime = Date.now(); return _cache; })
        .catch(() => { _cache = []; _cacheTime = Date.now(); return []; })
        .finally(() => { _inFlight = null; })
    );

    fetcher.then(rows => { if (!cancelled) { setById(_toMap(rows)); setLoading(false); } });
    return () => { cancelled = true; };
  }, []);

  return { byId, loading };
}

const _toMap = (rows) => {
  const m = {};
  (rows || []).forEach(r => { m[r.id] = r; });
  return m;
};

const daysOverdueFromDate = (isoDate) => {
  if (!isoDate) return 0;
  try {
    const due = new Date(isoDate + 'T00:00:00');
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.max(0, Math.floor((today - due) / 86400000));
  } catch { return 0; }
};

// ── Inline badge: shows balance + overdue days when applicable ──
// Hides itself when balance is 0 or row not found.
export default function CustomerBalanceBadge({ row, customerId, byId, className = '', size = 'xs' }) {
  const r = row || (byId && customerId ? byId[customerId] : null);
  if (!r) return null;
  if (!r.balance || r.balance <= 0.005) return null;

  const isOverdue = r.overdue_balance > 0.005;
  const daysOver = isOverdue ? daysOverdueFromDate(r.oldest_overdue_due_date) : 0;

  // 0 = current; 1-30 = amber; >30 = red
  const tone = !isOverdue
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : daysOver > 30
      ? 'bg-red-50 text-red-700 border-red-200'
      : 'bg-amber-50 text-amber-700 border-amber-200';

  const sizeCls = size === 'xs' ? 'text-[9.5px] px-1.5 py-px' : 'text-[10px] px-2 py-0.5';
  const dot = !isOverdue ? 'bg-emerald-500' : daysOver > 30 ? 'bg-red-500' : 'bg-amber-500';

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border font-mono leading-tight ${sizeCls} ${tone} ${className}`}
      title={
        isOverdue
          ? `Open ${formatPHP(r.balance)} · ${formatPHP(r.overdue_balance)} overdue (${daysOver}d past due, oldest due ${r.oldest_overdue_due_date})`
          : `Open balance ${formatPHP(r.balance)} — current`
      }
      data-testid={`balance-badge-${r.id}`}
    >
      <span className={`w-1 h-1 rounded-full ${dot}`} />
      {formatPHP(r.balance)}
      {isOverdue && daysOver > 0 && <span className="opacity-80">· {daysOver}d</span>}
    </span>
  );
}
