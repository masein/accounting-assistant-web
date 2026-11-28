package app.accountingassistant.android

import app.accountingassistant.android.security.AppLock
import app.accountingassistant.android.security.LockState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The app lock when the activity is recreated, and after time away
 * (scenario CX3). The lock's state is a view model: a rotation keeps the same
 * one, a new process starts a new one.
 */
class LockStateTest {
    private val twoMinutes = AppLock.RELOCK_AFTER_MS

    @Test fun aColdStartAsksThenARotationDoesNot() {
        val lock = LockState()
        lock.opened(signedIn = true, lockOn = true, now = 1_000)
        assertTrue(lock.locked.value)                                     // a cold start asks
        lock.unlocked()
        // rotated: the activity stops, and a new one starts before it has read the session, then opens
        lock.stopped(now = 5_000)
        lock.started(now = 5_200, signedIn = false, lockOn = false)
        lock.opened(signedIn = true, lockOn = true, now = 5_300)
        assertFalse(lock.locked.value)
        // and the theme or the language, the same
        lock.stopped(now = 9_000)
        lock.started(now = 9_100, signedIn = true, lockOn = true)
        lock.opened(signedIn = true, lockOn = true, now = 9_200)
        assertFalse(lock.locked.value)
    }

    @Test fun twoMinutesAwayAsksAgain() {
        val lock = LockState()
        lock.opened(signedIn = true, lockOn = true, now = 1_000)
        lock.unlocked()
        lock.stopped(now = 10_000)
        lock.started(now = 10_000 + twoMinutes / 2, signedIn = true, lockOn = true)
        assertFalse(lock.locked.value)                                    // a minute away: no
        lock.stopped(now = 200_000)
        lock.started(now = 200_001 + twoMinutes, signedIn = true, lockOn = true)
        assertTrue(lock.locked.value)
    }

    /** The language changed in Android's settings while the app was away: the activity is new, and it still asks. */
    @Test fun recreatedAfterTwoMinutesAwayItStillAsks() {
        val lock = LockState()
        lock.opened(signedIn = true, lockOn = true, now = 1_000)
        lock.unlocked()
        lock.stopped(now = 10_000)
        lock.started(now = 10_001 + twoMinutes, signedIn = false, lockOn = false)   // the new activity hasn't read the session yet
        lock.opened(signedIn = true, lockOn = true, now = 10_002 + twoMinutes)
        assertTrue(lock.locked.value)
    }

    @Test fun signedOutAndInAgainARotationDoesNotAsk() {
        val lock = LockState()                                            // the view models went with the person
        lock.settled()
        lock.stopped(now = 1_000)
        lock.opened(signedIn = true, lockOn = true, now = 1_200)
        assertFalse(lock.locked.value)
    }

    @Test fun signedOutNothingLocks() {
        val lock = LockState()
        lock.opened(signedIn = false, lockOn = true, now = 1_000)
        lock.stopped(now = 2_000)
        lock.started(now = 2_001 + twoMinutes, signedIn = false, lockOn = true)
        assertFalse(lock.locked.value)
    }
}
