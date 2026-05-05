// ─────────────────────────────────────────────────────────────────────
// Centralized date / time formatters — Iter 238
//
// Single source of truth for ALL date+time display in the app. Replaces
// 200+ ad-hoc `toLocaleString()` / `toLocaleDateString()` / `slice(0, 10)`
// calls that produced inconsistent formats and dependent on the browser
// locale defaulting (en-US vs en-PH vs en-GB).
//
// Display contract:
//   • Date  →  MM/DD/YYYY                  (e.g. 05/05/2026)
//   • Time  →  hh:mm AM/PM                 (e.g. 08:30 AM)
//   • Both  →  MM/DD/YYYY · hh:mm AM/PM    (e.g. 05/05/2026 · 08:30 AM)
//
// All formatters accept a UTC ISO string (or a `Date`, or an empty
// value) and render in the org's local timezone (cached in localStorage
// under `agribooks.org_tz`, defaulting to Asia/Manila).
// ─────────────────────────────────────────────────────────────────────

const DEFAULT_TZ = 'Asia/Manila';

function _tz() {
  try {
    return localStorage.getItem('agribooks.org_tz') || DEFAULT_TZ;
  } catch {
    return DEFAULT_TZ;
  }
}

function _toDate(input) {
  if (!input) return null;
  if (input instanceof Date) return isNaN(input.getTime()) ? null : input;
  // If string is a YYYY-MM-DD calendar-day (no time component), parse as
  // local-day at midnight to avoid the toISOString TZ-shift trap.
  if (typeof input === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(input)) {
    const [y, m, d] = input.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  const d = new Date(input);
  return isNaN(d.getTime()) ? null : d;
}

/** MM/DD/YYYY in org-local TZ. Returns '' for falsy input. */
export function formatDate(input) {
  const d = _toDate(input);
  if (!d) return '';
  try {
    return d.toLocaleDateString('en-US', {
      timeZone: _tz(),
      month: '2-digit',
      day: '2-digit',
      year: 'numeric',
    });
  } catch {
    return d.toLocaleDateString('en-US');
  }
}

/** hh:mm AM/PM in org-local TZ. Returns '' for falsy input. */
export function formatTime(input) {
  const d = _toDate(input);
  if (!d) return '';
  try {
    return d.toLocaleTimeString('en-US', {
      timeZone: _tz(),
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
  }
}

/** MM/DD/YYYY · hh:mm AM/PM in org-local TZ. Returns '' for falsy input. */
export function formatDateTime(input) {
  const d = _toDate(input);
  if (!d) return '';
  const date = formatDate(d);
  const time = formatTime(d);
  return date && time ? `${date} · ${time}` : (date || time);
}

/** hh:mm:ss AM/PM (used for live clocks / very precise audit logs). */
export function formatTimeFull(input) {
  const d = _toDate(input);
  if (!d) return '';
  try {
    return d.toLocaleTimeString('en-US', {
      timeZone: _tz(),
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  } catch {
    return d.toLocaleTimeString('en-US', { hour12: true });
  }
}

/** "May 5, 2026" — long-form date, used in section headers / report titles. */
export function formatDateLong(input) {
  const d = _toDate(input);
  if (!d) return '';
  try {
    return d.toLocaleDateString('en-US', {
      timeZone: _tz(),
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    });
  } catch {
    return d.toLocaleDateString('en-US');
  }
}

/** Today's date in org-local TZ as YYYY-MM-DD (canonical storage format).
 *  Replaces ad-hoc `new Date().toISOString().slice(0, 10)` which used UTC
 *  and produced yesterday-Manila for ~8 hours per day. */
export function localTodayStr() {
  const tz = _tz();
  try {
    // 'en-CA' yields YYYY-MM-DD natively.
    return new Date().toLocaleDateString('en-CA', { timeZone: tz });
  } catch {
    return new Date().toLocaleDateString('en-CA');
  }
}

/** Convert a server-issued UTC ISO string into the wall-clock time
 *  string the user actually saw at that moment. Used for the sales_log
 *  "time" column and any place we display a recorded time. */
export function timeFromIso(iso) {
  return formatTime(iso);
}

/** Convert a `time_str` ("HH:MM:SS" 24h) into "hh:mm AM/PM". */
export function format24To12(timeStr) {
  if (!timeStr || typeof timeStr !== 'string') return '';
  const m = timeStr.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return timeStr;
  let h = parseInt(m[1], 10);
  const min = m[2];
  if (Number.isNaN(h)) return timeStr;
  const period = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${String(h).padStart(2, '0')}:${min} ${period}`;
}

const _api = {
  formatDate,
  formatTime,
  formatDateTime,
  formatTimeFull,
  formatDateLong,
  localTodayStr,
  timeFromIso,
  format24To12,
};
export default _api;
