package app.accountingassistant.android

import android.content.Intent
import android.view.ViewGroup
import android.widget.FrameLayout
import app.accountingassistant.android.data.SummaryReply
import app.accountingassistant.android.util.Shortcuts
import app.accountingassistant.android.widget.CashWidget
import app.accountingassistant.android.widget.Held
import app.accountingassistant.android.widget.WidgetState
import app.accountingassistant.android.widget.widgetText
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.Json
import java.math.BigDecimal
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
    private val books = WidgetState(signedIn = true, books = "شرکت بازرگانی آرمان", cash = BigDecimal(49_600_000), cashCurrency = "IRR",
                                    budgetLeft = BigDecimal(9_600_000), budgetCurrency = "IRR", asOf = "2026-10-10")

    @Test @Config(qualifiers = "+fa")
    fun theTwoNumbersInPersian() {
        val t = widgetText(books, "fa", words, formatted)
        assertEquals("شرکت بازرگانی آرمان", t.title)
        assertEquals("۴۹,۶۰۰,۰۰۰ ریال", t.amount)
        assertEquals("بودجهٔ مانده: ۹,۶۰۰,۰۰۰", t.line)
        assertEquals("تا ۱۸ مهر", t.note)
        // a Gregorian company: the date in its calendar, in Persian words (scenario N38)
        assertEquals("تا ۱۰ اکتبر", widgetText(books.copy(calendar = "gregorian"), "fa", words, formatted).note)
    }

    @Test fun poundsWithTheirSign() {
        val uk = WidgetState(signedIn = true, books = "Hartley & Co Ltd", cash = BigDecimal(23_923), cashCurrency = "GBP", asOf = "2026-10-12",
                             calendar = "gregorian")
        assertEquals("£23,923.00", widgetText(uk, "en", words, formatted).amount)
        assertEquals("£23,915.50", widgetText(uk.copy(cash = BigDecimal("23915.5")), "en", words, formatted).amount)
    }

    /** Scenario CX6: the amounts as the server wrote them, the pence and the last rial kept, on screen and on the phone. */
    @Test fun theAmountsStayExactAsSent() {
        val json = Json { ignoreUnknownKeys = true }
        val s = json.decodeFromString(SummaryReply.serializer(),
            """{"as_of":"2026-10-12","cash":{"total":12345678901234567.89,"currency":"GBP","by_currency":[""" +
            """{"currency":"GBP","total":12345678901234567.89},{"currency":"IRR","total":98765432109876543}]},""" +
            """"budget":{"left":0.1,"total":0.3,"currency":"GBP"}}""")
        assertEquals(BigDecimal("12345678901234567.89"), s.cash.total)              // a Double kept «…4567.88» or less
        assertEquals(BigDecimal("98765432109876543"), s.cash.byCurrency[1].total)
        assertEquals(BigDecimal("0.1"), s.budget?.left)
        val state = WidgetState(signedIn = true, books = "Hartley & Co Ltd", cash = s.cash.total, cashCurrency = s.cash.currency,
                                others = s.cash.byCurrency.drop(1).map { Held(it.currency, it.total) },
                                budgetLeft = s.budget?.left, budgetCurrency = s.budget?.currency, asOf = s.asOf, calendar = "gregorian")
        val t = widgetText(state, "en", words, formatted)
        assertEquals("£12,345,678,901,234,567.89", t.amount)
        assertEquals("98,765,432,109,876,543 IRR", t.others)
        // kept on the phone between refreshes and read back: still exact
        assertEquals(state, json.decodeFromString(WidgetState.serializer(), json.encodeToString(WidgetState.serializer(), state)))
        // what an earlier version kept, the amounts as numbers, still reads
        val before = json.decodeFromString(WidgetState.serializer(), """{"signedIn":true,"cash":4.96E7,"cashCurrency":"IRR","budgetLeft":9600000.0}""")
        assertEquals("49,600,000 IRR", widgetText(before, "en", words, formatted).amount)
    }

    /** Each currency as it is (docs/PLAN_CURRENCIES.md phase 2): the first large, the rest under it, then the budget. */
    private val wallets = books.copy(books = "دفتر من", cash = BigDecimal("77.5"), cashCurrency = "OMR",
                                     others = listOf(Held("IRR", BigDecimal(3_000_000)), Held("USD", BigDecimal(500))))

    @Test @Config(qualifiers = "+fa")
    fun eachCurrencyAsItIs() {
        val t = widgetText(wallets, "fa", words, formatted)
        assertEquals("۷۷٫۵۰۰ ریال عمان", t.amount)
        // the dollars named after their figure, «۵۰۰ دلار»: «$۵۰۰٫۰۰» put its sign on the right (Persian copy review, 2026-10-31)
        assertEquals("۳,۰۰۰,۰۰۰ ریال، ۵۰۰ دلار", t.others)
        assertEquals("3,000,000 IRR · \$500.00", widgetText(wallets, "en", words, formatted).others)
        assertEquals("بودجهٔ مانده: ۹,۶۰۰,۰۰۰", t.line)
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
