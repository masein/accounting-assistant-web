package app.accountingassistant.android.ui.chat

import app.accountingassistant.android.ui.components.VoucherLine
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

/** One thing in the conversation, as the screen draws it. */
sealed interface ChatItem {
    val id: String

    /** What the user sent; [clientId] is the phone's id for it (its outbox entry while it waits). */
    data class User(override val id: String, val text: String, val files: List<String> = emptyList(),
                    val clientId: String? = null) : ChatItem
    /** The accountant's words; [nextStatement] offers the statement's next difference under them. */
    data class Words(override val id: String, val text: String, val nextStatement: String? = null,
                     /** A short question's answers to tap («keep it in Omani rials»): sent as if typed. */
                     val choices: List<String> = emptyList()) : ChatItem
    /** A chart from the books, drawn to scale: the cash forecast, week by week from today. */
    data class Chart(
        override val id: String,
        val kind: String,
        val title: String,
        val currency: String?,
        val points: List<Point>,
        val lowest: Point?,
        val firstNegative: String?,
    ) : ChatItem {
        /** [x] is an ISO date; the first point is today's balance when the server sends it. */
        data class Point(val x: String?, val value: Long)
    }
    /** A bank statement read and checked against the books: what matched, what differs, the balance. */
    data class Statement(
        override val id: String,
        val statementId: String,
        val bank: String?,
        val from: String?,
        val to: String?,
        val rows: Int,
        val counts: Map<String, Int>,
        val gap: java.math.BigDecimal?,
        val currency: String?,
        val clean: Boolean,
    ) : ChatItem
    data class Proposal(
        override val id: String,
        val token: String,
        val title: String,
        val amount: java.math.BigDecimal?,
        val currency: String?,
        val date: String?,
        val lines: List<VoucherLine>,
        val needsApproval: Boolean,
        val phase: Phase = Phase.Draft,
        val voucher: String? = null,
        val postedDate: String? = null,
        val auditLogId: String? = null,
        val undoUntil: Long = 0L,
        val error: String? = null,
        /** The document the posting made (an invoice's PDF). */
        val document: File? = null,
        /** Someone else's voucher waiting for this person's decision (or their own, to withdraw). */
        val approval: Approval? = null,
        /** The entry date as the server keeps it (Gregorian ISO), for Edit. */
        val dateIso: String? = null,
        /** The server offers Edit for this card (its tool can be changed from the phone). */
        val editable: Boolean = false,
    ) : ChatItem {
        enum class Phase { Draft, Posting, Posted, Waiting, Cancelled, Undone, Rejected, Replaced }
        data class Approval(val askedBy: String, val mine: Boolean)
        /** Edit can change the amount only when one line is debited and one credited. */
        val amountEditable: Boolean get() = lines.count { it.debit.signum() > 0 } == 1 && lines.count { it.credit.signum() > 0 } == 1
    }
    data class Figure(override val id: String, val label: String, val value: java.math.BigDecimal, val currency: String?) : ChatItem
    /** A document from the books: open it, or share it to another app. */
    data class File(override val id: String, val name: String, val path: String, val mime: String) : ChatItem
    data class Table(override val id: String, val kind: String, val rows: List<Row>, val title: String? = null) : ChatItem {
        /** [gain]: what today's rate adds to a holding (gold, currency). */
        data class Row(val label: String, val sub: String?, val value: java.math.BigDecimal, val currency: String?,
                       val gain: java.math.BigDecimal? = null)
    }
    /** The turn failed: a known [code] is said in the user's language, else [detail]. */
    data class Problem(override val id: String, val code: String?, val detail: String) : ChatItem
    /** A block this version of the app can't draw: its sentence instead. */
    data class Fallback(override val id: String, val text: String) : ChatItem
    /** The turn under way; [text] is the step the server says it is on, [upload] how much of its file has gone. */
    data class Thinking(override val id: String = "thinking", val text: String? = null, val upload: Float? = null) : ChatItem
    /** Older messages wait on the server: tap to bring the page before. */
    data class Earlier(override val id: String = "earlier") : ChatItem
    /** A first-run hint: questions the books can answer at once. */
    data class Suggestions(override val id: String = "suggestions", val options: List<String>) : ChatItem
}

private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
private fun JsonObject.long(key: String): Long? = (this[key] as? JsonPrimitive)?.longOrNull
/** An amount, exactly as sent: 950, 7.5 (pence are kept, docs/PLAN_PENCE.md). */
private fun JsonObject.money(key: String): java.math.BigDecimal? =
    (this[key] as? JsonPrimitive)?.takeIf { !it.isString }?.contentOrNull?.toBigDecimalOrNull()
/** A chart's point, whole: its axes and headline stay compact. */
private fun JsonObject.whole(key: String): Long? =
    long(key) ?: money(key)?.setScale(0, java.math.RoundingMode.HALF_UP)?.toLong()
