package app.accountingassistant.android

import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatScreen
import app.accountingassistant.android.ui.chat.ChatUiState
import app.accountingassistant.android.ui.components.VoucherLine
import app.accountingassistant.android.ui.theme.AccountantTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The real chat screen with what the server sends: a figure, a draft voucher, words. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class ChatScreenScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private fun items(fa: Boolean) = listOf(
        ChatItem.User("u1", if (fa) "موجودی بانک چقدره؟ اجارهٔ مهر رو هم ثبت کن، ۸۰ میلیون" else "What's in the bank? And record October's rent, 800"),
        ChatItem.Figure("f1", if (fa) "۱۱۱۰ بانک ملت" else "1200 Barclays", if (fa) 1_240_000_000 else 12_400, if (fa) "IRR" else "GBP"),
        ChatItem.Proposal(
            id = "p1", token = "t1", title = if (fa) "اجارهٔ دفتر، مهر" else "Office rent, October",
            amount = if (fa) 80_000_000 else 800, currency = if (fa) "IRR" else "GBP",
            date = if (fa) "۱۸ مهر ۱۴۰۵" else "10 Oct 2026",
            lines = listOf(VoucherLine(if (fa) "6112" else "7100", if (fa) "اجاره" else "Rent", debit = if (fa) 80_000_000 else 800),
                           VoucherLine(if (fa) "1110" else "1200", if (fa) "بانک ملت" else "Barclays", credit = if (fa) 80_000_000 else 800)),
            needsApproval = false,
        ),
        ChatItem.Words("w1", if (fa) "موجودی و پیش‌نویس سند اجاره آماده است. برای ثبت، «ثبت» را بزنید." else "Here's the balance, and the rent ready to post."),
    )

    private fun shot(name: String, fa: Boolean, dark: Boolean) {
        compose.setContent {
            AccountantTheme(dark = dark) {
                ChatScreen(
                    state = ChatUiState(items = items(fa)), lang = if (fa) "fa" else "en",
                    booksName = if (fa) "شرکت بازرگانی آرمان" else "Thames Studio Ltd", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianLight() = shot("chat-fa-light", fa = true, dark = false)

    @Test @Config(qualifiers = "+fa-night")
    fun persianDark() = shot("chat-fa-dark", fa = true, dark = true)

    @Test @Config(qualifiers = "+en")
    fun englishLight() = shot("chat-en-light", fa = false, dark = false)
}
