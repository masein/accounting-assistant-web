package app.accountingassistant.android.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.accountingassistant.android.AccountantApp
import app.accountingassistant.android.MainActivity
import app.accountingassistant.android.R
import app.accountingassistant.android.security.AppLock
import app.accountingassistant.android.ui.components.currencyName
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.Numbers
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.concurrent.TimeUnit

/** What the widget last knew, kept on the phone between refreshes. */
@Serializable
data class WidgetState(
    val signedIn: Boolean = false,
    /** The app lock is on: the widget never shows the books past it. */
    val locked: Boolean = false,
    val books: String = "",
    val cash: Long? = null,
    val cashCurrency: String? = null,
    val budgetLeft: Long? = null,
    val budgetCurrency: String? = null,
    val asOf: String? = null,
)

/** The widget's lines, worked out away from Android's views so they can be tested. */
data class WidgetText(val title: String, val amount: String?, val line: String?, val note: String?)

fun widgetText(state: WidgetState, lang: String, words: (Int) -> String, formatted: (Int, String) -> String): WidgetText {
    val title = state.books.ifBlank { words(R.string.widget_title) }
    return when {
        !state.signedIn -> WidgetText(title, null, null, words(R.string.widget_signed_out))
        state.locked -> WidgetText(title, null, null, words(R.string.widget_locked))
        state.cash == null -> WidgetText(title, null, null, words(R.string.widget_loading))
        else -> WidgetText(
            title = title,
            amount = Numbers.amount(state.cash, lang) + (state.cashCurrency?.let { " " + currencyName(it, lang) } ?: ""),
            line = state.budgetLeft?.let { formatted(R.string.widget_budget_left, Numbers.amount(it, lang)) },
            note = state.asOf?.let { formatted(R.string.widget_as_of, Dates.short(it, lang)) },
        )
    }
}

/**
 * Cash and bank, and this month's budget left, on the home screen (roadmap
 * ROADMAP_ANDROID_CHAT P3.7). Plain RemoteViews, no extra library. Refreshed
 * every few hours by WorkManager and when the app opens; a tap opens the
 * chat. While the app lock is on it shows no figures: the lock would
 * otherwise be one swipe away from the books.
 */
class CashWidget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        render(context, load(context), manager, ids)
        WidgetRefresh.now(context)
    }

    override fun onEnabled(context: Context) = WidgetRefresh.schedule(context)

    override fun onDisabled(context: Context) = WidgetRefresh.cancel(context)

    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        private const val PREFS = "cash_widget"

        fun load(context: Context): WidgetState = runCatching {
            json.decodeFromString(WidgetState.serializer(), context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("state", null)!!)
        }.getOrDefault(WidgetState())

        fun save(context: Context, state: WidgetState) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("state", json.encodeToString(WidgetState.serializer(), state)).apply()
        }

        /** Draw every widget the user placed from what is kept. */
        fun renderAll(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, CashWidget::class.java))
            if (ids.isNotEmpty()) render(context, load(context), manager, ids)
        }

        fun views(context: Context, state: WidgetState): RemoteViews {
            val lang = context.resources.configuration.locales[0].language
            val t = widgetText(state, lang, context::getString) { id, arg -> context.getString(id, arg) }
            // three lines fit a two-cell widget: what and when, the amount, the budget left;
            // without figures, the books and why
            val figures = t.amount != null
            return RemoteViews(context.packageName, R.layout.widget_cash).apply {
                setTextViewText(R.id.widget_title, t.title)
                setViewVisibility(R.id.widget_title, if (figures) View.GONE else View.VISIBLE)
                setTextViewText(R.id.widget_label, listOfNotNull(context.getString(R.string.widget_cash), t.note).joinToString(" · "))
                setViewVisibility(R.id.widget_label, if (figures) View.VISIBLE else View.GONE)
                setTextViewText(R.id.widget_amount, t.amount ?: t.note ?: "")
                setFloat(R.id.widget_amount, "setTextSize", if (figures) 22f else 15f)
                setTextViewText(R.id.widget_line, t.line ?: "")
                setViewVisibility(R.id.widget_line, if (t.line != null) View.VISIBLE else View.GONE)
                setViewVisibility(R.id.widget_note, View.GONE)
                val open = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                setOnClickPendingIntent(R.id.widget_root, PendingIntent.getActivity(context, 0, open,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            }
        }

        private fun render(context: Context, state: WidgetState, manager: AppWidgetManager, ids: IntArray) {
            val v = views(context, state)
            ids.forEach { manager.updateAppWidget(it, v) }
        }
    }
}

/** Fetches the two numbers and redraws the widgets. */
class WidgetRefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as AccountantApp
        val session = app.api.session ?: app.api.restore()
        val locked = AppLock(applicationContext).enabled()
        val state = if (session == null) WidgetState() else if (locked) WidgetState(signedIn = true, locked = true, books = session.company?.name.orEmpty())
        else {
            val s = runCatching { app.api.summary() }.getOrNull() ?: return Result.retry()
            WidgetState(signedIn = true, books = session.company?.name.orEmpty(), cash = s.cash.total, cashCurrency = s.cash.currency,
                        budgetLeft = s.budget?.left, budgetCurrency = s.budget?.currency, asOf = s.asOf)
        }
        CashWidget.save(applicationContext, state)
        CashWidget.renderAll(applicationContext)
        return Result.success()
    }
}

object WidgetRefresh {
    private const val PERIODIC = "widget-periodic"
    private const val ONCE = "widget-once"
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    fun schedule(context: Context) {
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<WidgetRefreshWorker>(3, TimeUnit.HOURS).setConstraints(online).build())
    }

    /** Now: the app opened, or a voucher was posted or undone. Only when a widget is placed. */
    fun now(context: Context) {
        val ids = AppWidgetManager.getInstance(context).getAppWidgetIds(ComponentName(context, CashWidget::class.java))
        if (ids.isEmpty()) return
        WorkManager.getInstance(context).enqueueUniqueWork(ONCE, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<WidgetRefreshWorker>().setConstraints(online).build())
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC)
    }

    /** Signed out: the widget forgets the books at once. */
    fun forget(context: Context) {
        CashWidget.save(context, WidgetState())
        CashWidget.renderAll(context)
    }
}
