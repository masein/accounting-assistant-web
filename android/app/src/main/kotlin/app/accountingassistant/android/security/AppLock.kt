package app.accountingassistant.android.security

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first

private val Context.lockData by preferencesDataStore(name = "app_lock")

/**
 * The app locks behind the phone's own fingerprint, face or PIN (design rule
 * "private on the lock screen"). On by default when the phone has a screen
 * lock; it asks again after [RELOCK_AFTER_MS] in the background.
 */
class AppLock(private val context: Context) {
    private val enabledKey = booleanPreferencesKey("enabled")
    private val keyguard = context.getSystemService(KeyguardManager::class.java)

    /** The phone itself has a screen lock to lean on. */
    val available: Boolean get() = keyguard?.isDeviceSecure == true

    suspend fun enabled(): Boolean = available && (context.lockData.data.first()[enabledKey] ?: true)

    suspend fun setEnabled(on: Boolean) {
        context.lockData.edit { it[enabledKey] = on }
    }

    /**
     * Ask for the fingerprint, face or PIN. [fallback] starts the device's
     * own credential screen on Android 8, which has no biometric prompt here.
     */
    fun prompt(activity: Activity, title: String, onUnlocked: () -> Unit, fallback: (Intent) -> Unit) {
        if (Build.VERSION.SDK_INT >= 28) {
            val builder = BiometricPrompt.Builder(activity).setTitle(title)
            if (Build.VERSION.SDK_INT >= 30) {
                builder.setAllowedAuthenticators(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL)
            } else {
                @Suppress("DEPRECATION") builder.setDeviceCredentialAllowed(true)
            }
            builder.build().authenticate(CancellationSignal(), activity.mainExecutor,
                object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult?) = onUnlocked()
                })
        } else {
            @Suppress("DEPRECATION")
            keyguard?.createConfirmDeviceCredentialIntent(title, null)?.let(fallback) ?: onUnlocked()
        }
    }

    companion object {
        const val RELOCK_AFTER_MS = 2 * 60 * 1000L
    }
}
