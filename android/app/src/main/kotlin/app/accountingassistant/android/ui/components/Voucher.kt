package app.accountingassistant.android.ui.components

import android.os.Build
import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.PathOperation
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onPlaced
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.theme.FigureStyle
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers

/** One line of a draft voucher: an account, and a debit or a credit. */
data class VoucherLine(val code: String, val name: String, val debit: Long = 0, val credit: Long = 0)

sealed interface VoucherState {
    data object Draft : VoucherState
    data object Posting : VoucherState
    data class Posted(val number: String, val date: String, val undoSecondsLeft: Int) : VoucherState
    data object WaitingForApproval : VoucherState
    data object Cancelled : VoucherState
    data object Undone : VoucherState
    /** Someone else's voucher waiting for this person's decision; [mine] when they asked for it. */
    data class ForApproval(val askedBy: String, val mine: Boolean, val busy: Boolean = false) : VoucherState
    data object Rejected : VoucherState
    /** Edited: a new draft follows it. */
    data object Replaced : VoucherState
}

/**
 * A draft سند: the amount, what it is, the lines, and which books, with a
 * perforated fold above its buttons. Confirming stamps the round seal onto
 * the fold (docs/design/android-chat.html, "the stamp").
 */
@Composable
fun Voucher(
    lang: String,
    amount: Long,
    currency: String,
    summary: String,
    lines: List<VoucherLine>,
    booksName: String,
    booksColor: androidx.compose.ui.graphics.Color,
    state: VoucherState,
    onConfirm: () -> Unit,
    onEdit: () -> Unit,
    onCancel: () -> Unit,
    onUndo: () -> Unit,
    modifier: Modifier = Modifier,
    footer: @Composable (() -> Unit)? = null,
    onApprove: () -> Unit = {},
    onReject: () -> Unit = {},
) {
    val c = LocalAccountantColors.current
    val view = LocalView.current
    var foldY by remember { mutableFloatStateOf(0f) }
    val notch = 9.dp
    val corner = 20.dp

    Box(modifier.fillMaxWidth()) {
        Column(
            Modifier
                .fillMaxWidth()
                .drawWithCache {
                    val r = corner.toPx()
                    val n = notch.toPx()
                    val outline = Path().apply {
                        addRoundRect(RoundRect(0f, 0f, size.width, size.height, CornerRadius(r, r)))
                    }
                    val shape = if (foldY > 0f) {
                        val cuts = Path().apply {
                            addOval(androidx.compose.ui.geometry.Rect(Offset(0f, foldY), n))
                            addOval(androidx.compose.ui.geometry.Rect(Offset(size.width, foldY), n))
                        }
                        Path().apply { op(outline, cuts, PathOperation.Difference) }
                    } else outline
                    val dash = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 5.dp.toPx()))
                    onDrawBehind {
                        drawPath(shape, c.surface)
                        drawPath(shape, c.line, style = Stroke(1.dp.toPx()))
                        if (foldY > 0f) {
                            drawLine(c.line, Offset(n + 6.dp.toPx(), foldY), Offset(size.width - n - 6.dp.toPx(), foldY),
                                     strokeWidth = 1.5.dp.toPx(), pathEffect = dash)
                        }
                    }
                },
        ) {
            Column(Modifier.padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = 12.dp),
                   verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(when (state) {
                             is VoucherState.ForApproval -> stringResource(R.string.voucher_asked_by, state.askedBy)
                             VoucherState.Draft, VoucherState.Posting -> stringResource(R.string.voucher_draft)
                             else -> stringResource(R.string.voucher_title)
                         }, color = if (state is VoucherState.ForApproval) c.saffron else c.muted, fontSize = 11.sp,
                         fontWeight = if (state is VoucherState.ForApproval) FontWeight(600) else FontWeight(400))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Canvas(Modifier.size(7.dp)) { drawCircle(booksColor) }
                        Spacer(Modifier.width(5.dp))
                        Text(booksName, color = c.ink, fontSize = 11.sp, fontWeight = FontWeight(600))
                    }
                }
                Row(verticalAlignment = Alignment.Bottom) {
                    val symbol = currencySymbol(currency)
                    Text((symbol ?: "") + Numbers.amount(amount, lang), style = FigureStyle, color = c.ink)
                    if (symbol == null) {
                        Spacer(Modifier.width(6.dp))
                        Text(currencyName(currency, lang), color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(600),
                             modifier = Modifier.padding(bottom = 4.dp))
                    }
                }
                Text(summary, color = c.muted, fontSize = 12.sp)
                if (lines.isNotEmpty()) Column(
                    Modifier.fillMaxWidth().padding(top = 2.dp)
                        .drawWithCache {
                            onDrawBehind { drawRoundRect(c.surface2, cornerRadius = CornerRadius(10.dp.toPx())) }
                        }
                        .padding(horizontal = 10.dp, vertical = 4.dp),
                ) {
                    lines.forEach { line ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(Numbers.digits(line.code, lang) + "  ", color = c.muted, fontSize = 10.5.sp)
                            Text(line.name, color = c.ink, fontSize = 12.sp, modifier = Modifier.weight(1f))
                            val (label, value) = if (line.debit > 0) stringResource(R.string.debit_short) to line.debit
                                                 else stringResource(R.string.credit_short) to line.credit
                            Text("$label ${Numbers.amount(value, lang)}", color = c.ink, fontSize = 12.sp,
                                 style = FigureStyle.copy(fontSize = 12.sp, fontWeight = FontWeight(500)))
                        }
                    }
                }
            }
            Row(
                Modifier.fillMaxWidth().onPlaced { foldY = it.positionInParent().y }
                    .padding(start = 12.dp, end = if (state is VoucherState.Posted) 104.dp else 12.dp, top = 10.dp, bottom = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                when (state) {
                    VoucherState.Draft, VoucherState.Posting -> {
                        Button(onClick = {
                            if (Build.VERSION.SDK_INT >= 30) view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                            onConfirm()
                        }, enabled = state == VoucherState.Draft,
                            colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
                            Text(stringResource(R.string.voucher_post), fontWeight = FontWeight(700))
                        }
                        FilledTonalButton(onClick = onEdit, enabled = state == VoucherState.Draft,
                            colors = ButtonDefaults.filledTonalButtonColors(containerColor = c.firouzehSoft, contentColor = c.ink)) {
                            Text(stringResource(R.string.voucher_edit))
                        }
                        TextButton(onClick = onCancel, enabled = state == VoucherState.Draft) {
                            Text(stringResource(R.string.voucher_cancel), color = c.muted)
                        }
                    }
                    is VoucherState.Posted -> {
                        UndoRing(secondsLeft = state.undoSecondsLeft, total = 120)
                        if (state.undoSecondsLeft > 0) {
                            FilledTonalButton(onClick = onUndo,
                                colors = ButtonDefaults.filledTonalButtonColors(containerColor = c.firouzehSoft, contentColor = c.ink)) {
                                val m = state.undoSecondsLeft / 60
                                val s = state.undoSecondsLeft % 60
                                Text(stringResource(R.string.voucher_undo, Numbers.digits("$m:${"%02d".format(s)}", lang)),
                                     fontWeight = FontWeight(700))
                            }
                        }
                        footer?.invoke()
                    }
                    VoucherState.WaitingForApproval -> Text(stringResource(R.string.voucher_waits), color = c.saffron,
                                                          fontWeight = FontWeight(600), fontSize = 13.sp)
                    VoucherState.Cancelled -> Text(stringResource(R.string.voucher_cancelled), color = c.muted, fontSize = 13.sp)
                    VoucherState.Undone -> Text(stringResource(R.string.voucher_undone), color = c.muted, fontSize = 13.sp)
                    VoucherState.Rejected -> Text(stringResource(R.string.voucher_rejected), color = c.pomegranate,
                                                  fontWeight = FontWeight(600), fontSize = 13.sp)
                    VoucherState.Replaced -> Text(stringResource(R.string.voucher_replaced), color = c.muted, fontSize = 13.sp)
                    is VoucherState.ForApproval -> if (state.mine) {
                        Text(stringResource(R.string.voucher_waits), color = c.saffron, fontWeight = FontWeight(600),
                             fontSize = 13.sp, modifier = Modifier.weight(1f))
                        TextButton(onClick = onCancel, enabled = !state.busy) {
                            Text(stringResource(R.string.voucher_withdraw), color = c.muted)
                        }
                    } else {
                        Button(onClick = {
                            if (Build.VERSION.SDK_INT >= 30) view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                            onApprove()
                        }, enabled = !state.busy,
                            colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
                            Text(stringResource(R.string.voucher_approve), fontWeight = FontWeight(700))
                        }
                        TextButton(onClick = onReject, enabled = !state.busy) {
                            Text(stringResource(R.string.voucher_reject), color = c.pomegranate)
                        }
                    }
                }
            }
        }
        if (state is VoucherState.Posted) {
            Seal(number = state.number, date = state.date, lang = lang,
                 modifier = Modifier.align(Alignment.BottomEnd).padding(end = 14.dp, bottom = 6.dp))
        }
    }
}

