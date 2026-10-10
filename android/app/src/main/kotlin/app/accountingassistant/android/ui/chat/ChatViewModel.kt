package app.accountingassistant.android.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.ApiError
import app.accountingassistant.android.data.NetworkError
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

data class ChatUiState(
    val items: List<ChatItem> = emptyList(),
    val draft: String = "",
    val sending: Boolean = false,
    val threadId: String? = null,
    val notice: Notice? = null,
    /** Files uploaded and waiting to go with the next message. */
    val attachments: List<Attachment> = emptyList(),
    val uploading: Boolean = false,
    val listening: Boolean = false,
    val transcribing: Boolean = false,
) {
    enum class Notice { Offline, Failed, UploadFailed, VoiceFailed }
    data class Attachment(val id: String, val name: String)
}

/**
 * One conversation: what the user sends, the blocks that come back, and the
 * life of each voucher (draft → posting → posted with an undo window, or
 * cancelled, or waiting for a second person).
 */
class ChatViewModel(
    private val api: ApiClient,
    private val suggestions: List<String> = emptyList(),
    private val now: () -> Long = System::currentTimeMillis,
    restore: Boolean = true,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state

    init {
        if (restore) viewModelScope.launch { reopen() }
    }

    /**
     * Back to the latest conversation, its cards redrawn; a first run gets
     * suggestions. Then the accountant says what needs attention today.
     */
    private suspend fun reopen() {
        val latest = runCatching { api.threads().firstOrNull() }.getOrNull()
        val items = latest?.let { t ->
            runCatching { api.messages(t.id) }.getOrNull()?.flatMap { m ->
                if (m.role == "user") listOf(ChatItem.User(m.id, m.text.orEmpty())) else m.blocks.map(::parseBlock)
            }
        }.orEmpty()
        _state.update {
            if (it.items.isNotEmpty()) it
            else if (items.isEmpty()) it.copy(items = if (suggestions.isEmpty()) emptyList() else listOf(ChatItem.Suggestions(options = suggestions)))
            else it.copy(items = items, threadId = latest?.id)
        }
        runCatching { api.briefing(_state.value.threadId) }.getOrNull()?.let { b ->
            if (b.blocks.isNotEmpty()) _state.update {
                it.copy(items = it.items + b.blocks.map(::parseBlock), threadId = b.threadId ?: it.threadId)
            }
        }
    }

    /** A photo or a document: uploaded now, sent with the next message. */
    fun attach(bytes: ByteArray, name: String, mime: String) {
        _state.update { it.copy(uploading = true, notice = null) }
        viewModelScope.launch {
            runCatching { api.upload(bytes, name, mime) }
                .onSuccess { r -> _state.update { it.copy(uploading = false, attachments = it.attachments + ChatUiState.Attachment(r.id, name)) } }
                .onFailure { _state.update { it.copy(uploading = false, notice = ChatUiState.Notice.UploadFailed) } }
        }
    }

    fun detach(id: String) = _state.update { it.copy(attachments = it.attachments.filterNot { a -> a.id == id }) }

    fun listening(on: Boolean) = _state.update { it.copy(listening = on) }

    /** A voice note's words go into the composer, to check before sending. */
    fun heard(bytes: ByteArray, name: String, mime: String) {
        _state.update { it.copy(listening = false, transcribing = true, notice = null) }
        viewModelScope.launch {
            runCatching { api.transcribe(bytes, name, mime) }
                .onSuccess { r -> _state.update { it.copy(transcribing = false, draft = (it.draft + " " + r.text).trim()) } }
                .onFailure { _state.update { it.copy(transcribing = false, notice = ChatUiState.Notice.VoiceFailed) } }
        }
    }

    /** A suggestion chip: sent as if typed. */
    fun ask(text: String) {
        _state.update { it.copy(draft = text) }
        send()
    }

    fun edit(text: String) = _state.update { it.copy(draft = text) }

    fun send() {
        val text = _state.value.draft.trim()
        val files = _state.value.attachments
        if ((text.isEmpty() && files.isEmpty()) || _state.value.sending || _state.value.uploading) return
        _state.update {
            it.copy(draft = "", sending = true, notice = null, attachments = emptyList(),
                    items = it.items.filterNot { item -> item is ChatItem.Suggestions } +
                            ChatItem.User("u${now()}", text, files.map { f -> f.name }) + ChatItem.Thinking())
        }
        viewModelScope.launch {
            val clientId = UUID.randomUUID().toString()
            try {
                val thread = _state.value.threadId
                val ids = files.map { it.id }
                val reply = try {
                    api.chatStream(text, thread, ids, clientId) { step ->
                        _state.update { st -> st.copy(items = st.items.map { if (it is ChatItem.Thinking) it.copy(text = step) else it }) }
                    }
                } catch (e: NetworkError) {
                    // one quiet retry: the server knows the message by its id and never answers it twice
                    api.chat(text, thread, ids, clientId)
                }
                _state.update { s ->
                    s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + reply.blocks.map(::parseBlock),
                           threadId = reply.threadId, sending = false)
                }
            } catch (e: NetworkError) {
                // keep the words in the box so nothing typed is lost
                _state.update { s -> s.copy(items = s.items.dropLastWhile { it is ChatItem.Thinking || (it is ChatItem.User && it.text == text) },
                                            draft = text, attachments = files, sending = false, notice = ChatUiState.Notice.Offline) }
            } catch (e: ApiError) {
                _state.update { s -> s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + ChatItem.Problem("e${now()}", e.code, e.message),
                                            sending = false, notice = ChatUiState.Notice.Failed) }
            }
        }
    }

    private fun updateProposal(id: String, change: (ChatItem.Proposal) -> ChatItem.Proposal) = _state.update { s ->
        s.copy(items = s.items.map { if (it is ChatItem.Proposal && it.id == id) change(it) else it })
    }

    fun confirm(id: String) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Posting, error = null) }
        viewModelScope.launch {
            try {
                val r = api.confirm(p.token)
                if (r.state == "waiting_for_approval") {
                    updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Waiting) }
                } else {
                    val b = r.block
                    val undo = (b?.get("undo_seconds") as? JsonPrimitive)?.intOrNull ?: 0
                    updateProposal(id) {
                        it.copy(phase = ChatItem.Proposal.Phase.Posted,
                                voucher = (b?.get("voucher") as? JsonPrimitive)?.contentOrNull,
                                postedDate = (b?.get("date") as? kotlinx.serialization.json.JsonObject)
                                    ?.get("display")?.let { d -> (d as? JsonPrimitive)?.contentOrNull },
                                auditLogId = (b?.get("audit_log_id") as? JsonPrimitive)?.contentOrNull,
                                document = fileOf(b?.get("file") as? kotlinx.serialization.json.JsonObject),
                                undoUntil = now() + undo * 1000L)
                    }
                }
            } catch (e: Exception) {
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Draft, error = e.message) }
            }
        }
    }

    fun cancel(id: String) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Cancelled) }
        viewModelScope.launch {
            runCatching { api.cancel(p.token) }.onFailure { e ->
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Draft, error = e.message) }
            }
        }
    }

    fun undo(id: String) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        val audit = p.auditLogId ?: return
        viewModelScope.launch {
            runCatching { api.undo(audit) }
                .onSuccess { updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Undone, undoUntil = 0) } }
                .onFailure { e -> updateProposal(id) { it.copy(error = e.message) } }
        }
    }
}
