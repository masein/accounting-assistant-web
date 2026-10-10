package app.accountingassistant.android.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

/** A file waiting to go with its message: kept on the phone until uploaded. */
@Serializable
data class QueuedFile(
    val name: String,
    val mime: String,
    /** Where the outbox keeps its bytes (sealed) until the upload. */
    val path: String? = null,
    /** The server's id once uploaded; a retry doesn't upload it again. */
    val uploadedId: String? = null,
)

/** A message the user sent, kept until the server answers it. */
@Serializable
data class Queued(
    val clientId: String,
    val text: String,
    val threadId: String? = null,
    /**
     * The phone's key for a conversation the server hasn't made yet: the first
     * answer names the thread, and the messages after it go to that thread.
     */
    val newThread: String? = null,
    val files: List<QueuedFile> = emptyList(),
    val queuedAt: Long = 0,
    /** Who typed it: it is only ever sent as them. */
    val userId: String? = null,
    /** The server refused it (its code, its words): it waits for Try again or Don't send. */
    val failure: String? = null,
    val detail: String? = null,
)

/** Where the outbox keeps its messages and files between launches. */
interface OutboxStore {
    suspend fun load(): List<Queued>
    suspend fun save(entries: List<Queued>)
    suspend fun putFile(bytes: ByteArray): String
    suspend fun readFile(path: String): ByteArray
    suspend fun deleteFile(path: String)
    suspend fun clear()
}

/**
 * The outbox on the phone, sealed with a Keystore key like the session: what
 * was typed offline is the user's books, so it never sits on the disk in the
 * clear. In the no-backup folder, so it never leaves the phone either.
 */
class SealedOutboxStore(context: Context, private val json: Json) : OutboxStore {
    private val dir = File(context.noBackupFilesDir, "outbox")
    private val index = File(dir, "entries.bin")
    private val sealer = KeystoreSealer("aa.outbox.v1")
    private val list = ListSerializer(Queued.serializer())

    override suspend fun load(): List<Queued> = withContext(Dispatchers.IO) {
        if (!index.exists()) return@withContext emptyList()
        runCatching { json.decodeFromString(list, String(sealer.open(index.readBytes()), Charsets.UTF_8)) }.getOrDefault(emptyList())
    }

    override suspend fun save(entries: List<Queued>) = withContext(Dispatchers.IO) {
        dir.mkdirs()
        val tmp = File(dir, "entries.tmp")
        tmp.writeBytes(sealer.seal(json.encodeToString(list, entries).toByteArray(Charsets.UTF_8)))
        if (!tmp.renameTo(index)) { index.delete(); tmp.renameTo(index) }
        Unit
    }

    override suspend fun putFile(bytes: ByteArray): String = withContext(Dispatchers.IO) {
        dir.mkdirs()
        val f = File(dir, UUID.randomUUID().toString() + ".bin")
        f.writeBytes(sealer.seal(bytes))
        f.name
    }

    override suspend fun readFile(path: String): ByteArray = withContext(Dispatchers.IO) {
        sealer.open(File(dir, File(path).name).readBytes())
    }

    override suspend fun deleteFile(path: String) = withContext(Dispatchers.IO) {
        File(dir, File(path).name).delete()
        Unit
    }

    override suspend fun clear() = withContext(Dispatchers.IO) {
        dir.deleteRecursively()
        Unit
    }
}

/** For tests and previews. */
class MemoryOutboxStore : OutboxStore {
    private var entries = emptyList<Queued>()
    val files = mutableMapOf<String, ByteArray>()
    override suspend fun load() = entries
    override suspend fun save(entries: List<Queued>) { this.entries = entries }
    override suspend fun putFile(bytes: ByteArray) = UUID.randomUUID().toString().also { files[it] = bytes }
    override suspend fun readFile(path: String) = files.getValue(path)
    override suspend fun deleteFile(path: String) { files.remove(path) }
    override suspend fun clear() { entries = emptyList(); files.clear() }
}

/**
 * Every message goes out through here (roadmap ROADMAP_ANDROID_CHAT P1.5): it
 * is kept first, then sent, oldest first, and kept until the server answers.
 * Offline, it waits, and [wake] asks the system to try again when the network
 * is back (WorkManager), even with the app closed; the answer is in the thread
 * when the app opens. The server answers a message once however often it
 * arrives (its client id), so sending from the app and from the background at
 * once is harmless.
 */
