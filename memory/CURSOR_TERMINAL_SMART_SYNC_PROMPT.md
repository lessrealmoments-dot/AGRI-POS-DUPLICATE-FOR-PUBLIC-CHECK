# AgriSmart Terminal — Smart Sync Android Enhancements (Cursor AI Prompt)

## Context
The web app (loaded in Capacitor WebView at `https://agri-books.com`) has been upgraded with a **Smart Sync** system:

1. **Instant Load**: Terminal opens instantly from IndexedDB cache (no network wait)
2. **Background Delta Sync**: Only changed records fetched since `last_sync` timestamp
3. **Inventory Pulse**: Lightweight stock-level polling every 60 seconds via `/api/sync/inventory-pulse`
4. **Sync Indicator**: Header shows "Syncing..." / "Up to date" / "Sync failed" non-blocking

All sync logic runs in JavaScript (IndexedDB). The Android APK is a Capacitor WebView wrapper.

---

## What to Implement

### 1. Ensure WebView IndexedDB Persistence

**File:** `frontend/android/app/src/main/java/com/agribooks/terminal/MainActivity.java`

The Capacitor WebView must persist IndexedDB data across app restarts and "Clear recent apps" gestures. Without this, IndexedDB is wiped and the terminal has to do a full re-download every time.

```java
// In onCreate(), after super.onCreate():
import android.webkit.WebSettings;

// After the bridge/WebView is initialized:
WebView webView = getBridge().getWebView();
WebSettings settings = webView.getSettings();

// Critical: Enable DOM storage and database storage for IndexedDB
settings.setDomStorageEnabled(true);
settings.setDatabaseEnabled(true);

// Set cache mode — load from cache when offline, normal when online
settings.setCacheMode(WebSettings.LOAD_DEFAULT);

// Ensure the WebView data directory path is set (required for IndexedDB on some Android versions)
// This is usually handled by Capacitor, but verify it's not overridden
```

### 2. WebView Cache Mode for Offline Resilience

When the device loses connectivity briefly (common on H10P in warehouse environments), the WebView should serve from cache rather than showing a blank/error page:

```java
// In onResume() or a network change listener:
ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
NetworkInfo ni = cm.getActiveNetworkInfo();
boolean isOnline = ni != null && ni.isConnected();

WebSettings settings = getBridge().getWebView().getSettings();
if (isOnline) {
    settings.setCacheMode(WebSettings.LOAD_DEFAULT);
} else {
    settings.setCacheMode(WebSettings.LOAD_CACHE_ELSE_NETWORK);
}
```

### 3. Prevent WebView Data Wipe on Process Kill

Android may kill background processes. When the user returns, the WebView reloads but IndexedDB should still have data. Add to `AndroidManifest.xml`:

```xml
<activity
    android:name=".MainActivity"
    android:configChanges="orientation|screenSize|keyboardHidden"
    android:launchMode="singleTask">
    <!-- singleTask prevents re-creation on return -->
</activity>
```

### 4. (Optional) Background Sync via WorkManager

For very fresh stock data, you can trigger a web-based sync when the app starts or comes to foreground. This is done by calling the JavaScript sync function from native:

```java
// In onResume():
getBridge().getWebView().evaluateJavascript(
    "if(window.__triggerBackgroundSync) window.__triggerBackgroundSync();",
    null
);
```

Then in the web JS (e.g., TerminalShell.jsx or a global script):
```javascript
// Expose trigger for native layer
window.__triggerBackgroundSync = () => {
    // This calls the existing backgroundSync function
    if (typeof backgroundSync === 'function') backgroundSync(false);
};
```

### 5. Network State Awareness

Don't sync on metered connections unless the user explicitly taps "Sync Now":

```java
// Check if on metered connection
ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
boolean isMetered = cm.isActiveNetworkMetered();

// Pass to WebView
getBridge().getWebView().evaluateJavascript(
    "window.__isMeteredConnection = " + isMetered + ";",
    null
);
```

The web sync code can then check `window.__isMeteredConnection` before auto-polling.

---

## Files to Modify

| File | Changes |
|------|---------|
| `MainActivity.java` | WebSettings for IndexedDB persistence, cache mode, `onResume` sync trigger |
| `AndroidManifest.xml` | `singleTask` launch mode, `configChanges` |
| (Optional) New `NetworkReceiver.java` | Broadcast receiver for connectivity changes → update WebView cache mode |

## Testing

1. Open terminal → pair → scan a product
2. Force-close the app (swipe away from recent apps)
3. Reopen the app → terminal should load **instantly** from cache (no spinner)
4. Put device in airplane mode → terminal should still work with cached data
5. Turn on airplane mode → new products/price changes should sync within 60 seconds

## Important Notes

- The web app already handles all sync logic. The Android side just needs to ensure **IndexedDB persists** and **WebView doesn't wipe data**.
- The `printer-release.aar` SDK is unchanged — no print-related modifications needed.
- All changes are backwards-compatible — if the APK isn't updated, the web sync still works (it just might lose cache on app restart).
