package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import app.accountingassistant.android.ui.support.SupportUiState
import app.accountingassistant.android.ui.support.SupportViewModel
import app.accountingassistant.android.ui.support.supportTime
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.time.LocalDate
import java.time.ZoneOffset

/** Support against a scripted server: the conversation, a reply read, a message with what the app knew (DB1–DB8). */
@OptIn(ExperimentalCoroutinesApi::class)
class SupportViewModelTest {
    private val server = MockWebServer()
    private lateinit var api: ApiClient
    @Volatile private var offline = false
    @Volatile private var seen = 0

    @Before fun start() {
        Dispatchers.setMain(Dispatchers.Unconfined)
        server.start()
        val store = MemorySessionStore(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            if (offline) throw IOException("no network")
            chain.proceed(chain.request())
        }.build()
        api = ApiClient(base = server.url("/").toString(), store = store, appVersion = "0.1.0", language = { "fa" }, http = http)
        runBlocking { api.restore() }
    }

    @After fun stop() {
        server.close()
        Dispatchers.resetMain()
    }

    // a minute between polls: each test says when to ask
    private fun model() = SupportViewModel(api, "0.1.0", language = { "fa" }, screen = { "393x852" },
                                           onSeen = { seen++ }, pollMs = 60_000)

    private fun reply(body: String, code: Int = 200, header: Pair<String, String>? = null) = server.enqueue(
        MockResponse.Builder().code(code).body(body).apply { header?.let { addHeader(it.first, it.second) } }.build())

    private suspend fun until(check: () -> Boolean) = withTimeout(5000) { while (!check()) delay(10) }

    private val asked = """{"id":"m1","author":"user","channel":"web","text":"صفحهٔ دفتر کل خطا می‌دهد","attachments":[],
        "at":"2026-11-28T09:00:00.123456+00:00","seen":null}"""
    private val answered = """{"id":"m2","author":"desk","channel":"desk","text":"درست شد؛ دوباره ببینید.","attachments":[],
        "at":"2026-11-28T09:05:00+00:00","seen":false}"""

    private fun conversation(vararg messages: String, unread: Int = 0, online: Boolean = true) =
        """{"thread_id":"th1","messages":[${messages.joinToString(",")}],"unread":$unread,"desk":{"online":$online},"daily_limit":30}"""

    /** Scenario DB4: on screen, the conversation comes, the reply is marked read and its notification goes. */
    @Test fun shownTheConversationComesAndTheReplyIsRead() = runBlocking {
        reply(conversation(asked, answered, unread = 1))
        reply("""{"seen":1}""")
        val vm = model()
        vm.resume()
        until { vm.state.value.loaded && vm.state.value.unread == 0 && seen == 1 }
        vm.pause()
        val st = vm.state.value
        assertEquals(listOf("m1", "m2"), st.messages.map { it.id })
        assertTrue(st.online)
        assertEquals(true, st.messages[1].seen)
        val first = server.takeRequest()
        assertEquals("/api/mobile/v1/support", first.url.encodedPath)
        assertNull(first.url.queryParameter("after"))
        assertEquals("/api/mobile/v1/support/seen", server.takeRequest().url.encodedPath)
    }

    /** Scenario DB4: off screen only the count is read (the badge); the next call asks only for what is newer. */
    @Test fun offScreenTheBadgeCountsAndTheNextCallAsksForWhatIsNew() = runBlocking {
        reply(conversation(asked, online = false))
        val vm = model()
        vm.check()
        until { vm.state.value.loaded }
        assertFalse(vm.state.value.online)
        reply(conversation(answered, unread = 1))
        vm.check()
        until { vm.state.value.unread == 1 }
        assertEquals(listOf("m1", "m2"), vm.state.value.messages.map { it.id })
        assertEquals(0, seen)                                              // not read: only counted
        server.takeRequest()
        assertEquals("2026-11-28T09:00:00.123456+00:00", server.takeRequest().url.queryParameter("after"))
        assertEquals(2, server.requestCount)                               // nothing marked seen
    }

    /** Scenarios DB1, DB3: a message carries the screen, the version, the language, the size and the last failures. */
    @Test fun aMessageCarriesWhatTheAppKnew() = runBlocking {
        reply("""{"detail":"Internal Server Error"}""", 500, "X-Request-ID" to "9f2c41d07a1b4e55")
        runCatching { api.threads() }
        server.takeRequest()
        val vm = model()
        vm.opened("chat")
        vm.edit("  صفحهٔ دفتر کل خطا می‌دهد  ")
        reply("""{"message":$asked,"desk":{"online":false}}""", 201)
        vm.send()
        until { !vm.state.value.sending && vm.state.value.messages.isNotEmpty() }
        val body = server.takeRequest().body!!.utf8()
        assertTrue(body, body.contains("\"text\":\"صفحهٔ دفتر کل خطا می‌دهد\""))
        assertTrue(body, body.contains("\"page\":\"chat\""))
        assertTrue(body, body.contains("\"app_version\":\"0.1.0\""))
        assertTrue(body, body.contains("\"language\":\"fa\""))
        assertTrue(body, body.contains("\"platform\":\"android\""))
        assertTrue(body, body.contains("\"screen\":\"393x852\""))
        assertTrue(body, body.contains("\"path\":\"/api/mobile/v1/threads\""))
        assertTrue(body, body.contains("\"request_id\":\"9f2c41d07a1b4e55\""))
        assertEquals("", vm.state.value.draft)
        assertFalse(vm.state.value.online)
        assertNull(vm.state.value.problem)
    }