class Outbox(
    private val store: OutboxStore,
    private val api: ApiClient,
    private val wake: () -> Unit = {},
) {
    enum class Result { Done, Offline, Busy }

    /** The message on its way, and the step the server says it is on. */
    data class Sending(val clientId: String, val step: String? = null)

    sealed interface Delivery {
        val entry: Queued
        data class Answered(override val entry: Queued, val reply: ChatReply) : Delivery
        data class Refused(override val entry: Queued, val error: ApiError) : Delivery
    }

    private val lock = Mutex()
    private val flushing = Mutex()
    @Volatile private var loaded = false

    private val _entries = MutableStateFlow<List<Queued>>(emptyList())
    val entries: StateFlow<List<Queued>> = _entries
    private val _sending = MutableStateFlow<Sending?>(null)
    val sending: StateFlow<Sending?> = _sending
    private val _deliveries = MutableSharedFlow<Delivery>(extraBufferCapacity = 64)
    val deliveries: SharedFlow<Delivery> = _deliveries

    suspend fun load() = lock.withLock {
        if (!loaded) {
            _entries.value = store.load()
            loaded = true
        }
    }

    private suspend fun change(edit: (List<Queued>) -> List<Queued>) = lock.withLock {
        if (!loaded) { _entries.value = store.load(); loaded = true }
        val next = edit(_entries.value)
        store.save(next)
        _entries.value = next
    }

    suspend fun add(entry: Queued) = change { it.filterNot { q -> q.clientId == entry.clientId } + entry }

    /** A photo taken offline: kept (sealed) until its message goes. */
    suspend fun keep(bytes: ByteArray): String = store.putFile(bytes)

    /** A kept file that won't be sent after all (taken off the composer). */
    suspend fun forget(path: String) = store.deleteFile(path)

    /** Don't send: the message and its kept files are gone. */
    suspend fun drop(clientId: String) {
        val gone = _entries.value.firstOrNull { it.clientId == clientId }
        change { it.filterNot { q -> q.clientId == clientId } }
        gone?.files?.mapNotNull { it.path }?.forEach { store.deleteFile(it) }
    }

    /** Try again: a refused message goes back in line. */
    suspend fun retry(clientId: String) = change { list ->
        list.map { if (it.clientId == clientId) it.copy(failure = null, detail = null) else it }
    }

    private fun ready(q: Queued) = q.failure == null && (q.userId == null || q.userId == api.session?.user?.id)

    /** Signed in as [userId]: what someone else left typed is gone, never sent as this person. */
    suspend fun keepOnly(userId: String) {
        val others = _entries.value.filter { it.userId != null && it.userId != userId }
        if (others.isEmpty()) return
        change { list -> list.filterNot { it in others } }
        others.flatMap { it.files }.mapNotNull { it.path }.forEach { store.deleteFile(it) }
    }

    /** Signing out: nothing typed stays behind for the next person. */
    suspend fun clear() = lock.withLock {
        store.clear()
        _entries.value = emptyList()
        loaded = true
    }

    /** Send what waits, oldest first, until it is all answered or the network is gone. */
    suspend fun flush(): Result {
        while (true) {
            val r = flushOnce()
            // a message added just as a flush finished: that flush said Busy to its sender, so look again
            if (r != Result.Done || _entries.value.none(::ready)) return r
        }
    }

    private suspend fun flushOnce(): Result {
        load()
        if (!flushing.tryLock()) return Result.Busy           // the running flush takes the new ones too
        try {
            while (true) {
                var q = _entries.value.firstOrNull(::ready) ?: return Result.Done
                _sending.value = Sending(q.clientId)
                try {
                    val ids = mutableListOf<String>()
                    for ((i, f) in q.files.withIndex()) {
                        val id = f.uploadedId ?: api.upload(store.readFile(f.path ?: continue), f.name, f.mime).id.also { up ->
                            q = q.copy(files = q.files.toMutableList().also { it[i] = f.copy(uploadedId = up) })
                            val kept = q
                            change { list -> list.map { if (it.clientId == kept.clientId) kept else it } }
                        }
                        ids += id
                    }
                    val reply = try {
                        api.chatStream(q.text, q.threadId, ids, q.clientId) { step -> _sending.value = Sending(q.clientId, step) }
                    } catch (e: NetworkError) {
                        api.chat(q.text, q.threadId, ids, q.clientId)     // one quiet retry, same id
                    }
                    val answered = q
                    change { list ->
                        list.filterNot { it.clientId == answered.clientId }.map {
                            if (answered.newThread != null && it.newThread == answered.newThread)
                                it.copy(threadId = reply.threadId, newThread = null)
                            else it
                        }
                    }
                    answered.files.mapNotNull { it.path }.forEach { store.deleteFile(it) }
                    _deliveries.emit(Delivery.Answered(answered, reply))
                } catch (e: NetworkError) {
                    wake()
                    return Result.Offline
                } catch (e: ApiError) {
                    when {
                        e.sessionOver || e.updateNeeded -> return Result.Done  // the app signs out or asks for an update
                        e.code == "turn_in_progress" -> { wake(); return Result.Offline }
                        else -> {
                            val refused = q.copy(failure = e.code ?: "error", detail = e.message)
                            change { list -> list.map { if (it.clientId == refused.clientId) refused else it } }
                            _deliveries.emit(Delivery.Refused(refused, e))
                        }
                    }
                }
            }
        } finally {
            _sending.value = null
            flushing.unlock()
        }
    }
}
