package app.accountingassistant.android.notify

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.accountingassistant.android.AccountantApp
import app.accountingassistant.android.MainActivity
import app.accountingassistant.android.R
import app.accountingassistant.android.data.FeedItem
import app.accountingassistant.android.security.AppLock
import java.util.concurrent.TimeUnit

/**
 * Notifications without a push provider (roadmap ROADMAP_ANDROID_CHAT P0.8,
 * P1.6): every hour WorkManager reads GET /feed and tells what it hasn't told
 * before. FCM is unreliable in Iran; when a provider is chosen, a push only
 * has to wake this same sync sooner. The first sync after turning them on is
 * silent, so nothing old floods in. While the app lock is on, a notification
 * says only that something needs attention: no figures past the lock.
 */
class FeedNotifier(private val context: Context) {
    private val prefs = context.getSharedPreferences("feed", Context.MODE_PRIVATE)

    /** Turned off, it forgets what it told: turned back on, it starts silent again. */
    var enabled: Boolean
        get() = prefs.getBoolean("on", false)
        set(on) {
            if (!on) prefs.edit().remove("seen").remove("primed").apply()
            prefs.edit().putBoolean("on", on).apply()
        }

    private fun seen(): MutableSet<String> = prefs.getStringSet("seen", emptySet())!!.toMutableSet()

    /** Tell what is new; returns how many notifications went out. */
    fun deliver(items: List<FeedItem>, locked: Boolean): Int {
        val seen = seen()
        val primed = prefs.getBoolean("primed", false)
        val fresh = items.filter { it.key !in seen }
        prefs.edit().putStringSet("seen", (seen + items.map { it.key }).toList().takeLast(MAX_SEEN).toSet())
            .putBoolean("primed", true).apply()
        if (!primed || fresh.isEmpty()) return 0
        channel()
        val manager = NotificationManagerCompat.from(context)
        if (!manager.areNotificationsEnabled()) return 0
        // allowed in the account sheet, but it can be taken back in Android's settings at any time
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return 0
        val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
        fresh.forEach { item ->
            val title = if (locked) context.getString(R.string.feed_locked_title) else item.title
            val body = if (locked) context.getString(R.string.feed_locked_body) else item.body
            val n = NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title).setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setColor(context.getColor(R.color.firouzeh))
                .setContentIntent(open).setAutoCancel(true)
                // on the lock screen, never the figures
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setPublicVersion(NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_notification)
                    .setContentTitle(context.getString(R.string.feed_locked_title))
                    .setContentText(context.getString(R.string.feed_locked_body)).build())
                .build()
            runCatching { manager.notify(item.key.hashCode(), n) }        // taken back between the check and now
        }
        return fresh.size
    }

    /** Signed out: forget what was told, so the next person starts silent. */
    fun forget() {
        prefs.edit().remove("seen").remove("primed").apply()
        NotificationManagerCompat.from(context).cancelAll()
    }

    private fun channel() {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.feed_channel),
            NotificationManager.IMPORTANCE_DEFAULT).apply { description = context.getString(R.string.feed_channel_hint) })
    }

    companion object {
        const val CHANNEL = "books"
        private const val MAX_SEEN = 300
        private const val WORK = "feed"

        fun schedule(context: Context) {
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<FeedWorker>(1, TimeUnit.HOURS)
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK)
        }
    }
}

class FeedWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val notifier = FeedNotifier(applicationContext)
        if (!notifier.enabled) return Result.success()
        val app = applicationContext as AccountantApp
        if (app.api.session == null && app.api.restore() == null) return Result.success()
        val feed = runCatching { app.api.feed() }.getOrNull() ?: return Result.retry()
        notifier.deliver(feed.items, locked = AppLock(applicationContext).enabled())
        return Result.success()
    }
}
