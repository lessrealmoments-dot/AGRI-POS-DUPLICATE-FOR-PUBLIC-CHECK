# AgriSMS Gateway 2.0 — Code Analysis & Targeted Fix Prompt

## ANALYSIS FINDINGS (What I Read)

After reading all 35 source files, here is the complete picture:

---

## WHAT IS ALREADY WORKING CORRECTLY

- **Flow 1 (Outbound Queue):** `SyncEngine.fetchAndQueueOutgoing()` + `sendPendingOutgoing()` + `SmsSentReceiver` — fully implemented with multipart support and `SENT` PendingIntent confirmation. ✓
- **Flow 2 (Incoming SMS):** `SmsDeliverReceiver` (when default SMS app) + `SmsReceivedFallbackReceiver` (fallback) → `SmsInbound.handle()` → saves to Room DB → posts to `/api/sms/inbox`. ✓
- **Flow 3 (Native send sync):** `SentSmsObserver` → saves to Room DB as `source="device"` → `SyncEngine.syncOne()` → posts to `/api/sms/sent-from-device`. ✓
- **`initLastProcessedId()` is correct** — reads from SharedPreferences (`agrisms_sent_prefs`, key `last_processed_sms_id`) first, only initializes from max `_id` on first install. ✓
- **Room DB** — `LocalMessage`, `LogEntry`, `SentQueueRecord` all properly implemented with background thread access. ✓
- **`RemoteLogger`** — logs stored in Room DB, flushed to `/api/sms/gateway/logs/batch` via `SmsApiClient.postGatewayLogsBatch()`. ✓
- **`SentSmsBackendGuard`** — fingerprint-based dedup prevents queue-sent messages from being double-posted. ✓
- **Auto re-login on 401** — `SmsApiClient.tryRelogin()` handles expired tokens automatically. ✓
- **Battery-optimized polling** — 15s when charging, 60s on battery, via `AlarmManager.setExactAndAllowWhileIdle()`. ✓
- **All permissions in manifest** — `SEND_SMS`, `RECEIVE_SMS`, `READ_SMS`, `INTERNET`, `FOREGROUND_SERVICE`, `RECEIVE_BOOT_COMPLETED`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, `SCHEDULE_EXACT_ALARM` all present. ✓
- **Default SMS app support** — `SmsRoleHelper`, `SmsDeliverReceiver`, `MmsReceiver`, `HeadlessSmsSendService` all properly declared. ✓

---

## BUGS FOUND (3 total)

### BUG 1 — CRITICAL: Wrong sort order causes duplicate native SMS posts (Flow 3)

**File:** `SentSmsObserver.java`
**Method:** `scanNewSentSms()`
**Line:** The cursor query at the bottom of the method

**The bug:**
```java
// CURRENT CODE — WRONG
cursor = resolver.query(SENT_URI, projection, selection, selectionArgs,
    Telephony.Sms.Sent.DEFAULT_SORT_ORDER);
```

`Telephony.Sms.Sent.DEFAULT_SORT_ORDER` is `"date DESC"` — newest row first.

The scan processes rows newest → oldest and updates `lastProcessedSmsId = id` on each iteration. After the loop, `lastProcessedSmsId` holds the **oldest** id in the batch (the last row processed). On the NEXT scan, the query `_id > lastProcessedSmsId` re-finds all the newer rows, causing them to be posted to the server **again**.

**Example:**
- Phone sends messages with ids: 10, 11, 12
- DESC scan processes: 12 → save 12, then 11 → save 11, then 10 → save 10
- After loop: `lastProcessedSmsId = 10`
- Next scan: `_id > 10` finds 11, 12 — **posted again**

**The fix:**
```java
// CORRECT — process oldest first, lastId ends up as the maximum seen
cursor = resolver.query(SENT_URI, projection, selection, selectionArgs,
    BaseColumns._ID + " ASC");
```

With ASC: processes 10 → 11 → 12. After loop: `lastProcessedSmsId = 12`. Next scan: `_id > 12` — nothing new. ✓

---

### BUG 2 — MINOR: Preview server URL is wrong

**File:** `GatewayPrefs.java`
**Line:** `public static final String SERVER_PREVIEW = "https://po-discount-align.preview.emergentagent.com";`

