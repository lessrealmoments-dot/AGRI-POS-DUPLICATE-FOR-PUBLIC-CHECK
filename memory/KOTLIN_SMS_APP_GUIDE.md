# AgriBooks SMS Gateway — Android App Setup Guide

## What This App Does
- Runs **silently and automatically** on your admin phone — no button pressing needed
- Starts itself automatically when the phone boots
- Every 30 seconds, checks AgriBooks for pending SMS messages
- Sends them via your Globe SIM, completely in the background
- Auto-refreshes its login token — never expires
- Shows a persistent notification so Android won't kill it

---

## STEP 1: Create New Project in Android Studio

1. Open Android Studio
2. Click **"New Project"** → **"Empty Views Activity"** → Next
3. Fill in:
   - **Name:** `AgriSMS Gateway`
   - **Package name:** `com.agribooks.smsgateway`
   - **Language:** `Kotlin`
   - **Minimum SDK:** `API 26 (Android 8.0)`
   - **Build configuration language:** `Kotlin DSL`
4. Click **Finish**

---

## STEP 2: Update build.gradle.kts (Module: app)

Open `app/build.gradle.kts` and replace the `dependencies { }` block with:

```kotlin
dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.json:json:20231013")
}
```

Click **"Sync Now"** when the yellow bar appears.

---

## STEP 3: AndroidManifest.xml

Replace the entire content of `app/src/main/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <uses-permission android:name="android.permission.SEND_SMS" />
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="AgriSMS Gateway"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/Theme.Material3.DayNight.NoActionBar"
        tools:targetApi="31">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:label="AgriSMS Gateway">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <!-- Foreground service -->
        <service
            android:name=".SmsGatewayService"
            android:foregroundServiceType="specialUse"
            android:exported="false" />

        <!-- Auto-start on phone boot -->
        <receiver
            android:name=".BootReceiver"
            android:enabled="true"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
                <action android:name="android.intent.action.QUICKBOOT_POWERON" />
            </intent-filter>
        </receiver>

    </application>
</manifest>
```

---

## STEP 4: Layout (activity_main.xml)

