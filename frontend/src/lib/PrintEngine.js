/**
 * PrintEngine v2 — Professional receipt/invoice generator
 * Generates print-ready HTML for thermal (58mm) and full-page (8.5×11) documents.
 * 
 * Document types: order_slip, trust_receipt, purchase_order, branch_transfer,
 *                 expense_voucher, return_slip, statement
 * 
 * Usage: PrintEngine.print({ type, data, format, businessInfo, docCode })
 */

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';

const TAX_DISCLAIMER = 'THIS DOCUMENT IS NOT VALID FOR CLAIMING INPUT TAX. THIS IS NOT AN OFFICIAL RECEIPT, PLEASE ASK FOR RECEIPT UPON PAYMENT.';

function formatPHP(v) {
  const n = parseFloat(v) || 0;
  return `₱${n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDate(d) {
  if (!d) return '';
  try { return new Date(d).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' }); }
  catch { return d; }
}

function fmtDateTime(d) {
  if (!d) return '';
  try { return new Date(d).toLocaleString('en-PH', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch { return d; }
}

// Shows date + time only when the string has a time component (contains 'T').
// Date-only strings (e.g. "2026-04-06") show date only — avoids UTC-midnight-to-PHT-8AM bug.
function fmtDateMaybeTime(d) {
  if (!d) return '';
  if (typeof d === 'string' && d.includes('T')) return fmtDateTime(d);
  return fmtDate(d);
}

// ── QR Code helper (uses public API for print-friendly inline image) ────────
function qrImgTag(code, size = 100) {
  if (!code) return '';
  const url = `${window.location.origin}/doc/${code}`;
  const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(url)}&margin=2`;
  return `
    <div class="qr-block">
      <img src="${qrUrl}" alt="QR" width="${size}" height="${size}" />
      <div class="qr-code-text">${code}</div>
      <div class="qr-hint">Scan to view document</div>
    </div>`;
}

// ── Thermal Receipt CSS (58mm / ~384px — use full WebView width; printer still has ~1–2mm dead zone) ──
// KEY: use width:100% not width:384px — on high-DPI Android the CSS viewport is smaller than 384px
// so a fixed 384px body overflows and gets clipped. 100% fills exactly the WebView's CSS viewport.
const thermalCSS = `
  @page { size: 58mm auto; margin: 0; }
  * { margin: 0; padding: 0; box-sizing: border-box;
      -webkit-font-smoothing: none;
      -moz-osx-font-smoothing: unset;
      text-rendering: optimizeSpeed; }
  html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
  body { font-family: 'Courier New', monospace; font-size: 13px; font-weight: normal; line-height: 1.35; padding: 2px 4px; color: #000; word-wrap: break-word; overflow-wrap: anywhere; }
  .header { text-align: center; margin-bottom: 5px; }
  .header .biz-name { font-size: 15px; font-weight: bold; text-transform: uppercase; line-height: 1.2; }
  .header .biz-detail { font-size: 11px; font-weight: normal; line-height: 1.25; }
  .doc-title { text-align: center; font-size: 14px; font-weight: bold; border-top: 1px solid #000; border-bottom: 1px solid #000; padding: 4px 0; margin: 4px 0; letter-spacing: 0.5px; }
  .meta-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 4px; font-size: 11px; }
  .meta-row > span { min-width: 0; }
  .meta-row > span:last-child { text-align: right; }
  .meta-row .label { color: #000; flex-shrink: 0; }
  .sep { border-top: 1px solid #000; margin: 4px 0; }
  .items-table { width: 100%; table-layout: fixed; font-size: 11px; border-collapse: collapse; }
  .items-table td { padding: 2px 0; vertical-align: top; word-break: break-word; }
  .items-table .item-name { font-weight: bold; font-size: 12px; }
  .items-table .item-detail { width: 58%; padding-left: 4px; font-size: 10px; color: #000; }
  .items-table .item-total { width: 42%; text-align: right; font-weight: bold; white-space: nowrap; }
  .totals { margin-top: 5px; }
  .totals .row { display: flex; justify-content: space-between; gap: 4px; font-size: 11px; padding: 2px 0; }
  .totals .row > span:last-child { text-align: right; white-space: nowrap; }
  .totals .grand { font-size: 15px; font-weight: bold; border-top: 2px solid #000; padding-top: 4px; margin-top: 2px; }
  .payment-info { margin-top: 5px; font-size: 11px; }
  .trust-terms { margin-top: 6px; font-size: 9px; line-height: 1.25; border-top: 1px solid #000; padding-top: 4px; word-break: break-word; }
  .trust-terms .terms-title { font-weight: bold; font-size: 10px; margin-bottom: 2px; text-align: center; }
  .signature-line { margin-top: 10px; text-align: center; }
  .signature-line .line { border-top: 1px solid #000; width: 70%; margin: 0 auto; }
  .signature-line .sig-label { font-size: 9px; color: #000; margin-top: 2px; }
  .footer { text-align: center; font-size: 9px; color: #000; margin-top: 6px; border-top: 1px solid #000; padding-top: 4px; line-height: 1.25; }
  .qr-block { text-align: center; margin: 6px 0 4px; }
  .qr-block img { display: block; margin: 0 auto; max-width: 100%; height: auto; image-rendering: pixelated; }
  .qr-code-text { font-size: 12px; font-weight: bold; letter-spacing: 1px; margin-top: 2px; word-break: break-all; }
  .qr-hint { font-size: 9px; color: #000; }
  @media print { body { width: 100%; } }
`;

