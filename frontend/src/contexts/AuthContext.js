import { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import {
  getProducts, getCustomers, getPriceSchemes,
  getInventory, getInventoryItem, getBranchPrice, addPendingSale,
  setOfflineOrg,
} from '../lib/offlineDB';
import { onSyncUpdate } from '../lib/syncManager';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const AuthContext = createContext(null);

export const api = axios.create({ baseURL: `${BACKEND_URL}/api` });

// ── Offline fallback handler ─────────────────────────────────────────────────
export async function hashPinOffline(pin) {
  const encoder = new TextEncoder();
  const data = encoder.encode(pin + '_agripos_kiosk_v1');
  const hash = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function handleOfflineRequest(config) {
  const path = config.url || '';
  const params = config.params || {};
  const method = (config.method || 'get').toLowerCase();

  if (method === 'get') {
    // Products list / search
    if (path === '/products' || path.startsWith('/products?')) {
      let products = await getProducts();
      const q = (params.search || '').toLowerCase();
      if (q) products = products.filter(p =>
        (p.name || '').toLowerCase().includes(q) || (p.sku || '').toLowerCase().includes(q));
      if (params.is_repack !== undefined) {
        const wantRepack = params.is_repack === true || params.is_repack === 'true';
        products = products.filter(p => !!p.is_repack === wantRepack);
      }
      const skip = parseInt(params.skip) || 0;
      const limit = parseInt(params.limit) || 50;
      return { data: { products: products.slice(skip, skip + limit), total: products.length } };
    }

    // Product search-detail (POS search with branch prices + inventory)
    if (path === '/products/search-detail') {
      const q = (params.q || '').toLowerCase();
      const branchId = params.branch_id;
      let products = await getProducts();
      const filtered = products.filter(p =>
        (p.name || '').toLowerCase().includes(q) ||
        (p.sku || '').toLowerCase().includes(q) ||
        (p.barcode || '').includes(params.q || '')
      ).slice(0, 10);

      const enriched = await Promise.all(filtered.map(async p => {
        const inv = await getInventoryItem(p.id);
        const bp = branchId ? await getBranchPrice(p.id) : null;
        const prices = bp?.prices ? { ...(p.prices || {}), ...bp.prices } : (p.prices || {});
        const cost = bp?.cost_price ?? p.cost_price;
        return { ...p, prices, cost_price: cost, available: inv?.quantity ?? 0, reserved: 0, coming: 0 };
      }));
      return { data: enriched };
    }

    // Customers
    if (path === '/customers' || path.startsWith('/customers?')) {
      let customers = await getCustomers();
      const q = (params.search || '').toLowerCase();
      if (q) customers = customers.filter(c => (c.name || '').toLowerCase().includes(q));
      const skip = parseInt(params.skip) || 0;
      const limit = parseInt(params.limit) || 50;
      return { data: { customers: customers.slice(skip, skip + limit), total: customers.length } };
    }

    // Price schemes
    if (path === '/price-schemes') {
      const schemes = await getPriceSchemes();
      return { data: schemes };
    }

    // Inventory
    if (path === '/inventory' || path.startsWith('/inventory?')) {
      const inventory = await getInventory();
      return { data: { items: inventory, total: inventory.length } };
    }

    // Barcode lookup (for kiosk / POS barcode scanning)
    if (path.startsWith('/products/barcode-lookup/')) {
      const barcode = decodeURIComponent(path.split('/products/barcode-lookup/')[1]);
      const products = await getProducts();
      const found = products.find(p => p.barcode === barcode && p.active !== false);
      if (!found) {
        return Promise.reject({ response: { status: 404, data: { detail: 'No product found with this barcode' } } });
      }
      const inv = await getInventoryItem(found.id);
      const branchId = params.branch_id;
      const bp = branchId ? await getBranchPrice(found.id) : null;
      const prices = bp?.prices ? { ...(found.prices || {}), ...bp.prices } : (found.prices || {});
      const cost = bp?.cost_price ?? found.cost_price;
      return { data: { ...found, prices, cost_price: cost, available: inv?.quantity ?? 0 } };
    }
  }

  // Offline sale → queue to pending_sales
  if (method === 'post' && path === '/unified-sale') {
    const sale = typeof config.data === 'string' ? JSON.parse(config.data) : (config.data || {});
    sale.id = sale.id || `offline-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    sale.offline = true;
    sale.timestamp = new Date().toISOString();
    await addPendingSale(sale);
    return {
      data: {
        id: sale.id,
        invoice_number: `OFFLINE-${sale.id.toString().slice(-8).toUpperCase()}`,
        offline: true,
        status: 'queued',
        grand_total: sale.grand_total || 0,
        message: 'Sale saved offline — will sync when connected.',
      }
    };
  }

  // Offline PIN verification (for kiosk unlock / cost reveal / credit sale approval)
  if (method === 'post' && path === '/auth/verify-manager-pin') {
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : (config.data || {});
    const pin = body.pin || '';
    if (!pin) {
      return { data: { valid: false, detail: 'PIN required' } };
    }
    // Primary path: bcrypt-hashed admin_pin from /sync/pos-data (offline robustness Iter 192)
    try {
      const { verifyOfflinePin } = await import('../lib/offlineAuth');
      const result = await verifyOfflinePin(pin);
      if (result.ok) {
        return {
          data: {
            valid: true,
            manager_id: 'offline_admin_pin',
            manager_name: 'Manager (offline)',
            role: 'admin_pin',
            method: result.method || 'admin_pin',
          },
        };
      }
    } catch { /* fall through to legacy kiosk cache */ }
    // Legacy fallback: SHA-256 kiosk_pin_cache (older flow)
    const cachedHash = localStorage.getItem('kiosk_pin_cache');
    if (cachedHash) {
      const inputHash = await hashPinOffline(pin);
      if (inputHash === cachedHash) {
        return { data: { valid: true, manager_id: 'offline_cached', manager_name: 'Manager (offline)', role: 'cached' } };
      }
    }
    return {
      data: {
        valid: false,
        detail: 'Invalid PIN. Offline mode accepts only the system Admin PIN. Sync once while online to refresh credentials.',
      },
    };
  }

  // Can't serve from cache — propagate original error
  return Promise.reject(new Error('Network unavailable'));
}

// Branch filter state - will be set by AuthProvider
let currentBranchFilter = null;
let isMultiBranchUser = false;

// Request interceptor: Add auth token and branch filter
api.interceptors.request.use(config => {
  const token = localStorage.getItem('agripos_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  
  // Auto-append branch_id for GET requests (data filtering)
  // Skip if already has branch_id, or if it's a "global" endpoint
  if (config.method === 'get' && currentBranchFilter && currentBranchFilter !== 'all') {
    const globalEndpoints = ['/branches', '/products', '/price-schemes', '/permissions', '/auth'];
    const isGlobal = globalEndpoints.some(ep => config.url?.startsWith(ep));
    
    if (!isGlobal && !config.params?.branch_id) {
      config.params = { ...config.params, branch_id: currentBranchFilter };
    }
  }
  
  return config;
});

api.interceptors.response.use(
  res => res,
  async err => {
    // Only serve from IndexedDB when the device is genuinely offline.
    // Do NOT intercept when online — backend errors should propagate normally.
    if (!navigator.onLine && !err.response && err.config) {
      try {
        return await handleOfflineRequest(err.config);
      } catch {
        // Cache miss — fall through
      }
    }
    if (err.response?.status === 401) {
      localStorage.removeItem('agripos_token');
      localStorage.removeItem('agripos_user');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('agripos_token'));
  const [loading, setLoading] = useState(true);
  const [branches, setBranches] = useState([]);
  
  // Branch selection: 'all' for consolidated view, or specific branch ID
  const [selectedBranchId, setSelectedBranchId] = useState(() => {
    return localStorage.getItem('agripos_selected_branch') || 'all';
  });

  // Determine if user can view multiple branches
  const canViewAllBranches = useMemo(() => {
    if (!user) return false;
    // Admin/owner with no branch_id restriction can view all
    return user.role === 'admin' || user.branch_id === null;
  }, [user]);

  // Get the current branch object (or null if viewing all)
  const currentBranch = useMemo(() => {
    if (selectedBranchId === 'all') return null;
    return branches.find(b => b.id === selectedBranchId) || null;
  }, [selectedBranchId, branches]);

  // Effective branch ID for API calls
  const effectiveBranchId = useMemo(() => {
    // If user is restricted to a branch, always use that
    if (user?.branch_id) return user.branch_id;
    // Otherwise use selected (could be 'all' or specific ID)
    return selectedBranchId;
  }, [user, selectedBranchId]);

  // Update the interceptor's branch filter
  useEffect(() => {
    currentBranchFilter = effectiveBranchId;
    isMultiBranchUser = canViewAllBranches;
  }, [effectiveBranchId, canViewAllBranches]);

  const fetchUser = useCallback(async () => {
    if (!token) { setLoading(false); return; }
    // ── Offline reload guard ──
    // When the browser is offline (or backend is unreachable), /auth/me will
    // fail and would log the cashier out — losing pending sales context if
    // they accidentally pressed reload. Hydrate from the cached user
    // snapshot so the session survives the reload, and re-validate when
    // the network returns.
    const cachedUserRaw = localStorage.getItem('agripos_user');
    let cachedUser = null;
    try { cachedUser = cachedUserRaw ? JSON.parse(cachedUserRaw) : null; } catch { cachedUser = null; }

    try {
      const res = await api.get('/auth/me');
      setUser(res.data);
      localStorage.setItem('agripos_user', JSON.stringify(res.data));
      // Scope offline DB to this organization
      if (res.data.organization_id) setOfflineOrg(res.data.organization_id);

      // Cache the org's configured timezone so synchronous helpers like
      // `localToday()` can resolve dates in the tenant's wall-clock time
      // (critical for multi-tenant: a PH tenant sees 2026-05-01 while a US
      // tenant still on 2026-04-30 will not drift into each other).
      try {
        const tzRes = await api.get('/settings/timezone');
        if (tzRes.data?.timezone) {
          localStorage.setItem('agribooks.org_tz', tzRes.data.timezone);
        }
      } catch { /* best-effort; falls back to browser-local */ }

      let branchList = [];
      try {
        const branchRes = await api.get('/branches');
        branchList = branchRes.data || [];
        setBranches(branchList);
        localStorage.setItem('agripos_branches', JSON.stringify(branchList));
      } catch {
        // Use cached branches if branch fetch failed
        try {
          const cachedBranches = JSON.parse(localStorage.getItem('agripos_branches') || '[]');
          branchList = cachedBranches;
          setBranches(cachedBranches);
        } catch { /* ignore */ }
      }

      // If user is branch-restricted, force that branch
      if (res.data.branch_id) {
        setSelectedBranchId(res.data.branch_id);
        localStorage.setItem('agripos_selected_branch', res.data.branch_id);
      } else {
        // Admin/owner: use saved preference or default to 'all'
        const saved = localStorage.getItem('agripos_selected_branch');
        if (saved && (saved === 'all' || branchList.some(b => b.id === saved))) {
          setSelectedBranchId(saved);
        } else {
          setSelectedBranchId('all');
        }
      }
    } catch {
      // /auth/me failed — could be offline OR a real auth failure (401/403).
      // We can't distinguish without the response code in this catch, so we
      // do a lightweight ping to /api/health: if reachable AND /auth/me
      // failed, the token is genuinely invalid → log out. If unreachable,
      // hydrate from cache and stay logged in.
      let backendReachable = false;
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 4000);
        const r = await fetch(`${process.env.REACT_APP_BACKEND_URL}/api/health`, {
          method: 'GET', signal: ctrl.signal, cache: 'no-store',
        });
        clearTimeout(t);
        backendReachable = r.ok;
      } catch { backendReachable = false; }

      if (!backendReachable && cachedUser) {
        // Offline reload — keep the user signed in with cached snapshot.
        setUser(cachedUser);
        if (cachedUser.organization_id) setOfflineOrg(cachedUser.organization_id);
        try {
          const cachedBranches = JSON.parse(localStorage.getItem('agripos_branches') || '[]');
          setBranches(cachedBranches);
          if (cachedUser.branch_id) {
            setSelectedBranchId(cachedUser.branch_id);
          } else {
            const saved = localStorage.getItem('agripos_selected_branch');
            setSelectedBranchId(saved || 'all');
          }
        } catch { /* ignore */ }
      } else {
        // Backend reachable but /auth/me rejected → real auth failure.
        localStorage.removeItem('agripos_token');
        localStorage.removeItem('agripos_user');
        setToken(null);
      }
    }
    setLoading(false);
  }, [token]);

  useEffect(() => { fetchUser(); }, [fetchUser]);

  const login = async (identifier, password) => {
    // Support both email and username via the 'email' field (backend checks both)
    const res = await api.post('/auth/login', { email: identifier, password });
    localStorage.setItem('agripos_token', res.data.token);
    localStorage.setItem('agripos_user', JSON.stringify(res.data.user));
    setToken(res.data.token);
    setUser(res.data.user);
    // Scope offline DB to this organization
    if (res.data.user?.organization_id) setOfflineOrg(res.data.user.organization_id);
    
    // Store subscription info if present
    if (res.data.subscription) {
      localStorage.setItem('agripos_subscription', JSON.stringify(res.data.subscription));
    }
    
    // Set branch based on user's access
    if (res.data.user.branch_id) {
      setSelectedBranchId(res.data.user.branch_id);
      localStorage.setItem('agripos_selected_branch', res.data.user.branch_id);
    } else {
      setSelectedBranchId('all');
      localStorage.setItem('agripos_selected_branch', 'all');
    }
    
    return res.data;
  };

  const logout = async () => {
    // Check for pending offline sales before logout
    try {
      const { hasPendingSales } = await import('../lib/offlineDB');
      const pending = await hasPendingSales();
      if (pending) {
        const confirmed = window.confirm(
          'You have unsynced offline sales! If you log out now, they will sync next time you log in.\n\nAre you sure you want to log out?'
        );
        if (!confirmed) return;
      }
    } catch { /* proceed with logout */ }

    localStorage.removeItem('agripos_token');
    localStorage.removeItem('agripos_user');
    localStorage.removeItem('agripos_selected_branch');
    localStorage.removeItem('agripos_subscription');
    setToken(null);
    setUser(null);
    setBranches([]);
    setSelectedBranchId('all');
  };

  const switchBranch = (branchId) => {
    // Only allow switching if user can view all branches
    if (!canViewAllBranches && branchId !== user?.branch_id) return;
    
    setSelectedBranchId(branchId);
    localStorage.setItem('agripos_selected_branch', branchId);
  };

  const hasPerm = (module, action) => {
    if (!user) return false;
    if (user.role === 'admin') return true;
    if (user.permissions?.[module]?.[action]) return true;
    // Check delegated access
    if (delegations[module]) return true;
    return false;
  };

  // Delegation system — temporary section access via TOTP/PIN
  const [delegations, setDelegations] = useState({});

  const requestSectionOverride = async (module, pin) => {
    const res = await api.post('/auth/section-override', {
      module,
      pin,
      current_token: token,
    });
    if (res.data.valid) {
      // Update token with delegation embedded
      const newToken = res.data.token;
      localStorage.setItem('agripos_token', newToken);
      setToken(newToken);
      api.defaults.headers.common['Authorization'] = `Bearer ${newToken}`;
      setDelegations(res.data.delegations || {});
      return res.data;
    }
    throw new Error(res.data.error || 'Invalid PIN/TOTP');
  };

  // On login, extract delegations from token if present
  const extractDelegations = useCallback((tkn) => {
    if (!tkn) return;
    try {
      const payload = JSON.parse(atob(tkn.split('.')[1]));
      if (payload.delegations) setDelegations(payload.delegations);
    } catch {}
  }, []);

  useEffect(() => { extractDelegations(token); }, [token, extractDelegations]);

  // View mode helpers
  const isConsolidatedView = selectedBranchId === 'all' && canViewAllBranches;
  const viewingBranchName = currentBranch?.name || (isConsolidatedView ? 'All Branches' : 'Unknown');

  return (
    <AuthContext.Provider value={{ 
      user, 
      token, 
      loading, 
      login, 
      logout, 
      // Branch management
      branches,
      currentBranch,           // Current branch object (null if viewing all)
      selectedBranchId,        // 'all' or specific branch ID
      effectiveBranchId,       // What to use for API calls
      canViewAllBranches,      // Whether user can switch branches
      isConsolidatedView,      // true if viewing all branches
      viewingBranchName,       // Display name for current view
      switchBranch,
      // Permissions
      hasPerm,
      // Delegation
      delegations,
      requestSectionOverride,
      refreshUser: fetchUser 
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