Replace `app/src/main/res/layout/activity_main.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:orientation="vertical"
    android:padding="24dp"
    android:background="#F5F5F0">

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="AgriSMS Gateway"
        android:textSize="24sp"
        android:textStyle="bold"
        android:textColor="#1A4D2E"
        android:layout_marginBottom="4dp" />

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="Fully automatic — runs silently in background"
        android:textSize="13sp"
        android:textColor="#94a3b8"
        android:layout_marginBottom="24dp" />

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="Server" android:textSize="12sp" android:textColor="#64748b"
        android:layout_marginBottom="4dp" />

    <!-- Quick-select buttons -->
    <LinearLayout android:layout_width="match_parent" android:layout_height="wrap_content"
        android:orientation="horizontal" android:layout_marginBottom="8dp">

        <Button android:id="@+id/btnProduction"
            android:layout_width="0dp" android:layout_height="40dp"
            android:layout_weight="1" android:layout_marginEnd="6dp"
            android:text="agri-books.com"
            android:textSize="11sp" android:backgroundTint="#1A4D2E" />

        <Button android:id="@+id/btnPreview"
            android:layout_width="0dp" android:layout_height="40dp"
            android:layout_weight="1"
            android:text="Preview (Emergent)"
            android:textSize="11sp" android:backgroundTint="#475569" />

    </LinearLayout>

    <EditText android:id="@+id/serverUrlInput"
        android:layout_width="match_parent" android:layout_height="48dp"
        android:hint="or type a custom URL"
        android:inputType="textUri" android:textSize="13sp"
        android:background="@android:color/white" android:padding="12dp"
        android:layout_marginBottom="12dp" />

    <!-- Email -->
    <TextView android:layout_width="wrap_content" android:layout_height="wrap_content"
        android:text="Email" android:textSize="12sp" android:textColor="#64748b"
        android:layout_marginBottom="4dp" />
    <EditText android:id="@+id/emailInput"
        android:layout_width="match_parent" android:layout_height="48dp"
        android:hint="admin@yourcompany.com"
        android:inputType="textEmailAddress" android:textSize="14sp"
        android:background="@android:color/white" android:padding="12dp"
        android:layout_marginBottom="12dp" />

    <!-- Password -->
    <TextView android:layout_width="wrap_content" android:layout_height="wrap_content"
        android:text="Password" android:textSize="12sp" android:textColor="#64748b"
        android:layout_marginBottom="4dp" />
    <EditText android:id="@+id/passwordInput"
        android:layout_width="match_parent" android:layout_height="48dp"
        android:hint="Your password"
        android:inputType="textPassword" android:textSize="14sp"
        android:background="@android:color/white" android:padding="12dp"
        android:layout_marginBottom="16dp" />

    <!-- Status Card -->
    <LinearLayout android:layout_width="match_parent" android:layout_height="wrap_content"
        android:orientation="vertical" android:background="@android:color/white"
        android:padding="16dp" android:layout_marginBottom="16dp">

        <TextView android:id="@+id/statusText"
            android:layout_width="wrap_content" android:layout_height="wrap_content"
            android:text="Status: Not configured" android:textSize="16sp"
            android:textStyle="bold" android:textColor="#334155"
            android:layout_marginBottom="8dp" />

        <TextView android:id="@+id/statsText"
            android:layout_width="wrap_content" android:layout_height="wrap_content"
            android:text="Sent: 0  |  Failed: 0  |  Pending: 0"
            android:textSize="13sp" android:textColor="#64748b"
            android:layout_marginBottom="8dp" />

        <TextView android:id="@+id/lastCheckText"
            android:layout_width="wrap_content" android:layout_height="wrap_content"
            android:text="Last check: never" android:textSize="11sp"
            android:textColor="#94a3b8" />
    </LinearLayout>

    <!-- Save & Start Button -->
    <Button android:id="@+id/saveBtn"
        android:layout_width="match_parent" android:layout_height="56dp"
        android:text="Save &amp; Start Gateway"
        android:textSize="16sp" android:backgroundTint="#1A4D2E"
        android:layout_marginBottom="8dp" />

    <Button android:id="@+id/stopBtn"
        android:layout_width="match_parent" android:layout_height="48dp"
        android:text="Stop Gateway"
        android:textSize="14sp" android:backgroundTint="#DC2626"
        android:layout_marginBottom="16dp" />

    <!-- Log -->
    <TextView android:layout_width="wrap_content" android:layout_height="wrap_content"
        android:text="Recent Activity" android:textSize="12sp" android:textColor="#64748b"
        android:layout_marginBottom="4dp" />

    <ScrollView android:layout_width="match_parent" android:layout_height="0dp"
        android:layout_weight="1">
        <TextView android:id="@+id/logText"
            android:layout_width="match_parent" android:layout_height="wrap_content"
            android:text="" android:textSize="11sp" android:textColor="#475569"
            android:fontFamily="monospace" android:background="@android:color/white"
            android:padding="12dp" />
    </ScrollView>

</LinearLayout>
```

---

## STEP 5: SmsGatewayService.kt

Create `SmsGatewayService.kt` in your package folder:

