package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import app.accountingassistant.android.util.Crashes
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** A crash is kept without its words and sent once there is a session (scenario N30). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])                  // like the screenshot tests: Robolectric's API 36 set-up needs JDK internals
class CrashesTest {
    private val server = MockWebServer()
    private lateinit var crashes: Crashes
    private lateinit var api: ApiClient

    @Before fun start() {
        server.start()
        crashes = Crashes(RuntimeEnvironment.getApplication(), appVersion = "0.1.0", device = "Google Pixel 8", sdk = 36)
        crashes.enabled = false                         // a clean folder for each test
        crashes.enabled = true
        api = ApiClient(base = server.url("/").toString(),
                        store = MemorySessionStore(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam"))),
                        appVersion = "0.1.0", language = { "fa" })
        runBlocking { api.restore() }
    }

    @After fun stop() = server.close()

    private fun crash(): Throwable = try {
        "۸۵٬۰۰۰٬۰۰۰ ریال".toLong()
        error("unreachable")
    } catch (e: NumberFormatException) {
        IllegalStateException("couldn't post اجارهٔ مهر 85000000", e)
    }

    @Test fun aCrashKeepsWhereNotWhat() {
        val report = crashes.reportOf(Thread.currentThread(), crash())
        assertEquals("java.lang.IllegalStateException", report.exception)
        assertEquals(listOf("java.lang.NumberFormatException"), report.causes)
        assertTrue(report.frames.isNotEmpty() && report.frames.all { Regex("""^[\w$.<>\-]+\([\w$.\- ]*(:\d+)?\)$""").matches(it) })
        val all = report.toString()
        assertFalse(all.contains("85000000") || all.contains("اجاره") || all.contains("۸۵"))
        assertEquals(36, report.android)
    }

    @Test fun keptCrashesAreSentOnceThenGone() = runBlocking {
        repeat(7) { crashes.keep(Thread.currentThread(), crash()) }
        assertEquals(5, crashes.pending().size)                       // the newest five
        server.enqueue(MockResponse.Builder().code(503).body("{}").build())
        runCatching { crashes.send(api) }
        assertEquals(5, crashes.pending().size)                       // the server couldn't take them: kept
        server.enqueue(MockResponse.Builder().body("""{"received":5}""").build())
        assertEquals(5, crashes.send(api))
        assertTrue(crashes.pending().isEmpty())
        server.takeRequest()
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/crashes", sent.url.encodedPath)
        val body = sent.body!!.utf8()
        assertTrue(body.contains("\"app_version\":\"0.1.0\"") && body.contains("java.lang.IllegalStateException"))
        assertFalse(body.contains("85000000"))
    }

    @Test fun turnedOffNothingIsKeptOrSent() = runBlocking {
        crashes.keep(Thread.currentThread(), crash())
        crashes.enabled = false
        assertTrue(crashes.pending().isEmpty())                       // and what was kept is gone
        crashes.keep(Thread.currentThread(), crash())
        assertTrue(crashes.pending().isEmpty())
        assertEquals(0, crashes.send(api))
        assertEquals(0, server.requestCount)
    }
}
