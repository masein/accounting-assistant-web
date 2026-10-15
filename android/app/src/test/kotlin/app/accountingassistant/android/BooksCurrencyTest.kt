package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.CompanyDto
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** A person's books choose their currency (scenario Z3): the phone's books say so at once, and after a restart. */
class BooksCurrencyTest {
    @Test fun choosingOmaniRialsIsTheBooksCurrencyFromNowOn() = runBlocking {
        val server = MockWebServer().apply { start() }
        try {
            val store = MemorySessionStore()
            store.save(StoredSession("A1", "R1", "d1", UserDto("u1", "masein"),
                                     CompanyDto("c1", "masein", baseCurrency = "IRR", kind = "personal")))
            val api = ApiClient(base = server.url("/").toString(), store = store, appVersion = "0.1.0", language = { "fa" })
            api.restore()
            server.enqueue(MockResponse.Builder().code(200).body("""{"currency":"OMR","name":"ریال عمان","changed":true,
                "options":[{"code":"IRR","name":"ریال ایران"},{"code":"OMR","name":"ریال عمان"}]}""").build())
            val r = api.setBooksCurrency("OMR")
            assertEquals("OMR", r.currency)
            assertEquals(listOf("IRR", "OMR"), r.options.map { it.code })
            val req = server.takeRequest()
            assertEquals("PUT", req.method)
            assertEquals("/api/mobile/v1/books/currency", req.url.encodedPath)
            assertTrue(req.body!!.utf8().contains("\"currency\":\"OMR\""))
            assertEquals("OMR", api.session!!.company!!.baseCurrency)
            assertEquals("OMR", store.load()!!.company!!.baseCurrency)          // kept for the next start
        } finally {
            server.close()
        }
    }
}
