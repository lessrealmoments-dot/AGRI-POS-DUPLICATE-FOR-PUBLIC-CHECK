import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export const formatPHP = (amount) => {
  return `₱${(amount || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const formatQty = (qty, unit) => {
  const q = qty || 0;
  return `${q % 1 === 0 ? q : q.toFixed(2)} ${unit || ''}`.trim();
};

// ─── Error message extractor ────────────────────────────────────────────────
// FastAPI `HTTPException(detail=...)` accepts strings OR dicts. When the
// backend sends a structured error (e.g. {type:"insufficient_funds",
// message:"..."}), a naive `e.response?.data?.detail` turns into
// `[object Object]` in toasts. This helper unpacks common shapes.
export function extractApiError(err, fallback = 'Something went wrong') {
  const d = err?.response?.data?.detail;
  if (!d) return err?.message || fallback;
  if (typeof d === 'string') return d;
  if (typeof d === 'object') {
    if (d.message) return d.message;
    if (d.detail) return d.detail;
    if (d.error) return d.error;
    try { return JSON.stringify(d); } catch { return fallback; }
  }
  return fallback;
}



// ─── Timezone-aware date / datetime formatters (Iter 221) ──────────────────
// Backend stores all *_at timestamps as UTC ISO strings (e.g. 2026-02-03T07:00:00+00:00).
// Naively slicing produces UTC wall-clock — for PH staff a 3pm sale would
// appear as "07:00 early morning". These helpers convert to the org's
// configured timezone (cached in localStorage as `agribooks.org_tz` by
// AuthContext on the main app, and by TerminalShell.backgroundSync on the
// POS terminal — single source of truth driven by the web Settings page).
// Safe with null / undefined / non-ISO inputs.
//
// Fallback rationale: if the localStorage cache is missing (first run on
// a fresh terminal, private-mode browser, etc.) we use Asia/Manila — the
// product's primary market — instead of the browser's local TZ. This
// matches lib/dateFormat.js so the two formatter families never disagree.
//
// Use:
//   fmtDateTime(iso) → "2026-02-03 15:00"   (was buggy `.slice(0,16).replace('T',' ')`)
//   fmtDate(iso)     → "2026-02-03"          (was buggy `.slice(0,10)` on UTC ISO)
//   fmtTime(iso)     → "15:00"
const DEFAULT_TZ = 'Asia/Manila';
const _tz = () => {
  try { return localStorage.getItem('agribooks.org_tz') || DEFAULT_TZ; } catch { return DEFAULT_TZ; }
};

const _looksLikePlainDate = (s) =>
  typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s.trim());

export const fmtDate = (iso) => {
  if (!iso) return '';
  // Already a plain YYYY-MM-DD (e.g. order_date, purchase_date) → pass through.
  if (_looksLikePlainDate(iso)) return iso;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: _tz(), year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d);
    const get = (k) => parts.find((p) => p.type === k)?.value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  } catch { return String(iso).slice(0, 10); }
};

export const fmtDateTime = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: _tz(), year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(d);
    const get = (k) => parts.find((p) => p.type === k)?.value || '';
    let hour = get('hour');
    if (hour === '24') hour = '00'; // Intl quirk: midnight as 24
    return `${get('year')}-${get('month')}-${get('day')} ${hour}:${get('minute')}`;
  } catch { return String(iso).slice(0, 16).replace('T', ' '); }
};

export const fmtTime = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: _tz(), hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(d);
    const get = (k) => parts.find((p) => p.type === k)?.value || '';
    let hour = get('hour');
    if (hour === '24') hour = '00';
    return `${hour}:${get('minute')}`;
  } catch { return ''; }
};
