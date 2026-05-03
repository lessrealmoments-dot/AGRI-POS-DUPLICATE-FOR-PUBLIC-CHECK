# AgriGateway 3.0 — Full Cursor Build Prompt

## OVERVIEW

Build a complete Android SMS Gateway app called **AgriGateway 3.0** in **Java**, minimum SDK API 26 (Android 8.0).

This app acts as a physical SMS conduit for the AgriBooks web platform. It runs as a persistent foreground service and handles 3 SMS flows:

1. **Flow 1 — Outbound Queue**: Web generates a message → app polls server → app sends via native SmsManager → confirms delivery back to server.
2. **Flow 2 — Incoming Reply**: Customer replies to the phone → app detects incoming SMS → app posts it to server → server classifies which branch/company it belongs to.
3. **Flow 3 — Native Send Sync**: Admin types an SMS directly in the phone's native SMS app → our app detects it → posts to server → shows in web dashboard as "sent by Admin".

**Multi-tenancy is 100% server-side.** The JWT token already embeds `organization_id`. The app just authenticates once and all API calls are automatically scoped to the right company.

---

## PROJECT SETUP

**Package**: `com.agribooks.agrigateway`
**App Name**: AgriGateway 3.0
**Min SDK**: 26 (Android 8.0 Oreo)
**Target SDK**: 34
**Language**: Java only (no Kotlin)

### `build.gradle` (app level) — dependencies:

```gradle
dependencies {
    // Room (local DB for offline buffering)
    implementation "androidx.room:room-runtime:2.6.1"
    annotationProcessor "androidx.room:room-compiler:2.6.1"

    // WorkManager (background retry sync)
    implementation "androidx.work:work-runtime:2.9.0"

    // Retrofit + OkHttp (API calls)
    implementation "com.squareup.retrofit2:retrofit:2.9.0"
    implementation "com.squareup.retrofit2:converter-gson:2.9.0"
    implementation "com.squareup.okhttp3:logging-interceptor:4.12.0"

    // Encrypted SharedPreferences (for JWT token storage)
    implementation "androidx.security:security-crypto:1.1.0-alpha06"

    // Lifecycle Service
    implementation "androidx.lifecycle:lifecycle-service:2.7.0"
    implementation "androidx.lifecycle:lifecycle-runtime:2.7.0"

    // Material Design
    implementation "com.google.android.material:material:1.11.0"
    implementation "androidx.appcompat:appcompat:1.6.1"
    implementation "androidx.constraintlayout:constraintlayout:2.1.4"
    implementation "androidx.recyclerview:recyclerview:1.3.2"
    implementation "androidx.swiperefreshlayout:swiperefreshlayout:1.1.0"
}
```

---

## ANDROIDMANIFEST.XML

```xml
<manifest package="com.agribooks.agrigateway">

    <!-- SMS Permissions -->
    <uses-permission android:name="android.permission.SEND_SMS" />
    <uses-permission android:name="android.permission.RECEIVE_SMS" />
    <uses-permission android:name="android.permission.READ_SMS" />

    <!-- Network -->
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

    <!-- Background / Foreground Service -->
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
    <uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />

    <application
        android:name=".AgriGatewayApp"
        android:label="AgriGateway 3.0"
        android:theme="@style/Theme.AgriGateway">

        <activity android:name=".ui.LoginActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <activity android:name=".ui.MainActivity"
            android:exported="false" />

        <activity android:name=".ui.SettingsActivity"
            android:exported="false" />

        <!-- Foreground Gateway Service -->
        <service android:name=".service.GatewayForegroundService"
            android:foregroundServiceType="specialUse"
            android:exported="false" />

        <!-- Auto-start on boot -->
        <receiver android:name=".receiver.BootReceiver"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
                <action android:name="android.intent.action.MY_PACKAGE_REPLACED" />
            </intent-filter>
        </receiver>

        <!-- Incoming SMS receiver — HIGHEST priority -->
        <receiver android:name=".receiver.SmsReceiver"
            android:exported="true"
            android:permission="android.permission.BROADCAST_SMS">
            <intent-filter android:priority="999">
                <action android:name="android.provider.Telephony.SMS_RECEIVED" />
            </intent-filter>
        </receiver>

    </application>
</manifest>
```

---

## DATABASE SCHEMA (Room)

Create file: `db/AgriGatewayDatabase.java` — Room database with 4 tables.

### Table 1: `device_sent_queue` — Flow 3 (native SMS sent by admin)

