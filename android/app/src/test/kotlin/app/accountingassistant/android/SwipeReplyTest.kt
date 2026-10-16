package app.accountingassistant.android

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipe
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatScreen
import app.accountingassistant.android.ui.chat.ChatUiState
import app.accountingassistant.android.ui.chat.Quote
import app.accountingassistant.android.ui.theme.AccountantTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Swipe a message left to reply to it (scenario AC11), in Persian and in English:
 * far enough and let go, it replies; a short swipe springs back; scrolling never replies.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class SwipeReplyTest {
    @get:Rule val compose = createComposeRule()
    private val replies = mutableListOf<Quote?>()
    private val words = "در کیف پول سبز ۷۷٫۵۰۰ ریال عمان دارید."

    private fun show(lang: String) {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(state = ChatUiState(items = listOf(ChatItem.User("u1", "پول من کجاست؟"), ChatItem.Words("w1", words))),
                           lang = lang, booksName = "masein", personalBooks = true,
                           onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                           onReply = { replies += it })
            }
        }
        compose.mainClock.advanceTimeBy(500)
    }

    private fun swipeLeftBy(text: String, dx: Float) = compose.onNodeWithText(text, substring = true).performTouchInput {
        val from = Offset(width * 0.7f, height / 2f)
        swipe(from, from + Offset(-dx, 0f), durationMillis = 300)
    }

    @Test @Config(qualifiers = "+fa")
    fun swipingAnAnswerLeftRepliesToIt() {
        show("fa")
        swipeLeftBy("ریال عمان دارید", 260f)
        compose.mainClock.advanceTimeBy(600)
        assertEquals(listOf(Quote(false, words)), replies)
        swipeLeftBy("پول من کجاست", 260f)                       // one's own message too
        compose.mainClock.advanceTimeBy(600)
        assertEquals(Quote(true, "پول من کجاست؟"), replies.last())
    }

    @Test @Config(qualifiers = "+en")
    fun aShortSwipeOrARightOneOrAScrollDoesNothing() {
        show("en")
        swipeLeftBy("ریال عمان دارید", 60f)                    // short of the line: it springs back
        compose.onNodeWithText("ریال عمان دارید", substring = true).performTouchInput {
            swipe(Offset(width * 0.3f, height / 2f), Offset(width * 0.3f + 260f, height / 2f), durationMillis = 300)
        }
        compose.onNodeWithText("ریال عمان دارید", substring = true).performTouchInput {
            swipe(Offset(width * 0.5f, height / 2f), Offset(width * 0.5f - 40f, height / 2f - 400f), durationMillis = 300)
        }
        compose.mainClock.advanceTimeBy(600)
        assertEquals(emptyList<Quote?>(), replies)
    }

    /** Mid-swipe: the message follows the finger and the arrow comes up beside it. */
    @Test @Config(qualifiers = "+fa")
    fun midSwipeTheArrowShows() {
        show("fa")
        compose.mainClock.autoAdvance = false
        compose.onNodeWithText("ریال عمان دارید", substring = true).performTouchInput {
            down(Offset(width * 0.7f, height / 2f))
            repeat(8) { moveBy(Offset(-30f, 0f)) }
        }
        compose.mainClock.advanceTimeBy(100)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-swipe-reply.png")
        compose.onNodeWithText("ریال عمان دارید", substring = true).performTouchInput { up() }
        compose.mainClock.autoAdvance = true
        compose.mainClock.advanceTimeBy(600)
        assertEquals(listOf(Quote(false, words)), replies)
    }
}
