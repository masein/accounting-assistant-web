package app.accountingassistant.android

import android.content.Intent
import android.view.ViewGroup
import android.widget.FrameLayout
import app.accountingassistant.android.util.Shortcuts
import app.accountingassistant.android.widget.CashWidget
import app.accountingassistant.android.widget.Held
import app.accountingassistant.android.widget.WidgetState
import app.accountingassistant.android.widget.widgetText
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import androidx.activity.ComponentActivity
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The home-screen widget and the app shortcuts (scenario N33). */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
class WidgetTest {
    private val words: (Int) -> String = { RuntimeEnvironment.getApplication().getString(it) }
    private val formatted: (Int, String) -> String = { id, arg -> RuntimeEnvironment.getApplication().getString(id, arg) }
    private val books = WidgetState(signedIn = true, books = "شرکت بازرگانی آرمان", cash = 49_600_000.0, cashCurrency = "IRR",
                                    budgetLeft = 9_600_000.0, budgetCurrency = "IRR", asOf = "2026-10-10")

    @Test @Config(qualifiers = "+fa")
    fun theTwoNumbersInPersian() {
        val t = widgetText(books, "fa", words, formatted)
        assertEquals("شرکت بازرگانی آرمان", t.title)
        assertEquals("۴۹٬۶۰۰٬۰۰۰ ریال", t.amount)
        assertEquals("بودجهٔ مانده: ۹٬۶۰۰٬۰۰۰", t.line)
        assertEquals("تا ۱۸ مهر", t.note)
        // a Gregorian company: the date in its calendar, in Persian words (scenario N38)
        assertEquals("تا ۱۰ اکتبر", widgetText(books.copy(calendar = "gregorian"), "fa", words, formatted).note)
    }

    @Test fun poundsWithTheirSign() {
        val uk = WidgetState(signedIn = true, books = "Hartley & Co Ltd", cash = 23_923.0, cashCurrency = "GBP", asOf = "2026-10-12",
                             calendar = "gregorian")
        assertEquals("£23,923.00", widgetText(uk, "en", words, formatted).amount)
        assertEquals("£23,915.50", widgetText(uk.copy(cash = 23_915.5), "en", words, formatted).amount)
    }

    /** Each currency as it is (docs/PLAN_CURRENCIES.md phase 2): the first large, the rest under it, then the budget. */
    private val wallets = books.copy(books = "دفتر من", cash = 77.5, cashCurrency = "OMR",
                                     others = listOf(Held("IRR", 3_000_000.0), Held("USD", 500.0)))

    @Test @Config(qualifiers = "+fa")
    fun eachCurrencyAsItIs() {
        val t = widgetText(wallets, "fa", words, formatted)
        assertEquals("۷۷٫۵۰۰ ریال عمان", t.amount)
        // the dollars named after their figure, «۵۰۰ دلار»: «$۵۰۰٫۰۰» put its sign on the right (Persian copy review, 2026-10-31)
        assertEquals("۳٬۰۰۰٬۰۰۰ ریال، ۵۰۰ دلار", t.others)
        assertEquals("3,000,000 IRR · \$500.00", widgetText(wallets, "en", words, formatted).others)
        assertEquals("بودجهٔ مانده: ۹٬۶۰۰٬۰۰۰", t.line)
        assertNull(widgetText(books, "fa", words, formatted).others)
    }

    @Test fun lockedOrSignedOutItShowsNoFigures() {
        val locked = widgetText(books.copy(locked = true), "en", words, formatted)
        assertNull(locked.amount)
        assertEquals("Open the app to see", locked.note)
        val out = widgetText(WidgetState(), "en", words, formatted)
        assertNull(out.amount)
        assertEquals("Sign in to see your books", out.note)
        assertEquals("Accountant", out.title)
    }

    @Test fun onlyOurShortcutsAreActions() {
        assertEquals(Shortcuts.PHOTO_RECEIPT, Shortcuts.actionOf(Intent(Shortcuts.PHOTO_RECEIPT)))
        assertNull(Shortcuts.actionOf(Intent(Intent.ACTION_MAIN)))
        assertNull(Shortcuts.actionOf(null))
    }

    private fun shot(state: WidgetState, name: String) {
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        val frame = FrameLayout(activity).apply { setPadding(24, 24, 24, 24) }
        frame.addView(CashWidget.views(activity, state).apply(activity, frame), FrameLayout.LayoutParams(660, 330))
        activity.setContentView(frame, ViewGroup.LayoutParams(708, 378))
        frame.captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa-w360dp-h640dp-xxhdpi")
    fun widgetInPersian() = shot(books, "widget-fa")

    @Test @Config(qualifiers = "+fa-w360dp-h640dp-xxhdpi")
    fun widgetInSeveralCurrencies() = shot(wallets, "widget-fa-currencies")

    @Test @Config(qualifiers = "+en-w360dp-h640dp-night-xxhdpi")
    fun widgetLockedInEnglishDark() = shot(books.copy(locked = true, books = "Arman Trading Ltd"), "widget-en-locked-dark")
}
