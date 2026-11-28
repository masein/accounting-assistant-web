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
import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
     * own credential screen on Android 9, whose biometric prompt can't fall
     * back to the PIN (and has no setDeviceCredentialAllowed).
     */
    fun prompt(activity: Activity, title: String, onUnlocked: () -> Unit, fallback: (Intent) -> Unit) {
        if (Build.VERSION.SDK_INT >= 29) {
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

/**
 * Whether the app is locked now. A view model, so it outlives the activity:
 * a rotation, a theme or a language change recreates the activity, and none
 * of them asks for the fingerprint again (scenario CX3). A cold start asks,
 * and so does coming back after [AppLock.RELOCK_AFTER_MS] away, the activity
 * recreated meanwhile or not. A process Android ended starts a new one, locked.
 */
class LockState : ViewModel() {
    private val _locked = MutableStateFlow(false)
    val locked: StateFlow<Boolean> = _locked

    /** The first look in this process is done: later activities only check the time away. */
    private var decided = false
    /** When the app last left the screen ([android.os.SystemClock.elapsedRealtime]), 0 before. */
    private var stoppedAt = 0L

    private fun away(now: Long) = stoppedAt > 0 && now - stoppedAt > AppLock.RELOCK_AFTER_MS

    /**
     * The activity knows who is signed in and whether the lock is on (each
     * time it is created): a cold start locks; a recreated activity, only
     * after long enough away.
     */
    fun opened(signedIn: Boolean, lockOn: Boolean, now: Long) {
        if (!decided) {
            decided = true
            _locked.value = signedIn && lockOn
        } else if (signedIn && lockOn && away(now)) {
            _locked.value = true
        }
    }

    /** Off the screen: the time away starts. */
    fun stopped(now: Long) {
        stoppedAt = now
    }

    /** Back on the screen: locked again after long enough away. */
    fun started(now: Long, signedIn: Boolean, lockOn: Boolean) {
        if (signedIn && lockOn && away(now)) _locked.value = true
    }

    fun unlocked() {
        _locked.value = false
    }

    /** Signed out (the next person signs in with a password): nothing to unlock, and no cold start to come. */
    fun settled() {
        decided = true
        _locked.value = false
        stoppedAt = 0L
    }
}
