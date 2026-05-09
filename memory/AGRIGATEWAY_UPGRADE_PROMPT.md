# AgriSMS Gateway — Upgrade Prompt for Cursor

## CONTEXT

You are upgrading an existing Android Kotlin app called **AgriSMS Gateway** (`com.agribooks.smsgateway`).

The app already has:
- `SmsGatewayService.kt` — Foreground service that polls `/api/sms/queue/pending` every 30 seconds and sends via `SmsManager`
- `MainActivity.kt` — UI with server URL, email, password fields
- `BootReceiver.kt` — Auto-starts service on device reboot

**DO NOT touch or rewrite any existing code that is already working.** Only add or modify the specific things listed below.

---

## WHAT NEEDS TO BE ADDED OR FIXED

### Summary of changes:
1. **Manifest** — Add 3 missing permissions + SmsReceiver declaration
2. **New file: `SmsReceiver.kt`** — Detects ALL incoming SMS and posts to server (Flow 2)
3. **New file: `SentSmsObserver.kt`** — Detects SMS typed in native SMS app and posts to server (Flow 3). **This is the critical bug fix.**
4. **New file: `RemoteLogger.kt`** — Buffers activity logs and flushes them to the web dashboard
5. **Update `SmsGatewayService.kt`** — Register/unregister the observer, call remote logger, add a log flush loop

---

## CHANGE 1: AndroidManifest.xml

Add these 3 permissions (insert after the existing `<uses-permission>` lines):

```xml
<uses-permission android:name="android.permission.RECEIVE_SMS" />
<uses-permission android:name="android.permission.READ_SMS" />
<uses-permission android:name="android.permission.BROADCAST_SMS" />
```

Add this `<receiver>` declaration inside `<application>`, alongside the existing `SmsGatewayService` and `BootReceiver`:

```xml
<!-- Receives ALL incoming SMS and posts them to the server -->
<receiver
    android:name=".SmsReceiver"
    android:exported="true"
    android:permission="android.permission.BROADCAST_SMS">
    <intent-filter android:priority="999">
        <action android:name="android.provider.Telephony.SMS_RECEIVED" />
    </intent-filter>
</receiver>
```

---

## CHANGE 2: New file `SmsReceiver.kt`

Create this file in the same package folder as `SmsGatewayService.kt`.

**Purpose:** This BroadcastReceiver fires every time the phone receives an SMS. It posts the message to the server's `/api/sms/inbox` endpoint. The server handles classification (which company/branch it belongs to). The app is a dumb pipe — post everything, no filtering.

```kotlin
package com.agribooks.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.telephony.SmsMessage
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

class SmsReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return

        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (messages.isEmpty()) return

        // Combine multipart message bodies
        val phone = messages[0].displayOriginatingAddress ?: return
        val body = messages.joinToString("") { it.messageBody ?: "" }
        val receivedAt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }
            .format(Date(messages[0].timestampMillis))

        val serverUrl = SmsGatewayService.serverUrl
        val token = SmsGatewayService.authToken

        if (serverUrl.isEmpty() || token.isEmpty()) return

        // Post to server — fire and forget
        val json = JSONObject()
            .put("phone", phone)
            .put("message", body)
            .put("received_at", receivedAt)
            .toString()

        val request = Request.Builder()
            .url("$serverUrl/api/sms/inbox")
            .header("Authorization", "Bearer $token")
            .post(json.toRequestBody("application/json".toMediaType()))
            .build()

        OkHttpClient().newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                RemoteLogger.addLog(context, "WARN", "received",
                    "Inbox post failed: ${e.message}", phone)
            }
            override fun onResponse(call: Call, response: Response) {
                response.close()
                if (response.isSuccessful) {
                    RemoteLogger.addLog(context, "INFO", "received",
                        "Incoming SMS from $phone posted to server", phone)
                } else {
                    RemoteLogger.addLog(context, "WARN", "received",
                        "Inbox post HTTP ${response.code} for $phone", phone)
                }
            }
        })
    }
}
```

---

## CHANGE 3: New file `SentSmsObserver.kt`

Create this file in the same package folder.

**Purpose:** Watches `content://sms/sent` for new outgoing SMS typed directly in the phone's native SMS app. Posts them to `/api/sms/sent-from-device` so they appear on the web dashboard.

