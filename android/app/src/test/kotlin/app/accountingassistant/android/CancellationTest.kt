package app.accountingassistant.android

import app.accountingassistant.android.util.runSuspendCatching
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/** A cancelled call stays cancelled; a failed one is a failure (scenario CX7). */
class CancellationTest {
    @Test fun aCancelledCallIsNotTakenForAFailure() = runBlocking {
        var failed = false
        var after = false
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            runSuspendCatching { delay(10_000) }.onFailure { failed = true }
            after = true                                       // runCatching went on here, as if the call had failed
        }
        job.cancel()
        job.join()
        assertTrue(job.isCancelled)
        assertFalse(failed)
        assertFalse(after)
    }

    @Test fun aFailedCallIsStillAFailure() = runBlocking {
        val r = runSuspendCatching<Int> { delay(1); throw IOException("no network") }
        assertEquals("no network", r.exceptionOrNull()?.message)
        assertEquals(42, runSuspendCatching { delay(1); 42 }.getOrNull())
    }
}
