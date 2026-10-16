package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.data.MemoryOutboxStore
import app.accountingassistant.android.data.Outbox
import app.accountingassistant.android.data.Queued
import app.accountingassistant.android.ui.chat.ChatUiState
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatViewModel
import app.accountingassistant.android.ui.chat.Quote
import app.accountingassistant.android.data.ReplyDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
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
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import java.io.IOException

/** The conversation's life against a scripted server: first run, a photo, a voice note. */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {
    private val server = MockWebServer()
    private lateinit var api: ApiClient
    private var offline = false

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

    /** The 2026-10-11 pass: switched from Spanish to Arabic, the first-run questions stayed Spanish. */
    @Test fun theFirstRunQuestionsFollowTheAppsLanguage() = runBlocking {
        reply("[]")
        reply("""{"thread_id":null,"blocks":[]}""")
        val vm = ChatViewModel(api, suggestions = listOf("¿Cuánto dinero tenemos?"))
        until { vm.state.value.items.firstOrNull() is ChatItem.Suggestions }
        vm.useSuggestions(listOf("كم لدينا من المال؟"))
        assertEquals(listOf("كم لدينا من المال؟"), (vm.state.value.items.first() as ChatItem.Suggestions).options)
        vm.startNew()
        assertEquals(listOf("كم لدينا من المال؟"), (vm.state.value.items.first() as ChatItem.Suggestions).options)
    }

    @Test fun aPhotoGoesWithTheNextMessage() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("""{"id":"up1","received":0,"size_bytes":3,"max_chunk":1048576,"done":false}""")
        reply("""{"id":"up1","received":3,"size_bytes":3,"done":true,
                  "attachment":{"id":"att-1","file_name":"receipt.jpg","content_type":"image/jpeg","size_bytes":3}}""")
        vm.attach(byteArrayOf(1, 2, 3), "receipt.jpg", "image/jpeg")
        until { vm.state.value.attachments.isNotEmpty() }
        assertTrue(server.takeRequest().body!!.utf8().contains("\"file_name\":\"receipt.jpg\""))
        assertEquals("/api/mobile/v1/uploads/up1", server.takeRequest().url.encodedPath)
        assertEquals(null, vm.state.value.uploadProgress)
        reply("event: status\ndata: {\"stage\":\"reading_files\",\"text\":\"در حال خواندن پیوست…\"}\n\n" +
              "event: reply\ndata: {\"thread_id\":\"t9\",\"blocks\":[{\"type\":\"text\",\"id\":\"w\",\"text\":\"رسید خوانده شد.\"}]}\n\n" +
              "event: done\ndata: {}\n\n")
        vm.send()
        until { !vm.state.value.sending }
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/chat/stream", sent.url.encodedPath)
        assertTrue(sent.body!!.utf8().contains("\"attachment_ids\":[\"att-1\"]"))
        assertTrue(sent.body!!.utf8().contains("\"client_message_id\":"))
        assertEquals(listOf("receipt.jpg"), (vm.state.value.items.first() as ChatItem.User).files)
        assertTrue(vm.state.value.attachments.isEmpty())
    }

    @Test fun aSharedFileThatCannotBeReadSaysSo() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        vm.unreadable()
        assertEquals(ChatUiState.Notice.Unreadable, vm.state.value.notice)
    }

    @Test fun aVoiceNotesWordsLandInTheComposer() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("""{"text":"اجارهٔ مهر را ثبت کن"}""")
        vm.heard(byteArrayOf(9, 9), "voice-note.m4a", "audio/mp4")
        until { !vm.state.value.transcribing }
        assertEquals("اجارهٔ مهر را ثبت کن", vm.state.value.draft)
        assertEquals("/api/mobile/v1/transcribe", server.takeRequest().url.encodedPath)
    }

    @Test fun aTouchTooShortForWordsSaysHowToRecordAndSendsNothing() {
        val vm = ChatViewModel(api, restore = false)
        vm.listening(true)
        vm.tooShort()                                          // W5: let go before any words
        assertEquals(false, vm.state.value.listening)
        assertEquals(ChatUiState.Notice.VoiceTooShort, vm.state.value.notice)
        vm.listening(true)                                     // the next recording clears the hint
        assertEquals(null, vm.state.value.notice)
        assertEquals(0, server.requestCount)                   // nothing went to be transcribed
    }

    @Test fun aDroppedStreamIsRetriedOnceWithTheSameId() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        // the stream breaks before its reply, then the plain chat answers
        reply("event: status\ndata: {\"stage\":\"thinking\",\"text\":\"در حال فکر کردن…\"}\n\n")
        reply("""{"thread_id":"t1","blocks":[{"type":"text","id":"w","text":"سلام"}],"stop_reason":"repeat"}""")
        vm.edit("سلام")
        vm.send()
        until { !vm.state.value.sending }
        val streamed = server.takeRequest().body!!.utf8()
        val retried = server.takeRequest()
        assertEquals("/api/mobile/v1/chat", retried.url.encodedPath)
        val id = Regex("\"client_message_id\":\"([^\"]+)\"").find(streamed)!!.groupValues[1]
        assertTrue(retried.body!!.utf8().contains(id))
        assertEquals("سلام", (vm.state.value.items.last() as ChatItem.Words).text)
    }

    @Test fun aRefusedTurnWaitsUnderItsBubbleWithItsCode() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("event: error\ndata: {\"status\":502,\"code\":\"ai_unavailable\",\"detail\":\"provider unreachable\"}\n\n" +
              "event: done\ndata: {}\n\n")
        vm.edit("record the tea")
        vm.send()
        until { vm.state.value.queued.values.any { it.failure != null } }
        val bubble = vm.state.value.items.last() as ChatItem.User
        assertEquals("ai_unavailable", vm.state.value.queued.getValue(bubble.clientId!!).failure)   // said in the user's language
        // Try again: the same message, the same id, now answered
        reply("event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[{\"type\":\"text\",\"id\":\"w\",\"text\":\"ثبت شد\"}]}\n\n")
        vm.retry(bubble.clientId!!)
        until { vm.state.value.queued.isEmpty() && vm.state.value.items.last() is ChatItem.Words }
        val ids = (1..2).map { Regex("\"client_message_id\":\"([^\"]+)\"").find(server.takeRequest().body!!.utf8())!!.groupValues[1] }
        assertEquals(ids[0], ids[1])
        assertEquals("t1", vm.state.value.threadId)
    }

    @Test fun offlineTheMessageWaitsThenSendsItselfWhenTheNetworkIsBack() = runBlocking {
        var woken = 0
        val outbox = Outbox(MemoryOutboxStore(), api, wake = { woken++ })
        val vm = ChatViewModel(api, restore = false, outbox = outbox)
        offline = true
        vm.edit("۱۲۰ هزار تومان چای")
        vm.send()
        until { !vm.state.value.sending }
        val bubble = vm.state.value.items.single() as ChatItem.User
        assertEquals("", vm.state.value.draft)                                   // nothing typed is lost: it waits in the outbox
        assertTrue(bubble.clientId in vm.state.value.queued)
        assertEquals(ChatUiState.Notice.Offline, vm.state.value.notice)
        assertEquals(1, woken)
        // the network is back: the background sender (here, called directly) sends it; the answer lands in the chat
        offline = false
        server.enqueue(MockResponse.Builder().headersDelay(600, TimeUnit.MILLISECONDS).body(
            "event: reply\ndata: {\"thread_id\":\"t5\",\"blocks\":[{\"type\":\"text\",\"id\":\"w\",\"text\":\"پیش‌نویس آماده است\"}]}\n\n").build())
        val flushing = async(Dispatchers.Default) { outbox.flush() }
        until { vm.state.value.sendingId == bubble.clientId }      // on its way: its bubble no longer says "waiting"
        assertEquals(null, vm.state.value.notice)                  // and "no connection" is gone
        assertEquals(Outbox.Result.Done, flushing.await())
        until { vm.state.value.items.last() is ChatItem.Words }
        assertTrue(vm.state.value.queued.isEmpty())
        assertEquals("t5", vm.state.value.threadId)
        assertEquals(null, vm.state.value.sendingId)
    }

    @Test fun reopeningShowsWhatStillWaitsAfterTheConversation() = runBlocking {
        val outbox = Outbox(MemoryOutboxStore(), api)
        outbox.add(Queued("m9", "و قبض برق", threadId = "t1", queuedAt = 1, userId = "u1"))
        reply("""[{"id":"t1","title":"اجاره","message_count":2}]""")
        reply("""[{"id":"s1","role":"user","text":"اجاره را ثبت کن","client_message_id":"m1"},""" +
              """{"id":"s2","role":"assistant","blocks":[{"type":"text","id":"b1","text":"ثبت شد"}]}]""")
        reply("""{"thread_id":"t1","blocks":[]}""")                             // no briefing today
        offline = false
        reply("event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[{\"type\":\"text\",\"id\":\"b2\",\"text\":\"قبض هم ثبت شد\"}]}\n\n")
        val vm = ChatViewModel(api, outbox = outbox)
        until { vm.state.value.items.lastOrNull() is ChatItem.Words && (vm.state.value.items.last() as ChatItem.Words).text == "قبض هم ثبت شد" }
        val users = vm.state.value.items.filterIsInstance<ChatItem.User>()
        assertEquals(listOf("اجاره را ثبت کن", "و قبض برق"), users.map { it.text })
        assertTrue(outbox.entries.value.isEmpty())
    }

    /** Scenario AC4: a reply goes with its quote, shows it in its bubble, and the composer forgets it. */
    @Test fun aReplyGoesWithWhatItQuotes() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[{\"type\":\"text\",\"id\":\"w\",\"text\":\"حدود ۲۰۰ دلار\"}]}\n\n")
        vm.reply(Quote(mine = false, text = "۷۷٫۵۰۰ ریال عمان در کیف پول سبز", token = null))
        assertEquals("۷۷٫۵۰۰ ریال عمان در کیف پول سبز", vm.state.value.replyTo?.text)
        vm.edit("و به دلار؟")
        vm.send()
        assertEquals(null, vm.state.value.replyTo)                                 // the strip above the composer goes
        val bubble = vm.state.value.items.filterIsInstance<ChatItem.User>().single()
        assertEquals(Quote(false, "۷۷٫۵۰۰ ریال عمان در کیف پول سبز"), bubble.quote)
        until { vm.state.value.items.last() is ChatItem.Words }
        val sent = server.takeRequest().body!!.utf8()
        assertTrue(sent, sent.contains("\"reply_to\":{\"role\":\"assistant\",\"text\":\"۷۷٫۵۰۰ ریال عمان در کیف پول سبز\"}"))
        // the next message answers nothing
        reply("event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[]}\n\n")
        vm.edit("ممنون")
        vm.send()
        until { !vm.state.value.sending }
        assertTrue(!server.takeRequest().body!!.utf8().contains("reply_to"))
    }

    /** Scenario AC6: offline, the reply waits in the outbox with its quote, a card's token too. */
    @Test fun offlineAReplyWaitsWithItsQuote() = runBlocking {
        val outbox = Outbox(MemoryOutboxStore(), api)
        val vm = ChatViewModel(api, restore = false, outbox = outbox)
        offline = true
        vm.reply(Quote(mine = false, text = "کیف پول سبز — موجودی اولیه · 77.500 OMR", token = "tok-1"))
        vm.edit("مبلغش رو ۸۰ کن")
        vm.send()
        until { !vm.state.value.sending }
        assertEquals(ReplyDto("assistant", "کیف پول سبز — موجودی اولیه · 77.500 OMR", "tok-1"), outbox.entries.value.single().replyTo)
    }

    /** Scenario AC4: the thread opened again keeps the quote above the message. */
    @Test fun reopeningShowsWhatAMessageReplied() = runBlocking {
        reply("""[{"id":"t1","title":"پول","message_count":2}]""")
        reply("""[{"id":"s1","role":"user","text":"و به دلار؟","reply_to":{"role":"assistant","text":"۷۷٫۵۰۰ ریال عمان"}},""" +
              """{"id":"s2","role":"assistant","blocks":[{"type":"text","id":"b1","text":"حدود ۲۰۰ دلار"}]}]""")
        reply("""{"thread_id":"t1","blocks":[]}""")
        val vm = ChatViewModel(api)
        until { vm.state.value.items.any { it is ChatItem.Words } }
        assertEquals(Quote(false, "۷۷٫۵۰۰ ریال عمان"), vm.state.value.items.filterIsInstance<ChatItem.User>().single().quote)
    }

    /** Scenario N39: a message that carried only a statement comes back with its name, not as an empty bubble. */
    @Test fun reopeningShowsTheFilesAMessageCarried() = runBlocking {
        reply("""[{"id":"t1","title":"صورتحساب","message_count":2}]""")
        reply("""[{"id":"s1","role":"user","text":"","files":[{"name":"mellat-mehr.csv","content_type":"text/csv"}]},""" +
              """{"id":"s2","role":"assistant","blocks":[{"type":"text","id":"b1","text":"خواندم"}]}]""")
        reply("""{"thread_id":"t1","blocks":[]}""")
        val vm = ChatViewModel(api)
        until { vm.state.value.items.any { it is ChatItem.Words } }
        val user = vm.state.value.items.filterIsInstance<ChatItem.User>().single()
        assertEquals("" to listOf("mellat-mehr.csv"), user.text to user.files)
    }

    @Test fun olderMessagesComeAPageAtATimeAndCatchingUpSkipsWhatIsDrawn() = runBlocking {
        reply("""[{"id":"t1","message_count":200}]""")
        server.enqueue(MockResponse.Builder().addHeader("X-More-Before", "true").body(
            """[{"id":"s9","role":"user","text":"آخری","client_message_id":"m9"},""" +
            """{"id":"s10","role":"assistant","blocks":[{"type":"text","id":"b10","text":"باشه"}]}]""").build())
        reply("""{"thread_id":"t1","blocks":[]}""")
        val vm = ChatViewModel(api)
        until { vm.state.value.items.size == 3 }
        assertTrue(vm.state.value.items.first() is ChatItem.Earlier)
        server.takeRequest(); val firstPage = server.takeRequest(); server.takeRequest()
        assertEquals("60", firstPage.url.queryParameter("limit"))
        server.enqueue(MockResponse.Builder().addHeader("X-More-Before", "false").body(
            """[{"id":"s1","role":"user","text":"اولی"}]""").build())
        vm.earlier()
        until { vm.state.value.items.first() is ChatItem.User }
        assertEquals("s9", server.takeRequest().url.queryParameter("before"))
        assertEquals(listOf("اولی", "آخری"), vm.state.value.items.filterIsInstance<ChatItem.User>().map { it.text })
        // back in the app: the web chat added a turn; the phone's own message isn't drawn twice
        reply("""[{"id":"s10b","role":"user","text":"آخری","client_message_id":"m9"},""" +
              """{"id":"s11","role":"user","text":"از وب"},{"id":"s12","role":"assistant","blocks":[{"type":"text","id":"b12","text":"دیدم"}]}]""")
        vm.catchUp()
        until { (vm.state.value.items.last() as? ChatItem.Words)?.text == "دیدم" }
        assertEquals("s10", server.takeRequest().url.queryParameter("after"))
        assertEquals(listOf("اولی", "آخری", "از وب"), vm.state.value.items.filterIsInstance<ChatItem.User>().map { it.text })
    }

    @Test fun aConversationBegunHereCatchesUpToo() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("event: reply\ndata: {\"thread_id\":\"t3\",\"blocks\":[{\"type\":\"text\",\"id\":\"b1\",\"text\":\"صفر\"}]}\n\n")
        vm.edit("موجودی چقدره؟")
        vm.send()
        until { vm.state.value.items.lastOrNull() is ChatItem.Words }
        val clientId = (vm.state.value.items.first() as ChatItem.User).clientId
        server.takeRequest()
        // meanwhile the web chat drafted a voucher in this thread
        reply("""[{"id":"s1","role":"user","text":"موجودی چقدره؟","client_message_id":"$clientId"},""" +
              """{"id":"s2","role":"assistant","blocks":[{"type":"text","id":"b1","text":"صفر"}]},""" +
              """{"id":"s3","role":"user","text":"نان و میوه"},""" +
              """{"id":"s4","role":"assistant","blocks":[{"type":"proposal","id":"proposal:t5","token":"t5","title":"نان","lines":[]}]}]""")
        vm.catchUp()
        until { vm.state.value.items.lastOrNull() is ChatItem.Proposal }
        assertEquals("/api/mobile/v1/threads/t3/messages", server.takeRequest().url.encodedPath)
        assertEquals(listOf("موجودی چقدره؟", "نان و میوه"), vm.state.value.items.filterIsInstance<ChatItem.User>().map { it.text })
        assertEquals(1, vm.state.value.items.count { it is ChatItem.Words })
    }

    private fun draft(vm: ChatViewModel, token: String = "t1", type: String = "proposal", extra: String = "") {
        reply("event: reply\ndata: {\"thread_id\":\"t1\",\"blocks\":[{\"type\":\"$type\",\"id\":\"$type:$token\",\"token\":\"$token\"," +
              "\"title\":\"Office rent\",\"amount\":{\"value\":80000000,\"currency\":\"IRR\"},\"lines\":[]$extra}]}\n\n")
        vm.edit("record the rent")
        vm.send()
    }

    @Test fun anEditedDraftIsFollowedByItsNewVoucher() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        draft(vm)
        until { vm.state.value.items.any { it is ChatItem.Proposal } }
        server.takeRequest()
        reply("""{"replaces":"t1","block":{"type":"proposal","id":"proposal:t2","token":"t2","title":"Office rent, Shahrivar",""" +
              """"amount":{"value":85000000,"currency":"IRR"},"lines":[]}}""")
        var said: String? = "pending"
        vm.edit("proposal:t1", EditRequest(amount = "85000000")) { said = it }
        until { said != "pending" }
        assertEquals(null, said)
        val cards = vm.state.value.items.filterIsInstance<ChatItem.Proposal>()
        assertEquals(listOf("t1" to ChatItem.Proposal.Phase.Replaced, "t2" to ChatItem.Proposal.Phase.Draft), cards.map { it.token to it.phase })
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/proposals/t1/edit", sent.url.encodedPath)
        assertEquals("""{"amount":"85000000"}""", sent.body!!.utf8())         // as written: pence go exactly
        // a refusal is said in the sheet, and the card stays as it was
        reply("""{"detail":"این تاریخ خوانده نشد."}""", code = 422)
        vm.edit("proposal:t2", EditRequest(date = "someday")) { said = it }
        until { said != null }
        assertEquals("این تاریخ خوانده نشد.", said)
        assertEquals(ChatItem.Proposal.Phase.Draft, vm.state.value.items.filterIsInstance<ChatItem.Proposal>().last().phase)
    }

    @Test fun anApproverApprovesOneAndRejectsAnother() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        draft(vm, token = "t7", type = "approval", extra = ",\"requested_by\":\"maryam\",\"mine\":false")
        until { vm.state.value.items.any { it is ChatItem.Proposal } }
        server.takeRequest()
        reply("""{"state":"posted","block":{"type":"posted","voucher":"1042","undo_seconds":120,"audit_log_id":"a1",""" +
              """"date":{"iso":"2026-10-10","display":"۱۸ مهر ۱۴۰۵"}}}""")
        vm.approve("approval:t7")
        until { vm.state.value.items.filterIsInstance<ChatItem.Proposal>().single().phase == ChatItem.Proposal.Phase.Posted }
        assertEquals("/api/mobile/v1/approvals/t7/approve", server.takeRequest().url.encodedPath)
        assertEquals("1042", vm.state.value.items.filterIsInstance<ChatItem.Proposal>().single().voucher)
        draft(vm, token = "t8", type = "approval", extra = ",\"requested_by\":\"maryam\",\"mine\":false")
        until { vm.state.value.items.count { it is ChatItem.Proposal } == 2 }
        server.takeRequest()
        reply("""{"state":"rejected"}""")
        vm.reject("approval:t8", "Wrong month")
        until { vm.state.value.items.filterIsInstance<ChatItem.Proposal>().last().phase == ChatItem.Proposal.Phase.Rejected }
        val r = server.takeRequest()
        assertEquals("/api/mobile/v1/approvals/t8/reject", r.url.encodedPath)
        assertEquals("""{"note":"Wrong month"}""", r.body!!.utf8())
    }

    @Test fun theNextDifferenceComesAsAVoucherWithTheNextOneOffered() = runBlocking {
        val vm = ChatViewModel(api, restore = false)
        reply("""{"thread_id":"t4","blocks":[{"type":"proposal","id":"proposal:r1","token":"r1","title":"کارمزد بانکی",""" +
              """"amount":{"value":90000,"currency":"IRR"},"lines":[],"actions":["confirm","edit","cancel"]},""" +
              """{"type":"text","id":"text:m1","text":"پس از این، ۱ ردیف دیگر مانده.","kind":"statement_next","statement_id":"s9"}]}""")
        vm.nextDifference("s9")
        until { vm.state.value.items.lastOrNull() is ChatItem.Words }
        val sent = server.takeRequest()
        assertEquals("/api/mobile/v1/statements/s9/next", sent.url.encodedPath)
        assertEquals("s9", (vm.state.value.items.last() as ChatItem.Words).nextStatement)
        assertEquals("t4", vm.state.value.threadId)
        reply("""{"thread_id":"t4","blocks":[{"type":"text","id":"text:m2","text":"همهٔ ردیف‌ها در دفاتر است."}]}""")
        vm.nextDifference("s9")
        until { (vm.state.value.items.last() as? ChatItem.Words)?.text == "همهٔ ردیف‌ها در دفاتر است." }
        assertTrue(server.takeRequest().body!!.utf8().contains("\"thread_id\":\"t4\""))
    }
}
