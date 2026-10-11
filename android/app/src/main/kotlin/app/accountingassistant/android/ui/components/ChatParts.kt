package app.accountingassistant.android.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.runtime.getValue
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.background
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.LinearEasing
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.AbsoluteRoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.AbsoluteAlignment
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.theme.FigureStyle
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.ui.theme.Vazirmatn

/** Which books every message and card belongs to: name and colour, always visible. */
@Composable
fun BooksBadge(name: String, color: Color, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Surface(onClick = onClick, shape = CircleShape, color = c.surface, border = BorderStroke(1.dp, c.line), modifier = modifier) {
        Row(Modifier.padding(start = 10.dp, end = 10.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Canvas(Modifier.size(9.dp)) { drawCircle(color) }
            Spacer(Modifier.width(7.dp))
            Text(name, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(600), maxLines = 1)
            Spacer(Modifier.width(4.dp))
            Icon(AppIcons.ChevronDown, contentDescription = stringResource(R.string.books_switch),
                 tint = c.muted, modifier = Modifier.size(14.dp))
        }
    }
}

/** What the person sent: a dark bubble on the right, in Persian and English alike. */
@Composable
fun UserBubble(text: String, from: String? = null, modifier: Modifier = Modifier, files: List<String> = emptyList()) {
    val c = LocalAccountantColors.current
    Box(modifier.fillMaxWidth(), contentAlignment = AbsoluteAlignment.CenterRight) {
        Surface(color = c.bubble, shape = AbsoluteRoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp),
                modifier = Modifier.widthIn(max = 280.dp)) {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                if (from != null) Text(from, color = c.onBubble.copy(alpha = 0.7f), fontSize = 10.5.sp)
                files.forEach { name ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(AppIcons.Clip, contentDescription = null, tint = c.onBubble.copy(alpha = 0.8f), modifier = Modifier.size(13.dp))
                        Spacer(Modifier.width(4.dp))
                        Text(name, color = c.onBubble.copy(alpha = 0.85f), fontSize = 12.sp, maxLines = 1)
                    }
                }
                // each message in its own direction: "will we have enough cash?" keeps its question mark at the end
                if (text.isNotEmpty()) Text(text, color = c.onBubble, fontSize = 14.sp, lineHeight = 21.sp,
                     style = TextStyle(fontFeatureSettings = "tnum", textDirection = TextDirection.Content))
            }
        }
    }
}

/** The accountant's own words: plain text, no bubble, one short paragraph. */
@Composable
fun AssistantText(text: String, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Text(text, color = c.ink, fontSize = 14.sp, lineHeight = 22.sp, modifier = modifier.fillMaxWidth(),
         style = TextStyle(textDirection = TextDirection.Content))
}

/** One question's likely answers as chips; the picked one is tinted firouzeh. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Choices(options: List<String>, selected: Int?, onPick: (Int) -> Unit, enabled: Boolean = true, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    FlowRow(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEachIndexed { i, label ->
            val on = i == selected
            Surface(onClick = { onPick(i) }, enabled = enabled, shape = CircleShape,
                    color = if (on) c.firouzehSoft else c.surface,
                    border = BorderStroke(1.dp, if (on) c.firouzeh else c.line)) {
                Text(label, color = c.ink, fontSize = 13.sp, fontWeight = if (on) FontWeight(600) else FontWeight(400),
                     modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
            }
        }
    }
}

/**
 * One number that matters, with its comparison, how fresh it is, and its
 * trend drawn to scale with the latest point marked.
 */
@Composable
fun FigureCard(
    label: String, value: String, unit: String?, delta: String?, deltaIsBad: Boolean,
    freshness: String?, series: List<Float>, modifier: Modifier = Modifier,
) {
    val c = LocalAccountantColors.current
    Surface(color = c.surface, shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, c.line), modifier = modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = c.muted, fontSize = 11.sp)
                if (freshness != null) Row(verticalAlignment = Alignment.CenterVertically) {
                    Canvas(Modifier.size(6.dp)) { drawCircle(c.firouzeh) }
                    Spacer(Modifier.width(4.dp))
                    Text(freshness, color = c.muted, fontSize = 11.sp)
                }
            }
            Row(verticalAlignment = Alignment.Bottom) {
                Text(value, style = FigureStyle, color = c.ink)
                if (unit != null) {
                    Spacer(Modifier.width(6.dp))
                    Text(unit, color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(600), modifier = Modifier.padding(bottom = 4.dp))
                }
            }
            if (delta != null) Text(delta, color = if (deltaIsBad) c.pomegranate else c.firouzeh, fontSize = 12.sp, fontWeight = FontWeight(600))
            if (series.size >= 2) Sparkline(series, Modifier.fillMaxWidth().height(46.dp))
        }
    }
}

