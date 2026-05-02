/**
 * useUnsavedChangesGuard — registers a "this page has unsaved work" guard
 * with the global UnsavedChangesProvider so the dialog fires when the
 * user clicks any sidebar link, refreshes the tab, or invokes
 * `requestSafe()` for an in-page destructive switch.
 *
 * No more useBlocker. The legacy `<BrowserRouter>` setup doesn't expose a
 * data router, so we route all blocking through a global click capture
 * (see UnsavedChangesContext) plus a `beforeunload` listener.
 *
 * Usage
 * ─────
 *   const guard = useUnsavedChangesGuard({
 *     isDirty: cart.length > 0,
 *     onPark: async () => { await parkCurrentSale(); },
 *     label: 'Sales',
 *   });
 *   // For in-page destructive switches (tabs, mode toggles, ...):
 *   <button onClick={() => guard.requestSafe(() => setMainTab('history'))} />
 *
 * The page does NOT need to render <UnsavedChangesDialog> itself anymore
 * (the Provider does it). The hook returns a stable `requestSafe` only.
 */
import { useEffect, useRef } from 'react';
import { useUnsavedChangesContext } from '../contexts/UnsavedChangesContext';

let _guardSeq = 0;

export function useUnsavedChangesGuard({ isDirty, onPark = null, label = 'this section' }) {
  const ctx = useUnsavedChangesContext();
  // Stable id per guard instance so the registry can find/replace it.
  const idRef = useRef(null);
  if (!idRef.current) {
    _guardSeq += 1;
    idRef.current = `guard-${_guardSeq}`;
  }
  // Latest values via ref so the registry's getDirty() always sees fresh
  // state without forcing the Provider to re-register on every render.
  const latestRef = useRef({ isDirty, onPark, label });
  latestRef.current = { isDirty, onPark, label };

  useEffect(() => {
    if (!ctx) return undefined;
    const id = idRef.current;
    ctx.register(id, {
      getDirty: () => !!latestRef.current.isDirty,
      onPark: latestRef.current.onPark
        ? () => latestRef.current.onPark()
        : null,
      label: latestRef.current.label,
    });
    return () => ctx.unregister(id);
  }, [ctx]);

  // Re-register when `onPark` becomes available/unavailable so the dialog
  // can correctly decide whether to show the Park button. (label / isDirty
  // changes are picked up via the ref so they don't need re-register.)
  useEffect(() => {
    if (!ctx) return;
    const id = idRef.current;
    ctx.register(id, {
      getDirty: () => !!latestRef.current.isDirty,
      onPark: latestRef.current.onPark
        ? () => latestRef.current.onPark()
        : null,
      label: latestRef.current.label,
    });
  }, [ctx, onPark]);

  return {
    requestSafe: (action) => {
      if (!ctx) { action(); return; }
      ctx.requestSafe(action);
    },
  };
}
