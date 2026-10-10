package app.accountingassistant.android.ui.signin

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.ui.theme.LocalAccountantColors

/** The seal, the promise in Reem Kufi, and two fields. */
@Composable
fun SignInScreen(
    state: SignInState,
    onUsername: (String) -> Unit,
    onPassword: (String) -> Unit,
    onCode: (String) -> Unit,
    onSubmit: () -> Unit,
) {
    val c = LocalAccountantColors.current
    Column(
        Modifier.fillMaxSize().background(c.ground).statusBarsPadding().imePadding().padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Canvas(Modifier.size(64.dp).graphicsLayer { rotationZ = -12f }) {
            drawCircle(c.firouzeh, radius = size.minDimension / 2 - 2.dp.toPx(), style = Stroke(3.dp.toPx()))
            drawCircle(c.firouzeh, radius = size.minDimension / 2 - 8.dp.toPx(), style = Stroke(1.5.dp.toPx()))
        }
        Spacer(Modifier.height(20.dp))
        Text(stringResource(R.string.signin_title), style = MaterialTheme.typography.displaySmall, color = c.firouzeh)
        Spacer(Modifier.height(28.dp))
        if (state.challenge == null) {
            OutlinedTextField(state.username, onUsername, label = { Text(stringResource(R.string.signin_username)) },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next))
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(state.password, onPassword, label = { Text(stringResource(R.string.signin_password)) },
                singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done))
        } else {
            OutlinedTextField(state.code, onCode, label = { Text(stringResource(R.string.signin_code)) },
                singleLine = true, modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done))
        }
        state.error?.let { Spacer(Modifier.height(10.dp)); Text(it, color = c.pomegranate, fontSize = 13.sp) }
        if (state.offline) { Spacer(Modifier.height(10.dp)); Text(stringResource(R.string.error_network), color = c.saffron, fontSize = 13.sp) }
        Spacer(Modifier.height(20.dp))
        Button(onClick = onSubmit, enabled = !state.busy, modifier = Modifier.fillMaxWidth().height(50.dp),
               colors = ButtonDefaults.buttonColors(containerColor = c.firouzeh, contentColor = c.onFirouzeh)) {
            Text(stringResource(if (state.challenge == null) R.string.signin_button else R.string.signin_code_button),
                 fontWeight = FontWeight(700), fontSize = 15.sp)
        }
    }
}
