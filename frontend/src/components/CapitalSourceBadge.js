import React from 'react';
import { Badge } from './ui/badge';
import { Sparkles, Pencil, TrendingUp, Globe } from 'lucide-react';

/**
 * CapitalSourceBadge — small visual indicator showing where a capital number
 * came from. Helps cashiers and managers understand WHY ₱20 vs ₱24 instead of
 * suspecting a bug.
 *
 *  source values (from backend):
 *    - "derived_from_parent" → repack capital live from parent's branch capital
 *    - "manual"               → admin-set branch override
 *    - "po_received"          → automatic update from PO receipt
 *    - "transfer_received"    → automatic update from branch transfer receipt
 *    - "" / null              → falling back to global product.cost_price
 */
export default function CapitalSourceBadge({ source, dataTestId }) {
  let label, color, Icon, title;
  switch (source) {
    case 'derived_from_parent':
      label = 'Live (parent)';
      color = 'bg-emerald-100 text-emerald-700 hover:bg-emerald-100';
      Icon = Sparkles;
      title = 'Computed live from this branch\'s parent capital';
      break;
    case 'po_received':
      label = 'PO';
      color = 'bg-sky-100 text-sky-700 hover:bg-sky-100';
      Icon = TrendingUp;
      title = 'Auto-updated from a Purchase Order receipt';
      break;
    case 'transfer_received':
      label = 'Transfer';
      color = 'bg-indigo-100 text-indigo-700 hover:bg-indigo-100';
      Icon = TrendingUp;
      title = 'Auto-updated from a Branch Transfer receipt';
      break;
    case 'manual':
      label = 'Manual';
      color = 'bg-slate-100 text-slate-700 hover:bg-slate-100';
      Icon = Pencil;
      title = 'Manually set as a branch override';
      break;
    default:
      label = 'Global';
      color = 'bg-amber-100 text-amber-700 hover:bg-amber-100';
      Icon = Globe;
      title = 'Falling back to the global product capital (no branch-specific data yet)';
  }
  return (
    <Badge className={`text-[9px] py-0 px-1.5 inline-flex items-center gap-1 font-normal ${color}`} title={title} data-testid={dataTestId}>
      <Icon size={9} /> {label}
    </Badge>
  );
}
