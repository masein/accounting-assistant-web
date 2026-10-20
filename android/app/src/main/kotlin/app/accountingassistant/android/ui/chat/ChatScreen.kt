package app.accountingassistant.android.ui.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import app.accountingassistant.android.MainActivity
import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.unit.IntOffset
import androidx.compose.foundation.layout.absoluteOffset
import app.accountingassistant.android.ui.components.ReplyingTo
import kotlin.math.roundToInt
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.absolutePadding
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import kotlinx.coroutines.launch
import androidx.compose.material3.TextButton
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.AbsoluteAlignment
import androidx.compose.foundation.layout.widthIn
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.util.Shortcuts
import androidx.compose.ui.focus.FocusRequester
import app.accountingassistant.android.data.Queued
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.gestures.scrollBy
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
import app.accountingassistant.android.ui.components.priced
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
    onSpeakCancel: () -> Unit = {},
    level: () -> Float = { 0f },
    userInitial: String = "",
    onAccount: () -> Unit = {},
    onFile: (ChatItem.File, Boolean) -> Unit = { _, _ -> },
    onThreads: (() -> Unit)? = null,
    onRetry: (String) -> Unit = {},
    onDiscard: (String) -> Unit = {},
    onEarlier: () -> Unit = {},
    onApprove: (String) -> Unit = {},
    onReject: (String, String?) -> Unit = { _, _ -> },
    onEdit: (String, EditRequest, (String?) -> Unit) -> Unit = { _, _, _ -> },
    onStatementNext: (String) -> Unit = {},
    /** A home-screen shortcut to carry out once (util/Shortcuts.kt). */
    action: String? = null,
    onActionDone: () -> Unit = {},
    /** Reply to a message held down; null drops the reply (scenario AC4). */
    onReply: (Quote?) -> Unit = {},
) {
    val c = LocalAccountantColors.current
    val booksColor = if (personalBooks) c.saffron else c.firouzeh
    val list = rememberLazyListState()
    var editing by remember { mutableStateOf<ChatItem.Proposal?>(null) }
    var rejecting by remember { mutableStateOf<String?>(null) }
    val nowMs by produceState(System.currentTimeMillis()) {
        while (true) { delay(1000); value = System.currentTimeMillis() }
    }
    // follow the newest item; an older page prepended keeps the place
    LaunchedEffect(state.items.lastOrNull()?.id) { if (state.items.isNotEmpty()) list.animateScrollToItem(state.items.lastIndex) }
    // The composer floats over the list with whatever sits above it — a file waiting to be sent,
    // a notice — so the list keeps clear of all of it, not a fixed 104dp: a chosen file covered
    // the last answer (phone QA, 2026-10-12). When it grows, the conversation moves up with it.
    var overlayPx by remember { mutableIntStateOf(0) }
    var shownPx by remember { mutableIntStateOf(0) }
    LaunchedEffect(overlayPx) {
        val before = shownPx
        shownPx = overlayPx
        if (before > 0 && overlayPx > before) list.scrollBy((overlayPx - before).toFloat())
    }
    val listBottom = maxOf(104.dp, with(LocalDensity.current) { overlayPx.toDp() } + 6.dp)
    // a line's side, as the card says it: Dr and Cr, or To and From in a person's books (scenario AC8)
    val sides = stringResource(if (personalBooks) R.string.side_to else R.string.debit_short) to
        stringResource(if (personalBooks) R.string.side_from else R.string.credit_short)
    val reply: (Quote) -> Unit = { onReply(it) }

    Box(Modifier.fillMaxSize().background(c.ground)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                BooksBadge(name = booksName, color = booksColor, onClick = onBooks)
                Spacer(Modifier.weight(1f))
                if (onThreads != null) {
                    val label = stringResource(R.string.threads_title)
                    Surface(onClick = onThreads, shape = CircleShape, color = c.surface2,
                            modifier = Modifier.size(34.dp).semantics { contentDescription = label }) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(AppIcons.Threads, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                        }
                    }
                    Spacer(Modifier.width(8.dp))
                }
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
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = listBottom),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // keyed by block id; a repeated id must never crash the list
                items(state.items.distinctBy { it.id }, key = { it.id }) { item ->
                    when (item) {
                        is ChatItem.User -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Held(item, lang, sides, reply) { held ->
                                UserBubble(item.text, files = item.files, held = held,
                                           quoteFrom = item.quote?.let { stringResource(if (it.mine) R.string.quote_self else R.string.quote_assistant) },
                                           quoteText = item.quote?.text)
                            }
                            // on its way now: the row below says what is happening, not "waiting for the network"
                            item.clientId?.takeIf { it != state.sendingId }?.let { state.queued[it] }?.let { q ->
                                Queue(q, onRetry = { onRetry(q.clientId) }, onDiscard = { onDiscard(q.clientId) })
                            }
                        }
                        is ChatItem.Earlier -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                            Surface(onClick = onEarlier, shape = RoundedCornerShape(50), color = c.surface2) {
                                Text(stringResource(R.string.earlier), color = c.muted, fontSize = 12.5.sp,
                                     modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
                            }
                        }
                        is ChatItem.Words -> if (item.nextStatement == null && item.choices.isEmpty())
                                Held(item, lang, sides, reply) { held -> AssistantText(item.text, modifier = held) }
                            else Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                // only the words are held: a chip under them sends itself as the finger lifts
                                Held(item, lang, sides, reply) { held -> AssistantText(item.text, modifier = held) }
                                if (item.nextStatement != null) NextDifferenceChip(onClick = { onStatementNext(item.nextStatement) })
                                // a short question's answers: tapping one sends it, as a typed answer would be
                                if (item.choices.isNotEmpty()) Choices(options = item.choices, selected = null,
                                                                       onPick = { onSuggestion(item.choices[it]) })
                            }
                        is ChatItem.Chart -> Held(item, lang, sides, reply) { held -> Box(held) { ChartCard(item, lang) } }
                        is ChatItem.Statement -> StatementCard(item, lang, onFix = { onStatementNext(item.statementId) })
                        is ChatItem.Thinking -> ThinkingRow(
                            item.upload?.let { stringResource(R.string.uploading) + " " + Numbers.percent(it, lang) }
                                ?: item.text ?: stringResource(R.string.thinking),
                            progress = item.upload)
                        is ChatItem.Suggestions -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            AssistantText(stringResource(R.string.suggestions_intro))
                            Choices(options = item.options, selected = null, onPick = { onSuggestion(item.options[it]) })
                        }
                        is ChatItem.Fallback -> Held(item, lang, sides, reply) { held -> AssistantText(item.text, modifier = held) }
                        is ChatItem.Problem -> Surface(shape = RoundedCornerShape(16.dp), color = c.saffronSoft) {
                            Text(problemText(item.code, item.detail), color = c.ink, fontSize = 13.5.sp, lineHeight = 21.sp,
                                 modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
                        }
                        is ChatItem.Figure -> Held(item, lang, sides, reply) { held -> Box(held) { FigureCard(
                            label = item.label,
                            value = (item.currency?.let { currencySymbol(it) } ?: "") +
                                Numbers.amount(item.value, lang, Numbers.places(item.currency)),
                            unit = item.currency?.takeIf { currencySymbol(it) == null }?.let { currencyName(it, lang) },
                            delta = null, deltaIsBad = false,
                            freshness = null, series = emptyList()) } }
                        is ChatItem.Table -> Held(item, lang, sides, reply) { held -> Box(held) { TableCard(item, lang) } }
                        is ChatItem.File -> FileCard(item, onOpen = { onFile(item, false) }, onShare = { onFile(item, true) })
                        is ChatItem.Proposal -> Held(item, lang, sides, reply) { held -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Voucher(
                                lang = lang, amount = item.amount ?: java.math.BigDecimal.ZERO, currency = item.currency ?: "",
                                summary = summaryOf(item.title, item.date),
                                bodyModifier = held, plainSides = personalBooks,
                                people = item.people.map { it.name to it.new },
                                lines = item.lines, booksName = booksName, booksColor = booksColor,
                                state = when (item.phase) {
                                    ChatItem.Proposal.Phase.Draft -> item.approval?.let { VoucherState.ForApproval(it.askedBy, it.mine) }
                                        ?: VoucherState.Draft
                                    ChatItem.Proposal.Phase.Posting -> item.approval?.let { VoucherState.ForApproval(it.askedBy, it.mine, busy = true) }
                                        ?: VoucherState.Posting
                                    ChatItem.Proposal.Phase.Rejected -> VoucherState.Rejected
                                    ChatItem.Proposal.Phase.Replaced -> VoucherState.Replaced
                                    ChatItem.Proposal.Phase.Posted -> VoucherState.Posted(
                                        item.voucher.orEmpty(), item.postedDate.orEmpty(),
                                        ((item.undoUntil - nowMs) / 1000).coerceIn(0, 120).toInt())
                                    ChatItem.Proposal.Phase.Waiting -> VoucherState.WaitingForApproval
                                    ChatItem.Proposal.Phase.Cancelled -> VoucherState.Cancelled
                                    ChatItem.Proposal.Phase.Undone -> VoucherState.Undone
                                },
                                onConfirm = { onConfirm(item.id) }, onEdit = if (item.editable) ({ editing = item }) else null, onCancel = { onCancel(item.id) },
                                onUndo = { onUndo(item.id) },
                                onApprove = { onApprove(item.id) }, onReject = { rejecting = item.id },
                            )
                            item.document?.let { doc -> FileCard(doc, onOpen = { onFile(doc, false) }, onShare = { onFile(doc, true) }) }
                            item.error?.let { Text(it, color = c.pomegranate, fontSize = 12.sp) }
                        } }
                    }
                }
            }
        }
        Column(Modifier.align(Alignment.BottomCenter).onSizeChanged { overlayPx = it.height }
                   .navigationBarsPadding().imePadding().padding(10.dp),
               verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val notice = when (state.notice) {
                ChatUiState.Notice.Offline -> R.string.error_network
                ChatUiState.Notice.UploadFailed -> R.string.error_upload
                ChatUiState.Notice.Unreadable -> R.string.error_unreadable
                ChatUiState.Notice.VoiceFailed -> R.string.error_voice
                ChatUiState.Notice.VoiceTooShort -> R.string.error_voice_short
                else -> null
            }
            if (notice != null) {
                Surface(shape = RoundedCornerShape(14.dp), color = c.saffronSoft) {
                    Text(stringResource(notice), color = c.ink, fontSize = 12.5.sp,
                         modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
            if (state.uploading) {
                val gone = state.uploadProgress
                ThinkingRow(stringResource(R.string.uploading) + (gone?.let { " " + Numbers.percent(it, lang) } ?: ""),
                            Modifier.padding(start = 8.dp), progress = gone)
            }
            state.replyTo?.let { q ->
                ReplyingTo(stringResource(if (q.mine) R.string.replying_to_self else R.string.replying_to_assistant), q.text,
                           onDrop = { onReply(null) })
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
                val focus = remember { FocusRequester() }
                val askCash = stringResource(R.string.ask_cash)
                // Reply opens the keyboard on the composer, under the quote
                LaunchedEffect(state.replyTo) { if (state.replyTo != null) runCatching { focus.requestFocus() } }
                LaunchedEffect(action) {
                    when (action) {
                        Shortcuts.PHOTO_RECEIPT -> capture.camera()
                        Shortcuts.RECORD_SPENDING -> runCatching { focus.requestFocus() }
                        Shortcuts.ASK_CASH -> onSuggestion(askCash)
                        else -> return@LaunchedEffect
                    }
                    onActionDone()
                }
                Composer(value = state.draft, onValueChange = onDraft, onSend = onSend, onAttach = { menu = true }, focus = focus,
                         onSpeakStart = { capture.speak(onSpeakStart) }, onSpeakEnd = onSpeakEnd,
                         onSpeakCancel = onSpeakCancel, level = level, transcribing = state.transcribing, lang = lang,
                         listening = state.listening,
                         canSend = state.draft.isNotBlank() || state.attachments.isNotEmpty())
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_camera)) }, onClick = { menu = false; capture.camera() })
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_photo)) }, onClick = { menu = false; capture.photo() })
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_file)) }, onClick = { menu = false; capture.file() })
                }
            }
        }
        editing?.let { p ->
            EditVoucherSheet(p, lang, onSave = { change, done -> onEdit(p.id, change, done) }, onDismiss = { editing = null })
        }
        rejecting?.let { id ->
            RejectDialog(onReject = { note -> rejecting = null; onReject(id, note) }, onDismiss = { rejecting = null })
        }
    }
}

