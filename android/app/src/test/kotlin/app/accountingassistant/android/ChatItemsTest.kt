package app.accountingassistant.android

import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.ui.chat.editChange
import app.accountingassistant.android.ui.chat.editableDate
import app.accountingassistant.android.ui.chat.parseBlock
import app.accountingassistant.android.ui.chat.parseBlocks
import app.accountingassistant.android.ui.chat.Quote
import app.accountingassistant.android.ui.chat.asText
import app.accountingassistant.android.ui.chat.quote
import app.accountingassistant.android.ui.chat.summaryOf
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
        assertEquals(java.math.BigDecimal(80_000_000), item.amount)
        assertEquals("۱۸ مهر ۱۴۰۵", item.date)
        assertEquals(listOf("6112", "1110"), item.lines.map { it.code })
        assertEquals(ChatItem.Proposal.Phase.Posted, item.phase)          // redrawn from history
    }

    /** «امیر هم به‌عنوان طرف معامله جدید به کارت اضافه شده» — and the card named no one (scenario AK4). */
    @Test fun aCardSaysWhoItNames() {
        val item = parseBlock(block("""{"type":"proposal","id":"proposal:t7","token":"t7","title":"پول امیر",
            "amount":{"value":20,"currency":"OMR"},"lines":[],"needs_approval":false,
            "people":[{"name":"امیر","new":false},{"name":"سارا","new":true},{"name":"","new":true}]}""")) as ChatItem.Proposal
        assertEquals(listOf(ChatItem.Proposal.Person("امیر", false), ChatItem.Proposal.Person("سارا", true)), item.people)
        val none = parseBlock(block("""{"type":"proposal","id":"p8","token":"t8","title":"x","lines":[]}""")) as ChatItem.Proposal
        assertEquals(emptyList<ChatItem.Proposal.Person>(), none.people)          // an older server sends none
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

    /** £7.50 came in as nothing (a Long), and Edit kept only digits: «4.25» became 425 (docs/PLAN_PENCE.md). */
    @Test fun penceArriveAndEditKeepsThem() {
        val item = parseBlock(block("""{"type":"proposal","id":"proposal:t3","token":"t3","title":"Coffee",
            "amount":{"value":7.5,"currency":"GBP"},"date":{"iso":"2026-10-11","display":"11 Oct 2026"},
            "lines":[{"account":"7400","name":"Entertaining","debit":7.5,"credit":0},
                     {"account":"1200","name":"Barclays","debit":0,"credit":7.5}],"needs_approval":false}""")) as ChatItem.Proposal
        assertEquals(java.math.BigDecimal("7.5"), item.amount)
        assertEquals(listOf(java.math.BigDecimal("7.5"), java.math.BigDecimal.ZERO), item.lines.map { it.debit })
        assertTrue(item.amountEditable)
        assertEquals("7.50", app.accountingassistant.android.ui.chat.typedAmount("7.505", "en", 2))
        assertEquals("1,234.5", app.accountingassistant.android.ui.chat.typedAmount("1234.5", "en", 2))
        assertEquals("۷٫۵", app.accountingassistant.android.ui.chat.typedAmount("۷٫۵", "fa", 2))
        assertEquals("۸۵٬۰۰۰٬۰۰۰", app.accountingassistant.android.ui.chat.typedAmount("85000000.5", "fa", 0))  // rials: no decimals
        assertEquals(EditRequest(amount = "4.25"), editChange(item, "en", editableDate(item.dateIso, "en"), item.title, "4.25"))
        assertEquals(null, editChange(item, "en", editableDate(item.dateIso, "en"), item.title, "7.50"))   // the same amount
    }

    /** «Count the books in Omani rials, or keep this in its own currency?», with its answers to tap (scenario AB1). */
    @Test fun aQuestionComesWithItsAnswersToTap() {
        val item = parseBlock(block("""{"type":"text","id":"text:1","text":"ارز دفاتر ریال عمان شود، یا این پول به ریال عمان بماند؟",
            "choices":["دفاتر به ریال عمان","ریال ایران بماند","  "],"fallback_text":"…"}""")) as ChatItem.Words
        assertEquals(listOf("دفاتر به ریال عمان", "ریال ایران بماند"), item.choices)
        val plain = parseBlock(block("""{"type":"text","id":"text:2","text":"ثبت شد.","fallback_text":"ثبت شد."}""")) as ChatItem.Words
        assertTrue(plain.choices.isEmpty())
    }

    /** The model's «**…**» reads bold, without its stars (scenario AB7). */
    @Test fun boldMarksBecomeBoldWords() {
        val t = app.accountingassistant.android.ui.components.boldMarks("دفاتر شما با **ریال ایران (IRR)** ثبت می‌شوند؛ ۲ * ۳")
        assertEquals("دفاتر شما با ریال ایران (IRR) ثبت می‌شوند؛ ۲ * ۳", t.text)
        assertEquals(1, t.spanStyles.size)
        assertEquals("ریال ایران (IRR)", t.text.substring(t.spanStyles[0].start, t.spanStyles[0].end))
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
        assertEquals(EditRequest(date = "1405/06/25", amount = "85000000"),
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

    private val wallet = """{"type":"proposal","id":"proposal:t9","token":"t9","title":"کیف پول سبز — موجودی اولیه",
        "amount":{"value":77.5,"currency":"OMR"},"date":{"iso":"2026-10-16","display":"۲۴ مهر ۱۴۰۵"},
        "lines":[{"account":"112001","name":"کیف پول سبز","debit":77.5,"credit":0},
                 {"account":"3110","name":"خالص دارایی اولیه","debit":0,"credit":77.5}],"needs_approval":false}"""

    /** Scenarios AC2, AC8: a card copied as it reads, its lines To and From in a person's books. */
    @Test fun aCardCopiesAsWhatItIsItsAmountAndItsLines() {
        val card = parseBlock(block(wallet))
        assertEquals("کیف پول سبز — موجودی اولیه · ۲۴ مهر ۱۴۰۵\n77.500 OMR\n" +
                     "112001 کیف پول سبز  To 77.500\n3110 خالص دارایی اولیه  From 77.500",
                     card.asText("en", "To" to "From"))
        assertEquals("۳۱۱۰ خالص دارایی اولیه  بس ۷۷٫۵۰۰", card.asText("fa", "بد" to "بس")!!.lines().last())
        // a reply to it quotes its title and amount, and names the card
        assertEquals(Quote(false, "کیف پول سبز — موجودی اولیه · 77.500 OMR", "t9"), card.quote("en", "Dr" to "Cr"))
    }

    /** Scenario AC1: the words as they read, bold without its stars; a long one quoted in short. */
    @Test fun wordsCopyWithoutTheirMarksAndAQuoteIsShort() {
        val words = ChatItem.Words("w", "در **کیف پول سبز** ۷۷٫۵۰۰ ریال عمان دارید.")
        assertEquals("در کیف پول سبز ۷۷٫۵۰۰ ریال عمان دارید.", words.asText("fa", "بد" to "بس"))
        val long = ChatItem.Words("w2", "word\n".repeat(200)).quote("en", "Dr" to "Cr")!!
        assertEquals(400, long.text.length)
        assertTrue(long.text.startsWith("word word") && long.token == null && !long.mine)
        assertEquals(Quote(true, "و به دلار؟"), ChatItem.User("u", "و به دلار؟").quote("fa", "بد" to "بس"))
        assertEquals(null, ChatItem.Thinking().asText("en", "Dr" to "Cr"))
    }

    /** Scenario AC9: the date in the direction of its own words, inside a line that follows its first word. */
    @Test fun aCardsDateIsIsolatedInItsOwnDirection() {
        assertEquals("کیف پول سبز، \u2067۲۴ مهر ۱۴۰۵\u2069", summaryOf("کیف پول سبز", "۲۴ مهر ۱۴۰۵"))
        assertEquals("Office rent · \u206610 Oct 2026\u2069", summaryOf("Office rent", "10 Oct 2026"))
        assertEquals("Office rent", summaryOf("Office rent", null))
    }
}
