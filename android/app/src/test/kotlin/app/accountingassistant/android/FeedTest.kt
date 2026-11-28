package app.accountingassistant.android

import android.Manifest
import android.app.NotificationManager
import app.accountingassistant.android.data.FeedItem
import app.accountingassistant.android.notify.FeedNotifier
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** What the hourly sync tells, and how (scenario N35). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], qualifiers = "+fa")
class FeedTest {
    private val context = RuntimeEnvironment.getApplication()
    private val notifier = FeedNotifier(context)
    private val shade get() = shadowOf(context.getSystemService(NotificationManager::class.java))

    private val approval = FeedItem("approval:t1", "approval", "سندی منتظر تأیید شماست", "اجارهٔ دفتر: ۸۰٬۰۰۰٬۰۰۰ IRR، درخواست maryam")
    private val payroll = FeedItem("insight:payroll-1405-07", "insight", "حقوق ۱۲٪ بالا رفت", "۱ در برابر ۲ ماه قبل.")

    @Before fun fresh() {
        notifier.forget()
        shadowOf(context).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)    // "Allow" in the account sheet
    }

    @Test fun takenBackInAndroidsSettingsNothingIsPosted() {
        notifier.deliver(emptyList(), locked = false)
        shadowOf(context).denyPermissions(Manifest.permission.POST_NOTIFICATIONS)
        assertEquals(0, notifier.deliver(listOf(approval), locked = false))
        assertEquals(0, shade.allNotifications.size)
    }

    @Test fun theFirstSyncIsSilentThenEachThingIsToldOnce() {
        assertEquals(0, notifier.deliver(listOf(payroll), locked = false))        // what was already there: no flood
        assertEquals(1, notifier.deliver(listOf(payroll, approval), locked = false))
        assertEquals(1, shade.allNotifications.size)
        val n = shade.allNotifications.single()
        assertEquals("سندی منتظر تأیید شماست", shadowOf(n).contentTitle)
        assertEquals(0, notifier.deliver(listOf(payroll, approval), locked = false))  // told already
    }

    @Test fun behindTheAppLockNoFigures() {
        notifier.deliver(emptyList(), locked = true)
        assertEquals(1, notifier.deliver(listOf(approval), locked = true))
        val n = shadowOf(shade.allNotifications.single())
        assertEquals("حسابدار", n.contentTitle)
        assertEquals("چیزی در دفاترتان نیاز به توجه دارد.", n.contentText)
    }

    @Test fun signingOutStartsTheNextPersonSilent() {
        notifier.deliver(emptyList(), locked = false)
        notifier.forget()
        assertEquals(0, notifier.deliver(listOf(approval), locked = false))
    }

    @Test fun turnedOffAndOnItStartsSilentAgain() {
        notifier.enabled = true
        notifier.deliver(emptyList(), locked = false)
        notifier.enabled = false
        notifier.enabled = true
        assertEquals(0, notifier.deliver(listOf(approval, payroll), locked = false))   // a week's worth doesn't arrive at once
        assertEquals(0, shade.allNotifications.size)
    }

    /** Scenario CX5: what was told is kept in order; past 300, the oldest is forgotten, never the newest. */
    @Test fun pastThreeHundredTheOldestIsForgottenNotTheNewest() {
        val old = (0 until 300).map { FeedItem("insight:old-$it", "insight", "t$it") }
        notifier.deliver(old, locked = false)                                       // the first sync: silent, all seen
        assertEquals(1, notifier.deliver(listOf(approval), locked = false))
        assertEquals(0, notifier.deliver(listOf(approval), locked = false))         // the newest, told once
        // still in the feed, the old ones aren't forgotten either: only what left the feed longest ago
        assertEquals(0, notifier.deliver(old.takeLast(299) + approval, locked = false))
        assertEquals(1, notifier.deliver(listOf(old.first()), locked = false))      // the one made room for
    }

    /** Scenario CX5: two things are two notifications, even when their keys' hash codes are the same. */
    @Test fun twoThingsAreTwoNotificationsEvenWhenTheirKeysHashAlike() {
        notifier.deliver(emptyList(), locked = false)
        val one = FeedItem("insight:Aa", "insight", "یک")
        val two = FeedItem("insight:BB", "insight", "دو")
        assertEquals(one.key.hashCode(), two.key.hashCode())                       // the ids they had: one replaced the other
        assertEquals(2, notifier.deliver(listOf(one, two), locked = false))
        assertEquals(2, shade.allNotifications.size)
    }
}