// ── Full Page CSS (Professional Template) ───────────────────────────────────
const fullPageCSS = `
  @page { size: 8.5in 11in; margin: 0.6in 0.7in; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 11px; line-height: 1.5; color: #222; }

  /* Header: company left, doc info right */
  .page-header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 14px; border-bottom: 3px solid #1A4D2E; margin-bottom: 18px; }
  .page-header .company { }
  .page-header .company .biz-name { font-size: 24px; font-weight: 800; color: #1A4D2E; letter-spacing: -0.5px; line-height: 1.1; }
  .page-header .company .biz-detail { font-size: 10px; color: #666; margin-top: 2px; }
  .page-header .doc-meta { text-align: right; }
  .page-header .doc-meta .doc-type { font-size: 13px; font-weight: 700; color: #1A4D2E; text-transform: uppercase; letter-spacing: 2px; }
  .page-header .doc-meta .doc-number { font-size: 18px; font-weight: 800; color: #222; margin-top: 2px; }
  .page-header .doc-meta .doc-date { font-size: 10px; color: #888; margin-top: 2px; }

  /* Info boxes */
  .info-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .info-box { background: #f8faf8; border: 1px solid #e2e8e2; border-radius: 6px; padding: 12px 14px; }
  .info-box .box-label { font-size: 9px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
  .info-box .box-value { font-size: 14px; font-weight: 700; color: #1A4D2E; }
  .info-box .box-sub { font-size: 10px; color: #666; margin-top: 2px; }

  /* Meta grid */
  .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 24px; margin-bottom: 18px; font-size: 11px; }
  .meta-grid .m-label { color: #888; font-size: 10px; }
  .meta-grid .m-value { font-weight: 600; color: #333; }

  /* Items table */
  .items-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
  .items-table thead th { background: #1A4D2E; color: #fff; padding: 8px 10px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; text-align: left; }
  .items-table thead th.r { text-align: right; }
  .items-table thead th.c { text-align: center; }
  .items-table tbody td { padding: 7px 10px; font-size: 11px; border-bottom: 1px solid #eee; }
  .items-table tbody tr:nth-child(even) { background: #fafcfa; }
  .items-table tbody td.r { text-align: right; font-family: 'Courier New', monospace; }
  .items-table tbody td.c { text-align: center; }
  .items-table tbody td .item-sub { font-size: 9px; color: #999; font-family: monospace; }

  /* Totals */
  .totals-area { display: flex; justify-content: flex-end; margin-bottom: 20px; }
  .totals-box { width: 260px; border: 1px solid #e2e8e2; border-radius: 6px; overflow: hidden; }
  .totals-box .t-row { display: flex; justify-content: space-between; padding: 6px 12px; font-size: 11px; border-bottom: 1px solid #f0f0f0; }
  .totals-box .t-row:last-child { border-bottom: none; }
  .totals-box .t-grand { background: #1A4D2E; color: #fff; font-weight: 700; font-size: 13px; padding: 8px 12px; }
  .totals-box .t-highlight { background: #e8f5e9; color: #1A4D2E; font-weight: 600; }

  /* Notes */
  .notes-box { font-size: 10px; color: #555; margin-bottom: 16px; padding: 8px 12px; background: #fffde7; border: 1px solid #fff3cd; border-radius: 4px; }

  /* Signatures */
  .sig-row { display: flex; justify-content: space-between; margin-top: 48px; gap: 24px; }
  .sig-block { text-align: center; flex: 1; }
  .sig-block .sig-line { border-bottom: 1px solid #333; margin-bottom: 4px; height: 28px; }
  .sig-block .sig-label { font-size: 9px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }

  /* QR */
  .qr-block { text-align: center; }
  .qr-block img { display: block; margin: 0 auto; }
  .qr-code-text { font-size: 12px; font-weight: 700; letter-spacing: 3px; margin-top: 4px; color: #333; }
  .qr-hint { font-size: 8px; color: #999; }

  /* Footer */
  .page-footer { text-align: center; font-size: 8px; color: #aaa; margin-top: 24px; padding-top: 10px; border-top: 1px solid #eee; }
  .page-footer .thank-you { font-size: 12px; font-weight: 600; color: #1A4D2E; margin-bottom: 4px; }

  /* Payment info */
  .payment-box { font-size: 10px; padding: 8px 12px; background: #f9f9f9; border: 1px solid #eee; border-radius: 4px; margin-bottom: 12px; }

  @media print { body { max-width: none; } }
`;

// ── Build page header ───────────────────────────────────────────────────────
function buildPageHeader(biz, docType, docNumber, date, extraLines = []) {
  let companyHtml = `<div class="biz-name">${biz.business_name || 'AgriBooks'}</div>`;
  if (biz.address) companyHtml += `<div class="biz-detail">${biz.address}</div>`;
  if (biz.phone) companyHtml += `<div class="biz-detail">${biz.phone}</div>`;
  if (biz.tin) companyHtml += `<div class="biz-detail">TIN: ${biz.tin}</div>`;

  let metaHtml = `<div class="doc-type">${docType}</div>`;
  metaHtml += `<div class="doc-number">${docNumber}</div>`;
  metaHtml += `<div class="doc-date">${fmtDate(date)}</div>`;
  for (const line of extraLines) {
    metaHtml += `<div class="doc-date">${line}</div>`;
  }

  return `<div class="page-header"><div class="company">${companyHtml}</div><div class="doc-meta">${metaHtml}</div></div>`;
}

function buildThermalHeader(biz) {
  const parts = [];
  parts.push(`<div class="biz-name">${biz.business_name || 'AgriBooks'}</div>`);
  if (biz.address) parts.push(`<div class="biz-detail">${biz.address}</div>`);
  if (biz.phone) parts.push(`<div class="biz-detail">${biz.phone}</div>`);
  if (biz.tin) parts.push(`<div class="biz-detail">TIN: ${biz.tin}</div>`);
  return `<div class="header">${parts.join('')}</div>`;
}

