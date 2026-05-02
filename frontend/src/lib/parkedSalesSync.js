/**
 * Parked Sales — offline-first sync helper.
 *
 * The local IndexedDB store is the source of truth on the device:
 *   • Park while online  → POST to server, mark _sync='synced'
 *   • Park while offline → save locally with _sync='pending_create'
 *   • Discard while offline → mark _sync='pending_delete' (do not remove
 *     yet — we still need to tell the server later).
 *
 * On reconnect, drainSyncQueue() fires every pending mutation in order
 * and then reconciles the cache against the server's canonical list.
 *
 * Branch isolation: the list/reconcile path is keyed on branch_id so a
 * cashier switching branches never sees stale parks for a different
 * branch.
 */
import { api } from '../contexts/AuthContext';
import {
  putParkedSaleLocal,
  getParkedSalesLocal,
  getAllParkedSalesIncludingPending,
  removeParkedSaleLocal,
  reconcileParkedSales,
} from './offlineDB';

function nowIso() { return new Date().toISOString(); }
function newId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'p-' + Math.random().toString(36).slice(2) + '-' + Date.now();
}

/** Build a park payload from the current sale state. Caller decides
 *  whether the active sale was Quick or Order — both shapes are stored
 *  on the same row so resume() can route correctly. */
export function buildParkPayload({
  branchId, mode, label,
  customer, activeScheme,
  cart, lines, header,
  itemCount, subtotal,
}) {
  return {
    id: newId(),
    branch_id: branchId,
    mode,
    label: label || '',
    customer: customer || null,
    active_scheme: activeScheme || 'retail',
    cart: cart || [],
    lines: lines || [],
    header: header || {},
    item_count: itemCount || 0,
    subtotal: subtotal || 0,
    created_at: nowIso(),
    updated_at: nowIso(),
  };
}

/** Park the current sale. Always saves locally first; server push is
 *  best-effort and falls back to the offline queue. */
export async function parkSale(payload) {
  const local = { ...payload, _sync: 'pending_create' };
  await putParkedSaleLocal(local);
  if (navigator.onLine) {
    try {
      await api.post('/parked-sales', payload);
      await putParkedSaleLocal({ ...payload, _sync: 'synced' });
    } catch {
      // Stay queued — drainSyncQueue() will retry on next online tick.
    }
  }
  return local;
}

/** Discard a parked sale. PIN is required server-side when discarding
 *  another cashier's park; pass it through here. */
export async function discardParkedSale(parkId, opts = {}) {
  const { pin } = opts;
  if (navigator.onLine) {
    try {
      const url = pin
        ? `/parked-sales/${parkId}?pin=${encodeURIComponent(pin)}`
        : `/parked-sales/${parkId}`;
      await api.delete(url);
      await removeParkedSaleLocal(parkId);
      return { ok: true };
    } catch (err) {
      // Server refused (likely PIN error) — surface it instead of queuing.
      const status = err?.response?.status;
      if (status === 403 || status === 400) throw err;
      // Network/server hiccup — queue the delete
    }
  }
  // Offline path — mark for delete, don't remove yet
  const all = await getAllParkedSalesIncludingPending();
  const row = (all || []).find(r => r.id === parkId);
  if (row) {
    await putParkedSaleLocal({ ...row, _sync: 'pending_delete' });
  }
  return { ok: true, queued: true };
}

/** Drain offline mutations against the server. Safe to call multiple
 *  times; each row is moved to 'synced' (or removed) on success. */
export async function drainSyncQueue() {
  if (!navigator.onLine) return { synced: 0, deleted: 0, failed: 0 };
  const rows = await getAllParkedSalesIncludingPending();
  let synced = 0, deleted = 0, failed = 0;
  for (const row of rows) {
    if (row._sync === 'pending_create') {
      try {
        const { _sync, ...payload } = row; void _sync;
        await api.post('/parked-sales', payload);
        await putParkedSaleLocal({ ...row, _sync: 'synced' });
        synced += 1;
      } catch { failed += 1; }
    } else if (row._sync === 'pending_delete') {
      try {
        await api.delete(`/parked-sales/${row.id}`);
        await removeParkedSaleLocal(row.id);
        deleted += 1;
      } catch (err) {
        // 404 = already gone — clear locally too
        if (err?.response?.status === 404) {
          await removeParkedSaleLocal(row.id);
          deleted += 1;
        } else {
          failed += 1;
        }
      }
    }
  }
  return { synced, deleted, failed };
}

/** Refresh local cache for the active branch from server. Falls back to
 *  the local cache when offline so the UI never goes blank. Returns the
 *  display list (excluding rows queued for delete). */
export async function loadParkedSales(branchId) {
  if (!branchId) return [];
  if (navigator.onLine) {
    try {
      // Drain pending mutations first so the reconcile sees the freshest
      // server state.
      await drainSyncQueue();
      const res = await api.get('/parked-sales', { params: { branch_id: branchId } });
      const serverRows = res.data?.parks || [];
      await reconcileParkedSales(branchId, serverRows);
    } catch {
      // Stay offline — show cached.
    }
  }
  const all = await getParkedSalesLocal();
  return (all || []).filter(r => r.branch_id === branchId)
                    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
}
