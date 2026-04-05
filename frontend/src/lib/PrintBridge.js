/**
 * PrintBridge.js — Environment-aware print router.
 *
 * SINGLE entry point for ALL printing in the AgriSmart Terminal.
 * Routes print calls to the correct execution path:
 *
 *   Capacitor APK (H10P device):
 *     PrintEngine.generateHtml() → local QR data URL → H10PPrinterPlugin.printHtml() → native SDK → 58mm paper
 *
 *   Web browser (desktop admin / dev):
 *     PrintEngine.print() → window.open() + window.print() → browser print dialog
 *
 * IMPORTANT: Do NOT call PrintEngine.print() directly from terminal components.
 *            Always use PrintBridge.print() so the H10P printer works.
 *
 * Affected call sites (these import PrintBridge instead of PrintEngine):
 *   - TerminalSales.jsx       (sale receipt after checkout)
 *   - TerminalShell.jsx       (QuickScan sheet reprint buttons)
 *   - DocViewerPage.jsx       (Tier 2 reprint buttons)
 *
 * Non-terminal pages (SalesPage, BranchTransferPage, etc.) continue using
 * PrintEngine.print() directly — they are desktop admin pages, not H10P pages.
 */
import { Capacitor } from '@capacitor/core';
import PrintEngine from './PrintEngine';
import { H10PPrinter } from './H10PPrinterPlugin';
import QRCode from 'qrcode'; // static import — ensures it's always bundled (dynamic import can fail in Capacitor WebView)

/**
 * Replace the api.qrserver.com QR image URL with a locally-generated
 * base64 PNG data URL using the 'qrcode' npm package.
 *
 * Why: The H10P renders HTML in a headless Android WebView. External network
 * requests from that WebView are unreliable and slow. Generating locally
 * guarantees the QR code is always embedded and renders instantly.
 */
async function replaceQrWithLocalDataUrl(html) {
  const qrMatch = html.match(/src="(https:\/\/api\.qrserver\.com[^"]+)"/);
  if (!qrMatch) return html;

  const qrUrl = qrMatch[1];
  let qrData = null;
  try {
    qrData = new URL(qrUrl).searchParams.get('data');
  } catch (_) { /* malformed URL, skip */ }

  if (!qrData) return html;

  try {
    const dataUrl = await QRCode.toDataURL(qrData, {
      width: 200,
      margin: 1,
      errorCorrectionLevel: 'M',
      color: { dark: '#000000', light: '#ffffff' },
    });
    return html.replace(qrUrl, dataUrl);
  } catch (e) {
    console.warn('[PrintBridge] Local QR generation failed, removing QR img:', e);
    return html.replace(/<img[^>]*api\.qrserver\.com[^>]*/gi, '');
  }
}

const PrintBridge = {
  /**
   * Main print function — matches PrintEngine.print() signature exactly.
   * Drop-in replacement: swap `import PrintEngine` → `import PrintBridge`.
   * @param {number} [opts.feedLinesAfter] - Blank lines to feed after receipt (default controlled by Java). Use 6–10 if tear cut is tight.
   */
  async print({ type, data, format = 'thermal', businessInfo = {}, docCode = '', feedLinesAfter } = {}) {
    if (Capacitor.isNativePlatform()) {
      // Native H10P path: generate HTML, replace QR with local data URL, then send to printer SDK
      try {
        let html = PrintEngine.generateHtml({ type, data, format, businessInfo, docCode });
        html = await replaceQrWithLocalDataUrl(html);
        const payload = { html, format };
        if (feedLinesAfter != null && Number.isFinite(Number(feedLinesAfter))) {
          payload.feedLinesAfter = Math.max(0, Math.min(24, Math.round(Number(feedLinesAfter))));
        }
        await H10PPrinter.printHtml(payload);
      } catch (err) {
        console.error('[PrintBridge] Native print failed:', err);
        throw err;
      }
    } else {
      // Browser path: use existing PrintEngine (opens popup + window.print())
      PrintEngine.print({ type, data, format, businessInfo, docCode });
    }
  },

  /**
   * Check if the native printer is connected (H10P only).
   * Returns { connected: true/false }.
   * Always returns { connected: false } in a browser.
   */
  async checkPrinterStatus() {
    if (!Capacitor.isNativePlatform()) {
      return { connected: false };
    }
    try {
      return await H10PPrinter.checkStatus();
    } catch {
      return { connected: false };
    }
  },

  /** True when running inside the Capacitor APK */
  isNative() {
    return Capacitor.isNativePlatform();
  },

  /** Pass-through so callers don't need to import PrintEngine for doc type detection */
  getDocType(invoice) {
    return PrintEngine.getDocType(invoice);
  },

  /** Advance paper only (H10P). No-op in browser. */
  async feedPaper(lines = 8) {
    if (!Capacitor.isNativePlatform()) return;
    const n = Math.max(1, Math.min(40, Math.round(Number(lines)) || 8));
    await H10PPrinter.feedPaper({ lines: n });
  },
};

export default PrintBridge;
