package app.accountingassistant.android.ui.support

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.SupportContext
import app.accountingassistant.android.data.SupportMessageDto
import app.accountingassistant.android.util.runSuspendCatching
import java.time.Instant
import java.time.OffsetDateTime
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class SupportUiState(
    /** The conversation, oldest first: the person's messages and the desk's replies. */
    val messages: List<SupportMessageDto> = emptyList(),
    /** The conversation came at least once: until then the desk's state isn't known. */
    val loaded: Boolean = false,
    val online: Boolean = false,
    /** The desk's replies not read yet: the badge on the account button and in the account sheet. */
    val unread: Int = 0,
    val draft: String = "",
    /** Screenshots uploaded and waiting to go with the next message. */
    val attachments: List<Attachment> = emptyList(),
    val uploading: Boolean = false,
    val sending: Boolean = false,
    val problem: Problem? = null,
) {
    data class Attachment(val id: String, val name: String)

    sealed interface Problem {
        /** Not sent, or not uploaded: "Couldn't send it — try again." */
        data object Failed : Problem
        /** A file over 20 MB: not sent at all. */
        data object TooLarge : Problem
        /** The server's own words, already in the person's language (past 30 messages a day). */
        data class Said(val text: String) : Problem
    }

    val canSend: Boolean get() = (draft.isNotBlank() || attachments.isNotEmpty()) && !uploading && !sending
}

/**
 * The person's conversation with the support desk (scenarios DB1–DB8): the
 * same one as the web's support panel. While the screen is shown it asks for
 * what is new every five seconds and marks the desk's replies read; otherwise
 * it only counts them, for the badge. Each message carries what the app knew:
 * the screen the person came from, the app's version, the language, the
 * screen's size and the last calls the server refused, with their request ids.
 */
class SupportViewModel(
    private val api: ApiClient,
    private val appVersion: String,
    private val language: () -> String,
    /** The screen's size in dp, "393x852". */
    private val screen: () -> String,
    /** The replies were read: the "Support replied" notification goes. */
    private val onSeen: () -> Unit = {},
    private val pollMs: Long = POLL_MS,
) : ViewModel() {
    private val _state = MutableStateFlow(SupportUiState())
    val state: StateFlow<SupportUiState> = _state

    /** Where the person was when support opened: "chat", or "support" from its notification. */
    private var from = "chat"
    /** The newest message the server sent: the next call asks only for what came after it. */
    private var newest: String? = null
    private var polling: Job? = null
    private val loading = Mutex()

    /** Support is opened, from [page] (the screen the person was on). */
    fun opened(page: String) {
        from = page
    }

    /** On screen and in front: the conversation now and every five seconds, the replies marked read. */
    fun resume() {
        if (polling?.isActive == true) return
        polling = viewModelScope.launch {
            while (true) {
                load(shown = true)
                delay(pollMs)
            }
        }
    }

    /** Left, or the app went to the background: no more asking. */
    fun pause() {
        polling?.cancel()
        polling = null
    }

    /** The app opened or came back: how many replies wait, without marking them read. */
    fun check() {
        if (polling?.isActive == true) return
        viewModelScope.launch { load(shown = false) }
    }

    private suspend fun load(shown: Boolean) {
        loading.withLock {
            val conv = runSuspendCatching { api.support(after = newest) }.getOrNull() ?: return
            conv.messages.lastOrNull()?.at?.let { newest = it }
            _state.update { st ->
                // a message already drawn (one just sent, a reply now read) is replaced by the server's
                val byId = st.messages.associateBy { it.id }.toMutableMap()
                conv.messages.forEach { byId[it.id] = it }
                st.copy(messages = byId.values.sortedBy { instantOf(it.at) }, loaded = true,
                        online = conv.desk.online, unread = conv.unread)
            }
            if (shown && conv.unread > 0) {
                runSuspendCatching { api.supportSeen() }.onSuccess {
                    _state.update { st ->
                        st.copy(unread = 0, messages = st.messages.map { m -> if (m.fromDesk) m.copy(seen = true) else m })
                    }
                    onSeen()
                }
            }
        }
    }

    fun edit(text: String) = _state.update { it.copy(draft = text) }

    /** A screenshot or a PDF: uploaded now, its id sent with the next message. */
    fun attach(bytes: ByteArray, name: String, mime: String) {
        if (_state.value.attachments.size >= MAX_ATTACHMENTS) return
        if (bytes.size > MAX_BYTES) {
            _state.update { it.copy(problem = SupportUiState.Problem.TooLarge) }
            return
        }
        _state.update { it.copy(uploading = true, problem = null) }
        viewModelScope.launch {
            runSuspendCatching { api.supportAttachment(bytes, name, mime) }
                .onSuccess { r ->
                    _state.update {
                        it.copy(uploading = false,
                                attachments = it.attachments + SupportUiState.Attachment(r.id, r.fileName.ifBlank { name }))
                    }
                }
                .onFailure { _state.update { it.copy(uploading = false, problem = SupportUiState.Problem.Failed) } }
        }
    }

    /** A file picked that couldn't be read. */
    fun unreadable() = _state.update { it.copy(problem = SupportUiState.Problem.Failed) }

    fun detach(id: String) = _state.update { it.copy(attachments = it.attachments.filterNot { a -> a.id == id }) }

    fun dismiss() = _state.update { it.copy(problem = null) }

    fun send() {
        val st = _state.value
        if (!st.canSend) return
        val text = st.draft.trim()
        val ids = st.attachments.map { it.id }
        _state.update { it.copy(sending = true, problem = null) }
        viewModelScope.launch {
            try {
                val about = SupportContext(page = from, appVersion = appVersion, language = language(), screen = screen(),
                                           errors = api.recentFailures())
                val r = api.supportMessage(text, ids, about)
                _state.update { s ->
                    s.copy(sending = false, online = r.desk.online,
                           // what was typed meanwhile stays; what went, goes
                           draft = if (s.draft.trim() == text) "" else s.draft,
                           attachments = s.attachments.filterNot { it.id in ids },
                           messages = s.messages.filterNot { it.id == r.message.id } + r.message)
                }
            } catch (e: CancellationException) {
                throw e                                       // left, or cleared: not a failed call (scenario CX7)
            } catch (e: ApiError) {
                // past 30 a day the server says so in the person's language; anything else: try again
                val said = e.message.takeIf { e.status == 429 && it.isNotBlank() }
                _state.update {
                    it.copy(sending = false,
                            problem = if (said != null) SupportUiState.Problem.Said(said) else SupportUiState.Problem.Failed)
                }
            } catch (e: Exception) {
                _state.update { it.copy(sending = false, problem = SupportUiState.Problem.Failed) }
            }
        }
    }

    companion object {
        /** While support is on screen, how often it asks for a reply. */
        const val POLL_MS = 5_000L
        /** The server takes 20 MB a file, and five files a message. */
        const val MAX_BYTES = 20 * 1024 * 1024
        const val MAX_ATTACHMENTS = 5

        private fun instantOf(at: String?): Instant =
            at?.let { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() } ?: Instant.MAX
    }
}
