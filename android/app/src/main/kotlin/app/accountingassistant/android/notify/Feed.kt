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
import app.accountingassistant.android.data.SupportConversation
import app.accountingassistant.android.security.AppLock
import app.accountingassistant.android.util.runSuspendCatching
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.util.concurrent.TimeUnit

/**
 * Notifications without a push provider (roadmap ROADMAP_ANDROID_CHAT P0.8,
 * P1.6): every hour WorkManager reads GET /feed and tells what it hasn't told
 * before. FCM is unreliable in Iran; when a provider is chosen, a push only
 * has to wake this same sync sooner. The first sync after turning them on is
 * silent, so nothing old floods in. While the app lock is on, a notification
 * says only that something needs attention: no figures past the lock.
 * A reply from support (scenario DB5) is told the same way, once per reply;
 * tapping it opens Support.
 */
class FeedNotifier(private val context: Context) {
    private val prefs = context.getSharedPreferences("feed", Context.MODE_PRIVATE)

    /** Turned off, it forgets what it told: turned back on, it starts silent again. */
    var enabled: Boolean
        get() = prefs.getBoolean("on", false)
        set(on) {
            if (!on) prefs.edit().remove(SEEN).remove(OLD_SEEN).remove("primed").apply()
            prefs.edit().putBoolean("on", on).apply()
        }

    /**
     * What was told, the longest gone from the feed first: kept as a list, so
     * trimming it to [MAX_SEEN] forgets the oldest. Android's string set keeps
     * no order, and trimming it forgot any of them, the newest too, which were
     * then told again (scenario CX5).
     */
    private fun seen(): List<String> {
        prefs.getString(SEEN, null)?.let { kept ->
            return runCatching { Json.decodeFromString(KEYS, kept) }.getOrDefault(emptyList())
        }
        // kept before as a set, in no order: nothing told is told again, and from now on the order holds
        return prefs.getStringSet(OLD_SEEN, null)?.toList().orEmpty()
    }

    /** Tell what is new; returns how many notifications went out. */
    fun deliver(items: List<FeedItem>, locked: Boolean): Int {
        val seen = seen()
        val told = seen.toHashSet()
        val primed = prefs.getBoolean("primed", false)
        val fresh = items.filter { it.key !in told }.distinctBy { it.key }
        // what the feed still holds goes last, so only what left it longest ago is forgotten
        val now = items.map { it.key }.distinct()
        val inFeed = now.toHashSet()
        val kept = (seen.filterNot { it in inFeed } + now).takeLast(MAX_SEEN)
        prefs.edit().putString(SEEN, Json.encodeToString(KEYS, kept)).remove(OLD_SEEN)
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
        // a reply from support opens Support, not the chat (scenario DB5)
        val openSupport = PendingIntent.getActivity(context, 1, Intent(context, MainActivity::class.java).setAction(ACTION_SUPPORT)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
        fresh.forEach { item ->
            val support = item.kind == SUPPORT
            // behind the lock a reply says only that support replied: never its words
            val title = if (locked && !support) context.getString(R.string.feed_locked_title) else item.title
            val body = if (!locked) item.body else if (support) "" else context.getString(R.string.feed_locked_body)
            val n = NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title).setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setColor(context.getColor(R.color.firouzeh))
                .setContentIntent(if (support) openSupport else open).setAutoCancel(true)
                // on the lock screen, never the figures
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setPublicVersion(NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_notification)
                    .setContentTitle(context.getString(R.string.feed_locked_title))
                    .setContentText(context.getString(R.string.feed_locked_body)).build())
                .build()
            // each under its own key (the tag): two keys whose hash codes met replaced each other's notification (scenario CX5)
            runCatching { manager.notify(item.key, NOTIFICATION_ID, n) }   // taken back between the check and now
        }
        return fresh.size
    }

    /** Signed out: forget what was told, so the next person starts silent. */
    fun forget() {
        prefs.edit().remove(SEEN).remove(OLD_SEEN).remove("primed").apply()
        NotificationManagerCompat.from(context).cancelAll()
    }

    private fun channel() {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.feed_channel),
            NotificationManager.IMPORTANCE_DEFAULT).apply { description = context.getString(R.string.feed_channel_hint) })
    }

    companion object {
        const val CHANNEL = "books"
        /** A feed item's kind for a reply from support; its key is "support:" and the reply's id. */
        const val SUPPORT = "support"
        /** The intent a support notification opens the app with: straight to Support. */
        const val ACTION_SUPPORT = "app.accountingassistant.android.action.SUPPORT"
        private const val MAX_SEEN = 300
        /** The keys told, oldest first, as a JSON list; [OLD_SEEN] is the unordered set kept before. */
        private const val SEEN = "seen_in_order"
        private const val OLD_SEEN = "seen"
        private val KEYS = ListSerializer(String.serializer())
        /** A feed notification is known by its key, as its tag; the id is the same for all. */
        private const val NOTIFICATION_ID = 1
        private const val WORK = "feed"

        fun schedule(context: Context) {
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<FeedWorker>(1, TimeUnit.HOURS)
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK)
        }

        /** The app was opened from a support notification. */
        fun opensSupport(intent: Intent?): Boolean = intent?.action == ACTION_SUPPORT

        /**
         * The newest reply from support not read yet, as a feed item: /feed
         * doesn't carry support's replies, so the hourly sync reads the
         * conversation's unread count itself. Null when nothing waits.
         */
        fun supportItem(conv: SupportConversation, title: String): FeedItem? {
            if (conv.unread <= 0) return null
            val reply = conv.messages.lastOrNull { it.fromDesk && it.seen != true } ?: return null
            return FeedItem("$SUPPORT:${reply.id}", SUPPORT, title, firstWords(reply.text.orEmpty()))
        }

        /** A reply's first words, cut at a space: «Fixed — the ledger page…». */
        fun firstWords(text: String, max: Int = 120): String {
            val t = text.trim().replace(Regex("\\s+"), " ")
            if (t.length <= max) return t
            return t.take(max).substringBeforeLast(' ').ifBlank { t.take(max) }.trimEnd() + "…"
        }

        /** The replies were read in the app: their notifications go. */
        fun clearSupport(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            runCatching {
                manager.activeNotifications.filter { it.tag?.startsWith("$SUPPORT:") == true }
                    .forEach { manager.cancel(it.tag, it.id) }
            }
        }
    }
}

class FeedWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val notifier = FeedNotifier(applicationContext)
        if (!notifier.enabled) return Result.success()
        val app = applicationContext as AccountantApp
        if (app.api.session == null && app.api.restore() == null) return Result.success()
        val feed = runSuspendCatching { app.api.feed() }.getOrNull() ?: return Result.retry()
        // a reply from support (scenario DB5): /feed doesn't carry it, so the conversation is asked
        val support = if (feed.items.any { it.kind == FeedNotifier.SUPPORT }) null
                      else runSuspendCatching { app.api.support() }.getOrNull()
                          ?.let { FeedNotifier.supportItem(it, applicationContext.getString(R.string.support_replied)) }
        notifier.deliver(feed.items + listOfNotNull(support), locked = AppLock(applicationContext).enabled())
        return Result.success()
    }
}
