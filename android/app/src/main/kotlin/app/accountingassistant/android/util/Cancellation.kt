package app.accountingassistant.android.util

import kotlin.coroutines.cancellation.CancellationException

/**
 * [runCatching] for suspending work (scenario CX7): a failed call is kept as
 * a failure, but a cancelled coroutine (the screen gone, the view model
 * cleared, a timeout) stays cancelled. Its [CancellationException] is thrown
 * on, never taken for a call that failed and handled as one.
 */
inline fun <T> runSuspendCatching(block: () -> T): Result<T> =
    try {
        Result.success(block())
    } catch (e: CancellationException) {
        throw e
    } catch (e: Throwable) {
        Result.failure(e)
    }