// ── Thermal item list ───────────────────────────────────────────────────────
function buildItemsThermal(items) {
  let html = '<table class="items-table">';
  for (const item of items) {
    const qty = parseFloat(item.quantity || item.qty) || 0;
    const rate = parseFloat(item.rate || item.unit_price || item.price || item.transfer_capital) || 0;
    const total = parseFloat(item.total) || (qty * rate);
    html += `<tr><td class="item-name" colspan="2">${item.product_name || item.description || ''}</td></tr>`;
    html += `<tr><td class="item-detail">${qty} x ${formatPHP(rate)}</td><td class="item-total">${formatPHP(total)}</td></tr>`;
  }
  html += '</table>';
  return html;
}

// ═══════════════════════════════════════════════════════════════════════════
//  FULL PAGE DOCUMENTS
// ═══════════════════════════════════════════════════════════════════════════

// ── Order Slip (Sales) ──────────────────────────────────────────────────────
function orderSlipFullPage(data, biz, docCode) {
  const inv = data;
  let html = buildPageHeader(biz, 'Order Slip', inv.invoice_number || '', inv.created_at || inv.order_date, [
    inv.cashier_name ? `Cashier: ${inv.cashier_name}` : '',
  ].filter(Boolean));

  // Customer info box (only if not walk-in)
  if (inv.customer_name && inv.customer_name !== 'Walk-in') {
    html += `<div class="info-row"><div class="info-box"><div class="box-label">Customer</div><div class="box-value">${inv.customer_name}</div>`;
    if (inv.customer_address) html += `<div class="box-sub">${inv.customer_address}</div>`;
    if (inv.customer_phone) html += `<div class="box-sub">${inv.customer_phone}</div>`;
    html += `</div><div class="info-box"><div class="box-label">Payment</div><div class="box-value">${inv.payment_method || 'Cash'}</div>`;
    if (inv.terms && inv.terms !== 'COD') html += `<div class="box-sub">Terms: ${inv.terms}</div>`;
    if (inv.due_date) html += `<div class="box-sub">Due: ${fmtDate(inv.due_date)}</div>`;
    html += `</div></div>`;
  }

  // Items table
  const items = inv.items || [];
  html += '<table class="items-table"><thead><tr>';
  html += '<th style="width:5%">#</th><th>Item</th><th class="c" style="width:10%">Qty</th><th class="r" style="width:15%">Price</th>';
  if (items.some(i => parseFloat(i.discount_amount) > 0)) html += '<th class="r" style="width:12%">Discount</th>';
  html += '<th class="r" style="width:15%">Total</th>';
  html += '</tr></thead><tbody>';
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty = parseFloat(item.quantity) || 0;
    const rate = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const disc = parseFloat(item.discount_amount) || 0;
    const total = parseFloat(item.total) || (qty * rate - disc);
    html += `<tr><td class="c">${i + 1}</td><td>${item.product_name || ''}</td><td class="c">${qty}</td><td class="r">${formatPHP(rate)}</td>`;
    if (items.some(it => parseFloat(it.discount_amount) > 0)) html += `<td class="r">${disc > 0 ? formatPHP(disc) : '-'}</td>`;
    html += `<td class="r" style="font-weight:600">${formatPHP(total)}</td></tr>`;
  }
  html += '</tbody></table>';

  // Totals + QR side by side
  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 100);
  html += '<div class="totals-box">';
  html += `<div class="t-row"><span>Subtotal</span><span>${formatPHP(inv.subtotal)}</span></div>`;
  if (inv.overall_discount > 0) html += `<div class="t-row"><span>Discount</span><span>-${formatPHP(inv.overall_discount)}</span></div>`;
  if (inv.freight > 0) html += `<div class="t-row"><span>Freight</span><span>${formatPHP(inv.freight)}</span></div>`;
  html += `<div class="t-row t-grand"><span>TOTAL</span><span>${formatPHP(inv.grand_total)}</span></div>`;
  if (inv.amount_paid > 0) html += `<div class="t-row"><span>Amount Paid</span><span>${formatPHP(inv.amount_paid)}</span></div>`;
  const balance = (inv.grand_total || 0) - (inv.amount_paid || 0);
  if (balance > 0 && inv.payment_type === 'credit') html += `<div class="t-row" style="color:#c00;font-weight:600"><span>Balance Due</span><span>${formatPHP(balance)}</span></div>`;
  html += '</div></div>';

  // Acknowledgment + Signature block
  const today = fmtDate(new Date().toISOString());
  html += `<div style="margin-top:32px;padding-top:16px;border-top:1px solid #eee;">`;
  html += `<p style="font-size:10px;color:#444;margin-bottom:28px;line-height:1.5">I acknowledge receipt of the items listed above in good physical condition and complete.</p>`;
  html += `<div class="sig-row">`;
  html += `<div class="sig-block"><div class="sig-line"></div><div class="sig-label">Customer Signature</div></div>`;
  html += `<div class="sig-block"><div class="sig-line"></div><div class="sig-label">Printed Name</div></div>`;
  html += `<div class="sig-block"><div class="sig-line" style="display:flex;align-items:flex-end;justify-content:center;padding-bottom:2px;font-size:11px;color:#333">${today}</div><div class="sig-label">Date</div></div>`;
  html += `</div></div>`;

  // Footer
  html += `<div class="page-footer"><div class="thank-you">Thank you for your business!</div><div style="font-size:8px;font-style:italic;color:#999;margin-top:4px">${TAX_DISCLAIMER}</div></div>`;
  return html;
}

