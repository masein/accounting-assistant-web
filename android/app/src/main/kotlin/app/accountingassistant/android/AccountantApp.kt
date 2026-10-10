package app.accountingassistant.android

import android.app.Application
import android.os.Build
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.KeystoreSessionStore
import kotlinx.serialization.json.Json

class AccountantApp : Application() {
    /** One client for the app's life, so the refresh lock is shared by every screen. */
    val api: ApiClient by lazy {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }
        ApiClient(
            base = BuildConfig.API_BASE,
            store = KeystoreSessionStore(this, json),
            appVersion = BuildConfig.VERSION_NAME,
            language = { resources.configuration.locales[0].language },
            json = json,
        )
    }

    /** What the device list shows: "Google Pixel 8". */
    val deviceName: String
        get() = listOf(Build.MANUFACTURER.replaceFirstChar { it.uppercase() }, Build.MODEL).distinct().joinToString(" ")
}
