package app.accountingassistant.android.util

import android.content.Intent
import android.net.Uri
import android.os.Build

/** What another app shared into the accountant: words, files, or both. */
data class Shared(val text: String?, val files: List<Uri>) {
    val isEmpty: Boolean get() = text.isNullOrBlank() && files.isEmpty()

    companion object {
        fun from(intent: Intent?): Shared? {
            if (intent == null) return null
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()?.take(4000)
            val files: List<Uri> = when (intent.action) {
                Intent.ACTION_SEND -> listOfNotNull(stream(intent))
                Intent.ACTION_SEND_MULTIPLE -> streams(intent)
                else -> return null
            }
            return Shared(text, files.take(5)).takeUnless { it.isEmpty }
        }

        private fun stream(intent: Intent): Uri? =
            if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_STREAM)

        private fun streams(intent: Intent): List<Uri> =
            if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
            else @Suppress("DEPRECATION") intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
    }
}