**THE CRITICAL FIX IS IN `initialize()`.** The old broken pattern was:
```kotlin
// WRONG — this was the bug. It jumped to the latest _id on EVERY restart,
// permanently skipping messages sent while the app was stopped.
lastId = getMaxSentSmsId()
```

The correct pattern below uses SharedPreferences so the position is NEVER reset:

```kotlin
package com.agribooks.smsgateway

import android.content.Context
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

class SentSmsObserver(
    handler: Handler,
    private val context: Context
) : ContentObserver(handler) {

    companion object {
        private const val PREFS_NAME = "agri_sms"
        private const val KEY_LAST_SMS_ID = "last_processed_sent_sms_id"
        private val SENT_URI = Uri.parse("content://sms/sent")
    }

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val client = OkHttpClient()
    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }

    /**
     * Call this ONCE when the service starts.
     *
     * RULES:
     * - If KEY_LAST_SMS_ID == -1L (first ever install): set it to the current max _id.
     *   This prevents spamming ALL historical messages on first run.
     * - If KEY_LAST_SMS_ID >= 0 (every other start): READ IT and process from there.
     *   This guarantees we never skip messages sent while the app was stopped/backgrounded.
     * - NEVER reset this value to the current max on a normal start. That was the old bug.
     */
    fun initialize() {
        val lastId = prefs.getLong(KEY_LAST_SMS_ID, -1L)
        if (lastId == -1L) {
            // First install — initialize to current max so we don't replay history
            val maxId = getMaxSentSmsId()
            prefs.edit().putLong(KEY_LAST_SMS_ID, maxId).apply()
            RemoteLogger.addLog(context, "INFO", "observer_start",
                "First run: initialized lastSentSmsId to $maxId")
        } else {
            // Subsequent starts — process anything we may have missed while stopped
            RemoteLogger.addLog(context, "INFO", "observer_start",
                "Resuming native send observer from lastSentSmsId=$lastId")
            processNewSentSms()
        }
    }

    override fun onChange(selfChange: Boolean) {
        super.onChange(selfChange)
        processNewSentSms()
    }

    private fun processNewSentSms() {
        val lastId = prefs.getLong(KEY_LAST_SMS_ID, -1L)
        if (lastId < 0) return

        val serverUrl = SmsGatewayService.serverUrl
        val token = SmsGatewayService.authToken
        if (serverUrl.isEmpty() || token.isEmpty()) return

        val projection = arrayOf("_id", "address", "body", "date")
        // type=2 means SENT box in Android SMS content provider
        val selection = "_id > ? AND type = 2"
        val selectionArgs = arrayOf(lastId.toString())
        val sortOrder = "_id ASC"

        val cursor = try {
            context.contentResolver.query(SENT_URI, projection, selection, selectionArgs, sortOrder)
        } catch (e: Exception) {
            RemoteLogger.addLog(context, "ERROR", "db_error",
                "SentSmsObserver query failed: ${e.message}")
            return
        }

        cursor?.use {
            var newLastId = lastId

            while (it.moveToNext()) {
                val smsId = it.getLong(it.getColumnIndexOrThrow("_id"))
                val phone = it.getString(it.getColumnIndexOrThrow("address")) ?: continue
                val body = it.getString(it.getColumnIndexOrThrow("body")) ?: ""
                val dateMillis = it.getLong(it.getColumnIndexOrThrow("date"))
                val sentAt = isoFormat.format(Date(dateMillis))

                // Update our high-water mark BEFORE posting (so restarts don't re-scan even if post fails)
                // Offline resilience note: if the post fails, the message is simply not shown on the dashboard.
                // This is acceptable — the SMS was still sent to the customer.
                if (smsId > newLastId) newLastId = smsId

                postDeviceSent(serverUrl, token, phone, body, sentAt)
                RemoteLogger.addLog(context, "INFO", "device_sent",
                    "Native SMS to $phone detected (id=$smsId)", phone)
            }

            // Save the new high-water mark
            if (newLastId > lastId) {
                prefs.edit().putLong(KEY_LAST_SMS_ID, newLastId).apply()
            }
        }
    }

    private fun postDeviceSent(serverUrl: String, token: String,
                                phone: String, message: String, sentAt: String) {
        val json = JSONObject()
            .put("phone", phone)
            .put("message", message)
            .put("sent_at", sentAt)
            .toString()

        val request = Request.Builder()
            .url("$serverUrl/api/sms/sent-from-device")
            .header("Authorization", "Bearer $token")
            .post(json.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                RemoteLogger.addLog(context, "WARN", "failed",
                    "Device sent post failed: ${e.message}", phone)
            }
            override fun onResponse(call: Call, response: Response) {
                response.close()
                if (!response.isSuccessful) {
                    RemoteLogger.addLog(context, "WARN", "failed",
                        "Device sent HTTP ${response.code} for $phone", phone)
                }
            }
        })
    }

    private fun getMaxSentSmsId(): Long {
        val cursor = try {
            context.contentResolver.query(
                SENT_URI, arrayOf("_id"), null, null, "_id DESC LIMIT 1"
            )
        } catch (e: Exception) { return 0L }

        return cursor?.use {
            if (it.moveToFirst()) it.getLong(it.getColumnIndexOrThrow("_id")) else 0L
        } ?: 0L
    }
}
```