@Composable
private fun Sparkline(series: List<Float>, modifier: Modifier) {
    val c = LocalAccountantColors.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Canvas(modifier) {
        val lo = series.min()
        val hi = series.max()
        val span = (hi - lo).takeIf { it > 0f } ?: 1f
        val top = 6.dp.toPx()
        val bottom = size.height - 6.dp.toPx()
        // time runs with the reading direction: right to left in Persian
        fun x(i: Int) = size.width * i / (series.size - 1).toFloat()
        fun px(i: Int) = if (rtl) size.width - x(i) else x(i)
        fun y(v: Float) = bottom - (v - lo) / span * (bottom - top)
        val dash = PathEffect.dashPathEffect(floatArrayOf(2.dp.toPx(), 3.dp.toPx()))
        drawLine(c.line, Offset(0f, top), Offset(size.width, top), 1.dp.toPx(), pathEffect = dash)
        drawLine(c.line, Offset(0f, bottom), Offset(size.width, bottom), 1.dp.toPx(), pathEffect = dash)
        val line = Path().apply {
            series.forEachIndexed { i, v -> if (i == 0) moveTo(px(i), y(v)) else lineTo(px(i), y(v)) }
        }
        val area = Path().apply {
            addPath(line)
            lineTo(px(series.lastIndex), size.height)
            lineTo(px(0), size.height)
            close()
        }
        drawPath(area, c.firouzehSoft)
        drawPath(line, c.firouzeh, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        val end = Offset(px(series.lastIndex), y(series.last()))
        drawCircle(c.surface, radius = 5.dp.toPx(), center = end)
        drawCircle(c.firouzeh, radius = 3.5.dp.toPx(), center = end)
    }
}

/** While the tools run: Material's morphing shape and words saying what is being read. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ThinkingRow(text: String, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        LoadingIndicator(Modifier.size(22.dp), color = c.firouzeh)
        Spacer(Modifier.width(8.dp))
        Text(text, color = c.muted, fontSize = 12.sp)
    }
}

/**
 * The floating composer: attach, type, then hold to speak or send. The one
 * translucent thing on the screen. While the mic is held it listens.
 */
@Composable
fun Composer(
    value: String, onValueChange: (String) -> Unit, onSend: () -> Unit, onAttach: () -> Unit,
    onSpeakStart: () -> Unit, onSpeakEnd: () -> Unit,
    focus: androidx.compose.ui.focus.FocusRequester? = null,
    modifier: Modifier = Modifier,
    listening: Boolean = false,
    canSend: Boolean = value.isNotBlank(),
) {
    val c = LocalAccountantColors.current
    Surface(shape = CircleShape, color = c.glass, border = BorderStroke(1.dp, c.line.copy(alpha = 0.6f)),
            shadowElevation = 6.dp, modifier = modifier.fillMaxWidth()) {
        Row(Modifier.padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onAttach, enabled = !listening,
                       colors = IconButtonDefaults.iconButtonColors(containerColor = c.surface2, contentColor = c.ink),
                       modifier = Modifier.size(38.dp)) {
                Icon(AppIcons.Plus, contentDescription = stringResource(R.string.attach), modifier = Modifier.size(18.dp))
            }
            Box(Modifier.weight(1f).padding(horizontal = 10.dp), contentAlignment = Alignment.CenterStart) {
                if (listening) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        VoiceBars(Modifier.size(width = 54.dp, height = 22.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(stringResource(R.string.listening), color = c.muted, fontSize = 13.sp, maxLines = 1)
                    }
                } else {
                    if (value.isEmpty()) Text(stringResource(R.string.composer_hint), color = c.muted, fontSize = 14.sp, maxLines = 1)
                    BasicTextField(value = value, onValueChange = onValueChange, cursorBrush = SolidColor(c.firouzeh),
                        textStyle = TextStyle(color = c.ink, fontSize = 14.sp, fontFamily = Vazirmatn), maxLines = 4,
                        modifier = Modifier.fillMaxWidth().let { m -> focus?.let { m.focusRequester(it) } ?: m })
                }
            }
            if (canSend && !listening) {
                IconButton(onClick = onSend,
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh),
                           modifier = Modifier.size(38.dp)) {
                    Icon(AppIcons.Send, contentDescription = stringResource(R.string.send), modifier = Modifier.size(18.dp))
                }
            } else {
                val speak = stringResource(R.string.speak)
                Box(
                    Modifier.size(if (listening) 44.dp else 38.dp).clip(CircleShape)
                        .background(if (listening) c.pomegranate else c.firouzeh)
                        .semantics { contentDescription = speak; role = Role.Button }
                        .pointerInput(Unit) {
                            detectTapGestures(onPress = {
                                onSpeakStart()
                                tryAwaitRelease()
                                onSpeakEnd()
                            })
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(AppIcons.Mic, contentDescription = null, tint = c.onFirouzeh, modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

/** Listening: bars that rise and fall; still, when animations are off. */
@Composable
fun VoiceBars(modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    val t = rememberInfiniteTransition(label = "voice")
    val phase by t.animateFloat(0f, (2 * Math.PI).toFloat(),
        infiniteRepeatable(tween(1100, easing = LinearEasing)), label = "voice-phase")
    Canvas(modifier) {
        val n = 9
        val w = size.width / (n * 2 - 1)
        for (i in 0 until n) {
            val h = size.height * (0.3f + 0.7f * kotlin.math.abs(kotlin.math.sin(phase + i * 0.7f)))
            drawRoundRect(c.firouzeh, topLeft = Offset(i * 2 * w, (size.height - h) / 2),
                          size = androidx.compose.ui.geometry.Size(w, h),
                          cornerRadius = androidx.compose.ui.geometry.CornerRadius(w / 2))
        }
    }
}