```java
@Entity(tableName = "device_sent_queue",
        indices = {@Index(value = "sms_android_id", unique = true)})
public class DeviceSentItem {
    @PrimaryKey(autoGenerate = true) public long id;
    public long sms_android_id;    // Android's _id in content://sms/sent — UNIQUE dedup key
    public String phone;           // recipient number
    public String message;         // message body
    public String sent_at;         // ISO string of when it was sent
    public String status;          // "PENDING" | "SYNCED" | "FAILED_PERMANENT"
    public int attempt_count;
    public long last_attempt_at;   // epoch millis
}
```

### Table 2: `inbox_queue` — Flow 2 (customer replies)

```java
@Entity(tableName = "inbox_queue",
        indices = {@Index(value = "sms_android_id", unique = true)})
public class InboxItem {
    @PrimaryKey(autoGenerate = true) public long id;
    public long sms_android_id;    // Android's _id — UNIQUE dedup key
    public String phone;           // sender's number
    public String message;         // message body
    public String received_at;     // ISO string
    public String status;          // "PENDING" | "SYNCED" | "FAILED_PERMANENT"
    public int attempt_count;
    public long last_attempt_at;
}
```

### Table 3: `outbound_sent_log` — Flow 1 (server-queued outbound)

```java
@Entity(tableName = "outbound_sent_log",
        indices = {@Index(value = "server_queue_id", unique = true)})
public class OutboundLogItem {
    @PrimaryKey(autoGenerate = true) public long id;
    public String server_queue_id; // server's queue item id — UNIQUE dedup key
    public String phone;
    public String status;          // "SENDING" | "SENT" | "FAILED"
    public long created_at;        // epoch millis
}
```

### Table 4: `gateway_logs` — Remote debug logs

```java
@Entity(tableName = "gateway_logs")
public class GatewayLogItem {
    @PrimaryKey(autoGenerate = true) public long id;
    public String level;           // "INFO" | "WARN" | "ERROR" | "DEBUG"
    public String event_type;      // see VALID_EVENTS below
    public String message;
    public String phone;           // optional
    public String device_id;       // optional
    public String created_at;      // ISO string
    public boolean synced;         // false = not yet flushed to server
}
```

Valid event_type values:
`boot`, `poll`, `send_queued`, `sent`, `failed`, `received`, `device_sent`, `sync`, `token_loaded`, `observer_start`, `observer_stop`, `db_error`, `error`, `custom`

### DAOs

Create a DAO interface for each table:

**DeviceSentDao**:
- `insert(DeviceSentItem)` with `onConflict = IGNORE` (dedup via UNIQUE index)
- `getPending()` returns List of items where status = 'PENDING' or (status = 'FAILED_PERMANENT' with attempt_count < 5), ordered by sms_android_id ASC
- `updateStatus(long id, String status, int attempt_count, long last_attempt_at)`
- `markSynced(long id)`
- `getCountByStatus(String status)` returns int

**InboxDao**:
- Same pattern as DeviceSentDao

**OutboundLogDao**:
- `insertIfNotExists(OutboundLogItem)` with `onConflict = IGNORE`
- `existsByServerId(String server_queue_id)` returns boolean
- `updateStatus(long id, String status)`

**GatewayLogDao**:
- `insert(GatewayLogItem)`
- `getUnsynced(int limit)` returns List (limit = 100)
- `markAllSynced(List<Long> ids)` using `@Query("UPDATE gateway_logs SET synced=1 WHERE id IN (:ids)")`
- `deleteOldSynced(long olderThanEpoch)` — cleanup logs older than 24h

---

## SHARED PREFERENCES

Create `storage/TokenStorage.java` using `EncryptedSharedPreferences` (from `androidx.security`):

Store:
- `jwt_token` — the Bearer token
- `server_url_override` — custom URL (empty = use production)
- `last_processed_sms_id` — **Long** — the last Android SMS `_id` from `content://sms/sent` that was processed (default: -1 meaning "first run, initialize from current max")
- `device_id` — a random UUID generated on first launch, never changes

Methods:
- `saveToken(String token)`
- `getToken()` — returns null if not set
- `clearToken()`
- `getServerBaseUrl()` — returns override if set, else `"https://agri-books.com"`
- `saveServerOverride(String url)`
- `getLastProcessedSmsId()` — returns long (-1 if first run)
- `saveLastProcessedSmsId(long id)`
- `getDeviceId()` — generate UUID on first call, persist and return

---

## NETWORK LAYER

### `network/ApiService.java` — Retrofit interface

