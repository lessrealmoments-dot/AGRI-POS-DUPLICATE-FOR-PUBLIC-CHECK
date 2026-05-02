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
 * Stable identity
 * ───────────────
 * The hook only registers ONCE per mount. Pages typically inline arrow
 * functions for `onPark`, which would otherwise re-register on every
 * keystroke. To stay cheap, we read the latest `isDirty`, `onPark`, and
 * `label` through a ref so the registry's lookup callbacks always see
 * fresh values without forcing the Provider to re-state-update.
 *
 * Usage
 * ─────
 *   const guard = useUnsavedChangesGuard({
 *     isDirty: cart.length > 0,
 *     onPark: async () => { await parkCurrentSale(); },
 *     label: 'Sales',
 *   });
 *   <button onClick={() => guard.requestSafe(() => setMainTab('history'))} />
 *
 * The page does NOT need to render <UnsavedChangesDialog> itself — the
 * Provider does it. The hook returns a stable `requestSafe` only.
 */
import { useEffect, useRef } from 'react';
import { useUnsavedChangesContext } from '../contexts/UnsavedChangesContext';

let _guardSeq = 0;

export function useUnsavedChangesGuard({ isDirty, onPark = null, label = 'this section' }) {
  const ctx = useUnsavedChangesContext();
  const idRef = useRef(null);
  if (!idRef.current) {
    _guardSeq += 1;
    idRef.current = `guard-${_guardSeq}`;
  }
  // Latest values via ref so the registry's getDirty()/onPark() callbacks
  // always see fresh state without forcing the Provider to re-state-update.
  const latestRef = useRef({ isDirty, onPark, label });
  latestRef.current = { isDirty, onPark, label };

  useEffect(() => {
    if (!ctx) return undefined;
    const id = idRef.current;
    ctx.register(id, {
      getDirty: () => !!latestRef.current.isDirty,
      // Provider checks `!!guard.onPark` to decide whether to render the
      // Park button. Wrap dynamically so this stays accurate when the
      // page passes onPark conditionally.
      get onPark() {
        return latestRef.current.onPark ? () => latestRef.current.onPark() : null;
      },
      get label() {
        return latestRef.current.label;
      },
    });
    return () => ctx.unregister(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx]);

  return {
    requestSafe: (action) => {
      if (!ctx) { action(); return; }
      ctx.requestSafe(action);
    },
  };
}
