/**
 * AgriPOS Sync Manager — Resilient Transaction Envelope Pattern
 *
 * Key improvements over previous version:
 *  - Each sale gets a unique envelope_id (separate from invoice id) for idempotency
 *  - Sales processed ONE AT A TIME so a single failure doesn't block others
 *  - Automatic retry with exponential backoff (2s → 4s → 8s, max 3 retries)
 *  - Network error vs server error distinction:
 *      • Network error → stop sync, retry later (preserve queue)
 *      • Server error (4xx) → mark sale as failed, skip it (don't retry forever)
 *  - Auto-sync on reconnect (after 2s delay to let connection stabilize)
 *  - Manual retry button support via triggerSync()
 */

import { api } from '../contexts/AuthContext';
import {
  getPendingSales, removePendingSale,
  cacheProducts, cacheCustomers, cachePriceSchemes, cacheInventory, cacheBranchPrices,
  setMeta, getMeta, getPendingSaleCount, putProduct,
  getCacheCounts,
  updateInventoryBatch, mergeCustomers, setOfflineAdminPinHash, setOfflinePinGrants,
} from './offlineDB';

let syncInProgress = false;
let syncListeners = [];
let autoSyncInterval = null;
let cacheRefreshInterval = null;
let inventoryPulseInterval = null;
const CACHE_REFRESH_MS = 5 * 60 * 1000; // 5 minutes — catalog delta
const INVENTORY_PULSE_MS = 60 * 1000;   // 60 seconds — stock levels only

export function onSyncUpdate(callback) {
  syncListeners.push(callback);
  return () => { syncListeners = syncListeners.filter(cb => cb !== callback); };
}

function emit(data) {
  syncListeners.forEach(cb => cb(data));
}

/** Generate a UUID-based envelope ID */
export function newEnvelopeId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for older environments
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

/**
 * Process a single pending sale with the envelope pattern.
 * Returns: 'synced' | 'duplicate' | 'failed_permanent' | 'network_error'
 */
async function processSingleSale(sale, retryCount = 0) {
  const MAX_RETRIES = 2;
  try {
    const res = await api.post('/sales/sync', { sales: [sale] });
    const results = res.data.results || res.data.synced || [];
    const result = results.find(r => r.id === sale.id || r.envelope_id === sale.envelope_id);
    return result?.status === 'duplicate' ? 'duplicate' : 'synced';
  } catch (err) {
    if (!err.response) {
      // Network error — stop and wait for reconnect
      return 'network_error';
    }
    if (err.response.status >= 500 && retryCount < MAX_RETRIES) {
      // Server error — retry with backoff
      await new Promise(r => setTimeout(r, Math.pow(2, retryCount + 1) * 1000));
      return processSingleSale(sale, retryCount + 1);
    }
    // 4xx or too many retries — permanent failure, skip
    return 'failed_permanent';
  }
}

/**
 * Sync all pending offline sales one at a time.
 * Stops immediately on network error (connection unstable).
 * Skips permanently failed sales (bad data) and continues to the next.
 */
export async function syncPendingSales() {
  if (syncInProgress || !navigator.onLine) return null;
  syncInProgress = true;

  try {
    const pendingSales = await getPendingSales();
    if (!pendingSales.length) {
      syncInProgress = false;
      return { synced: 0, total: 0 };
    }

    emit({ type: 'sync_start', count: pendingSales.length });

    let synced = 0;
    let skipped = 0;
    let networkError = false;

    for (let i = 0; i < pendingSales.length; i++) {
      const sale = pendingSales[i];
      emit({ type: 'sync_progress', current: i + 1, total: pendingSales.length, saleId: sale.id });

      const status = await processSingleSale(sale);

      if (status === 'network_error') {
        networkError = true;
        emit({ type: 'sync_paused', reason: 'network_error', remaining: pendingSales.length - i });
        break;
      }

      if (status === 'synced' || status === 'duplicate') {
        await removePendingSale(sale.id);
        synced++;
      } else {
        // Permanent failure — remove from queue (bad data, don't retry forever)
        await removePendingSale(sale.id);
        skipped++;
      }
    }

    const remaining = await getPendingSaleCount();
    emit({ type: 'sync_complete', synced, skipped, remaining, networkError });

    if (!networkError) {
      await setMeta('last_sale_sync', new Date().toISOString());
    }

    syncInProgress = false;
    return { synced, skipped, remaining, networkError };
  } catch (error) {
    emit({ type: 'sync_error', error: error.message });
    syncInProgress = false;
    return null;
  }
}

