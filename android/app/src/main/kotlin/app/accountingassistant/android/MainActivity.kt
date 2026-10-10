package app.accountingassistant.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import app.accountingassistant.android.data.DeviceDto
import app.accountingassistant.android.security.AppLock
import app.accountingassistant.android.ui.account.AccountSheet
import app.accountingassistant.android.ui.account.LockScreen
import kotlinx.coroutines.launch
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import app.accountingassistant.android.ui.chat.ChatScreen
import app.accountingassistant.android.ui.chat.ChatViewModel
import app.accountingassistant.android.ui.signin.SignInScreen
import app.accountingassistant.android.ui.signin.SignInViewModel
import app.accountingassistant.android.ui.theme.AccountantTheme
import app.accountingassistant.android.util.VoiceRecorder

class MainActivity : ComponentActivity() {
    private var stoppedAt = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as AccountantApp
        val lock = AppLock(this)
        setContent {
            AccountantTheme {
                val scope = rememberCoroutineScope()
                var restored by remember { mutableStateOf(false) }
                var signedIn by remember { mutableStateOf(false) }
                var locked by remember { mutableStateOf(false) }
                var lockOn by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) {
                    signedIn = app.api.restore() != null
                    lockOn = lock.enabled()
                    locked = signedIn && lockOn                       // a cold start asks first
                    restored = true
                }
                LaunchedEffect(Unit) { app.api.signedOut.collect { signedIn = false } }
                // back from the background after a while: ask again
                val owner = LocalLifecycleOwner.current
                DisposableEffect(owner) {
                    val watch = LifecycleEventObserver { _, event ->
                        when (event) {
                            Lifecycle.Event.ON_STOP -> stoppedAt = System.currentTimeMillis()
                            Lifecycle.Event.ON_START -> if (signedIn && lockOn && stoppedAt > 0 &&
                                System.currentTimeMillis() - stoppedAt > AppLock.RELOCK_AFTER_MS) locked = true
                            else -> Unit
                        }
                    }
                    owner.lifecycle.addObserver(watch)
                    onDispose { owner.lifecycle.removeObserver(watch) }
                }
                val confirmCredential = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
                    if (it.resultCode == RESULT_OK) locked = false
                }
                if (!restored) return@AccountantTheme
                val lang = resources.configuration.locales[0].language
                if (signedIn && locked) {
                    val title = stringResource(R.string.lock_locked)
                    LockScreen(onUnlock = {
                        lock.prompt(this@MainActivity, title, onUnlocked = { locked = false },
                                    fallback = { confirmCredential.launch(it) })
                    })
                } else if (!signedIn) {
                    val vm: SignInViewModel = viewModel(factory = viewModelFactory {
                        initializer { SignInViewModel(app.api, app.deviceName) }
                    })
                    val s by vm.state.collectAsState()
                    LaunchedEffect(s.signedIn) { if (s.signedIn) signedIn = true }
                    SignInScreen(s, vm::username, vm::password, vm::code, vm::submit)
                } else {
                    val suggestions = resources.getStringArray(R.array.suggestions).toList()
                    val vm: ChatViewModel = viewModel(factory = viewModelFactory {
                        initializer { ChatViewModel(app.api, suggestions) }
                    })
                    val s by vm.state.collectAsState()
                    val recorder = remember { VoiceRecorder(this@MainActivity) }
                    val session = app.api.session
                    val company = session?.company
                    var account by remember { mutableStateOf(false) }
                    var devices by remember { mutableStateOf<List<DeviceDto>?>(null) }
                    ChatScreen(
                        state = s, lang = lang,
                        booksName = company?.name ?: "",
                        personalBooks = company?.kind == "personal",
                        onDraft = vm::edit, onSend = vm::send, onConfirm = vm::confirm,
                        onCancel = vm::cancel, onUndo = vm::undo, onBooks = {}, onSuggestion = vm::ask,
                        onPicked = { vm.attach(it.bytes, it.name, it.mime) }, onDetach = vm::detach,
                        onSpeakStart = { if (runCatching { recorder.start() }.isSuccess) vm.listening(true) },
                        onSpeakEnd = {
                            vm.listening(false)
                            recorder.stop()?.let { vm.heard(it.bytes, it.name, it.mime) }
                        },
                        userInitial = session?.user?.username?.take(1)?.uppercase() ?: "",
                        onAccount = {
                            account = true
                            devices = null
                            scope.launch { devices = runCatching { app.api.devices() }.getOrDefault(emptyList()) }
                        },
                    )
                    if (account && session != null) {
                        AccountSheet(
                            username = session.user.username, booksName = company?.name ?: "", role = session.user.role,
                            devices = devices, lockAvailable = lock.available, lockOn = lockOn,
                            onLock = { on -> lockOn = on; scope.launch { lock.setEnabled(on) } },
                            onRevoke = { id ->
                                scope.launch {
                                    runCatching { app.api.revokeDevice(id) }
                                    devices = runCatching { app.api.devices() }.getOrDefault(devices.orEmpty())
                                }
                            },
                            onSignOut = { account = false; scope.launch { app.api.signOut(); signedIn = false } },
                            onDismiss = { account = false },
                        )
                    }
                }
            }
        }
    }
}