```java
public interface ApiService {

    // ── AUTH ──
    @POST("api/auth/login")
    Call<LoginResponse> login(@Body LoginRequest body);

    // ── FLOW 1: Outbound Queue ──
    @GET("api/sms/queue/pending")
    Call<List<QueueItem>> getPendingQueue(@Query("limit") int limit);

    @PATCH("api/sms/queue/{id}/mark-sent")
    Call<Void> markSent(@Path("id") String id);

    @PATCH("api/sms/queue/{id}/mark-failed")
    Call<Void> markFailed(@Path("id") String id, @Body MarkFailedBody body);

    // ── FLOW 2: Incoming ──
    @POST("api/sms/inbox")
    Call<Void> postInbox(@Body InboxPayload body);

    // ── FLOW 3: Device Sent ──
    @POST("api/sms/sent-from-device")
    Call<Void> postDeviceSent(@Body DeviceSentPayload body);

    // ── LOGGING ──
    @POST("api/sms/gateway/logs/batch")
    Call<BatchLogResponse> postLogsBatch(@Body BatchLogPayload body);
}
```

Data model classes (plain Java POJOs with Gson annotations):

```
LoginRequest:     email (String), password (String)
LoginResponse:    token (String), user (UserInfo)
UserInfo:         id, email, role, organization_id, full_name
QueueItem:        id, phone, message, customer_id, customer_name, branch_id, branch_name
MarkFailedBody:   error (String)
InboxPayload:     phone (String), message (String), received_at (String)
DeviceSentPayload: phone (String), message (String), sent_at (String)
LogEntry:         level, event_type, message, phone, device_id, created_at
BatchLogPayload:  entries (List<LogEntry>)
BatchLogResponse: inserted (int)
```

### `network/ApiClient.java`

- Singleton Retrofit instance
- Base URL comes from `TokenStorage.getServerBaseUrl()`
- OkHttp interceptor that adds `Authorization: Bearer <token>` header on every request (reads from TokenStorage)
- `HttpLoggingInterceptor` at BASIC level in debug builds
- 30-second connect + read timeout
- **IMPORTANT**: When `TokenStorage.getServerBaseUrl()` changes (user edits settings), call `ApiClient.reset()` to rebuild the Retrofit instance with the new base URL. Store `ApiClient` as a lazily-initialized singleton that can be invalidated.

---

## REMOTE LOGGER

### `logging/RemoteLogger.java` — Singleton

This class:
1. Has a static `addLog(String level, String event_type, String message)` method
2. Has an overloaded `addLog(String level, String event_type, String message, String phone)` method
3. Internally inserts into `gateway_logs` Room table via background thread (use `Executors.newSingleThreadExecutor()`)
4. Has a `flush()` method that:
   - Queries `getUnsynced(100)` from GatewayLogDao
   - If empty, returns immediately
   - Builds BatchLogPayload with the device_id from TokenStorage
   - POSTs to `ApiService.postLogsBatch()`
   - On success: calls `markAllSynced(ids)` and `deleteOldSynced(now - 86400000)`
   - On failure: logs locally at WARN level (do NOT retry logs aggressively to avoid log storms)
5. `flush()` is called:
   - Every 30 seconds from the foreground service
   - On service boot
   - After every Flow 1/2/3 activity (after sending a message, after receiving, after device sent sync)

Use constants for log levels: `INFO`, `WARN`, `ERROR`, `DEBUG`
Use constants for event types listed above.

---

## FOREGROUND SERVICE

### `service/GatewayForegroundService.java`

This is the **heart of the app**. It must:
1. Start as a FOREGROUND service (show persistent notification)
2. Never stop unless the user explicitly logs out
3. Coordinate all 3 flows

**Notification**:
- Channel ID: `agrigateway_service`
- Title: "AgriGateway 3.0 Active"
- Content: "Monitoring SMS — Last sync: {time}" (update this text periodically)
- Small icon: use a simple message/SMS icon
- No user-dismissable

**On `onCreate()`**:
1. Start foreground with notification
2. Initialize `RemoteLogger`, `TokenStorage`, `AgriGatewayDatabase`
3. Register `SentSmsObserver` on `content://sms/sent`
4. Start polling loop (Flow 1) — every 15 seconds
5. Start log flush loop — every 30 seconds
6. Start sync retry loop — every 2 minutes (retry FAILED items in Room)
7. Log `boot` event: `RemoteLogger.addLog("INFO", "boot", "AgriGateway service started, device=" + deviceId)`

**On `onDestroy()`**:
1. Unregister ContentObserver
2. Cancel all handlers/schedulers
3. Log `observer_stop`

**Polling Loop (Flow 1)** — via `Handler.postDelayed`:
```
Every 15 seconds:
  1. Log "INFO", "poll", "Polling server for pending SMS"
  2. Call ApiService.getPendingQueue(limit=50)
  3. For each QueueItem:
     a. Check OutboundLogDao.existsByServerId(item.id) → skip if true (DEDUP)
     b. Insert into outbound_sent_log with status=SENDING
     c. Send via SmsManager (see Outbound Sender section)
  4. On HTTP error: log "WARN", "poll", "Poll failed: " + error.message
```

**Log Flush Loop** — via `Handler.postDelayed`:
```
Every 30 seconds:
  1. Call RemoteLogger.flush()
```

