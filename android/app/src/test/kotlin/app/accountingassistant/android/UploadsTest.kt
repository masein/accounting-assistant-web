package app.accountingassistant.android

import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.MemorySessionStore
import app.accountingassistant.android.data.NetworkError
import app.accountingassistant.android.data.StoredSession
import app.accountingassistant.android.data.UserDto
import kotlinx.coroutines.runBlocking
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.IOException

/** A file in pieces (scenario N37): from where the server stands, after a drop, never twice. */
class UploadsTest {
    /** The server's side of an upload, taking at most 100 bytes a piece. */
    private class Pieces(val size: Int, var received: Int = 0, var has: Boolean = false, var damage: Int = 0,
                         val max: Int = 100) : Dispatcher() {
        val kept = ByteArrayOutputStream()
        val offsets = mutableListOf<Int>()
        var begins = 0

        override fun dispatch(request: RecordedRequest): MockResponse = when {
            request.url.encodedPath.endsWith("/uploads/begin") -> { begins++; state() }
            request.method == "PUT" -> {
                val at = request.url.queryParameter("offset")!!.toInt()
                offsets += at
                if (at == received) { kept.write(request.body!!.toByteArray()); received += request.bodySize.toInt() }
                if (received == size && damage > 0) {
                    damage--; received = 0; kept.reset()
                    MockResponse.Builder().code(422).addHeader("X-Error-Code", "upload_corrupt")
                        .body("""{"detail":"The file arrived damaged. It will be sent again."}""").build()
                } else {
                    if (received == size) has = true
                    state()
                }
            }
            else -> MockResponse.Builder().code(404).build()
        }

        fun state(): MockResponse = MockResponse.Builder().body(
            if (has) """{"id":"up1","received":$size,"size_bytes":$size,"max_chunk":$max,"done":true,
                         "attachment":{"id":"att-1","file_name":"statement.pdf","content_type":"application/pdf","size_bytes":$size}}"""
            else """{"id":"up1","received":$received,"size_bytes":$size,"max_chunk":$max,"done":false}""").build()
    }

    private val server = MockWebServer()
    /** The n-th piece (from 1) finds no network. */
    private var dropPiece = 0
    private var pieces = 0
    private lateinit var api: ApiClient
    private val file = ByteArray(250) { (it * 7).toByte() }

    @Before fun start() {
        server.start()
        val http = OkHttpClient.Builder().addInterceptor { chain ->
            if (chain.request().method == "PUT" && ++pieces == dropPiece) throw IOException("no network")
            chain.proceed(chain.request())
        }.build()
        api = ApiClient(base = server.url("/").toString(),
                        store = MemorySessionStore(StoredSession("A1", "R1", "d1", UserDto("u1", "maryam"))),
                        appVersion = "0.1.0", language = { "fa" }, http = http)
        runBlocking { api.restore() }
    }

    @After fun stop() = server.close()

    private fun serve(p: Pieces) = p.also { server.dispatcher = it }

    @Test fun aFileGoesInPiecesSayingHowMuchHasGone() = runBlocking {
        val s = serve(Pieces(file.size))
        val seen = mutableListOf<Float>()
        val r = api.upload(file, "statement.pdf", "application/pdf") { seen += it }
        assertEquals("att-1", r.id)
        assertEquals(listOf(0, 100, 200), s.offsets)
        assertArrayEquals(file, s.kept.toByteArray())
        assertEquals(listOf(0f, 0.4f, 0.8f, 1f), seen)
        val begin = server.takeRequest().body!!.utf8()
        assertTrue(begin.contains("\"size_bytes\":250") && begin.contains("\"sha256\":\"") && begin.contains("\"file_name\":\"statement.pdf\""))
    }

    @Test fun afterADropItGoesOnFromWhatArrivedNotFromNothing() = runBlocking {
        val s = serve(Pieces(file.size))
        dropPiece = 2
        val dropped = runCatching { api.upload(file, "statement.pdf", "application/pdf") }.exceptionOrNull()
        assertTrue(dropped is NetworkError)                          // the outbox keeps it and tries later
        assertEquals(100, s.received)
        assertEquals("att-1", api.upload(file, "statement.pdf", "application/pdf").id)
        assertEquals(listOf(0, 100, 200), s.offsets)                 // the first piece never went twice
        assertArrayEquals(file, s.kept.toByteArray())
    }

    @Test fun aFileTheServerHasIsNotSentAgain() = runBlocking {
        val s = serve(Pieces(file.size, has = true))
        val seen = mutableListOf<Float>()
        assertEquals("att-1", api.upload(file, "statement.pdf", "application/pdf") { seen += it }.id)
        assertTrue(s.offsets.isEmpty())
        assertEquals(listOf(1f), seen)
    }

    @Test fun aDamagedFileIsSentAgainFromTheStartOnceOnly() = runBlocking {
        val s = serve(Pieces(file.size, damage = 1))
        assertEquals("att-1", api.upload(file, "statement.pdf", "application/pdf").id)
        assertEquals(listOf(0, 100, 200, 0, 100, 200), s.offsets)
        assertEquals(2, s.begins)
        val twice = serve(Pieces(file.size, damage = 2))
        val err = runCatching { api.upload(file, "statement.pdf", "application/pdf") }.exceptionOrNull() as ApiError
        assertEquals("upload_corrupt", err.code)
        assertEquals(2, twice.begins)
    }

    @Test fun piecesGrowOnAFastLineAndShrinkOnASlowOne() {
        assertEquals(128 * 1024, ApiClient.nextPiece(64 * 1024, 0.2, 1024 * 1024))
        assertEquals(1024 * 1024, ApiClient.nextPiece(1024 * 1024, 0.2, 1024 * 1024))     // never past the server's limit
        assertEquals(256 * 1024, ApiClient.nextPiece(256 * 1024, 2.5, 1024 * 1024))       // steady
        assertEquals(128 * 1024, ApiClient.nextPiece(256 * 1024, 9.0, 1024 * 1024))
        assertEquals(64 * 1024, ApiClient.nextPiece(64 * 1024, 30.0, 1024 * 1024))        // never below the first piece
        assertEquals(100, ApiClient.nextPiece(100, 30.0, 100))
    }

    @Test fun onAFastLineTheFirstPieceIsSmallAndTheNextOnesGrow() = runBlocking {
        val big = ByteArray(300 * 1024) { it.toByte() }
        val s = serve(Pieces(big.size, max = 1024 * 1024))
        api.upload(big, "statement.pdf", "application/pdf")
        assertEquals(listOf(0, 64 * 1024, 192 * 1024), s.offsets)
        assertArrayEquals(big, s.kept.toByteArray())
    }
}
