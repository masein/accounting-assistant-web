package app.accountingassistant.android.ui.account

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.SheetValue
import androidx.compose.material3.rememberBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.data.DeviceDto
import app.accountingassistant.android.ui.theme.LocalAccountantColors

/**
 * Behind the avatar: who you are and in which books, the phones signed in
 * (sign a lost one out from here), the app lock, and signing out.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountSheet(
    username: String,
    booksName: String,
    role: String,
    devices: List<DeviceDto>?,
    lockAvailable: Boolean,
    lockOn: Boolean,
    onLock: (Boolean) -> Unit,
    onRevoke: (String) -> Unit,
    onSignOut: () -> Unit,
    onDismiss: () -> Unit,
    crashesOn: Boolean = true,
    onCrashes: (Boolean) -> Unit = {},
) {
    val c = LocalAccountantColors.current
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberBottomSheetState(SheetValue.Hidden, setOf(SheetValue.Hidden, SheetValue.Expanded)),
                     containerColor = c.surface) {
        AccountSheetContent(username, booksName, role, devices, lockAvailable, lockOn, onLock, onRevoke, onSignOut,
                            crashesOn, onCrashes)
    }
}

@Composable
fun AccountSheetContent(
    username: String, booksName: String, role: String, devices: List<DeviceDto>?,
    lockAvailable: Boolean, lockOn: Boolean, onLock: (Boolean) -> Unit, onRevoke: (String) -> Unit, onSignOut: () -> Unit,
    crashesOn: Boolean = true, onCrashes: (Boolean) -> Unit = {},
) {
    val c = LocalAccountantColors.current
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp).padding(bottom = 16.dp),
           verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Column {
            Text(username, color = c.ink, fontSize = 18.sp, fontWeight = FontWeight(800))
            Text("$booksName · ${roleName(role)}", color = c.muted, fontSize = 13.sp)
        }
        HorizontalDivider(color = c.line)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.lock_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
                Text(stringResource(if (lockAvailable) R.string.lock_hint else R.string.lock_unavailable),
                     color = c.muted, fontSize = 12.5.sp)
            }
            Spacer(Modifier.width(12.dp))
            Switch(checked = lockOn && lockAvailable, onCheckedChange = onLock, enabled = lockAvailable,
                   colors = SwitchDefaults.colors(checkedTrackColor = c.firouzeh, checkedThumbColor = c.onFirouzeh))
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.crash_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
                Text(stringResource(R.string.crash_hint), color = c.muted, fontSize = 12.5.sp)
            }
            Spacer(Modifier.width(12.dp))
            Switch(checked = crashesOn, onCheckedChange = onCrashes,
                   colors = SwitchDefaults.colors(checkedTrackColor = c.firouzeh, checkedThumbColor = c.onFirouzeh))
        }
        HorizontalDivider(color = c.line)
        Text(stringResource(R.string.devices_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
        if (devices == null) {
            Text(stringResource(R.string.thinking), color = c.muted, fontSize = 13.sp)
        } else devices.forEach { d ->
            Surface(shape = RoundedCornerShape(14.dp), color = c.surface,
                    border = BorderStroke(1.dp, if (d.thisDevice) c.firouzeh else c.line)) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(d.name, color = c.ink, fontSize = 14.sp, fontWeight = FontWeight(600))
                        Text(stringResource(if (d.thisDevice) R.string.device_this else R.string.device_other),
                             color = c.muted, fontSize = 12.sp)
                    }
                    if (!d.thisDevice) TextButton(onClick = { onRevoke(d.id) }) {
                        Text(stringResource(R.string.device_sign_out), color = c.pomegranate)
                    }
                }
            }
        }
        OutlinedButton(onClick = onSignOut, modifier = Modifier.fillMaxWidth(), border = BorderStroke(1.dp, c.line)) {
            Text(stringResource(R.string.sign_out), color = c.pomegranate, fontWeight = FontWeight(700))
        }
    }
}

/** The role as the user's language says it. */
@Composable
fun roleName(role: String): String = stringResource(when (role) {
    "cfo" -> R.string.role_cfo
    "accountant" -> R.string.role_accountant
    "manager" -> R.string.role_manager
    "employee" -> R.string.role_employee
    "viewer" -> R.string.role_viewer
    "personal" -> R.string.role_personal
    else -> R.string.role_owner
})
