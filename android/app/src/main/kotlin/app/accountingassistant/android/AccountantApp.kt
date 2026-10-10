package app.accountingassistant.android

import android.app.Application
import android.os.Build
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.KeystoreSessionStore
import app.accountingassistant.android.data.Outbox
import app.accountingassistant.android.data.OutboxWorker
import app.accountingassistant.android.data.SealedOutboxStore
import kotlinx.serialization.json.Json

class AccountantApp : Application() {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }

    /** One client for the app's life, so the refresh lock is shared by every screen. */
    val api: ApiClient by lazy {
        ApiClient(
            base = BuildConfig.API_BASE,
            store = KeystoreSessionStore(this, json),
            appVersion = BuildConfig.VERSION_NAME,
            language = { resources.configuration.locales[0].language },
            json = json,
        )
    }

    /** One outbox for the app and the background sender, so they never send side by side. */
    val outbox: Outbox by lazy { Outbox(SealedOutboxStore(this, json), api, wake = { OutboxWorker.schedule(this) }) }

    /** Signed out, by choice or because the session ended: nothing typed stays behind. */
    suspend fun forgetOutbox() {
        OutboxWorker.cancel(this)
        outbox.clear()
    }

    /** What the device list shows: "Google Pixel 8". */
    val deviceName: String
        get() = listOf(Build.MANUFACTURER.replaceFirstChar { it.uppercase() }, Build.MODEL).distinct().joinToString(" ")
}
