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
)

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
)

@Serializable
data class UploadReply(
    val id: String,
    @SerialName("file_name") val fileName: String = "",
    @SerialName("content_type") val contentType: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
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
