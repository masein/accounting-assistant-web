package app.accountingassistant.android.ui.chat

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.components.priced
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Dates
import app.accountingassistant.android.util.LocalCalendar
import app.accountingassistant.android.util.Numbers

/** Where a value sits between the chart's bottom and top, from 0 (bottom) to 1. */
fun chartScale(values: List<Long>): (Long) -> Float {
    val lo = minOf(0L, values.min())
    val hi = maxOf(0L, values.max())
    val span = (hi - lo).takeIf { it > 0 } ?: 1L
    return { v -> (v - lo).toFloat() / span }
}

/**
 * The cash forecast as a line, drawn to scale from today: firouzeh above
 * zero, pomegranate below it, the zero line dashed, the lowest week marked.
 * Time runs with the reading direction (right to left in Persian), like the
 * figure card's sparkline. An estimate, and it says so.
 */
@Composable
fun ChartCard(item: ChatItem.Chart, lang: String) {
    val c = LocalAccountantColors.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val values = item.points.map { it.value }
    val y = chartScale(values)
    val lowest = item.lowest ?: item.points.minBy { it.value }
    val lowIndex = item.points.indexOfLast { it.x == lowest.x && it.value == lowest.value }.takeIf { it >= 0 }
        ?: values.indexOf(values.min())
    val said = stringResource(R.string.chart_lowest, priced(lowest.value, item.currency, lang), Dates.short(lowest.x, lang, LocalCalendar.current))
    Surface(color = c.surface, shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, c.line), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val history = item.kind == "net_worth"            // the past up to today, not a forecast
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(item.title, color = c.muted, fontSize = 11.sp)
                if (!history) Text(stringResource(R.string.chart_estimate), color = c.muted, fontSize = 11.sp)
            }
            // the last value, said as what it is: "after 13 weeks", or today's with the change over the months
            Row(verticalAlignment = Alignment.Bottom) {
                Text(priced(values.last(), item.currency, lang), color = if (values.last() < 0) c.pomegranate else c.ink,
                     fontSize = 22.sp, fontWeight = FontWeight(800))
                Spacer(Modifier.width(8.dp))
                if (history) {
                    val change = values.last() - values.first()
                    Text(Numbers.digits(stringResource(R.string.chart_change, Numbers.signed(change, lang), item.points.size), lang),
                         color = if (change >= 0) c.firouzeh else c.pomegranate, fontSize = 12.sp,
                         modifier = Modifier.padding(bottom = 4.dp))
                } else {
                    Text(Numbers.digits(stringResource(R.string.chart_after, item.points.size - 1), lang), color = c.muted,
                         fontSize = 12.sp, modifier = Modifier.padding(bottom = 4.dp))
                }
            }
            // nothing known to come in or go out: a flat line says less than a sentence
            if (values.distinct().size == 1) {
                Text(stringResource(R.string.chart_flat), color = c.muted, fontSize = 13.sp)
                return@Column
            }
            val anyBelow = values.min() < 0
            Canvas(Modifier.fillMaxWidth().height(140.dp).semantics { contentDescription = "${item.title}. $said" }) {
                val n = item.points.size
                val pad = 6.dp.toPx()
                val w = size.width - 2 * pad
                val h = size.height - 2 * pad
                fun at(i: Int): Offset {
                    val fx = i.toFloat() / (n - 1)
                    return Offset(pad + w * (if (rtl) 1 - fx else fx), pad + h * (1 - y(values[i])))
                }
                val zeroY = pad + h * (1 - y(0))
                val line = Path().apply { item.points.indices.forEach { i -> at(i).let { if (i == 0) moveTo(it.x, it.y) else lineTo(it.x, it.y) } } }
                val area = Path().apply {
                    addPath(line)
                    lineTo(at(n - 1).x, zeroY)
                    lineTo(at(0).x, zeroY)
                    close()
                }
                // above zero in firouzeh, below in pomegranate: the same path, clipped at the zero line
                clipRect(bottom = zeroY) {
                    drawPath(area, c.firouzeh.copy(alpha = 0.12f))
                    drawPath(line, c.firouzeh, style = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
                if (anyBelow) clipRect(top = zeroY) {
                    drawPath(area, c.pomegranate.copy(alpha = 0.14f))
                    drawPath(line, c.pomegranate, style = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
                drawLine(c.line, Offset(pad, zeroY), Offset(size.width - pad, zeroY), strokeWidth = 1.dp.toPx(),
                         pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx())))
                drawCircle(c.ink, 3.5.dp.toPx(), at(if (history) n - 1 else 0))                // today
                if (!history) {
                    val low = at(lowIndex)
                    val lowColor = if (values[lowIndex] < 0) c.pomegranate else c.saffron
                    drawCircle(c.surface, 6.dp.toPx(), low)
                    drawCircle(lowColor, 6.dp.toPx(), low, style = Stroke(2.dp.toPx()))
                    drawCircle(lowColor, 2.5.dp.toPx(), low)
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(if (history) Dates.short(item.points.first().x, lang, LocalCalendar.current) else stringResource(R.string.chart_today),
                     color = c.muted, fontSize = 10.5.sp)
                Text(Dates.short(item.points[item.points.size / 2].x, lang, LocalCalendar.current), color = c.muted, fontSize = 10.5.sp)
                Text(if (history) stringResource(R.string.chart_today) else Dates.short(item.points.last().x, lang, LocalCalendar.current),
                     color = c.muted, fontSize = 10.5.sp)
            }
            if (!history) Row(verticalAlignment = Alignment.CenterVertically) {
                Canvas(Modifier.size(8.dp)) { drawCircle(if (lowest.value < 0) c.pomegranate else c.saffron) }
                Spacer(Modifier.width(6.dp))
                Text(said, color = c.ink, fontSize = 12.5.sp)
            }
            item.firstNegative?.let {
                Text(stringResource(R.string.chart_negative, Dates.short(it, lang, LocalCalendar.current)), color = c.pomegranate, fontSize = 12.5.sp,
                     fontWeight = FontWeight(600))
            }
        }
    }
}

