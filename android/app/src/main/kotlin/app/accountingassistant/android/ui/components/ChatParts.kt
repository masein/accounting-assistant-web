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
import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.ui.text.style.TextOverflow
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
import androidx.compose.material3.CircularWavyProgressIndicator
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
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import androidx.compose.animation.core.RepeatMode
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.offset
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.IntOffset
import app.accountingassistant.android.util.Numbers
import kotlinx.coroutines.delay
import kotlin.math.max
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
/**
 * What the user sent, in its bubble on the right. [quoteFrom] and [quoteText]:
 * the message it answers, above its words (scenario AC4). [held] goes on the
 * bubble itself: holding it down opens Reply, Copy, Share.
 */
@Composable
fun UserBubble(text: String, from: String? = null, modifier: Modifier = Modifier, files: List<String> = emptyList(),
               quoteFrom: String? = null, quoteText: String? = null, held: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Box(modifier.fillMaxWidth(), contentAlignment = AbsoluteAlignment.CenterRight) {
        Surface(color = c.bubble, shape = AbsoluteRoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp),
                modifier = Modifier.widthIn(max = 280.dp).then(held)) {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                if (from != null) Text(from, color = c.onBubble.copy(alpha = 0.7f), fontSize = 10.5.sp)
                if (quoteText != null) Quoted(quoteFrom, quoteText, ink = c.onBubble, Modifier.padding(bottom = 6.dp))
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

/** A quoted message: a bar at its start, whose it was, its first words. */
@Composable
fun Quoted(from: String?, text: String, ink: Color, modifier: Modifier = Modifier, lines: Int = 2, accent: Color = ink) {
    Row(modifier.height(IntrinsicSize.Min)) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(accent.copy(alpha = 0.6f), RoundedCornerShape(2.dp)))
        Spacer(Modifier.width(8.dp))
        Column {
            if (from != null) Text(from, color = accent.copy(alpha = 0.9f), fontSize = 11.sp, fontWeight = FontWeight(700))
            Text(text, color = ink.copy(alpha = 0.75f), fontSize = 12.5.sp, lineHeight = 18.sp, maxLines = lines,
                 overflow = TextOverflow.Ellipsis, style = TextStyle(textDirection = TextDirection.Content))
        }
    }
}

/** Above the composer while a reply is being written: what it answers, and ✕ to answer nothing. */
@Composable
fun ReplyingTo(label: String, text: String, onDrop: () -> Unit, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Surface(shape = RoundedCornerShape(14.dp), color = c.surface, border = BorderStroke(1.dp, c.line), modifier = modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 12.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Quoted(label, text, ink = c.ink, Modifier.weight(1f), lines = 1, accent = c.firouzeh)
            val drop = stringResource(R.string.reply_drop)
            IconButton(onClick = onDrop, modifier = Modifier.semantics { contentDescription = drop }) {
                Icon(AppIcons.Close, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
            }
        }
    }
}

/** The accountant's own words: plain text, no bubble, one short paragraph. */
@Composable
fun AssistantText(text: String, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    Text(boldMarks(text), color = c.ink, fontSize = 14.sp, lineHeight = 22.sp, modifier = modifier.fillMaxWidth(),
         style = TextStyle(textDirection = TextDirection.Content))
}

/**
 * «**ریال ایران (IRR)**» as bold words, not with its stars: the model writes Markdown
 * now and then, and the phone showed the marks (owner's phone, 2026-10-16). Only the
 * paired «**» or «__»; a lone star stays.
 */
fun boldMarks(text: String): AnnotatedString = buildAnnotatedString {
    var i = 0
    val marks = Regex("""\*\*(.+?)\*\*|__(.+?)__""")
    for (m in marks.findAll(text)) {
        append(text.substring(i, m.range.first))
        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(m.groupValues[1].ifEmpty { m.groupValues[2] }) }
        i = m.range.last + 1
    }
    append(text.substring(i))
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

/**
 * While the tools run: Material's morphing shape and words saying what is
 * being read. While a file goes, a wavy ring fills with how much has gone.
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ThinkingRow(text: String, modifier: Modifier = Modifier, progress: Float? = null) {
    val c = LocalAccountantColors.current
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        if (progress != null) {
            val shown by animateFloatAsState(progress, label = "upload")
            // Material's ring is drawn for 48 dp; at 22 dp a shorter, gentler wave and thinner strokes keep it a ring
            val stroke = Stroke(with(LocalDensity.current) { 2.5.dp.toPx() }, cap = StrokeCap.Round)
            CircularWavyProgressIndicator(progress = { shown }, modifier = Modifier.size(22.dp),
                                          color = c.firouzeh, trackColor = c.line, stroke = stroke, trackStroke = stroke,
                                          gapSize = 3.dp, amplitude = { if (it <= 0f || it >= 1f) 0f else 0.45f },
                                          wavelength = 7.dp)
        } else LoadingIndicator(Modifier.size(22.dp), color = c.firouzeh)
        Spacer(Modifier.width(8.dp))
        Text(text, color = c.muted, fontSize = 12.sp)
    }
}

/**
 * The floating composer: attach, type, then speak or send. The one translucent thing on the
 * screen. The microphone (scenarios W1–W6): hold it to talk and let go to have the words
 * written in the box; slide toward the start side while holding to throw the recording away;
 * tap it to record hands-free, then ✓ writes and ✕ throws away. While it records, a red dot,
 * the time and bars that follow the voice take the text's place; then «writing…».
 */
