package app.accountingassistant.android.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.accountingassistant.android.data.ApiClient
import app.accountingassistant.android.data.EditRequest
import app.accountingassistant.android.data.MemoryOutboxStore
import app.accountingassistant.android.data.Outbox
import app.accountingassistant.android.data.Queued
import app.accountingassistant.android.data.QueuedFile
import app.accountingassistant.android.data.ThreadDto
import app.accountingassistant.android.data.ThreadMessageDto
import app.accountingassistant.android.data.NetworkError
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

data class ChatUiState(
    val items: List<ChatItem> = emptyList(),
    val draft: String = "",
    /** A message is on its way (the outbox is sending one of this conversation's). */
    val sending: Boolean = false,
    val threadId: String? = null,
    val notice: Notice? = null,
    /** Files uploaded (or kept, offline) and waiting to go with the next message. */
    val attachments: List<Attachment> = emptyList(),
    val uploading: Boolean = false,
    val listening: Boolean = false,
    val transcribing: Boolean = false,
    /** The outbox by client id: messages waiting for the network, or refused. */
    val queued: Map<String, Queued> = emptyMap(),
) {
    enum class Notice { Offline, Failed, UploadFailed, VoiceFailed }
    /** [uploadedId] once on the server; [path] where the outbox keeps it when the upload waits. */
    data class Attachment(val id: String, val name: String, val mime: String = "",
                          val uploadedId: String? = id, val path: String? = null)
}

/**
 * One conversation: what the user sends, the blocks that come back, and the
 * life of each voucher (draft → posting → posted with an undo window, or
 * cancelled, or waiting for a second person).
 *
 * Every message goes out through the [outbox]: kept first, sent, kept until
 * answered. Offline it waits under its bubble and sends itself when the
 * network is back, from here or, with the app closed, from the background.
 */
private const val PAGE = 60

