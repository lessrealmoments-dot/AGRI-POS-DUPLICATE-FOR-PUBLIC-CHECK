/**
 * Offline authentication helpers — Phase 2 of Offline POS Robustness.
 *
 * When the Terminal is offline and a credit/partial sale needs manager
 * approval (since signature_session creation requires the server), we
 * fall back to verifying the entered PIN locally against the cached
 * bcrypt-hashed admin PIN that came down from /sync/pos-data.
 *
 * Uses bcryptjs (compareSync) — single-purpose, no other crypto on this
 * page. Hash is one-way, so device compromise alone cannot recover PIN.
 */
import bcrypt from 'bcryptjs';
import { getOfflineAdminPinHash } from './offlineDB';

/**
 * Verify a Manager/Admin PIN locally against the cached bcrypt hash.
 * Returns: { ok, method, reason }
 *  ok=true → PIN matches cached admin hash; sale may proceed offline.
 *  ok=false → PIN incorrect, cache missing, or hash mismatch.
 *
 * NOTE: Only the admin_pin (system-wide) is cached. Manager-PIN-only
 * users won't pass this check offline; they'll need the admin PIN.
 * This is a deliberate trade-off — the admin PIN is the most-trusted
 * single secret and is the only one we can verify without network.
 */
export async function verifyOfflinePin(pin) {
  const cleaned = String(pin || '').trim();
  if (!cleaned) {
    return { ok: false, reason: 'PIN required' };
  }
  const hash = await getOfflineAdminPinHash();
  if (!hash) {
    return {
      ok: false,
      reason: 'No cached admin PIN — sync once while online to enable offline manager bypass',
    };
  }
  try {
    const ok = bcrypt.compareSync(cleaned, hash);
    if (!ok) return { ok: false, reason: 'Invalid PIN' };
    return { ok: true, method: 'admin_pin' };
  } catch {
    return { ok: false, reason: 'Could not verify PIN' };
  }
}

/**
 * Robust connectivity check — combines navigator.onLine with a lightweight
 * backend ping. Returns true ONLY if both checks pass.
 *
 * Used by Phase 4 to avoid false-positive "offline" toasts when the
 * browser claims offline but actually has network (Captive portal,
 * stale browser state, slow DHCP, etc).
 *
 * Caller may cache the result with a short TTL — e.g. 15 seconds — to
 * avoid hammering the backend on every render.
 */
export async function pingBackend(backendUrl, timeoutMs = 4000) {
  if (!navigator.onLine) return false;
  if (!backendUrl) return navigator.onLine; // no backend URL configured
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(`${backendUrl}/api/health`, {
      method: 'GET',
      signal: controller.signal,
      // Don't send credentials — health is public-ish
      cache: 'no-store',
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}