// ── Charge Agreement (Credit / Partial Sales) ──────────────────────────────
function trustReceiptFullPage(data, biz, docCode) {
  const inv = data;
  let html = buildPageHeader(biz, 'Charge Agreement', inv.invoice_number || '', inv.order_date || inv.created_at, [
    inv.terms && inv.terms !== 'COD' ? `Terms: ${inv.terms}` : '',
    inv.due_date ? `Due: ${fmtDate(inv.due_date)}` : '',
  ].filter(Boolean));

  html += `<div class="info-row">`;
  html += `<div class="info-box"><div class="box-label">Customer</div><div class="box-value">${inv.customer_name || ''}</div>`;
  if (inv.customer_address) html += `<div class="box-sub">${inv.customer_address}</div>`;
  if (inv.customer_phone) html += `<div class="box-sub">${inv.customer_phone}</div>`;
  html += `</div>`;
  html += `<div class="info-box"><div class="box-label">Cashier</div><div class="box-value">${inv.cashier_name || ''}</div></div>`;
  html += `</div>`;

  // Items
  const items = inv.items || [];
  html += '<table class="items-table"><thead><tr>';
  html += '<th style="width:5%">#</th><th>Item</th><th class="c" style="width:10%">Qty</th><th class="r" style="width:15%">Price</th><th class="r" style="width:15%">Total</th>';
  html += '</tr></thead><tbody>';
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty = parseFloat(item.quantity) || 0;
    const rate = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const total = parseFloat(item.total) || (qty * rate);
    html += `<tr><td class="c">${i + 1}</td><td>${item.product_name || ''}</td><td class="c">${qty}</td><td class="r">${formatPHP(rate)}</td><td class="r" style="font-weight:600">${formatPHP(total)}</td></tr>`;
  }
  html += '</tbody></table>';

  // Totals + QR
  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 100);
  html += '<div class="totals-box">';
  html += `<div class="t-row"><span>Subtotal</span><span>${formatPHP(inv.subtotal)}</span></div>`;
  if (inv.overall_discount > 0) html += `<div class="t-row"><span>Discount</span><span>-${formatPHP(inv.overall_discount)}</span></div>`;
  html += `<div class="t-row t-grand"><span>TOTAL</span><span>${formatPHP(inv.grand_total)}</span></div>`;
  if (inv.amount_paid > 0) html += `<div class="t-row"><span>Paid</span><span>${formatPHP(inv.amount_paid)}</span></div>`;
  if (inv.balance > 0) html += `<div class="t-row" style="color:#c00;font-weight:700"><span>Balance Due</span><span>${formatPHP(inv.balance)}</span></div>`;
  html += '</div></div>';

  // Trust terms
  const terms = (biz.trust_receipt_terms || '').replace('{business_name}', biz.business_name || '');
  if (terms) {
    html += `<div style="margin:16px 0;font-size:9px;line-height:1.4;border:1px solid #ddd;padding:10px;border-radius:4px"><div style="font-weight:bold;font-size:10px;margin-bottom:4px;text-transform:uppercase">Terms and Conditions</div><p>${terms}</p></div>`;
  }

  // Disclaimer
  html += `<div style="margin:16px 0 0;padding:8px 12px;border:1px solid #e0e0e0;border-radius:4px;background:#fafafa;text-align:center">`;
  html += `<p style="font-size:8px;font-style:italic;color:#888;line-height:1.4">${TAX_DISCLAIMER}</p>`;
  html += `</div>`;

  // Signatures
  html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Authorized Representative</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Customer Signature &amp; Printed Name</div></div></div>';
  return html;
}

// ── Purchase Order ──────────────────────────────────────────────────────────
function purchaseOrderFullPage(data, biz, docCode) {
  const po = data;
  let html = buildPageHeader(biz, 'Purchase Order', po.po_number || '', po.purchase_date, [
    `Status: ${(po.status || '').toUpperCase()}`,
  ]);

  html += `<div class="info-row">`;
  html += `<div class="info-box"><div class="box-label">Supplier</div><div class="box-value">${po.vendor || ''}</div>`;
  if (po.dr_number) html += `<div class="box-sub">DR #: ${po.dr_number}</div>`;
  html += `</div>`;
  html += `<div class="info-box"><div class="box-label">Payment</div><div class="box-value">${po.po_type === 'cash' ? 'Cash' : po.terms_label || 'Terms'}</div>`;
  html += `<div class="box-sub">${po.payment_status || 'Unpaid'}</div>`;
  if (po.due_date) html += `<div class="box-sub">Due: ${fmtDate(po.due_date)}</div>`;
  html += `</div></div>`;

  if (po.notes) html += `<div class="notes-box"><strong>Notes:</strong> ${po.notes}</div>`;

  // Items
  const items = po.items || [];
  html += '<table class="items-table"><thead><tr>';
  html += '<th style="width:5%">#</th><th>Item</th><th class="c" style="width:10%">Qty</th><th class="r" style="width:15%">Unit Cost</th><th class="r" style="width:15%">Total</th>';
  html += '</tr></thead><tbody>';
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty = parseFloat(item.quantity) || 0;
    const rate = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const total = parseFloat(item.total) || (qty * rate);
    html += `<tr><td class="c">${i + 1}</td><td>${item.product_name || item.description || ''}</td><td class="c">${qty}</td><td class="r">${formatPHP(rate)}</td><td class="r" style="font-weight:600">${formatPHP(total)}</td></tr>`;
  }
  html += '</tbody></table>';

  // Totals + QR
  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 100);
  html += '<div class="totals-box">';
  html += `<div class="t-row"><span>Subtotal</span><span>${formatPHP(po.subtotal || po.line_subtotal)}</span></div>`;
  if (po.overall_discount_amount > 0) html += `<div class="t-row"><span>Discount</span><span>-${formatPHP(po.overall_discount_amount)}</span></div>`;
  if (po.freight > 0) html += `<div class="t-row"><span>Freight</span><span>${formatPHP(po.freight)}</span></div>`;
  if (po.tax_amount > 0) html += `<div class="t-row"><span>VAT (${po.tax_rate}%)</span><span>${formatPHP(po.tax_amount)}</span></div>`;
  html += `<div class="t-row t-grand"><span>Grand Total</span><span>${formatPHP(po.grand_total)}</span></div>`;
  if (po.balance > 0) html += `<div class="t-row" style="color:#c00;font-weight:600"><span>Balance</span><span>${formatPHP(po.balance)}</span></div>`;
  html += '</div></div>';

  // Signatures
  html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Prepared By</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Received By</div></div></div>';
  html += `<div class="page-footer"><div class="thank-you">Thank you!</div>AgriBooks — Purchase Order</div>`;
  return html;
}

