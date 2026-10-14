package app.accountingassistant.android

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import app.accountingassistant.android.ui.components.Composer
import app.accountingassistant.android.ui.theme.AccountantTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * The microphone's gestures (scenarios W1–W4): hold and let go writes, sliding toward the
 * start side throws it away (left in English, right in Persian), a tap records hands-free
 * with ✓ to write and ✕ to throw away.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class ComposerGestureTest {
    @get:Rule val compose = createComposeRule()
    private var starts = 0
    private var ends = 0
    private var cancels = 0

    private fun show() {
        compose.mainClock.autoAdvance = false                  // the recording's dot and timer never idle
        compose.setContent {
            AccountantTheme(dark = false) {
                var listening by remember { mutableStateOf(false) }
                Composer(value = "", onValueChange = {}, onSend = {}, onAttach = {},
                         onSpeakStart = { starts++; listening = true },
                         onSpeakEnd = { ends++; listening = false },
                         onSpeakCancel = { cancels++; listening = false },
                         listening = listening)
            }
        }
        compose.mainClock.advanceTimeBy(100)
    }

    private fun text(id: Int) = RuntimeEnvironment.getApplication().getString(id)
    private fun mic() = compose.onNodeWithContentDescription(text(R.string.speak))

    private fun hold(ms: Long, slideDp: Float = 0f) {
        mic().performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(50)                   // a frame: recording has started, the bar shows
        mic().performTouchInput {
            advanceEventTime(ms)
            if (slideDp != 0f) {
                repeat(6) { moveBy(Offset(slideDp.dp.toPx() / 6, 0f)); advanceEventTime(20) }
            }
            up()
        }
        compose.mainClock.advanceTimeBy(100)
    }

    @Test fun holdingThenLettingGoWritesIt() {
        show()
        hold(900)
        assertEquals(listOf(1, 1, 0), listOf(starts, ends, cancels))
    }

    @Test fun slidingTowardTheStartThrowsItAway() {
        show()
        hold(500, slideDp = -140f)
        assertEquals(listOf(1, 0, 1), listOf(starts, ends, cancels))
    }

    @Test fun slidingTowardTheEndKeepsIt() {
        show()
        hold(500, slideDp = 140f)
        assertEquals(listOf(1, 1, 0), listOf(starts, ends, cancels))
    }

    @Test fun aTapRecordsHandsFreeThenTheTickWritesIt() {
        show()
        hold(80)
        assertEquals(listOf(1, 0, 0), listOf(starts, ends, cancels))          // still recording
        compose.onNodeWithContentDescription(text(R.string.voice_done)).performClick()
        compose.mainClock.advanceTimeBy(100)
        assertEquals(listOf(1, 1, 0), listOf(starts, ends, cancels))
    }

    @Test fun handsFreeTheCrossThrowsItAway() {
        show()
        hold(80)
        compose.onNodeWithContentDescription(text(R.string.voice_cancel)).performClick()
        compose.mainClock.advanceTimeBy(100)
        assertEquals(listOf(1, 0, 1), listOf(starts, ends, cancels))
    }

    @Test @Config(qualifiers = "+fa")
    fun inPersianTheStartIsTheRight() {
        show()
        hold(500, slideDp = 140f)                                             // rightward: thrown away
        assertEquals(listOf(1, 0, 1), listOf(starts, ends, cancels))
        hold(500, slideDp = -140f)                                            // leftward: kept
        assertEquals(listOf(2, 1, 1), listOf(starts, ends, cancels))
    }
}
