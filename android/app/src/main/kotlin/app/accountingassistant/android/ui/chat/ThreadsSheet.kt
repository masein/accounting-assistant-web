package app.accountingassistant.android.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.SheetValue
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.data.ThreadDto
import app.accountingassistant.android.ui.components.ThinkingRow
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.Numbers

/** The conversations: open one with its cards, or start afresh. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ThreadsSheet(threads: List<ThreadDto>?, current: String?, lang: String, onOpen: (String) -> Unit, onNew: () -> Unit,
                 onDismiss: () -> Unit) {
    val c = LocalAccountantColors.current
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = c.surface,
                     sheetState = rememberBottomSheetState(SheetValue.Hidden, setOf(SheetValue.Hidden, SheetValue.Expanded))) {
        ThreadsContent(threads, current, lang, onOpen, onNew)
    }
}

@Composable
fun ThreadsContent(threads: List<ThreadDto>?, current: String?, lang: String, onOpen: (String) -> Unit, onNew: () -> Unit) {
    val c = LocalAccountantColors.current
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp).padding(bottom = 16.dp),
           verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.threads_title), color = c.ink, fontSize = 18.sp, fontWeight = FontWeight(800),
                 modifier = Modifier.weight(1f))
            Button(onClick = onNew, colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
                Text(stringResource(R.string.threads_new), fontWeight = FontWeight(700))
            }
        }
        when {
            threads == null -> ThinkingRow(stringResource(R.string.thinking))
            threads.isEmpty() -> Text(stringResource(R.string.threads_none), color = c.muted, fontSize = 13.sp)
            else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(threads, key = { it.id }) { t ->
                    val on = t.id == current
                    Surface(onClick = { onOpen(t.id) }, shape = RoundedCornerShape(14.dp), color = if (on) c.firouzehSoft else c.surface,
                            border = BorderStroke(1.dp, if (on) c.firouzeh else c.line)) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(t.title?.takeIf { it.isNotBlank() } ?: stringResource(R.string.threads_untitled),
                                     color = c.ink, fontSize = 14.sp, fontWeight = FontWeight(600), maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(stringResource(R.string.threads_messages, Numbers.digits(t.messageCount.toString(), lang)),
                                     color = c.muted, fontSize = 12.sp)
                            }
                            Text(Dates.short(t.updatedAt, lang), color = c.muted, fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }
}
