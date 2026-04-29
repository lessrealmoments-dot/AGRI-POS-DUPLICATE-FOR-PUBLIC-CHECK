/**
 * Offline authentication helpers — Phase 2 of Offline POS Robustness.
 *
 * When the Terminal/Web POS is offline and a credit/partial sale needs
 * manager approval, we verify the entered PIN locally against:
 *   1. The bcrypt-hashed system admin PIN (cached from /sync/pos-data)
 *   2. The list of branch-scoped manager + admin/owner PINs (plain-text
 *      cache, same trust boundary as customer balances and product
 *      catalog already cached).
 *
 * Real-world: admin/owner is rarely in-store; the manager runs the POS.
 * Restricting offline bypass to admin-only would block legitimate sales.
 */
import bcrypt from 'bcryptjs';
import { getOfflineAdminPinHash, getOfflinePinGrants } from './offlineDB';

/**
 * Verify a Manager/Admin PIN locally.
 * Returns: { ok, method, verifier_id, verifier_name, reason }
 *  ok=true → PIN matches; sale may proceed offline.
 *  ok=false → PIN incorrect or no cached credentials yet.
 */
export async function verifyOfflinePin(pin) {
  const cleaned = String(pin || '').trim();
  if (!cleaned) {
    return { ok: false, reason: 'PIN required' };
  }
  // 1. Try the bcrypt-hashed system admin PIN
  const hash = await getOfflineAdminPinHash();
  if (hash) {
    try {
      if (bcrypt.compareSync(cleaned, hash)) {
        return {
          ok: true,
          method: 'admin_pin',
          verifier_id: 'system_admin',
          verifier_name: 'Admin',
        };
      }
    } catch { /* fall through to grants */ }
  }
  // 2. Try the plain-text manager + admin/owner PIN grants
  const grants = await getOfflinePinGrants();
  for (const g of grants) {
    if (g && g.pin && cleaned === String(g.pin).trim()) {
      return {
        ok: true,
        method: g.method || 'manager_pin',
        verifier_id: g.verifier_id,
        verifier_name: g.verifier_name || 'Manager',
      };
    }
  }
  if (!hash && grants.length === 0) {
    return {
      ok: false,
      reason: 'No cached credentials — sync once while online to enable offline manager bypass',
    };
  }
  return { ok: false, reason: 'Invalid PIN' };
}

/**
 * Robust connectivity check — combines navigator.onLine with a lightweight
 * backend ping. Returns true ONLY if both checks pass.
 */
export async function pingBackend(backendUrl, timeoutMs = 4000) {
  if (!navigator.onLine) return false;
  if (!backendUrl) return navigator.onLine;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(`${backendUrl}/api/health`, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}