@Composable
private fun TableCard(item: ChatItem.Table, lang: String) {
    val c = LocalAccountantColors.current
    Surface(color = c.surface, shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, c.line)) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp)) {
            item.title?.let { Text(it, color = c.muted, fontSize = 11.sp, modifier = Modifier.padding(top = 4.dp, bottom = 2.dp)) }
            item.rows.forEachIndexed { i, row ->
                if (i > 0) HorizontalDivider(color = c.line, thickness = 1.dp)
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(row.label, color = c.ink, fontSize = 13.sp)
                        row.sub?.let { Text(Numbers.digits(it, lang), color = c.muted, fontSize = 11.sp) }
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        // «£360» like the cards around it; a rial table keeps bare numbers, no unit on every row
                        val shown = if (row.currency?.let { currencySymbol(it) } != null) priced(row.value, row.currency, lang)
                                    else Numbers.amount(row.value, lang, Numbers.places(row.currency))
                        Text(shown, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(700))
                        row.gain?.let { g ->
                            Text(Numbers.signed(g, lang, Numbers.places(row.currency)), color = if (g.signum() > 0) c.firouzeh else c.pomegranate,
                                 fontSize = 11.sp)
                        }
                    }
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

/** A failed turn said in the user's language when its code is known, else the server's words. */
@Composable
private fun problemText(code: String?, detail: String?): String = when (code) {
    "ai_unavailable" -> stringResource(R.string.problem_ai_unavailable)
    "ai_budget_exceeded" -> stringResource(R.string.problem_ai_budget)
    "ai_rate_limited", "rate_limited" -> stringResource(R.string.problem_rate_limited)
    else -> detail?.takeIf { it.isNotBlank() } ?: stringResource(R.string.error_generic)
}

/**
 * Under a message still in the outbox: waiting for the network (it sends
 * itself), or refused by the server, with Try again and Don't send.
 */
@Composable
private fun Queue(q: Queued, onRetry: () -> Unit, onDiscard: () -> Unit) {
    val c = LocalAccountantColors.current
    val failed = q.failure != null
    val small = PaddingValues(horizontal = 8.dp, vertical = 0.dp)
    @Composable fun discard() = TextButton(onClick = onDiscard, contentPadding = small) {
        Text(stringResource(R.string.outbox_discard), color = c.muted, fontSize = 12.sp)
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = AbsoluteAlignment.Right) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(if (failed) AppIcons.Alert else AppIcons.Clock, contentDescription = null,
                 tint = if (failed) c.pomegranate else c.muted, modifier = Modifier.size(13.dp))
            Spacer(Modifier.width(4.dp))
            Text(if (failed) problemText(q.failure, q.detail) else stringResource(R.string.outbox_waiting),
                 color = if (failed) c.pomegranate else c.muted, fontSize = 11.5.sp, lineHeight = 16.sp,
                 modifier = Modifier.widthIn(max = if (failed) 280.dp else 220.dp))
            if (!failed) discard()                                  // waiting: one line, its one action beside it
        }
        if (failed) Row(Modifier.offset(y = (-6).dp)) {
            TextButton(onClick = onRetry, contentPadding = small) {
                Text(stringResource(R.string.outbox_retry), color = c.firouzeh, fontSize = 12.sp, fontWeight = FontWeight(600))
            }
            discard()
        }
    }
}

