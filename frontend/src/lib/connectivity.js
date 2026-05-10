/**
 * Phase 4A.1 — Connectivity helpers.
 *
 * isTrueNetworkError(err): true ONLY for transport-layer failures (no
 *   server response). 4xx / 5xx with a body are NOT network errors —
 *   the cashier needs to see and fix them; saving offline would silently
 *   mask validation/business-rule failures.
 *
 * pingBackendHealth(timeoutMs): lightweight reachability probe of the
 *   already-existing GET /api/health endpoint. Returns true on HTTP 2xx,
 *   false on any other response, abort, or thrown error.
 */

export function isTrueNetworkError(err) {
  if (!err) return false;
  // axios convention — server-response present means the backend
  // actually answered, so this is NOT a network error.
  if (err.response) return false;
  if (err.code === 'ERR_NETWORK') return true;
  if (err.code === 'ECONNABORTED') return true;
  const msg = (err.message || '').toLowerCase();
  if (msg.includes('network error')) return true;
  if (msg.includes('failed to fetch')) return true;
  // Anything left without an .response object is transport-layer.
  return !err.response;
}

export async function pingBackendHealth(timeoutMs = 4000) {
  const url = `${process.env.REACT_APP_BACKEND_URL}/api/health`;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const r = await fetch(url, { method: 'GET', signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}
