package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.SupportContext
import app.accountingassistant.android.data.UpdateRequired
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
        assertEquals("0.2.0", err.minVersion)
        assertEquals(UpdateRequired("0.2.0"), api.updateRequired.value)    // the sign-in screen gives way to the update (CX2)
    }

    /** Scenario CX2: any call answered 426 asks for the update; the session stays, good again after it. */
    @Test fun anyCallTooOldAsksForTheUpdateAndKeepsTheSession() = runBlocking {
        store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        assertNull(api.updateRequired.value)
        reply(426, """{"code":"upgrade_required","min_version":"0.2.0","detail":"This version of the app is too old."}""")
        val err = runCatching { api.threads() }.exceptionOrNull() as ApiError
        assertTrue(err.updateNeeded)
        assertEquals(UpdateRequired("0.2.0"), api.updateRequired.value)
        assertEquals("A1", api.session?.accessToken)
        assertEquals("R1", store.load()?.refreshToken)
    }

    /** Scenario CX2: a renewal answered 426 asks for the update too, and doesn't sign out. */
    @Test fun aRenewalTooOldAsksForTheUpdateWithoutSigningOut() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(426, """{"code":"upgrade_required","min_version":"0.2.0","detail":"This version of the app is too old."}""")
        val err = runCatching { api.chat("hi", null) }.exceptionOrNull() as ApiError
        assertTrue(err.updateNeeded)
        assertEquals("0.2.0", api.updateRequired.value?.minVersion)
        assertEquals("R1", store.load()?.refreshToken)
        assertEquals("OLD", api.session?.accessToken)
    }

    /** Scenario CX8: refused again with the fresh token, a streamed turn ends the session as every other call does. */
    @Test fun aStreamRefusedAfterRenewingEndsTheSession() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(200, """{"ok":true,"access_token":"NEW","refresh_token":"R2","device_id":"d1"}""")
        reply(401, """{"detail":"Your session on this phone has ended. Sign in again.","code":"session_ended"}""")
        val heard = async(start = CoroutineStart.UNDISPATCHED) { withTimeout(5000) { api.signedOut.first() } }
        val err = runCatching { api.chatStream("x", null, emptyList(), "c-3") {} }.exceptionOrNull() as ApiError
        assertEquals(401, err.status)
        assertEquals("session_ended", heard.await().code)
        assertNull(store.load())
        assertNull(api.session)
        server.takeRequest(); server.takeRequest()
        assertEquals("Bearer NEW", server.takeRequest().headers["Authorization"])   // refused with the fresh token
    }

    /** Scenario CX8: the same for a document. */
    @Test fun aDownloadRefusedAfterRenewingEndsTheSession() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(200, """{"ok":true,"access_token":"NEW","refresh_token":"R2","device_id":"d1"}""")
        reply(401, """{"detail":"Your session on this phone has ended. Sign in again."}""", "X-Error-Code" to "session_ended")
        val heard = async(start = CoroutineStart.UNDISPATCHED) { withTimeout(5000) { api.signedOut.first() } }
        val err = runCatching { api.download("/api/mobile/v1/documents/invoices/1") }.exceptionOrNull() as ApiError
        assertEquals(401, err.status)
        assertEquals("session_ended", heard.await().code)
        assertNull(store.load())
        assertNull(api.session)
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

    @Test fun aDocumentDownloadsWithTheBearerAndRenewsOnce() = runBlocking {
        store.save(StoredSession("OLD", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
        reply(401, """{"detail":"Authentication required","code":"session_expired"}""")
        reply(200, """{"ok":true,"access_token":"NEW","refresh_token":"R2","device_id":"d1"}""")
        server.enqueue(MockResponse.Builder().code(200).body("%PDF-1.7 test").build())
        val bytes = api.download("/api/mobile/v1/documents/invoices/1")
        assertEquals("%PDF-1.7 test", String(bytes))
        server.takeRequest(); server.takeRequest()
        assertEquals("Bearer NEW", server.takeRequest().headers["Authorization"])
    }

    /** Run 2026-10-11: the phone's language changed after sign-in; the accountant kept answering in the old one. */
    @Test fun aNewLanguageBecomesTheAccountsAndIsRemembered() = runBlocking {
        store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam", language = "fa")))
        api.restore()
        reply(200, """{"language":"es"}""")
        api.setLanguage("es")
        val req = server.takeRequest()
        assertEquals("PUT", req.method)
        assertEquals("/api/mobile/v1/me/language", req.url.encodedPath)
        assertTrue(req.body!!.utf8().contains("\"language\":\"es\""))
        assertEquals("es", store.load()!!.user.language)
        assertEquals("es", api.session!!.user.language)
    }

    private suspend fun signedInAsMaryam() {
        store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        api.restore()
    }

    private fun about() = SupportContext(page = "chat", appVersion = "0.1.0", language = "fa", screen = "393x852",
                                         errors = api.recentFailures())

    /** Scenario DB1: the conversation with support, as the web's, with the bearer and the app's version. */
    @Test fun theSupportConversationComesOldestFirstAndThenOnlyWhatIsNew() = runBlocking {
        signedInAsMaryam()
        reply(200, """{"thread_id":"th1","messages":[
            {"id":"m1","author":"user","channel":"web","text":"صفحهٔ دفتر کل خطا می‌دهد","attachments":["a1"],"at":"2026-11-28T09:00:00.123456+00:00","seen":null},
            {"id":"m2","author":"desk","channel":"desk","text":"درست شد؛ دوباره ببینید.","attachments":[],"at":"2026-11-28T09:05:00+00:00","seen":false}],
            "unread":1,"desk":{"online":true},"daily_limit":30}""")
        val conv = api.support()
        assertEquals("th1", conv.threadId)
        assertEquals(listOf("m1", "m2"), conv.messages.map { it.id })
        assertEquals(listOf(false, true), conv.messages.map { it.fromDesk })
        assertEquals(listOf("a1"), conv.messages[0].attachments)
        assertEquals(1, conv.unread)
        assertTrue(conv.desk.online)
        val first = server.takeRequest()
        assertEquals("GET", first.method)
        assertEquals("/api/mobile/v1/support", first.url.encodedPath)
        assertNull(first.url.queryParameter("after"))
        assertEquals("Bearer A1", first.headers["Authorization"])
        assertEquals("0.1.0", first.headers["X-App-Version"])

        reply(200, """{"thread_id":"th1","messages":[],"unread":0,"desk":{"online":false},"daily_limit":30}""")
        api.support(after = "2026-11-28T09:05:00+00:00")
        assertEquals("2026-11-28T09:05:00+00:00", server.takeRequest().url.queryParameter("after"))
    }

    /** Scenarios DB1, DB2: a screenshot goes first, then the message with its id and what the app knew. */
    @Test fun aSupportMessageCarriesItsScreenshotAndContext() = runBlocking {
        signedInAsMaryam()
        reply(201, """{"id":"a1","file_name":"shot.png","content_type":"image/png","size_bytes":3}""")
        val up = api.supportAttachment(byteArrayOf(1, 2, 3), "shot.png", "image/png")
        assertEquals("a1", up.id)
        val upload = server.takeRequest()
        assertEquals("POST", upload.method)
        assertEquals("/api/mobile/v1/support/attachments", upload.url.encodedPath)
        assertTrue(upload.headers["Content-Type"]!!.startsWith("multipart/form-data"))
        assertTrue(upload.body!!.utf8().contains("name=\"file\"; filename=\"shot.png\""))

        reply(201, """{"message":{"id":"m3","author":"user","channel":"android","text":"the ledger shows an error",
            "attachments":["a1"],"at":"2026-11-28T10:00:00+00:00","seen":null},"desk":{"online":true}}""")
        val posted = api.supportMessage("the ledger shows an error", listOf("a1"), about())
        assertEquals("android", posted.message.channel)
        assertTrue(posted.desk.online)
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/support/messages", sent.url.encodedPath)
        val body = sent.body!!.utf8()
        assertTrue(body, body.contains("\"attachment_ids\":[\"a1\"]"))
        assertTrue(body, body.contains("\"platform\":\"android\""))
        assertTrue(body, body.contains("\"page\":\"chat\""))
        assertTrue(body, body.contains("\"app_version\":\"0.1.0\""))
        assertTrue(body, body.contains("\"screen\":\"393x852\""))

        reply(200, """{"seen":1}""")
        assertEquals(1, api.supportSeen())
        assertEquals("/api/mobile/v1/support/seen", server.takeRequest().url.encodedPath)
    }

    /** Scenario DB6: past 30 a day the server's words come back as they are, and the session stays. */
    @Test fun pastThirtyADayTheRefusalSaysWhyAndKeepsTheSession() = runBlocking {
        signedInAsMaryam()
        val why = "امروز ۳۰ پیام به پشتیبانی فرستاده‌اید. همه به دستشان رسیده؛ فردا دوباره بنویسید."
        reply(429, """{"detail":"$why"}""", "X-Error-Code" to "support_limit")
        val err = runCatching { api.supportMessage("again", emptyList(), about()) }.exceptionOrNull() as ApiError
        assertEquals(429, err.status)
        assertEquals("support_limit", err.code)
        assertEquals(why, err.message)
        assertEquals("A1", api.session?.accessToken)
        assertNull(api.updateRequired.value)
    }

    /**
     * Scenario DB3: the last ten calls the server refused are kept with their
     * request ids, oldest first, for a support message; a streamed turn's
     * error too. Support's own calls aren't.
     */
    @Test fun theLastTenRefusedCallsAreKeptForSupport() = runBlocking {
        signedInAsMaryam()
        reply(500, """{"detail":"Internal Server Error"}""", "X-Request-ID" to "9f2c41d07a1b4e55")
        runCatching { api.threads() }
        reply(404, """{"detail":"Not Found"}""")
        runCatching { api.summary() }
        server.enqueue(MockResponse.Builder().code(200).addHeader("X-Request-ID", "req-stream")
            .body("event: error\ndata: {\"status\":502,\"code\":\"ai_unavailable\",\"detail\":\"…\"}\n\nevent: done\ndata: {}\n\n").build())
        runCatching { api.chatStream("x", null, emptyList(), "c-9") {} }
        reply(429, """{"detail":"limit"}""", "X-Error-Code" to "support_limit")
        runCatching { api.supportMessage("x", emptyList(), about()) }

        val failed = api.recentFailures()
        assertEquals(listOf("/api/mobile/v1/threads", "/api/mobile/v1/summary", "/api/mobile/v1/chat/stream"), failed.map { it.path })
        assertEquals(listOf(500, 404, 502), failed.map { it.status })
        assertEquals(listOf("GET", "GET", "POST"), failed.map { it.method })
        assertEquals("9f2c41d07a1b4e55", failed[0].requestId)
        assertNull(failed[1].requestId)
        assertEquals("req-stream", failed[2].requestId)
        assertTrue(failed[0].at.startsWith("20"))

        repeat(12) {
            reply(503, "{}")
            runCatching { api.feed() }
        }
        val kept = api.recentFailures()
        assertEquals(ApiClient.MAX_FAILURES, kept.size)                 // the oldest forgotten
        assertTrue(kept.all { it.path == "/api/mobile/v1/feed" && it.status == 503 })
    }
}
