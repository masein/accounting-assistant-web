package app.accountingassistant.android.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.serializer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * The app's one door to the server: /api/mobile/v1 with the phone's bearer
 * token, the app version (an app too old gets 426) and the user's language
 * (error messages come back in it). An expired access token is renewed with
 * the refresh token once, transparently; when the session is over the store
 * is cleared and [signedOut] fires.
 */
class ApiClient(
    private val base: String,
    private val store: SessionStore,
    private val appVersion: String,
    private val language: () -> String,
    val json: Json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true },
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)          // a chat turn runs every tool before it answers
        .build(),
) {
    private val _signedOut = MutableSharedFlow<ApiError>(extraBufferCapacity = 1)
    val signedOut: SharedFlow<ApiError> = _signedOut

    @Volatile var session: StoredSession? = null
        private set
    private val refreshLock = Mutex()

    suspend fun restore(): StoredSession? = store.load().also { session = it }

    // --- sign-in ---------------------------------------------------------------------------

    suspend fun login(username: String, password: String, deviceName: String): SessionResponse =
        signIn(call("POST", "/auth/login", LoginRequest(username, password, deviceName, appVersion = appVersion),
                    serializer(), auth = false))

    suspend fun twoFactor(challenge: String, code: String, deviceName: String): SessionResponse =
        signIn(call("POST", "/auth/2fa", TwoFactorRequest(challenge, code, deviceName, appVersion = appVersion),
                    serializer(), auth = false))

    private suspend fun signIn(r: SessionResponse): SessionResponse {
        if (r.ok && r.accessToken != null && r.refreshToken != null && r.deviceId != null && r.user != null) {
            val s = StoredSession(r.accessToken, r.refreshToken, r.deviceId, r.user, r.company)
            store.save(s)
            session = s
        }
        return r
    }

    /** The phones signed in to this account, this one marked. */
    suspend fun devices(): List<DeviceDto> = call<Unit, List<DeviceDto>>("GET", "/devices", null, serializer())

    /** Sign another phone out: a lost one, an old one. */
    suspend fun revokeDevice(id: String) { call<Unit, JsonObject>("DELETE", "/devices/$id", null, serializer()) }

    suspend fun signOut() {
        runCatching { call<Unit, JsonObject>("DELETE", "/session", null, serializer()) }
        store.clear()
        session = null
    }

    // --- the chat ----------------------------------------------------------------------------

    suspend fun chat(message: String, threadId: String?, attachmentIds: List<String> = emptyList(),
                     clientMessageId: String? = null): ChatReply =
        call("POST", "/chat", ChatRequest(message, threadId, attachmentIds, clientMessageId), serializer())

    /**
     * The same turn, streamed: [onStatus] hears each step as it happens
     * («در حال بررسی دفاتر…»), then the reply comes back. A dropped
     * connection is a [NetworkError]; retry with the same [clientMessageId].
     */
    suspend fun chatStream(message: String, threadId: String?, attachmentIds: List<String>, clientMessageId: String,
                           onStatus: (String) -> Unit): ChatReply = withContext(Dispatchers.IO) {
        val payload = json.encodeToString(ChatRequest.serializer(), ChatRequest(message, threadId, attachmentIds, clientMessageId))
        var renewed = false
        while (true) {
            val token = session?.accessToken
            val req = Request.Builder()
                .url(base.trimEnd('/') + PREFIX + "/chat/stream")
                .header("X-App-Version", appVersion).header("X-UI-Language", language())
                .header("Accept", "text/event-stream")
                .apply { if (token != null) header("Authorization", "Bearer $token") }
                .post(payload.toRequestBody(JSON_TYPE))
                .build()
            val response = try { http.newCall(req).execute() } catch (e: IOException) { throw NetworkError(e) }
            try {
                if (response.code == 401 && !renewed && session != null) {
                    renewed = true
                    val refusal = refresh(token)
                    if (refusal != null) { endSession(refusal); throw refusal }
                    continue
                }
                if (!response.isSuccessful) {
                    throw Answer(response.code, response.body.string(), response.header("X-Error-Code"), token).error()
                }
                return@withContext readStream(response.body.source(), onStatus)
            } finally {
                response.close()
            }
        }
        @Suppress("UNREACHABLE_CODE") error("unreachable")
    }

    private fun readStream(source: okio.BufferedSource, onStatus: (String) -> Unit): ChatReply {
        var event: String? = null
        var reply: ChatReply? = null
        while (true) {
            val line = try { source.readUtf8Line() } catch (e: IOException) { throw NetworkError(e) } ?: break
            when {
                line.startsWith("event: ") -> event = line.removePrefix("event: ")
                line.startsWith("data: ") -> {
                    val data = line.removePrefix("data: ")
                    when (event) {
                        "status" -> runCatching { (json.parseToJsonElement(data) as JsonObject)["text"]?.jsonPrimitive?.contentOrNull }
                            .getOrNull()?.let(onStatus)
                        "reply" -> reply = json.decodeFromString(ChatReply.serializer(), data)
                        "error" -> {
                            val o = json.parseToJsonElement(data) as JsonObject
                            throw ApiError(o["status"]?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 500,
                                           o["code"]?.jsonPrimitive?.contentOrNull,
                                           o["detail"]?.jsonPrimitive?.contentOrNull ?: "")
                        }
                    }
                }
            }
        }
        return reply ?: throw NetworkError(IOException("the stream ended without a reply"))
    }

    /** A document from the books (a file block's path, e.g. an invoice's PDF). */
    suspend fun download(path: String): ByteArray = withContext(Dispatchers.IO) {
        require(path.startsWith(PREFIX + "/")) { "not a phone document: $path" }
        var renewed = false
        while (true) {
            val token = session?.accessToken
            val req = Request.Builder().url(base.trimEnd('/') + path)
                .header("X-App-Version", appVersion).header("X-UI-Language", language())
                .apply { if (token != null) header("Authorization", "Bearer $token") }
                .get().build()
            val response = try { http.newCall(req).execute() } catch (e: IOException) { throw NetworkError(e) }
            response.use { r ->
                if (r.code == 401 && !renewed && session != null) {
                    renewed = true
                    refresh(token)?.let { endSession(it); throw it }
                } else if (!r.isSuccessful) {
                    throw Answer(r.code, r.body.string(), r.header("X-Error-Code"), token).error()
                } else {
                    return@withContext r.body.bytes()
                }
            }
        }
        @Suppress("UNREACHABLE_CODE") error("unreachable")
    }

    /** The phone's language becomes the account's, so replies come in it. */
    suspend fun setLanguage(lang: String) {
        call<LanguageRequest, JsonObject>("PUT", "/me/language", LanguageRequest(lang), serializer())
    }

    /** The conversations, most recent first; with [since], only those changed after it. */
    suspend fun threads(since: String? = null): List<ThreadDto> =
        call<Unit, List<ThreadDto>>("GET", "/threads" + query("since" to since), null, serializer())

    suspend fun messages(threadId: String): List<ThreadMessageDto> = messagesPage(threadId).messages

    /**
     * A page of a conversation, oldest first: the latest [limit]; the page
     * [before] a message id; or what came [after] one (catching up).
     */
    suspend fun messagesPage(threadId: String, before: String? = null, after: String? = null, limit: Int? = null): MessagesPage {
        val a = exchange("GET", "/threads/$threadId/messages" +
                         query("before" to before, "after" to after, "limit" to limit?.toString()), { null })
        return MessagesPage(json.decodeFromString(ListSerializer(ThreadMessageDto.serializer()), a.body.ifBlank { "[]" }),
                            moreBefore = a.moreBefore)
    }

    private fun query(vararg pairs: Pair<String, String?>): String =
        pairs.filter { it.second != null }.joinToString("&") { (k, v) -> k + "=" + URLEncoder.encode(v, "UTF-8") }
            .let { if (it.isEmpty()) "" else "?$it" }

    /** A photo, a PDF, a statement: its id then goes with the chat message. */
    suspend fun upload(bytes: ByteArray, fileName: String, contentType: String): UploadReply =
        callBody("POST", "/uploads", { multipart(bytes, fileName, contentType) }, serializer())

    /** A voice note's words, for the user to check before sending. */
    suspend fun transcribe(bytes: ByteArray, fileName: String, contentType: String): TranscribeReply =
        callBody("POST", "/transcribe", { multipart(bytes, fileName, contentType) }, serializer())

    /** What needs attention today, said first when the app opens. */
    suspend fun briefing(threadId: String?): BriefingReply =
        call("POST", "/briefing", BriefingRequest(threadId), serializer())

    private fun multipart(bytes: ByteArray, fileName: String, contentType: String): RequestBody =
        MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", fileName, bytes.toRequestBody(contentType.toMediaType()))
            .build()

    suspend fun confirm(token: String): ConfirmReply = call<Unit, ConfirmReply>("POST", "/proposals/$token/confirm", null, serializer())

    suspend fun cancel(token: String): StateReply = call<Unit, StateReply>("POST", "/proposals/$token/cancel", null, serializer())

    suspend fun undo(auditLogId: String): StateReply = call<Unit, StateReply>("POST", "/postings/$auditLogId/undo", null, serializer())

    /** Change a draft: the date, the description, or (one debit, one credit) the amount. */
    suspend fun editProposal(token: String, change: EditRequest): EditReply =
        call("POST", "/proposals/$token/edit", change, serializer())

    /** Someone else's voucher, posted by this person's approval: the stamped receipt. */
    suspend fun approve(token: String): ConfirmReply = call<Unit, ConfirmReply>("POST", "/approvals/$token/approve", null, serializer())

    suspend fun reject(token: String, note: String?): StateReply =
        call("POST", "/approvals/$token/reject", RejectRequest(note?.takeIf { it.isNotBlank() }), serializer())

    // --- plumbing ------------------------------------------------------------------------------

    private suspend inline fun <reified B, R> call(
        method: String, path: String, body: B?, out: KSerializer<R>, auth: Boolean = true,
    ): R = callBody(method, path, { body?.let { json.encodeToString(serializer<B>(), it).toRequestBody(JSON_TYPE) } }, out, auth)

    private suspend fun <R> callBody(
        method: String, path: String, body: () -> RequestBody?, out: KSerializer<R>, auth: Boolean = true,
    ): R = json.decodeFromString(out, exchange(method, path, body, auth).body.ifBlank { "{}" })

    /** One call, renewing an expired token once; a refusal is thrown as [ApiError]. */
    private suspend fun exchange(method: String, path: String, body: () -> RequestBody?, auth: Boolean = true): Answer {
        val first = send(method, path, body(), auth)
        val result = if (first.status == 401 && auth && session != null) {
            val refusal = refresh(first.usedToken)
            if (refusal == null) send(method, path, body(), auth)
            else throw refusal.also { endSession(it) }
        } else first
        if (result.status !in 200..299) {
            val err = result.error()
            if (auth && err.sessionOver) endSession(err)
            throw err
        }
        return result
    }

    private class Answer(val status: Int, val body: String, val code: String?, val usedToken: String?,
                         val moreBefore: Boolean = false) {
        fun error(): ApiError {
            val parsed = runCatching { Json.parseToJsonElement(body) as? JsonObject }.getOrNull()
            val detail = parsed?.get("detail")?.let { runCatching { it.jsonPrimitive.contentOrNull }.getOrNull() }
            val bodyCode = parsed?.get("code")?.let { runCatching { it.jsonPrimitive.contentOrNull }.getOrNull() }
            return ApiError(status, bodyCode ?: code, detail ?: "HTTP $status")
        }
    }

    private suspend fun send(method: String, path: String, body: RequestBody?, auth: Boolean): Answer = withContext(Dispatchers.IO) {
        val token = if (auth) session?.accessToken else null
        val req = Request.Builder()
            .url(base.trimEnd('/') + PREFIX + path)
            .header("X-App-Version", appVersion)
            .header("X-UI-Language", language())
            .header("Accept", "application/json")
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .method(method, body ?: if (method == "POST") "".toRequestBody(JSON_TYPE) else null)
            .build()
        try {
            http.newCall(req).execute().use { r ->
                Answer(r.code, r.body.string(), r.header("X-Error-Code"), token, r.header("X-More-Before") == "true")
            }
        } catch (e: IOException) {
            throw NetworkError(e)
        }
    }

    /**
     * Trade the refresh token for a new pair. Null when the call can be
     * retried; otherwise why the session is over (the server's own reason).
     */
    private suspend fun refresh(staleToken: String?): ApiError? = refreshLock.withLock {
        val ended = ApiError(401, "session_ended", "Your session on this phone has ended. Sign in again.")
        val current = session ?: return ended
        if (current.accessToken != staleToken) return null            // another call already refreshed
        val answer = send("POST", "/auth/refresh",
                          json.encodeToString(RefreshRequest.serializer(), RefreshRequest(current.refreshToken, appVersion))
                              .toRequestBody(JSON_TYPE),
                          auth = false)
        if (answer.status != 200) return answer.error()
        val r = json.decodeFromString(SessionResponse.serializer(), answer.body)
        if (r.accessToken == null || r.refreshToken == null) return ended
        val renewed = current.copy(accessToken = r.accessToken, refreshToken = r.refreshToken,
                                   user = r.user ?: current.user, company = r.company ?: current.company)
        store.save(renewed)
        session = renewed
        null
    }

    private suspend fun endSession(err: ApiError) {
        store.clear()
        session = null
        _signedOut.tryEmit(err)
    }

    companion object {
        const val PREFIX = "/api/mobile/v1"
        private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}
