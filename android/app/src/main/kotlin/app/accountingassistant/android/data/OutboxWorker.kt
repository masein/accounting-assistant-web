package app.accountingassistant.android.data

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.accountingassistant.android.AccountantApp
import java.util.concurrent.TimeUnit

/**
 * Sends the outbox when the network is back, with the app open or closed:
 * the answers wait in their threads. Retried with a growing pause while the
 * network comes and goes.
 */
class OutboxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as AccountantApp
        if (app.api.session == null && app.api.restore() == null) return Result.success()   // signed out: nothing to send as anyone
        return when (app.outbox.flush()) {
            Outbox.Result.Offline -> Result.retry()
            else -> Result.success()
        }
    }

    companion object {
        private const val NAME = "outbox"

        /** Try again once there is a network (one request at a time; a waiting one is kept). */
        fun schedule(context: Context) {
            val request = OneTimeWorkRequestBuilder<OutboxWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(NAME, ExistingWorkPolicy.KEEP, request)
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(NAME)
        }
    }
}
