/**
 * useConnectivity — Phase 4 Pass 1 extraction.
 *
 * Centralises the connectivity-related state, effects, and helpers that
 * previously lived inline inside `pages/UnifiedSalesPage.js`. Nothing
 * about the behavior changes — this is purely a relocation so the hook
 * can be unit-tested in isolation and reused by future surfaces
 * (UnifiedSalesPage today, the planned HeldSalesQueue tomorrow, and any
 * other page that needs the same three-state indicator + offline queue
 * size + auto-sync wiring).
 *
 * Reuses (does NOT recreate):
 *   - `./connectivity`              → isTrueNetworkError, pingBackendHealth
 *   - `./syncManager`               → syncPendingSales / startAutoSync / stopAutoSync
 *   - `./offlineDB`                 → getPendingSaleCount
 *
 * Returned API:
 *   isOnline                  legacy boolean kept for places that still read it
 *   connectivityStatus        'online' | 'reconnecting' | 'offline'
 *   setConnectivityStatus     escape hatch for the processSale retry loop
 *   reconnectCountdown        seconds remaining during the 10s grace window
 *   setReconnectCountdown     escape hatch for the processSale retry loop
 *   pendingCount              size of the offline IndexedDB queue
 *   setPendingCount           escape hatch for the offline-save branches
 *   refreshPendingCount()     re-queries `getPendingSaleCount()` and returns it
 *
 * Options:
 *   onReconnect()             optional async callback invoked after a successful
 *                             `online` event + sync. Used by UnifiedSalesPage to
 *                             call `loadData(true)` so the product grid refreshes.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { toast } from 'sonner';
import { isTrueNetworkError, pingBackendHealth } from './connectivity';
import { syncPendingSales, startAutoSync, stopAutoSync } from './syncManager';
import { getPendingSaleCount } from './offlineDB';

// Re-export the underlying primitives so callers can either use the hook
// for stateful behavior or import the helpers directly for one-shot
// classification (e.g., inside an inline catch block).
export { isTrueNetworkError, pingBackendHealth };

const HEALTH_PROBE_INTERVAL_MS = 30000;
const HEALTH_PROBE_TIMEOUT_MS = 3000;

export function useConnectivity({ onReconnect } = {}) {
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [connectivityStatus, setConnectivityStatus] = useState('online');
  const [reconnectCountdown, setReconnectCountdown] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);

  // Mirror the countdown into a ref so the 30s health probe interval
  // (which captures its closure once at mount) can read the latest value
  // and avoid stomping on the in-flight retry loop.
  const reconnectCountdownRef = useRef(0);
  useEffect(() => { reconnectCountdownRef.current = reconnectCountdown; }, [reconnectCountdown]);

  const onReconnectRef = useRef(onReconnect);
  useEffect(() => { onReconnectRef.current = onReconnect; }, [onReconnect]);

  const refreshPendingCount = useCallback(async () => {
    const count = await getPendingSaleCount();
    setPendingCount(count);
    return count;
  }, []);

  useEffect(() => {
    const goOnline = async () => {
      setIsOnline(true);
      setConnectivityStatus('online');
      toast.success('Back online! Syncing...');
      const result = await syncPendingSales();
      if (result?.synced > 0) toast.success(`${result.synced} sale(s) synced!`);
      const count = await getPendingSaleCount();
      setPendingCount(count);
      if (onReconnectRef.current) {
        try { await onReconnectRef.current(); } catch { /* swallow */ }
      }
    };
    const goOffline = () => {
      setIsOnline(false);
      setConnectivityStatus('offline');
      toast('Offline Mode - Sales saved locally', { duration: 4000 });
    };
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    startAutoSync();

    // Phase 4A.1 — Backend reachability probe. The browser `online`
    // event only knows about the LAN; it cannot tell the difference
    // between "WiFi is connected to a router with no internet" and a
    // real upstream outage. Probe /api/health periodically so the
    // indicator can downgrade from 'online' to 'offline' without the
    // cashier hitting Submit first. Skipped while the processSale
    // retry loop is active (it owns its own pinging cadence).
    let cancelled = false;
    const probeNow = async () => {
      if (cancelled) return;
      if (reconnectCountdownRef.current > 0) return;
      const browserOnline = navigator.onLine;
      if (!browserOnline) { setConnectivityStatus('offline'); return; }
      const healthy = await pingBackendHealth(HEALTH_PROBE_TIMEOUT_MS);
      if (cancelled) return;
      setConnectivityStatus(healthy ? 'online' : 'offline');
    };
    const probeTimer = setInterval(probeNow, HEALTH_PROBE_INTERVAL_MS);

    // Initial offline-queue size — keeps the pending-sync pill correct
    // immediately on mount instead of waiting for the first offline save.
    getPendingSaleCount().then(setPendingCount).catch(() => {});

    return () => {
      cancelled = true;
      clearInterval(probeTimer);
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
      stopAutoSync();
    };
  // Mount-once, exactly as before. onReconnect is read through a ref so
  // changes to the callback do not re-bind window listeners or restart
  // the probe interval.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    isOnline,
    connectivityStatus,
    setConnectivityStatus,
    reconnectCountdown,
    setReconnectCountdown,
    pendingCount,
    setPendingCount,
    refreshPendingCount,
  };
}
