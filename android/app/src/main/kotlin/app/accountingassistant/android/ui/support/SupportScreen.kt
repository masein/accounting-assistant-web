package app.accountingassistant.android.ui.support

import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.data.SupportMessageDto
import app.accountingassistant.android.ui.components.AppIcons
import app.accountingassistant.android.ui.components.ThinkingRow
import app.accountingassistant.android.ui.components.boldMarks
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.ui.theme.Vazirmatn
import app.accountingassistant.android.util.Capture
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.LocalCalendar
import app.accountingassistant.android.util.Numbers
import app.accountingassistant.android.util.Picked
import app.accountingassistant.android.util.runSuspendCatching
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Support (scenarios DB1–DB8): the conversation with the support desk, the
 * same as the web's support panel. The person's messages on the end side and
 * the desk's on the start side, each with its time under it; whether the desk
 * is online; a box to write in, with screenshots attached from the photo
 * picker or the files.
 */
@Composable
fun SupportScreen(
    state: SupportUiState,
    lang: String,
    onBack: () -> Unit,
    onDraft: (String) -> Unit,
    onSend: () -> Unit,
    onPicked: (Picked) -> Unit = {},
    onUnreadable: () -> Unit = {},
    onDetach: (String) -> Unit = {},
    onDismissProblem: () -> Unit = {},
) {
    val c = LocalAccountantColors.current
    val calendar = LocalCalendar.current
    val list = rememberLazyListState()
    // follow the newest message
    LaunchedEffect(state.messages.lastOrNull()?.id) {
        if (state.messages.isNotEmpty()) list.animateScrollToItem(state.messages.lastIndex)
    }
    Column(Modifier.fillMaxSize().background(c.ground)) {
        Row(Modifier.fillMaxWidth().statusBarsPadding().padding(start = 4.dp, end = 14.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(AppIcons.Back, contentDescription = stringResource(R.string.dialog_back), tint = c.ink,
                     modifier = Modifier.size(22.dp))
            }
            Spacer(Modifier.width(2.dp))
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.support_title), color = c.ink, fontSize = 17.sp, fontWeight = FontWeight(800))
                if (state.loaded) {
                    Text(stringResource(if (state.online) R.string.support_online else R.string.support_offline),
                         color = if (state.online) c.firouzeh else c.muted, fontSize = 12.sp, lineHeight = 17.sp)
                }
            }
        }
        LazyColumn(
            state = list,
            modifier = Modifier.fillMaxWidth().weight(1f),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // before the first message: what helps support help
            if (state.messages.isEmpty()) item(key = "intro") {
                Surface(shape = RoundedCornerShape(16.dp), color = c.surface, border = BorderStroke(1.dp, c.line)) {
                    Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                        Icon(AppIcons.Headset, contentDescription = null, tint = c.firouzeh, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text(stringResource(R.string.support_intro), color = c.ink, fontSize = 13.5.sp, lineHeight = 21.sp)
                    }
                }
            }
            items(state.messages.distinctBy { it.id }, key = { it.id }) { m -> SupportBubble(m, lang, calendar) }
        }
        Column(Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(10.dp),
               verticalArrangement = Arrangement.spacedBy(6.dp)) {
            state.problem?.let { p ->
                Surface(onClick = onDismissProblem, shape = RoundedCornerShape(14.dp), color = c.saffronSoft) {
                    Text(when (p) {
                        SupportUiState.Problem.Failed -> stringResource(R.string.support_failed)
                        SupportUiState.Problem.TooLarge -> stringResource(R.string.support_too_large)
                        is SupportUiState.Problem.Said -> p.text
                    }, color = c.ink, fontSize = 12.5.sp, lineHeight = 18.sp,
                         modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp))
                }
            }
            if (state.uploading) ThinkingRow(stringResource(R.string.uploading), Modifier.padding(start = 8.dp))
            if (state.attachments.isNotEmpty()) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    state.attachments.forEach { a ->
                        Surface(onClick = { onDetach(a.id) }, shape = RoundedCornerShape(50), color = c.surface,
                                border = BorderStroke(1.dp, c.line)) {
                            Row(Modifier.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                Icon(AppIcons.Clip, contentDescription = null, tint = c.muted, modifier = Modifier.size(12.dp))
                                Spacer(Modifier.width(4.dp))
                                Text(a.name, color = c.ink, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                     modifier = Modifier.widthIn(max = 140.dp))
                                Text("  ✕", color = c.muted, fontSize = 12.sp)
                            }
                        }
                    }
                }
            }
            SupportComposer(state, onDraft, onSend, onPicked, onUnreadable)
        }
    }
}

