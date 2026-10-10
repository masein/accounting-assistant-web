package app.accountingassistant.android.util

import android.content.Context
import android.os.Build
import app.accountingassistant.android.data.ApiClient
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.time.Instant

/** What the app keeps of a crash: where it happened, never what it said. */
@Serializable
data class CrashReport(
    val at: String,
    @SerialName("app_version") val appVersion: String,
    val android: Int,
    val device: String,
    val thread: String,
    val exception: String,
    val causes: List<String> = emptyList(),
    val frames: List<String> = emptyList(),
)

/**
 * Crash reports without message content (roadmap ROADMAP_ANDROID_CHAT P1.8),
 * sent to our own server rather than a third party (Firebase is unreliable in
 * Iran, and a crash report is still about someone's books). A crash keeps
 * the exception's class, its causes' classes and the stack frames, never an
 * exception's message, which can hold an amount or the words typed; the app
 * and Android versions and the phone model. Kept in the no-backup folder and
 * sent on the next launch. The user can turn it off in the account sheet.
 */
class Crashes(context: Context, private val appVersion: String, private val device: String,
              private val json: Json = Json { encodeDefaults = true }, private val sdk: Int = Build.VERSION.SDK_INT) {
    private val dir = File(context.noBackupFilesDir, "crashes")
    private val prefs = context.getSharedPreferences("crash_reports", Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = prefs.getBoolean("on", true)
        set(on) {
            prefs.edit().putBoolean("on", on).apply()
            if (!on) dir.deleteRecursively()
        }

    /** Catch what nothing else caught, keep it, then let the system end the app as before. */
    fun install() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching { keep(thread, error) }
            previous?.uncaughtException(thread, error)
        }
    }

    fun reportOf(thread: Thread, error: Throwable, now: Instant = Instant.now()): CrashReport {
        val causes = generateSequence(error.cause) { it.cause }.take(8).toList()
        val root = causes.lastOrNull() ?: error                     // the innermost cause says where it started
        return CrashReport(
            at = now.toString(), appVersion = appVersion, android = sdk, device = device.take(80),
            thread = thread.name.take(60), exception = error.javaClass.name,
            causes = causes.map { it.javaClass.name },
            frames = root.stackTrace.take(40).map {
                "${it.className}.${it.methodName}(${it.fileName ?: "Unknown"}${if (it.lineNumber >= 0) ":${it.lineNumber}" else ""})"
            },
        )
    }

    /** Written at once, on the crashing thread; the newest five are kept. */
    fun keep(thread: Thread, error: Throwable) {
        if (!enabled) return
        dir.mkdirs()
        File(dir, "${System.currentTimeMillis()}-${System.nanoTime()}.json")
            .writeText(json.encodeToString(CrashReport.serializer(), reportOf(thread, error)))
        pending().dropLast(MAX).forEach { it.delete() }
    }

    fun pending(): List<File> = dir.listFiles { f -> f.extension == "json" }?.sortedBy { it.name } ?: emptyList()

    /** Send what was kept; gone once the server has them, kept when it couldn't be reached. */
    suspend fun send(api: ApiClient): Int {
        if (!enabled) return 0
        val files = pending().takeLast(MAX)
        val reports = files.mapNotNull { f -> runCatching { json.decodeFromString(CrashReport.serializer(), f.readText()) }.getOrNull() }
        if (reports.isEmpty()) { files.forEach { it.delete() }; return 0 }
        api.sendCrashes(reports)
        files.forEach { it.delete() }
        return reports.size
    }

    private companion object {
        const val MAX = 5
    }
}
