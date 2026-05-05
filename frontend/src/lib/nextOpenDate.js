/**
 * nextOpenDate.js — shared helper for computing the default sale/encoding date
 * across the Web POS (UnifiedSalesPage) and the Terminal POS (TerminalSales).
 *
 * Why this exists
 * ---------------
 * Iter 243: After the Day-Close flow was stabilised (Iter 241 ledger fix),
 * the UI began reliably seeing `lastCloseDate === today` as soon as a cashier
 * closed the drawer at mid-day. That created a dead-lock in the native date
 * input on /sales-new (`min > max` → browser emits empty-string onChange →
 * client-side floor-date guard fires "Cannot encode before system start date").
 *
 * Beyond the immediate bug, we realised we never encoded the business rule
 * the user actually wanted:
 *
 *   "If today is closed, the next sale default to TOMORROW automatically."
 *
 * This helper centralises that rule so Web + Terminal never diverge.
 *
 * Rules
 * -----
 * 1. If `lastCloseDate` is null / not provided → use today.
 * 2. If `lastCloseDate >= today`  → return lastCloseDate + 1 day.
 * 3. Otherwise (today > lastCloseDate) → use today.
 * 4. Future-cap: never return a date more than 1 day past today. This prevents
 *    "forward-dated stock laundering" where a cashier dates a sale far in the
 *    future so stock deducts silently and the sale never appears on any
 *    Z-Report.
 */

const addDays = (isoDate, delta) => {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + delta);
  return d.toISOString().slice(0, 10);
};

export const localTodayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/**
 * Compute the default encoding date.
 *
 * @param {string|null} lastCloseDate  YYYY-MM-DD of the last closed day (or null)
 * @returns {string} YYYY-MM-DD
 */
export function getNextOpenDate(lastCloseDate) {
  const today = localTodayStr();
  if (!lastCloseDate) return today;
  if (lastCloseDate >= today) {
    // Today (or somehow future) is already closed — bump to tomorrow but
    // never more than +1 from today.
    return addDays(today, 1);
  }
  return today;
}

/**
 * The maximum date the user is ALLOWED to encode a sale for.
 * Protects against forward-dated stock laundering (see file header).
 *
 * @param {string|null} lastCloseDate
 * @returns {string} YYYY-MM-DD
 */
export function getMaxAllowedDate(lastCloseDate) {
  const today = localTodayStr();
  if (!lastCloseDate) return today;
  if (lastCloseDate >= today) return addDays(today, 1);
  return today;
}

/**
 * Guard used by both Web and server. True if the candidate date would be
 * a forward-dated sale beyond the allowed window.
 *
 * @param {string} candidate  YYYY-MM-DD
 * @param {string|null} lastCloseDate
 */
export function isForwardDatedBeyondCap(candidate, lastCloseDate) {
  return candidate > getMaxAllowedDate(lastCloseDate);
}
