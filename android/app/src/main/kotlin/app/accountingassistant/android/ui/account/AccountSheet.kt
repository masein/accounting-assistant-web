package app.accountingassistant.android.ui.account

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.SheetValue
import androidx.compose.material3.rememberBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R
import app.accountingassistant.android.data.CurrencyOption
import app.accountingassistant.android.data.DeviceDto
import app.accountingassistant.android.sms.BankText
import app.accountingassistant.android.sms.SmsSender
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers

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
    notifyOn: Boolean = false,
    onNotify: (Boolean) -> Unit = {},
    sms: SmsSection? = null,
    currency: CurrencySection? = null,
) {
    val c = LocalAccountantColors.current
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberBottomSheetState(SheetValue.Hidden, setOf(SheetValue.Hidden, SheetValue.Expanded)),
                     containerColor = c.surface) {
        AccountSheetContent(username, booksName, role, devices, lockAvailable, lockOn, onLock, onRevoke, onSignOut,
                            crashesOn, onCrashes, notifyOn, onNotify, sms, currency)
    }
}

@Composable
fun AccountSheetContent(
    username: String, booksName: String, role: String, devices: List<DeviceDto>?,
    lockAvailable: Boolean, lockOn: Boolean, onLock: (Boolean) -> Unit, onRevoke: (String) -> Unit, onSignOut: () -> Unit,
    crashesOn: Boolean = true, onCrashes: (Boolean) -> Unit = {},
    notifyOn: Boolean = false, onNotify: (Boolean) -> Unit = {},
    sms: SmsSection? = null,
    currency: CurrencySection? = null,
) {
    val c = LocalAccountantColors.current
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding()
               .padding(horizontal = 20.dp).padding(bottom = 16.dp),
           verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Column {
            Text(username, color = c.ink, fontSize = 18.sp, fontWeight = FontWeight(800))
            Text("$booksName · ${roleName(role)}", color = c.muted, fontSize = 13.sp)
        }
        HorizontalDivider(color = c.line)
        if (currency != null) BooksCurrencySetting(currency)
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
                Text(stringResource(R.string.feed_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
                Text(stringResource(R.string.feed_hint), color = c.muted, fontSize = 12.5.sp)
            }
            Spacer(Modifier.width(12.dp))
            Switch(checked = notifyOn, onCheckedChange = onNotify,
                   colors = SwitchDefaults.colors(checkedTrackColor = c.firouzeh, checkedThumbColor = c.onFirouzeh))
        }
        if (sms != null) SmsSettings(sms)
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

/**
 * A person's books: the currency they count in (scenario Z3). A person in
 * Muscat counts Omani rials; personal books started in Iranian rials for
 * everyone. Change opens the choices in place.
 */
data class CurrencySection(
    val code: String,
    val name: String,
    val options: List<CurrencyOption>,
    val onChoose: (String) -> Unit,
    /** The books already have entries: the currency is shown, not offered. */
    val locked: Boolean = false,
)

@Composable
private fun BooksCurrencySetting(cur: CurrencySection) {
    val c = LocalAccountantColors.current
    var picking by rememberSaveable { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(stringResource(R.string.currency_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
            Text(if (cur.name.isNotBlank()) "${cur.name} (${cur.code})" else cur.code, color = c.ink, fontSize = 13.5.sp)
            Text(stringResource(if (cur.locked) R.string.currency_locked else R.string.currency_hint), color = c.muted,
                 fontSize = 12.5.sp)
        }
        if (!cur.locked) TextButton(onClick = { picking = !picking }, enabled = cur.options.isNotEmpty()) {
            Text(stringResource(R.string.currency_change), color = c.firouzeh, fontWeight = FontWeight(700))
        }
    }
    if (!picking || cur.locked) return
    Surface(shape = RoundedCornerShape(14.dp), color = c.surface, border = BorderStroke(1.dp, c.line)) {
        Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            cur.options.forEach { o ->
                Row(Modifier.fillMaxWidth().clickable { picking = false; if (o.code != cur.code) cur.onChoose(o.code) }
                        .padding(horizontal = 12.dp, vertical = 9.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = o.code == cur.code, onClick = null,
                                colors = RadioButtonDefaults.colors(selectedColor = c.firouzeh))
                    Spacer(Modifier.width(10.dp))
                    Text(o.name, color = c.ink, fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Text(o.code, color = c.muted, fontSize = 12.sp)
                }
            }
        }
    }
}

/**
 * «Read bank SMS» (the builds outside Play, roadmap ROADMAP_ANDROID_CHAT §7.1):
 * the switch, and each sender the user was asked about with what happens to
 * its messages.
 */
data class SmsSection(
    val on: Boolean,
    val denied: Boolean,
    val senders: List<Pair<SmsSender, Int>>,
    val onSwitch: (Boolean) -> Unit,
    val onDecide: (sender: String, read: Boolean) -> Unit,
)

@Composable
private fun SmsSettings(sms: SmsSection) {
    val c = LocalAccountantColors.current
    val lang = LocalConfiguration.current.locales[0].language
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(stringResource(R.string.sms_title), color = c.ink, fontSize = 15.sp, fontWeight = FontWeight(700))
            Text(stringResource(if (sms.denied) R.string.sms_denied else R.string.sms_hint),
                 color = if (sms.denied) c.pomegranate else c.muted, fontSize = 12.5.sp)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = sms.on, onCheckedChange = sms.onSwitch,
               colors = SwitchDefaults.colors(checkedTrackColor = c.firouzeh, checkedThumbColor = c.onFirouzeh))
    }
    if (!sms.on) return
    if (sms.senders.isEmpty()) Text(stringResource(R.string.sms_none), color = c.muted, fontSize = 12.5.sp)
    sms.senders.forEach { (s, waiting) ->
        Surface(shape = RoundedCornerShape(14.dp), color = c.surface,
                border = BorderStroke(1.dp, if (s.read == null) c.firouzeh else c.line)) {
            Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    val bank = BankText.displayName(s.bank, lang)
                    Text(Numbers.digits(s.sender, lang) + (bank?.let { " · $it" } ?: ""), color = c.ink, fontSize = 14.sp,
                         fontWeight = FontWeight(600))
                    Text(when (s.read) {
                        true -> stringResource(R.string.sms_reading)
                        false -> stringResource(R.string.sms_ignored)
                        null -> stringResource(R.string.sms_waiting, waiting)
                    }, color = if (s.read == null) c.firouzeh else c.muted, fontSize = 12.sp)
                }
                when (s.read) {
                    true -> TextButton(onClick = { sms.onDecide(s.sender, false) }) {
                        Text(stringResource(R.string.sms_stop), color = c.pomegranate)
                    }
                    false -> TextButton(onClick = { sms.onDecide(s.sender, true) }) {
                        Text(stringResource(R.string.sms_read), color = c.firouzeh)
                    }
                    null -> {
                        TextButton(onClick = { sms.onDecide(s.sender, false) }) { Text(stringResource(R.string.sms_ignore), color = c.muted) }
                        TextButton(onClick = { sms.onDecide(s.sender, true) }) {
                            Text(stringResource(R.string.sms_read), color = c.firouzeh, fontWeight = FontWeight(700))
                        }
                    }
                }
            }
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
