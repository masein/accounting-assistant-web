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

    data class User(override val id: String, val text: String, val files: List<String> = emptyList()) : ChatItem
    data class Words(override val id: String, val text: String) : ChatItem
    data class Proposal(
        override val id: String,
        val token: String,
        val title: String,
        val amount: Long?,
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
    ) : ChatItem {
        enum class Phase { Draft, Posting, Posted, Waiting, Cancelled, Undone }
    }
    data class Figure(override val id: String, val label: String, val value: Long, val currency: String?) : ChatItem
    data class Table(override val id: String, val kind: String, val rows: List<Row>) : ChatItem {
        data class Row(val label: String, val sub: String?, val value: Long, val currency: String?)
    }
    /** The turn failed: a known [code] is said in the user's language, else [detail]. */
    data class Problem(override val id: String, val code: String?, val detail: String) : ChatItem
    /** A block this version of the app can't draw: its sentence instead. */
    data class Fallback(override val id: String, val text: String) : ChatItem
    /** The turn under way; [text] is the step the server says it is on. */
    data class Thinking(override val id: String = "thinking", val text: String? = null) : ChatItem
    /** A first-run hint: questions the books can answer at once. */
    data class Suggestions(override val id: String = "suggestions", val options: List<String>) : ChatItem
}

private fun JsonObject.str(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
private fun JsonObject.long(key: String): Long? = (this[key] as? JsonPrimitive)?.longOrNull
private fun JsonObject.bool(key: String): Boolean = (this[key] as? JsonPrimitive)?.booleanOrNull ?: false
private fun JsonObject.obj(key: String): JsonObject? = this[key] as? JsonObject
private fun JsonElement?.array(): List<JsonObject> = (this as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

/** The server's blocks (app/services/ai_accountant/blocks.py) as chat items. */
fun parseBlock(b: JsonObject): ChatItem {
    val id = b.str("id") ?: b.hashCode().toString()
    return when (b.str("type")) {
        "text" -> ChatItem.Words(id, b.str("text").orEmpty())
        "proposal" -> ChatItem.Proposal(
            id = id,
            token = b.str("token").orEmpty(),
            title = b.str("title").orEmpty(),
            amount = b.obj("amount")?.long("value"),
            currency = b.obj("amount")?.str("currency"),
            date = b.obj("date")?.str("display"),
            lines = b["lines"].array().map {
                VoucherLine(it.str("account").orEmpty(), it.str("name").orEmpty(), it.long("debit") ?: 0, it.long("credit") ?: 0)
            },
            needsApproval = b.bool("needs_approval"),
            phase = when (b.str("state")) {
                "executed" -> ChatItem.Proposal.Phase.Posted
                "cancelled", "expired" -> ChatItem.Proposal.Phase.Cancelled
                else -> ChatItem.Proposal.Phase.Draft
            },
        )
        "figure" -> ChatItem.Figure(id, b.str("label").orEmpty(), b.long("value") ?: 0, b.str("currency"))
        "table" -> ChatItem.Table(id, b.str("kind").orEmpty(), b["rows"].array().map {
            ChatItem.Table.Row(it.str("label").orEmpty(), it.str("sub"), it.long("value") ?: 0, it.str("currency"))
        })
        else -> ChatItem.Fallback(id, b.str("fallback_text").orEmpty())
    }
}