**Sync Retry Loop** — via `Handler.postDelayed`:
```
Every 2 minutes:
  1. Query DeviceSentDao.getPending() — items with status=PENDING or FAILED (attempt < 5)
  2. For each: retry HTTP POST to /sent-from-device
  3. Query InboxDao.getPending() — same
  4. For each: retry HTTP POST to /inbox
```

---

## FLOW 1 — OUTBOUND SENDER

### `sms/SmsQueueSender.java`

Method: `send(QueueItem item, OutboundLogDao logDao)`

```java
// Split message if longer than 160 characters
ArrayList<String> parts = smsManager.divideMessage(item.message);

// Create SENT and DELIVERED PendingIntents
String SENT_ACTION = "SMS_SENT_" + item.id;
String DELIVERED_ACTION = "SMS_DELIVERED_" + item.id;

PendingIntent sentIntent = PendingIntent.getBroadcast(context, 0,
    new Intent(SENT_ACTION), PendingIntent.FLAG_IMMUTABLE);

// Register one-shot BroadcastReceiver for SENT result
context.registerReceiver(new BroadcastReceiver() {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        ctx.unregisterReceiver(this);
        if (getResultCode() == Activity.RESULT_OK) {
            // SUCCESS — mark as sent on server
            apiService.markSent(item.id).enqueue(...);
            logDao.updateStatus(..., "SENT", ...);
            RemoteLogger.addLog("INFO", "sent", "SMS delivered to carrier for " + item.phone, item.phone);
        } else {
            // FAILURE
            String errorMsg = getResultCode() == SmsManager.RESULT_ERROR_NO_SERVICE
                ? "No service" : "Send failed code=" + getResultCode();
            apiService.markFailed(item.id, new MarkFailedBody(errorMsg)).enqueue(...);
            logDao.updateStatus(..., "FAILED", ...);
            RemoteLogger.addLog("WARN", "failed", errorMsg + " for " + item.phone, item.phone);
        }
    }
}, new IntentFilter(SENT_ACTION));

if (parts.size() == 1) {
    smsManager.sendTextMessage(item.phone, null, item.message, sentIntent, null);
} else {
    ArrayList<PendingIntent> sentIntents = new ArrayList<>();
    for (int i = 0; i < parts.size(); i++) sentIntents.add(sentIntent);
    smsManager.sendMultipartTextMessage(item.phone, null, parts, sentIntents, null);
}

RemoteLogger.addLog("INFO", "send_queued",
    "Sending queued SMS to " + item.phone + " (" + parts.size() + " part(s))", item.phone);
```

---

## FLOW 2 — INCOMING SMS RECEIVER

### `receiver/SmsReceiver.java`

```java
public class SmsReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(intent.getAction())) return;

        SmsMessage[] messages = Telephony.Sms.Intents.getMessagesFromIntent(intent);
        if (messages == null || messages.length == 0) return;

        // Combine multipart messages
        String phone = messages[0].getDisplayOriginatingAddress();
        StringBuilder body = new StringBuilder();
        long androidId = 0; // We'll get this from content://sms after a short delay

        for (SmsMessage msg : messages) {
            body.append(msg.getMessageBody());
        }

        String receivedAt = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'",
            java.util.Locale.US)
            .format(new java.util.Date(messages[0].getTimestampMillis()));

        // Store in Room immediately (best-effort — _id may be 0, use timestamp+phone as alternate)
        // Use a hash of (phone + message + timestamp) as a unique identifier stored separately
        // to handle cases where we can't get the Android _id synchronously
        InboxItem item = new InboxItem();
        item.sms_android_id = messages[0].getTimestampMillis(); // use timestamp as provisional id
        item.phone = phone;
        item.message = body.toString();
        item.received_at = receivedAt;
        item.status = "PENDING";
        item.attempt_count = 0;
        item.last_attempt_at = System.currentTimeMillis();

        // Insert into Room on background thread
        ExecutorService executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            AgriGatewayDatabase db = AgriGatewayDatabase.getInstance(context);
            long inserted = db.inboxDao().insert(item);
            if (inserted > 0) {
                // New item inserted (not a duplicate) — try to POST immediately
                postInboxToServer(context, item);
                RemoteLogger.addLog("INFO", "received",
                    "Incoming SMS from " + phone + " stored", phone);
            }
        });
    }

    private void postInboxToServer(Context context, InboxItem item) {
        InboxPayload payload = new InboxPayload(item.phone, item.message, item.received_at);
        ApiClient.getInstance(context).getService().postInbox(payload).enqueue(new Callback<Void>() {
            @Override
            public void onResponse(Call<Void> call, Response<Void> response) {
                if (response.isSuccessful()) {
                    AgriGatewayDatabase.getInstance(context).inboxDao().markSynced(item.id);
                    RemoteLogger.addLog("INFO", "sync", "Inbox synced for " + item.phone, item.phone);
                } else {
                    AgriGatewayDatabase.getInstance(context).inboxDao()
                        .updateStatus(item.id, "FAILED", item.attempt_count + 1, System.currentTimeMillis());
                }
            }
            @Override
            public void onFailure(Call<Void> call, Throwable t) {
                AgriGatewayDatabase.getInstance(context).inboxDao()
                    .updateStatus(item.id, "FAILED", item.attempt_count + 1, System.currentTimeMillis());
            }
        });
    }
}
```