---

## CHANGE 4: New file `RemoteLogger.kt`

Create this file in the same package folder.

**Purpose:** Collects activity logs and periodically flushes them in a batch to the server. The web dashboard's "Gateway Log" tab reads these logs for remote debugging.

```kotlin
package com.agribooks.smsgateway

import android.content.Context
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

object RemoteLogger {

    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }

    // In-memory buffer — max 200 entries to avoid memory issues
    private val buffer = mutableListOf<JSONObject>()
    private val lock = Any()
    private val client = OkHttpClient()

    // Valid log levels
    private val VALID_LEVELS = setOf("INFO", "WARN", "ERROR", "DEBUG")

    // Valid event types (must match server's VALID_EVENTS set)
    private val VALID_EVENTS = setOf(
        "boot", "poll", "send_queued", "sent", "failed",
        "received", "device_sent", "sync", "token_loaded",
        "observer_start", "observer_stop", "db_error", "error", "custom"
    )

    /**
     * Add a log entry to the in-memory buffer.
     * Safe to call from any thread.
     */
    fun addLog(context: Context, level: String, eventType: String,
               message: String, phone: String = "") {
        val safeLevel = if (level.uppercase() in VALID_LEVELS) level.uppercase() else "INFO"
        val safeEvent = if (eventType.lowercase() in VALID_EVENTS) eventType.lowercase() else "custom"

        val deviceId = context.getSharedPreferences("agri_sms", Context.MODE_PRIVATE)
            .getString("device_id", "unknown") ?: "unknown"

        val entry = JSONObject()
            .put("level", safeLevel)
            .put("event_type", safeEvent)
            .put("message", message)
            .put("phone", phone)
            .put("device_id", deviceId)
            .put("created_at", isoFormat.format(Date()))

        synchronized(lock) {
            buffer.add(entry)
            if (buffer.size > 200) buffer.removeAt(0) // keep buffer bounded
        }
    }

    /**
     * Flush buffered logs to the server.
     * Call this periodically from the service (e.g., every 30 seconds).
     */
    fun flush(context: Context) {
        val serverUrl = SmsGatewayService.serverUrl
        val token = SmsGatewayService.authToken
        if (serverUrl.isEmpty() || token.isEmpty()) return

        val toSend: List<JSONObject>
        synchronized(lock) {
            if (buffer.isEmpty()) return
            toSend = buffer.toList()
            buffer.clear()
        }

        val entriesArray = JSONArray()
        toSend.forEach { entriesArray.put(it) }
        val payload = JSONObject().put("entries", entriesArray).toString()

        val request = Request.Builder()
            .url("$serverUrl/api/sms/gateway/logs/batch")
            .header("Authorization", "Bearer $token")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                // Put logs back in the buffer so they're not lost
                synchronized(lock) { buffer.addAll(0, toSend) }
            }
            override fun onResponse(call: Call, response: Response) {
                response.close()
                // Logs were sent — they're already cleared from the buffer
            }
        })
    }
}
```

---

## CHANGE 5: Update `SmsGatewayService.kt`

Make the following targeted changes to the existing `SmsGatewayService.kt`. **Only add the lines marked below — do not rewrite the whole file.**

