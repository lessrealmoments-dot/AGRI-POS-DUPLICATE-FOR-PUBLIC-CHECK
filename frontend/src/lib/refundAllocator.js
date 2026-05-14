/**
 * refundAllocator — frontend mirror of backend's `compute_refund_allocation`.
 *
 * Used by terminal modals (Return & Refund, Update for Incomplete Stock) to
 * preview how a refund will be routed before the cashier confirms:
 *
 *    1. AR FIRST   → shrinks invoice.balance, no money moves
 *    2. DIGITAL    → reverses digital wallet by paid channel/ref number
 *    3. CASH       → debits cashier wallet for the residual
 *
 * Stays intentionally pure — no fetches, no state. Caller hands it an
 * invoice-shape object and a refund amount.
 */

const DIGITAL_METHODS = new Set([
  'gcash', 'maya', 'paymaya', 'bank transfer', 'instapay', 'pesonet',
  'paypal', 'shopee pay', 'grabpay', 'coins.ph', 'seabank',
  'digital', 'e-wallet', 'online transfer', 'mobile payment',
]);

function isDigital(method) {
  if (!method) return false;
  const m = String(method).toLowerCase().trim();
  if (['cash', 'check', 'cheque', 'credit', 'partial', 'split', ''].includes(m)) return false;
  return DIGITAL_METHODS.has(m) || true;
}

function classify(method, fundSource) {
  const m = (method || '').toLowerCase().trim();
  const fs = (fundSource || '').toLowerCase().trim();
  if (m === 'check' || m === 'cheque') return 'check';
  if (fs === 'digital' || isDigital(method)) return 'digital';
  return 'cash';
}

function num(v) { const f = parseFloat(v); return isNaN(f) ? 0 : f; }

export function summarizeInvoicePayments(invoice) {
  const grandTotal = num(invoice?.grand_total);
  const balance = num(invoice?.balance);
  const paymentType = (invoice?.payment_type || '').toLowerCase();
  const paymentMethod = invoice?.payment_method || 'Cash';

  const rows = [];

  // Subtract subsequent payments to derive the initial payment amount.
  const subsequentTotal = (invoice?.payments || [])
    .filter(p => !p?.voided)
    .reduce((s, p) => s + num(p?.amount), 0);
  const initialAmount = Math.max(0, +(num(invoice?.amount_paid) - subsequentTotal).toFixed(2));

  if (initialAmount > 0) {
    if (paymentType === 'split') {
      let cashPart = num(invoice?.cash_amount);
      let digitalPart = num(invoice?.digital_amount);
      if (cashPart <= 0 && digitalPart <= 0) {
        cashPart = initialAmount / 2;
        digitalPart = initialAmount / 2;
      }
      if (cashPart > 0) {
        rows.push({ amount: +cashPart.toFixed(2), method: 'Cash',
                    fund_source: 'cashier', kind: 'cash', source: 'initial-split-cash' });
      }
      if (digitalPart > 0) {
        rows.push({
          amount: +digitalPart.toFixed(2),
          method: invoice?.digital_platform || 'Digital',
          fund_source: 'digital', kind: 'digital',
          platform: invoice?.digital_platform || 'Digital',
          ref_number: invoice?.digital_ref_number || '',
          source: 'initial-split-digital',
        });
      }
    } else {
      const fs = isDigital(paymentMethod) ? 'digital' : 'cashier';
      rows.push({
        amount: +initialAmount.toFixed(2),
        method: paymentMethod,
        fund_source: fs,
        kind: fs === 'digital' ? 'digital' : 'cash',
        platform: fs === 'digital' ? (invoice?.digital_platform || paymentMethod) : '',
        ref_number: fs === 'digital' ? (invoice?.digital_ref_number || '') : '',
        source: 'initial',
      });
    }
  }

  for (const pmt of (invoice?.payments || [])) {
    if (pmt?.voided) continue;
    const amt = num(pmt?.amount);
    if (amt <= 0) continue;
    const method = pmt?.method || 'Cash';
    const fs = pmt?.fund_source || (isDigital(method) ? 'digital' : 'cashier');
    rows.push({
      id: pmt.id || '',
      amount: +amt.toFixed(2),
      method, fund_source: fs,
      kind: classify(method, fs),
      platform: fs === 'digital' ? (pmt.digital_platform || method) : '',
      ref_number: fs === 'digital' ? (pmt.digital_ref_number || pmt.reference || '') : '',
      source: 'subsequent',
    });
  }

  const cashPaid = +rows.filter(r => r.kind === 'cash').reduce((s, r) => s + r.amount, 0).toFixed(2);
  const digitalPaid = +rows.filter(r => r.kind === 'digital').reduce((s, r) => s + r.amount, 0).toFixed(2);
  const checkPaid = +rows.filter(r => r.kind === 'check').reduce((s, r) => s + r.amount, 0).toFixed(2);

  return {
    grand_total: +grandTotal.toFixed(2),
    open_balance: +balance.toFixed(2),
    cash_paid: cashPaid,
    digital_paid: digitalPaid,
    check_paid: checkPaid,
    payment_rows: rows,
  };
}

export function computeRefundAllocation(invoice, refundAmount) {
  const refund = Math.max(0, num(refundAmount));
  const summary = summarizeInvoicePayments(invoice);
  let remaining = refund;

  const arTake = Math.min(remaining, summary.open_balance);
  remaining = +(remaining - arTake).toFixed(2);

  const digitalRefunds = [];
  if (remaining > 0 && summary.digital_paid > 0) {
    const digitalRows = summary.payment_rows.filter(r => r.kind === 'digital');
    for (let i = digitalRows.length - 1; i >= 0; i--) {
      if (remaining <= 0) break;
      const r = digitalRows[i];
      const take = Math.min(remaining, r.amount);
      if (take <= 0) continue;
      digitalRefunds.push({
        amount: +take.toFixed(2),
        method: r.method,
        platform: r.platform || '',
        ref_number: r.ref_number || '',
        payment_id: r.id || '',
      });
      remaining = +(remaining - take).toFixed(2);
    }
  }

  // Cash bounded by what was actually paid in cash.
  const cashResidual = Math.max(0, remaining);
  const cashRefund = +Math.min(cashResidual, summary.cash_paid).toFixed(2);

  const allocated = +(arTake
    + digitalRefunds.reduce((s, d) => s + d.amount, 0)
    + cashRefund).toFixed(2);
  const unalloc = +Math.max(0, refund - allocated).toFixed(2);

  return {
    total: +refund.toFixed(2),
    ar_reduction: +arTake.toFixed(2),
    digital_refunds: digitalRefunds,
    check_refunds: [],
    cash_refund: cashRefund,
    remaining_unallocated: unalloc,
    summary,
  };
}