private fun JsonObject.bool(key: String): Boolean = (this[key] as? JsonPrimitive)?.booleanOrNull ?: false
private fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject
private fun JsonElement?.array(): List<JsonObject> = (this as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

/**
 * A reply's blocks as chat items. A fallback that says nothing, or repeats
 * the reply's own words, is left out: the card of a statement imported
 * before used to bring the whole reply a second time.
 */
fun parseBlocks(blocks: List<JsonObject>): List<ChatItem> {
    val items = blocks.map(::parseBlock)
    val said = items.filterIsInstance<ChatItem.Words>().map { it.text.trim() }.toSet()
    return items.filterNot { it is ChatItem.Fallback && (it.text.isBlank() || it.text.trim() in said) }
}

/** The server's blocks (app/services/ai_accountant/blocks.py) as chat items. */
fun parseBlock(b: JsonObject): ChatItem {
    val id = b.str("id") ?: b.hashCode().toString()
    return when (b.str("type")) {
        "text" -> ChatItem.Words(id, b.str("text").orEmpty(),
                                 nextStatement = b.str("statement_id").takeIf { b.str("kind") == "statement_next" },
                                 choices = (b["choices"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
                                     ?.filter { it.isNotBlank() }?.take(4).orEmpty())
        // imported now, or before (then the card is the earlier import, as it stands)
        "intake" -> if (b.str("kind") == "bank_statement" && b.str("statement_id") != null &&
                        (b.str("status") == "imported" || (b.str("status") == "duplicate" && b.long("total_rows") != null)))
            ChatItem.Statement(
                id = id, statementId = b.str("statement_id")!!,
                bank = b.str("bank_label") ?: b.str("bank_name")?.takeIf { it != "Unknown" },     // «ملت» in Persian
                from = b.str("from_date"), to = b.str("to_date"), rows = b.long("total_rows")?.toInt() ?: 0,
                counts = b.obj("counts")?.mapNotNull { (k, v) -> (v as? JsonPrimitive)?.longOrNull?.let { k to it.toInt() } }?.toMap().orEmpty(),
                gap = b.obj("balance")?.money("gap")?.takeIf { it.signum() != 0 }, currency = b.str("currency"), clean = b.bool("clean"),
            )
        else ChatItem.Fallback(id, b.str("fallback_text").orEmpty())
        "proposal", "approval" -> ChatItem.Proposal(
            id = id,
            token = b.str("token").orEmpty(),
            title = b.str("title").orEmpty(),
            amount = b.obj("amount")?.money("value"),
            currency = b.obj("amount")?.str("currency"),
            date = b.obj("date")?.str("display"),
            lines = b["lines"].array().map {
                VoucherLine(it.str("account").orEmpty(), it.str("name").orEmpty(),
                            it.money("debit") ?: java.math.BigDecimal.ZERO, it.money("credit") ?: java.math.BigDecimal.ZERO)
            },
            needsApproval = b.bool("needs_approval"),
            phase = when (b.str("state")) {
                // the history's stamp says whether it was undone since (the state stays "executed")
                "executed" -> if (b.obj("posted")?.bool("undone") == true) ChatItem.Proposal.Phase.Undone
                              else ChatItem.Proposal.Phase.Posted
                "cancelled" -> if (b.str("approval_status") == "rejected") ChatItem.Proposal.Phase.Rejected
                               else ChatItem.Proposal.Phase.Cancelled
                "expired" -> ChatItem.Proposal.Phase.Cancelled
                "replaced" -> ChatItem.Proposal.Phase.Replaced
                // the asker's own card, sent for approval: it waits
                else -> if (b.str("type") == "proposal" && b.str("approval_status") == "requested") ChatItem.Proposal.Phase.Waiting
                        else ChatItem.Proposal.Phase.Draft
            },
            approval = if (b.str("type") == "approval")
                ChatItem.Proposal.Approval(b.str("requested_by").orEmpty(), b.bool("mine")) else null,
            dateIso = b.obj("date")?.str("iso"),
            editable = (b["actions"] as? JsonArray)?.any { (it as? JsonPrimitive)?.contentOrNull == "edit" } == true,
            // a posted card opened again keeps its seal's number and date
            voucher = b.obj("posted")?.str("voucher"),
            postedDate = b.obj("posted")?.obj("date")?.str("display"),
        )
        "chart" -> {
            fun point(o: JsonObject?) = o?.whole("value")?.let { ChatItem.Chart.Point(o.str("x"), it) }
            val points = listOfNotNull(point(b.obj("start"))) + b["points"].array().mapNotNull { point(it) }
            if (points.size < 2) ChatItem.Fallback(id, b.str("fallback_text").orEmpty())
            else ChatItem.Chart(id, b.str("kind").orEmpty(), b.str("title").orEmpty(), b.str("currency"), points,
                                point(b.obj("lowest")), b.str("first_negative"))
        }
        "figure" -> ChatItem.Figure(id, b.str("label").orEmpty(), b.money("value") ?: java.math.BigDecimal.ZERO, b.str("currency"))
        "file" -> fileOf(b) ?: ChatItem.Fallback(id, b.str("fallback_text").orEmpty())
        "table" -> ChatItem.Table(id, b.str("kind").orEmpty(), b["rows"].array().map {
            ChatItem.Table.Row(it.str("label").orEmpty(), it.str("sub"), it.money("value") ?: java.math.BigDecimal.ZERO, it.str("currency"),
                               gain = it.money("gain")?.takeIf { g -> g.signum() != 0 })
        }, title = b.str("title"))
        else -> ChatItem.Fallback(id, b.str("fallback_text").orEmpty())
    }
}

/** A file block (or a posted block's "file"), when it names a phone document. */
fun fileOf(b: JsonObject?): ChatItem.File? {
    b ?: return null
    val path = b.str("path") ?: return null
    return ChatItem.File(b.str("id") ?: path, b.str("name") ?: "document", path, b.str("mime") ?: "application/octet-stream")
}

/** A posted block (the answer to Confirm or Approve): the stamp's number and date, the undo window, the document. */
data class Stamp(val voucher: String?, val date: String?, val auditLogId: String?, val undoSeconds: Int, val document: ChatItem.File?)

fun stampOf(b: JsonObject?): Stamp = Stamp(
    voucher = b?.str("voucher"),
    date = b?.obj("date")?.str("display"),
    auditLogId = b?.str("audit_log_id"),
    undoSeconds = (b?.get("undo_seconds") as? JsonPrimitive)?.longOrNull?.toInt() ?: 0,
    document = fileOf(b?.obj("file")),
)
