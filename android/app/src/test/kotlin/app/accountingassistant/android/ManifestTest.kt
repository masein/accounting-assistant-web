package app.accountingassistant.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import app.accountingassistant.android.util.Capture
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

    /** Android's own files in Downloads call a CSV text/comma-separated-values: a statement must still come in. */
    @Test fun aCsvStatementCanBePickedOrSharedUnderEveryName() {
        val app = RuntimeEnvironment.getApplication()
        for (type in listOf("text/csv", "text/comma-separated-values", "text/tab-separated-values")) {
            assertTrue(type, type in Capture.DOCUMENT_TYPES)
            val share = Intent(Intent.ACTION_SEND).setType(type).setPackage(app.packageName)
            assertTrue(type, app.packageManager.queryIntentActivities(share, 0).isNotEmpty())
        }
    }
}
