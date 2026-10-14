package app.accountingassistant.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Telephony
import app.accountingassistant.android.sms.SmsSwitch
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** The build outside Play reads bank SMS (owner's decision, roadmap §11.5; scenario Y5). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class StoreBuildTest {
    @Test fun itMayReadSmsAndHearsThemArrive() {
        val app = RuntimeEnvironment.getApplication()
        assertTrue(SmsSwitch.available)
        val asked = app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_PERMISSIONS).requestedPermissions.orEmpty()
        assertTrue(Manifest.permission.RECEIVE_SMS in asked)
        val hears = app.packageManager.queryBroadcastReceivers(
            Intent(Telephony.Sms.Intents.SMS_RECEIVED_ACTION).setPackage(app.packageName), 0)
        assertTrue(hears.any { it.activityInfo.name.endsWith(".sms.BankSmsReceiver") })
    }
}
