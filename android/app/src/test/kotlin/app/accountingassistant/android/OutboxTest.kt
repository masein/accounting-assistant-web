package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.MemoryOutboxStore
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.Outbox
import app.accountingassistant.android.data.Queued
import app.accountingassistant.android.data.QueuedFile
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

/** What is typed offline waits, then goes in order, once, as the person who typed it. */
class OutboxTest {
    private val server = MockWebServer()
    /** Which calls find no network: all of them offline, or only some. */
    private var unreachable: (String) -> Boolean = { false }
    private var offline: Boolean
        get() = error("write-only")
        set(v) { unreachable = { v } }
    private var woken = 0
    private lateinit var api: ApiClient
    private lateinit var store: MemoryOutboxStore
    private lateinit var outbox: Outbox

    @Before fun start() {
        server.start()
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            if (unreachable(chain.request().url.encodedPath)) throw IOException("no network")
            chain.proceed(chain.request())
        }.build()
        api = ApiClient(base = server.url("/").toString(),
                        store = MemorySessionStore(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam"))),
                        appVersion = "0.1.0", language = { "fa" }, http = http)
        runBlocking { api.restore() }
        store = MemoryOutboxStore()
        outbox = Outbox(store, api, wake = { woken++ })
    }

    @After fun stop() = server.close()

    private fun reply(body: String, code: Int = 200, header: Pair<String, String>? = null) = server.enqueue(
        MockResponse.Builder().code(code).body(body).apply { header?.let { addHeader(it.first, it.second) } }.build())

    private fun answer(thread: String, text: String) =
        reply("event: reply\ndata: {\"thread_id\":\"$thread\",\"blocks\":[{\"type\":\"text\",\"id\":\"$text\",\"text\":\"$text\"}]}\n\nevent: done\ndata: {}\n\n")

    private fun q(id: String, text: String, thread: String? = null, newThread: String? = null, user: String? = "u1",
                  files: List<QueuedFile> = emptyList()) = Queued(id, text, thread, newThread, files, queuedAt = 1, userId = user)

    @Test fun offlineItWaitsAndAsksToBeWokenThenGoesInOrder() = runBlocking {
        outbox.add(q("m1", "اجاره", newThread = "k1"))
        outbox.add(q("m2", "و سوخت", newThread = "k1"))
        offline = true
        assertEquals(Outbox.Result.Offline, outbox.flush())
        assertEquals(1, woken)
        assertEquals(listOf("m1", "m2"), outbox.entries.value.map { it.clientId })
        offline = false
        answer("t7", "a1")
        answer("t7", "a2")
        assertEquals(Outbox.Result.Done, outbox.flush())
        val first = server.takeRequest().body!!.utf8()
        val second = server.takeRequest().body!!.utf8()
        assertTrue(first.contains("\"client_message_id\":\"m1\"") && !first.contains("thread_id\":\""))
        // the first answer named the new conversation; the second message goes to it
        assertTrue(second.contains("\"client_message_id\":\"m2\"") && second.contains("\"thread_id\":\"t7\""))
        assertTrue(outbox.entries.value.isEmpty())
    }

    @Test fun aPhotoIsUploadedOnceEvenWhenTheMessageWaits() = runBlocking {
        val kept = outbox.keep(byteArrayOf(7, 7, 7))
        outbox.add(q("m1", "", thread = "t1", files = listOf(QueuedFile("receipt.jpg", "image/jpeg", path = kept))))
        reply("""{"id":"att-9","file_name":"receipt.jpg"}""")
        unreachable = { it.contains("/chat") }                         // the upload gets through, the message doesn't
        assertEquals(Outbox.Result.Offline, outbox.flush())
        assertEquals("att-9", outbox.entries.value.single().files.single().uploadedId)
        offline = false
        answer("t1", "خوانده شد")
        assertEquals(Outbox.Result.Done, outbox.flush())
        assertEquals("/api/mobile/v1/uploads", server.takeRequest().url.encodedPath)
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/chat/stream", sent.url.encodedPath)   // no second upload
        assertTrue(sent.body!!.utf8().contains("\"attachment_ids\":[\"att-9\"]"))
        assertTrue(store.files.isEmpty())                                // the kept copy is gone once sent
    }

    @Test fun aRefusedMessageWaitsForTryAgainAndTheRestStillGo() = runBlocking {
        outbox.add(q("m1", "record the tea", thread = "t1"))
        outbox.add(q("m2", "how much cash?", thread = "t1"))
        reply("event: error\ndata: {\"status\":502,\"code\":\"ai_unavailable\",\"detail\":\"down\"}\n\nevent: done\ndata: {}\n\n")
        answer("t1", "cash")
        assertEquals(Outbox.Result.Done, outbox.flush())
        val left = outbox.entries.value.single()
        assertEquals("m1", left.clientId)
        assertEquals("ai_unavailable", left.failure)
        outbox.retry("m1")
        answer("t1", "tea")
        assertEquals(Outbox.Result.Done, outbox.flush())
        assertTrue(outbox.entries.value.isEmpty())
    }

    @Test fun aMessageStillBeingAnsweredIsTriedLaterNotRefused() = runBlocking {
        outbox.add(q("m1", "record the rent", thread = "t1"))
        reply("""{"detail":"Still working on this message."}""", code = 409, header = "X-Error-Code" to "turn_in_progress")
        assertEquals(Outbox.Result.Offline, outbox.flush())
        assertNull(outbox.entries.value.single().failure)
        assertEquals(1, woken)
    }

    @Test fun whatSomeoneElseTypedIsNeverSentAsYou() = runBlocking {
        outbox.add(q("m1", "theirs", thread = "t1", user = "u2"))
        assertEquals(Outbox.Result.Done, outbox.flush())
        assertEquals(0, server.requestCount)
        outbox.keepOnly("u1")
        assertTrue(outbox.entries.value.isEmpty())
    }

    @Test fun signingOutLeavesNothingBehind() = runBlocking {
        outbox.keep(byteArrayOf(1))
        outbox.add(q("m1", "x", thread = "t1"))
        outbox.clear()
        assertTrue(outbox.entries.value.isEmpty() && store.files.isEmpty())
    }
}
