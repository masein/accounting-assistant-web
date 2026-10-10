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
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

data class ChatUiState(
    val items: List<ChatItem> = emptyList(),
    val draft: String = "",
    val sending: Boolean = false,
    val threadId: String? = null,
    val notice: Notice? = null,
) {
    enum class Notice { Offline, Failed }
}

/**
 * One conversation: what the user sends, the blocks that come back, and the
 * life of each voucher (draft → posting → posted with an undo window, or
 * cancelled, or waiting for a second person).
 */
class ChatViewModel(private val api: ApiClient, private val now: () -> Long = System::currentTimeMillis) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state

    fun edit(text: String) = _state.update { it.copy(draft = text) }

    fun send() {
        val text = _state.value.draft.trim()
        if (text.isEmpty() || _state.value.sending) return
        _state.update {
            it.copy(draft = "", sending = true, notice = null,
                    items = it.items + ChatItem.User("u${now()}", text) + ChatItem.Thinking())
        }
        viewModelScope.launch {
            try {
                val reply = api.chat(text, _state.value.threadId)
                _state.update { s ->
                    s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + reply.blocks.map(::parseBlock),
                           threadId = reply.threadId, sending = false)
                }
            } catch (e: NetworkError) {
                // keep the words in the box so nothing typed is lost
                _state.update { s -> s.copy(items = s.items.dropLastWhile { it is ChatItem.Thinking || (it is ChatItem.User && it.text == text) },
                                            draft = text, sending = false, notice = ChatUiState.Notice.Offline) }
            } catch (e: ApiError) {
                _state.update { s -> s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + ChatItem.Fallback("e${now()}", e.message),
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