    /** Scenario DB2: a screenshot is uploaded first; the message goes with its id, even without words. */
    @Test fun aScreenshotGoesWithTheMessageById() = runBlocking {
        val vm = model()
        reply("""{"id":"a1","file_name":"shot.png","content_type":"image/png","size_bytes":3}""", 201)
        vm.attach(byteArrayOf(1, 2, 3), "shot.png", "image/png")
        until { vm.state.value.attachments.isNotEmpty() }
        assertEquals("/api/mobile/v1/support/attachments", server.takeRequest().url.encodedPath)
        assertTrue(vm.state.value.canSend)
        reply("""{"message":{"id":"m5","author":"user","channel":"android","text":"","attachments":["a1"],
            "at":"2026-11-28T10:00:00+00:00","seen":null},"desk":{"online":true}}""", 201)
        vm.send()
        until { vm.state.value.messages.isNotEmpty() }
        assertTrue(server.takeRequest().body!!.utf8().contains("\"attachment_ids\":[\"a1\"]"))
        assertTrue(vm.state.value.attachments.isEmpty())
        assertEquals(listOf("a1"), vm.state.value.messages.single().attachments)
    }

    /** Scenario DB2: a file over 20 MB is refused on the phone, before anything is sent. */
    @Test fun aFileOverTwentyMegabytesIsNotSent() = runBlocking {
        val vm = model()
        vm.attach(ByteArray(SupportViewModel.MAX_BYTES + 1), "big.pdf", "application/pdf")
        assertEquals(SupportUiState.Problem.TooLarge, vm.state.value.problem)
        assertFalse(vm.state.value.uploading)
        assertEquals(0, server.requestCount)
    }

    /** Scenario DB6: past 30 a day the server's words are shown, and what was typed stays. */
    @Test fun pastThirtyADayTheServersWordsAreShown() = runBlocking {
        val why = "امروز ۳۰ پیام به پشتیبانی فرستاده‌اید؛ فردا دوباره بنویسید."
        reply("""{"detail":"$why"}""", 429, "X-Error-Code" to "support_limit")
        val vm = model()
        vm.edit("یکی دیگر")
        vm.send()
        until { vm.state.value.problem != null }
        assertEquals(SupportUiState.Problem.Said(why), vm.state.value.problem)
        assertEquals("یکی دیگر", vm.state.value.draft)
        assertFalse(vm.state.value.sending)
    }

    /** Scenario DB6: offline, "Couldn't send it — try again", and the words stay to send again. */
    @Test fun offlineItSaysTryAgainAndKeepsTheWords() = runBlocking {
        offline = true
        val vm = model()
        vm.edit("the ledger shows an error")
        vm.send()
        until { vm.state.value.problem != null }
        assertEquals(SupportUiState.Problem.Failed, vm.state.value.problem)
        assertEquals("the ledger shows an error", vm.state.value.draft)
        assertTrue(vm.state.value.canSend)
        vm.dismiss()
        assertNull(vm.state.value.problem)
    }

    /** Nothing to send: no call. */
    @Test fun anEmptyMessageIsNotSent() = runBlocking {
        val vm = model()
        vm.edit("   ")
        assertFalse(vm.state.value.canSend)
        vm.send()
        assertFalse(vm.state.value.sending)
        assertEquals(0, server.requestCount)
    }

    /** The time under each message: the hour today, the day and the hour before, in the person's digits (DB7). */
    @Test fun eachMessageSaysWhenInThePersonsDigits() {
        val today = LocalDate.of(2026, 11, 28)
        assertEquals("09:05", supportTime("2026-11-28T09:05:00+00:00", "en", today = today, zone = ZoneOffset.UTC))
        assertEquals("۰۹:۰۵", supportTime("2026-11-28T09:05:00.5+00:00", "fa", today = today, zone = ZoneOffset.UTC))
        assertEquals("۶ آذر، ۱۲:۰۰", supportTime("2026-11-27T12:00:00+00:00", "fa", today = today, zone = ZoneOffset.UTC))
        assertEquals("27 Nov · 12:00", supportTime("2026-11-27T12:00:00+00:00", "en", today = today, zone = ZoneOffset.UTC))
        // the hour where the phone is: 23:30 UTC is three in the morning in Tehran, the next day (today)
        assertEquals("03:00", supportTime("2026-11-27T23:30:00+00:00", "en", today = today, zone = ZoneOffset.ofHoursMinutes(3, 30)))
        assertEquals("", supportTime(null, "en"))
    }
}
