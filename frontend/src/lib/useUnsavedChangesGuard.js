/**
 * useUnsavedChangesGuard — blocks navigation away from a page that has
 * uncommitted user input. Three blockers fire:
 *
 *   1. In-app navigation (sidebar click, route change) — via react-router
 *      `useBlocker`. The hook returns a `blocker` object the caller renders
 *      `<UnsavedChangesDialog />` against.
 *   2. Browser tab close / refresh — via `beforeunload`.
 *   3. In-page tab/section switches (e.g. UnifiedSalesPage `quote`↔`history`
 *      tab) — caller invokes `requestSafe(action)` from the returned API and
 *      we open the same dialog.
 *
 * For Sales specifically we also accept an `onPark` callback so the dialog
 * can offer a "Park & leave" button that turns the in-progress cart into a
 * draft instead of throwing it away.
 */
import { useEffect, useState, useCallback } from 'react';
import { useBlocker } from 'react-router-dom';

export function useUnsavedChangesGuard({ isDirty, onPark = null, label = 'this section' }) {
  // ── 1. Browser tab close ──────────────────────────────────────────────
  useEffect(() => {
    if (!isDirty) return undefined;
    const handler = (e) => {
      e.preventDefault();
      // Modern browsers ignore custom messages and show their own, but
      // setting returnValue is required to trigger the prompt at all.
      e.returnValue = '';
      return '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  // ── 2. In-app route navigation ─────────────────────────────────────────
  // useBlocker returns { state, proceed, reset } when navigation is
  // intercepted. We only block when actually leaving the current page.
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    isDirty && currentLocation.pathname !== nextLocation.pathname
  );

  // ── 3. In-page (non-route) destructive actions ─────────────────────────
  // pendingAction holds a callback the page wants to run after confirmation
  // (e.g. setMainTab('history')). When set, we render the same dialog.
  const [pendingAction, setPendingAction] = useState(null);
  const [parking, setParking] = useState(false);

  const requestSafe = useCallback((action) => {
    if (!isDirty) {
      action();
      return;
    }
    setPendingAction(() => action);
  }, [isDirty]);

  const isInAppBlocked = blocker?.state === 'blocked';
  const isOpen = isInAppBlocked || pendingAction !== null;

  const onCancel = useCallback(() => {
    if (isInAppBlocked) blocker.reset();
    setPendingAction(null);
  }, [blocker, isInAppBlocked]);

  const onConfirmLeave = useCallback(() => {
    if (isInAppBlocked) blocker.proceed();
    if (pendingAction) {
      const fn = pendingAction;
      setPendingAction(null);
      // Defer one tick so the dialog can close cleanly before the action runs.
      setTimeout(() => fn(), 0);
    }
  }, [blocker, isInAppBlocked, pendingAction]);

  const onParkAndLeave = useCallback(async () => {
    if (!onPark || parking) return;
    setParking(true);
    try {
      await onPark();
      // Park succeeded — proceed.
      if (isInAppBlocked) blocker.proceed();
      if (pendingAction) {
        const fn = pendingAction;
        setPendingAction(null);
        setTimeout(() => fn(), 0);
      }
    } catch {
      // Park failed — stay so the user can decide.
      if (isInAppBlocked) blocker.reset();
      setPendingAction(null);
    } finally {
      setParking(false);
    }
  }, [onPark, blocker, isInAppBlocked, parking, pendingAction]);

  return {
    isOpen,
    onCancel,
    onConfirmLeave,
    onParkAndLeave,
    parking,
    canPark: !!onPark,
    label,
    requestSafe,
  };
}
