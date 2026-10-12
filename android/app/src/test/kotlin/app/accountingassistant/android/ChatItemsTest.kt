package app.accountingassistant.android

import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.ui.chat.editChange
import app.accountingassistant.android.ui.chat.editableDate
import app.accountingassistant.android.ui.chat.parseBlock
import app.accountingassistant.android.ui.chat.parseBlocks
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

    /** Opened again, a seal came back blank and an undone voucher came back posted (phone QA, 2026-10-12). */
    @Test fun aPostedCardOpenedAgainKeepsItsSealOrSaysItWasUndone() {
        fun card(undone: Boolean) = parseBlock(block("""{"type":"proposal","id":"proposal:t2","token":"t2","title":"Printer",
            "amount":{"value":240,"currency":"GBP"},"lines":[],"needs_approval":false,"state":"executed",
            "posted":{"voucher":"INV-1001","date":{"iso":"2026-10-11","display":"11 Oct 2026"},"undone":$undone}}""")) as ChatItem.Proposal
        val posted = card(false)
        assertEquals(ChatItem.Proposal.Phase.Posted, posted.phase)
        assertEquals("INV-1001", posted.voucher)
        assertEquals("11 Oct 2026", posted.postedDate)
        assertEquals(ChatItem.Proposal.Phase.Undone, card(true).phase)
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

    @Test fun anApprovalCardKnowsWhoAskedAndARejectedOneSaysSo() {
        val card = parseBlock(block("""{"type":"approval","id":"approval:t9:m1","token":"t9","title":"Office rent",
            "amount":{"value":80000000,"currency":"IRR"},"date":{"iso":"2026-10-10","display":"۱۸ مهر ۱۴۰۵"},
            "lines":[],"needs_approval":true,"requested_by":"maryam","mine":false}""")) as ChatItem.Proposal
        assertEquals(ChatItem.Proposal.Approval("maryam", mine = false), card.approval)
        assertEquals(ChatItem.Proposal.Phase.Draft, card.phase)
        assertEquals("2026-10-10", card.dateIso)
        val rejected = parseBlock(block("""{"type":"approval","id":"a2","token":"t9","title":"x","lines":[],
            "requested_by":"maryam","state":"cancelled","approval_status":"rejected"}""")) as ChatItem.Proposal
        assertEquals(ChatItem.Proposal.Phase.Rejected, rejected.phase)
        // the asker's own card, sent for approval, waits when redrawn
        val asked = parseBlock(block("""{"type":"proposal","id":"p3","token":"t9","title":"x","lines":[],
            "state":"pending","approval_status":"requested"}""")) as ChatItem.Proposal
        assertEquals(ChatItem.Proposal.Phase.Waiting, asked.phase)
        // an edited one, redrawn: changed, not cancelled
        val edited = parseBlock(block("""{"type":"proposal","id":"p4","token":"t4","title":"x","lines":[],"state":"replaced"}""")) as ChatItem.Proposal
        assertEquals(ChatItem.Proposal.Phase.Replaced, edited.phase)
    }

    @Test fun editSendsOnlyWhatChangedInTheUsersDigits() {
        val p = parseBlock(block("""{"type":"proposal","id":"p1","token":"t1","title":"Office rent",
            "amount":{"value":80000000,"currency":"IRR"},"date":{"iso":"2026-10-10","display":"۱۸ مهر ۱۴۰۵"},
            "lines":[{"account":"6112","name":"اجاره","debit":80000000,"credit":0},
                     {"account":"1110","name":"بانک","debit":0,"credit":80000000}]}""")) as ChatItem.Proposal
        assertEquals("۱۴۰۵/۰۷/۱۸", editableDate(p.dateIso, "fa"))
        assertEquals(null, editChange(p, "fa", "۱۴۰۵/۰۷/۱۸", "Office rent", "۸۰۰۰۰۰۰۰"))
        assertEquals(EditRequest(date = "1405/06/25", amount = 85_000_000),
                     editChange(p, "fa", "۱۴۰۵/۰۶/۲۵", "Office rent ", "۸۵٬۰۰۰٬۰۰۰"))
        val several = p.copy(lines = p.lines + p.lines)
        assertEquals(EditRequest(description = "Rent"), editChange(several, "en", "2026-10-10", "Rent", "1"))
    }

    @Test fun theChartIsDrawnToScaleWithZeroInIt() {
        val y = app.accountingassistant.android.ui.chat.chartScale(listOf(100L, -50L, 250L))
        assertEquals(0f, y(-50L))                                          // the bottom is the lowest value…
        assertEquals(1f, y(250L))                                          // …the top the highest
        assertEquals(50f / 300f, y(0L), 1e-6f)                             // and zero sits where it is
        val allAbove = app.accountingassistant.android.ui.chat.chartScale(listOf(400L, 800L))
        assertEquals(0f, allAbove(0L))                                     // a chart of money always shows zero
    }

    /** The Spanish and Arabic pass (2026-10-11): a statement sent again came back as the same sentence twice. */
    @Test fun aStatementSentAgainIsItsCardAndItsWordsOnce() {
        val words = "Este archivo ya se importó el 11 oct 2026 como «mellat.csv»."
        val reply = listOf(
            block("""{"type":"intake","id":"i1","kind":"bank_statement","status":"duplicate","statement_id":"s1",
                      "total_rows":24,"from_date":"2026-10-01","to_date":"2026-10-08","counts":{"unrecorded":24},"fallback_text":""}"""),
            block("""{"type":"text","id":"t1","text":"$words"}"""))
        val items = parseBlocks(reply)
        assertEquals(2, items.size)
        assertEquals(24, (items[0] as ChatItem.Statement).rows)
        assertEquals(words, (items[1] as ChatItem.Words).text)
        // replies stored before the fix: a card the app can't draw, whose fallback repeats the words
        val old = listOf(block("""{"type":"intake","id":"i2","kind":"bank_statement","status":"duplicate","statement_id":"s1",
                                   "fallback_text":"$words"}"""),
                         block("""{"type":"text","id":"t2","text":"$words"}"""))
        assertEquals(listOf("t2"), parseBlocks(old).map { it.id })
    }
}