### 5a. Add a device ID to SharedPreferences on first run

In `onCreate()`, after `loadFromPrefs(this)`, add:

```kotlin
// Generate a persistent device ID for log attribution (one-time)
val prefs = getSharedPreferences("agri_sms", MODE_PRIVATE)
if (!prefs.contains("device_id")) {
    prefs.edit().putString("device_id", java.util.UUID.randomUUID().toString()).apply()
}
```

### 5b. Declare the observer as a class-level property

At the top of the class (with `private val handler` and `private val client`), add:

```kotlin
private var sentSmsObserver: SentSmsObserver? = null
```

### 5c. Register the observer in `onCreate()`

At the END of `onCreate()`, add:

```kotlin
// Register native SMS send observer (Flow 3)
sentSmsObserver = SentSmsObserver(handler, this)
contentResolver.registerContentObserver(
    android.net.Uri.parse("content://sms/sent"),
    true,
    sentSmsObserver!!
)
sentSmsObserver!!.initialize()
```

### 5d. Unregister the observer in `onDestroy()`

At the START of `onDestroy()` (before `running = false`), add:

```kotlin
sentSmsObserver?.let { contentResolver.unregisterContentObserver(it) }
RemoteLogger.addLog(this, "INFO", "observer_stop", "Gateway service stopped")
```

### 5e. Add a log flush loop

Add a new `Runnable` alongside `pollRunnable`:

```kotlin
private val logFlushRunnable = object : Runnable {
    override fun run() {
        if (running) {
            RemoteLogger.flush(this@SmsGatewayService)
            handler.postDelayed(this, 30_000L) // flush every 30 seconds
        }
    }
}
```

### 5f. Start the log flush loop in `onStartCommand()`

After `handler.post(pollRunnable)` (in BOTH places it appears — after auto-login success and in the direct-start path), also add:

```kotlin
handler.post(logFlushRunnable)
```

### 5g. Add a boot log event

In `onStartCommand()`, replace or extend the existing `addLog("Gateway started (auto)")` line with:

```kotlin
addLog("Gateway started (auto)")
RemoteLogger.addLog(this, "INFO", "boot",
    "AgriGateway service started, polling every ${POLL_INTERVAL_MS / 1000}s")
```

### 5h. Add poll events to `pollAndSend()`

In `pollAndSend()`, after the `client.newCall(request).enqueue(...)` is set up, before the `build()` call for the request — actually in the `onResponse` callback where `addLog("Found ${messages.length()} pending")` is, add:

```kotlin
RemoteLogger.addLog(this@SmsGatewayService, "INFO", "poll",
    "Poll OK — ${messages.length()} pending")
```

And in the `sendSms()` method, after the successful `markSent(id)` call:

```kotlin
RemoteLogger.addLog(this, "INFO", "sent",
    "SMS sent to $customerName ($phone)", phone)
```

And after `markFailed(id, ...)`:

```kotlin
RemoteLogger.addLog(this, "WARN", "failed",
    "SMS failed for $customerName ($phone): ${e.message}", phone)
```

---

## CHANGE 6: Update `MainActivity.kt` — Add RECEIVE_SMS + READ_SMS to permission request

Find the `requestPermissions()` method and update the permissions list to include the new ones:

```kotlin
private fun requestPermissions() {
    val perms = mutableListOf(
        Manifest.permission.SEND_SMS,
        Manifest.permission.RECEIVE_SMS,
        Manifest.permission.READ_SMS
    )
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        perms.add(Manifest.permission.POST_NOTIFICATIONS)
    }
    val needed = perms.filter {
        ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
    }
    if (needed.isNotEmpty()) {
        ActivityCompat.requestPermissions(this, needed.toTypedArray(), 100)
    }
}
```

---

## WHAT DOES NOT CHANGE

- `SmsGatewayService.kt` polling logic (Flow 1) — leave it exactly as is
- `MainActivity.kt` UI, save/start/stop logic — leave exactly as is
- `BootReceiver.kt` — leave exactly as is
- The `loadFromPrefs()` companion object — leave exactly as is
- All existing SharedPreferences keys (`server_url`, `email`, `password`, `auth_token`) — leave exactly as is
- `build.gradle.kts` dependencies — no new libraries needed (OkHttp is already there)

---

