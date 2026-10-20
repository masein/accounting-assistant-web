package app.accountingassistant.android.ui.chat

import android.provider.Settings
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.components.currencyName
import app.accountingassistant.android.ui.components.currencySymbol
import app.accountingassistant.android.ui.theme.AccountantColors
import app.accountingassistant.android.ui.theme.FigureStyle
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers
import kotlinx.coroutines.launch
import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.random.Random

/** Reviews already shown moving in this process: scrolled back into view, a card stays still. */
private val moved = mutableSetOf<String>()

/**
 * Item ``i`` of ``n``'s share of the card's progress ``p``: each starts ``step`` after the one before, after
 * ``delay``, and every one ends at 1 when ``p`` does (an item stopping at 0.82 left an idea half faded).
 */
internal fun stagger(p: Float, i: Int, n: Int, step: Float = 0.08f, delay: Float = 0f): Float {
    if (p >= 1f) return 1f
    val span = (1f - delay - step * (n - 1).coerceAtLeast(0)).coerceAtLeast(0.2f)
    return ((p - delay - step * i) / span).coerceIn(0f, 1f)
}

private fun moodColor(mood: String, c: AccountantColors): Color = when (mood) {
    "good" -> if (c.isDark) Color(0xFF34D399) else Color(0xFF0F9D6E)
    "watch" -> c.saffron
    "nudge" -> if (c.isDark) Color(0xFFA594F9) else Color(0xFF6D5BD0)
    else -> c.firouzeh
}