/**
 * A card's description and date. The date is isolated in the direction of
 * its own words: «۲۴ مهر ۱۴۰۵» right to left, "10 Oct 2026" left to right. A
 * first-strong isolate came out left to right on the owner's phone, «مهر
 * ۲۴۱۴۰۵» (scenario AC9). A Persian or Arabic date follows a comma.
 */
internal fun summaryOf(title: String, date: String?): String {
    val rtl = date?.any { Character.getDirectionality(it).let { d ->
        d == Character.DIRECTIONALITY_RIGHT_TO_LEFT || d == Character.DIRECTIONALITY_RIGHT_TO_LEFT_ARABIC } } == true
    return listOfNotNull(title.ifBlank { null }, date?.let { (if (rtl) "\u2067" else "\u2066") + it + "\u2069" })
        // beside Persian digits a middle dot reads as a zero, «۲۴۰ مهر»: a comma there
        .joinToString(if (rtl) "، " else " · ")
}

/**
 * Holding a message down (scenarios AC1–AC4): Reply, Copy, Share, where the
 * finger is. Swiping it left is Reply (AC11): it follows the finger, the reply
 * arrow comes up beside it, a tick past the line, and it springs back. [content]
 * puts the modifier it is given on the part that can be held: the bubble, the
 * words, a card above its fold. Never on a button: a button's own tap fires
 * as the finger lifts, and Post would post.
 */
