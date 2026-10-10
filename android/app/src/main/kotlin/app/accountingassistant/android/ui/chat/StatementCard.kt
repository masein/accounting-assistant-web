package app.accountingassistant.android.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.components.currencyName
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.Numbers

/**
 * A bank statement read and checked against the books: the bank and the
 * period, what matched and what differs, the balance gap, and "fix step by
 * step", which brings the differences one voucher at a time.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StatementCard(item: ChatItem.Statement, lang: String, onFix: () -> Unit) {
    val c = LocalAccountantColors.current
    Surface(color = c.surface, shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, c.line), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.statement_title), color = c.muted, fontSize = 11.sp)
                item.bank?.let { Text(it, color = c.ink, fontSize = 12.sp, fontWeight = FontWeight(600)) }
            }
            Text(listOfNotNull(
                     if (item.from != null && item.to != null) "⁨${Dates.short(item.from, lang)} – ${Dates.short(item.to, lang)}⁩" else null,
                     Numbers.digits(pluralStringResource(R.plurals.statement_rows, item.rows, item.rows), lang),
                 ).joinToString(" · "),
                 color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
            val chips = listOf(
                Triple("matched", R.plurals.statement_matched, c.firouzeh),
                Triple("unrecorded", R.plurals.statement_unrecorded, c.saffron),
                Triple("needs_confirmation", R.plurals.statement_confirm, c.saffron),
                Triple("amount_mismatch", R.plurals.statement_mismatch, c.pomegranate),
                Triple("missing_in_bank", R.plurals.statement_missing, c.pomegranate),
                Triple("duplicates", R.plurals.statement_duplicates, c.muted),
            ).mapNotNull { (key, res, tint) -> item.counts[key]?.takeIf { it > 0 }?.let { Triple(it, res, tint) } }
            if (chips.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                chips.forEach { (n, res, tint) -> Chip(Numbers.digits(pluralStringResource(res, n, n), lang), tint) }
            }
            item.gap?.let { gap ->
                Text(stringResource(R.string.statement_gap,
                                    Numbers.amount(kotlin.math.abs(gap), lang) + (item.currency?.let { " " + currencyName(it, lang) } ?: "")),
                     color = c.pomegranate, fontSize = 13.sp)
            }
            if (item.clean) Text(stringResource(R.string.statement_clean), color = c.firouzeh, fontSize = 13.sp, fontWeight = FontWeight(600))
            else Button(onClick = onFix, colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
                Text(stringResource(R.string.statement_fix), fontWeight = FontWeight(700))
            }
        }
    }
}

@Composable
private fun Chip(text: String, tint: Color) {
    Surface(shape = CircleShape, color = tint.copy(alpha = 0.12f), border = BorderStroke(1.dp, tint.copy(alpha = 0.35f))) {
        Text(text, color = LocalAccountantColors.current.ink, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp))
    }
}

/** Under the words that offer it: the statement's next difference. */
@Composable
fun NextDifferenceChip(onClick: () -> Unit) {
    val c = LocalAccountantColors.current
    Surface(onClick = onClick, shape = CircleShape, color = c.firouzehSoft, border = BorderStroke(1.dp, c.firouzeh)) {
        Text(stringResource(R.string.statement_next), color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(600),
             modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
    }
}