@Composable
private fun UndoRing(secondsLeft: Int, total: Int) {
    val c = LocalAccountantColors.current
    Canvas(Modifier.size(26.dp)) {
        val stroke = 3.dp.toPx()
        drawCircle(c.line, radius = size.minDimension / 2 - stroke, style = Stroke(stroke))
        drawArc(c.firouzeh, startAngle = -90f, sweepAngle = 360f * secondsLeft / total, useCenter = false,
                topLeft = Offset(stroke, stroke),
                size = androidx.compose.ui.geometry.Size(size.width - 2 * stroke, size.height - 2 * stroke),
                style = Stroke(stroke, cap = androidx.compose.ui.graphics.StrokeCap.Round))
    }
}

/**
 * The round seal (مهر) pressed onto a posted voucher: number, «ثبت شد», date.
 * It lands with a spring; with animations turned off it simply appears.
 */
@Composable
fun Seal(number: String, date: String, lang: String, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    var landed by remember { mutableFloatStateOf(0f) }
    val scale by animateFloatAsState(if (landed > 0f) 1f else 1.9f,
        spring(dampingRatio = 0.45f, stiffness = Spring.StiffnessMediumLow), label = "seal-scale")
    val alpha by animateFloatAsState(if (landed > 0f) 0.92f else 0f, spring(stiffness = Spring.StiffnessMedium),
        label = "seal-alpha")
    androidx.compose.runtime.LaunchedEffect(Unit) { landed = 1f }
    val posted = stringResource(R.string.voucher_posted)
    // a reference with Latin letters (INV-1042) keeps its own digits
    val numberText = stringResource(R.string.voucher_number,
        if (number.any { it in 'A'..'Z' || it in 'a'..'z' }) number else Numbers.digits(number, lang))
    Box(
        modifier.size(80.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale; rotationZ = -12f; this.alpha = alpha }
            .semantics { contentDescription = if (number.isBlank()) posted else "$posted, $numberText" },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.matchParentSize()) {
            val outer = size.minDimension / 2
            drawCircle(c.firouzeh, radius = outer - 1.dp.toPx(), style = Stroke(1.5.dp.toPx()))
            drawCircle(c.firouzeh, radius = outer - 5.5.dp.toPx(), style = Stroke(2.5.dp.toPx()))
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            if (number.isNotBlank()) Text(numberText, color = c.firouzeh, fontSize = 8.5.sp, lineHeight = 10.sp,
                 fontWeight = FontWeight(600), textAlign = TextAlign.Center)
            Text(posted, color = c.firouzeh, fontSize = 13.sp, lineHeight = 16.sp, fontWeight = FontWeight(800),
                 textAlign = TextAlign.Center)
            Text(Numbers.digits(date, lang), color = c.firouzeh, fontSize = 8.sp, lineHeight = 10.sp,
                 fontWeight = FontWeight(600))
        }
    }
}

fun currencyName(code: String, lang: String): String = when (code.uppercase()) {
    "IRR" -> if (lang == "fa") "ریال" else "IRR"
    else -> code
}

/** Currencies written with a symbol before the number. */
fun currencySymbol(code: String): String? = when (code.uppercase()) {
    "GBP" -> "£"
    "USD" -> "$"
    "EUR" -> "€"
    else -> null
}
