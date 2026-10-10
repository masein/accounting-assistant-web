package app.accountingassistant.android.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.serializer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
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

    suspend fun signOut() {
        runCatching { call<Unit, JsonObject>("DELETE", "/session", null, serializer()) }
        store.clear()
        session = null
    }

    // --- the chat ----------------------------------------------------------------------------

    suspend fun chat(message: String, threadId: String?, attachmentIds: List<String> = emptyList()): ChatReply =
        call("POST", "/chat", ChatRequest(message, threadId, attachmentIds), serializer())

    suspend fun confirm(token: String): ConfirmReply = call<Unit, ConfirmReply>("POST", "/proposals/$token/confirm", null, serializer())

    suspend fun cancel(token: String): StateReply = call<Unit, StateReply>("POST", "/proposals/$token/cancel", null, serializer())

    suspend fun undo(auditLogId: String): StateReply = call<Unit, StateReply>("POST", "/postings/$auditLogId/undo", null, serializer())

    // --- plumbing ------------------------------------------------------------------------------

    private suspend inline fun <reified B, R> call(
        method: String, path: String, body: B?, out: KSerializer<R>, auth: Boolean = true,
    ): R {
        val first = send(method, path, body?.let { json.encodeToString(serializer<B>(), it) }, auth)
        val result = if (first.status == 401 && auth && session != null) {
            val refusal = refresh(first.usedToken)
            if (refusal == null) send(method, path, body?.let { json.encodeToString(serializer<B>(), it) }, auth)
            else throw refusal.also { endSession(it) }
        } else first
        if (result.status !in 200..299) {
            val err = result.error()
            if (auth && err.sessionOver) endSession(err)
            throw err
        }
        return json.decodeFromString(out, result.body.ifBlank { "{}" })
    }

    private class Answer(val status: Int, val body: String, val code: String?, val usedToken: String?) {
        fun error(): ApiError {
            val parsed = runCatching { Json.parseToJsonElement(body) as? JsonObject }.getOrNull()
            val detail = parsed?.get("detail")?.let { runCatching { it.jsonPrimitive.contentOrNull }.getOrNull() }
            val bodyCode = parsed?.get("code")?.let { runCatching { it.jsonPrimitive.contentOrNull }.getOrNull() }
            return ApiError(status, bodyCode ?: code, detail ?: "HTTP $status")
        }
    }

    private suspend fun send(method: String, path: String, body: String?, auth: Boolean): Answer = withContext(Dispatchers.IO) {
        val token = if (auth) session?.accessToken else null
        val req = Request.Builder()
            .url(base.trimEnd('/') + PREFIX + path)
            .header("X-App-Version", appVersion)
            .header("X-UI-Language", language())
            .header("Accept", "application/json")
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .method(method, body?.toRequestBody(JSON_TYPE) ?: if (method == "POST") "".toRequestBody(JSON_TYPE) else null)
            .build()
        try {
            http.newCall(req).execute().use { r ->
                Answer(r.code, r.body.string(), r.header("X-Error-Code"), token)
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
                          json.encodeToString(RefreshRequest.serializer(), RefreshRequest(current.refreshToken, appVersion)),
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
