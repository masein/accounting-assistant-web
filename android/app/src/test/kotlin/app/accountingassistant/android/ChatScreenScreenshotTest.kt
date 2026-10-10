package app.accountingassistant.android

import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.data.Queued
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatScreen
import app.accountingassistant.android.ui.chat.ChatUiState
import app.accountingassistant.android.ui.components.VoucherLine
import app.accountingassistant.android.ui.theme.AccountantTheme
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import app.accountingassistant.android.ui.chat.EditVoucherSheet
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

    @Test @Config(qualifiers = "+fa")
    fun persianPostedInvoiceWithItsPdf() {
        val invoice = ChatItem.Proposal(
            id = "p2", token = "t2", title = "فاکتور فروش INV-1042 · شرکت آریا", amount = 60_000_000, currency = "IRR",
            date = "۱۸ مهر ۱۴۰۵", lines = emptyList(), needsApproval = false, phase = ChatItem.Proposal.Phase.Posted,
            voucher = "INV-1042", postedDate = "1405/07/18", undoUntil = Long.MAX_VALUE,
            document = ChatItem.File("f1", "invoice-INV-1042.pdf", "/api/mobile/v1/documents/invoices/1", "application/pdf"))
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(ChatItem.User("u1", "برای آریا فاکتور بزن: ۳ ساعت مشاوره، ساعتی ۲۰ میلیون ریال"), invoice)),
                    lang = "fa", booksName = "شرکت بازرگانی آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-invoice-pdf.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianListeningWithAFile() {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = items(true).take(2), listening = true,
                                        attachments = listOf(ChatUiState.Attachment("a1", "رسید-نانوایی.jpg"))),
                    lang = "fa", booksName = "دفتر شخصی", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(600)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-listening.png")
    }

    private fun outbox(fa: Boolean, name: String, dark: Boolean) {
        val waiting = Queued("m2", if (fa) "۱۲۰ هزار تومان چای و قند برای دفتر" else "Tea and sugar for the office, 12.00",
                             threadId = "t1", queuedAt = 1)
        val refused = Queued("m3", if (fa) "فاکتور آریا را هم بزن" else "And invoice Aria", threadId = "t1", queuedAt = 2,
                             failure = "ai_unavailable", detail = "provider unreachable")
        compose.setContent {
            AccountantTheme(dark = dark) {
                ChatScreen(
                    state = ChatUiState(
                        items = listOf(ChatItem.Earlier()) + items(fa).take(2) +
                                listOf(ChatItem.User("m2", waiting.text, clientId = "m2"), ChatItem.User("m3", refused.text, clientId = "m3")),
                        queued = mapOf("m2" to waiting, "m3" to refused), notice = ChatUiState.Notice.Offline),
                    lang = if (fa) "fa" else "en", booksName = if (fa) "شرکت بازرگانی آرمان" else "Arman Trading Ltd",
                    personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianOfflineOutbox() = outbox(fa = true, name = "chat-fa-outbox", dark = false)

    @Test @Config(qualifiers = "+en")
    fun englishOfflineOutboxDark() = outbox(fa = false, name = "chat-en-outbox-dark", dark = true)

    private fun voucherFor(fa: Boolean, approval: ChatItem.Proposal.Approval?) = ChatItem.Proposal(
        id = "p9", token = "t9", title = if (fa) "اجارهٔ دفتر، مهر" else "Office rent, October", amount = 80_000_000,
        currency = "IRR", date = if (fa) "۱۸ مهر ۱۴۰۵" else "10 Oct 2026",
        lines = listOf(VoucherLine("6112", if (fa) "هزینهٔ اجاره" else "Rent", debit = 80_000_000),
                       VoucherLine("1110", if (fa) "بانک ملت" else "Bank Mellat", credit = 80_000_000)),
        needsApproval = true, approval = approval, dateIso = "2026-10-10")

    @Test @Config(qualifiers = "+fa")
    fun persianApprovalCard() {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.Words("w1", "۱ سند منتظر تأیید شماست."),
                        voucherFor(true, ChatItem.Proposal.Approval("maryam", mine = false)),
                        voucherFor(true, null).copy(id = "p8", phase = ChatItem.Proposal.Phase.Rejected,
                                                    approval = ChatItem.Proposal.Approval("ali", mine = false)))),
                    lang = "fa", booksName = "شرکت بازرگانی آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-approval.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianEditSheet() {
        compose.setContent {
            AccountantTheme(dark = false) {
                EditVoucherSheet(voucherFor(true, null), "fa", onSave = { _, _ -> }, onDismiss = {})
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        captureScreenRoboImage("build/outputs/roborazzi/chat-fa-edit-sheet.png")
    }
}
