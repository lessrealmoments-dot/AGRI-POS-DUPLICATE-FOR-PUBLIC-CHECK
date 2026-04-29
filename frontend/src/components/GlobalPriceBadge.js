import { useState } from 'react';
import { api as defaultApi } from '../contexts/AuthContext';
import { Globe, Check } from 'lucide-react';
import { toast } from 'sonner';

/**
 * GlobalPriceBadge — small amber chip shown when a product's pricing has not
 * yet been reviewed for the current branch. Click to mark reviewed (clears).
 *
 * Props:
 *   productId     — required
 *   branchId      — required
 *   reviewed      — boolean (true = badge hidden)
 *   compact       — small dot-only variant (used on Terminal)
 *   apiClient     — optional axios instance override (Terminal injects its own)
 *   onMarked      — callback after successful ack (parent can refresh local state)
 */
export default function GlobalPriceBadge({ productId, branchId, reviewed, compact = false, apiClient, onMarked }) {
  const client = apiClient || defaultApi;
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(false);

  if (reviewed || hidden) return null;
  if (!productId || !branchId) return null;

  const onClick = async (e) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true);
    try {
      await client.post('/inventory/mark-reviewed', { product_id: productId, branch_id: branchId });
      setHidden(true);
      toast.success('Marked as reviewed');
      onMarked?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to mark reviewed');
    } finally {
      setBusy(false);
    }
  };

  if (compact) {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title="Global Price — not yet reviewed for this branch. Click to mark reviewed."
        data-testid={`global-price-dot-${productId}`}
        className="inline-flex items-center justify-center w-1.5 h-1.5 rounded-full bg-amber-500 ring-2 ring-amber-200 hover:bg-amber-600 transition-colors"
        aria-label="Global price — pending review"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title="Not yet reviewed for this branch. Click to mark reviewed."
      data-testid={`global-price-badge-${productId}`}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-800 border border-amber-200 hover:bg-amber-200 transition-colors group"
    >
      {busy ? (
        <span className="w-2.5 h-2.5 rounded-full border border-amber-700 border-t-transparent animate-spin" />
      ) : (
        <Globe size={10} className="group-hover:hidden" />
      )}
      {!busy && <Check size={10} className="hidden group-hover:inline" />}
      <span className="group-hover:hidden">Global Price</span>
      <span className="hidden group-hover:inline">Mark reviewed</span>
    </button>
  );
}
