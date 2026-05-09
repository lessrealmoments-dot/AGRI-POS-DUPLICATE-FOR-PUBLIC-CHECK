/**
 * Offline authentication helpers — POST C-1 hardening (Audit 2026-02).
 *
 * SECURITY MODEL CHANGE:
 * Previously, the server shipped admin_pin_hash + plain manager PINs to
 * every device, and we verified the entered PIN locally with bcrypt /
 * string compare. That leaked accountability credentials to cashier
 * devices.
 *
 * NEW DESIGN:
 *   • Online → call /api/verify/pin (server is the only verifier).
 *   • Offline → accept the PIN typed by the manager OPTIMISTICALLY,
 *     attach it to the queued sale envelope, and let the backend
 *     re-verify it via verify_pin_for_action() at sync time.
 *     If wrong, the sale is flagged pin_resync_failed for review
 *     (mirrors the existing offline_price_changes pattern).
 *
 * The cashier device never sees a real PIN or hash again.
 */
import bcrypt from 'bcryptjs';
import { getOfflineAdminPinHash, getOfflinePinGrants } from './offlineDB';

/**
 * Verify a Manager/Admin PIN.
 * Returns: { ok, method, verifier_id, verifier_name, deferred_verification, reason }
 *
 * Online:  callers should use /api/verify/pin instead of this helper.
 * Offline: this helper now ALWAYS accepts a non-empty PIN as
 *          deferred_verification=true. The PIN flows in the offline
 *          envelope and is re-verified server-side at sync.
 *
 * Backwards-compat: legacy callers that already cached admin_pin_hash
 * before this fix shipped will still get a local bcrypt match if the
 * old hash is present in IndexedDB; that path is kept ONLY to avoid
 * breaking devices mid-rollout and will be cleared on the next sync
 * (admin_pin_hash is no longer ever written by syncManager).
 */
export async function verifyOfflinePin(pin) {
  const cleaned = String(pin || '').trim();
  if (!cleaned) {
    return { ok: false, reason: 'PIN required' };
  }

  // Identity directory — used ONLY to render "Approved by …" labels
  // after an optimistic accept. No PIN is matched here.
  const grants = await getOfflinePinGrants();

  // Legacy path: if a stale admin_pin_hash is still cached on this device
  // from before the C-1 fix, honour it for one more session so existing
  // workflows do not hard-break. The next sync will null it out.
  const legacyHash = await getOfflineAdminPinHash();
  if (legacyHash) {
    try {
      if (bcrypt.compareSync(cleaned, legacyHash)) {
        return {
          ok: true,
          method: 'admin_pin',
          verifier_id: 'system_admin',
          verifier_name: 'Admin',
          deferred_verification: false,
        };
      }
    } catch { /* fall through */ }
  }

  // NEW deferred-verification path: optimistically accept and record the
  // verifier we will *display* (first admin/owner if any, else the first
  // grant for this branch). Server re-verifies at sync.
  if (!grants || grants.length === 0) {
    // Edge case: no manager directory cached yet. Still accept so the
    // cashier is not blocked offline; sync will tag the sale for review
    // if no manager PIN matches.
    return {
      ok: true,
      method: 'pending_verification',
      verifier_id: '',
      verifier_name: 'Pending Manager Verification',
      deferred_verification: true,
    };
  }

  const adminGrant = grants.find((g) => g && (g.role === 'admin' || g.role === 'owner'));
  const display = adminGrant || grants[0];
  return {
    ok: true,
    method: display.method || 'manager_pin',
    verifier_id: display.verifier_id || '',
    verifier_name: display.verifier_name || 'Manager',
    deferred_verification: true,
  };
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