```kotlin
package com.agribooks.smsgateway

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.telephony.SmsManager
import android.util.Log
import androidx.core.app.NotificationCompat
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.*

class SmsGatewayService : Service() {

    companion object {
        const val TAG = "SmsGateway"
        const val CHANNEL_ID = "sms_gateway_channel"
        const val NOTIFICATION_ID = 1
        const val POLL_INTERVAL_MS = 30_000L

        var serverUrl = ""
        var email = ""
        var password = ""
        var authToken = ""
        var sentCount = 0
        var failedCount = 0
        var pendingCount = 0
        var lastCheck = ""
        var logLines = mutableListOf<String>()
        var onUpdate: (() -> Unit)? = null

        fun loadFromPrefs(context: Context) {
            val prefs = context.getSharedPreferences("agri_sms", Context.MODE_PRIVATE)
            serverUrl = prefs.getString("server_url", "") ?: ""
            email = prefs.getString("email", "") ?: ""
            password = prefs.getString("password", "") ?: ""
            authToken = prefs.getString("auth_token", "") ?: ""
        }
    }

    private val handler = Handler(Looper.getMainLooper())
    private val client = OkHttpClient()
    private var running = false

    private val pollRunnable = object : Runnable {
        override fun run() {
            if (running) {
                pollAndSend()
                handler.postDelayed(this, POLL_INTERVAL_MS)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        loadFromPrefs(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        loadFromPrefs(this)
        val notification = buildNotification("Gateway running — checking every 30s")
        startForeground(NOTIFICATION_ID, notification)
        running = true
        addLog("Gateway started (auto)")
        // Login first if we have credentials but no token
        if (authToken.isEmpty() && email.isNotEmpty() && password.isNotEmpty()) {
            loginAndStart()
        } else {
            handler.post(pollRunnable)
        }
        return START_STICKY // Restart automatically if killed
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacks(pollRunnable)
        addLog("Gateway stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** Login with stored credentials and start polling */
    private fun loginAndStart() {
        if (serverUrl.isEmpty() || email.isEmpty() || password.isEmpty()) return
        val json = JSONObject().put("email", email).put("password", password).toString()
        val request = Request.Builder()
            .url("$serverUrl/api/auth/login")
            .post(json.toRequestBody("application/json".toMediaType()))
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                addLog("Auto-login failed: ${e.message}")
                // Retry in 60 seconds
                handler.postDelayed({ loginAndStart() }, 60_000L)
            }
            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string() ?: ""
                response.close()
                if (response.isSuccessful) {
                    try {
                        authToken = JSONObject(body).getString("token")
                        // Save new token
                        getSharedPreferences("agri_sms", MODE_PRIVATE)
                            .edit().putString("auth_token", authToken).apply()
                        addLog("Auto-login OK")
                        handler.post(pollRunnable)
                    } catch (e: Exception) {
                        addLog("Auto-login parse error: ${e.message}")
                    }
                } else {
                    addLog("Auto-login error: ${response.code}")
                }
            }
        })
    }

    private fun pollAndSend() {
        if (serverUrl.isEmpty() || authToken.isEmpty()) {
            // Try to login first
            if (email.isNotEmpty() && password.isNotEmpty()) loginAndStart()
            return
        }

        val request = Request.Builder()
            .url("$serverUrl/api/sms/queue/pending?limit=10")
            .header("Authorization", "Bearer $authToken")
            .get()
            .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                addLog("Poll failed: ${e.message}")
                updateLastCheck()
            }

            override fun onResponse(call: Call, response: Response) {
                updateLastCheck()
                // If token expired, auto-relogin
                if (response.code == 401) {
                    response.close()
                    addLog("Token expired — re-logging in...")
                    authToken = ""
                    loginAndStart()
                    return
                }
                if (!response.isSuccessful) {
                    addLog("Poll error: ${response.code}")
                    response.close()
                    return
                }

                val body = response.body?.string() ?: "[]"
                response.close()

                try {
                    val messages = JSONArray(body)
                    pendingCount = messages.length()
                    if (messages.length() == 0) { onUpdate?.invoke(); return }
                    addLog("Found ${messages.length()} pending")
                    for (i in 0 until messages.length()) {
                        val msg = messages.getJSONObject(i)
                        sendSms(
                            msg.getString("id"),
                            msg.getString("phone"),
                            msg.getString("message"),
                            msg.optString("customer_name", "")
                        )
                    }
                } catch (e: Exception) {
                    addLog("Parse error: ${e.message}")
                }
            }
        })
    }

    private fun sendSms(id: String, phone: String, message: String, customerName: String) {
        try {
            val smsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                getSystemService(SmsManager::class.java)
            } else {
                @Suppress("DEPRECATION")
                SmsManager.getDefault()
            }
            val parts = smsManager.divideMessage(message)
            if (parts.size > 1) {
                smsManager.sendMultipartTextMessage(phone, null, parts, null, null)
            } else {
                smsManager.sendTextMessage(phone, null, message, null, null)
            }
            markSent(id)
            sentCount++
            addLog("Sent to $customerName ($phone)")
            updateNotification("Last sent: $customerName")
        } catch (e: Exception) {
            markFailed(id, e.message ?: "Send failed")
            failedCount++
            addLog("FAILED $customerName: ${e.message}")
        }
    }

    private fun markSent(id: String) {
        val request = Request.Builder()
            .url("$serverUrl/api/sms/queue/$id/mark-sent")
            .header("Authorization", "Bearer $authToken")
            .patch("{}".toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {}
            override fun onResponse(call: Call, response: Response) { response.close() }
        })
    }

    private fun markFailed(id: String, error: String) {
        val json = JSONObject().put("error", error).toString()
        val request = Request.Builder()
            .url("$serverUrl/api/sms/queue/$id/mark-failed")
            .header("Authorization", "Bearer $authToken")
            .patch(json.toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {}
            override fun onResponse(call: Call, response: Response) { response.close() }
        })
    }

    private fun updateLastCheck() {
        lastCheck = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        onUpdate?.invoke()
    }

    private fun addLog(text: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        val line = "[$time] $text"
        Log.d(TAG, line)
        handler.post {
            logLines.add(0, line)
            if (logLines.size > 50) logLines.removeAt(logLines.size - 1)
            onUpdate?.invoke()
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "SMS Gateway", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "AgriBooks SMS sending service" }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val pending = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AgriSMS Gateway")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentIntent(pending)
            .setOngoing(true) // Can't be swiped away
            .build()
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(text))
    }
}
```