---

## FLOW 3 — NATIVE SMS SEND OBSERVER

### `sms/SentSmsObserver.java`

**THIS IS THE MOST CRITICAL CLASS.** This solves the bug that existed in the old app.

```java
public class SentSmsObserver extends ContentObserver {

    private static final String PREFS_KEY_LAST_ID = "last_processed_sms_id";
    private final Context context;
    private final AgriGatewayDatabase db;
    private final TokenStorage tokenStorage;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    public SentSmsObserver(Handler handler, Context context) {
        super(handler);
        this.context = context;
        this.db = AgriGatewayDatabase.getInstance(context);
        this.tokenStorage = TokenStorage.getInstance(context);
    }

    /**
     * Called once on service start.
     * CRITICAL LOGIC:
     * - If lastProcessedSmsId == -1 (first install): initialize to current max _id.
     *   This prevents spamming all historical SMS on first run.
     * - Otherwise: do NOT reset lastProcessedSmsId. Process from where we left off.
     *   This ensures messages sent while app was backgrounded are NOT missed.
     */
    public void initialize() {
        executor.execute(() -> {
            long lastId = tokenStorage.getLastProcessedSmsId();
            if (lastId == -1L) {
                // First install — find the current max SMS _id and start from there
                long maxId = getCurrentMaxSentSmsId();
                tokenStorage.saveLastProcessedSmsId(maxId);
                RemoteLogger.addLog("INFO", "observer_start",
                    "First run: initialized lastProcessedSmsId to " + maxId);
            } else {
                RemoteLogger.addLog("INFO", "observer_start",
                    "Resuming from lastProcessedSmsId=" + lastId);
                // Process any messages we may have missed while the app was stopped
                processNewSentSms();
            }
        });
    }

    @Override
    public void onChange(boolean selfChange) {
        super.onChange(selfChange);
        executor.execute(this::processNewSentSms);
    }

    private void processNewSentSms() {
        long lastId = tokenStorage.getLastProcessedSmsId();
        if (lastId == -1L) return; // Not initialized yet

        String[] projection = {"_id", "address", "body", "date"};
        String selection = "_id > ? AND type = 2"; // type=2 is SENT in Android SMS content provider
        String[] selectionArgs = {String.valueOf(lastId)};
        String sortOrder = "_id ASC";

        Uri sentUri = Uri.parse("content://sms/sent");
        Cursor cursor = null;
        try {
            cursor = context.getContentResolver().query(sentUri, projection, selection, selectionArgs, sortOrder);
            if (cursor == null || cursor.getCount() == 0) return;

            long newLastId = lastId;

            while (cursor.moveToNext()) {
                long smsId = cursor.getLong(cursor.getColumnIndexOrThrow("_id"));
                String phone = cursor.getString(cursor.getColumnIndexOrThrow("address"));
                String body = cursor.getString(cursor.getColumnIndexOrThrow("body"));
                long dateMillis = cursor.getLong(cursor.getColumnIndexOrThrow("date"));

                String sentAt = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'",
                    java.util.Locale.US)
                    .format(new java.util.Date(dateMillis));

                // Create DeviceSentItem
                DeviceSentItem item = new DeviceSentItem();
                item.sms_android_id = smsId;
                item.phone = phone;
                item.message = body;
                item.sent_at = sentAt;
                item.status = "PENDING";
                item.attempt_count = 0;
                item.last_attempt_at = System.currentTimeMillis();

                // Insert with IGNORE conflict — safe dedup
                long inserted = db.deviceSentDao().insert(item);

                // Update lastId tracker regardless of whether insert was new or duplicate
                // This advances our position so we never re-scan the same range
                if (smsId > newLastId) newLastId = smsId;

                if (inserted > 0) {
                    // New item — post to server immediately
                    postDeviceSentToServer(item);
                    RemoteLogger.addLog("INFO", "device_sent",
                        "Native SMS to " + phone + " detected, id=" + smsId, phone);
                }
            }

            // CRITICAL: Save the new high-water mark AFTER processing all items
            // Even if HTTP fails, we've stored them in Room for retry
            if (newLastId > lastId) {
                tokenStorage.saveLastProcessedSmsId(newLastId);
                RemoteLogger.addLog("DEBUG", "sync",
                    "Updated lastProcessedSmsId from " + lastId + " to " + newLastId);
            }

        } catch (Exception e) {
            RemoteLogger.addLog("ERROR", "db_error", "SentSmsObserver error: " + e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
    }

    private void postDeviceSentToServer(DeviceSentItem item) {
        DeviceSentPayload payload = new DeviceSentPayload(item.phone, item.message, item.sent_at);
        ApiClient.getInstance(context).getService().postDeviceSent(payload).enqueue(new Callback<Void>() {
            @Override
            public void onResponse(Call<Void> call, Response<Void> response) {
                if (response.isSuccessful()) {
                    db.deviceSentDao().markSynced(item.id);
                    RemoteLogger.addLog("INFO", "sync",
                        "Device sent synced for " + item.phone, item.phone);
                } else {
                    db.deviceSentDao().updateStatus(item.id, "FAILED",
                        item.attempt_count + 1, System.currentTimeMillis());
                    RemoteLogger.addLog("WARN", "failed",
                        "Device sent sync failed HTTP " + response.code() + " for " + item.phone, item.phone);
                }
            }
            @Override
            public void onFailure(Call<Void> call, Throwable t) {
                db.deviceSentDao().updateStatus(item.id, "FAILED",
                    item.attempt_count + 1, System.currentTimeMillis());
                RemoteLogger.addLog("WARN", "failed",
                    "Device sent sync network error: " + t.getMessage() + " for " + item.phone, item.phone);
            }
        });
    }

    private long getCurrentMaxSentSmsId() {
        String[] projection = {"_id"};
        String sortOrder = "_id DESC";
        Uri sentUri = Uri.parse("content://sms/sent");
        Cursor cursor = null;
        try {
            cursor = context.getContentResolver().query(sentUri, projection, null, null, sortOrder + " LIMIT 1");
            if (cursor != null && cursor.moveToFirst()) {
                return cursor.getLong(cursor.getColumnIndexOrThrow("_id"));
            }
        } finally {
            if (cursor != null) cursor.close();
        }
        return 0L;
    }
}
```