## HOW THESE CHANGES SOLVE THE KNOWN BUGS

### Bug 1 — Native SMS not syncing to web
**Root cause:** The old `initLastProcessedId()` called `getMaxSentSmsId()` on EVERY app restart, jumping the tracker to the newest SMS `_id`. Any message sent while the app was backgrounded was permanently skipped.

**Fix in `SentSmsObserver.initialize()`:**
- First install only: set `last_processed_sent_sms_id` to the current max (to avoid replaying history)
- Every subsequent start: READ the saved value from SharedPreferences — never overwrite it with the current max
- The tracker only moves FORWARD as new messages are processed

### Bug 2 — No incoming SMS visibility in web dashboard
**Fix:** `SmsReceiver.kt` posts ALL incoming SMS to `/api/sms/inbox`. The server classifies them automatically — known customers go to their branch conversation, unknown numbers go to the admin "Unknown" inbox.

### Bug 3 — No remote debug visibility
**Fix:** `RemoteLogger.kt` buffers all activity events and flushes them in a batch to `/api/sms/gateway/logs/batch` every 30 seconds. The "Gateway Log" tab on the web dashboard will now show live activity from the phone.

---

## TESTING CHECKLIST

After applying these changes:

1. **Build and install** the updated APK on the admin phone.

2. **Flow 1 (Outbound):**
   - From the web, manually compose a message to any customer
   - Within 30 seconds, the phone should send it
   - Check the web Queue tab — message should move from "Pending" to "Sent"

3. **Flow 2 (Incoming):**
   - From another phone, send an SMS to the admin phone's number
   - Check the web Conversations tab → Customers or Unknown section
   - The incoming SMS should appear within seconds

4. **Flow 3 (Native Send Sync):**
   - Open the phone's native SMS app
   - Type and send a message to any number from there (not from AgriGateway)
   - Check the web Conversations tab
   - The message should appear with "Admin (via device)" attribution
   - **Restart the app** and verify no messages were missed

5. **Gateway Log tab (web):**
   - Go to Messages → Gateway Log tab on the web
   - You should see live log entries from the phone
   - Events like `boot`, `poll`, `sent`, `received`, `device_sent` should all appear

---

## SERVER API QUICK REFERENCE

All endpoints require `Authorization: Bearer <token>` header.

| Flow | Method | Endpoint | Body |
|------|--------|----------|------|
| Login | POST | `/api/auth/login` | `{"email":"...","password":"..."}` |
| Flow 1 — Poll | GET | `/api/sms/queue/pending?limit=10` | — |
| Flow 1 — Mark Sent | PATCH | `/api/sms/queue/{id}/mark-sent` | `{}` |
| Flow 1 — Mark Failed | PATCH | `/api/sms/queue/{id}/mark-failed` | `{"error":"..."}` |
| Flow 2 — Incoming | POST | `/api/sms/inbox` | `{"phone":"09...","message":"...","received_at":"ISO"}` |
| Flow 3 — Device Sent | POST | `/api/sms/sent-from-device` | `{"phone":"09...","message":"...","sent_at":"ISO"}` |
| Logging | POST | `/api/sms/gateway/logs/batch` | `{"entries":[{"level":"INFO","event_type":"sent","message":"...","phone":"...","device_id":"...","created_at":"ISO"}]}` |

**Production URL:** `https://agri-books.com`
**Preview/Test URL:** `https://permission-lockdown-1.preview.emergentagent.com`

---

## NOTES FOR CURSOR

- All new `.kt` files go in `app/src/main/java/com/agribooks/smsgateway/`
- The package declaration in each new file must be `package com.agribooks.smsgateway`
- `SmsGatewayService.serverUrl` and `SmsGatewayService.authToken` are accessible from `SmsReceiver` and `SentSmsObserver` because they are declared as `companion object` `var` fields in `SmsGatewayService.kt`
- Do NOT add Room, Retrofit, WorkManager, or any new Gradle dependencies. All 3 new features use only the existing `OkHttpClient` and `SharedPreferences` — no new libraries needed.
- The `SentSmsObserver` constructor requires `Handler(Looper.getMainLooper())` — pass `handler` from `SmsGatewayService`
- Test the app by checking the "Gateway Log" tab on the web dashboard — it shows real-time activity from the phone
