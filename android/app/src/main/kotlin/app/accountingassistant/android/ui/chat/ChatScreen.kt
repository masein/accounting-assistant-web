package app.accountingassistant.android.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.components.AssistantText
import app.accountingassistant.android.ui.components.BooksBadge
import app.accountingassistant.android.ui.components.Composer
import app.accountingassistant.android.ui.components.FigureCard
import app.accountingassistant.android.ui.components.ThinkingRow
import app.accountingassistant.android.ui.components.UserBubble
import app.accountingassistant.android.ui.components.Voucher
import app.accountingassistant.android.ui.components.VoucherState
import app.accountingassistant.android.ui.components.currencyName
import app.accountingassistant.android.ui.components.currencySymbol
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers
import kotlinx.coroutines.delay

/**
 * The one screen: the books badge on top, the conversation, the floating
 * composer. Every block the server sends is drawn here.
 */
@Composable
fun ChatScreen(
    state: ChatUiState,
    lang: String,
    booksName: String,
    personalBooks: Boolean,
    onDraft: (String) -> Unit,
    onSend: () -> Unit,
    onConfirm: (String) -> Unit,
    onCancel: (String) -> Unit,
    onUndo: (String) -> Unit,
    onBooks: () -> Unit,
) {
    val c = LocalAccountantColors.current
    val booksColor = if (personalBooks) c.saffron else c.firouzeh
    val list = rememberLazyListState()
    val nowMs by produceState(System.currentTimeMillis()) {
        while (true) { delay(1000); value = System.currentTimeMillis() }
    }
    LaunchedEffect(state.items.size) { if (state.items.isNotEmpty()) list.animateScrollToItem(state.items.lastIndex) }

    Box(Modifier.fillMaxSize().background(c.ground)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                BooksBadge(name = booksName, color = booksColor, onClick = onBooks)
            }
            LazyColumn(
                state = list,
                modifier = Modifier.fillMaxWidth().weight(1f),
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 104.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(state.items, key = { it.id }) { item ->
                    when (item) {
                        is ChatItem.User -> UserBubble(item.text)
                        is ChatItem.Words -> AssistantText(item.text)
                        is ChatItem.Thinking -> ThinkingRow(stringResource(R.string.thinking))
                        is ChatItem.Fallback -> AssistantText(item.text)
                        is ChatItem.Figure -> FigureCard(
                            label = item.label,
                            value = (item.currency?.let { currencySymbol(it) } ?: "") + Numbers.amount(item.value, lang),
                            unit = item.currency?.takeIf { currencySymbol(it) == null }?.let { currencyName(it, lang) },
                            delta = null, deltaIsBad = false,
                            freshness = null, series = emptyList())
                        is ChatItem.Table -> TableCard(item, lang)
                        is ChatItem.Proposal -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Voucher(
                                lang = lang, amount = item.amount ?: 0, currency = item.currency ?: "",
                                summary = listOfNotNull(item.title, item.date).joinToString(" · "),
                                lines = item.lines, booksName = booksName, booksColor = booksColor,
                                state = when (item.phase) {
                                    ChatItem.Proposal.Phase.Draft -> VoucherState.Draft
                                    ChatItem.Proposal.Phase.Posting -> VoucherState.Posting
                                    ChatItem.Proposal.Phase.Posted -> VoucherState.Posted(
                                        item.voucher.orEmpty(), item.postedDate.orEmpty(),
                                        ((item.undoUntil - nowMs) / 1000).toInt().coerceAtLeast(0))
                                    ChatItem.Proposal.Phase.Waiting -> VoucherState.WaitingForApproval
                                    ChatItem.Proposal.Phase.Cancelled -> VoucherState.Cancelled
                                    ChatItem.Proposal.Phase.Undone -> VoucherState.Undone
                                },
                                onConfirm = { onConfirm(item.id) }, onEdit = {}, onCancel = { onCancel(item.id) },
                                onUndo = { onUndo(item.id) },
                            )
                            item.error?.let { Text(it, color = c.pomegranate, fontSize = 12.sp) }
                        }
                    }
                }
            }
        }
        Column(Modifier.align(Alignment.BottomCenter).navigationBarsPadding().imePadding().padding(10.dp),
               verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (state.notice == ChatUiState.Notice.Offline) {
                Surface(shape = RoundedCornerShape(14.dp), color = c.saffronSoft) {
                    Text(stringResource(R.string.error_network), color = c.ink, fontSize = 12.5.sp,
                         modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
            Composer(value = state.draft, onValueChange = onDraft, onSend = onSend, onAttach = {}, onSpeak = {})
        }
    }
}

@Composable
private fun TableCard(item: ChatItem.Table, lang: String) {
    val c = LocalAccountantColors.current
    Surface(color = c.surface, shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, c.line)) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp)) {
            item.rows.forEachIndexed { i, row ->
                if (i > 0) HorizontalDivider(color = c.line, thickness = 1.dp)
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(row.label, color = c.ink, fontSize = 13.sp)
                        row.sub?.let { Text(Numbers.digits(it, lang), color = c.muted, fontSize = 11.sp) }
                    }
                    Text(Numbers.amount(row.value, lang), color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(700))
                }
            }
        }
    }
}