---

## BOOT RECEIVER

### `receiver/BootReceiver.java`

```java
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) ||
            Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {

            TokenStorage storage = TokenStorage.getInstance(context);
            if (storage.getToken() != null) {
                // Only auto-start if user is logged in
                Intent serviceIntent = new Intent(context, GatewayForegroundService.class);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent);
                } else {
                    context.startService(serviceIntent);
                }
            }
        }
    }
}
```

---

## UI SCREENS

### `ui/LoginActivity.java`

Layout:
- AgriGateway 3.0 logo/title at top
- Email field
- Password field
- "Login" button
- A small expandable section "Advanced: Server URL" containing:
  - Label: "Production URL (default): https://agri-books.com"
  - Text field: "Preview/Custom URL (optional)" — pre-filled with empty, hint: "https://multi-branch-pos-14.preview.emergentagent.com"
  - "Save URL" button
  - Note: "Leave blank to use production URL"

On login button press:
1. Show loading spinner
2. Call `ApiService.login(email, password)`
3. On success: save token via `TokenStorage.saveToken(token)`, start `GatewayForegroundService`, navigate to `MainActivity`
4. On 401: show "Invalid email or password"
5. On network error: show "Cannot reach server — check URL and internet"

Auto-login: on `onCreate()`, if `TokenStorage.getToken() != null`, skip login and go to MainActivity directly (and ensure service is running).

### `ui/MainActivity.java`

This is the dashboard. Show:

**Header**: "AgriGateway 3.0" with a green/red dot indicating service status.

**Status Card**:
- Service Status: Running / Stopped (with start/stop button)
- Server URL: display the active URL
- Logged in as: {email} [{role}]
- Last poll: {timestamp}
- Last incoming: {timestamp}
- Last device-sent sync: {timestamp}

**Stats Cards** (4 cards in a 2x2 grid):
- SMS Sent Today (count from outbound_sent_log where status=SENT and today)
- SMS Received Today (count from inbox_queue where today)
- Pending Queue (count from outbound_sent_log where status=SENDING)
- Failed Syncs (count from device_sent_queue + inbox_queue where status=FAILED)

**Live Logs** — RecyclerView showing last 50 gateway_logs entries, newest at top:
- Each row: [TIMESTAMP] [LEVEL badge] [EVENT] [MESSAGE]
- Color coding: ERROR=red, WARN=amber, INFO=gray, DEBUG=violet
- Manual refresh button + auto-refresh every 10 seconds

