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
        ChatItem.Figure("f1", if (fa) "۱۱۱۰ بانک ملت" else "1200 Barclays", java.math.BigDecimal(if (fa) "1240000000" else "12400.75"), if (fa) "IRR" else "GBP"),
        ChatItem.Proposal(
            id = "p1", token = "t1", title = if (fa) "اجارهٔ دفتر، مهر" else "Office rent, October",
            amount = java.math.BigDecimal(if (fa) 80_000_000 else 800), currency = if (fa) "IRR" else "GBP",
            date = if (fa) "۱۸ مهر ۱۴۰۵" else "10 Oct 2026",
            lines = listOf(VoucherLine(if (fa) "6112" else "7100", if (fa) "اجاره" else "Rent", debit = java.math.BigDecimal(if (fa) 80_000_000 else 800)),
                           VoucherLine(if (fa) "1110" else "1200", if (fa) "بانک ملت" else "Barclays", credit = java.math.BigDecimal(if (fa) 80_000_000 else 800))),
            needsApproval = false, editable = true,
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
            id = "p2", token = "t2", title = "فاکتور فروش INV-1042 · شرکت آریا", amount = java.math.BigDecimal(60_000_000), currency = "IRR",
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
        id = "p9", token = "t9", title = if (fa) "اجارهٔ دفتر، مهر" else "Office rent, October", amount = java.math.BigDecimal(80_000_000),
        currency = "IRR", date = if (fa) "۱۸ مهر ۱۴۰۵" else "10 Oct 2026",
        lines = listOf(VoucherLine("6112", if (fa) "هزینهٔ اجاره" else "Rent", debit = 80_000_000),
                       VoucherLine("1110", if (fa) "بانک ملت" else "Bank Mellat", credit = java.math.BigDecimal(80_000_000))),
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

    @Test @Config(qualifiers = "+fa")
    fun persianStatementCard() {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.User("u1", "صورتحساب ملت شهریور", files = listOf("mellat-1405-06.xlsx")),
                        ChatItem.Statement("st1", "s9", "ملت", "2026-08-23", "2026-09-21", 12,
                                           mapOf("matched" to 8, "unrecorded" to 2, "needs_confirmation" to 1, "amount_mismatch" to 1),
                                           gap = java.math.BigDecimal(90_000), currency = "IRR", clean = false),
                        ChatItem.Words("w1", "پس از این، ۱ ردیف دیگر مانده.", nextStatement = "s9"))),
                    lang = "fa", booksName = "شرکت بازرگانی آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-statement.png")
    }

    private fun forecast(name: String, fa: Boolean, dark: Boolean) {
        val weeks = listOf("2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23",
                           "2026-11-30", "2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28", "2027-01-04")
        val closing = listOf(1_100L, 980, 860, 640, 420, 180, 40, -120, 90, 260, 380, 500, 610).map { it * 1_000_000 }
        val chart = ChatItem.Chart("c1", "cash_forecast", if (fa) "نقدینگی هفته‌های آینده" else "Cash, the coming weeks", "IRR",
                                   listOf(ChatItem.Chart.Point("2026-10-10", 1_240_000_000)) + weeks.zip(closing) { w, v -> ChatItem.Chart.Point(w, v) },
                                   ChatItem.Chart.Point("2026-11-30", -120_000_000), "2026-11-30")
        compose.setContent {
            AccountantTheme(dark = dark) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.User("u1", if (fa) "پیش‌بینی نقدینگی" else "Will we have enough cash?"), chart,
                        ChatItem.Words("w1", if (fa) "نقدینگی ۱۳ هفتهٔ آینده، به تخمین: از ۱٬۲۴۰٬۰۰۰٬۰۰۰ به ۶۱۰٬۰۰۰٬۰۰۰ ریال؛ کمترین، −۱۲۰٬۰۰۰٬۰۰۰، در هفتهٔ ۹ آذر ۱۴۰۵. در هفتهٔ ۹ آذر ۱۴۰۵ منفی می‌شود."
                                             else "Cash over the next 13 weeks, an estimate: from 1,240,000,000 to 610,000,000 IRR; the lowest, −120,000,000, in the week of 30 Nov 2026. It goes below zero in the week of 30 Nov 2026."))),
                    lang = if (fa) "fa" else "en", booksName = if (fa) "شرکت بازرگانی آرمان" else "Arman Trading Ltd", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianForecastChart() = forecast("chat-fa-forecast", fa = true, dark = false)

    @Test @Config(qualifiers = "+en-night")
    fun englishForecastChartDark() = forecast("chat-en-forecast-dark", fa = false, dark = true)

    @Test @Config(qualifiers = "+fa")
    fun persianFlatForecastUnderAnEnglishQuestion() {
        val flat = ChatItem.Chart("c2", "cash_forecast", "نقدینگی هفته‌های آینده", "IRR",
                                  (0..13).map { ChatItem.Chart.Point("2026-10-${10 + it}", 0) }, ChatItem.Chart.Point("2026-10-12", 0), null)
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(ChatItem.User("u1", "will we have enough cash?"), flat)),
                    lang = "fa", booksName = "دفتر شخصی", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-forecast-flat.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianPayHistoryWithThePayslip() {
        val table = ChatItem.Table("t1", "pay_history", listOf(
            ChatItem.Table.Row("آبان ۱۴۰۵", "ثبت‌شده", java.math.BigDecimal(84_000_000), "IRR"),
            ChatItem.Table.Row("مهر ۱۴۰۵", "پرداخت‌شده", java.math.BigDecimal(84_000_000), "IRR"),
            ChatItem.Table.Row("شهریور ۱۴۰۵", "پرداخت‌شده", java.math.BigDecimal(82_500_000), "IRR")), title = "سارا احمدی")
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.User("u1", "فیش حقوقی سارا"), table,
                        ChatItem.File("f1", "payslip-Sara_Ahmadi.pdf", "/api/mobile/v1/documents/payslips/r/e", "application/pdf"),
                        ChatItem.Words("w1", "سارا در سه ماه گذشته حقوق گرفته؛ فیش آبان پیوست است."))),
                    lang = "fa", booksName = "شرکت بازرگانی آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-payslip.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianNetWorth() {
        val months = listOf("2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31",
                            "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-10")
        val values = listOf(610L, 640, 655, 690, 700, 735, 760, 790, 820, 870, 910, 950).map { it * 1_000_000 }
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.User("u1", "دارایی خالص من چقدره؟"),
                        ChatItem.Figure("f1", "دارایی خالص", java.math.BigDecimal(950_000_000), "IRR"),
                        ChatItem.Chart("c1", "net_worth", "دارایی خالص، دوازده ماه گذشته", "IRR",
                                       months.zip(values) { m, v -> ChatItem.Chart.Point(m, v) }, null, null),
                        ChatItem.Table("t1", "holdings", listOf(
                            ChatItem.Table.Row("طلا", "۲۵٫۵ گرم طلا", java.math.BigDecimal(600_000_000), "IRR", gain = java.math.BigDecimal(180_000_000)),
                            ChatItem.Table.Row("ارز", "۴۰۰ دلار", java.math.BigDecimal(240_000_000), "IRR", gain = java.math.BigDecimal(35_000_000)))),
                        ChatItem.Words("w1", "دارایی خالص شما امروز: ۹۵۰٬۰۰۰٬۰۰۰ ریال؛ طلا و ارز به نرخ امروز ۲۱۵٬۰۰۰٬۰۰۰ بیشتر می‌ارزند."))),
                    lang = "fa", booksName = "دفتر شخصی", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-net-worth.png")
    }

    /** Scenario N37: a statement going in pieces on a poor line, and a photo being attached. */
    @Test @Config(qualifiers = "+fa")
    fun persianAStatementGoingInPieces() = pieces("chat-fa-upload", dark = false, lang = "fa",
        ChatItem.User("u1", "صورتحساب مهر", files = listOf("صورتحساب-ملت-مهر.pdf")))

    @Test @Config(qualifiers = "+en-night")
    fun englishAStatementGoingInPiecesDark() = pieces("chat-en-upload-dark", dark = true, lang = "en",
        ChatItem.User("u1", "October's statement", files = listOf("mellat-october.pdf")))

    private fun pieces(name: String, dark: Boolean, lang: String, user: ChatItem.User) {
        compose.setContent {
            AccountantTheme(dark = dark) {
                ChatScreen(
                    state = ChatUiState(items = listOf(user, ChatItem.Thinking(upload = 0.4f)),
                                        uploading = true, uploadProgress = 0.62f),
                    lang = lang, booksName = if (lang == "fa") "شرکت بازرگانی آرمان" else "Arman Trading", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    /** Arabic in Western digits everywhere (the owner's call, 2026-10-11): figure, card, dates and reply. */
    @Test @Config(qualifiers = "+ar")
    fun arabicFiguresInWesternDigits() {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(
                        ChatItem.User("u1", "كم لدينا من المال؟"),
                        ChatItem.Figure("f1", "النقد والبنوك", java.math.BigDecimal(49_600_000), "IRR"),
                        ChatItem.Statement("st1", "s9", "ملت", "2026-10-02", "2026-10-10", 18,
                                           mapOf("matched" to 12, "unrecorded" to 6), gap = java.math.BigDecimal(100_400_000), currency = "IRR", clean = false),
                        ChatItem.Words("w1", "قرأت كشف حساب ملت: 18 سطراً (من 2 أكتوبر 2026 إلى 10 أكتوبر 2026) وقارنتها بالدفاتر."))),
                    lang = "ar", booksName = "شركة آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-ar-western-digits.png")
    }
}