// ── Branch Transfer Invoice ─────────────────────────────────────────────────
function branchTransferFullPage(data, biz, docCode) {
  const t = data;
  const invoiceNo = t.invoice_number || t.order_number || '';
  let html = buildPageHeader(biz, 'Branch Transfer', invoiceNo, t.created_at, [
    t.order_number !== invoiceNo ? `Transfer: ${t.order_number}` : '',
    t.request_po_number ? `Request: ${t.request_po_number}` : '',
    `Status: ${(t.status || '').toUpperCase()}`,
  ].filter(Boolean));

  // From / To boxes
  html += `<div class="info-row">`;
  html += `<div class="info-box"><div class="box-label">From (Source Branch)</div><div class="box-value">${t.from_branch_name || ''}</div></div>`;
  html += `<div class="info-box"><div class="box-label">To (Receiving Branch)</div><div class="box-value">${t.to_branch_name || ''}</div></div>`;
  html += `</div>`;

  // Items table - clean columns
  const items = t.items || [];
  html += '<table class="items-table"><thead><tr>';
  html += '<th style="width:5%">#</th><th>Product</th><th class="c" style="width:10%">Qty</th><th class="r" style="width:15%">Transfer Price</th><th class="r" style="width:15%">Total</th><th class="r" style="width:15%">Retail</th>';
  html += '</tr></thead><tbody>';
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty = parseFloat(item.qty) || 0;
    const tc = parseFloat(item.transfer_capital) || 0;
    html += `<tr><td class="c">${i + 1}</td>`;
    html += `<td>${item.product_name || ''}<div class="item-sub">${item.sku || ''} ${item.category ? '· ' + item.category : ''}</div></td>`;
    html += `<td class="c">${qty} ${item.unit || ''}</td>`;
    html += `<td class="r">${formatPHP(tc)}</td>`;
    html += `<td class="r" style="font-weight:600">${formatPHP(tc * qty)}</td>`;
    html += `<td class="r" style="color:#1A4D2E;font-weight:600">${formatPHP(item.branch_retail)}</td>`;
    html += `</tr>`;
  }
  html += '</tbody></table>';

  // Totals + QR
  const totalTransfer = items.reduce((s, i) => s + (parseFloat(i.transfer_capital) || 0) * (parseFloat(i.qty) || 0), 0);
  const totalRetail = items.reduce((s, i) => s + (parseFloat(i.branch_retail) || 0) * (parseFloat(i.qty) || 0), 0);

  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 100);
  html += '<div class="totals-box">';
  html += `<div class="t-row"><span>Total Items</span><span>${items.length}</span></div>`;
  html += `<div class="t-row"><span>Total Qty</span><span>${items.reduce((s, i) => s + (parseFloat(i.qty) || 0), 0)}</span></div>`;
  html += `<div class="t-row t-grand"><span>Transfer Total</span><span>${formatPHP(totalTransfer)}</span></div>`;
  html += `<div class="t-row t-highlight"><span>Retail Value</span><span>${formatPHP(totalRetail)}</span></div>`;
  html += '</div></div>';

  // Signatures
  html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Prepared By</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Driver / Released By</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Received By</div></div></div>';
  html += `<div class="page-footer"><div class="thank-you">Thank you!</div>AgriBooks — Internal Branch Transfer</div>`;
  return html;
}

// ── Expense Voucher ─────────────────────────────────────────────────────────
function expenseVoucherFullPage(data, biz, docCode) {
  const e = data;
  let html = buildPageHeader(biz, 'Expense Voucher', e.reference_number || e.id?.slice(0, 8) || '', e.date || e.created_at);

  html += '<div class="meta-grid">';
  html += `<div><span class="m-label">Category</span><div class="m-value">${e.category || 'General'}</div></div>`;
  html += `<div><span class="m-label">Payment</span><div class="m-value">${e.payment_method || 'Cash'}</div></div>`;
  html += `<div><span class="m-label">Amount</span><div class="m-value" style="font-size:16px;color:#1A4D2E">${formatPHP(e.amount)}</div></div>`;
  if (e.fund_source) html += `<div><span class="m-label">Source</span><div class="m-value">${e.fund_source}</div></div>`;
  html += '</div>';
  if (e.description) html += `<div class="notes-box"><strong>Description:</strong> ${e.description}</div>`;
  if (e.notes) html += `<div class="notes-box"><strong>Notes:</strong> ${e.notes}</div>`;

  html += '<div style="display:flex;justify-content:flex-end">' + qrImgTag(docCode, 80) + '</div>';
  html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Approved By</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Received By</div></div></div>';
  return html;
}

