package app.accountingassistant.android.ui.signin

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.NetworkError
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SignInState(
    val username: String = "",
    val password: String = "",
    val code: String = "",
    val challenge: String? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val offline: Boolean = false,
    val signedIn: Boolean = false,
)

/** Password, then (when the account has it) the authenticator code. */
class SignInViewModel(private val api: ApiClient, private val deviceName: String) : ViewModel() {
    private val _state = MutableStateFlow(SignInState())
    val state: StateFlow<SignInState> = _state

    fun username(v: String) = _state.update { it.copy(username = v, error = null) }
    fun password(v: String) = _state.update { it.copy(password = v, error = null) }
    fun code(v: String) = _state.update { it.copy(code = v.filter { ch -> ch.isLetterOrDigit() || ch == '-' }, error = null) }

    fun submit() {
        val s = _state.value
        if (s.busy) return
        _state.update { it.copy(busy = true, error = null, offline = false) }
        viewModelScope.launch {
            try {
                val r = if (s.challenge == null) api.login(s.username.trim(), s.password, deviceName)
                        else api.twoFactor(s.challenge, s.code.trim(), deviceName)
                _state.update {
                    when {
                        r.twoFactorRequired -> it.copy(busy = false, challenge = r.challenge, password = "")
                        r.ok -> it.copy(busy = false, signedIn = true, password = "", code = "")
                        else -> it.copy(busy = false)
                    }
                }
            } catch (e: ApiError) {
                // a timed-out challenge starts over at the password
                val restart = e.code == "signin_timed_out"
                _state.update { it.copy(busy = false, error = e.message, challenge = if (restart) null else it.challenge) }
            } catch (e: NetworkError) {
                _state.update { it.copy(busy = false, offline = true) }
            }
        }
    }
}
