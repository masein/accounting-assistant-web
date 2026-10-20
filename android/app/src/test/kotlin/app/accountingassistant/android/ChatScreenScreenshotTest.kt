package app.accountingassistant.android

import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.isPopup
import app.accountingassistant.android.ui.chat.Quote
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

    /** The owner's phone, in English, personal books: a lunch in Omani rials, and a card that names Amir (AK1, AK4). */
    @Test
    fun englishPersonalCardsSayWhoTheyName() {
        val lunch = ChatItem.Proposal(
            id = "p1", token = "t1", title = "ناهار با امیر از کیف پول قشنگم", amount = java.math.BigDecimal("7.000"),
            currency = "OMR", date = "۲۸ مهر ۱۴۰۵",
            lines = listOf(VoucherLine("6180", "تفریح و رستوران", debit = java.math.BigDecimal("7.000")),
                           VoucherLine("112001", "کیف پول قشنگم", java.math.BigDecimal.ZERO, java.math.BigDecimal("7.000"))),
            needsApproval = false)
        val back = ChatItem.Proposal(
            id = "p2", token = "t2", title = "پرداخت به امیر", amount = java.math.BigDecimal("20.000"),
            currency = "OMR", date = "۲۸ مهر ۱۴۰۵",
            lines = listOf(VoucherLine("2130", "بدهی به دیگران", debit = java.math.BigDecimal("20.000")),
                           VoucherLine("112001", "کیف پول قشنگم", java.math.BigDecimal.ZERO, java.math.BigDecimal("20.000"))),
            needsApproval = false, people = listOf(ChatItem.Proposal.Person("امیر", false)))
        val sara = back.copy(id = "p3", token = "t3", title = "سارا پول تاکسی رو داد",
                             lines = listOf(VoucherLine("6130", "حمل‌ونقل", debit = java.math.BigDecimal("3.500")),
                                            VoucherLine("2130", "بدهی به دیگران", java.math.BigDecimal.ZERO, java.math.BigDecimal("3.500"))),
                             amount = java.math.BigDecimal("3.500"), people = listOf(ChatItem.Proposal.Person("سارا", true)))
        compose.setContent {
            AccountantTheme(dark = true) {
                ChatScreen(
                    state = ChatUiState(items = listOf(lunch, back, sara)),
                    lang = "en", booksName = "masein", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(600)
        compose.onNodeWithText("Person: امیر").assertExists()
        compose.onNodeWithText("New person: سارا").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-en-personal-people.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianCompanyCardSaysItsParty() {
        val rent = items(true)[2] as ChatItem.Proposal
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = listOf(rent.copy(people = listOf(ChatItem.Proposal.Person("املاک پارس", true))))),
                    lang = "fa", booksName = "شرکت بازرگانی آرمان", personalBooks = false,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(600)
        compose.onNodeWithText("طرف حساب جدید: املاک پارس").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-company-party.png")
    }

    /** The week's review as the server says it (app/services/proactive_reviews.py, scenarios AL1, AL7, AL9). */
    @Test @Config(qualifiers = "+fa")
    fun persianWeeklyReview() {
        val blocks = kotlinx.serialization.json.Json.parseToJsonElement(REVIEW_FA) as kotlinx.serialization.json.JsonArray
        val items = blocks.map { app.accountingassistant.android.ui.chat.parseBlock(it as kotlinx.serialization.json.JsonObject) }
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = items),
                    lang = "fa", booksName = "masein", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(2500)                                     // grown, counted, settled
        compose.onNodeWithText("۴۱٫۵۰۰").assertExists()
        compose.onNodeWithText("برای تفریح و رستوران بودجهٔ ماهانهٔ ۳۰٫۰۰۰ ریال عمان بگذار").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-weekly-review.png")
    }

    /** «Can I afford it?»: a car for 300,000,000 — save first, with its goal to make (AM4, AM9). */
    @Test @Config(qualifiers = "+fa")
    fun persianCanIAffordIt() {
        val blocks = kotlinx.serialization.json.Json.parseToJsonElement(AFFORD_FA) as kotlinx.serialization.json.JsonArray
        val items = listOf(ChatItem.User("u1", "می‌خوام یه ماشین ۳۰ میلیون تومنی بخرم، می‌تونم؟")) +
            blocks.map { app.accountingassistant.android.ui.chat.parseBlock(it as kotlinx.serialization.json.JsonObject) }
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(
                    state = ChatUiState(items = items),
                    lang = "fa", booksName = "masein", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(2500)
        compose.onNodeWithText("چطور خرجم را کم کنم؟").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-fa-afford.png")
    }

    /** A good month: the trophy, the savings ring filled, the categories against last month (AL17). */
    @Test
    fun englishMonthlyReviewInTheDark() {
        val blocks = kotlinx.serialization.json.Json.parseToJsonElement(REVIEW_MONTH_EN) as kotlinx.serialization.json.JsonArray
        val items = blocks.map { app.accountingassistant.android.ui.chat.parseBlock(it as kotlinx.serialization.json.JsonObject) }
        assert(items.single() is ChatItem.Review)
        compose.mainClock.autoAdvance = false                                     // to catch it moving
        compose.setContent {
            AccountantTheme(dark = true) {
                ChatScreen(
                    state = ChatUiState(items = items),
                    lang = "en", booksName = "Holly", personalBooks = true,
                    onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {},
                )
            }
        }
        compose.mainClock.advanceTimeBy(800)                                      // mid-way: the bars still growing
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-en-monthly-review-moving.png")
        compose.mainClock.advanceTimeBy(2500)
        compose.onNodeWithText("Saved").assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-en-monthly-review.png")
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

    // --- reply, copy, share (scenarios AC1–AC9) --------------------------------------------------

    private val walletCard = ChatItem.Proposal(
        id = "p9", token = "t9", title = "کیف پول سبز — موجودی اولیه", amount = java.math.BigDecimal("77.5"), currency = "OMR",
        date = "۲۴ مهر ۱۴۰۵",
        lines = listOf(VoucherLine("112001", "کیف پول سبز", debit = java.math.BigDecimal("77.5")),
                       VoucherLine("3110", "خالص دارایی اولیه", credit = java.math.BigDecimal("77.5"))),
        needsApproval = false)

    private fun personal(state: ChatUiState, lang: String) {
        compose.setContent {
            AccountantTheme(dark = false) {
                ChatScreen(state = state, lang = lang, booksName = "masein", personalBooks = true,
                           onDraft = {}, onSend = {}, onConfirm = {}, onCancel = {}, onUndo = {}, onBooks = {})
            }
        }
        compose.mainClock.advanceTimeBy(1500)
    }

    /** The owner's screenshot: the app in English, a Persian card in a person's books, a reply on its way. */
    @Test @Config(qualifiers = "+en")
    fun aPersonsCardInAnEnglishAppAndAReply() {
        personal(ChatUiState(items = listOf(
            ChatItem.User("u1", "دفاتر روی ریال ایران بماند و کیف پول با ریال عمان ثبت شود"),
            walletCard,
            ChatItem.Words("w1", "ثبت موجودی اولیه کیف پول سبز به مبلغ ۷۷٫۵۰۰ ریال عمان برای ۲۴ مهر ۱۴۰۵ آماده است."),
            ChatItem.User("u2", "مبلغش رو ۸۰ کن", quote = Quote(false, "کیف پول سبز — موجودی اولیه · 77.500 OMR", "t9"))),
            replyTo = Quote(false, "ثبت موجودی اولیه کیف پول سبز به مبلغ ۷۷٫۵۰۰ ریال عمان برای ۲۴ مهر ۱۴۰۵ آماده است.")), "en")
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/chat-en-personal-reply.png")
    }

    /** Holding an answer down: Reply, Copy, Share where the finger is. */
    @Test @Config(qualifiers = "+fa")
    fun holdingAnAnswerOpensReplyCopyShare() {
        personal(ChatUiState(items = listOf(ChatItem.User("u1", "چقدر پول دارم؟"), walletCard,
            ChatItem.Words("w1", "در **کیف پول سبز** ۷۷٫۵۰۰ ریال عمان دارید."))), "fa")
        compose.onNodeWithText("ریال عمان دارید", substring = true).performTouchInput { longClick(center) }
        compose.mainClock.advanceTimeBy(800)
        compose.onNodeWithText("پاسخ").assertExists()
        compose.onNode(isPopup()).captureRoboImage("build/outputs/roborazzi/chat-fa-hold-menu.png")
    }
}

// what the server sends for a week and a month (docs/contracts/conversations/review-*.json; Kotlin source stays ASCII)
private const val REVIEW_FA = """[{"type": "review", "id": "review:weekly-1", "kind": "weekly", "mood": "watch", "emoji": "\ud83d\udc40", "title": "\u0645\u0631\u0648\u0631 \u0647\u0641\u062a\u0647\u060c \u06f2\u06f5 \u0645\u0647\u0631 \u062a\u0627 \u06f1 \u0622\u0628\u0627\u0646 \u06f1\u06f4\u06f0\u06f5", "currency": "OMR", "headline": {"label": "\u062e\u0631\u062c \u0627\u06cc\u0646 \u0647\u0641\u062a\u0647", "value": 41.5, "display": "\u06f4\u06f1\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "delta": {"pct": 37, "dir": "up", "good": false, "text": "\u06f3\u06f7\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0645\u0639\u0645\u0648\u0644"}}, "chart": {"title": "\u067e\u0646\u062c \u0647\u0641\u062a\u0647\u0654 \u0627\u062e\u06cc\u0631", "bars": [{"label": "\u06f2\u06f8 \u0634\u0647\u0631\u06cc\u0648\u0631", "value": 28, "display": "\u06f2\u06f8\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "current": false}, {"label": "\u06f4 \u0645\u0647\u0631", "value": 31, "display": "\u06f3\u06f1\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "current": false}, {"label": "\u06f1\u06f1 \u0645\u0647\u0631", "value": 30, "display": "\u06f3\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "current": false}, {"label": "\u06f1\u06f8 \u0645\u0647\u0631", "value": 32, "display": "\u06f3\u06f2\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "current": false}, {"label": "\u0627\u06cc\u0646 \u0647\u0641\u062a\u0647", "value": 41.5, "display": "\u06f4\u06f1\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "current": true}], "usual": 30.25, "usual_label": "\u0645\u0639\u0645\u0648\u0644", "legend": ["\u062e\u0631\u062c"]}, "ring": null, "categories": [{"name": "\u062a\u0641\u0631\u06cc\u062d \u0648 \u0631\u0633\u062a\u0648\u0631\u0627\u0646", "value": 18.5, "display": "\u06f1\u06f8\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "sub": "\u06f1\u06f6\u06f4\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0645\u0639\u0645\u0648\u0644", "dir": "up", "width": 1.0, "usual_at": 0.3784}, {"name": "\u062e\u0648\u0631\u0627\u06a9 \u0648 \u0633\u0648\u067e\u0631\u0645\u0627\u0631\u06a9\u062a", "value": 15, "display": "\u06f1\u06f5\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646", "sub": "\u0645\u0639\u0645\u0648\u0644", "dir": "same", "width": 0.8108, "usual_at": 0.8378}], "categories_title": "\u06a9\u062c\u0627 \u062e\u0631\u062c \u0634\u062f", "facts": ["\u062f\u0631\u0622\u0645\u062f: \u06f6\u06f0\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646.", "\u0628\u06cc\u0634\u062a\u0631\u06cc\u0646 \u062e\u0631\u062c: \u0634\u0627\u0645 \u062a\u0648\u0644\u062f\u060c \u06f1\u06f2\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 (\u06f2\u06f9 \u0645\u0647\u0631 \u06f1\u06f4\u06f0\u06f5).", "\u0628\u0647 \u0627\u0645\u06cc\u0631 \u06f2\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u062f\u0647\u06a9\u0627\u0631\u06cc\u062f."], "ideas": [{"key": "goal_payday", "icon": "\ud83d\udcb0", "text": "\u0627\u06cc\u0646 \u0647\u0641\u062a\u0647 \u062f\u0631\u0622\u0645\u062f \u062f\u0627\u0634\u062a\u06cc\u062f \u2014 \u06f8\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u0631\u0627\u06cc \u00ab\u0644\u067e\u200c\u062a\u0627\u067e\u00bb \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u061f", "ask": "\u06f8\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u0631\u0627\u06cc \u0647\u062f\u0641 \u0644\u067e\u200c\u062a\u0627\u067e \u0628\u0647 \u067e\u0633\u200c\u0627\u0646\u062f\u0627\u0632 \u0627\u0631\u0632\u06cc \u0645\u0646\u062a\u0642\u0644 \u06a9\u0646"}, {"key": "hot_category", "icon": "\ud83d\udd25", "text": "\u062e\u0631\u062c \u00ab\u062a\u0641\u0631\u06cc\u062d \u0648 \u0631\u0633\u062a\u0648\u0631\u0627\u0646\u00bb \u06f1\u06f6\u06f4\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0645\u0639\u0645\u0648\u0644 \u0628\u0648\u062f \u2014 \u0628\u0648\u062f\u062c\u0647\u0654 \u0645\u0627\u0647\u0627\u0646\u0647 \u0628\u0631\u0627\u06cc\u0634 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u061f", "ask": "\u0628\u0631\u0627\u06cc \u062a\u0641\u0631\u06cc\u062d \u0648 \u0631\u0633\u062a\u0648\u0631\u0627\u0646 \u0628\u0648\u062f\u062c\u0647\u0654 \u0645\u0627\u0647\u0627\u0646\u0647\u0654 \u06f3\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u06af\u0630\u0627\u0631"}], "ideas_title": "\u067e\u06cc\u0634\u0646\u0647\u0627\u062f\u0647\u0627", "note": "\u0647\u0631 \u0647\u0641\u062a\u0647 \u0648 \u0647\u0631 \u0645\u0627\u0647 \u06cc\u06a9\u06cc \u0627\u0632 \u0627\u06cc\u0646\u200c\u0647\u0627 \u0645\u06cc\u200c\u0622\u06cc\u062f. \u0627\u06af\u0631 \u0646\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u06cc\u062f\u060c \u0628\u06af\u0648\u06cc\u06cc\u062f \u00ab\u0645\u0631\u0648\u0631 \u0647\u0641\u062a\u06af\u06cc \u0646\u0641\u0631\u0633\u062a\u00bb.", "fallback_text": "\u0645\u0631\u0648\u0631 \u0647\u0641\u062a\u0647\u060c \u06f2\u06f5 \u0645\u0647\u0631 \u062a\u0627 \u06f1 \u0622\u0628\u0627\u0646 \u06f1\u06f4\u06f0\u06f5\n\u0627\u06cc\u0646 \u0647\u0641\u062a\u0647 \u06f4\u06f1\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u062e\u0631\u062c \u06a9\u0631\u062f\u06cc\u062f\u061b \u06f3\u06f7\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0647\u0641\u062a\u0647\u200c\u0647\u0627\u06cc \u0645\u0639\u0645\u0648\u0644\u062a\u0627\u0646 (\u06f3\u06f0\u066b\u06f2\u06f5\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646).\n\u062f\u0631\u0622\u0645\u062f: \u06f6\u06f0\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646.\n\u0628\u06cc\u0634\u062a\u0631\u06cc\u0646 \u062e\u0631\u062c: \u0634\u0627\u0645 \u062a\u0648\u0644\u062f\u060c \u06f1\u06f2\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 (\u06f2\u06f9 \u0645\u0647\u0631 \u06f1\u06f4\u06f0\u06f5).\n\u0628\u0647 \u0627\u0645\u06cc\u0631 \u06f2\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u062f\u0647\u06a9\u0627\u0631\u06cc\u062f.\n\u06a9\u062c\u0627 \u062e\u0631\u062c \u0634\u062f:\n\u2022 \u062a\u0641\u0631\u06cc\u062d \u0648 \u0631\u0633\u062a\u0648\u0631\u0627\u0646: \u06f1\u06f8\u066b\u06f5\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 (\u06f1\u06f6\u06f4\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0645\u0639\u0645\u0648\u0644)\n\u2022 \u062e\u0648\u0631\u0627\u06a9 \u0648 \u0633\u0648\u067e\u0631\u0645\u0627\u0631\u06a9\u062a: \u06f1\u06f5\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 (\u0645\u0639\u0645\u0648\u0644)\n\u0627\u06cc\u0646 \u0647\u0641\u062a\u0647 \u062f\u0631\u0622\u0645\u062f \u062f\u0627\u0634\u062a\u06cc\u062f \u2014 \u06f8\u06f0\u066b\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0639\u0645\u0627\u0646 \u0628\u0631\u0627\u06cc \u00ab\u0644\u067e\u200c\u062a\u0627\u067e\u00bb \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u061f\n\u062e\u0631\u062c \u00ab\u062a\u0641\u0631\u06cc\u062d \u0648 \u0631\u0633\u062a\u0648\u0631\u0627\u0646\u00bb \u06f1\u06f6\u06f4\u066a \u0628\u06cc\u0634\u062a\u0631 \u0627\u0632 \u0645\u0639\u0645\u0648\u0644 \u0628\u0648\u062f \u2014 \u0628\u0648\u062f\u062c\u0647\u0654 \u0645\u0627\u0647\u0627\u0646\u0647 \u0628\u0631\u0627\u06cc\u0634 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u061f\n\u0647\u0631 \u0647\u0641\u062a\u0647 \u0648 \u0647\u0631 \u0645\u0627\u0647 \u06cc\u06a9\u06cc \u0627\u0632 \u0627\u06cc\u0646\u200c\u0647\u0627 \u0645\u06cc\u200c\u0622\u06cc\u062f. \u0627\u06af\u0631 \u0646\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u06cc\u062f\u060c \u0628\u06af\u0648\u06cc\u06cc\u062f \u00ab\u0645\u0631\u0648\u0631 \u0647\u0641\u062a\u06af\u06cc \u0646\u0641\u0631\u0633\u062a\u00bb."}]"""
private const val REVIEW_MONTH_EN = """[{"type": "review", "id": "review:monthly-1", "kind": "monthly", "mood": "good", "emoji": "\ud83c\udfc6", "title": "Your month: Sep 2026", "currency": "GBP", "headline": {"label": "Spent in Sep 2026", "value": 2480.5, "display": "2,480.50 Pound sterling", "delta": {"pct": 6, "dir": "down", "good": true, "text": "6% less than last month"}}, "chart": {"title": "The last four months", "bars": [{"label": "Jun", "value": 2550, "display": "2,550.00 Pound sterling", "current": false}, {"label": "Jul", "value": 2700, "display": "2,700.00 Pound sterling", "current": false}, {"label": "Aug", "value": 2650, "display": "2,650.00 Pound sterling", "current": false}, {"label": "Sep", "value": 2480.5, "display": "2,480.50 Pound sterling", "current": true}], "usual": null, "usual_label": "usual", "legend": ["Spending"]}, "ring": {"pct": 22.5, "label": "Saved", "display": "719.50 Pound sterling", "text": "22% of income"}, "categories": [{"name": "Rent", "value": 1200, "display": "1,200.00 Pound sterling", "sub": "as usual", "dir": "same", "width": 1.0, "usual_at": 1.0}, {"name": "Food & groceries", "value": 410.25, "display": "410.25 Pound sterling", "sub": "as usual", "dir": "same", "width": 0.3419, "usual_at": 0.3167}], "categories_title": "Where it went", "facts": ["Spending is 6% less than Aug 2026.", "2 of 2 budgets kept.", "Net worth rose by 719.50 Pound sterling.", "Goals: Holiday 40%."], "ideas": [], "ideas_title": "Ideas", "note": null, "fallback_text": "Your month: Sep 2026\nIncome 3,200.00 Pound sterling, spending 2,480.50 Pound sterling, saved 719.50 Pound sterling (22% of income).\nSpending is 6% less than Aug 2026.\n2 of 2 budgets kept.\nNet worth rose by 719.50 Pound sterling.\nGoals: Holiday 40%.\nWhere it went:\n\u2022 Rent: 1,200.00 Pound sterling (as usual)\n\u2022 Food & groceries: 410.25 Pound sterling (as usual)"}]"""
private const val AFFORD_FA = """[{"type": "review", "kind": "afford", "mood": "watch", "emoji": "\ud83d\udc37", "title": "\u0645\u0627\u0634\u06cc\u0646 \u0628\u0647 \u0642\u06cc\u0645\u062a \u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646\u061f", "currency": "IRR", "headline": {"label": "\u0645\u0627\u0647\u06cc \u06f1\u06f5\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646 \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u060c \u062d\u062f\u0648\u062f \u06f1\u06f4 \u0645\u0627\u0647", "value": 300000000, "display": "\u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646", "delta": null}, "chart": {"title": "\u067e\u0648\u0644\u062a\u0627\u0646\u060c \u0642\u0628\u0644 \u0648 \u0628\u0639\u062f", "usual": 25000000, "usual_label": "\u067e\u0634\u062a\u0648\u0627\u0646\u0647", "legend": [], "bars": [{"label": "\u0627\u0644\u0627\u0646", "value": 120000000, "display": "\u06f1\u06f2\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646", "current": false}, {"label": "\u0628\u0639\u062f \u0627\u0632 \u062e\u0631\u06cc\u062f", "value": 0, "display": "-\u06f1\u06f8\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646", "current": true}]}, "ring": {"pct": 31.7, "label": "\u0622\u0632\u0627\u062f \u0628\u0631\u0627\u06cc \u062e\u0631\u062c", "display": "\u06f9\u06f5\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646", "text": "\u06f3\u06f2\u066a \u0627\u0632 \u0642\u06cc\u0645\u062a"}, "categories_title": null, "categories": [], "facts": ["\u0647\u06cc\u0686 \u0627\u0642\u0633\u0627\u0637\u06cc \u062a\u0627 \u06f2\u06f4 \u0645\u0627\u0647 \u0628\u0627 \u0622\u0646\u0686\u0647 \u0647\u0631 \u0645\u0627\u0647 \u0627\u0636\u0627\u0641\u0647 \u0645\u06cc\u200c\u0645\u0627\u0646\u062f \u062c\u0648\u0631 \u0646\u0645\u06cc\u200c\u0634\u0648\u062f.", "\u062f\u0631\u0622\u0645\u062f \u062d\u062f\u0648\u062f \u0645\u0627\u0647\u06cc \u06f4\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646", "\u062e\u0631\u062c \u062d\u062f\u0648\u062f \u0645\u0627\u0647\u06cc \u06f2\u06f5\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646"], "ideas_title": "\u067e\u06cc\u0634\u0646\u0647\u0627\u062f\u0647\u0627", "ideas": [{"key": "goal", "icon": "\ud83c\udfaf", "text": "\u06cc\u06a9 \u0647\u062f\u0641 \u067e\u0633\u200c\u0627\u0646\u062f\u0627\u0632 \u0628\u0633\u0627\u0632\u06cc\u062f: \u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646 \u062a\u0627 \u06f7 \u062f\u06cc \u06f1\u06f4\u06f0\u06f6\u061b \u0647\u0631 \u0647\u0641\u062a\u0647 \u06cc\u0627\u062f\u0622\u0648\u0631\u06cc \u0645\u06cc\u200c\u06a9\u0646\u0645.", "ask": "\u06cc\u06a9 \u0647\u062f\u0641 \u067e\u0633\u200c\u0627\u0646\u062f\u0627\u0632 \u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646 \u0628\u0631\u0627\u06cc \u0645\u0627\u0634\u06cc\u0646 \u062a\u0627 \u06f7 \u062f\u06cc \u06f1\u06f4\u06f0\u06f6 \u0628\u0633\u0627\u0632"}, {"key": "spend_less", "icon": "\ud83d\udca1", "text": "\u0628\u0628\u06cc\u0646\u06cc\u062f \u067e\u0648\u0644 \u0647\u0631 \u0645\u0627\u0647 \u06a9\u062c\u0627 \u0645\u06cc\u200c\u0631\u0648\u062f \u0648 \u06a9\u062c\u0627 \u0645\u06cc\u200c\u0634\u0648\u062f \u06a9\u0645 \u06a9\u0631\u062f.", "ask": "\u0686\u0637\u0648\u0631 \u062e\u0631\u062c\u0645 \u0631\u0627 \u06a9\u0645 \u06a9\u0646\u0645\u061f"}], "note": null, "fallback_text": "\u0645\u0627\u0634\u06cc\u0646 \u0628\u0647 \u0642\u06cc\u0645\u062a \u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646\u061f\n\u0645\u0627\u0647\u06cc \u06f1\u06f5\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646 \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f\u060c \u062d\u062f\u0648\u062f \u06f1\u06f4 \u0645\u0627\u0647\n\u0647\u06cc\u0686 \u0627\u0642\u0633\u0627\u0637\u06cc \u062a\u0627 \u06f2\u06f4 \u0645\u0627\u0647 \u0628\u0627 \u0622\u0646\u0686\u0647 \u0647\u0631 \u0645\u0627\u0647 \u0627\u0636\u0627\u0641\u0647 \u0645\u06cc\u200c\u0645\u0627\u0646\u062f \u062c\u0648\u0631 \u0646\u0645\u06cc\u200c\u0634\u0648\u062f.\n\u062f\u0631\u0622\u0645\u062f \u062d\u062f\u0648\u062f \u0645\u0627\u0647\u06cc \u06f4\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646\n\u062e\u0631\u062c \u062d\u062f\u0648\u062f \u0645\u0627\u0647\u06cc \u06f2\u06f5\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646\n\u06cc\u06a9 \u0647\u062f\u0641 \u067e\u0633\u200c\u0627\u0646\u062f\u0627\u0632 \u0628\u0633\u0627\u0632\u06cc\u062f: \u06f3\u06f0\u06f0\u066c\u06f0\u06f0\u06f0\u066c\u06f0\u06f0\u06f0 \u0631\u06cc\u0627\u0644 \u0627\u06cc\u0631\u0627\u0646 \u062a\u0627 \u06f7 \u062f\u06cc \u06f1\u06f4\u06f0\u06f6\u061b \u0647\u0631 \u0647\u0641\u062a\u0647 \u06cc\u0627\u062f\u0622\u0648\u0631\u06cc \u0645\u06cc\u200c\u06a9\u0646\u0645.\n\u0628\u0628\u06cc\u0646\u06cc\u062f \u067e\u0648\u0644 \u0647\u0631 \u0645\u0627\u0647 \u06a9\u062c\u0627 \u0645\u06cc\u200c\u0631\u0648\u062f \u0648 \u06a9\u062c\u0627 \u0645\u06cc\u200c\u0634\u0648\u062f \u06a9\u0645 \u06a9\u0631\u062f.", "id": "check_affordability:call_0", "tool": "check_affordability"}, {"type": "text", "id": "text:0", "text": "\u0641\u0639\u0644\u0627\u064b \u0646\u0647 \u0628\u0647\u200c\u0635\u0648\u0631\u062a \u0646\u0642\u062f\u061b \u0645\u0627\u0647\u06cc \u062d\u062f\u0648\u062f \u06f1\u06f5 \u0645\u06cc\u0644\u06cc\u0648\u0646 \u0631\u06cc\u0627\u0644 \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f.", "fallback_text": "\u0641\u0639\u0644\u0627\u064b \u0646\u0647 \u0628\u0647\u200c\u0635\u0648\u0631\u062a \u0646\u0642\u062f\u061b \u0645\u0627\u0647\u06cc \u062d\u062f\u0648\u062f \u06f1\u06f5 \u0645\u06cc\u0644\u06cc\u0648\u0646 \u0631\u06cc\u0627\u0644 \u06a9\u0646\u0627\u0631 \u0628\u06af\u0630\u0627\u0631\u06cc\u062f."}]"""
