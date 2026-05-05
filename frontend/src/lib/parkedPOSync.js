/**
 * Parked Purchase Orders — server-shared (network-only).
 *
 * Unlike Sales (which is offline-first because cashiering MUST keep
 * working when the gateway flickers), POs are deliberate buying
 * actions taken by managers / admins. They're online-only — no
 * IndexedDB queue. If the network is down, parking simply fails with
 * a toast, which is the right UX (the user can always Save Draft
 * instead, which goes through the regular PO endpoint).
 *
 * Branch isolation: list / consume / discard are keyed on branch_id
 * server-side so a buyer switching branches never sees a stale park
 * for a different location.
 */
import { api } from '../contexts/AuthContext';

function newId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'ppo-' + Math.random().toString(36).slice(2) + '-' + Date.now();
}

/** Build a park payload from the current PO draft state. */
export function buildParkPOPayload({
  branchId, label, vendor, header, lines, vendorPrices,
  sourceType, supplyBranchId, receiptSessionId, receiptFileCount,
  itemCount, grandTotal,
}) {
  return {
    id: newId(),
    branch_id: branchId,
    label: label || '',
    vendor: vendor || '',
    header: header || {},
    lines: lines || [],
    vendor_prices: vendorPrices || {},
    source_type: sourceType || 'external',
    supply_branch_id: supplyBranchId || '',
    receipt_session_id: receiptSessionId || '',
    receipt_file_count: receiptFileCount || 0,
    item_count: itemCount || 0,
    grand_total: grandTotal || 0,
  };
}

export async function parkPO(payload) {
  const res = await api.post('/parked-purchase-orders', payload);
  return res.data;
}

export async function loadParkedPOs(branchId) {
  if (!branchId) return [];
  const res = await api.get('/parked-purchase-orders', { params: { branch_id: branchId } });
  return res.data?.parks || [];
}

export async function consumeParkedPO(parkId) {
  const res = await api.post(`/parked-purchase-orders/${parkId}/consume`);
  return res.data;
}

export async function discardParkedPO(parkId, opts = {}) {
  const { pin } = opts;
  const url = pin
    ? `/parked-purchase-orders/${parkId}?pin=${encodeURIComponent(pin)}`
    : `/parked-purchase-orders/${parkId}`;
  const res = await api.delete(url);
  return res.data;
}