---

## STEP 6: BootReceiver.kt (NEW — Auto-start on phone reboot)

Create a new file `BootReceiver.kt`:

```kotlin
package com.agribooks.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED ||
            intent.action == "android.intent.action.QUICKBOOT_POWERON") {

            // Only auto-start if credentials are saved
            val prefs = context.getSharedPreferences("agri_sms", Context.MODE_PRIVATE)
            val serverUrl = prefs.getString("server_url", "") ?: ""
            val email = prefs.getString("email", "") ?: ""

            if (serverUrl.isNotEmpty() && email.isNotEmpty()) {
                val serviceIntent = Intent(context, SmsGatewayService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent)
                } else {
                    context.startService(serviceIntent)
                }
            }
        }
    }
}
```

---

## STEP 7: MainActivity.kt

Replace `app/src/main/java/com/agribooks/smsgateway/MainActivity.kt`:

```kotlin
package com.agribooks.smsgateway

import android.Manifest
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    companion object {
        const val URL_PRODUCTION = "https://agri-books.com"
        const val URL_PREVIEW    = "https://invoice-recovery-fix.preview.emergentagent.com"
    }

    private lateinit var prefs: SharedPreferences
    private lateinit var serverUrlInput: EditText
    private lateinit var emailInput: EditText
    private lateinit var passwordInput: EditText
    private lateinit var saveBtn: Button
    private lateinit var stopBtn: Button
    private lateinit var btnProduction: Button
    private lateinit var btnPreview: Button
    private lateinit var statusText: TextView
    private lateinit var statsText: TextView
    private lateinit var lastCheckText: TextView
    private lateinit var logText: TextView

    // Debounce rapid UI refreshes — only update once per 500ms
    private val uiHandler = Handler(Looper.getMainLooper())
    private var refreshPending = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences("agri_sms", MODE_PRIVATE)

        serverUrlInput  = findViewById(R.id.serverUrlInput)
        emailInput      = findViewById(R.id.emailInput)
        passwordInput   = findViewById(R.id.passwordInput)
        saveBtn         = findViewById(R.id.saveBtn)
        stopBtn         = findViewById(R.id.stopBtn)
        btnProduction   = findViewById(R.id.btnProduction)
        btnPreview      = findViewById(R.id.btnPreview)
        statusText      = findViewById(R.id.statusText)
        statsText       = findViewById(R.id.statsText)
        lastCheckText   = findViewById(R.id.lastCheckText)
        logText         = findViewById(R.id.logText)

        // Load saved values
        serverUrlInput.setText(prefs.getString("server_url", URL_PRODUCTION))
        emailInput.setText(prefs.getString("email", ""))

        requestPermissions()

        // Quick-select server buttons
        btnProduction.setOnClickListener {
            serverUrlInput.setText(URL_PRODUCTION)
            highlightSelected(btnProduction, btnPreview)
        }
        btnPreview.setOnClickListener {
            serverUrlInput.setText(URL_PREVIEW)
            highlightSelected(btnPreview, btnProduction)
        }

        // Highlight whichever matches the saved URL
        val saved = prefs.getString("server_url", URL_PRODUCTION)
        when (saved) {
            URL_PRODUCTION -> highlightSelected(btnProduction, btnPreview)
            URL_PREVIEW    -> highlightSelected(btnPreview, btnProduction)
        }

        saveBtn.setOnClickListener { saveAndStart() }
        stopBtn.setOnClickListener { stopGateway() }

        // Auto-start if already configured
        val savedUrl   = prefs.getString("server_url", "")
        val savedEmail = prefs.getString("email", "")
        if (!savedUrl.isNullOrEmpty() && !savedEmail.isNullOrEmpty()) {
            autoStart()
        }

        refreshUI()
    }

    // Attach callback only while activity is visible — prevents crash on detached views
    override fun onStart() {
        super.onStart()
        SmsGatewayService.onUpdate = { scheduleRefresh() }
        refreshUI()
    }

    override fun onStop() {
        super.onStop()
        // Detach callback so service doesn't call back to a hidden/destroyed activity
        SmsGatewayService.onUpdate = null
        uiHandler.removeCallbacksAndMessages(null)
        refreshPending = false
    }

    /** Debounced refresh — batches rapid updates into one UI pass every 500ms */
    private fun scheduleRefresh() {
        if (refreshPending) return
        refreshPending = true
        uiHandler.postDelayed({
            refreshPending = false
            if (!isFinishing && !isDestroyed) refreshUI()
        }, 500)
    }

    private fun highlightSelected(active: Button, inactive: Button) {
        active.setBackgroundColor(0xFF1A4D2E.toInt())
        inactive.setBackgroundColor(0xFF475569.toInt())
    }

    private fun saveAndStart() {
        val url      = serverUrlInput.text.toString().trim().trimEnd('/')
        val email    = emailInput.text.toString().trim()
        val password = passwordInput.text.toString()

        if (url.isEmpty() || email.isEmpty() || password.isEmpty()) {
            Toast.makeText(this, "Enter server, email and password", Toast.LENGTH_SHORT).show()
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS)
            != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "SMS permission required!", Toast.LENGTH_LONG).show()
            requestPermissions(); return
        }

        prefs.edit()
            .putString("server_url", url)
            .putString("email", email)
            .putString("password", password)
            .putString("auth_token", "")
            .apply()

        SmsGatewayService.serverUrl = url
        SmsGatewayService.email     = email
        SmsGatewayService.password  = password
        SmsGatewayService.authToken = ""

        startGatewayService()
        Toast.makeText(this, "Gateway started! Auto-starts on every reboot.", Toast.LENGTH_LONG).show()
    }

    private fun autoStart() {
        SmsGatewayService.loadFromPrefs(this)
        startGatewayService()
    }

    private fun startGatewayService() {
        val intent = Intent(this, SmsGatewayService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        statusText.text = "Status: Running"
        statusText.setTextColor(0xFF16a34a.toInt())
    }

    private fun stopGateway() {
        stopService(Intent(this, SmsGatewayService::class.java))
        statusText.text = "Status: Stopped"
        statusText.setTextColor(0xFF334155.toInt())
        Toast.makeText(this, "Gateway stopped", Toast.LENGTH_SHORT).show()
    }

    private fun refreshUI() {
        statsText.text    = "Sent: ${SmsGatewayService.sentCount}  |  " +
                "Failed: ${SmsGatewayService.failedCount}  |  " +
                "Pending: ${SmsGatewayService.pendingCount}"
        lastCheckText.text = "Last check: ${SmsGatewayService.lastCheck.ifEmpty { "never" }}"
        // Build log string from thread-safe list snapshot
        logText.text = SmsGatewayService.logLines.toList().joinToString("\n")
    }

    private fun requestPermissions() {
        val perms = mutableListOf(Manifest.permission.SEND_SMS)
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
}
```

