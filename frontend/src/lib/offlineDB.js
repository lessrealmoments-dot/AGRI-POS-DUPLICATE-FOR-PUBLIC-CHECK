/**
 * AgriPOS Offline Database (IndexedDB)
 * Stores products, customers, price schemes, inventory, and pending offline sales.
 * Org-scoped: each company gets its own database to prevent cross-tenant data leaks.
 */

let _currentOrgId = null;

function getDBName() {
  return _currentOrgId ? `agripos_offline_${_currentOrgId}` : 'agripos_offline';
}

/** Set the current organization for DB scoping. Call on login. */
export function setOfflineOrg(orgId) {
  if (orgId && orgId !== _currentOrgId) {
    const previousOrg = _currentOrgId;
    _currentOrgId = orgId;
    // If switching to a different org, clear the old org's data (except pending sales)
    if (previousOrg && previousOrg !== orgId) {
      clearOrgCache(previousOrg);
    }
  }
}

/** Get current org ID */
export function getOfflineOrg() {
  return _currentOrgId;
}

/**
 * Clear cached data for a previous org (on org switch).
 * Preserves pending_sales (they must sync first) and meta.
 */
async function clearOrgCache(oldOrgId) {
  const oldDbName = `agripos_offline_${oldOrgId}`;
  try {
    // Check if old DB has pending sales — if so, don't delete it yet
    const db = await new Promise((resolve, reject) => {
      const req = indexedDB.open(oldDbName, DB_VERSION);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    const tx = db.transaction('pending_sales', 'readonly');
    const count = await new Promise((resolve) => {
      const req = tx.objectStore('pending_sales').count();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(0);
    });
    db.close();

    if (count === 0) {
      // Safe to delete — no pending sales
      indexedDB.deleteDatabase(oldDbName);
    }
    // If pending sales exist, keep the DB — they'll sync when that org logs in again
  } catch {
    // Silently ignore — old DB may not exist
  }
}

const DB_VERSION = 4;

const STORES = {
  PRODUCTS: 'products',
  CUSTOMERS: 'customers',
  PRICE_SCHEMES: 'price_schemes',
  INVENTORY: 'inventory',
  BRANCH_PRICES: 'branch_prices',
  PENDING_SALES: 'pending_sales',
  META: 'meta',
};

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(getDBName(), DB_VERSION);
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      // Custom key paths per store
      const keyPaths = {
        [STORES.META]: 'key',
        [STORES.INVENTORY]: 'product_id',
        [STORES.BRANCH_PRICES]: 'product_id', // one branch cached at a time
      };
      Object.values(STORES).forEach((store) => {
        if (!db.objectStoreNames.contains(store)) {
          const keyPath = keyPaths[store] || 'id';
          db.createObjectStore(store, { keyPath });
        }
      });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function clearAndPut(storeName, items) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const store = tx.objectStore(storeName);
    store.clear();
    items.forEach((item) => store.put(item));
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

async function getAll(storeName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const request = tx.objectStore(storeName).getAll();
    request.onsuccess = () => { db.close(); resolve(request.result); };
    request.onerror = () => { db.close(); reject(request.error); };
  });
}

async function putOne(storeName, item) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    tx.objectStore(storeName).put(item);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

async function deleteOne(storeName, key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    tx.objectStore(storeName).delete(key);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

async function countStore(storeName) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const request = tx.objectStore(storeName).count();
    request.onsuccess = () => { db.close(); resolve(request.result); };
    request.onerror = () => { db.close(); reject(request.error); };
  });
}

// ==================== Public API ====================

export async function cacheProducts(products) {
  await clearAndPut(STORES.PRODUCTS, products);
}

/** Upsert a single product (for delta sync) */
export async function putProduct(product) {
  await putOne(STORES.PRODUCTS, product);
}

export async function getProducts() {
  return getAll(STORES.PRODUCTS);
}

export async function cacheCustomers(customers) {
  await clearAndPut(STORES.CUSTOMERS, customers);
}

export async function getCustomers() {
  return getAll(STORES.CUSTOMERS);
}

export async function cachePriceSchemes(schemes) {
  await clearAndPut(STORES.PRICE_SCHEMES, schemes);
}

export async function getPriceSchemes() {
  return getAll(STORES.PRICE_SCHEMES);
}

export async function addPendingSale(sale) {
  await putOne(STORES.PENDING_SALES, sale);
}

export async function getPendingSales() {
  return getAll(STORES.PENDING_SALES);
}

