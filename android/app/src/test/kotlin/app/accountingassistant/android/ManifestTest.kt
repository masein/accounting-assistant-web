package app.accountingassistant.android

import android.Manifest
import android.content.pm.PackageManager
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** What the app must be allowed, or it fails on a real phone where the emulator never noticed. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])                  // like the screenshot tests: Robolectric's API 36 set-up needs JDK internals
class ManifestTest {
    private fun requested(): List<String> {
        val app = RuntimeEnvironment.getApplication()
        return app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toList()
    }

    /** The app lock is on whenever the phone has a screen lock; without this BiometricPrompt throws at launch. */
    @Test fun theAppLockMayAskForTheFingerprintFaceOrPin() {
        assertTrue(Manifest.permission.USE_BIOMETRIC in requested())
    }
}
