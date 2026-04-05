/**
 * PrintBridge.js — Environment-aware print router.
 *
 * SINGLE entry point for ALL printing in the AgriSmart Terminal.
 * Routes print calls to the correct execution path:
 *
 *   Capacitor APK (H10P device):
 *     PrintEngine.generateHtml() → inline images → H10PPrinterPlugin.printHtml() → native SDK → 58mm paper
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

/**
 * Inline external <img src="https://..."> as base64 data URLs.
 * The H10P native plugin renders HTML in a headless WebView — external images
 * (like QR codes from api.qrserver.com) often fail to load in time before
 * bitmap capture. Converting to data URLs guarantees they render.
 */
async function inlineExternalImages(html) {
  const imgRegex = /<img\s[^>]*src="(https?:\/\/[^"]+)"[^>]*/gi;
  const matches = [...html.matchAll(imgRegex)];
  for (const match of matches) {
    const url = match[1];
    try {
      const resp = await fetch(url, { mode: 'cors' });
      if (!resp.ok) continue;
      const blob = await resp.blob();
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      html = html.replace(url, dataUrl);
    } catch (e) {
      console.warn('[PrintBridge] Failed to inline image, removing:', url);
      // Remove the entire img tag if we can't inline it
      html = html.replace(match[0], '');
    }
  }
  return html;
}

const PrintBridge = {
  /**
   * Main print function — matches PrintEngine.print() signature exactly.
   * Drop-in replacement: swap `import PrintEngine` → `import PrintBridge`.
   */
  async print({ type, data, format = 'thermal', businessInfo = {}, docCode = '' }) {
    if (Capacitor.isNativePlatform()) {
      // Native H10P path: generate HTML, inline images, then send to printer SDK
      try {
        let html = PrintEngine.generateHtml({ type, data, format, businessInfo, docCode });
        html = await inlineExternalImages(html);
        await H10PPrinter.printHtml({ html, format });
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
};

export default PrintBridge;
