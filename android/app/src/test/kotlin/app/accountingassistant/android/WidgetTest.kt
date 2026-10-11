package app.accountingassistant.android

import android.content.Intent
import android.view.ViewGroup
import android.widget.FrameLayout
import app.accountingassistant.android.util.Shortcuts
import app.accountingassistant.android.widget.CashWidget
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
    private val books = WidgetState(signedIn = true, books = "شرکت بازرگانی آرمان", cash = 49_600_000, cashCurrency = "IRR",
                                    budgetLeft = 9_600_000, budgetCurrency = "IRR", asOf = "2026-10-10")

    @Test @Config(qualifiers = "+fa")
    fun theTwoNumbersInPersian() {
        val t = widgetText(books, "fa", words, formatted)
        assertEquals("شرکت بازرگانی آرمان", t.title)
        assertEquals("۴۹٬۶۰۰٬۰۰۰ ریال", t.amount)
        assertEquals("بودجهٔ مانده: ۹٬۶۰۰٬۰۰۰", t.line)
        assertEquals("تا ۱۸ مهر", t.note)
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

    @Test @Config(qualifiers = "+en-w360dp-h640dp-night-xxhdpi")
    fun widgetLockedInEnglishDark() = shot(books.copy(locked = true, books = "Arman Trading Ltd"), "widget-en-locked-dark")
}
