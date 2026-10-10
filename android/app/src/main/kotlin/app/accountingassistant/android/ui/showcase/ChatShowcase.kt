package app.accountingassistant.android.ui.showcase

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.ui.components.AssistantText
import app.accountingassistant.android.ui.components.BooksBadge
import app.accountingassistant.android.ui.components.Choices
import app.accountingassistant.android.ui.components.Composer
import app.accountingassistant.android.ui.components.UserBubble
import app.accountingassistant.android.ui.components.Voucher
import app.accountingassistant.android.ui.components.VoucherLine
import app.accountingassistant.android.ui.components.VoucherState
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import kotlinx.coroutines.delay

/**
 * The design board's opening scene (docs/design/android-chat.html) as a
 * working screen: a bank SMS becomes a draft voucher, and confirming stamps
 * it. The screenshot tests render it in both languages and themes, so the
 * design is checked without a server.
 */
@Composable
fun ChatShowcase(lang: String, startPosted: Boolean = false, fixedUndoSeconds: Int? = null) {
    val c = LocalAccountantColors.current
    val fa = lang == "fa"
    val cats = if (fa) listOf("خوراک", "رستوران", "حمل‌ونقل", "سایر") else listOf("Groceries", "Eating out", "Transport", "Other")
    val catLines = if (fa) listOf("خوراک و خواربار", "رستوران", "حمل‌ونقل", "سایر هزینه‌ها")
                   else listOf("Groceries", "Eating out", "Transport", "Other spending")
    var picked by remember { mutableIntStateOf(0) }
    var posted by remember { mutableStateOf(startPosted) }
    var undo by remember { mutableIntStateOf(fixedUndoSeconds ?: 120) }
    if (posted && fixedUndoSeconds == null) {
        LaunchedEffect(Unit) { while (undo > 0) { delay(1000); undo -= 1 } }
    }

    Box(Modifier.fillMaxSize().background(c.ground)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                BooksBadge(name = if (fa) "دفتر شخصی" else "Personal", color = c.saffron, onClick = {})
                Surface(shape = CircleShape, color = c.surface2, modifier = Modifier.size(32.dp)) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(if (fa) "س" else "S", color = c.muted, fontWeight = FontWeight(700), fontSize = 13.sp)
                    }
                }
            }
            Column(
                Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    Surface(shape = CircleShape, color = c.surface2) {
                        Text(if (fa) "امروز · ۱۸ مهر" else "Today · 10 Oct", color = c.muted, fontSize = 11.sp,
                             modifier = Modifier.padding(horizontal = 10.dp, vertical = 1.dp))
                    }
                }
                UserBubble(
                    from = if (fa) "پیامک · بانک ملت" else "SMS · Barclays",
                    text = if (fa) "برداشت: ۲٬۵۰۰٬۰۰۰\nمانده: ۴۱٬۲۰۰٬۰۰۰\n۱۴۰۵/۰۷/۱۸ - ۱۲:۳۰"
                           else "Card payment £25.00\nBalance £412.00\n10/10/2026 12:30",
                )
                AssistantText(if (fa) "یک برداشت از کارت ملت. خرجِ چه بود؟"
                              else "A card payment from Barclays. What was it for?")
                Choices(options = cats, selected = picked, onPick = { picked = it }, enabled = !posted)
                Voucher(
                    lang = lang,
                    amount = if (fa) 2_500_000 else 25,
                    currency = if (fa) "IRR" else "GBP",
                    summary = if (fa) "هزینهٔ ${catLines[picked]} · کارت ملت · ۱۸ مهر ۱۴۰۵"
                              else "${catLines[picked]} · Barclays card · 10 Oct 2026",
                    lines = listOf(
                        VoucherLine(if (fa) "6110" else "7400", catLines[picked], debit = if (fa) 2_500_000 else 25),
                        VoucherLine(if (fa) "1110" else "1200", if (fa) "بانک ملت" else "Barclays", credit = if (fa) 2_500_000 else 25),
                    ),
                    booksName = if (fa) "دفتر شخصی" else "Personal",
                    booksColor = c.saffron,
                    state = if (posted) VoucherState.Posted("217", if (fa) "1405/07/18" else "10/10/2026", undo) else VoucherState.Draft,
                    onConfirm = { posted = true; undo = fixedUndoSeconds ?: 120 },
                    onEdit = {}, onCancel = {}, onUndo = { posted = false },
                )
                Spacer(Modifier.height(96.dp))
            }
        }
        var draft by remember { mutableStateOf("") }
        Composer(
            value = draft, onValueChange = { draft = it }, onSend = { draft = "" }, onAttach = {},
            onSpeakStart = {}, onSpeakEnd = {},
            modifier = Modifier.align(Alignment.BottomCenter).navigationBarsPadding().imePadding()
                .padding(horizontal = 10.dp, vertical = 10.dp),
        )
    }
}