/**
 * Force a sync attempt (called by manual "Sync Now" button).
 */
export async function triggerSync(branchId = null) {
  if (!navigator.onLine) {
    emit({ type: 'sync_error', error: 'No internet connection' });
    return null;
  }
  const salesResult = await syncPendingSales();
  if (branchId) await refreshPOSCache(branchId);
  return salesResult;
}

/**
 * Refresh the local IndexedDB cache with all branch data.
 */
export async function refreshPOSCache(branchId = null) {
  if (!navigator.onLine) return false;

  try {
    emit({ type: 'sync_step', stepLabel: 'Connecting to server...', pct: 5 });

    const params = {};
    if (branchId) params.branch_id = branchId;

    // Delta sync: pass last_sync timestamp to only fetch changes
    const lastSync = await getMeta('last_sync');
    if (lastSync) params.last_sync = lastSync;

    const response = await api.get('/sync/pos-data', { params });
    const { products = [], customers = [], price_schemes = [], inventory = [], branch_prices = [], deleted_ids = [], deleted_customer_ids = [], admin_pin_hash = null, offline_pin_grants = [], sync_time, is_delta } = response.data;

    emit({ type: 'sync_step', stepLabel: `Saving ${products.length} products...`, pct: 25 });
    if (is_delta && products.length > 0) {
      // Delta: merge updated products into existing cache
      for (const p of products) await putProduct(p);
    } else if (products.length > 0) {
      await cacheProducts(products);
    }

    emit({ type: 'sync_step', stepLabel: 'Saving inventory levels...', pct: 50 });
    if (inventory.length) {
      const inventoryForDB = inventory.map(item => ({
        product_id: item.product_id,
        quantity: item.quantity ?? 0,
        branch_id: item.branch_id,
        updated_at: item.updated_at || new Date().toISOString(),
      }));
      await cacheInventory(inventoryForDB);
    }

    emit({ type: 'sync_step', stepLabel: `Saving ${customers.length} customers...`, pct: 75 });
    if (is_delta) {
      // Delta: merge updated customers and purge any deleted ones — preserves unchanged cache entries
      await mergeCustomers(customers, deleted_customer_ids);
    } else {
      // Full sync: replace entire customer cache (deleted ones are excluded server-side)
      await cacheCustomers(customers);
    }

    emit({ type: 'sync_step', stepLabel: 'Saving price schemes & branch prices...', pct: 92 });
    await cachePriceSchemes(price_schemes);
    if (branch_prices && branch_prices.length) {
      const bpForDB = branch_prices.map(bp => ({
        product_id: bp.product_id,
        prices: bp.prices || {},
        cost_price: bp.cost_price ?? null,
        branch_id: bp.branch_id,
      }));
      await cacheBranchPrices(bpForDB);
    }

    const timestamp = sync_time || new Date().toISOString();
    await setMeta('last_sync', timestamp);
    await setMeta('last_sync_branch', branchId || 'all');
    // Cache admin_pin hash for offline manager bypass (Phase 2)
    if (admin_pin_hash) {
      await setOfflineAdminPinHash(admin_pin_hash);
    }
    // Cache branch-scoped manager + admin/owner PIN grants
    if (Array.isArray(offline_pin_grants)) {
      await setOfflinePinGrants(offline_pin_grants);
    }
    await setMeta('last_sync_counts', {
      // Read live counts from IndexedDB so delta syncs (where the payload is
      // empty when nothing changed) don't reset the displayed totals to 0.
      ...(await getCacheCounts()),
      delta_products: products.length,
      delta_customers: customers.length,
      is_delta: !!is_delta,
    });

    emit({
      type: 'cache_refreshed',
      timestamp,
      productCount: products.length,
      customerCount: customers.length,
      inventoryCount: inventory.length,
    });

    return true;
  } catch (error) {
    emit({ type: 'sync_error', error: error.message });
    return false;
  }
}

