package com.agribooks.terminal;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.os.IBinder;
import android.os.RemoteException;
import android.util.Log;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import recieptservice.com.recieptservice.PrinterInterface;

/**
 * H10PPrinterPlugin — Capacitor native bridge to the H10P built-in 58mm thermal printer.
 *
 * Service: recieptservice.com.recieptservice.service.PrinterService
 * SDK:     printer-release.aar (in app/libs/)
 *
 * Flow:
 *   JS calls printHtml({ html, format })
 *     → renders HTML to Bitmap via headless WebView (same receipt as browser)
 *     → binds to PrinterService
 *     → printer.beginWork() → printer.printBitmap(bitmap) → printer.endWork()
 *
 * Printer width:
 *   58mm thermal  = 384px @ 203 DPI
 *   Full page     = 576px (A4 width approximation)
 */
@CapacitorPlugin(name = "H10PPrinter")
public class H10PPrinterPlugin extends Plugin {

    private PrinterInterface printer = null;
    private boolean printerConnected = false;

    private final ServiceConnection printerServiceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            printer = PrinterInterface.Stub.asInterface(service);
            printerConnected = true;
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            printer = null;
            printerConnected = false;
        }
    };

    @Override
    public void load() {
        bindPrinterService();
    }

    private static final String TAG = "H10PPrinter";

    private void bindPrinterService() {
        try {
            Intent intent = new Intent();
            intent.setClassName(
                "recieptservice.com.recieptservice",
                "recieptservice.com.recieptservice.service.PrinterService"
            );
            boolean bound = getContext().bindService(intent, printerServiceConnection, Context.BIND_AUTO_CREATE);
            Log.d(TAG, "bindService returned: " + bound);
            if (!bound) {
                Log.e(TAG, "bindService failed — is recieptservice app installed?");
            }
        } catch (Exception e) {
            Log.e(TAG, "bindPrinterService exception: " + e.getMessage());
        }
    }

    /**
     * printHtml — accepts HTML string from PrintBridge.js, renders it to bitmap,
     * and sends to the H10P printer SDK.
     *
     * Called from JS:
     *   H10PPrinter.printHtml({ html: '<html>...</html>', format: 'thermal' | 'full_page' })
     */
    @PluginMethod
    public void printHtml(PluginCall call) {
        String html = call.getString("html");
        String format = call.getString("format", "thermal");

        if (html == null || html.isEmpty()) {
            call.reject("html is required");
            return;
        }

        // Wait for printer service on a background thread (bindService is async)
        new Thread(() -> {
            if (!printerConnected || printer == null) {
                bindPrinterService();
                // Poll up to 12 seconds for the service to connect
                int waited = 0;
                while ((!printerConnected || printer == null) && waited < 12000) {
                    try { Thread.sleep(300); } catch (InterruptedException ignored) {}
                    waited += 300;
                }
            }

            if (!printerConnected || printer == null) {
                call.reject("Printer service not connected after 12s. Is the recieptservice app installed?");
                return;
            }

            // 58mm @ 203 DPI = 384px; full page approximation = 576px
            final int widthPx = "full_page".equals(format) ? 576 : 384;

            // Must run on main thread — WebView requires it
            getActivity().runOnUiThread(() -> renderHtmlToBitmap(html, widthPx, bitmap -> {
                if (bitmap == null) {
                    call.reject("Failed to render receipt HTML to bitmap");
                    return;
                }

                // Print on a background thread to avoid blocking the UI
                new Thread(() -> {
                    try {
                        printer.beginWork();
                        printer.printBitmap(bitmap);
                        printer.nextLine(3);   // Feed 3 blank lines so receipt tears cleanly
                        printer.endWork();
                        bitmap.recycle();
                        call.resolve(new JSObject().put("success", true));
                    } catch (RemoteException e) {
                        call.reject("Print SDK error: " + e.getMessage());
                    } catch (Exception e) {
                        call.reject("Print failed: " + e.getMessage());
                    }
                }).start();
            }));
        }).start();
    }

    /**
     * checkStatus — returns whether the printer service is bound and ready.
     * Called from JS as a health check before showing print buttons.
     */
    @PluginMethod
    public void checkStatus(PluginCall call) {
        if (!printerConnected || printer == null) {
            bindPrinterService();
            // Wait up to 4 seconds for connection
            new Thread(() -> {
                int waited = 0;
                while ((!printerConnected || printer == null) && waited < 4000) {
                    try { Thread.sleep(300); } catch (InterruptedException ignored) {}
                    waited += 300;
                }
                JSObject result = new JSObject();
                result.put("connected", printerConnected && printer != null);
                call.resolve(result);
            }).start();
            return;
        }
        JSObject result = new JSObject();
        result.put("connected", printerConnected && printer != null);
        call.resolve(result);
    }

    /**
     * Render an HTML string to a Bitmap using a headless (off-screen) WebView.
     * The WebView is sized to widthPx wide and expands vertically to fit content.
     * Callback is called on the main thread with the rendered Bitmap.
     */
    private void renderHtmlToBitmap(String html, int widthPx, BitmapCallback callback) {
        WebView webView = new WebView(getContext());
        webView.setBackgroundColor(Color.WHITE);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(false); // No JS needed for receipt rendering
        settings.setLoadWithOverviewMode(false);
        settings.setUseWideViewPort(false);
        settings.setTextZoom(100);

        // Initial measure at the target width
        webView.measure(
            View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        );

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                view.post(() -> {
                    // Determine rendered content height
                    int contentHeight = view.getContentHeight();
                    if (contentHeight <= 0) contentHeight = 2000;

                    // Lay out at the final size
                    view.measure(
                        View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
                        View.MeasureSpec.makeMeasureSpec(contentHeight, View.MeasureSpec.EXACTLY)
                    );
                    view.layout(0, 0, widthPx, contentHeight);

                    // Render to bitmap
                    try {
                        Bitmap bitmap = Bitmap.createBitmap(widthPx, contentHeight, Bitmap.Config.RGB_565);
                        Canvas canvas = new Canvas(bitmap);
                        canvas.drawColor(Color.WHITE);
                        view.draw(canvas);
                        callback.onBitmap(bitmap);
                    } catch (OutOfMemoryError e) {
                        callback.onBitmap(null);
                    } finally {
                        view.destroy();
                    }
                });
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                // Still attempt to render whatever loaded
                view.post(() -> {
                    int h = Math.max(view.getContentHeight(), 200);
                    view.measure(
                        View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
                        View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY)
                    );
                    view.layout(0, 0, widthPx, h);
                    Bitmap bitmap = Bitmap.createBitmap(widthPx, h, Bitmap.Config.RGB_565);
                    Canvas canvas = new Canvas(bitmap);
                    canvas.drawColor(Color.WHITE);
                    view.draw(canvas);
                    callback.onBitmap(bitmap);
                    view.destroy();
                });
            }
        });

        // Load the HTML. Note: no base URL needed (receipt is self-contained except QR image).
        // External QR image (api.qrserver.com) loads over internet — H10P has 4G.
        webView.loadDataWithBaseURL(
            "https://agri-books.com",   // baseUrl for relative resource resolution
            html,
            "text/html",
            "UTF-8",
            null
        );
    }

    interface BitmapCallback {
        void onBitmap(Bitmap bitmap);
    }
}
