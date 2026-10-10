package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The client against a scripted server: sign-in, refresh-and-retry, the end of a session, 426. */
class ApiClientTest {
    private val server = MockWebServer()
    private val store = MemorySessionStore()
    private lateinit var api: ApiClient

    @Before fun start() {
        server.start()
        api = ApiClient(base = server.url("/").toString(), store = store, appVersion = "0.1.0", language = { "fa" })
    }

    @After fun stop() = server.close()

    private fun reply(code: Int, body: String, header: Pair<String, String>? = null) = server.enqueue(
        MockResponse.Builder().code(code).body(body).apply { header?.let { addHeader(it.first, it.second) } }.build())

    private val signedIn = """{"ok":true,"access_token":"A1","expires_in":900,"refresh_token":"R1","device_id":"d1",
        "user":{"id":"u1","username":"maryam","role":"owner","preferred_language":"fa"},
        "company":{"id":"c1","name":"Arman","base_currency":"IRR","kind":"business"}}"""

    @Test fun signingInKeepsTheSessionAndSaysWhoItIs() = runBlocking {
        reply(200, signedIn)
        val r = api.login("maryam", "secret", "Pixel 8")
        assertTrue(r.ok)
        assertEquals("R1", store.load()!!.refreshToken)
        val req = server.takeRequest()
        assertEquals("/api/mobile/v1/auth/login", req.url.encodedPath)
        assertEquals("0.1.0", req.headers["X-App-Version"])
        assertEquals("fa", req.headers["X-UI-Language"])
        assertNull(req.headers["Authorization"])
        assertTrue(req.body!!.utf8().contains("\"device_name\":\"Pixel 8\""))
    }

    @Test fun anExpiredAccessTokenIsRenewedOnceAndTheCallRetried() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(200, """{"ok":true,"access_token":"NEW","refresh_token":"R2","device_id":"d1"}""")
        reply(200, """{"thread_id":"t1","blocks":[{"type":"text","id":"x","text":"سلام"}]}""")
        val chat = api.chat("hi", null)
        assertEquals("t1", chat.threadId)
        assertEquals("Bearer OLD", server.takeRequest().headers["Authorization"])
        val refresh = server.takeRequest()
        assertEquals("/api/mobile/v1/auth/refresh", refresh.url.encodedPath)
        assertTrue(refresh.body!!.utf8().contains("\"refresh_token\":\"R1\""))
        assertEquals("Bearer NEW", server.takeRequest().headers["Authorization"])
        assertEquals("R2", store.load()!!.refreshToken)
    }

    @Test fun whenTheRefreshIsRefusedTheSessionEnds() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(401, """{"detail":"Your session on this phone has ended. Sign in again."}""", "X-Error-Code" to "session_ended")
        // the app listens from launch; so does the test, before the call
        val heard = async(start = CoroutineStart.UNDISPATCHED) { withTimeout(5000) { api.signedOut.first() } }
        val err = runCatching { api.chat("hi", null) }.exceptionOrNull()
        assertTrue(err is ApiError)
        assertNull(store.load())
        assertNull(api.session)
        assertEquals("session_ended", heard.await().code)
    }

    @Test fun anAppTooOldIsToldToUpdate() = runBlocking {
        reply(426, """{"code":"upgrade_required","min_version":"0.2.0","detail":"This version of the app is too old."}""")
        val err = runCatching { api.login("m", "p", "Pixel") }.exceptionOrNull() as ApiError
        assertTrue(err.updateNeeded)
        assertEquals("upgrade_required", err.code)
    }

    @Test fun aStreamedTurnSaysEachStepThenReplies() = runBlocking {
        store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(200, "event: status\ndata: {\"stage\":\"thinking\",\"text\":\"در حال فکر کردن…\"}\n\n" +
                   "event: status\ndata: {\"stage\":\"tool\",\"tool\":\"get_account_balance\",\"text\":\"در حال بررسی دفاتر…\"}\n\n" +
                   "event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[]}\n\n" +
                   "event: done\ndata: {}\n\n")
        val steps = mutableListOf<String>()
        val r = api.chatStream("موجودی؟", null, emptyList(), "c-1") { steps += it }
        assertEquals(listOf("در حال فکر کردن…", "در حال بررسی دفاتر…"), steps)
        assertEquals("t1", r.threadId)
        assertEquals("text/event-stream", server.takeRequest().headers["Accept"])
    }

    @Test fun anErrorInTheStreamIsAnApiError() = runBlocking {
        store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(200, "event: error\ndata: {\"status\":502,\"code\":\"ai_unavailable\",\"detail\":\"دستیار الان نمی‌تواند پاسخ دهد.\"}\n\n" +
                   "event: done\ndata: {}\n\n")
        val err = runCatching { api.chatStream("x", null, emptyList(), "c-2") {} }.exceptionOrNull() as ApiError
        assertEquals(502, err.status)
        assertEquals("ai_unavailable", err.code)
    }
}
