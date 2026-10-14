package app.accountingassistant.android.sms

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Telephony
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.accountingassistant.android.AccountantApp
import app.accountingassistant.android.BuildConfig
import app.accountingassistant.android.MainActivity
import app.accountingassistant.android.R
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.NetworkError
import app.accountingassistant.android.data.PhoneSms
import app.accountingassistant.android.security.AppLock
import app.accountingassistant.android.util.Numbers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.util.concurrent.TimeUnit

/**
 * Bank SMS read as they arrive, in the builds outside Play (owner's decision,
 * roadmap ROADMAP_ANDROID_CHAT §11.5): `direct`'s manifest registers the
 * receivers and asks for RECEIVE_SMS; Play's has neither, and there the user
 * shares an SMS into the app instead. Off until the user turns it on in the
 * account sheet, and Android has allowed it.
 */
object SmsSwitch {
    private fun prefs(context: Context) = context.getSharedPreferences("bank_sms", Context.MODE_PRIVATE)

    /** This build can read SMS at all. */
    val available: Boolean get() = BuildConfig.READS_SMS

    fun on(context: Context): Boolean = available && prefs(context).getBoolean("on", false) &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED

    fun set(context: Context, on: Boolean) { prefs(context).edit().putBoolean("on", on).apply() }
}

/** Each SMS as it arrives: kept, asked about or sent only by [BankSms]'s rules. */
class BankSmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION || !SmsSwitch.on(context)) return
        val parts = runCatching { Telephony.Sms.Intents.getMessagesFromIntent(intent) }.getOrNull()?.filterNotNull() ?: return
        // a long SMS comes in parts: one message per sender
        val bySender = parts.groupBy { it.originatingAddress.orEmpty() }.filterKeys { it.isNotBlank() }
        if (bySender.isEmpty()) return
        val app = context.applicationContext as AccountantApp
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                for ((sender, ps) in bySender) {
                    val text = ps.joinToString("") { it.messageBody.orEmpty() }
                    when (app.bankSms.arrived(sender, text, ps.minOf { it.timestampMillis })) {
                        BankSms.Route.Queued -> BankSmsWorker.schedule(context)
                        BankSms.Route.Asked -> SmsAsk(context).ask(BankText.senderKey(sender), BankText.bankOf(text))
                        else -> Unit
                    }
                }
            } finally {
                pending.finish()
            }
        }
    }
}

/** Read or Ignore, from the notification that asked. */
class BankSmsChoice : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val sender = intent.getStringExtra(EXTRA_SENDER) ?: return
        val read = intent.getBooleanExtra(EXTRA_READ, false)
        val app = context.applicationContext as AccountantApp
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                app.bankSms.decide(sender, read)
                if (read) BankSmsWorker.schedule(context)
                SmsAsk(context).cancel(sender)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val EXTRA_SENDER = "sender"
        const val EXTRA_READ = "read"
    }
}

/**
 * «Is 98700717 your bank? Mellat: read its SMS into your books?», with Read
 * and Ignore. Never an amount; with the app lock on, not even the sender or
 * the bank: the choice waits in the account sheet.
 */
class SmsAsk(private val context: Context) {
    suspend fun ask(sender: String, bank: String?) {
        val manager = NotificationManagerCompat.from(context)
        if (!manager.areNotificationsEnabled()) return
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        channel()
        val locked = AppLock(context).enabled()
        val lang = context.resources.configuration.locales[0].language
        val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(context.getColor(R.color.firouzeh))
            .setContentIntent(open).setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
        if (locked) {
            n.setContentTitle(context.getString(R.string.sms_locked_title)).setContentText(context.getString(R.string.sms_locked_body))
        } else {
            val name = BankText.displayName(bank, lang)
            val body = if (name != null) context.getString(R.string.sms_ask_body, name) else context.getString(R.string.sms_ask_body_unknown)
            n.setContentTitle(context.getString(R.string.sms_ask_title, Numbers.digits(sender, lang)))
                .setContentText(body).setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .addAction(0, context.getString(R.string.sms_read), choice(sender, true))
                .addAction(0, context.getString(R.string.sms_ignore), choice(sender, false))
        }
        runCatching { manager.notify(id(sender), n.build()) }          // taken back between the check and now
    }

    fun cancel(sender: String) = NotificationManagerCompat.from(context).cancel(id(sender))

    private fun choice(sender: String, read: Boolean): PendingIntent = PendingIntent.getBroadcast(
        context, "$sender:$read".hashCode(),
        Intent(context, BankSmsChoice::class.java).putExtra(BankSmsChoice.EXTRA_SENDER, sender).putExtra(BankSmsChoice.EXTRA_READ, read),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun id(sender: String) = "sms:$sender".hashCode()

    private fun channel() {
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, context.getString(R.string.sms_channel), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = context.getString(R.string.sms_channel_hint) })
    }

    companion object { const val CHANNEL = "bank_sms" }
}

/**
 * Sends what [BankSms] keeps to send, when there is a network, with the app
 * open or closed. A message refused for good (this role can't record, or the
 * server can't read it) is let go; a signed-out phone or an app too old to
 * talk to the server keeps them for later.
 */
class BankSmsWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as AccountantApp
        if (app.api.session == null && app.api.restore() == null) return Result.success()
        repeat(MAX_BATCHES) {
            val batch = app.bankSms.outbox().take(BankSms.BATCH)
            if (batch.isEmpty()) return Result.success()
            try {
                app.api.bankSms(batch.map {
                    PhoneSms(it.sender, it.text, Instant.ofEpochMilli(it.receivedAt).atZone(ZoneId.systemDefault()).toOffsetDateTime().toString(), it.bank)
                })
            } catch (e: NetworkError) {
                return Result.retry()
            } catch (e: ApiError) {
                when {
                    e.sessionOver || e.updateNeeded -> return Result.success()
                    e.status == 403 || e.status == 422 -> Unit       // never to be taken: let them go
                    else -> return Result.retry()
                }
            }
            app.bankSms.done(batch.map { it.id }.toSet())
        }
        return Result.success()
    }

    companion object {
        private const val NAME = "bank_sms"
        private const val MAX_BATCHES = 20

        fun schedule(context: Context) {
            val request = OneTimeWorkRequestBuilder<BankSmsWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            // after a run under way, never instead of it: what arrived as it finished still goes
            WorkManager.getInstance(context).enqueueUniqueWork(NAME, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
        }

        fun cancel(context: Context) = WorkManager.getInstance(context).cancelUniqueWork(NAME)
    }
}
