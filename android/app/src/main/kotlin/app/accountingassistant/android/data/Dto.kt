package app.accountingassistant.android.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/** The wire shapes of /api/mobile/v1 (app/api/mobile.py, app/api/mobile_chat.py). */

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerialName("app_version") val appVersion: String,
)

@Serializable
data class TwoFactorRequest(
    val challenge: String,
    val code: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerialName("app_version") val appVersion: String,
)

@Serializable
data class RefreshRequest(
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("app_version") val appVersion: String,
)

@Serializable
data class UserDto(
    val id: String,
    val username: String,
    val role: String = "owner",
    @SerialName("preferred_language") val language: String = "en",
)

@Serializable
data class CompanyDto(
    val id: String,
    val name: String,
    val locale: String? = null,
    @SerialName("base_currency") val baseCurrency: String? = null,
    val kind: String? = null,
    /** "jalali" or "gregorian": the calendar the books write dates in, the same as the replies. */
    val calendar: String? = null,
)

@Serializable
data class SessionResponse(
    val ok: Boolean = false,
    @SerialName("two_factor_required") val twoFactorRequired: Boolean = false,
    val challenge: String? = null,
    @SerialName("access_token") val accessToken: String? = null,
    @SerialName("expires_in") val expiresIn: Int? = null,
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("device_id") val deviceId: String? = null,
    val user: UserDto? = null,
    val company: CompanyDto? = null,
)

@Serializable
data class ChatRequest(
    val message: String,
    @SerialName("thread_id") val threadId: String? = null,
    @SerialName("attachment_ids") val attachmentIds: List<String> = emptyList(),
    /** The phone's own id for the message: a retry gets the reply it already had. */
    @SerialName("client_message_id") val clientMessageId: String? = null,
)

@Serializable
data class LanguageRequest(val language: String)

@Serializable
data class ChatReply(
    @SerialName("thread_id") val threadId: String,
    val blocks: List<JsonObject> = emptyList(),
    @SerialName("stop_reason") val stopReason: String? = null,
)

@Serializable
data class ConfirmReply(
    val state: String,
    val block: JsonObject? = null,
    val approval: JsonObject? = null,
)

@Serializable
data class StateReply(val state: String, val mode: String? = null)

/** Only the fields that change are sent. */
@Serializable
data class EditRequest(val date: String? = null, val description: String? = null,
                       /** The new amount as written, «4.25» (the server reads it exactly, pence kept). */
                       val amount: String? = null)

/** The new draft, and the token it replaces. */
@Serializable
data class EditReply(val replaces: String, val block: JsonObject)

@Serializable
data class RejectRequest(val note: String? = null)

/** What the phone keeps between launches. */
@Serializable
data class StoredSession(
    val accessToken: String,
    val refreshToken: String,
    val deviceId: String,
    val user: UserDto,
    val company: CompanyDto? = null,
)

@Serializable
data class ThreadDto(
    val id: String,
    val title: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("message_count") val messageCount: Int = 0,
)

@Serializable
data class ThreadMessageDto(
    val id: String,
    val role: String,
    val text: String? = null,
    val blocks: List<JsonObject> = emptyList(),
    /** The phone's id for a message it sent: an outbox entry already on the server. */
    @SerialName("client_message_id") val clientMessageId: String? = null,
    /** The files a message of the user's carried. */
    val files: List<SentFileDto> = emptyList(),
)

@Serializable
data class SentFileDto(val name: String = "", @SerialName("content_type") val contentType: String? = null)

/** A page of a conversation, oldest first; [moreBefore] when older ones remain. */
data class MessagesPage(val messages: List<ThreadMessageDto>, val moreBefore: Boolean)

@Serializable
data class UploadReply(
    val id: String,
    @SerialName("file_name") val fileName: String = "",
    @SerialName("content_type") val contentType: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
)

@Serializable
data class UploadBegin(
    @SerialName("file_name") val fileName: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    val sha256: String,
)

/** How far an upload in pieces has got; [attachment] once it is whole. */
@Serializable
data class UploadState(
    val id: String,
    val received: Long = 0,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    /** The largest piece the server takes. */
    @SerialName("max_chunk") val maxChunk: Int = 1024 * 1024,
    val done: Boolean = false,
    val attachment: UploadReply? = null,
)

@Serializable
data class TranscribeReply(val text: String = "")

@Serializable
data class BriefingReply(
    @SerialName("thread_id") val threadId: String? = null,
    val blocks: List<JsonObject> = emptyList(),
)

@Serializable
data class BriefingRequest(@SerialName("thread_id") val threadId: String? = null)

@Serializable
data class DeviceDto(
    val id: String,
    val name: String,
    val platform: String = "android",
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("this_device") val thisDevice: Boolean = false,
)

@Serializable
data class CrashBatch(val reports: List<app.accountingassistant.android.util.CrashReport>)

/** The widget's two numbers (GET /summary). */
@Serializable
data class SummaryReply(
    @SerialName("as_of") val asOf: String? = null,
    val cash: SummaryCash,
    val budget: SummaryBudget? = null,
)

@Serializable
data class SummaryCash(val total: Double, val currency: String? = null)       // 23915.5: pence kept

@Serializable
data class SummaryBudget(val left: Double, val total: Double, val currency: String? = null)

/** What deserves a notification now (GET /feed); [key] is stable, so each is told once. */
@Serializable
data class FeedItem(val key: String, val kind: String = "", val title: String, val body: String = "")

@Serializable
data class FeedReply(val items: List<FeedItem> = emptyList())

/** Bank SMS the phone read (roadmap ROADMAP_ANDROID_CHAT §7.1): the text, its sender, when it arrived. */
@Serializable
data class PhoneSms(
    val sender: String,
    val text: String,
    @SerialName("received_at") val receivedAt: String,
    val bank: String? = null,
)

@Serializable
data class PhoneSmsBatch(val messages: List<PhoneSms>)

/** A person's books: the currency they count in, and the ones to choose from (scenario Z3). */
@Serializable
data class CurrencyOption(val code: String, val name: String)

@Serializable
data class BooksCurrency(val currency: String, val name: String = "", val options: List<CurrencyOption> = emptyList(),
                         val changed: Boolean = false)

@Serializable
data class CurrencyRequest(val currency: String)

@Serializable
data class BankSmsReply(val received: Int = 0, val added: Int = 0, val duplicates: Int = 0, val unread: Int = 0)
