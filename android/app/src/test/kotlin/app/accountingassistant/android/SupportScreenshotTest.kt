package app.accountingassistant.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.data.SupportMessageDto
import app.accountingassistant.android.ui.account.AccountSheetContent
import app.accountingassistant.android.ui.support.SupportScreen
import app.accountingassistant.android.ui.support.SupportUiState
import app.accountingassistant.android.ui.theme.AccountantTheme
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Support (scenarios DB1–DB8): the conversation with the desk in Persian and English, and its row in the account sheet. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class SupportScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private val app get() = RuntimeEnvironment.getApplication()

    private fun shot(name: String, state: SupportUiState, lang: String, dark: Boolean = false) {
        compose.setContent {
            AccountantTheme(dark = dark) {
                SupportScreen(state = state, lang = lang, onBack = {}, onDraft = {}, onSend = {})
            }
        }
        compose.mainClock.advanceTimeBy(600)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    private fun msg(id: String, desk: Boolean, text: String, at: String, files: Int = 0) =
        SupportMessageDto(id, author = if (desk) "desk" else "user", channel = if (desk) "desk" else "android", text = text,
                          attachments = (1..files).map { "a$it" }, at = at, seen = if (desk) true else null)

    /** The person's messages on the end side (the left, right to left), the desk's on the start side; online. */
    @Test @Config(qualifiers = "+fa")
    fun persianConversation() {
        shot("support-fa", SupportUiState(loaded = true, online = true, messages = listOf(
            msg("m1", false, "صفحهٔ دفتر کل باز نمی‌شود و خطا می‌دهد. از دیروز این‌طور است.", "2026-11-27T09:12:00+00:00", files = 1),
            msg("m2", true, "ممنون که خبر دادید. خطا پیدا شد: **دفتر کل** با تاریخ‌های شمسی پیش از ۱۴۰۰ باز نمی‌شد. درست شد و تا چند دقیقهٔ دیگر روی سرور است.", "2026-11-27T09:20:00+00:00"),
            msg("m3", false, "الان باز می‌شود. ممنون!", "2026-11-28T08:02:00+00:00"),
        ), draft = "یک سؤال دیگر هم دارم"), "fa")
        compose.onNodeWithText(app.getString(R.string.support_online)).assertExists()
        assertEquals("پشتیبانی", app.getString(R.string.support_title))
        assertEquals("● آنلاین — معمولاً ظرف چند دقیقه پاسخ می‌دهد", app.getString(R.string.support_online))
    }

    /** A first visit with nobody at the desk: the intro, "Away right now", a screenshot waiting to go. */
    @Test @Config(qualifiers = "+en")
    fun englishFirstVisitAway() {
        shot("support-en", SupportUiState(loaded = true, online = false,
                                          attachments = listOf(SupportUiState.Attachment("a1", "Screenshot_20261128.png")),
                                          draft = "The ledger page shows an error"), "en")
        compose.onNodeWithText(app.getString(R.string.support_intro)).assertExists()
        compose.onNodeWithText(app.getString(R.string.support_offline)).assertExists()
        assertEquals("Support", app.getString(R.string.support_title))
    }

    /** Past 30 a day: the server's words above the box (scenario DB6), dark. */
    @Test @Config(qualifiers = "+en-night")
    fun englishLimitDark() {
        val why = "You've sent 30 messages to support today. The desk has them all; write again tomorrow."
        shot("support-limit-en-dark", SupportUiState(loaded = true, online = true, draft = "One more thing",
            messages = listOf(msg("m1", false, "The ledger page shows an error.", "2026-11-28T09:00:00+00:00"),
                              msg("m2", true, "Fixed — it's live now. Try again?", "2026-11-28T09:06:00+00:00")),
            problem = SupportUiState.Problem.Said(why)), "en", dark = true)
        compose.onNodeWithText(why).assertExists()
    }

    /** The account sheet's Support row, with two replies waiting (scenario DB4). */
    @Test @Config(qualifiers = "+fa")
    fun persianAccountSupportRow() {
        compose.setContent {
            AccountantTheme(dark = false) {
                Box(Modifier.fillMaxSize().background(LocalAccountantColors.current.surface)) {
                    AccountSheetContent(
                        username = "maryam", booksName = "شرکت بازرگانی آرمان", role = "owner", devices = emptyList(),
                        lockAvailable = true, lockOn = true, onLock = {}, onRevoke = {}, onSignOut = {},
                        onSupport = {}, supportUnread = 2,
                    )
                }
            }
        }
        compose.onNodeWithText("پشتیبانی").assertExists()
        compose.onNodeWithText("۲").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/account-support-fa.png")
    }
}
