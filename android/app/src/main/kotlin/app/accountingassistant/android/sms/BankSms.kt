package app.accountingassistant.android.sms

import android.content.Context
import app.accountingassistant.android.data.KeystoreSealer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

/** A bank SMS kept on the phone: until the user names its sender, or until it is sent. */
@Serializable
data class SmsMessage(
    val id: String,
    val sender: String,
    val text: String,
    /** When the phone received it (epoch millis): dates a message that carries no date. */
    val receivedAt: Long,
    /** The bank the sender was named as, for a message that names none. */
    val bank: String? = null,
)

/** A sender the user was asked about: read its SMS into the books, ignore them, or not decided yet. */
@Serializable
data class SmsSender(val sender: String, val bank: String? = null, val read: Boolean? = null)

@Serializable
data class SmsState(
    val senders: List<SmsSender> = emptyList(),
    /** From senders not decided yet: kept, sealed, until the user says. */
    val waiting: List<SmsMessage> = emptyList(),
    /** From senders the user reads: to send. */
    val outbox: List<SmsMessage> = emptyList(),
)

/** Where the phone keeps the senders and the messages between launches. */
interface BankSmsStore {
    suspend fun load(): SmsState
    suspend fun save(state: SmsState)
    suspend fun clear()
}

/**
 * Sealed with a Keystore key like the outbox: bank messages are the user's
 * books, so they never sit on the disk in the clear, and in the no-backup
 * folder they never leave the phone either.
 */
class SealedBankSmsStore(context: Context, private val json: Json) : BankSmsStore {
    private val file = File(context.noBackupFilesDir, "bank_sms.bin")
    private val sealer = KeystoreSealer("aa.banksms.v1")

    override suspend fun load(): SmsState = withContext(Dispatchers.IO) {
        if (!file.exists()) return@withContext SmsState()
        runCatching { json.decodeFromString(SmsState.serializer(), String(sealer.open(file.readBytes()), Charsets.UTF_8)) }
            .getOrDefault(SmsState())
    }

    override suspend fun save(state: SmsState) = withContext(Dispatchers.IO) {
        val tmp = File(file.parentFile, "bank_sms.tmp")
        tmp.writeBytes(sealer.seal(json.encodeToString(SmsState.serializer(), state).toByteArray(Charsets.UTF_8)))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        Unit
    }

    override suspend fun clear() = withContext(Dispatchers.IO) { file.delete(); Unit }
}

/**
 * What happens to each SMS on the phone (roadmap ROADMAP_ANDROID_CHAT §7.1,
 * scenarios Y6–Y9). Only messages from senders the user said are their bank
 * leave the phone, and of those only the ones that look like a movement on an
 * account (a one-time code from the same number stays). A sender not named yet
 * whose message looks like a bank's is asked about once; its messages wait,
 * sealed. Anything else is not kept at all.
 */
class BankSms(private val store: BankSmsStore, private val newId: () -> String = { UUID.randomUUID().toString() }) {
    private val lock = Mutex()

    enum class Route { Queued, Asked, Held, Ignored, NotKept }

    /** Where an SMS goes: [Route.Queued] to send, [Route.Asked] to notify about its new sender. */
    suspend fun arrived(sender: String, text: String, at: Long): Route = lock.withLock {
        val key = BankText.senderKey(sender)
        val s = store.load()
        val known = s.senders.firstOrNull { it.sender == key }
        if (known?.read == false || !BankText.looksLikeMovement(text)) {
            return@withLock if (known?.read == false) Route.Ignored else Route.NotKept
        }
        val bank = known?.bank ?: BankText.bankOf(text)
        val msg = SmsMessage(newId(), key, text, at, bank)
        when (known?.read) {
            true -> { store.save(s.copy(outbox = (s.outbox + msg).takeLast(MAX_KEPT))); Route.Queued }
            else -> {
                store.save(s.copy(senders = if (known == null) s.senders + SmsSender(key, bank) else s.senders,
                                  waiting = (s.waiting + msg).takeLast(MAX_KEPT)))
                if (known == null) Route.Asked else Route.Held
            }
        }
    }

    /** The user's answer for a sender: its waiting messages go to be sent, or are thrown away. */
    suspend fun decide(sender: String, read: Boolean) = lock.withLock {
        val s = store.load()
        val mine = s.waiting.filter { it.sender == sender }
        val senders = if (s.senders.any { it.sender == sender }) s.senders.map { if (it.sender == sender) it.copy(read = read) else it }
                      else s.senders + SmsSender(sender, null, read)
        store.save(s.copy(senders = senders, waiting = s.waiting - mine.toSet(),
                          outbox = if (read) (s.outbox + mine).takeLast(MAX_KEPT) else s.outbox))
    }

    /** The senders, with how many messages wait for each. */
    suspend fun senders(): List<Pair<SmsSender, Int>> = lock.withLock {
        val s = store.load()
        s.senders.map { it to s.waiting.count { m -> m.sender == it.sender } }
    }

    suspend fun outbox(): List<SmsMessage> = lock.withLock { store.load().outbox }

    /** Sent, or refused for good: no longer kept. */
    suspend fun done(ids: Set<String>) = lock.withLock {
        val s = store.load()
        store.save(s.copy(outbox = s.outbox.filterNot { it.id in ids }))
    }

    /** Signed out: the next person starts with nothing. */
    suspend fun clear() = lock.withLock { store.clear() }

    companion object {
        /** Kept at most, of each kind: a phone off the network for weeks doesn't fill up. */
        const val MAX_KEPT = 500
        /** At most this many in one request (the server's limit). */
        const val BATCH = 50
    }
}
