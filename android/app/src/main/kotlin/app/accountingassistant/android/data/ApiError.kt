package app.accountingassistant.android.data

/**
 * A refusal from the server, with its machine-readable code when it sent one
 * (`session_expired`, `session_ended`, `upgrade_required`,
 * `password_change_required` …) and its message in the user's language.
 * [minVersion]: with a 426, the oldest app version the server still talks to.
 */
class ApiError(val status: Int, val code: String?, override val message: String,
               val minVersion: String? = null) : Exception(message) {
    val sessionOver: Boolean get() = status == 401 && code in setOf("session_expired", "session_ended", null)
    val updateNeeded: Boolean get() = status == 426
}

/** No answer at all: offline, DNS, a dropped connection. */
class NetworkError(cause: Throwable) : Exception(cause.message, cause)

/**
 * The server won't talk to this version of the app any more (426,
 * `upgrade_required`): the app asks for an update (scenario CX2).
 * [minVersion] is the version it needs, when the server says.
 */
data class UpdateRequired(val minVersion: String?)
