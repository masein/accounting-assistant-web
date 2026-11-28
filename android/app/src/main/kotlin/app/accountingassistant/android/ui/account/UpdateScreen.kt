package app.accountingassistant.android.ui.account

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers

/**
 * This version of the app is too old for the server (a 426 on any call): it
 * says so in the user's language, and the version needed when the server
 * names it (scenario CX2). The app's config names no store page yet, so there
 * is no link: the words say to install the latest version. What was typed
 * waits in the outbox and goes after the update.
 */
@Composable
fun UpdateScreen(minVersion: String?, lang: String) {
    val c = LocalAccountantColors.current
    Column(Modifier.fillMaxSize().background(c.ground).padding(horizontal = 32.dp),
           horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        // a ring with an arrow up in it: a newer version
        Canvas(Modifier.size(72.dp)) {
            val stroke = 3.dp.toPx()
            drawCircle(c.firouzeh, radius = size.minDimension / 2 - 2.dp.toPx(), style = Stroke(stroke))
            val mid = size.width / 2
            val top = size.height * 0.28f
            val bottom = size.height * 0.72f
            val wing = size.width * 0.16f
            drawLine(c.firouzeh, Offset(mid, bottom), Offset(mid, top), stroke, cap = StrokeCap.Round)
            drawLine(c.firouzeh, Offset(mid, top), Offset(mid - wing, top + wing), stroke, cap = StrokeCap.Round)
            drawLine(c.firouzeh, Offset(mid, top), Offset(mid + wing, top + wing), stroke, cap = StrokeCap.Round)
        }
        Spacer(Modifier.height(18.dp))
        Text(stringResource(R.string.update_title), color = c.ink, fontSize = 17.sp, fontWeight = FontWeight(700),
             textAlign = TextAlign.Center)
        Spacer(Modifier.height(10.dp))
        Text(stringResource(R.string.update_body), color = c.muted, fontSize = 14.sp, lineHeight = 22.sp,
             textAlign = TextAlign.Center)
        minVersion?.takeIf { it.isNotBlank() }?.let { v ->
            Spacer(Modifier.height(12.dp))
            // «0.2.0» kept left to right inside Persian or Arabic words
            Text(stringResource(R.string.update_version, "\u2066" + Numbers.digits(v, lang) + "\u2069"), color = c.ink,
                 fontSize = 13.sp, fontWeight = FontWeight(600), textAlign = TextAlign.Center)
        }
    }
}