export async function removePendingSale(saleId) {
  await deleteOne(STORES.PENDING_SALES, saleId);
}

export async function getPendingSaleCount() {
  return countStore(STORES.PENDING_SALES);
}

export async function setMeta(key, value) {
  await putOne(STORES.META, { key, value, updated_at: new Date().toISOString() });
}

export async function getMeta(key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.META, 'readonly');
    const request = tx.objectStore(STORES.META).get(key);
    request.onsuccess = () => { db.close(); resolve(request.result?.value || null); };
    request.onerror = () => { db.close(); reject(request.error); };
  });
}

// Branch price overrides cache (keyed by product_id — one branch at a time)
export async function cacheBranchPrices(items) {
  await clearAndPut(STORES.BRANCH_PRICES, items);
}

export async function getBranchPrice(productId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.BRANCH_PRICES, 'readonly');
    const request = tx.objectStore(STORES.BRANCH_PRICES).get(productId);
    request.onsuccess = () => { db.close(); resolve(request.result || null); };
    request.onerror = () => { db.close(); reject(request.error); };
  });
}

// Inventory cache (keyed by product_id — one branch at a time)
export async function cacheInventory(items) {
  await clearAndPut(STORES.INVENTORY, items);
}

export async function getInventory() {
  return getAll(STORES.INVENTORY);
}

export async function getInventoryItem(productId) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.INVENTORY, 'readonly');
    const request = tx.objectStore(STORES.INVENTORY).get(productId);
    request.onsuccess = () => { db.close(); resolve(request.result || null); };
    request.onerror = () => { db.close(); reject(request.error); };
  });
}

/**
 * Deduct inventory locally after an offline sale.
 * This ensures the cashier sees updated stock even when offline.
 */
export async function deductLocalInventory(items) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORES.INVENTORY, STORES.PRODUCTS], 'readwrite');
    const invStore = tx.objectStore(STORES.INVENTORY);
    const prodStore = tx.objectStore(STORES.PRODUCTS);

    items.forEach(item => {
      const productId = item.product_id;
      const qty = parseFloat(item.quantity) || 0;

      // Update inventory store
      const invReq = invStore.get(productId);
      invReq.onsuccess = () => {
        const inv = invReq.result;
        if (inv) {
          inv.quantity = Math.max(0, (inv.quantity || 0) - qty);
          inv.updated_at = new Date().toISOString();
          invStore.put(inv);
        }
      };

      // Update product's "available" field
      const prodReq = prodStore.get(productId);
      prodReq.onsuccess = () => {
        const prod = prodReq.result;
        if (prod) {
          prod.available = Math.max(0, (prod.available || 0) - qty);
          prodStore.put(prod);
        }
      };
    });

    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/** Fast product count — used by TerminalShell to decide instant-load vs full sync */
export async function getProductCount() {
  return countStore(STORES.PRODUCTS);
}

/** Get last sync timestamp from META store */
export async function getLastSyncTime() {
  return getMeta('last_sync');
}

/** Set last sync timestamp */
export async function setLastSyncTime(isoString) {
  return setMeta('last_sync', isoString);
}

/** Merge delta products into existing cache (upsert changed, remove deleted) */
export async function mergeProducts(changed, deletedIds = []) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.PRODUCTS, 'readwrite');
    const store = tx.objectStore(STORES.PRODUCTS);
    // Upsert changed products
    for (const p of changed) store.put(p);
    // Remove deleted products
    for (const id of deletedIds) store.delete(id);
    tx.oncomplete = () => { db.close(); resolve(changed.length + deletedIds.length); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/** Merge delta customers into existing cache */
export async function mergeCustomers(changed) {
  if (!changed.length) return 0;
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.CUSTOMERS, 'readwrite');
    const store = tx.objectStore(STORES.CUSTOMERS);
    for (const c of changed) store.put(c);
    tx.oncomplete = () => { db.close(); resolve(changed.length); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/** Batch update inventory quantities (for lightweight pulse sync) */
export async function updateInventoryBatch(items) {
  if (!items.length) return 0;
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORES.INVENTORY, 'readwrite');
    const store = tx.objectStore(STORES.INVENTORY);
    for (const item of items) store.put(item);
    tx.oncomplete = () => { db.close(); resolve(items.length); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/**
 * Check if there are pending sales that haven't been synced.
 * Used to warn before logout/close.
 */
export async function hasPendingSales() {
  const count = await countStore(STORES.PENDING_SALES);
  return count > 0;
}

