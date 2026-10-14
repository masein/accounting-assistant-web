package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.PhoneSms
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import app.accountingassistant.android.sms.BankSms
import app.accountingassistant.android.sms.BankSmsStore
import app.accountingassistant.android.sms.BankText
import app.accountingassistant.android.sms.SmsSender
import app.accountingassistant.android.sms.SmsState
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Bank SMS on the phone (scenarios Y6–Y9; roadmap ROADMAP_ANDROID_CHAT §7.1): what is kept, asked about and sent. */
class BankSmsTest {
    private class MemoryStore : BankSmsStore {
        var state = SmsState()
        override suspend fun load() = state
        override suspend fun save(state: SmsState) { this.state = state }
        override suspend fun clear() { state = SmsState() }
    }

    private val mellat = "بانک ملت\nبرداشت:۱٬۲۵۰٬۰۰۰\nحساب:123456789\nمانده:۸٬۴۰۰٬۰۰۰\n0722-13:45"
    private val friend = "سلام، فردا ساعت ۱۰ جلسه داریم؟"
    private val otp = "بانک ملت\nرمز پویا: ۴۸۲۹۱۳\nاعتبار ۲ دقیقه"

    @Test fun aBankMovementIsToldFromEverythingElse() {
        assertTrue(BankText.looksLikeMovement(mellat))
        assertEquals("Mellat", BankText.bankOf(mellat))
        assertFalse(BankText.looksLikeMovement(friend))
        assertFalse(BankText.looksLikeMovement(otp))                       // a code, not money
        assertFalse(BankText.looksLikeMovement("برداشت ۱۲۵٬۰۰۰"))           // no bank, no rials: not sure enough
        assertTrue(BankText.looksLikeMovement("واریز 65,433 ریال\nمانده 1,300,000"))
        // Arabic letters and digits read as Persian, as the server reads them
        assertEquals("Melli", BankText.bankOf("بانك ملي ايران\nبرداشت ٥٠٠٬٠٠٠"))
        assertEquals("98700717", BankText.senderKey("+98 700-717"))
        assertEquals("بانک ملت", BankText.displayName("Mellat", "fa"))
        assertEquals("پست بانک", BankText.displayName("PostBank", "fa"))
        assertEquals("Mellat", BankText.displayName("Mellat", "en"))
        assertNull(BankText.displayName("Nowhere", "fa"))
    }

    @Test fun y6ANewSendersMessageIsKeptAndAskedAboutOnceAFriendsIsNot() = runBlocking {
        val store = MemoryStore()
        val sms = BankSms(store)
        assertEquals(BankSms.Route.Asked, sms.arrived("+98700717", mellat, 1L))
        assertEquals(BankSms.Route.Held, sms.arrived("98700717", mellat.replace("13:45", "14:00"), 2L))   // asked already
        assertEquals(BankSms.Route.NotKept, sms.arrived("09121234567", friend, 3L))
        assertEquals(listOf(SmsSender("98700717", "Mellat", null) to 2), sms.senders())
        assertTrue(store.state.outbox.isEmpty())                            // nothing leaves before the user says
        assertTrue(store.state.waiting.none { it.text == friend })
    }

    @Test fun y7ReadSendsWhatWaitedAndWhatComesAfterButNotACode() = runBlocking {
        val store = MemoryStore()
        val sms = BankSms(store)
        sms.arrived("98700717", mellat, 1L)
        sms.decide("98700717", read = true)
        assertEquals(listOf(mellat), sms.outbox().map { it.text })
        assertEquals("Mellat", sms.outbox().single().bank)
        assertEquals(BankSms.Route.Queued, sms.arrived("98700717", mellat.replace("13:45", "18:00"), 2L))
        assertEquals(BankSms.Route.NotKept, sms.arrived("98700717", otp, 3L))   // from the bank, but no money moved
        assertEquals(2, sms.outbox().size)
        sms.done(sms.outbox().map { it.id }.toSet())
        assertTrue(sms.outbox().isEmpty())
        assertEquals(listOf(SmsSender("98700717", "Mellat", true) to 0), sms.senders())
    }

    @Test fun y8IgnoreThrowsAwayWhatWaitedAndWhatComesAfter() = runBlocking {
        val store = MemoryStore()
        val sms = BankSms(store)
        sms.arrived("9830007", "بانک سامان\nواریز ۲۰۰٬۰۰۰\nمانده ۲٬۰۰۰٬۰۰۰", 1L)
        sms.decide("9830007", read = false)
        assertTrue(store.state.waiting.isEmpty() && store.state.outbox.isEmpty())
        assertEquals(BankSms.Route.Ignored, sms.arrived("9830007", "بانک سامان\nواریز ۵۰٬۰۰۰\nمانده ۲٬۰۵۰٬۰۰۰", 2L))
        // changed later from the sheet: read from now on
        sms.decide("9830007", read = true)
        assertEquals(BankSms.Route.Queued, sms.arrived("9830007", "بانک سامان\nواریز ۷۰٬۰۰۰\nمانده ۲٬۱۲۰٬۰۰۰", 3L))
    }

    @Test fun y9SigningOutForgetsTheSendersAndWhatWaits() = runBlocking {
        val sms = BankSms(MemoryStore())
        sms.arrived("98700717", mellat, 1L)
        sms.clear()
        assertTrue(sms.senders().isEmpty() && sms.outbox().isEmpty())
    }

    @Test fun theyGoToTheServerWithTheirSenderTimeAndBank() = runBlocking {
        val server = MockWebServer().apply { start() }
        try {
            val store = MemorySessionStore()
            store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam")))
            val api = ApiClient(base = server.url("/").toString(), store = store, appVersion = "0.1.0", language = { "fa" })
            api.restore()
            server.enqueue(MockResponse.Builder().code(200)
                .body("""{"received":1,"added":1,"duplicates":0,"unread":0,"statements":[{"id":"s1"}]}""").build())
            val r = api.bankSms(listOf(PhoneSms("98700717", mellat, "2026-10-13T20:00+03:30", "Mellat")))
            assertEquals(1, r.added)
            val req = server.takeRequest()
            assertEquals("/api/mobile/v1/bank-sms", req.url.encodedPath)
            assertEquals("Bearer A1", req.headers["Authorization"])
            val body = req.body!!.utf8()
            assertTrue(body, body.contains("\"sender\":\"98700717\"") && body.contains("\"received_at\":\"2026-10-13T20:00+03:30\"")
                && body.contains("\"bank\":\"Mellat\""))
        } finally {
            server.close()
        }
    }
}
