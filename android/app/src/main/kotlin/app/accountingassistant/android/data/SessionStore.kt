package app.accountingassistant.android.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.Json
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Where the phone keeps its tokens between launches. */
interface SessionStore {
    suspend fun load(): StoredSession?
    suspend fun save(session: StoredSession)
    suspend fun clear()
}

private val Context.sessionData by preferencesDataStore(name = "session")

/** The session sealed with a Keystore key ([KeystoreSealer]). */
class KeystoreSessionStore(private val context: Context, private val json: Json) : SessionStore {
    private val slot = stringPreferencesKey("sealed")

    override suspend fun load(): StoredSession? {
        val sealed = context.sessionData.data.first()[slot] ?: return null
        return runCatching { json.decodeFromString(StoredSession.serializer(), open(sealed)) }.getOrNull()
    }

    override suspend fun save(session: StoredSession) {
        val sealed = seal(json.encodeToString(StoredSession.serializer(), session))
        context.sessionData.edit { it[slot] = sealed }
    }

    override suspend fun clear() {
        context.sessionData.edit { it.remove(slot) }
    }

    private fun seal(plain: String): String = Base64.encodeToString(sealer.seal(plain.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)

    private fun open(sealed: String): String = String(sealer.open(Base64.decode(sealed, Base64.NO_WRAP)), Charsets.UTF_8)

    private companion object {
        val sealer = KeystoreSealer("aa.session.v1")
    }
}

/**
 * AES-GCM with a key that lives in the Android Keystore: the key never leaves
 * the secure hardware, so a copied data folder is useless on another phone.
 * Sealed bytes are the 12-byte IV followed by the ciphertext and its tag.
 */
class KeystoreSealer(private val alias: String) {
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }

    fun seal(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
        return cipher.iv + cipher.doFinal(plain)
    }

    fun open(sealed: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, sealed, 0, IV_BYTES))
        return cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)
    }

    private companion object {
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}

/** For tests and previews. */
class MemorySessionStore(private var session: StoredSession? = null) : SessionStore {
    override suspend fun load() = session
    override suspend fun save(session: StoredSession) { this.session = session }
    override suspend fun clear() { session = null }
}
