/**
 * UnsavedChangesContext — global registry of "I have unsaved work" guards
 * that survives across the legacy `<BrowserRouter>` setup.
 *
 * Why this exists
 * ───────────────
 * react-router-dom v7's `useBlocker` requires a data router
 * (`createBrowserRouter` + `<RouterProvider>`). This app uses the
 * declarative `<BrowserRouter>`, so `useBlocker` throws on mount. Migrating
 * the whole router is too risky for now, so this Provider does the job by
 * intercepting in-app navigation at the click level + browser unload.
 *
 * Architecture
 * ────────────
 *   • Pages call `useUnsavedChangesGuard({ isDirty, onPark, label })`.
 *     The hook registers itself with this Provider and returns
 *     `requestSafe(action)` for in-page destructive switches (tab toggles).
 *   • The Provider listens at the document level for any click on an
 *     `<a>` element with an internal href. If ANY registered guard says
 *     dirty, it preventsDefault + opens the confirmation dialog.
 *   • On `beforeunload` (tab close / refresh) it triggers the browser's
 *     native warning when any guard is dirty.
 *   • The dialog (`<UnsavedChangesDialog>`) is rendered once at the
 *     Provider level, fed from the Provider's own state.
 *
 * Limitations
 * ───────────
 *   • Programmatic `navigate(...)` calls inside pages bypass the click
 *     interceptor. Those are typically intentional (after a successful
 *     save), so we don't try to wrap them. If a page ever needs to guard
 *     a programmatic navigate, it can call `requestSafe(() => navigate(...))`
 *     directly.
 *   • Forward/back buttons fire `popstate` and bypass the click handler;
 *     those are guarded only via `beforeunload` warning.
 */
import { createContext, useContext, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import UnsavedChangesDialog from '../components/UnsavedChangesDialog';

const Ctx = createContext(null);

export function UnsavedChangesProvider({ children }) {
  // Registry of active guards. We keep it in a ref so we can read the
  // CURRENT dirty state at click time without re-binding the global click
  // listener on every guard change. State is mirrored to a counter so
  // useMemo callers re-evaluate when the registry mutates.
  const guardsRef = useRef(new Map());
  const [, setVersion] = useState(0);
  const bumpVersion = useCallback(() => setVersion(v => v + 1), []);

  const navigate = useNavigate();

  // Pending leave request — populated when the click interceptor or
  // requestSafe wraps an action and we need user confirmation.
  const [pending, setPending] = useState(null);
  const [parking, setParking] = useState(false);

  // ── Public API for hooks ──────────────────────────────────────────────
  const register = useCallback((id, opts) => {
    guardsRef.current.set(id, opts);
    bumpVersion();
  }, [bumpVersion]);

  const unregister = useCallback((id) => {
    guardsRef.current.delete(id);
    bumpVersion();
  }, [bumpVersion]);

  // Walk the registry and return the FIRST dirty guard, or null. We don't
  // bother trying to merge multiple guards — there is at most one
  // primary form on screen at a time in this app.
  const findDirtyGuard = useCallback(() => {
    for (const [, g] of guardsRef.current.entries()) {
      try { if (g.getDirty()) return g; } catch { /* ignore */ }
    }
    return null;
  }, []);

  // Wrap a non-route in-page action (e.g. setMainTab('history')) with
  // the same confirmation flow.
  const requestSafe = useCallback((action) => {
    const g = findDirtyGuard();
    if (!g) { action(); return; }
    setPending({ kind: 'inpage', action, guard: g });
  }, [findDirtyGuard]);

  // ── beforeunload (tab close / refresh) ────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (findDirtyGuard()) {
        e.preventDefault();
        e.returnValue = '';
        return '';
      }
      return undefined;
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [findDirtyGuard]);

  // ── Click interceptor for in-app links ────────────────────────────────
  // Capture-phase so we beat react-router's onClick handler.
  useEffect(() => {
    const handler = (e) => {
      // Mousewheel / Cmd-click / Ctrl-click open in new tab — let them through.
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      // External / mailto / new-tab links are not blocked.
      const href = a.getAttribute('href') || '';
      const target = a.getAttribute('target') || '';
      if (!href || target === '_blank') return;
      if (href.startsWith('http://') || href.startsWith('https://')) return;
      if (href.startsWith('mailto:') || href.startsWith('tel:')) return;
      if (href.startsWith('#')) return;
      // Opt-out marker — pages can mark a link as guard-bypassing.
      if (a.dataset && a.dataset.skipGuard === 'true') return;

      const g = findDirtyGuard();
      if (!g) return;

      // Same path — nothing to block.
      const url = new URL(href, window.location.origin);
      const targetPath = url.pathname + url.search + url.hash;
      const currentPath = window.location.pathname + window.location.search + window.location.hash;
      if (targetPath === currentPath) return;

      e.preventDefault();
      e.stopPropagation();
      setPending({
        kind: 'route',
        action: () => navigate(targetPath),
        guard: g,
      });
    };
    document.addEventListener('click', handler, true);
    return () => document.removeEventListener('click', handler, true);
  }, [findDirtyGuard, navigate]);

  // ── Dialog action handlers ────────────────────────────────────────────
  const onCancel = useCallback(() => {
    setPending(null);
  }, []);

  const onConfirmLeave = useCallback(() => {
    if (!pending) return;
    const fn = pending.action;
    setPending(null);
    setTimeout(() => fn(), 0);
  }, [pending]);

  const onParkAndLeave = useCallback(async () => {
    if (!pending || !pending.guard?.onPark || parking) return;
    setParking(true);
    try {
      await pending.guard.onPark();
      const fn = pending.action;
      setPending(null);
      setTimeout(() => fn(), 0);
    } catch {
      // Park failed — keep dialog open so the user can decide.
    } finally {
      setParking(false);
    }
  }, [pending, parking]);

  const dialogGuard = pending ? {
    isOpen: true,
    onCancel,
    onConfirmLeave,
    onParkAndLeave,
    parking,
    canPark: !!pending.guard?.onPark,
    label: pending.guard?.label || 'this section',
  } : null;

  // Memoize the context value so consumers (and especially the
  // useUnsavedChangesGuard hook's `useEffect([ctx])`) don't see a fresh
  // reference on every Provider re-render. Without this, calling
  // bumpVersion() inside register/unregister causes the Provider to
  // re-render → new value object → consumer's effect re-fires → calls
  // register() → bumpVersion() → infinite loop ("Maximum update depth
  // exceeded").
  const ctxValue = useMemo(
    () => ({ register, unregister, requestSafe }),
    [register, unregister, requestSafe]
  );

  return (
    <Ctx.Provider value={ctxValue}>
      {children}
      {dialogGuard && <UnsavedChangesDialog guard={dialogGuard} />}
    </Ctx.Provider>
  );
}

/** Internal — used by the hook to subscribe + read the API. */
export function useUnsavedChangesContext() {
  return useContext(Ctx);
}
