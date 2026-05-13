import { formatDate, formatDateTime } from '../lib/dateFormat';
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
  return `P${n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDate(d) {
  if (!d) return '';
  try { return formatDate(d); }
  catch { return d; }
}

function fmtDateTime(d) {
  if (!d) return '';
  try { return formatDateTime(d); }
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
  .items-table { width: 100%; table-layout: fixed; font-size: 12px; border-collapse: collapse; }
  .items-table td { padding: 3px 0; vertical-align: top; word-break: break-word; }
  .items-table .item-name { font-weight: bold; font-size: 13px; line-height: 1.4; padding-top: 4px; }
  /* Visually separate one item from the next so wrapped product names
     don't appear to bleed into the row below. The first item row keeps
     no top border so the items table flows cleanly from the heading
     above it. */
  .items-table tr + tr .item-name { padding-top: 6px; border-top: 1px dashed #000; margin-top: 2px; }
  .items-table .item-detail { width: 58%; padding-left: 4px; font-size: 12px; color: #000; }
  .items-table .item-total { width: 42%; text-align: right; font-weight: bold; font-size: 13px; white-space: nowrap; }
  .items-table .item-discount { padding-left: 4px; font-size: 12px; color: #000; font-style: italic; }
  .items-table .item-disc-val { text-align: right; font-size: 12px; color: #000; white-space: nowrap; }
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

// ── Dot Matrix CSS (8.5×11 — Epson LX-310 optimised) ────────────────────────
// Rules: monospace throughout, solid black text only, no colours/gradients/
// shading, visible 1px solid borders on all table rows, minimum 11px body.
const dotMatrixCSS = `
  @page {
    size: 8.5in 11in;
    margin: 0.35in 0.6in 0.5in 0.6in;
    @bottom-center {
      content: "Page " counter(page) " of " counter(pages);
      font-family: 'Courier New', Courier, monospace;
      font-size: 9px; color: #000;
    }
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Courier New', Courier, monospace;
    font-size: 11px; line-height: 1.25; color: #000; background: #fff;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }

  /* ── Page-frame: thead repeats letterhead on every printed page,
        tfoot reserves space for the receipt-# fallback footer. ── */
  .dm-page-frame { width: 100%; border-collapse: collapse; }
  .dm-page-frame > thead { display: table-header-group; }
  .dm-page-frame > tfoot { display: table-footer-group; }
  .dm-page-frame > thead > tr > td,
  .dm-page-frame > tbody > tr > td,
  .dm-page-frame > tfoot > tr > td { padding: 0; border: 0; }

  /* ── Letterhead repeated on every page ── */
  .dm-letterhead {
    width: 100%; border-collapse: collapse;
    padding-bottom: 4px; margin-bottom: 2px;
  }
  .dm-letterhead td { vertical-align: top; padding: 0; }
  .dm-lh-biz { padding-left: 8px; }
  .dm-lh-biz .dm-biz-name-lh {
    font-size: 13px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .dm-lh-biz .dm-biz-detail-lh { font-size: 10px; line-height: 1.3; }
  .dm-lh-biz .dm-biz-detail-lh .dm-lbl { display: inline-block; min-width: 40px; }
  .dm-lh-title-cell { text-align: right; padding-right: 0; vertical-align: top; }
  .dm-lh-title {
    font-size: 20px; font-weight: bold; text-transform: uppercase;
    letter-spacing: 1.5px; margin-bottom: 3px;
  }
  .dm-lh-invbox {
    display: inline-block; border: 1px solid #000; padding: 2px 6px;
    font-size: 11px; line-height: 1.35; text-align: left; min-width: 200px;
  }
  .dm-lh-invbox table { border-collapse: collapse; }
  .dm-lh-invbox td { padding: 0 6px 0 0; }
  .dm-lh-invbox .dm-inv-val { font-weight: bold; text-align: right; padding-left: 12px; }

  /* ── Billing / Shipping band (first page only) ── */
  .dm-addr-band { width: 100%; border-collapse: collapse; margin: 4px 0 2px 0; }
  .dm-addr-band td { vertical-align: top; padding: 1px 4px; font-size: 10px; }
  .dm-addr-band .dm-addr-label { font-weight: bold; width: 95px; }
  .dm-addr-band .dm-addr-val { font-weight: bold; }

  /* ── Sales-rep / Payment terms grid (first page only) ── */
  .dm-meta-grid { width: 100%; border-collapse: collapse; margin: 3px 0 6px 0; }
  .dm-meta-grid th, .dm-meta-grid td {
    border: 1px solid #000; padding: 2px 6px; font-size: 10px; vertical-align: top;
  }
  .dm-meta-grid th { background: #f0f0f0; font-weight: bold; text-align: left; font-size: 9px; }

  /* ── Right-aligned totals box — compact ── */
  .dm-total-box {
    margin: 6px 0 4px auto;
    width: 280px; border-collapse: collapse;
  }
  .dm-total-box td { padding: 2px 8px; font-size: 11px; vertical-align: middle; }
  .dm-total-box td.dm-tb-label { text-align: left; font-weight: bold; }
  .dm-total-box td.dm-tb-val {
    text-align: right; border: 1px solid #000; min-width: 120px; font-weight: bold;
  }
  .dm-total-box tr.dm-tb-grand td.dm-tb-label { font-size: 13px; font-weight: bold; }
  .dm-total-box tr.dm-tb-grand td.dm-tb-val { font-size: 13px; }

  /* ── Centered AUTHORIZED REPRESENTATIVE signature line ── */
  .dm-auth-sig {
    text-align: center; margin: 10px auto 4px auto;
    page-break-inside: avoid;
  }
  .dm-auth-sig .dm-sig-line-c {
    border-bottom: 1px solid #000; width: 280px; height: 16px; margin: 0 auto;
  }
  .dm-auth-sig .dm-sig-label-c {
    font-size: 9px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;
    margin-top: 2px;
  }

  /* ── Terms block (charge agreement style) ── */
  .dm-terms-block {
    margin-top: 8px; font-size: 9.5px; line-height: 1.35;
    text-align: center;
    page-break-inside: avoid;
  }
  .dm-terms-block .dm-terms-head {
    font-weight: bold; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px;
    font-size: 10px;
  }
  .dm-terms-block .dm-terms-copy-key {
    font-size: 9.5px; font-weight: bold; letter-spacing: 0.5px;
    margin-top: 3px; text-transform: uppercase;
  }

  /* ── Receipt-# fallback footer (in tfoot — shows even if @page bottom unsupported) ── */
  .dm-foot-receipt {
    text-align: right; font-size: 9px; padding: 2px 0;
    border-top: 1px dashed #000;
  }

  /* ── Company header (legacy — order slip uses this) ── */
  .dm-header { text-align: center; border-bottom: 2px solid #000; padding-bottom: 6px; margin-bottom: 4px; }
  .dm-biz-name { font-size: 16px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; }
  .dm-biz-detail { font-size: 10px; line-height: 1.3; }

  /* ── Document title banner ── */
  .dm-doc-title {
    text-align: center; font-size: 14px; font-weight: bold;
    text-transform: uppercase; letter-spacing: 2px;
    border-top: 1px solid #000; border-bottom: 1px solid #000;
    padding: 3px 0; margin: 4px 0;
  }

  /* ── Meta grid (two-column key-value pairs) ── */
  .dm-meta-table { width: 100%; border-collapse: collapse; margin-bottom: 4px; }
  .dm-meta-table td { padding: 2px 4px; font-size: 11px; vertical-align: top; }
  .dm-meta-table .dm-label { font-weight: bold; white-space: nowrap; width: 1%; padding-right: 6px; }

  /* ── Customer / info box ── */
  .dm-info-box {
    border: 1px solid #000; padding: 4px 8px; margin: 4px 0;
  }
  .dm-info-box .dm-box-label {
    font-size: 9px; font-weight: bold; text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: 2px; border-bottom: 1px solid #000; padding-bottom: 1px;
  }
  .dm-info-box .dm-box-name { font-size: 12px; font-weight: bold; }
  .dm-info-box .dm-box-sub { font-size: 10px; }

  /* ── Warning banner ── */
  .dm-warning {
    border: 2px solid #000; padding: 4px; margin: 4px 0;
    text-align: center; font-weight: bold; font-size: 10px;
  }

  /* ── Items table — DENSE (target: 10-15 lines on page 1) ── */
  .dm-items-table { width: 100%; border-collapse: collapse; margin: 4px 0; }
  .dm-items-table thead tr { border-bottom: 1.5px solid #000; }
  .dm-items-table th {
    border: 1px solid #000; padding: 3px 6px;
    font-size: 10px; font-weight: bold; text-transform: uppercase;
    text-align: left; background: #fff; line-height: 1.2;
  }
  .dm-items-table th.r { text-align: right; }
  .dm-items-table th.c { text-align: center; }
  .dm-items-table td {
    border: 1px solid #000; padding: 2px 6px;
    font-size: 11px; vertical-align: top; word-break: break-word; line-height: 1.25;
  }
  .dm-items-table td.r { text-align: right; }
  .dm-items-table td.c { text-align: center; }
  .dm-items-table td.strong { font-weight: bold; }
  .dm-items-table .dm-row-num {
    width: 22px; text-align: center; font-size: 10px; color: #555;
  }

  /* ── Totals block (legacy used by order slip) ── */
  .dm-totals { margin-top: 4px; border-top: 1.5px solid #000; padding-top: 4px; }
  .dm-tot-row {
    display: flex; justify-content: flex-end; gap: 0;
    font-size: 11px; padding: 1px 0;
  }
  .dm-tot-row .dm-tot-label {
    min-width: 120px; text-align: right; font-weight: bold; padding-right: 12px;
  }
  .dm-tot-row .dm-tot-val { min-width: 110px; text-align: right; }
  .dm-tot-row.dm-grand {
    font-size: 12px; font-weight: bold;
    border-top: 1px solid #000; border-bottom: 1px solid #000;
    padding: 2px 0; margin: 2px 0;
  }

  /* ── QR section (legacy big — kept for order slip) ── */
  .dm-qr-section {
    display: flex; align-items: flex-start; gap: 16px;
    margin-top: 8px; border-top: 1px solid #000; padding-top: 6px;
  }
  .dm-qr-section img {
    display: block; width: 90px; height: 90px;
    image-rendering: pixelated; image-rendering: crisp-edges;
    flex-shrink: 0;
  }
  .dm-qr-info { font-size: 10px; }
  .dm-qr-info .dm-qr-code { font-size: 11px; font-weight: bold; letter-spacing: 0.5px; margin-bottom: 2px; }
  .dm-qr-info .dm-qr-scan { font-size: 10px; font-weight: bold; margin-bottom: 4px; }
  .dm-qr-info .dm-qr-sub { font-size: 10px; }

  /* ── Compact QR strip — pinned bottom-left of trust receipt below the
        Terms block. Same shape, smaller footprint, consistent placement
        across all printed copies. ── */
  .dm-qr-strip {
    display: flex; align-items: center; gap: 10px;
    margin-top: 6px; padding-top: 4px;
    border-top: 1px dashed #000;
    page-break-inside: avoid;
  }
  .dm-qr-strip img {
    width: 70px; height: 70px;
    image-rendering: pixelated; image-rendering: crisp-edges;
    flex-shrink: 0;
  }
  .dm-qr-strip .dm-qr-strip-info { font-size: 10px; line-height: 1.3; }
  .dm-qr-strip .dm-qr-code { font-size: 11px; font-weight: bold; letter-spacing: 0.5px; }
  .dm-qr-strip .dm-qr-scan { font-size: 10px; font-weight: bold; }
  .dm-qr-strip .dm-qr-sub  { font-size: 9.5px; }

  /* ── Signature row ── */
  .dm-sig-row { display: flex; justify-content: space-between; gap: 24px; margin-top: 16px; }
  .dm-sig-block { flex: 1; text-align: center; }
  .dm-sig-line { border-bottom: 1px solid #000; height: 22px; margin-bottom: 2px; }
  .dm-sig-label { font-size: 9px; font-weight: bold; text-transform: uppercase; }

  /* ── Footer ── */
  .dm-footer {
    text-align: center; border-top: 1.5px solid #000;
    margin-top: 8px; padding-top: 6px;
  }
  .dm-footer .dm-thankyou { font-size: 12px; font-weight: bold; margin-bottom: 2px; }
  .dm-footer .dm-disclaimer { font-size: 8px; line-height: 1.3; }

  /* ── Terms / Note boxes ── */
  .dm-box { border: 1px solid #000; padding: 4px 8px; margin: 4px 0; font-size: 10px; line-height: 1.35; }
  .dm-box strong { font-size: 11px; text-transform: uppercase; }

  @media print {
    body { max-width: none; }
    .dm-items-table { page-break-inside: auto; }
    .dm-items-table tr { page-break-inside: avoid; }
  }
`;

// ── Build page header ───────────────────────────────────────────────────────
function buildPageHeader(biz, docType, docNumber, date, extraLines = []) {
  let companyHtml = `<div class="biz-name">${biz.business_name || 'AgriBooks'}</div>`;
  if (biz.address) companyHtml += `<div class="biz-detail">${biz.address}</div>`;
  if (biz.phone) companyHtml += `<div class="biz-detail">${biz.phone}</div>`;
  if (biz.tin) companyHtml += `<div class="biz-detail">TIN: ${biz.tin}</div>`;

  let metaHtml = `<div class="doc-type">${docType}</div>`;
  metaHtml += `<div class="doc-number">${docNumber}</div>`;
  // Iter 243.3: ONE date on the receipt — the actual transaction moment.
  // The Z-Report posting date is recoverable via the QR code / Audit Center,
  // so we keep the customer-facing receipt clean (matches BIR expectations
  // for a single OR issuance date).
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
    const discAmt = parseFloat(item.discount_amount) || 0;
    const discVal = parseFloat(item.discount_value) || 0;
    const discType = item.discount_type || 'amount';
    const grossLine = qty * rate;
    // Total: prefer stored line_total, else compute from gross − discount
    const total = (item.total !== undefined && item.total !== null && item.total !== '')
      ? parseFloat(item.total)
      : (grossLine - discAmt);
    html += `<tr><td class="item-name" colspan="2">${item.product_name || item.description || ''}</td></tr>`;
    if (discAmt > 0) {
      // Show pre-discount line, then the discount line, then the net line.
      // Cashiers asked for this so the receipt no longer "lies": web view
      // shows the discount, the printed/thermal copy must too.
      const discLabel = discType === 'percent'
        ? `Less ${discVal}%`
        : (discVal > 0 ? `Less ${formatPHP(discVal)}/unit` : 'Less Discount');
      html += `<tr><td class="item-detail">${qty} x ${formatPHP(rate)}</td><td class="item-total">${formatPHP(grossLine)}</td></tr>`;
      html += `<tr><td class="item-discount">${discLabel}</td><td class="item-disc-val">-${formatPHP(discAmt)}</td></tr>`;
      html += `<tr><td class="item-detail" style="text-align:right;font-weight:bold">Line Total</td><td class="item-total">${formatPHP(total)}</td></tr>`;
    } else {
      html += `<tr><td class="item-detail">${qty} x ${formatPHP(rate)}</td><td class="item-total">${formatPHP(total)}</td></tr>`;
    }
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
  const isDraft = inv.status === 'for_preparation';
  // Iter 252 — Linked Offline Draft Finalization.
  // When this is being printed for an offline draft completion (before sync)
  // OR a synced one (after sync), show both the OFF receipt number AND the
  // canonical draft invoice number on the printed copy so the customer's
  // paper can always be tied back to the official record.
  const offlineDraftPending = inv.kind === 'draft_finalization_offline' && !inv.synced_from_offline;
  const offlineDraftSynced = inv.linked_offline_receipt_number && inv.finalized_from_draft_offline;
  const linkedOff = inv.linked_offline_receipt_number || (offlineDraftPending ? (inv.offline_receipt_number || inv.invoice_number) : '');
  const linkedDraftNum = inv.original_draft_invoice_number || inv.draft_invoice_number || (offlineDraftPending ? inv.draft_invoice_number : '');

  let html = buildPageHeader(biz, 'Order Slip', inv.invoice_number || '', inv.invoice_date || inv.created_at || inv.order_date, [
    inv.cashier_name ? `Cashier: ${inv.cashier_name}` : '',
    inv.release_mode === 'full' ? 'Status: FULLY RELEASED' : inv.release_mode === 'partial' ? 'Status: PARTIAL RELEASE' : '',
  ].filter(Boolean));

  // FOR PREPARATION banner
  if (isDraft) {
    html += `<div style="background:#fef3c7;border:3px solid #f59e0b;border-radius:8px;padding:14px 18px;margin:0 0 18px;text-align:center">
      <div style="font-size:17px;font-weight:900;color:#92400e;letter-spacing:1.5px;margin-bottom:3px">FOR PREPARATION ONLY</div>
      <div style="font-size:11px;font-weight:700;color:#92400e;letter-spacing:0.5px">NOT YET PAID — NOT A FINAL RECEIPT</div>
    </div>`;
  }

  // OFFLINE RECEIPT — pending sync (draft finalized while offline)
  if (offlineDraftPending) {
    html += `<div style="background:#fef9c3;border:3px solid #ca8a04;border-radius:8px;padding:14px 18px;margin:0 0 18px;text-align:center" data-testid="offline-pending-banner">
      <div style="font-size:17px;font-weight:900;color:#854d0e;letter-spacing:1.5px;margin-bottom:3px">OFFLINE RECEIPT — PENDING SYNC</div>
      <div style="font-size:12px;font-weight:700;color:#854d0e;margin-top:6px">Receipt No.: <span style="font-family:monospace">${linkedOff || ''}</span></div>
      <div style="font-size:12px;font-weight:700;color:#854d0e">Linked Draft / Original Invoice: <span style="font-family:monospace">${linkedDraftNum || ''}</span></div>
      <div style="font-size:10px;font-weight:600;color:#854d0e;margin-top:5px">This receipt will be linked to ${linkedDraftNum} once synced. The official invoice number remains ${linkedDraftNum}.</div>
    </div>`;
  }

  // Synced offline draft completion banner
  if (offlineDraftSynced) {
    html += `<div style="background:#dcfce7;border:2px solid #16a34a;border-radius:8px;padding:10px 14px;margin:0 0 16px;text-align:center" data-testid="offline-synced-banner">
      <div style="font-size:13px;font-weight:800;color:#166534">SYNCED — OFFICIAL RECEIPT</div>
      <div style="font-size:11px;color:#166534;margin-top:3px">Invoice No.: <span style="font-family:monospace;font-weight:700">${inv.invoice_number}</span> · Linked Offline Receipt: <span style="font-family:monospace">${linkedOff}</span></div>
    </div>`;
  }

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

  // Partial release notice
  if (inv.release_mode === 'partial') {
    html += `<div style="background:#fef3c7;border:2px solid #f59e0b;border-radius:8px;padding:12px;margin:16px 0;text-align:center">`;
    html += `<p style="font-size:13px;font-weight:700;color:#92400e;margin:0">⚠ PARTIAL RELEASE — Items must be scanned via QR code for release</p>`;
    html += `<p style="font-size:11px;color:#78350f;margin:4px 0 0">Scan the QR code below to manage item releases.</p>`;
    html += `</div>`;
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
  html += `<div class="page-footer">`;
  if (isDraft) {
    html += `<div style="font-size:12px;font-weight:900;color:#92400e;letter-spacing:1px;margin-bottom:4px">FOR PREPARATION ONLY — NOT YET PAID</div>`;
  } else {
    html += `<div class="thank-you">Thank you for your business!</div>`;
  }
  html += `<div style="font-size:8px;font-style:italic;color:#999;margin-top:4px">${TAX_DISCLAIMER}</div></div>`;
  return html;
}

// ── Charge Agreement (Credit / Partial Sales) ──────────────────────────────
function trustReceiptFullPage(data, biz, docCode) {
  const inv = data;
  let html = buildPageHeader(biz, 'Charge Agreement', inv.invoice_number || '', inv.invoice_date || inv.created_at || inv.order_date, [
    inv.terms && inv.terms !== 'COD' ? `Terms: ${inv.terms}` : '',
    inv.due_date ? `Due: ${fmtDate(inv.due_date)}` : '',
    inv.release_mode === 'full' ? 'Status: FULLY RELEASED' : inv.release_mode === 'partial' ? 'Status: PARTIAL RELEASE' : '',
  ].filter(Boolean));

  html += `<div class="info-row">`;
  html += `<div class="info-box"><div class="box-label">Customer</div><div class="box-value">${inv.customer_name || ''}</div>`;
  if (inv.customer_address) html += `<div class="box-sub">${inv.customer_address}</div>`;
  if (inv.customer_phone) html += `<div class="box-sub">${inv.customer_phone}</div>`;
  html += `</div>`;
  html += `<div class="info-box"><div class="box-label">Cashier</div><div class="box-value">${inv.cashier_name || ''}</div></div>`;
  html += `</div>`;

  // Partial release notice
  if (inv.release_mode === 'partial') {
    html += `<div style="background:#fef3c7;border:2px solid #f59e0b;border-radius:8px;padding:12px;margin:16px 0;text-align:center">`;
    html += `<p style="font-size:13px;font-weight:700;color:#92400e;margin:0">⚠ PARTIAL RELEASE — Items must be scanned via QR code for release</p>`;
    html += `<p style="font-size:11px;color:#78350f;margin:4px 0 0">Scan the QR code below to manage item releases.</p>`;
    html += `</div>`;
  }

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

  // Signatures — embed customer signature image if present, or "Manager Bypass" badge
  // Tamper-evident stamp below: signed-at + verification token (8-char HMAC)
  const sigStampFull = (() => {
    const t = data.signature_verification_token || '';
    const sa = data.signature_signed_at || '';
    if (!t && !sa) return '';
    const dt = sa ? new Date(sa) : null;
    const dtStr = dt ? dt.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '';
    return `<div style="text-align:center;font-size:8.5px;color:#666;margin-top:4px;letter-spacing:0.3px">Signed ${dtStr}${t ? ` &middot; v.${t}` : ''}</div>`;
  })();
  if (data.signature_url) {
    html += `<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Authorized Representative</div></div><div class="sig-block"><img src="${data.signature_url}" alt="signature" style="max-width:80%;max-height:60px;display:block;margin:0 auto 2px;object-fit:contain"/><div class="sig-line"></div><div class="sig-label">Customer Signature</div>${sigStampFull}</div></div>`;
  } else if (data.bypass_method) {
    html += `<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Authorized Representative</div></div><div class="sig-block"><div style="margin:8px auto;font-size:10px;border:1px solid #555;padding:4px 8px;display:inline-block;color:#555">AUTHORIZED VIA MANAGER PIN</div><div class="sig-label">Customer (bypassed)</div>${sigStampFull}</div></div>`;
  } else {
    html += '<div class="sig-row"><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Authorized Representative</div></div><div class="sig-block"><div class="sig-line"></div><div class="sig-label">Customer Signature &amp; Printed Name</div></div></div>';
  }
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
//  DOT MATRIX DOCUMENTS (8.5×11 — Epson LX-310)
// ═══════════════════════════════════════════════════════════════════════════

// ── Dot Matrix: shared company header ──────────────────────────────────────
function buildDotMatrixHeader(biz) {
  let html = `<div class="dm-header">`;
  html += `<div class="dm-biz-name">${biz.business_name || 'AgriBooks'}</div>`;
  if (biz.address) html += `<div class="dm-biz-detail">${biz.address}</div>`;
  if (biz.phone) html += `<div class="dm-biz-detail">Tel: ${biz.phone}</div>`;
  if (biz.tin) html += `<div class="dm-biz-detail">TIN: ${biz.tin}</div>`;
  html += `</div>`;
  return html;
}

// ── Dot Matrix: QR block (120×120, high contrast for LX-310) ───────────────
function qrImgTagDM(code, inv) {
  if (!code) return '';
  const url = `${window.location.origin}/doc/${code}`;
  const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=120x120&data=${encodeURIComponent(url)}&margin=4&color=000000&bgcolor=ffffff`;
  return `
    <div class="dm-qr-section">
      <img src="${qrUrl}" alt="QR Code" width="120" height="120" />
      <div class="dm-qr-info">
        <div class="dm-qr-code">${code}</div>
        <div class="dm-qr-scan">Scan to view document</div>
        ${inv.invoice_number ? `<div class="dm-qr-sub">Receipt No: ${inv.invoice_number}</div>` : ''}
        ${inv.invoice_date || inv.created_at || inv.order_date ? `<div class="dm-qr-sub">Date: ${fmtDate(inv.invoice_date || inv.created_at || inv.order_date)}</div>` : ''}
        ${inv.customer_name ? `<div class="dm-qr-sub">Customer: ${inv.customer_name}</div>` : ''}
      </div>
    </div>`;
}

// ── Order Slip — Dot Matrix ─────────────────────────────────────────────────
function orderSlipDotMatrix(data, biz, docCode) {
  const inv = data;
  const isDraft = inv.status === 'for_preparation';
  let html = buildDotMatrixHeader(biz);

  html += `<div class="dm-doc-title">${isDraft ? 'PREPARATION COPY — ORDER SLIP' : 'ORDER SLIP'}</div>`;

  // FOR PREPARATION banner
  if (isDraft) {
    html += `<div class="dm-warning" style="font-size:13px;padding:10px 8px;margin-bottom:10px;">
      *** FOR PREPARATION ONLY — NOT YET PAID ***<br/>*** NOT A FINAL RECEIPT ***
    </div>`;
  }

  // Meta: receipt no, date, cashier, payment method
  html += `<table class="dm-meta-table">`;
  html += `<tr>
    <td class="dm-label">Receipt No:</td><td>${inv.invoice_number || ''}</td>
    <td class="dm-label">Date:</td><td>${fmtDateMaybeTime(inv.invoice_date || inv.created_at || inv.order_date)}</td>
  </tr>`;
  html += `<tr>
    <td class="dm-label">Cashier:</td><td>${inv.cashier_name || ''}</td>
    <td class="dm-label">Payment:</td><td>${inv.payment_method || 'Cash'}</td>
  </tr>`;
  if (inv.release_mode === 'full') {
    html += `<tr><td class="dm-label">Status:</td><td colspan="3"><strong>FULLY RELEASED</strong></td></tr>`;
  } else if (inv.release_mode === 'partial') {
    html += `<tr><td class="dm-label">Status:</td><td colspan="3"><strong>PARTIAL RELEASE</strong></td></tr>`;
  }
  html += `</table>`;

  // Customer info (only when not walk-in)
  if (inv.customer_name && inv.customer_name !== 'Walk-in') {
    html += `<div class="dm-info-box">`;
    html += `<div class="dm-box-label">Customer</div>`;
    html += `<div class="dm-box-name">${inv.customer_name}</div>`;
    if (inv.customer_address) html += `<div class="dm-box-sub">${inv.customer_address}</div>`;
    if (inv.customer_phone) html += `<div class="dm-box-sub">Tel: ${inv.customer_phone}</div>`;
    html += `</div>`;
  }

  if (inv.release_mode === 'partial') {
    html += `<div class="dm-warning">** PARTIAL RELEASE — SCAN QR CODE BELOW TO RELEASE ITEMS **</div>`;
  }

  // Items table
  const items = inv.items || [];
  const hasDiscount = items.some(i => parseFloat(i.discount_amount) > 0);
  html += `<table class="dm-items-table"><thead><tr>`;
  html += `<th style="width:4%">#</th>`;
  html += `<th>ITEM DESCRIPTION</th>`;
  html += `<th class="c" style="width:7%">QTY</th>`;
  html += `<th class="r" style="width:16%">UNIT PRICE</th>`;
  if (hasDiscount) html += `<th class="r" style="width:12%">DISC</th>`;
  html += `<th class="r" style="width:15%">LINE TOTAL</th>`;
  html += `</tr></thead><tbody>`;

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const qty   = parseFloat(item.quantity) || 0;
    const rate  = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const disc  = parseFloat(item.discount_amount) || 0;
    const total = parseFloat(item.total) || (qty * rate - disc);
    html += `<tr>`;
    html += `<td class="c">${i + 1}</td>`;
    html += `<td>${item.product_name || ''}</td>`;
    html += `<td class="c">${qty}</td>`;
    html += `<td class="r">${formatPHP(rate)}</td>`;
    if (hasDiscount) html += `<td class="r">${disc > 0 ? formatPHP(disc) : '-'}</td>`;
    html += `<td class="r strong">${formatPHP(total)}</td>`;
    html += `</tr>`;
  }
  html += `</tbody></table>`;

  // Totals
  html += `<div class="dm-totals">`;
  html += `<div class="dm-tot-row"><span class="dm-tot-label">Subtotal:</span><span class="dm-tot-val">${formatPHP(inv.subtotal)}</span></div>`;
  if (parseFloat(inv.overall_discount) > 0) {
    html += `<div class="dm-tot-row"><span class="dm-tot-label">Discount:</span><span class="dm-tot-val">- ${formatPHP(inv.overall_discount)}</span></div>`;
  }
  if (parseFloat(inv.freight) > 0) {
    html += `<div class="dm-tot-row"><span class="dm-tot-label">Freight:</span><span class="dm-tot-val">${formatPHP(inv.freight)}</span></div>`;
  }
  html += `<div class="dm-tot-row dm-grand"><span class="dm-tot-label">GRAND TOTAL:</span><span class="dm-tot-val">${formatPHP(inv.grand_total)}</span></div>`;
  if (parseFloat(inv.amount_paid) > 0 && inv.payment_type !== 'credit') {
    html += `<div class="dm-tot-row"><span class="dm-tot-label">Amount Paid:</span><span class="dm-tot-val">${formatPHP(inv.amount_paid)}</span></div>`;
    const change = (parseFloat(inv.amount_paid) || 0) - (parseFloat(inv.grand_total) || 0);
    if (change > 0) {
      html += `<div class="dm-tot-row"><span class="dm-tot-label">Change:</span><span class="dm-tot-val">${formatPHP(change)}</span></div>`;
    }
  }
  if (inv.payment_type === 'credit' || parseFloat(inv.balance) > 0) {
    html += `<div class="dm-tot-row dm-grand"><span class="dm-tot-label">BALANCE DUE:</span><span class="dm-tot-val">${formatPHP(inv.balance || inv.grand_total)}</span></div>`;
  }
  html += `</div>`;

  // QR Code — placed after totals, before signature
  html += qrImgTagDM(docCode, inv);

  // Acknowledgment + signature
  const today = fmtDate(new Date().toISOString());
  html += `<div style="border-top:1px solid #000;margin-top:20px;padding-top:14px;">`;
  html += `<p style="font-size:11px;margin-bottom:24px;line-height:1.5;">I acknowledge receipt of the items listed above in good physical condition and complete.</p>`;
  html += `<div class="dm-sig-row">`;
  html += `<div class="dm-sig-block"><div class="dm-sig-line"></div><div class="dm-sig-label">Customer Signature</div></div>`;
  html += `<div class="dm-sig-block"><div class="dm-sig-line"></div><div class="dm-sig-label">Printed Name</div></div>`;
  html += `<div class="dm-sig-block"><div class="dm-sig-line" style="display:flex;align-items:flex-end;justify-content:center;padding-bottom:2px;font-size:12px">${today}</div><div class="dm-sig-label">Date</div></div>`;
  html += `</div></div>`;

  // Footer
  html += `<div class="dm-footer">`;
  if (isDraft) {
    html += `<div class="dm-thankyou" style="font-size:13px;">FOR PREPARATION ONLY — NOT YET PAID</div>`;
  } else {
    html += `<div class="dm-thankyou">Thank you for your business!</div>`;
  }
  html += `<div class="dm-disclaimer">${TAX_DISCLAIMER}</div>`;
  html += `</div>`;

  return html;
}

// ── Charge Agreement (Credit / Partial / Cash) — Dot Matrix ───────────────
// Layout matches the Trust Agreement reference: letterhead with logo, company
// info, doc title and invoice/date box repeats on every printed page; items
// table flows across pages; totals box + signature + terms render after the
// last item; receipt number + "Page X of Y" appear in the footer of every page.
function trustReceiptDotMatrix(data, biz, docCode) {
  const inv = data;
  const docTitle = (inv.payment_type === 'cash' || inv.payment_type === 'digital' || inv.payment_type === 'split')
    ? 'SALES INVOICE'
    : 'TRUST AGREEMENT';

  // ── 1. Repeated letterhead (sits in <thead>) ──────────────────────────────
  const logoHtml = biz.logo_url
    ? `<img src="${biz.logo_url}" alt="logo" style="max-width:130px;max-height:90px;object-fit:contain;display:block;" />`
    : '';
  const bizLines = [];
  if (biz.address) bizLines.push(`<div class="dm-biz-detail-lh">${biz.address}</div>`);
  if (biz.email)   bizLines.push(`<div class="dm-biz-detail-lh"><span class="dm-lbl">Email</span>${biz.email}</div>`);
  if (biz.phone)   bizLines.push(`<div class="dm-biz-detail-lh"><span class="dm-lbl">Tel</span>${biz.phone}</div>`);
  if (biz.tin)     bizLines.push(`<div class="dm-biz-detail-lh"><span class="dm-lbl">TIN</span>${biz.tin}</div>`);

  const letterhead = `
    <table class="dm-letterhead">
      <tr>
        <td style="width:140px;">${logoHtml}</td>
        <td class="dm-lh-biz">
          <div class="dm-biz-name-lh">${biz.business_name || 'AgriBooks'}</div>
          ${bizLines.join('')}
        </td>
        <td class="dm-lh-title-cell" style="width:240px;">
          <div class="dm-lh-title">${docTitle}</div>
          <div class="dm-lh-invbox">
            <table>
              <tr><td>Invoice #</td><td class="dm-inv-val">${inv.invoice_number || ''}</td></tr>
              <tr><td>Date</td><td class="dm-inv-val">${fmtDate(inv.invoice_date || inv.created_at || inv.order_date)}</td></tr>
            </table>
          </div>
        </td>
      </tr>
    </table>
  `;

  // ── 2. Footer fallback (sits in <tfoot>) ──────────────────────────────────
  //   The browser's @page @bottom-center prints "Page X of Y" centered. We
  //   also print the receipt # right-aligned in tfoot so even browsers that
  //   skip @page margin boxes still show grounding info on every page.
  const tfootHtml = `
    <div class="dm-foot-receipt">Receipt No: ${inv.invoice_number || ''}</div>
  `;

  // ── 3. Body content (first-page-only billing + meta grid + items + totals) ─
  let body = '';

  // First-page-only: Billing / Shipping address band (only when customer set)
  if (inv.customer_name && inv.customer_name !== 'Walk-in') {
    body += `<table class="dm-addr-band">
      <tr>
        <td class="dm-addr-label">Billing Address</td>
        <td class="dm-addr-val">${inv.customer_name}${inv.customer_address ? `<br/>${inv.customer_address}` : ''}</td>
        <td class="dm-addr-label" style="width:120px;">Shipping Address</td>
        <td class="dm-addr-val">${inv.shipping_name || inv.customer_name}${inv.shipping_address || inv.customer_address ? `<br/>${inv.shipping_address || inv.customer_address}` : ''}</td>
      </tr>
    </table>`;
  }

  // First-page-only: Sales Rep / Payment Terms grid
  body += `<table class="dm-meta-grid">
    <tr>
      <th style="width:50%;">Sales Rep</th>
      <th>Payment Terms</th>
    </tr>
    <tr>
      <td>${inv.cashier_name || ''}</td>
      <td>${inv.terms || (inv.due_date ? `Due ${fmtDate(inv.due_date)}` : 'Due on receipt')}</td>
    </tr>
  </table>`;

  if (inv.release_mode === 'partial') {
    body += `<div class="dm-warning">** PARTIAL RELEASE — SCAN QR CODE BELOW TO RELEASE ITEMS **</div>`;
  }

  // Items table — column structure mirrors the reference. The Discount
  // column is shown only when at least one line actually has a discount;
  // otherwise we drop it entirely so the row stays compact and readable.
  const items = inv.items || [];
  const hasDiscount = items.some(i =>
    parseFloat(i.discount_amount) > 0 || parseFloat(i.discount_value) > 0
  );
  body += `<table class="dm-items-table"><thead><tr>`;
  body += `<th class="c" style="width:24px;">#</th>`;
  body += `<th style="width:24%;">Item</th>`;
  body += `<th style="width:${hasDiscount ? '22' : '28'}%;">Description</th>`;
  body += `<th class="c" style="width:10%;">Qty</th>`;
  body += `<th class="r" style="width:14%;">Unit Price</th>`;
  if (hasDiscount) {
    body += `<th class="r" style="width:12%;">Discount</th>`;
  }
  body += `<th class="r" style="width:16%;">Sub-Total</th>`;
  body += `</tr></thead><tbody>`;

  let rowIdx = 0;
  for (const item of items) {
    rowIdx += 1;
    const qty   = parseFloat(item.quantity) || 0;
    const rate  = parseFloat(item.rate || item.unit_price || item.price) || 0;
    const disc  = parseFloat(item.discount_amount) || 0;
    const total = parseFloat(item.total) || (qty * rate - disc);
    const unit  = item.unit || item.uom || '';
    const desc  = item.description || item.category || '';
    body += `<tr>`;
    body += `<td class="dm-row-num">${rowIdx}</td>`;
    body += `<td>${item.product_name || ''}</td>`;
    body += `<td>${desc}</td>`;
    body += `<td class="c">${qty}${unit ? ' ' + unit : ''}</td>`;
    body += `<td class="r">${formatPHP(rate)}</td>`;
    if (hasDiscount) {
      body += `<td class="r">${disc > 0 ? '- ' + formatPHP(disc) : '—'}</td>`;
    }
    body += `<td class="r strong">${formatPHP(total)}</td>`;
    body += `</tr>`;
  }
  body += `</tbody></table>`;

  // Right-aligned totals box
  body += `<table class="dm-total-box">`;
  body += `<tr><td class="dm-tb-label">Sub-Total</td><td class="dm-tb-val">${formatPHP(inv.subtotal)}</td></tr>`;
  if (parseFloat(inv.overall_discount) > 0) {
    body += `<tr><td class="dm-tb-label">Discount</td><td class="dm-tb-val">- ${formatPHP(inv.overall_discount)}</td></tr>`;
  }
  if (parseFloat(inv.freight) > 0) {
    body += `<tr><td class="dm-tb-label">Freight</td><td class="dm-tb-val">${formatPHP(inv.freight)}</td></tr>`;
  }
  body += `<tr class="dm-tb-grand"><td class="dm-tb-label">Total</td><td class="dm-tb-val">${formatPHP(inv.grand_total)}</td></tr>`;
  if (parseFloat(inv.amount_paid) > 0) {
    body += `<tr><td class="dm-tb-label">Amount Paid</td><td class="dm-tb-val">${formatPHP(inv.amount_paid)}</td></tr>`;
  }
  if (parseFloat(inv.balance) > 0) {
    body += `<tr class="dm-tb-grand"><td class="dm-tb-label">Balance Due</td><td class="dm-tb-val">${formatPHP(inv.balance)}</td></tr>`;
  }
  body += `</table>`;

  // AUTHORIZED REPRESENTATIVE — centered signature line
  body += `<div class="dm-auth-sig">
    <div class="dm-sig-line-c"></div>
    <div class="dm-sig-label-c">Authorized Representative</div>
  </div>`;

  // Customer signature (if captured) — sits below the auth signature
  const sigStampDM = (() => {
    const t  = data.signature_verification_token || '';
    const sa = data.signature_signed_at || '';
    if (!t && !sa) return '';
    const dt    = sa ? new Date(sa) : null;
    const dtStr = dt ? dt.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '';
    return `<div style="font-size:9px;text-align:center;margin-top:3px;">Signed ${dtStr}${t ? ` - v.${t}` : ''}</div>`;
  })();
  if (data.signature_url) {
    body += `<div class="dm-auth-sig" style="margin-top:16px;">
      <img src="${data.signature_url}" alt="signature" style="max-width:300px;max-height:60px;display:block;margin:0 auto 2px;object-fit:contain"/>
      <div class="dm-sig-line-c"></div>
      <div class="dm-sig-label-c">Customer Signature</div>
      ${sigStampDM}
    </div>`;
  } else if (data.bypass_method) {
    body += `<div class="dm-auth-sig" style="margin-top:16px;">
      <div style="margin:8px auto;font-size:10px;border:1px solid #000;padding:4px 8px;display:inline-block;">AUTHORIZED VIA MANAGER PIN</div>
      <div class="dm-sig-label-c">Customer (Bypassed)</div>
      ${sigStampDM}
    </div>`;
  }

  // Terms & Conditions (charge agreement boilerplate)
  const termsTxt = (biz.trust_receipt_terms || '').replace('{business_name}', biz.business_name || 'this business');
  if (termsTxt) {
    body += `<div class="dm-terms-block">
      <div class="dm-terms-head">Terms and Condition</div>
      <div>${termsTxt}</div>
      <div class="dm-terms-copy-key">Cash and Check — White and Pink &nbsp;|&nbsp; Credit — Pink &nbsp;|&nbsp; Store Copy — Yellow</div>
    </div>`;
  }

  // Compact QR strip — consistent placement at the bottom-left,
  // below the Terms block. Same data as the legacy `qrImgTagDM` but
  // ~40% smaller footprint so the page stays dense.
  if (docCode) {
    const url = `${window.location.origin}/doc/${docCode}`;
    const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=${encodeURIComponent(url)}&margin=2&color=000000&bgcolor=ffffff`;
    body += `
      <div class="dm-qr-strip">
        <img src="${qrUrl}" alt="QR Code" width="70" height="70" />
        <div class="dm-qr-strip-info">
          <div class="dm-qr-code">${docCode}</div>
          <div class="dm-qr-scan">Scan to view document</div>
          ${inv.invoice_number ? `<div class="dm-qr-sub">Receipt No: ${inv.invoice_number}</div>` : ''}
          ${inv.invoice_date || inv.created_at || inv.order_date ? `<div class="dm-qr-sub">Date: ${fmtDate(inv.invoice_date || inv.created_at || inv.order_date)}</div>` : ''}
          ${inv.customer_name ? `<div class="dm-qr-sub">Customer: ${inv.customer_name}</div>` : ''}
        </div>
      </div>`;
  }

  // ── 4. Compose the page-frame table (thead+tbody+tfoot) ───────────────────
  return `
    <table class="dm-page-frame">
      <thead><tr><td>${letterhead}</td></tr></thead>
      <tfoot><tr><td>${tfootHtml}</td></tr></tfoot>
      <tbody><tr><td>${body}</td></tr></tbody>
    </table>
  `;
}

// ═══════════════════════════════════════════════════════════════════════════
//  THERMAL DOCUMENTS (keep existing for 58mm printers)
// ═══════════════════════════════════════════════════════════════════════════

function orderSlipThermal(data, biz, docCode) {
  const inv = data;
  let html = buildThermalHeader(biz);
  html += `<div class="doc-title">ORDER SLIP</div>`;
  html += `<div class="meta-row"><span class="label">No:</span><span>${inv.invoice_number || ''}</span></div>`;
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateMaybeTime(inv.invoice_date || inv.created_at || inv.order_date)}</span></div>`;
  if (inv.customer_name && inv.customer_name !== 'Walk-in') html += `<div class="meta-row"><span class="label">Customer:</span><span>${inv.customer_name}</span></div>`;
  html += `<div class="meta-row"><span class="label">Cashier:</span><span>${inv.cashier_name || ''}</span></div>`;
  // Stock release status
  if (inv.release_mode === 'full') {
    html += `<div class="meta-row"><span class="label">Status:</span><span style="font-weight:bold;color:#000">FULLY RELEASED</span></div>`;
  } else if (inv.release_mode === 'partial') {
    html += `<div class="meta-row" style="background:#fef3c7;padding:4px 2px;margin:2px 0;border:1px solid #f59e0b"><span style="font-size:10px;font-weight:bold;color:#92400e">PARTIAL RELEASE - SCAN QR CODE TO RELEASE ITEMS</span></div>`;
  }
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
  html += `<div class="meta-row"><span class="label">Date:</span><span>${fmtDateMaybeTime(inv.invoice_date || inv.created_at || inv.order_date)}</span></div>`;
  html += `<div class="meta-row"><span class="label">Customer:</span><span>${inv.customer_name || ''}</span></div>`;
  if (inv.due_date) html += `<div class="meta-row"><span class="label">Due:</span><span>${fmtDate(inv.due_date)}</span></div>`;
  // Stock release status
  if (inv.release_mode === 'full') {
    html += `<div class="meta-row"><span class="label">Status:</span><span style="font-weight:bold;color:#000">FULLY RELEASED</span></div>`;
  } else if (inv.release_mode === 'partial') {
    html += `<div class="meta-row" style="background:#fef3c7;padding:4px 2px;margin:2px 0;border:1px solid #f59e0b"><span style="font-size:10px;font-weight:bold;color:#92400e">PARTIAL RELEASE - SCAN QR CODE TO RELEASE ITEMS</span></div>`;
  }
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
  // Signature block — render embedded signature image if present
  // Tamper-evident stamp below: signed-at + verification token
  const sigStampThermal = (() => {
    const t = inv.signature_verification_token || '';
    const sa = inv.signature_signed_at || '';
    if (!t && !sa) return '';
    const dt = sa ? new Date(sa) : null;
    const dtStr = dt ? dt.toISOString().slice(0, 16).replace('T', ' ') + 'Z' : '';
    return `<div style="text-align:center;font-size:8px;color:#000;margin-top:2px">Signed ${dtStr}${t ? ` &middot; v.${t}` : ''}</div>`;
  })();
  if (inv.signature_url) {
    html += `<div class="signature-line"><img src="${inv.signature_url}" alt="signature" style="max-width:60%;max-height:50px;margin:6px auto 0;display:block;object-fit:contain"/><div class="line"></div><div class="sig-label">Customer Signature</div>${sigStampThermal}</div>`;
  } else if (inv.bypass_method) {
    html += `<div class="signature-line"><div style="margin-top:6px;font-size:9px;color:#000;border:1px solid #000;padding:3px 6px;display:inline-block">AUTHORIZED VIA MANAGER PIN</div>${sigStampThermal}</div>`;
  } else {
    html += '<div class="signature-line"><div style="margin-top:8px"></div><div class="line"></div><div class="sig-label">Customer Signature &amp; Printed Name</div></div>';
  }
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
    const css = format === 'thermal' ? thermalCSS
              : format === 'dot_matrix' ? dotMatrixCSS
              : fullPageCSS;
    let body = '';
    switch (type) {
      case 'order_slip':
        body = format === 'thermal'     ? orderSlipThermal(data, businessInfo, docCode)
             : format === 'dot_matrix'  ? orderSlipDotMatrix(data, businessInfo, docCode)
             :                           orderSlipFullPage(data, businessInfo, docCode);
        break;
      case 'trust_receipt':
        body = format === 'thermal'     ? trustReceiptThermal(data, businessInfo, docCode)
             : format === 'dot_matrix'  ? trustReceiptDotMatrix(data, businessInfo, docCode)
             :                           trustReceiptFullPage(data, businessInfo, docCode);
        break;
      case 'purchase_order':
        body = format === 'thermal'    ? purchaseOrderThermal(data, businessInfo, docCode)
             : format === 'dot_matrix' ? orderSlipDotMatrix(data, businessInfo, docCode)
             : purchaseOrderFullPage(data, businessInfo, docCode);
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
        body = format === 'thermal'    ? orderSlipThermal(data, businessInfo, docCode)
             : format === 'dot_matrix' ? orderSlipDotMatrix(data, businessInfo, docCode)
             :                          orderSlipFullPage(data, businessInfo, docCode);
    }
    // No window.print() script — the native plugin handles printing
    // Viewport meta: forces CSS viewport = 384px so body width:100% fills the bitmap exactly
    const viewportMeta = format === 'thermal'
      ? '<meta name="viewport" content="width=384, initial-scale=1.0, maximum-scale=1.0">'
      : '';
    return `<!DOCTYPE html><html><head><meta charset="utf-8">${viewportMeta}<title>Receipt</title><style>${css}</style></head><body>${body}</body></html>`;
  },

  print({ type, data, format = 'thermal', businessInfo = {}, docCode = '' }) {
    const css = format === 'thermal' ? thermalCSS
              : format === 'dot_matrix' ? dotMatrixCSS
              : fullPageCSS;
    let body = '';

    switch (type) {
      case 'order_slip':
        body = format === 'thermal'     ? orderSlipThermal(data, businessInfo, docCode)
             : format === 'dot_matrix'  ? orderSlipDotMatrix(data, businessInfo, docCode)
             :                           orderSlipFullPage(data, businessInfo, docCode);
        break;
      case 'trust_receipt':
        body = format === 'thermal'     ? trustReceiptThermal(data, businessInfo, docCode)
             : format === 'dot_matrix'  ? trustReceiptDotMatrix(data, businessInfo, docCode)
             :                           trustReceiptFullPage(data, businessInfo, docCode);
        break;
      case 'purchase_order':
        body = format === 'thermal'    ? purchaseOrderThermal(data, businessInfo, docCode)
             : format === 'dot_matrix' ? orderSlipDotMatrix(data, businessInfo, docCode)
             : purchaseOrderFullPage(data, businessInfo, docCode);
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
        body = format === 'thermal'    ? orderSlipThermal(data, businessInfo, docCode)
             : format === 'dot_matrix' ? orderSlipDotMatrix(data, businessInfo, docCode)
             :                          orderSlipFullPage(data, businessInfo, docCode);
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