---

## STEP 8: Build & Install

1. Connect your admin phone via USB (Developer Options + USB Debugging enabled)
2. Select your device from the dropdown in Android Studio
3. Click the green **Run** button (▶)
4. Wait for it to install (~2-3 minutes first time)

---

## STEP 9: First Time Setup (ONE TIME ONLY)

1. Open **AgriSMS Gateway** on your phone
2. Grant **SMS permission** when asked
3. Fill in:
   - **Server URL:** `https://your-server.emergentagent.com`
   - **Email:** your AgriBooks admin email
   - **Password:** your password
4. Tap **"Save & Start Gateway"**
5. **Done.** You never have to touch it again.

After this:
- Phone reboots → gateway auto-starts ✓
- Token expires → gateway auto-logs in ✓
- New credit sale created → SMS sent within 30 seconds ✓
- Phone locked → SMS still sends ✓

---

## IMPORTANT: Disable Battery Optimization (Required!)

Android aggressively kills background apps. You MUST do this:

1. Go to **Settings → Apps → AgriSMS Gateway**
2. Tap **Battery**
3. Select **"Unrestricted"** (not "Optimized")

On Samsung phones:
- Settings → Device Care → Battery → Background usage limits → Never sleeping apps → Add AgriSMS Gateway

On Xiaomi/MIUI phones:
- Settings → Apps → Manage Apps → AgriSMS Gateway → Battery saver → No restrictions
- Also: Settings → Battery & Performance → App battery saver → AgriSMS Gateway → No restrictions

---

## How It Works (Fully Automatic)

```
Customer makes credit purchase on AgriBooks
    ↓ (immediately)
AgriBooks backend queues SMS (pending)
    ↓ (within 30 seconds)
AgriSMS Gateway app (running silently on store phone)
    ↓ polls server
    ↓ sends SMS via Globe SIM — NO button press
    ↓ reports back: sent
Customer receives text message
```

---

## Troubleshooting

- **SMS not sending** → Check the log in the app — most likely SMS permission was revoked
- **"Poll failed"** → Phone has no internet — check WiFi or mobile data
- **Stopped after phone reboot** → Battery optimization not disabled (see above)
- **"Auto-login error 401"** → Wrong password saved — tap "Save & Start" again with correct password