// ── Return Slip ─────────────────────────────────────────────────────────────
function returnSlipFullPage(data, biz, docCode) {
  const r = data;
  let html = buildPageHeader(biz, 'Return Slip', r.return_number || r.id?.slice(0, 8) || '', r.created_at, [
    `Original Invoice: ${r.original_invoice_number || ''}`,
  ]);

  if (r.customer_name) {
    html += `<div class="info-row"><div class="info-box"><div class="box-label">Customer</div><div class="box-value">${r.customer_name}</div></div>`;
    html += `<div class="info-box"><div class="box-label">Refund Method</div><div class="box-value">${r.refund_method || 'Cash'}</div>`;
    if (r.reason) html += `<div class="box-sub">Reason: ${r.reason}</div>`;
    html += `</div></div>`;
  }

  const items = r.items || [];
  html += '<table class="items-table"><thead><tr><th style="width:5%">#</th><th>Item</th><th class="c" style="width:10%">Qty</th><th class="r" style="width:15%">Price</th><th class="r" style="width:15%">Total</th></tr></thead><tbody>';
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty = parseFloat(item.quantity) || 0;
    const rate = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const total = parseFloat(item.total) || (qty * rate);
    html += `<tr><td class="c">${i + 1}</td><td>${item.product_name || ''}</td><td class="c">${qty}</td><td class="r">${formatPHP(rate)}</td><td class="r" style="font-weight:600">${formatPHP(total)}</td></tr>`;
  }
  html += '</tbody></table>';

  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 80);
  html += `<div class="totals-box"><div class="t-row t-grand"><span>Total Refund</span><span>${formatPHP(r.refund_amount || r.total_refund || 0)}</span></div></div>`;
  html += '</div>';

  html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Authorized By</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Customer Signature</div></div></div>';
  return html;
}

