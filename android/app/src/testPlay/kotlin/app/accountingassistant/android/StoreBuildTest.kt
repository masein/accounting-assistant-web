package app.accountingassistant.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Telephony
import app.accountingassistant.android.sms.SmsSwitch
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Play's build doesn't read SMS until Google grants the exception (roadmap §7.1,
 * §11.5; scenario Y5): no permission, no receiver, no switch. Sharing an SMS
 * into the app still works.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class StoreBuildTest {
    @Test fun itNeitherAsksToReadSmsNorHearsThem() {
        val app = RuntimeEnvironment.getApplication()
        assertFalse(SmsSwitch.available)
        val asked = app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_PERMISSIONS).requestedPermissions.orEmpty()
        assertFalse(Manifest.permission.RECEIVE_SMS in asked || Manifest.permission.READ_SMS in asked)
        val hears = app.packageManager.queryBroadcastReceivers(
            Intent(Telephony.Sms.Intents.SMS_RECEIVED_ACTION).setPackage(app.packageName), 0)
        assertTrue(hears.isEmpty())
        val share = Intent(Intent.ACTION_SEND).setType("text/plain").setPackage(app.packageName)
        assertTrue(app.packageManager.queryIntentActivities(share, 0).isNotEmpty())
    }
}