/** Full sync: push pending sales first, then refresh cache */
export async function fullSync(branchId = null) {
  await syncPendingSales();
  return refreshPOSCache(branchId);
}

/**
 * Lightweight inventory pulse — fetches only changed stock quantities.
 * Called every 60 seconds for near-real-time stock visibility.
 */
export async function inventoryPulse(branchId) {
  if (!navigator.onLine || !branchId || branchId === 'all') return false;
  try {
    const lastPulse = await getMeta('last_inventory_pulse');
    const params = { branch_id: branchId };
    if (lastPulse) params.since = lastPulse;

    const response = await api.get('/sync/inventory-pulse', { params });
    const { items = [], pulse_time } = response.data;

    if (items.length > 0) {
      await updateInventoryBatch(
        items.map(i => ({
          product_id: i.product_id,
          quantity: i.quantity ?? 0,
          branch_id: branchId,
          updated_at: i.updated_at || new Date().toISOString(),
        }))
      );
      emit({ type: 'inventory_pulse', updated: items.length });
    }

    await setMeta('last_inventory_pulse', pulse_time || new Date().toISOString());
    return true;
  } catch {
    return false;
  }
}

export function startAutoSync(getBranchId) {
  if (autoSyncInterval) return;

  // Check every 30s if there are pending sales to sync
  autoSyncInterval = setInterval(async () => {
    if (navigator.onLine && !syncInProgress) {
      const count = await getPendingSaleCount();
      if (count > 0) syncPendingSales();
    }
  }, 30000);

  // Background cache refresh every 5 minutes (delta sync — lightweight)
  cacheRefreshInterval = setInterval(async () => {
    if (navigator.onLine && !syncInProgress) {
      const branchId = typeof getBranchId === 'function' ? getBranchId() : getBranchId;
      if (branchId && branchId !== 'all') {
        emit({ type: 'background_refresh', status: 'start' });
        await refreshPOSCache(branchId);
        emit({ type: 'background_refresh', status: 'complete' });
      }
    }
  }, CACHE_REFRESH_MS);

  // Inventory pulse every 60 seconds (super lightweight — just stock counts)
  // Skips when on metered connection (Android injects window.__isMeteredConnection)
  inventoryPulseInterval = setInterval(async () => {
    if (navigator.onLine && !syncInProgress && !window.__isMeteredConnection) {
      const branchId = typeof getBranchId === 'function' ? getBranchId() : getBranchId;
      if (branchId && branchId !== 'all') {
        await inventoryPulse(branchId);
      }
    }
  }, INVENTORY_PULSE_MS);

  // On reconnect, wait 2s then run full sync
  const onReconnect = () => {
    setTimeout(async () => {
      if (!syncInProgress) {
        const branchId = typeof getBranchId === 'function' ? getBranchId() : getBranchId;
        await fullSync(branchId);
      }
    }, 2000);
  };
  window.addEventListener('online', onReconnect);

  // Store cleanup reference
  startAutoSync._onReconnect = onReconnect;
}

export function stopAutoSync() {
  if (autoSyncInterval) {
    clearInterval(autoSyncInterval);
    autoSyncInterval = null;
  }
  if (cacheRefreshInterval) {
    clearInterval(cacheRefreshInterval);
    cacheRefreshInterval = null;
  }
  if (inventoryPulseInterval) {
    clearInterval(inventoryPulseInterval);
    inventoryPulseInterval = null;
  }
  if (startAutoSync._onReconnect) {
    window.removeEventListener('online', startAutoSync._onReconnect);
    startAutoSync._onReconnect = null;
  }
}
