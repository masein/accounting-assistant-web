package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.delay
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The conversation's life against a scripted server: first run, a photo, a voice note. */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {
    private val server = MockWebServer()
    private lateinit var api: ApiClient

    @Before fun start() {
        Dispatchers.setMain(Dispatchers.Unconfined)
        server.start()
        val store = MemorySessionStore(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
        api = ApiClient(base = server.url("/").toString(), store = store, appVersion = "0.1.0", language = { "fa" })
        runBlocking { api.restore() }
    }

    @After fun stop() {
        server.close()
        Dispatchers.resetMain()
    }

    private fun reply(body: String, code: Int = 200) =
        server.enqueue(MockResponse.Builder().code(code).body(body).build())

    private suspend fun until(check: () -> Boolean) = withTimeout(5000) { while (!check()) delay(10) }

    @Test fun aFirstRunGetsSuggestionsThenTheBriefing() = runBlocking {
        reply("[]")                                                         // no threads yet
        reply("""{"thread_id":"t1","blocks":[{"type":"text","id":"b1","kind":"briefing","text":"۲ فاکتور سررسید گذشته است."}]}""")
        val vm = ChatViewModel(api, suggestions = listOf("موجودی چقدره؟"))
        until { vm.state.value.items.size == 2 }
        val items = vm.state.value.items
        assertTrue(items[0] is ChatItem.Suggestions)
        assertEquals("۲ فاکتور سررسید گذشته است.", (items[1] as ChatItem.Words).text)
        assertEquals("t1", vm.state.value.threadId)
    }

    @Test fun aPhotoGoesWithTheNextMessage() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("""{"id":"att-1","file_name":"receipt.jpg","content_type":"image/jpeg","size_bytes":3}""")
        vm.attach(byteArrayOf(1, 2, 3), "receipt.jpg", "image/jpeg")
        until { vm.state.value.attachments.isNotEmpty() }
        assertTrue(server.takeRequest().body!!.utf8().contains("filename=\"receipt.jpg\""))
        reply("""{"thread_id":"t9","blocks":[{"type":"text","id":"w","text":"رسید خوانده شد."}]}""")
        vm.send()
        until { !vm.state.value.sending }
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/chat", sent.url.encodedPath)
        assertTrue(sent.body!!.utf8().contains("\"attachment_ids\":[\"att-1\"]"))
        assertEquals(listOf("receipt.jpg"), (vm.state.value.items.first() as ChatItem.User).files)
        assertTrue(vm.state.value.attachments.isEmpty())
    }

    @Test fun aVoiceNotesWordsLandInTheComposer() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("""{"text":"اجارهٔ مهر را ثبت کن"}""")
        vm.heard(byteArrayOf(9, 9), "voice-note.m4a", "audio/mp4")
        until { !vm.state.value.transcribing }
        assertEquals("اجارهٔ مهر را ثبت کن", vm.state.value.draft)
        assertEquals("/api/mobile/v1/transcribe", server.takeRequest().url.encodedPath)
    }
}
