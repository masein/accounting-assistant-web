package app.accountingassistant.android.util

import android.content.Context
import android.content.Intent
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import app.accountingassistant.android.MainActivity
import app.accountingassistant.android.R

/**
 * Long-press the app icon (roadmap ROADMAP_ANDROID_CHAT P3.7): photograph a
 * receipt, record spending, ask for the cash. Made in code, not in a static
 * shortcuts.xml, which would have to name the package, and the applicationId
 * is still to be settled.
 */
object Shortcuts {
    const val PHOTO_RECEIPT = "app.accountingassistant.android.action.PHOTO_RECEIPT"
    const val RECORD_SPENDING = "app.accountingassistant.android.action.RECORD_SPENDING"
    const val ASK_CASH = "app.accountingassistant.android.action.ASK_CASH"
    private val ALL = setOf(PHOTO_RECEIPT, RECORD_SPENDING, ASK_CASH)

    /** The shortcut an intent came from, if any. */
    fun actionOf(intent: Intent?): String? = intent?.action?.takeIf { it in ALL }

    fun publish(context: Context) {
        fun shortcut(id: String, action: String, short: Int, long: Int, icon: Int) =
            ShortcutInfoCompat.Builder(context, id)
                .setShortLabel(context.getString(short)).setLongLabel(context.getString(long))
                .setIcon(IconCompat.createWithResource(context, icon))
                .setIntent(Intent(context, MainActivity::class.java).setAction(action).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
                .build()
        runCatching {
            ShortcutManagerCompat.setDynamicShortcuts(context, listOf(
                shortcut("receipt", PHOTO_RECEIPT, R.string.shortcut_receipt, R.string.shortcut_receipt_long, R.drawable.ic_shortcut_receipt),
                shortcut("spending", RECORD_SPENDING, R.string.shortcut_spending, R.string.shortcut_spending_long, R.drawable.ic_shortcut_spending),
                shortcut("cash", ASK_CASH, R.string.shortcut_cash, R.string.shortcut_cash_long, R.drawable.ic_shortcut_cash),
            ))
        }
    }

    /** Signed out: the shortcuts would only lead to the sign-in. */
    fun remove(context: Context) {
        runCatching { ShortcutManagerCompat.removeAllDynamicShortcuts(context) }
    }
}