**Bottom action bar**:
- "Clear Logs" button
- "Retry Failed Syncs" button (manually triggers the retry loop)
- "Logout" button (stops service, clears token, returns to LoginActivity)

### `ui/SettingsActivity.java`

- Server URL override field (same as in login advanced section)
- "Test Connection" button — calls `GET /api/sms/queue/pending` with current token, shows success/failure
- Battery Optimization: label showing current status + "Disable Battery Optimization" button that opens the system settings
- "Reset lastProcessedSmsId" — danger zone button that resets to -1 (will re-initialize on next run). Show a confirmation dialog with warning.
- App version display

---

## `application/AgriGatewayApp.java`

Application class. In `onCreate()`:
- Initialize `AgriGatewayDatabase`
- Initialize `RemoteLogger` singleton
- Initialize `TokenStorage` singleton

---

## WORKMANAGER SYNC WORKER

### `worker/SyncRetryWorker.java`

Extends `Worker`. Triggered by WorkManager `PeriodicWorkRequest` every 15 minutes.

In `doWork()`:
1. Query all `DeviceSentItem` where `status='FAILED'` and `attempt_count < 5`
2. For each: POST to `/sent-from-device` synchronously (use `.execute()` not `.enqueue()`), update status
3. Query all `InboxItem` where `status='FAILED'` and `attempt_count < 5`
4. For each: POST to `/inbox` synchronously, update status
5. Call `RemoteLogger.flush()` synchronously
6. Return `Result.success()`

Register this in `GatewayForegroundService.onCreate()`:
```java
PeriodicWorkRequest syncWork = new PeriodicWorkRequest.Builder(
    SyncRetryWorker.class, 15, TimeUnit.MINUTES)
    .build();
WorkManager.getInstance(context).enqueueUniquePeriodicWork(
    "sync_retry", ExistingPeriodicWorkPolicy.KEEP, syncWork);
```

---

## AUTHENTICATION FLOW & 401 HANDLING