// ── Statement of Account ────────────────────────────────────────────────────
function statementFullPage(data, biz, docCode) {
  const s = data;
  let html = buildPageHeader(biz, 'Statement of Account', '', s.statement_date || new Date().toISOString());

  html += `<div class="info-row"><div class="info-box"><div class="box-label">Customer</div><div class="box-value" style="font-size:16px">${s.customer_name || ''}</div>`;
  if (s.customer_phone) html += `<div class="box-sub">${s.customer_phone}</div>`;
  if (s.customer_address) html += `<div class="box-sub">${s.customer_address}</div>`;
  html += `</div><div class="info-box"><div class="box-label">Balance Due</div><div class="box-value" style="font-size:18px;color:#c00">${formatPHP(s.closing_balance || s.balance || 0)}</div></div></div>`;

  if (s.transactions?.length) {
    html += '<table class="items-table"><thead><tr><th>Date</th><th>Reference</th><th>Description</th><th class="r">Debit</th><th class="r">Credit</th><th class="r">Balance</th></tr></thead><tbody>';
    for (const tx of s.transactions) {
      html += `<tr><td>${fmtDate(tx.date)}</td><td>${tx.reference || ''}</td><td>${tx.description || ''}</td><td class="r">${tx.debit > 0 ? formatPHP(tx.debit) : ''}</td><td class="r">${tx.credit > 0 ? formatPHP(tx.credit) : ''}</td><td class="r">${formatPHP(tx.running_balance || 0)}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px">';
  html += qrImgTag(docCode, 80);
  html += '<div class="totals-box">';
  if (s.opening_balance !== undefined) html += `<div class="t-row"><span>Opening</span><span>${formatPHP(s.opening_balance)}</span></div>`;
  if (s.total_charges !== undefined) html += `<div class="t-row"><span>Charges</span><span>${formatPHP(s.total_charges)}</span></div>`;
  if (s.total_payments !== undefined) html += `<div class="t-row"><span>Payments</span><span>-${formatPHP(s.total_payments)}</span></div>`;
  html += `<div class="t-row t-grand"><span>Balance Due</span><span>${formatPHP(s.closing_balance || s.balance || 0)}</span></div>`;
  html += '</div></div>';
  return html;
}

// ═══════════════════════════════════════════════════════════════════════════
//  THERMAL DOCUMENTS (keep existing for 58mm printers)
// ═══════════════════════════════════════════════════════════════════════════

function orderSlipThermal(data, biz, docCode) {
  const inv = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">ORDER SLIP</div>`;
  html += `<div class="meta-row"><span class="label">No:</span><span>${inv.invoice_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateMaybeTime(inv.created_at || inv.order_date)}</span></div>`;
  if (inv.customer_name && inv.customer_name !== 'Walk-in') html += `<div class="meta-row"><span class="label">Customer:</span><span>${inv.customer_name}</span></div>`;
  html += `<div class="meta-row"><span class="label">Cashier:</span><span>${inv.cashier_name || ''}</span></div>`;
  html += '<div class="sep"></div>';
  html += buildItemsThermal(inv.items || []);
  html += '<div class="sep"></div>';
  html += '<div class="totals">';
  html += `<div class="row"><span>Subtotal</span><span>${formatPHP(inv.subtotal)}</span></div>`;
  if (inv.overall_discount > 0) html += `<div class="row"><span>Discount</span><span>-${formatPHP(inv.overall_discount)}</span></div>`;
  html += `<div class="row grand"><span>TOTAL</span><span>${formatPHP(inv.grand_total)}</span></div>`;
  html += '</div>';
  html += '<div class="payment-info">';
  html += `<div class="meta-row"><span class="label">Payment:</span><span>${inv.payment_method || 'Cash'}</span></div>`;
  if (inv.amount_paid > 0 && inv.payment_type !== 'credit') {
    html += `<div class="meta-row"><span class="label">Paid:</span><span>${formatPHP(inv.amount_paid)}</span></div>`;
    const change = (inv.amount_paid || 0) - (inv.grand_total || 0);
    if (change > 0) html += `<div class="meta-row"><span class="label">Change:</span><span>${formatPHP(change)}</span></div>`;
  }
  html += '</div>';
  if (docCode) html += qrImgTag(docCode, 152);
  html += `<div class="footer">Thank you!${biz.receipt_footer ? ' ' + biz.receipt_footer : ''}</div>`;
  return html;
}

function trustReceiptThermal(data, biz, docCode) {
  const inv = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">CHARGE AGREEMENT</div>`;
  html += `<div class="meta-row"><span class="label">No:</span><span>${inv.invoice_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateMaybeTime(inv.order_date || inv.created_at)}</span></div>`;
  html += `<div class="meta-row"><span class="label">Customer:</span><span>${inv.customer_name || ''}</span></div>`;
  if (inv.due_date) html += `<div class="meta-row"><span class="label">Due:</span><span>${fmtDate(inv.due_date)}</span></div>`;
  html += '<div class="sep"></div>';
  html += buildItemsThermal(inv.items || []);
  html += '<div class="sep"></div>';
  html += '<div class="totals">';
  html += `<div class="row"><span>Subtotal</span><span>${formatPHP(inv.subtotal)}</span></div>`;
  html += `<div class="row grand"><span>TOTAL</span><span>${formatPHP(inv.grand_total)}</span></div>`;
  if (inv.amount_paid > 0) html += `<div class="row"><span>Paid</span><span>${formatPHP(inv.amount_paid)}</span></div>`;
  if (inv.balance > 0) html += `<div class="row" style="font-weight:bold"><span>BALANCE</span><span>${formatPHP(inv.balance)}</span></div>`;
  html += '</div>';
  const terms = (biz.trust_receipt_terms || '').replace('{business_name}', biz.business_name || '');
  if (terms) html += `<div class="trust-terms"><div class="terms-title">TERMS</div>${terms}</div>`;
  html += '<div class="signature-line"><div style="margin-top:8px"></div><div class="line"></div><div class="sig-label">Customer Signature &amp; Printed Name</div></div>';
  if (docCode) html += qrImgTag(docCode, 152);
  html += `<div class="footer">${TAX_DISCLAIMER}</div>`;
  return html;
}

function returnSlipThermal(data, biz, docCode) {
  const r = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">RETURN SLIP</div>`;
  html += `<div class="meta-row"><span class="label">Ref:</span><span>${r.return_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateTime(r.created_at)}</span></div>`;
  html += `<div class="meta-row"><span class="label">Orig:</span><span>${r.original_invoice_number || ''}</span></div>`;
  html += '<div class="sep"></div>';
  html += buildItemsThermal(r.items || []);
  html += '<div class="sep"></div>';
  html += '<div class="totals">';
  html += `<div class="row grand"><span>REFUND</span><span>${formatPHP(r.refund_amount || r.total_refund || 0)}</span></div>`;
  html += '</div>';
  if (docCode) html += qrImgTag(docCode, 152);
  html += `<div class="footer">${biz.receipt_footer || ''}</div>`;
  return html;
}


function purchaseOrderThermal(data, biz, docCode) {
  const po = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">PURCHASE ORDER</div>`;
  html += `<div class="meta-row"><span class="label">PO #:</span><span>${po.po_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateTime(po.purchase_date || po.created_at)}</span></div>`;
  html += `<div class="meta-row"><span class="label">Supplier:</span><span>${po.vendor || ''}</span></div>`;
  if (po.dr_number) html += `<div class="meta-row"><span class="label">DR #:</span><span>${po.dr_number}</span></div>`;
  html += `<div class="meta-row"><span class="label">Status:</span><span>${(po.status || '').toUpperCase()}</span></div>`;
  html += '<div class="sep"></div>';
  html += buildItemsThermal(po.items || []);
  html += '<div class="sep"></div>';
  html += '<div class="totals">';
  html += `<div class="row"><span>Subtotal</span><span>${formatPHP(po.subtotal || po.line_subtotal)}</span></div>`;
  if (po.overall_discount_amount > 0) html += `<div class="row"><span>Discount</span><span>-${formatPHP(po.overall_discount_amount)}</span></div>`;
  if (po.freight > 0) html += `<div class="row"><span>Freight</span><span>${formatPHP(po.freight)}</span></div>`;
  html += `<div class="row grand"><span>TOTAL</span><span>${formatPHP(po.grand_total)}</span></div>`;
  if (po.balance > 0) html += `<div class="row" style="font-weight:bold"><span>BALANCE</span><span>${formatPHP(po.balance)}</span></div>`;
  html += '</div>';
  if (docCode) html += qrImgTag(docCode, 152);
  html += `<div class="footer">${biz.receipt_footer || 'AgriBooks — Purchase Order'}</div>`;
  return html;
}

function branchTransferThermal(data, biz, docCode) {
  const t = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">BRANCH TRANSFER</div>`;
  html += `<div class="meta-row"><span class="label">No:</span><span>${t.order_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateTime(t.created_at)}</span></div>`;
  html += `<div class="meta-row"><span class="label">From:</span><span>${t.from_branch_name || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">To:</span><span>${t.to_branch_name || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Status:</span><span>${(t.status || '').toUpperCase()}</span></div>`;
  html += '<div class="sep"></div>';
  const items = t.items || [];
  let itemHtml = '<table class="items-table">';
  for (const item of items) {
    const qty = parseFloat(item.qty) || 0;
    const tc = parseFloat(item.transfer_capital) || 0;
    itemHtml += `<tr><td class="item-name" colspan="2">${item.product_name || ''}</td></tr>`;
    itemHtml += `<tr><td class="item-detail">${qty} x ${formatPHP(tc)}</td><td class="item-total">${formatPHP(tc * qty)}</td></tr>`;
  }
  itemHtml += '</table>';
  html += itemHtml;
  html += '<div class="sep"></div>';
  const totalTransfer = items.reduce((s, i) => s + (parseFloat(i.transfer_capital) || 0) * (parseFloat(i.qty) || 0), 0);
  html += '<div class="totals">';
  html += `<div class="row"><span>Items</span><span>${items.length}</span></div>`;
  html += `<div class="row grand"><span>TOTAL</span><span>${formatPHP(totalTransfer)}</span></div>`;
  html += '</div>';
  if (docCode) html += qrImgTag(docCode, 152);
  html += `<div class="footer">AgriBooks — Branch Transfer</div>`;
  return html;
}


// ═══════════════════════════════════════════════════════════════════════════
//  MAIN PRINT ENGINE
// ═══════════════════════════════════════════════════════════════════════════

const PrintEngine = {
  /**
   * @param {object} opts
   * @param {'order_slip'|'trust_receipt'|'purchase_order'|'branch_transfer'|'expense_voucher'|'return_slip'|'statement'} opts.type
   * @param {object} opts.data - Document data
   * @param {'thermal'|'full_page'} opts.format
   * @param {object} opts.businessInfo - From /settings/business-info
   * @param {string} opts.docCode - Unique document QR code (optional)
   */

  /**
   * generateHtml — returns the receipt HTML string WITHOUT opening a window or printing.
   * Used by PrintBridge when running in Capacitor native mode (H10P APK).
   * The HTML is passed to the native H10PPrinterPlugin which renders it to Bitmap.
   */
  generateHtml({ type, data, format = 'thermal', businessInfo = {}, docCode = '' }) {
    const css = format === 'thermal' ? thermalCSS : fullPageCSS;
    let body = '';
    switch (type) {
      case 'order_slip':
        body = format === 'thermal' ? orderSlipThermal(data, businessInfo, docCode) : orderSlipFullPage(data, businessInfo, docCode);
        break;
      case 'trust_receipt':
        body = format === 'thermal' ? trustReceiptThermal(data, businessInfo, docCode) : trustReceiptFullPage(data, businessInfo, docCode);
        break;
      case 'purchase_order':
        body = format === 'thermal' ? purchaseOrderThermal(data, businessInfo, docCode) : purchaseOrderFullPage(data, businessInfo, docCode);
        break;
      case 'branch_transfer':
        body = format === 'thermal' ? branchTransferThermal(data, businessInfo, docCode) : branchTransferFullPage(data, businessInfo, docCode);
        break;
      case 'expense_voucher':
        body = expenseVoucherFullPage(data, businessInfo, docCode);
        break;
      case 'return_slip':
        body = format === 'thermal' ? returnSlipThermal(data, businessInfo, docCode) : returnSlipFullPage(data, businessInfo, docCode);
        break;
      case 'statement':
        body = statementFullPage(data, businessInfo, docCode);
        break;
      default:
        body = format === 'thermal' ? orderSlipThermal(data, businessInfo, docCode) : orderSlipFullPage(data, businessInfo, docCode);
    }
    // No window.print() script — the native plugin handles printing
    // Viewport meta: forces CSS viewport = 384px so body width:100% fills the bitmap exactly
    const viewportMeta = format === 'thermal'
      ? '<meta name="viewport" content="width=384, initial-scale=1.0, maximum-scale=1.0">'
      : '';
    return `<!DOCTYPE html><html><head><meta charset="utf-8">${viewportMeta}<title>Receipt</title><style>${css}</style></head><body>${body}</body></html>`;
  },

  print({ type, data, format = 'thermal', businessInfo = {}, docCode = '' }) {
    const css = format === 'thermal' ? thermalCSS : fullPageCSS;
    let body = '';

    switch (type) {
      case 'order_slip':
        body = format === 'thermal' ? orderSlipThermal(data, businessInfo, docCode) : orderSlipFullPage(data, businessInfo, docCode);
        break;
      case 'trust_receipt':
        body = format === 'thermal' ? trustReceiptThermal(data, businessInfo, docCode) : trustReceiptFullPage(data, businessInfo, docCode);
        break;
      case 'purchase_order':
        body = format === 'thermal' ? purchaseOrderThermal(data, businessInfo, docCode) : purchaseOrderFullPage(data, businessInfo, docCode);
        break;
      case 'branch_transfer':
        body = format === 'thermal' ? branchTransferThermal(data, businessInfo, docCode) : branchTransferFullPage(data, businessInfo, docCode);
        break;
      case 'expense_voucher':
        body = expenseVoucherFullPage(data, businessInfo, docCode);
        break;
      case 'return_slip':
        body = format === 'thermal' ? returnSlipThermal(data, businessInfo, docCode) : returnSlipFullPage(data, businessInfo, docCode);
        break;
      case 'statement':
        body = statementFullPage(data, businessInfo, docCode);
        break;
      default:
        body = format === 'thermal' ? orderSlipThermal(data, businessInfo, docCode) : orderSlipFullPage(data, businessInfo, docCode);
    }

    const winWidth = format === 'thermal' ? 400 : 900;

    // Inject a script that waits for all images (QR code) to finish loading
    // before calling window.print() — eliminates the race with api.qrserver.com
    const printScript = `
<script>
(function() {
  var printed = false;
  function doPrint() { if (printed) return; printed = true; window.print(); }
  var imgs = document.images;
  if (!imgs.length) { doPrint(); return; }
  var remaining = imgs.length;
  function onDone() { remaining--; if (remaining <= 0) doPrint(); }
  for (var i = 0; i < imgs.length; i++) {
    if (imgs[i].complete) { onDone(); }
    else { imgs[i].onload = onDone; imgs[i].onerror = onDone; }
  }
  // Safety fallback: print after 6 seconds regardless
  setTimeout(doPrint, 6000);
})();
</script>`;

    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Print</title><style>${css}</style></head><body>${body}${printScript}</body></html>`;
    const win = window.open('', '_blank', `width=${winWidth},height=700`);
    if (!win) { alert('Please allow popups to print'); return; }
    win.document.write(html);
    win.document.close();
    win.focus();
  },

  // Helper to determine doc type from invoice data
  getDocType(invoice) {
    if (!invoice) return 'order_slip';
    const pt = invoice.payment_type || '';
    const balance = parseFloat(invoice.balance) || 0;
    if (pt === 'credit' || pt === 'partial' || (balance > 0 && invoice.status !== 'paid')) {
      return 'trust_receipt';
    }
    return 'order_slip';
  },
};

export default PrintEngine;
