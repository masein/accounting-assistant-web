package app.accountingassistant.android.data

/**
 * A refusal from the server, with its machine-readable code when it sent one
 * (`session_expired`, `session_ended`, `upgrade_required`,
 * `password_change_required` …) and its message in the user's language.
 */
class ApiError(val status: Int, val code: String?, override val message: String) : Exception(message) {
    val sessionOver: Boolean get() = status == 401 && code in setOf("session_expired", "session_ended", null)
    val updateNeeded: Boolean get() = status == 426
}

/** No answer at all: offline, DNS, a dropped connection. */
class NetworkError(cause: Throwable) : Exception(cause.message, cause)