class ChatViewModel(
    private val api: ApiClient,
    private val suggestions: List<String> = emptyList(),
    private val now: () -> Long = System::currentTimeMillis,
    restore: Boolean = true,
    private val outbox: Outbox = Outbox(MemoryOutboxStore(), api),
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state

    /** The phone's key for this conversation until the server names it. */
    private var newKey = UUID.randomUUID().toString()
    /** The newest and oldest server messages drawn: where catching up and paging start. */
    private var newest: String? = null
    private var oldest: String? = null

    init {
        viewModelScope.launch { outbox.entries.collect { list -> _state.update { it.copy(queued = list.associateBy { q -> q.clientId }) } } }
        viewModelScope.launch { outbox.sending.collect(::onSending) }
        viewModelScope.launch { outbox.deliveries.collect(::onDelivery) }
        if (restore) viewModelScope.launch {
            reopen()
            deliver()
        }
    }

    private fun ChatUiState.has(clientId: String) = items.any { it is ChatItem.User && it.clientId == clientId }

    /** The step the server is on shows under the message being sent, if it is this conversation's. */
    private fun onSending(s: Outbox.Sending?) = _state.update { st ->
        val rest = st.items.filterNot { it is ChatItem.Thinking }
        if (s != null && st.has(s.clientId)) st.copy(items = rest + ChatItem.Thinking(text = s.step), sending = true)
        else st.copy(items = rest, sending = s != null && st.sending)
    }

    private fun onDelivery(d: Outbox.Delivery) = _state.update { st ->
        if (!st.has(d.entry.clientId)) return@update st                  // another conversation's: it's in that thread
        val rest = st.items.filterNot { it is ChatItem.Thinking }
        when (d) {
            is Outbox.Delivery.Answered -> {
                val have = rest.map { it.id }.toSet()
                st.copy(items = rest + d.reply.blocks.map(::parseBlock).filterNot { it.id in have },
                        threadId = st.threadId ?: d.reply.threadId)
            }
            is Outbox.Delivery.Refused -> st.copy(items = rest, notice = ChatUiState.Notice.Failed)
        }
    }

    /** Send what waits; offline, say so (the messages stay under their bubbles). */
    private suspend fun deliver() {
        val result = outbox.flush()
        _state.update {
            it.copy(sending = outbox.sending.value != null,
                    notice = if (result == Outbox.Result.Offline) ChatUiState.Notice.Offline else it.notice)
        }
    }

    private fun bubble(q: Queued) = ChatItem.User(q.clientId, q.text, q.files.map { it.name }, clientId = q.clientId)

    private fun itemsOf(m: ThreadMessageDto): List<ChatItem> =
        if (m.role == "user") listOf(ChatItem.User(m.id, m.text.orEmpty(), clientId = m.clientMessageId))
        else m.blocks.map(::parseBlock)

    /** A conversation's latest page with its cards redrawn, and what still waits to go to it. */
    private suspend fun page(threadId: String): List<ChatItem>? {
        val page = runCatching { api.messagesPage(threadId, limit = PAGE) }.getOrNull() ?: return null
        val known = page.messages.mapNotNull { it.clientMessageId }.toSet()
        val waiting = outbox.entries.value.filter { it.threadId == threadId && it.clientId !in known }.map(::bubble)
        newest = page.messages.lastOrNull()?.id
        oldest = page.messages.firstOrNull()?.id
        return (if (page.moreBefore) listOf(ChatItem.Earlier()) else emptyList()) + page.messages.flatMap(::itemsOf) + waiting
    }

    /**
     * Back to the latest conversation, its cards redrawn; a first run gets
     * suggestions. Messages typed for a new conversation before the app
     * closed come back first. Then the accountant says what needs attention.
     */
    private suspend fun reopen() {
        outbox.load()
        api.session?.user?.id?.let { outbox.keepOnly(it) }
        val queued = outbox.entries.value
        val fresh = queued.filter { it.newThread != null }
        if (fresh.isNotEmpty()) {
            newKey = fresh.first().newThread!!
            _state.update { if (it.items.isNotEmpty()) it else it.copy(items = fresh.filter { q -> q.newThread == newKey }.map(::bubble)) }
            return
        }
        val latest = runCatching { api.threads().firstOrNull() }
        val thread = latest.getOrNull()?.id ?: queued.lastOrNull()?.threadId.takeIf { latest.isFailure }
        val items = when {
            thread == null -> null
            latest.isSuccess -> page(thread)
            else -> queued.filter { it.threadId == thread }.map(::bubble)        // offline: at least what waits
        }
        _state.update {
            if (it.items.isNotEmpty()) it
            else if (items.isNullOrEmpty()) it.copy(items = if (suggestions.isEmpty()) emptyList() else listOf(ChatItem.Suggestions(options = suggestions)))
            else it.copy(items = items, threadId = thread)
        }
        runCatching { api.briefing(_state.value.threadId) }.getOrNull()?.let { b ->
            if (b.blocks.isNotEmpty()) _state.update {
                it.copy(items = it.items + b.blocks.map(::parseBlock), threadId = b.threadId ?: it.threadId)
            }
        }
    }

    /** Back in the app: what the web chat, or the background sender, added meanwhile. */
    fun catchUp() {
        val thread = _state.value.threadId ?: return
        val after = newest
        viewModelScope.launch {
            // a conversation begun in this session has no cursor yet: its latest page, merged
            val page = runCatching { if (after != null) api.messagesPage(thread, after = after) else api.messagesPage(thread, limit = PAGE) }
                .getOrNull() ?: return@launch
            if (page.messages.isEmpty()) return@launch
            newest = page.messages.last().id
            _state.update { st ->
                if (st.threadId != thread) return@update st
                val ids = st.items.map { it.id }.toSet()
                val mine = st.items.mapNotNull { (it as? ChatItem.User)?.clientId }.toSet()
                val fresh = page.messages.filterNot { it.role == "user" && it.clientMessageId in mine }
                    .flatMap(::itemsOf).filterNot { it.id in ids }
                val thinking = st.items.filterIsInstance<ChatItem.Thinking>()
                st.copy(items = st.items.filterNot { it is ChatItem.Thinking } + fresh + thinking)
            }
        }
    }

    /** The page before the oldest message drawn. */
    fun earlier() {
        val thread = _state.value.threadId ?: return
        val before = oldest ?: return
        viewModelScope.launch {
            val page = runCatching { api.messagesPage(thread, before = before, limit = PAGE) }.getOrNull() ?: return@launch
            oldest = page.messages.firstOrNull()?.id ?: oldest
            _state.update { st ->
                if (st.threadId != thread) return@update st
                st.copy(items = (if (page.moreBefore) listOf(ChatItem.Earlier()) else emptyList()) +
                                page.messages.flatMap(::itemsOf) + st.items.filterNot { it is ChatItem.Earlier })
            }
        }
    }

    /** A photo or a document: uploaded now, sent with the next message; offline, kept until it can go. */
    fun attach(bytes: ByteArray, name: String, mime: String) {
        _state.update { it.copy(uploading = true, notice = null) }
        viewModelScope.launch {
            try {
                val r = api.upload(bytes, name, mime)
                _state.update { it.copy(uploading = false, attachments = it.attachments + ChatUiState.Attachment(r.id, name, mime)) }
            } catch (e: NetworkError) {
                val kept = runCatching { outbox.keep(bytes) }.getOrNull()
                _state.update {
                    if (kept == null) it.copy(uploading = false, notice = ChatUiState.Notice.UploadFailed)
                    else it.copy(uploading = false, notice = ChatUiState.Notice.Offline,
                                 attachments = it.attachments + ChatUiState.Attachment("kept-$kept", name, mime, uploadedId = null, path = kept))
                }
            } catch (e: Exception) {
                _state.update { it.copy(uploading = false, notice = ChatUiState.Notice.UploadFailed) }
            }
        }
    }

    fun detach(id: String) {
        val gone = _state.value.attachments.firstOrNull { it.id == id }
        _state.update { it.copy(attachments = it.attachments.filterNot { a -> a.id == id }) }
        gone?.path?.let { viewModelScope.launch { outbox.forget(it) } }
    }

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

    /** The conversations, most recent first (null while loading). */
    private val _threads = MutableStateFlow<List<ThreadDto>?>(null)
    val threads: StateFlow<List<ThreadDto>?> = _threads

    fun loadThreads() {
        _threads.value = null
        viewModelScope.launch { _threads.value = runCatching { api.threads() }.getOrDefault(emptyList()) }
    }

    /** Another conversation, its cards redrawn. */
    fun open(threadId: String) {
        if (threadId == _state.value.threadId) return
        newest = null
        oldest = null
        _state.update { it.copy(items = listOf(ChatItem.Thinking()), threadId = threadId, notice = null) }
        viewModelScope.launch {
            val items = page(threadId)
            _state.update {
                if (it.threadId != threadId) it
                else it.copy(items = items ?: outbox.entries.value.filter { q -> q.threadId == threadId }.map(::bubble),
                             notice = if (items == null) ChatUiState.Notice.Offline else null)
            }
        }
    }

    /** A fresh conversation: the suggestions again, a new thread on the first message. */
    fun startNew() {
        newKey = UUID.randomUUID().toString()
        newest = null
        oldest = null
        _state.update {
            it.copy(items = if (suggestions.isEmpty()) emptyList() else listOf(ChatItem.Suggestions(options = suggestions)),
                    threadId = null, draft = "", attachments = emptyList(), notice = null)
        }
    }

    /** A suggestion chip: sent as if typed. */
    fun ask(text: String) {
        _state.update { it.copy(draft = text) }
        send()
    }

    fun edit(text: String) = _state.update { it.copy(draft = text) }

    fun send() {
        val st = _state.value
        val text = st.draft.trim()
        val files = st.attachments
        if ((text.isEmpty() && files.isEmpty()) || st.uploading) return
        val q = Queued(
            clientId = UUID.randomUUID().toString(), text = text,
            threadId = st.threadId, newThread = if (st.threadId == null) newKey else null,
            files = files.map { QueuedFile(it.name, it.mime, it.path, it.uploadedId) }, queuedAt = now(),
            userId = api.session?.user?.id,
        )
        _state.update {
            it.copy(draft = "", sending = true, notice = null, attachments = emptyList(),
                    items = it.items.filterNot { item -> item is ChatItem.Suggestions } + bubble(q))
        }
        viewModelScope.launch {
            outbox.add(q)
            deliver()
        }
    }

    /** A statement's next difference: a voucher for the next row the books don't have, or "all done". */
    fun nextDifference(statementId: String) {
        if (_state.value.items.any { it is ChatItem.Thinking }) return
        _state.update { it.copy(items = it.items + ChatItem.Thinking(), notice = null) }
        viewModelScope.launch {
            try {
                val r = api.statementNext(statementId, _state.value.threadId)
                _state.update { s ->
                    val have = s.items.map { it.id }.toSet()
                    s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + r.blocks.map(::parseBlock).filterNot { it.id in have },
                           threadId = s.threadId ?: r.threadId)
                }
            } catch (e: NetworkError) {
                _state.update { s -> s.copy(items = s.items.filterNot { it is ChatItem.Thinking }, notice = ChatUiState.Notice.Offline) }
            } catch (e: Exception) {
                _state.update { s -> s.copy(items = s.items.filterNot { it is ChatItem.Thinking } + ChatItem.Problem("e${now()}", null, e.message ?: "")) }
            }
        }
    }

    /** A refused message, sent again. */
    fun retry(clientId: String) {
        _state.update { it.copy(notice = null) }
        viewModelScope.launch {
            outbox.retry(clientId)
            deliver()
        }
    }

    /** Don't send: the waiting message and its bubble go. */
    fun discard(clientId: String) {
        _state.update { it.copy(items = it.items.filterNot { i -> i is ChatItem.User && i.clientId == clientId }) }
        viewModelScope.launch { outbox.drop(clientId) }
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
                    stamp(id, r.block)
                }
            } catch (e: Exception) {
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Draft, error = e.message) }
            }
        }
    }

    /** The posted block's stamp: its reference and date, the undo window, the document it made. */
    private fun stamp(id: String, b: JsonObject?) {
        val st = stampOf(b)
        updateProposal(id) {
            it.copy(phase = ChatItem.Proposal.Phase.Posted, voucher = st.voucher, postedDate = st.date,
                    auditLogId = st.auditLogId, document = st.document,
                    undoUntil = now() + st.undoSeconds * 1000L, error = null)
        }
    }

    /** Someone else's voucher, approved: posted now, stamped, with the undo window. */
    fun approve(id: String) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Posting, error = null) }
        viewModelScope.launch {
            try {
                stamp(id, api.approve(p.token).block)
            } catch (e: Exception) {
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Draft, error = e.message) }
            }
        }
    }

    /** Turned down, with a reason for the one who asked. */
    fun reject(id: String, note: String?) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Posting, error = null) }
        viewModelScope.launch {
            try {
                api.reject(p.token, note)
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Rejected) }
            } catch (e: Exception) {
                updateProposal(id) { it.copy(phase = ChatItem.Proposal.Phase.Draft, error = e.message) }
            }
        }
    }

    /**
     * Change a draft. The server proposes it again through the same checks:
     * the old card says it was changed and the new one follows it. [done]
     * hears null when it worked, else why not (the sheet stays open).
     */
    fun edit(id: String, change: EditRequest, done: (String?) -> Unit) {
        val p = _state.value.items.filterIsInstance<ChatItem.Proposal>().firstOrNull { it.id == id } ?: return
        viewModelScope.launch {
            try {
                val r = api.editProposal(p.token, change)
                val fresh = parseBlock(r.block)
                _state.update { s ->
                    s.copy(items = s.items.flatMap { item ->
                        if (item is ChatItem.Proposal && item.id == id) listOf(item.copy(phase = ChatItem.Proposal.Phase.Replaced), fresh)
                        else listOf(item)
                    }.distinctBy { it.id })
                }
                done(null)
            } catch (e: Exception) {
                done(e.message ?: "")
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