/**
 * The app's own week, month or nudge as a card (scenario AL17): its emoji pops in, the figure counts up,
 * its change against the usual, the weeks or months as bars that grow with the usual line across them,
 * the month's savings ring filling, the categories as bars with their usual marked, and the ideas with a
 * button each that asks the assistant. A good one throws a little confetti. With the system's animations
 * off, it is all there at once.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReviewCard(item: ChatItem.Review, lang: String, onAsk: (String) -> Unit, modifier: Modifier = Modifier) {
    val c = LocalAccountantColors.current
    val mood = moodColor(item.mood, c)
    val context = LocalContext.current
    val still = remember {
        runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
            .getOrDefault(false)
    }
    val first = remember(item.id) { item.id !in moved && !still }
    val progress = remember(item.id) { Animatable(if (first) 0f else 1f) }
    val pop = remember(item.id) { Animatable(if (first) 0.4f else 1f) }
    LaunchedEffect(item.id) {
        if (first) {
            moved += item.id
            launch { pop.animateTo(1f, spring(dampingRatio = 0.42f, stiffness = 320f)) }
            progress.animateTo(1f, tween(1200, easing = FastOutSlowInEasing))
        }
    }
    var asked by remember(item.id) { mutableStateOf<String?>(null) }
    val places = Numbers.places(item.currency)
    val p = progress.value

    Box(modifier.fillMaxWidth()) {
        Surface(shape = RoundedCornerShape(22.dp), color = c.surface, border = BorderStroke(1.dp, mood.copy(alpha = 0.32f)),
                modifier = Modifier.fillMaxWidth()) {
            Column(
                Modifier.background(Brush.verticalGradient(listOf(mood.copy(alpha = if (c.isDark) 0.22f else 0.13f), Color.Transparent)))
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Box(
                        Modifier.size(46.dp).graphicsLayer { scaleX = pop.value; scaleY = pop.value; rotationZ = (1f - pop.value) * -25f }
                            .background(c.surface, RoundedCornerShape(15.dp))
                            .border(1.dp, mood.copy(alpha = 0.28f), RoundedCornerShape(15.dp)),
                        contentAlignment = Alignment.Center,
                    ) { Text(item.emoji, fontSize = 24.sp) }
                    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        val badge = when (item.kind) {
                            "weekly" -> R.string.review_weekly
                            "monthly" -> R.string.review_monthly
                            "afford" -> R.string.review_afford
                            else -> R.string.review_nudge
                        }
                        Surface(color = mood.copy(alpha = 0.15f), shape = CircleShape) {
                            Text(stringResource(badge), color = mood, fontSize = 10.5.sp, fontWeight = FontWeight(700),
                                 modifier = Modifier.padding(horizontal = 9.dp, vertical = 2.dp))
                        }
                        Text(item.title, color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700),
                             style = TextStyle(textDirection = TextDirection.Content))
                    }
                }

                item.headline?.let { h ->
                    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        // can I afford it? — the answer is the headline, the price under it (AM9)
                        if (item.kind == "afford") Text(h.label, color = mood, fontSize = 16.sp, fontWeight = FontWeight(800),
                                                        style = TextStyle(textDirection = TextDirection.Content))
                        else Text(h.label, color = c.muted, fontSize = 12.sp)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            val shown = h.value.multiply(BigDecimal(p.toDouble())).setScale(places, RoundingMode.HALF_UP)
                            val symbol = currencySymbol(item.currency ?: "")
                            Text((symbol ?: "") + Numbers.amount(if (p >= 1f) h.value else shown, lang, places),
                                 style = FigureStyle.copy(fontSize = 30.sp, fontWeight = FontWeight(800)), color = c.ink)
                            if (symbol == null && !item.currency.isNullOrBlank()) {
                                Text(currencyName(item.currency, lang), color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(600))
                            }
                            h.delta?.let { d ->
                                val tint = if (d.good) moodColor("good", c) else c.saffron
                                val arrow = when (d.dir) { "up" -> "▲ "; "down" -> "▼ "; else -> "● " }
                                Surface(color = tint.copy(alpha = 0.14f), shape = CircleShape,
                                        modifier = Modifier.graphicsLayer {
                                            val q = ((p - 0.55f) / 0.45f).coerceIn(0f, 1f)
                                            alpha = q; scaleX = 0.7f + 0.3f * q; scaleY = 0.7f + 0.3f * q
                                        }) {
                                    Text(arrow + d.text, color = tint, fontSize = 12.sp, fontWeight = FontWeight(700),
                                         modifier = Modifier.padding(horizontal = 10.dp, vertical = 3.dp))
                                }
                            }
                        }
                    }
                }

                item.chart?.let { chart -> Bars(chart, mood, p) }

                item.ring?.let { ring ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                        Canvas(Modifier.size(76.dp)) {
                            val stroke = 9.dp.toPx()
                            val inset = stroke / 2
                            val arcSize = Size(size.width - stroke, size.height - stroke)
                            drawArc(mood.copy(alpha = 0.16f), 0f, 360f, false, Offset(inset, inset), arcSize, style = Stroke(stroke))
                            drawArc(mood, -90f, 360f * (ring.pct / 100f) * FastOutSlowInEasing.transform(p), false,
                                    Offset(inset, inset), arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
                        }
                        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(ring.label, color = c.muted, fontSize = 12.sp)
                            Text(ring.display, color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(800))
                            Text(ring.text, color = mood, fontSize = 12.sp, fontWeight = FontWeight(700))
                        }
                    }
                }

                if (item.categories.isNotEmpty()) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        item.categoriesTitle?.let { Text(it, color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(700)) }
                        item.categories.forEachIndexed { i, cat ->
                            val q = FastOutSlowInEasing.transform(stagger(p, i, item.categories.size, delay = 0.15f))
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    Text(cat.name, color = c.ink, fontSize = 13.sp, fontWeight = FontWeight(600), modifier = Modifier.weight(1f),
                                         style = TextStyle(textDirection = TextDirection.Content), maxLines = 1)
                                    Text(Numbers.amount(cat.value, lang, places), color = c.ink, fontSize = 13.sp,
                                         style = FigureStyle.copy(fontSize = 13.sp, fontWeight = FontWeight(500)))
                                }
                                Box(Modifier.fillMaxWidth().height(9.dp).clip(CircleShape).background(c.surface2)) {
                                    Box(Modifier.fillMaxWidth((cat.width.coerceIn(0.02f, 1f)) * q).fillMaxHeight().clip(CircleShape)
                                        .background(Brush.horizontalGradient(listOf(mood.copy(alpha = 0.55f), mood))))
                                    cat.usualAt?.let { at ->
                                        Box(Modifier.fillMaxWidth(at.coerceIn(0.01f, 1f)).fillMaxHeight()) {
                                            Box(Modifier.align(Alignment.CenterEnd).width(2.dp).fillMaxHeight().background(c.ink.copy(alpha = 0.45f)))
                                        }
                                    }
                                }
                                val tone = when (cat.dir) { "up" -> c.saffron; "down" -> moodColor("good", c); else -> c.muted }
                                Text(cat.sub, color = tone, fontSize = 11.sp, fontWeight = if (cat.dir == "up" || cat.dir == "down") FontWeight(600) else FontWeight(400))
                            }
                        }
                    }
                }

                if (item.facts.isNotEmpty()) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        item.facts.forEach { fact ->
                            Surface(shape = RoundedCornerShape(14.dp), color = c.surface, border = BorderStroke(1.dp, c.line)) {
                                Text(fact, color = c.ink, fontSize = 12.sp, style = TextStyle(textDirection = TextDirection.Content),
                                     modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp))
                            }
                        }
                    }
                }

                if (item.ideas.isNotEmpty()) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        item.ideasTitle?.let { Text(it, color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(700)) }
                        item.ideas.forEachIndexed { i, idea ->
                            val q = stagger(p, i, item.ideas.size, step = 0.15f, delay = 0.35f)
                            Surface(shape = RoundedCornerShape(16.dp), color = c.surface, border = BorderStroke(1.dp, mood.copy(alpha = 0.26f)),
                                    modifier = Modifier.fillMaxWidth().graphicsLayer { alpha = q; translationY = (1f - q) * 18.dp.toPx() }) {
                                Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    Text(idea.icon, fontSize = 20.sp)
                                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                        Text(idea.text, color = c.ink, fontSize = 13.sp, lineHeight = 19.sp,
                                             style = TextStyle(textDirection = TextDirection.Content))
                                        Button(onClick = { asked = idea.ask; onAsk(idea.ask) }, enabled = asked == null,
                                               shape = RoundedCornerShape(50),
                                               colors = ButtonDefaults.buttonColors(containerColor = mood, contentColor = Color.White,
                                                                                    disabledContainerColor = mood.copy(alpha = if (asked == idea.ask) 0.75f else 0.3f),
                                                                                    disabledContentColor = Color.White)) {
                                            Text(idea.ask, fontSize = 12.5.sp, fontWeight = FontWeight(600), textAlign = TextAlign.Start,
                                                 style = TextStyle(textDirection = TextDirection.Content))
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                item.note?.let { Text(it, color = c.muted, fontSize = 11.5.sp, style = TextStyle(textDirection = TextDirection.Content)) }
            }
        }
        if (first && item.mood == "good") Confetti(listOf(moodColor("good", c), c.saffron, c.firouzeh, c.pomegranate))
    }
}

@Composable
private fun Bars(chart: ChatItem.Review.Chart, mood: Color, p: Float) {
    val c = LocalAccountantColors.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val bars = chart.bars
    val max = (bars.maxOf { maxOf(it.value, it.value2 ?: BigDecimal.ZERO) }.max(chart.usual ?: BigDecimal.ZERO))
        .toFloat().coerceAtLeast(1f)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(chart.title, color = c.muted, fontSize = 12.sp, fontWeight = FontWeight(700), modifier = Modifier.weight(1f))
            if (chart.usual != null) {
                Canvas(Modifier.width(16.dp).height(2.dp)) {
                    drawLine(c.muted, Offset(0f, 0f), Offset(size.width, 0f), strokeWidth = 2.dp.toPx(),
                             pathEffect = PathEffect.dashPathEffect(floatArrayOf(4f, 3f)))
                }
                Spacer(Modifier.width(4.dp))
                Text(chart.usualLabel, color = c.muted, fontSize = 11.sp)
            }
        }
        Canvas(Modifier.fillMaxWidth().height(92.dp)) {
            val n = bars.size
            val gap = 8.dp.toPx()
            val slot = (size.width - gap * (n - 1)) / n
            val pair = bars.any { it.value2 != null }
            val barW = minOf(slot, if (pair) 14.dp.toPx() else 30.dp.toPx())
            val radius = CornerRadius(7.dp.toPx())
            bars.forEachIndexed { i, bar ->
                val q = FastOutSlowInEasing.transform(stagger(p, i, n, step = 0.07f))
                val left = if (rtl) size.width - (i + 1) * slot - i * gap else i * (slot + gap)
                val tint = if (bar.current) mood else mood.copy(alpha = 0.3f)
                val h = size.height * (bar.value.toFloat() / max) * q
                val x = if (pair) left + slot / 2 - barW - 1.dp.toPx() else left + (slot - barW) / 2
                drawRoundRect(tint, Offset(x, size.height - h), Size(barW, h), radius)
                bar.value2?.let { v2 ->
                    val h2 = size.height * (v2.toFloat() / max) * q
                    drawRoundRect(c.muted.copy(alpha = 0.35f), Offset(left + slot / 2 + 1.dp.toPx(), size.height - h2), Size(barW, h2), radius)
                }
            }
            chart.usual?.let { usual ->
                val y = size.height * (1f - usual.toFloat() / max)
                drawLine(c.muted.copy(alpha = 0.8f), Offset(0f, y), Offset(size.width, y), strokeWidth = 1.5.dp.toPx(),
                         pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 7f)))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            bars.forEach { bar ->
                Text(bar.label, color = if (bar.current) mood else c.muted, fontSize = 10.5.sp,
                     fontWeight = if (bar.current) FontWeight(700) else FontWeight(400), textAlign = TextAlign.Center,
                     maxLines = 1, modifier = Modifier.weight(1f))
            }
        }
        if (chart.legend.size > 1) {
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                chart.legend.forEachIndexed { i, name ->
                    Box(Modifier.size(8.dp).clip(RoundedCornerShape(2.dp)).background(if (i == 0) mood else c.muted.copy(alpha = 0.35f)))
                    Text(name, color = c.muted, fontSize = 11.sp)
                }
            }
        }
    }
}

private class Bit(val x: Float, val delay: Float, val drift: Float, val spin: Float, val color: Color, val round: Boolean)

@Composable
private fun Confetti(colors: List<Color>) {
    val t = remember { Animatable(0f) }
    val bits = remember {
        val r = Random(7)
        List(26) { Bit(r.nextFloat(), r.nextFloat() * 0.3f, r.nextFloat() * 2f - 1f, (r.nextFloat() * 2f - 1f) * 540f,
                       colors[it % colors.size], it % 4 == 3) }
    }
    LaunchedEffect(Unit) { t.animateTo(1f, tween(1700, easing = LinearEasing)) }
    if (t.value >= 1f) return
    Canvas(Modifier.fillMaxWidth().height(280.dp)) {
        bits.forEach { b ->
            val q = ((t.value - b.delay) / (1f - b.delay)).coerceIn(0f, 1f)
            if (q <= 0f) return@forEach
            val y = -12.dp.toPx() + q * size.height
            val x = b.x * size.width + b.drift * 34.dp.toPx() * q
            val tint = b.color.copy(alpha = 1f - q)
            rotate(b.spin * q, Offset(x, y)) {
                if (b.round) drawCircle(tint, 3.5.dp.toPx(), Offset(x, y))
                else drawRect(tint, Offset(x - 3.5.dp.toPx(), y - 5.5.dp.toPx()), Size(7.dp.toPx(), 11.dp.toPx()))
            }
        }
    }
}
