package app.accountingassistant.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as AccountantApp
        setContent {
            AccountantTheme {
                var restored by remember { mutableStateOf(false) }
                var signedIn by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) {
                    signedIn = app.api.restore() != null
                    restored = true
                }
                LaunchedEffect(Unit) { app.api.signedOut.collect { signedIn = false } }
                if (!restored) return@AccountantTheme
                val lang = resources.configuration.locales[0].language
                if (!signedIn) {
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
                    val company = app.api.session?.company
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
                    )
                }
            }
        }
    }
}
