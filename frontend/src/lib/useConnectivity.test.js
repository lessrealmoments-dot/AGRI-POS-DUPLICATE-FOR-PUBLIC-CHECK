/**
 * useConnectivity — Pass 1 hook tests.
 *
 * Verifies:
 *   1. Initial state defaults to 'online' / pendingCount = 0 (with the
 *      hook also triggering an async refresh of the queue size).
 *   2. The hook exposes the manual setters that the processSale retry
 *      loop drives (setConnectivityStatus + setReconnectCountdown).
 *   3. Going offline via the browser `offline` event flips
 *      `connectivityStatus` to 'offline'.
 *   4. Going back online via the browser `online` event flips
 *      `connectivityStatus` to 'online' and triggers the onReconnect
 *      callback after sync.
 *   5. `refreshPendingCount()` re-queries `getPendingSaleCount` and
 *      updates `pendingCount` accordingly.
 *
 * NOTE: we mock the leaf modules (offlineDB, syncManager, connectivity)
 * so we can drive the hook deterministically without touching IndexedDB
 * or making real network calls.
 */

jest.mock('./offlineDB', () => ({
  getPendingSaleCount: jest.fn(),
}));
jest.mock('./syncManager', () => ({
  syncPendingSales: jest.fn(),
  startAutoSync: jest.fn(),
  stopAutoSync: jest.fn(),
}));
jest.mock('./connectivity', () => ({
  isTrueNetworkError: jest.fn(() => true),
  pingBackendHealth: jest.fn(),
}));
jest.mock('sonner', () => ({ toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }) }));

import { renderHook, act } from '@testing-library/react';
import { useConnectivity } from './useConnectivity';
import { getPendingSaleCount } from './offlineDB';
import { syncPendingSales } from './syncManager';
import { pingBackendHealth } from './connectivity';

beforeEach(() => {
  jest.clearAllMocks();
  getPendingSaleCount.mockResolvedValue(0);
  syncPendingSales.mockResolvedValue({ synced: 0 });
  pingBackendHealth.mockResolvedValue(true);
});

describe('useConnectivity', () => {
  test('initial state is online with zero pending sales', async () => {
    const { result } = renderHook(() => useConnectivity());
    expect(result.current.connectivityStatus).toBe('online');
    expect(result.current.reconnectCountdown).toBe(0);
    expect(result.current.pendingCount).toBe(0);
    expect(typeof result.current.setConnectivityStatus).toBe('function');
    expect(typeof result.current.setReconnectCountdown).toBe('function');
    expect(typeof result.current.refreshPendingCount).toBe('function');
  });

  test('setConnectivityStatus and setReconnectCountdown are wired (used by processSale retry loop)', () => {
    const { result } = renderHook(() => useConnectivity());
    act(() => { result.current.setConnectivityStatus('reconnecting'); });
    expect(result.current.connectivityStatus).toBe('reconnecting');
    act(() => { result.current.setReconnectCountdown(7); });
    expect(result.current.reconnectCountdown).toBe(7);
    act(() => { result.current.setReconnectCountdown(0); });
    act(() => { result.current.setConnectivityStatus('online'); });
    expect(result.current.connectivityStatus).toBe('online');
    expect(result.current.reconnectCountdown).toBe(0);
  });

  test('browser `offline` event flips connectivityStatus to offline', () => {
    const { result } = renderHook(() => useConnectivity());
    act(() => { window.dispatchEvent(new Event('offline')); });
    expect(result.current.connectivityStatus).toBe('offline');
    expect(result.current.isOnline).toBe(false);
  });

  test('browser `online` event triggers sync + onReconnect', async () => {
    const onReconnect = jest.fn().mockResolvedValue();
    const { result } = renderHook(() => useConnectivity({ onReconnect }));
    // Pretend we were offline first
    act(() => { window.dispatchEvent(new Event('offline')); });
    expect(result.current.connectivityStatus).toBe('offline');
    // Now reconnect
    await act(async () => {
      window.dispatchEvent(new Event('online'));
      // Let the promise chain in goOnline drain
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.connectivityStatus).toBe('online');
    expect(result.current.isOnline).toBe(true);
    expect(syncPendingSales).toHaveBeenCalled();
    expect(onReconnect).toHaveBeenCalled();
  });

  test('refreshPendingCount re-queries the offline queue and updates state', async () => {
    getPendingSaleCount
      .mockResolvedValueOnce(0)  // initial mount read
      .mockResolvedValueOnce(3); // explicit refresh
    const { result } = renderHook(() => useConnectivity());
    // wait for the mount read to finish
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.pendingCount).toBe(0);
    let returned;
    await act(async () => { returned = await result.current.refreshPendingCount(); });
    expect(returned).toBe(3);
    expect(result.current.pendingCount).toBe(3);
  });

  test('cleanup removes window listeners and stops auto sync', () => {
    const { unmount } = renderHook(() => useConnectivity());
    const removeSpy = jest.spyOn(window, 'removeEventListener');
    unmount();
    expect(removeSpy).toHaveBeenCalledWith('online', expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith('offline', expect.any(Function));
    removeSpy.mockRestore();
  });
});
