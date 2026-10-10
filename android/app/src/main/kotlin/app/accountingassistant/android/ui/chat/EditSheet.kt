package app.accountingassistant.android.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.Numbers
import java.time.LocalDate

/** The date as the user writes it: Jalali «۱۴۰۵/۰۷/۱۸» in Persian, ISO elsewhere. */
fun editableDate(iso: String?, lang: String): String {
    val d = iso?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() } ?: return ""
    return if (lang == "fa") {
        val (y, m, day) = Dates.toJalali(d)
        Numbers.digits("%04d/%02d/%02d".format(y, m, day), lang)
    } else Numbers.digits(d.toString(), lang)
}

/** What changed in the sheet, or null when nothing did. */
fun editChange(item: ChatItem.Proposal, lang: String, date: String, description: String, amount: String): EditRequest? {
    val newDate = Numbers.normalise(date).trim().takeIf { it.isNotEmpty() && it != Numbers.normalise(editableDate(item.dateIso, lang)) }
    val newText = description.trim().takeIf { it != item.title.trim() }
    val newAmount = Numbers.normalise(amount).filter { it.isDigit() }.toLongOrNull()?.takeIf { it > 0 && it != item.amount }
    return if (newDate == null && newText == null && newAmount == null) null
    else EditRequest(date = newDate, description = newText, amount = if (item.amountEditable) newAmount else null)
}

/**
 * Edit a draft: its date, its description and, when one line is debited and
 * one credited, its amount. Saving proposes it again through the server's
 * checks; a refusal is said here and the sheet stays open.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EditVoucherSheet(item: ChatItem.Proposal, lang: String, onSave: (EditRequest, (String?) -> Unit) -> Unit, onDismiss: () -> Unit) {
    val c = LocalAccountantColors.current
    var date by remember { mutableStateOf(editableDate(item.dateIso, lang)) }
    var description by remember { mutableStateOf(item.title) }
    var amount by remember { mutableStateOf(item.amount?.let { Numbers.amount(it, lang) } ?: "") }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
                     containerColor = c.surface) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 16.dp).navigationBarsPadding().imePadding(),
               verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(R.string.edit_title), color = c.ink, fontSize = 18.sp, fontWeight = FontWeight(700))
            OutlinedTextField(value = description, onValueChange = { description = it }, modifier = Modifier.fillMaxWidth(),
                              label = { Text(stringResource(R.string.edit_description)) })
            // grouped as it is typed («۸۰٬۰۰۰٬۰۰۰»): a rial amount is long
            OutlinedTextField(value = amount, onValueChange = { typed ->
                                  val digits = Numbers.normalise(typed).filter { it.isDigit() }.take(15)
                                  amount = digits.toLongOrNull()?.let { Numbers.amount(it, lang) } ?: ""
                              }, modifier = Modifier.fillMaxWidth(),
                              enabled = item.amountEditable, singleLine = true,
                              keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                              label = { Text(stringResource(R.string.edit_amount)) },
                              supportingText = if (item.amountEditable) null else { { Text(stringResource(R.string.edit_amount_locked)) } })
            OutlinedTextField(value = date, onValueChange = { date = it }, modifier = Modifier.fillMaxWidth(), singleLine = true,
                              label = { Text(stringResource(R.string.edit_date)) },
                              supportingText = { Text(stringResource(R.string.edit_date_hint)) })
            error?.let { Text(it, color = c.pomegranate, fontSize = 13.sp) }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    val change = editChange(item, lang, date, description, amount)
                    if (change == null) onDismiss()
                    else {
                        saving = true
                        error = null
                        onSave(change) { why -> saving = false; if (why == null) onDismiss() else error = why }
                    }
                }, enabled = !saving, colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
                    Text(stringResource(R.string.edit_save), fontWeight = FontWeight(700))
                }
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.dialog_back), color = c.muted) }
            }
        }
    }
}

/** Reject, with an optional reason the one who asked will read. */
@Composable
fun RejectDialog(onReject: (String?) -> Unit, onDismiss: () -> Unit) {
    val c = LocalAccountantColors.current
    var note by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.surface,
        title = { Text(stringResource(R.string.reject_title)) },
        text = {
            OutlinedTextField(value = note, onValueChange = { note = it }, modifier = Modifier.fillMaxWidth(),
                              label = { Text(stringResource(R.string.reject_reason)) })
        },
        confirmButton = {
            TextButton(onClick = { onReject(note.trim().ifEmpty { null }) }) {
                Text(stringResource(R.string.voucher_reject), color = c.pomegranate, fontWeight = FontWeight(700))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.dialog_back), color = c.muted) } },
    )
}