@Composable
fun Composer(
    value: String, onValueChange: (String) -> Unit, onSend: () -> Unit, onAttach: () -> Unit,
    onSpeakStart: () -> Unit, onSpeakEnd: () -> Unit,
    focus: androidx.compose.ui.focus.FocusRequester? = null,
    modifier: Modifier = Modifier,
    listening: Boolean = false,
    canSend: Boolean = value.isNotBlank(),
    onSpeakCancel: () -> Unit = {},
    level: () -> Float = { 0f },
    transcribing: Boolean = false,
    lang: String = "en",
) {
    val c = LocalAccountantColors.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val density = LocalDensity.current
    val haptic = LocalHapticFeedback.current
    val cancelPx = with(density) { 96.dp.toPx() }
    val nowListening by rememberUpdatedState(listening)
    var handsFree by remember { mutableStateOf(false) }
    var holding by remember { mutableStateOf(false) }
    var drag by remember { mutableFloatStateOf(0f) }           // px toward the start side
    var elapsed by remember { mutableLongStateOf(0L) }
    val levels = remember { mutableStateListOf<Float>() }
    LaunchedEffect(listening) {
        if (!listening) { handsFree = false; holding = false; drag = 0f; levels.clear(); elapsed = 0L; return@LaunchedEffect }
        val t0 = System.currentTimeMillis()
        while (true) {
            elapsed = System.currentTimeMillis() - t0
            levels.add(level())
            while (levels.size > 40) levels.removeAt(0)
            if (elapsed >= 120_000) { onSpeakEnd(); break }    // two minutes: stop and write (W6)
            delay(80)
        }
    }
    // nearly opaque: through the glass the messages scrolled behind read through the hint (2026-10-21)
    Surface(shape = CircleShape, color = c.glass.copy(alpha = 0.97f), border = BorderStroke(1.dp, c.line.copy(alpha = 0.6f)),
            shadowElevation = 6.dp, modifier = modifier.fillMaxWidth()) {
        Row(Modifier.padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            if (listening && handsFree) {
                val cancel = stringResource(R.string.voice_cancel)
                IconButton(onClick = { handsFree = false; onSpeakCancel() },
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.surface2, contentColor = c.pomegranate),
                           modifier = Modifier.size(38.dp).semantics { contentDescription = cancel }) {
                    Icon(AppIcons.Close, contentDescription = null, modifier = Modifier.size(18.dp))
                }
            } else {
                IconButton(onClick = onAttach, enabled = !listening && !transcribing,
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.surface2, contentColor = c.ink),
                           modifier = Modifier.size(38.dp)) {
                    Icon(AppIcons.Plus, contentDescription = stringResource(R.string.attach), modifier = Modifier.size(18.dp))
                }
            }
            Box(Modifier.weight(1f).padding(horizontal = 10.dp), contentAlignment = Alignment.CenterStart) {
                when {
                    listening -> Row(verticalAlignment = Alignment.CenterVertically) {
                        RecordingDot()
                        Spacer(Modifier.width(6.dp))
                        Text(Numbers.duration(elapsed / 1000, lang), color = c.ink, fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                             style = FigureStyle, maxLines = 1)
                        Spacer(Modifier.width(8.dp))
                        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
                            LevelBars(levels, Modifier.fillMaxWidth().height(24.dp).alpha(if (drag > 0f) 0.25f else 1f))
                            if (holding) {
                                val near = drag > cancelPx * 0.6f
                                Text("‹ " + stringResource(R.string.slide_to_cancel), maxLines = 1, fontSize = 12.5.sp,
                                     color = if (near) c.pomegranate else c.muted,
                                     fontWeight = if (near) FontWeight.SemiBold else FontWeight.Normal,
                                     modifier = Modifier.offset { IntOffset(if (rtl) drag.toInt() else -drag.toInt(), 0) }
                                         .background(c.glass, RoundedCornerShape(6.dp)).padding(horizontal = 4.dp))
                            }
                        }
                    }
                    transcribing -> WritingText(stringResource(R.string.transcribing))
                    else -> {
                        if (value.isEmpty()) Text(stringResource(R.string.composer_hint), color = c.muted, fontSize = 14.sp, maxLines = 1)
                        BasicTextField(value = value, onValueChange = onValueChange, cursorBrush = SolidColor(c.firouzeh),
                            // in the direction of what is typed: "and in dollars?" in the Persian app read «?and in dollars»
                            textStyle = TextStyle(color = c.ink, fontSize = 14.sp, fontFamily = Vazirmatn, textDirection = TextDirection.Content), maxLines = 4,
                            modifier = Modifier.fillMaxWidth().let { m -> focus?.let { m.focusRequester(it) } ?: m })
                    }
                }
            }
            if (canSend && !listening && !transcribing) {
                IconButton(onClick = onSend,
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh),
                           modifier = Modifier.size(38.dp)) {
                    Icon(AppIcons.Send, contentDescription = stringResource(R.string.send), modifier = Modifier.size(18.dp))
                }
            } else if (listening && handsFree) {
                val done = stringResource(R.string.voice_done)
                IconButton(onClick = { handsFree = false; onSpeakEnd() },
                           colors = IconButtonDefaults.iconButtonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh),
                           modifier = Modifier.size(44.dp).semantics { contentDescription = done }) {
                    Icon(AppIcons.Check, contentDescription = null, modifier = Modifier.size(20.dp))
                }
            } else {
                val speak = stringResource(R.string.speak)
                val grow by animateFloatAsState(if (holding) 1.25f else 1f, label = "mic-hold")
                Box(
                    Modifier.size(if (listening) 44.dp else 38.dp).scale(grow).clip(CircleShape)
                        .background(if (listening) c.pomegranate else c.firouzeh)
                        .semantics { contentDescription = speak; role = Role.Button }
                        .pointerInput(transcribing) {
                            if (transcribing) return@pointerInput
                            awaitEachGesture {
                                val down = awaitFirstDown()
                                var upAt = down.uptimeMillis              // the touch's own clock, not the wall's
                                holding = true
                                drag = 0f
                                onSpeakStart()
                                var cancelled = false
                                while (true) {
                                    val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
                                    upAt = change.uptimeMillis
                                    if (!change.pressed) break
                                    val dx = change.position.x - down.position.x
                                    drag = max(0f, if (rtl) dx else -dx)
                                    change.consume()
                                    if (drag > cancelPx) { cancelled = true; break }
                                }
                                holding = false
                                drag = 0f
                                when {
                                    cancelled -> {                         // slid away: throw it away (W4)
                                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                                        if (nowListening) onSpeakCancel()
                                        do { val e = awaitPointerEvent() } while (e.changes.any { it.pressed })
                                    }
                                    !nowListening -> Unit                  // the microphone was refused or is still asked for
                                    upAt - down.uptimeMillis < 300 -> handsFree = true          // a tap: hands-free (W1)
                                    else -> onSpeakEnd()                   // let go after holding: write it (W3)
                                }
                            }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(AppIcons.Mic, contentDescription = null, tint = c.onFirouzeh, modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

/** A red dot that breathes while recording; still, when animations are off. */
@Composable
private fun RecordingDot() {
    val c = LocalAccountantColors.current
    val t = rememberInfiniteTransition(label = "rec-dot")
    val a by t.animateFloat(1f, 0.25f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "rec-dot-alpha")
    Box(Modifier.size(9.dp).alpha(a).clip(CircleShape).background(c.pomegranate))
}

/** The voice's level as it comes: the newest bar at the end, older ones moving toward the start. */
@Composable
fun LevelBars(levels: List<Float>, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Canvas(modifier) {
        val bar = 3.dp.toPx()
        val gap = 2.dp.toPx()
        val n = (size.width / (bar + gap)).toInt().coerceAtLeast(1)
        val shown = levels.takeLast(n)
        shown.forEachIndexed { i, v ->
            val slot = shown.size - 1 - i                         // 0 = the newest
            val x = if (rtl) slot * (bar + gap) else size.width - (slot + 1) * (bar + gap) + gap
            val h = max(2.dp.toPx(), v * size.height)
            drawRoundRect(c.pomegranate.copy(alpha = 0.45f + 0.55f * (i + 1) / shown.size),
                          topLeft = Offset(x, (size.height - h) / 2), size = androidx.compose.ui.geometry.Size(bar, h),
                          cornerRadius = androidx.compose.ui.geometry.CornerRadius(bar / 2))
        }
    }
}

/** «Writing what you said…» with a light that passes over it. */
@Composable
private fun WritingText(text: String) {
    val c = LocalAccountantColors.current
    val t = rememberInfiniteTransition(label = "writing")
    val x by t.animateFloat(-1f, 2f, infiniteRepeatable(tween(1400, easing = LinearEasing)), label = "writing-x")
    val brush = androidx.compose.ui.graphics.Brush.linearGradient(
        0f to c.muted, ((x - 0.3f).coerceIn(0f, 1f)) to c.muted, (x.coerceIn(0f, 1f)) to c.firouzeh,
        ((x + 0.3f).coerceIn(0f, 1f)) to c.muted, 1f to c.muted,
        start = Offset.Zero, end = Offset(600f, 0f))
    Text(text, maxLines = 1, style = TextStyle(brush = brush, fontSize = 13.5.sp, fontFamily = Vazirmatn))
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
