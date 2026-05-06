/**
 * useDayPlusOne(branchId)
 *
 * Auto-bump hook for date pickers in date-bearing modules.
 *
 * On branchId change (or initial mount), checks `/api/invoices/check-date-closed`
 * for today. If today is closed for that branch, returns:
 *   {
 *     todayClosed: true,
 *     defaultDate:  <today + 1>,   // use this as the date input's initial value
 *     maxDate:      <today + 1>,   // use this as the date input's `max` attr
 *   }
 *
 * Otherwise:
 *   {
 *     todayClosed: false,
 *     defaultDate: <today>,
 *     maxDate:     <today>,
 *   }
 *
 * Mirrors the backend `enforce_max_date` rule applied across Sales / PO /
 * Pay Supplier / Expenses / Receive Payment / Fund Transfers — so the user
 * is never stuck "today is closed" with no way to pick tomorrow.
 *
 * Usage in a page:
 *
 *   const { defaultDate, maxDate, todayClosed } = useDayPlusOne(currentBranch?.id);
 *   useEffect(() => { setDate(prev => prev === todayLocal() ? defaultDate : prev); },
 *            [defaultDate]);
 *   <Input type="date" max={maxDate} value={date} ... />
 *   {todayClosed && <p className="text-amber-600 text-[10px]">Today closed — default rolled to {defaultDate}.</p>}
 */
import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const todayLocal = () => {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};
const tomorrowLocal = () => {
  const d = new Date(); d.setDate(d.getDate() + 1);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};

export function useDayPlusOne(branchId) {
  const [todayClosed, setTodayClosed] = useState(false);
  useEffect(() => {
    if (!branchId) { setTodayClosed(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`${BACKEND_URL}/api/invoices/check-date-closed`, {
          params: { date: todayLocal(), branch_id: branchId },
        });
        if (!cancelled) setTodayClosed(!!r.data.closed);
      } catch { if (!cancelled) setTodayClosed(false); }
    })();
    return () => { cancelled = true; };
  }, [branchId]);

  const defaultDate = todayClosed ? tomorrowLocal() : todayLocal();
  const maxDate     = defaultDate;
  return { todayClosed, defaultDate, maxDate, today: todayLocal(), tomorrow: tomorrowLocal() };
}

export default useDayPlusOne;