**The bug:** This hardcoded preview URL doesn't match the actual server. The correct current preview URL is:
`https://po-discount-align.preview.emergentagent.com`

**The fix:**
```java
public static final String SERVER_PREVIEW = "https://po-discount-align.preview.emergentagent.com";
```

---

### BUG 3 — MINOR: Queue fetch limit is only 10

**File:** `SmsApiClient.java`
**Method:** `fetchPendingQueue()`
**Line:** `authorizedGet(ctx, "/api/sms/queue/pending?limit=10", cb);`

When a credit blast is sent to 50+ customers, only 10 get processed per 15-second poll. This is slow.

**The fix:**
```java
authorizedGet(ctx, "/api/sms/queue/pending?limit=50", cb);
```

---

## CURSOR PROMPT — APPLY THESE 3 TARGETED FIXES

Paste this to Cursor to fix the issues:

---

**You are making 3 targeted bug fixes to the existing Android Java project. Do NOT rewrite any other code. Only change exactly what is described below.**

---

### Fix 1 of 3 — `SentSmsObserver.java` (Critical)

In the `scanNewSentSms()` method, find the cursor query that looks like this:

```java
cursor = resolver.query(SENT_URI, projection, selection, selectionArgs, Telephony.Sms.Sent.DEFAULT_SORT_ORDER);
```

Change ONLY the last argument from `Telephony.Sms.Sent.DEFAULT_SORT_ORDER` to `BaseColumns._ID + " ASC"`:

```java
cursor = resolver.query(SENT_URI, projection, selection, selectionArgs, BaseColumns._ID + " ASC");
```

**Why:** `DEFAULT_SORT_ORDER` is `"date DESC"` (newest first). Processing newest-to-oldest means `lastProcessedSmsId` ends up as the oldest id seen. The next scan re-finds everything newer than that oldest id, causing duplicate POSTs to the server. Sorting `_id ASC` processes oldest-to-newest so `lastProcessedSmsId` correctly ends at the highest (most recent) id.

---

### Fix 2 of 3 — `GatewayPrefs.java` (Minor)

Find this line:
```java
public static final String SERVER_PREVIEW = "https://po-discount-align.preview.emergentagent.com";
```

Change it to:
```java
public static final String SERVER_PREVIEW = "https://po-discount-align.preview.emergentagent.com";
```

---

### Fix 3 of 3 — `SmsApiClient.java` (Minor)

Find the `fetchPendingQueue` method:
```java
public static void fetchPendingQueue(Context ctx, StringCallback cb) {
    authorizedGet(ctx, "/api/sms/queue/pending?limit=10", cb);
}
```

Change `limit=10` to `limit=50`:
```java
public static void fetchPendingQueue(Context ctx, StringCallback cb) {
    authorizedGet(ctx, "/api/sms/queue/pending?limit=50", cb);
}
```

---

**That's it. These are the only 3 changes needed. Do not touch any other files.**

---

## TESTING AFTER THE FIX

1. Build and install the updated APK.
2. Open the app → Settings → set URL to `https://po-discount-align.preview.emergentagent.com` → login.
3. **Test Flow 3 fix:** Type a message in the native SMS app to any contact. Open the web dashboard → Messages → Conversations. The message should appear within 15 seconds attributed as "Admin (via device)". Restart the app and verify no duplicate messages appear.
4. **Test Flow 1:** From the web, compose and send a message. It should be sent by the phone within 15s.
5. **Test Flow 2:** From another phone, send an SMS to the gateway phone. It should appear in the web Conversations tab.
6. **Monitor logs:** Web dashboard → Messages → Gateway Log tab should show live activity including `device_sent`, `sent`, `received`, `poll` events.

---

## IMPORTANT NOTE ON DEFAULT SMS APP

For **Flow 2 (incoming)** and **Flow 3 (native send sync)** to work reliably on Android 10+, the app MUST be set as the **default SMS app**. The app already shows a notification when it's not the default. Make sure to set it as default on the admin phone.

- Go to: Settings → Apps → Default apps → SMS app → Select "AgriSMS Gateway"

Without this, `content://sms/sent` access may be restricted and `SMS_DELIVER` won't fire.
