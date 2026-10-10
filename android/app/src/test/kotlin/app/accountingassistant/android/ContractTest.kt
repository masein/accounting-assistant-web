package app.accountingassistant.android

import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.parseBlock
import app.accountingassistant.android.ui.chat.stampOf
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The app against what the server really sends (roadmap ROADMAP_ANDROID_CHAT
 * P0.10): every block of the conversations recorded by
 * tests/test_mobile_contract.py, in docs/contracts/conversations, becomes the
 * item it should, with its fields. Unit tests run from android/app.
 */
class ContractTest {
    private val dir = File("../../docs/contracts/conversations")

    private fun recordings(): Map<String, List<JsonObject>> =
        dir.listFiles { f -> f.extension == "json" }!!.sortedBy { it.name }.associate { f ->
            val o = Json.parseToJsonElement(f.readText()) as JsonObject
            f.nameWithoutExtension to (o["blocks"] as JsonArray).map { it as JsonObject }
        }

    private fun JsonObject.str(k: String) = (this[k] as? JsonPrimitive)?.contentOrNull

    @Test fun everyRecordedConversationIsHere() {
        assertTrue("run tests/test_mobile_contract.py to record them", dir.isDirectory)
        assertEquals(setOf("approval-fa", "balance-en", "budgets-fa", "cash-fa", "draft-fa", "forecast-fa", "intake-en",
                           "invoice-pdf-en", "invoices-en", "posted-fa", "spending-fa", "statement-fa"), recordings().keys)
    }

    @Test fun everyBlockBecomesTheItemItShould() {
        val drawn = mutableSetOf<String>()
        for ((name, blocks) in recordings()) for (b in blocks) {
            val type = b.str("type")!!
            if (type == "posted") continue                     // the answer to Confirm, not a chat item: below
            val item = parseBlock(b)
            val expected = when (type) {
                "text" -> ChatItem.Words::class
                "figure" -> ChatItem.Figure::class
                "chart" -> ChatItem.Chart::class
                "table" -> ChatItem.Table::class
                "proposal", "approval" -> ChatItem.Proposal::class
                "file" -> ChatItem.File::class
                // a bank statement is drawn as its card; other intakes as their sentence, for now
                "intake" -> if (b.str("kind") == "bank_statement") ChatItem.Statement::class else ChatItem.Fallback::class
                else -> ChatItem.Fallback::class
            }
            assertEquals("$name: $type", expected, item::class)
            assertEquals("$name: $type keeps its id", b.str("id"), item.id)
            if (item is ChatItem.Fallback) assertTrue("$name: $type has a sentence to show", item.text.isNotBlank())
            drawn += type
        }
        assertEquals(setOf("text", "figure", "chart", "table", "proposal", "approval", "file", "intake"), drawn)
    }

    @Test fun theFieldsArriveWhereTheScreenReadsThem() {
        val r = recordings()
        val cash = parseBlock(r.getValue("cash-fa").first()) as ChatItem.Figure
        assertEquals(1_240_000_000L, cash.value)
        assertEquals("IRR", cash.currency)
        val draft = parseBlock(r.getValue("draft-fa").first()) as ChatItem.Proposal
        assertEquals(80_000_000L, draft.amount)
        assertEquals("۱۸ مهر ۱۴۰۵", draft.date)
        assertEquals("2026-10-10", draft.dateIso)
        assertEquals(listOf(80_000_000L to 0L, 0L to 80_000_000L), draft.lines.map { it.debit to it.credit })
        assertTrue("a transaction draft offers Edit", draft.editable)
        assertTrue(draft.amountEditable)
        val approval = parseBlock(r.getValue("approval-fa").first()) as ChatItem.Proposal
        assertEquals(ChatItem.Proposal.Approval("maryam", mine = false), approval.approval)
        assertFalse("an approval card offers no Edit", approval.editable)
        val invoices = parseBlock(r.getValue("invoices-en").first()) as ChatItem.Table
        assertEquals(listOf("Aria Co" to 60_000_000L, "Sepehr" to 30_000_000L), invoices.rows.map { it.label to it.value })
        val pdf = parseBlock(r.getValue("invoice-pdf-en").first()) as ChatItem.File
        assertTrue(pdf.path.startsWith("/api/mobile/v1/"))
        assertEquals("application/pdf", pdf.mime)
    }

    @Test fun aStatementBringsItsCountsAndGap() {
        val st = parseBlock(recordings().getValue("statement-fa").first()) as ChatItem.Statement
        assertEquals("ملت", st.bank)                                     // the bank as the reader writes it
        assertEquals(12, st.rows)
        assertEquals(8, st.counts["matched"])
        assertEquals(2, st.counts["unrecorded"])
        assertEquals(90_000L, st.gap)
        assertFalse(st.clean)
    }

    @Test fun theForecastStartsTodayAndKnowsItsLowWeek() {
        val chart = parseBlock(recordings().getValue("forecast-fa").first()) as ChatItem.Chart
        assertEquals(14, chart.points.size)                              // today, then thirteen weeks
        assertEquals(1_240_000_000L, chart.points.first().value)
        assertEquals("2027-01-04", chart.points.last().x)
        assertEquals(ChatItem.Chart.Point("2026-11-30", -120_000_000), chart.lowest)
        assertEquals("2026-11-30", chart.firstNegative)
    }

    @Test fun aPostedBlockStampsTheVoucher() {
        val stamp = stampOf(recordings().getValue("posted-fa").single())
        assertEquals("1042", stamp.voucher)
        assertEquals("۱۸ مهر ۱۴۰۵", stamp.date)
        assertEquals(120, stamp.undoSeconds)
        assertEquals("invoice-INV-1042.pdf", stamp.document?.name)
    }
}