/** One message: the person's on the end side, dark; the desk's on the start side, light, with its bold words. */
@Composable
private fun SupportBubble(m: SupportMessageDto, lang: String, calendar: String?) {
    val c = LocalAccountantColors.current
    val mine = !m.fromDesk
    Column(Modifier.fillMaxWidth(), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
        Surface(color = if (mine) c.bubble else c.surface,
                shape = if (mine) RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp, bottomEnd = 6.dp, bottomStart = 18.dp)
                        else RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp, bottomEnd = 18.dp, bottomStart = 6.dp),
                border = if (mine) null else BorderStroke(1.dp, c.line),
                modifier = Modifier.widthIn(max = 300.dp)) {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                val ink = if (mine) c.onBubble else c.ink
                if (m.attachments.isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically) {
                    m.attachments.forEach { _ ->
                        Icon(AppIcons.Clip, contentDescription = null, tint = ink.copy(alpha = 0.8f), modifier = Modifier.size(14.dp))
                    }
                }
                val text = m.text.orEmpty()
                // each message in its own direction: a Persian reply right to left in the English app too
                if (text.isNotBlank()) Text(boldMarks(text), color = ink, fontSize = 14.sp, lineHeight = 21.sp,
                                            style = TextStyle(textDirection = TextDirection.Content))
            }
        }
        val time = supportTime(m.at, lang, calendar)
        if (time.isNotEmpty()) Text(time, color = c.muted, fontSize = 11.sp, modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp))
    }
}

/** The box: attach a screenshot, write, send. */
@Composable
private fun SupportComposer(state: SupportUiState, onDraft: (String) -> Unit, onSend: () -> Unit,
                            onPicked: (Picked) -> Unit, onUnreadable: () -> Unit) {
    val c = LocalAccountantColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val picked by rememberUpdatedState(onPicked)
    val unreadable by rememberUpdatedState(onUnreadable)
    // read off the main thread: a screenshot is a few megabytes
    val take: (Uri?) -> Unit = { uri ->
        if (uri != null) scope.launch {
            runSuspendCatching { withContext(Dispatchers.IO) { supportFile(context, uri) } }
                .onSuccess { picked(it) }
                .onFailure { unreadable() }
        }
    }
    val photo = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { take(it) }
    val file = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { take(it) }
    var menu by remember { mutableStateOf(false) }
    val busy = state.uploading || state.sending
    Surface(shape = RoundedCornerShape(24.dp), color = c.glass.copy(alpha = 0.97f), border = BorderStroke(1.dp, c.line.copy(alpha = 0.6f)),
            shadowElevation = 6.dp, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            Box {
                IconButton(onClick = { menu = true }, enabled = !busy && state.attachments.size < SupportViewModel.MAX_ATTACHMENTS,
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.surface2, contentColor = c.ink),
                           modifier = Modifier.size(38.dp)) {
                    Icon(AppIcons.Clip, contentDescription = stringResource(R.string.support_attach), modifier = Modifier.size(18.dp))
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.attach_photo)) }, onClick = {
                        menu = false
                        photo.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                    })
                    DropdownMenuItem(text = { Text(stringResource(R.string.support_file)) }, onClick = {
                        menu = false
                        file.launch(SUPPORT_TYPES)
                    })
                }
            }
            Box(Modifier.weight(1f).padding(horizontal = 10.dp), contentAlignment = Alignment.CenterStart) {
                if (state.draft.isEmpty()) Text(stringResource(R.string.support_hint), color = c.muted, fontSize = 14.sp, maxLines = 1,
                                                overflow = TextOverflow.Ellipsis)
                BasicTextField(value = state.draft, onValueChange = onDraft, cursorBrush = SolidColor(c.firouzeh),
                               // in the direction of what is typed, as the chat's box
                               textStyle = TextStyle(color = c.ink, fontSize = 14.sp, fontFamily = Vazirmatn,
                                                     textDirection = TextDirection.Content),
                               maxLines = 5, modifier = Modifier.fillMaxWidth())
            }
            IconButton(onClick = onSend, enabled = state.canSend,
                       colors = IconButtonDefaults.iconButtonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh,
                                                                    disabledContainerColor = c.surface2, disabledContentColor = c.muted),
                       modifier = Modifier.size(38.dp)) {
                Icon(AppIcons.Send, contentDescription = stringResource(R.string.send), modifier = Modifier.size(18.dp))
            }
        }
    }
}

/** What support takes: a screenshot or a PDF (app/api/support.py). */
val SUPPORT_TYPES = arrayOf("image/png", "image/jpeg", "image/webp", "application/pdf")

/**
 * A file for support: a screenshot or a PDF as it is, so its words stay
 * sharp; any other picture (a HEIC photo) made a JPEG, which support takes.
 */
internal fun supportFile(context: Context, uri: Uri): Picked {
    val mime = context.contentResolver.getType(uri).orEmpty()
    return if (mime in SUPPORT_TYPES) Capture.document(context, uri) else Capture.photo(context, uri)
}

/**
 * When a message was written, under it: «۱۴:۰۵» today, «۶ آذر، ۱۴:۰۵» on
 * another day, in the books' calendar as the chat's dates; "14:05",
 * "27 Nov · 14:05" in English.
 */
internal fun supportTime(iso: String?, lang: String, calendar: String? = null,
                         today: LocalDate = LocalDate.now(), zone: ZoneId = ZoneId.systemDefault()): String {
    val t = iso?.let { runCatching { OffsetDateTime.parse(it).atZoneSameInstant(zone) }.getOrNull() } ?: return ""
    val hm = Numbers.digits(t.hour.toString().padStart(2, '0') + ":" + t.minute.toString().padStart(2, '0'), lang)
    if (t.toLocalDate() == today) return hm
    val day = Dates.short(t.toLocalDate().toString(), lang, calendar, today)
    return if (day.isBlank()) hm else day + Numbers.separator(lang) + hm
}