In `ApiClient.java`, add an OkHttp `Interceptor` that:
1. On every response with code `401`:
   - Clear the stored token (`TokenStorage.clearToken()`)
   - Post a broadcast `com.agribooks.agrigateway.ACTION_TOKEN_EXPIRED`
   - Return the original response (don't retry)
2. `GatewayForegroundService` registers a `BroadcastReceiver` for this action that:
   - Stops the service
   - Starts `LoginActivity` with flag `FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK`

---

## PERMISSIONS HANDLING AT RUNTIME

In `MainActivity.onCreate()`:
Request these permissions at runtime (Android 6+):
- `SEND_SMS`
- `RECEIVE_SMS`
- `READ_SMS`

Show a permission rationale dialog explaining why each is needed. If denied, show a banner "SMS permissions required for gateway to work."

Also check if the app is the **default SMS app** on Android 10+ — if not, show a persistent banner:
> "AgriGateway needs to be set as the default SMS app to reliably detect outgoing messages. Tap to set as default."

This is needed for Flow 3 on Android 10+ because non-default SMS apps cannot read `content://sms`.

---

## KEY BEHAVIORS & EDGE CASES

1. **Duplicate prevention — Flow 1**: `outbound_sent_log` has UNIQUE constraint on `server_queue_id`. If the poll returns the same item twice (because mark-sent failed briefly), the second attempt is silently ignored.

2. **Duplicate prevention — Flow 2**: `inbox_queue` has UNIQUE constraint on `sms_android_id`. Multiple BroadcastReceiver firings for the same SMS are safe.

3. **Duplicate prevention — Flow 3**: `device_sent_queue` has UNIQUE constraint on `sms_android_id`. ContentObserver may fire multiple times for one SMS change — safe.

4. **lastProcessedSmsId never resets**: Only `SettingsActivity.resetLastProcessedSmsId()` can reset it (with user confirmation). Normal app restarts always read from SharedPreferences.

5. **Max retry = 5**: Items with `attempt_count >= 5` are marked `FAILED_PERMANENT` and excluded from auto-retry. User must manually trigger retry from dashboard.

6. **Long SMS (>160 chars)**: Flow 1 uses `divideMessage()` + `sendMultipartTextMessage()` automatically.

7. **Outgoing SMS from non-native app (e.g., WhatsApp)**: The ContentObserver watches `content://sms/sent` which only contains native SMS. WhatsApp and similar apps use their own protocols, so they will NOT appear — this is correct behavior.

8. **Service restart after OOM kill**: The service declares `START_STICKY`. When Android kills it for memory, it will restart automatically.

9. **Network retry timing**: Failed items are retried every 2 minutes by the foreground service inner loop AND every 15 minutes by WorkManager. This ensures both quick recovery (when briefly offline) and guaranteed delivery (even after long offline periods).

---

## API ENDPOINTS REFERENCE

**Base URL**: From `TokenStorage.getServerBaseUrl()` — default `https://agri-books.com`

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/auth/login` | None | Login, get JWT |
| GET | `/api/sms/queue/pending?limit=50` | Bearer | Get pending SMS to send |
| PATCH | `/api/sms/queue/{id}/mark-sent` | Bearer | Confirm SMS delivered |
| PATCH | `/api/sms/queue/{id}/mark-failed` | Bearer | Report send failure |
| POST | `/api/sms/inbox` | Bearer | Post incoming customer SMS |
| POST | `/api/sms/sent-from-device` | Bearer | Post native SMS admin sent |
| POST | `/api/sms/gateway/logs/batch` | Bearer | Bulk push debug logs |

**Login request body**:
```json
{"email": "user@example.com", "password": "password123"}
```
**Login response**:
```json
{"token": "eyJ...", "user": {"id": "...", "email": "...", "role": "admin", "organization_id": "...", "full_name": "..."}}
```

**Queue item structure** (GET /api/sms/queue/pending response is an array):
```json
[{
  "id": "abc123",
  "phone": "09123456789",
  "message": "Hi Juan...\n\n- AgriBooks | Branch 1",
  "customer_id": "cust_001",
  "customer_name": "Juan dela Cruz",
  "branch_id": "branch_01",
  "branch_name": "Branch 1"
}]
```

**Inbox POST body**:
```json
{"phone": "09123456789", "message": "Bayad na po ako", "received_at": "2025-04-02T10:30:00Z"}
```

**Device sent POST body**:
```json
{"phone": "09123456789", "message": "Ok noted bayad na po kayo", "sent_at": "2025-04-02T10:35:00Z"}
```

**Log batch POST body**:
```json
{
  "entries": [
    {
      "level": "INFO",
      "event_type": "sent",
      "message": "SMS sent to 09123456789",
      "phone": "09123456789",
      "device_id": "uuid-here",
      "created_at": "2025-04-02T10:35:00Z"
    }
  ]
}
```

---

## DIRECTORY STRUCTURE

```
app/src/main/java/com/agribooks/agrigateway/
├── AgriGatewayApp.java
├── db/
│   ├── AgriGatewayDatabase.java      (Room DB singleton)
│   ├── entity/
│   │   ├── DeviceSentItem.java
│   │   ├── InboxItem.java
│   │   ├── OutboundLogItem.java
│   │   └── GatewayLogItem.java
│   └── dao/
│       ├── DeviceSentDao.java
│       ├── InboxDao.java
│       ├── OutboundLogDao.java
│       └── GatewayLogDao.java
├── network/
│   ├── ApiClient.java
│   ├── ApiService.java
│   └── model/
│       ├── LoginRequest.java
│       ├── LoginResponse.java
│       ├── UserInfo.java
│       ├── QueueItem.java
│       ├── MarkFailedBody.java
│       ├── InboxPayload.java
│       ├── DeviceSentPayload.java
│       ├── LogEntry.java
│       ├── BatchLogPayload.java
│       └── BatchLogResponse.java
├── storage/
│   └── TokenStorage.java
├── logging/
│   └── RemoteLogger.java
├── sms/
│   ├── SmsQueueSender.java
│   └── SentSmsObserver.java
├── receiver/
│   ├── SmsReceiver.java
│   └── BootReceiver.java
├── service/
│   └── GatewayForegroundService.java
├── worker/
│   └── SyncRetryWorker.java
└── ui/
    ├── LoginActivity.java
    ├── MainActivity.java
    └── SettingsActivity.java

app/src/main/res/
├── layout/
│   ├── activity_login.xml
│   ├── activity_main.xml
│   ├── activity_settings.xml
│   └── item_log_row.xml
└── values/
    ├── strings.xml
    ├── colors.xml
    └── themes.xml
```

---

## NOTES FOR CURSOR

1. Do NOT use deprecated `android.telephony.SmsManager.getDefault()` — use `context.getSystemService(SmsManager.class)` on API 31+ and fallback for lower APIs.
2. All Room database operations MUST run on a background thread. Never call Room DAOs on the main thread.
3. The `GatewayForegroundService` inner polling loops use `Handler(Looper.getMainLooper())` with `postDelayed` — this is fine since the actual work is dispatched to background threads.
4. For `EncryptedSharedPreferences`, use `MasterKey.Builder` with `KeyScheme.AES256_GCM` as the master key scheme.
5. The app should be tested with the server at `https://multi-branch-pos-14.preview.emergentagent.com` during development — this URL should be the default text in the "Preview URL" field in settings.
6. Target a clean, professional Material Design 3 UI with the color scheme: primary green `#1A4D2E`, accent amber `#F59E0B`, background white `#FAFAFA`.
7. Keep all UI text in English.