@Composable
private fun Held(item: ChatItem, lang: String, sides: Pair<String, String>, onReply: (Quote) -> Unit,
                 content: @Composable (Modifier) -> Unit) {
    val text = remember(item, lang, sides) { item.asText(lang, sides) }
    if (text == null) { content(Modifier); return }
    val context = LocalContext.current
    val haptics = LocalHapticFeedback.current
    var at by remember { mutableStateOf<IntOffset?>(null) }
    var box by remember { mutableStateOf<LayoutCoordinates?>(null) }
    var part by remember { mutableStateOf<LayoutCoordinates?>(null) }
    val label = stringResource(R.string.msg_actions)
    val copied = stringResource(R.string.msg_copied)
    val c = LocalAccountantColors.current
    val scope = rememberCoroutineScope()
    val pull = remember { Animatable(0f) }
    val density = LocalDensity.current
    val line = with(density) { SWIPE_LINE.toPx() }
    val most = with(density) { SWIPE_MOST.toPx() }
    var armed by remember { mutableStateOf(false) }
    val held = Modifier
        .onGloballyPositioned { part = it }
        .pointerInput(item.id) {
            // left only, in the screen's own terms whichever way the page runs; past the line it gives slowly
            detectHorizontalDragGestures(
                onDragStart = { armed = false },
                onDragEnd = {
                    if (armed) item.quote(lang, sides)?.let(onReply)
                    armed = false
                    scope.launch { pull.animateTo(0f, spring(dampingRatio = 0.7f, stiffness = Spring.StiffnessMediumLow)) }
                },
                onDragCancel = { armed = false; scope.launch { pull.animateTo(0f) } },
                onHorizontalDrag = { change, dx ->
                    val next = (pull.value + dx * (if (-pull.value > line) 0.35f else 1f)).coerceIn(-most, 0f)
                    if (next != pull.value || dx < 0) change.consume()
                    scope.launch { pull.snapTo(next) }
                    val past = -next >= line
                    if (past != armed) {
                        armed = past
                        if (past) haptics.performHapticFeedback(HapticFeedbackType.GestureThresholdActivate)
                    }
                },
            )
        }
        .pointerInput(item.id) {
            detectTapGestures(onLongPress = { p ->
                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                val origin = box?.let { b -> part?.let { b.localPositionOf(it, p) } } ?: p
                at = IntOffset(origin.x.roundToInt(), origin.y.roundToInt())
            })
        }
        .semantics { onLongClick(label) { at = IntOffset.Zero; true } }
    Box(Modifier.onGloballyPositioned { box = it }) {
        val pulled = (-pull.value / line).coerceIn(0f, 1f)
        if (pulled > 0f) {
            // the arrow points the way the finger goes, in a right-to-left page too
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                Surface(shape = CircleShape, color = if (armed) c.firouzeh else c.surface2,
                        modifier = Modifier.align(AbsoluteAlignment.CenterRight).absolutePadding(right = 6.dp).size(32.dp)
                            .graphicsLayer { alpha = pulled; scaleX = 0.6f + 0.4f * pulled; scaleY = 0.6f + 0.4f * pulled }) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(AppIcons.Reply, contentDescription = null, tint = if (armed) c.onFirouzeh else c.firouzeh,
                             modifier = Modifier.size(17.dp))
                    }
                }
            }
        }
        Box(Modifier.absoluteOffset { IntOffset(pull.value.roundToInt(), 0) }) { content(held) }
        at?.let { offset ->
            // where the finger is, from the left whichever way the page runs (offset() would mirror it)
            Box(Modifier.align(AbsoluteAlignment.TopLeft).absoluteOffset { offset }.size(0.dp)) {
                DropdownMenu(expanded = true, onDismissRequest = { at = null }) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.msg_reply)) },
                                     leadingIcon = { Icon(AppIcons.Reply, contentDescription = null, modifier = Modifier.size(18.dp)) },
                                     onClick = { at = null; item.quote(lang, sides)?.let(onReply) })
                    DropdownMenuItem(text = { Text(stringResource(R.string.msg_copy)) },
                                     leadingIcon = { Icon(AppIcons.Copy, contentDescription = null, modifier = Modifier.size(18.dp)) },
                                     onClick = {
                                         at = null
                                         copyText(context, text)
                                         // Android 13 and later say it themselves
                                         if (Build.VERSION.SDK_INT < 33) Toast.makeText(context, copied, Toast.LENGTH_SHORT).show()
                                     })
                    DropdownMenuItem(text = { Text(stringResource(R.string.msg_share)) },
                                     leadingIcon = { Icon(AppIcons.Share, contentDescription = null, modifier = Modifier.size(18.dp)) },
                                     onClick = { at = null; shareText(context, text) })
                }
            }
        }
    }
}

/** How far a message is swiped left before letting go replies to it, and the most it moves. */
private val SWIPE_LINE = 56.dp
private val SWIPE_MOST = 84.dp

private fun copyText(context: Context, text: String) {
    context.getSystemService(ClipboardManager::class.java)
        ?.setPrimaryClip(ClipData.newPlainText(context.getString(R.string.app_name), text))
}

/** The phone's share sheet with the words: Telegram, WhatsApp, Gmail… but not this app, which takes shares too. */
private fun shareText(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    val chooser = Intent.createChooser(send, null)
        .putExtra(Intent.EXTRA_EXCLUDE_COMPONENTS, arrayOf(ComponentName(context, MainActivity::class.java)))
    runCatching { context.startActivity(chooser) }
}
