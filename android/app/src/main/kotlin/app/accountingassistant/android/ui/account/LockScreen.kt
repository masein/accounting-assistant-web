package app.accountingassistant.android.ui.account

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.theme.LocalAccountantColors

/** The books stay covered until the phone's own fingerprint, face or PIN says so. */
@Composable
fun LockScreen(onUnlock: () -> Unit, askAtOnce: Boolean = true) {
    val c = LocalAccountantColors.current
    LaunchedEffect(Unit) { if (askAtOnce) onUnlock() }
    Column(Modifier.fillMaxSize().background(c.ground), horizontalAlignment = Alignment.CenterHorizontally,
           verticalArrangement = Arrangement.Center) {
        Canvas(Modifier.size(72.dp).graphicsLayer { rotationZ = -12f }) {
            drawCircle(c.firouzeh, radius = size.minDimension / 2 - 2.dp.toPx(), style = Stroke(3.dp.toPx()))
            drawCircle(c.firouzeh, radius = size.minDimension / 2 - 9.dp.toPx(), style = Stroke(1.5.dp.toPx()))
        }
        Spacer(Modifier.height(18.dp))
        Text(stringResource(R.string.lock_locked), color = c.ink, fontSize = 17.sp, fontWeight = FontWeight(700))
        Spacer(Modifier.height(18.dp))
        Button(onClick = onUnlock, colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
            Text(stringResource(R.string.lock_unlock), fontWeight = FontWeight(700))
        }
    }
}
