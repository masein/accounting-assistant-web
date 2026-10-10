package app.accountingassistant.android

import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.parseBlock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatItemsTest {
    private fun block(s: String) = Json.parseToJsonElement(s) as JsonObject

    @Test fun aProposalBecomesAVoucher() {
        val item = parseBlock(block("""{"type":"proposal","id":"proposal:t1","token":"t1","title":"Office rent",
            "amount":{"value":80000000,"currency":"IRR"},"date":{"iso":"2026-10-10","display":"۱۸ مهر ۱۴۰۵"},
            "lines":[{"account":"6112","name":"اجاره","debit":80000000,"credit":0},
                     {"account":"1110","name":"بانک","debit":0,"credit":80000000}],
            "needs_approval":false,"state":"executed"}""")) as ChatItem.Proposal
        assertEquals(80_000_000L, item.amount)
        assertEquals("۱۸ مهر ۱۴۰۵", item.date)
        assertEquals(listOf("6112", "1110"), item.lines.map { it.code })
        assertEquals(ChatItem.Proposal.Phase.Posted, item.phase)          // redrawn from history
    }

    @Test fun anUnknownBlockFallsBackToItsSentence() {
        val item = parseBlock(block("""{"type":"hologram","id":"h1","fallback_text":"A chart of spending"}"""))
        assertTrue(item is ChatItem.Fallback)
        assertEquals("A chart of spending", (item as ChatItem.Fallback).text)
    }

    @Test fun aFileBlockIsADocumentToOpenOrShare() {
        val item = parseBlock(block("""{"type":"file","id":"file:invoice:1","name":"invoice-INV-1042.pdf",
            "mime":"application/pdf","path":"/api/mobile/v1/documents/invoices/1","fallback_text":"Invoice INV-1042 (PDF)"}""")) as ChatItem.File
        assertEquals("invoice-INV-1042.pdf", item.name)
        assertEquals("/api/mobile/v1/documents/invoices/1", item.path)
    }
}
