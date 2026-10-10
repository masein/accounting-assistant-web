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
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Surface
import app.accountingassistant.android.ui.components.AppIcons
import androidx.compose.material3.Icon
import app.accountingassistant.android.util.Picked
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
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
import app.accountingassistant.android.ui.components.Choices
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
    onSuggestion: (String) -> Unit = {},
    onPicked: (Picked) -> Unit = {},
    onDetach: (String) -> Unit = {},
    onSpeakStart: () -> Unit = {},
    onSpeakEnd: () -> Unit = {},
    userInitial: String = "",
    onAccount: () -> Unit = {},
    onFile: (ChatItem.File, Boolean) -> Unit = { _, _ -> },
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
                if (userInitial.isNotEmpty()) {
                    val account = stringResource(R.string.account)
                    Surface(onClick = onAccount, shape = CircleShape, color = c.surface2,
                            modifier = Modifier.size(34.dp).semantics { contentDescription = account }) {
                        Box(contentAlignment = Alignment.Center) {
                            Text(userInitial, color = c.muted, fontWeight = FontWeight(700), fontSize = 14.sp)
                        }
                    }
                }
            }
            LazyColumn(
                state = list,
                modifier = Modifier.fillMaxWidth().weight(1f),
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 104.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(state.items, key = { it.id }) { item ->
                    when (item) {
                        is ChatItem.User -> UserBubble(item.text, files = item.files)
                        is ChatItem.Words -> AssistantText(item.text)
                        is ChatItem.Thinking -> ThinkingRow(item.text ?: stringResource(R.string.thinking))
                        is ChatItem.Suggestions -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            AssistantText(stringResource(R.string.suggestions_intro))
                            Choices(options = item.options, selected = null, onPick = { onSuggestion(item.options[it]) })
                        }
                        is ChatItem.Fallback -> AssistantText(item.text)
                        is ChatItem.Problem -> Surface(shape = RoundedCornerShape(16.dp), color = c.saffronSoft) {
                            val said = when (item.code) {
                                "ai_unavailable" -> stringResource(R.string.problem_ai_unavailable)
                                "ai_budget_exceeded" -> stringResource(R.string.problem_ai_budget)
                                "ai_rate_limited", "rate_limited" -> stringResource(R.string.problem_rate_limited)
                                else -> item.detail
                            }
                            Text(said, color = c.ink, fontSize = 13.5.sp, lineHeight = 21.sp,
                                 modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
                        }
                        is ChatItem.Figure -> FigureCard(
                            label = item.label,
                            value = (item.currency?.let { currencySymbol(it) } ?: "") + Numbers.amount(item.value, lang),
                            unit = item.currency?.takeIf { currencySymbol(it) == null }?.let { currencyName(it, lang) },
                            delta = null, deltaIsBad = false,
                            freshness = null, series = emptyList())
                        is ChatItem.Table -> TableCard(item, lang)
                        is ChatItem.File -> FileCard(item, onOpen = { onFile(item, false) }, onShare = { onFile(item, true) })
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
                                        ((item.undoUntil - nowMs) / 1000).coerceIn(0, 120).toInt())
                                    ChatItem.Proposal.Phase.Waiting -> VoucherState.WaitingForApproval
                                    ChatItem.Proposal.Phase.Cancelled -> VoucherState.Cancelled
                                    ChatItem.Proposal.Phase.Undone -> VoucherState.Undone
                                },
                                onConfirm = { onConfirm(item.id) }, onEdit = {}, onCancel = { onCancel(item.id) },
                                onUndo = { onUndo(item.id) },
                            )
                            item.document?.let { doc -> FileCard(doc, onOpen = { onFile(doc, false) }, onShare = { onFile(doc, true) }) }
                            item.error?.let { Text(it, color = c.pomegranate, fontSize = 12.sp) }
                        }
                    }
                }
            }
        }
        Column(Modifier.align(Alignment.BottomCenter).navigationBarsPadding().imePadding().padding(10.dp),
               verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val notice = when (state.notice) {
                ChatUiState.Notice.Offline -> R.string.error_network
                ChatUiState.Notice.UploadFailed -> R.string.error_upload
                ChatUiState.Notice.VoiceFailed -> R.string.error_voice
                else -> null
            }
            if (notice != null) {
                Surface(shape = RoundedCornerShape(14.dp), color = c.saffronSoft) {
                    Text(stringResource(notice), color = c.ink, fontSize = 12.5.sp,
                         modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
            if (state.uploading || state.transcribing) {
                ThinkingRow(stringResource(if (state.uploading) R.string.uploading else R.string.transcribing),
                            Modifier.padding(start = 8.dp))
            }
            if (state.attachments.isNotEmpty()) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    state.attachments.forEach { a ->
                        Surface(onClick = { onDetach(a.id) }, shape = RoundedCornerShape(50), color = c.surface,
                                border = BorderStroke(1.dp, c.line)) {
                            Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(a.name, color = c.ink, fontSize = 12.sp, maxLines = 1)
                                Text("  ✕", color = c.muted, fontSize = 12.sp)
                            }
                        }
                    }
                }
            }
            Box {
                var menu by remember { mutableStateOf(false) }
                val capture = rememberCapture(onPicked = onPicked)
                Composer(value = state.draft, onValueChange = onDraft, onSend = onSend, onAttach = { menu = true },
                         onSpeakStart = { capture.speak(onSpeakStart) }, onSpeakEnd = onSpeakEnd,
                         listening = state.listening,
                         canSend = state.draft.isNotBlank() || state.attachments.isNotEmpty())
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_camera)) }, onClick = { menu = false; capture.camera() })
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_photo)) }, onClick = { menu = false; capture.photo() })
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_file)) }, onClick = { menu = false; capture.file() })
                }
            }
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

/** A document from the books: a PDF tile, its name, Open and Share. */
@Composable
fun FileCard(file: ChatItem.File, onOpen: () -> Unit, onShare: () -> Unit) {
    val c = LocalAccountantColors.current
    Surface(onClick = onOpen, color = c.surface, shape = RoundedCornerShape(16.dp), border = BorderStroke(1.dp, c.line)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(color = c.pomegranate, shape = RoundedCornerShape(6.dp), modifier = Modifier.size(width = 34.dp, height = 40.dp)) {
                Box(contentAlignment = Alignment.BottomCenter) {
                    Text(if (file.mime == "application/pdf") "PDF" else "FILE", color = c.surface, fontSize = 8.sp,
                         fontWeight = FontWeight(700), modifier = Modifier.padding(bottom = 5.dp))
                }
            }
            Column(Modifier.weight(1f).padding(horizontal = 10.dp)) {
                Text(file.name, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(600), maxLines = 1)
                Text(stringResource(R.string.file_open), color = c.muted, fontSize = 11.sp)
            }
            val share = stringResource(R.string.file_share)
            Surface(onClick = onShare, shape = CircleShape, color = c.surface2, modifier = Modifier.size(34.dp).semantics { contentDescription = share }) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(AppIcons.Share, contentDescription = null, tint = c.ink, modifier = Modifier.size(16.dp))
                }
            }
        }
    }
}
