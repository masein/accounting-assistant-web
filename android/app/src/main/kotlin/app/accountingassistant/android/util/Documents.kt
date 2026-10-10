package app.accountingassistant.android.util

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File

/** A document from the books, handed to another app: shared, or opened in a viewer. */
object Documents {
    fun hand(context: Context, bytes: ByteArray, name: String, mime: String, share: Boolean) {
        val dir = File(context.cacheDir, "shared").apply { mkdirs() }
        val file = File(dir, name.replace(Regex("[^\\p{L}\\p{N}._-]"), "_"))
        file.writeBytes(bytes)
        val uri = FileProvider.getUriForFile(context, context.packageName + ".files", file)
        val intent = if (share) {
            Intent.createChooser(Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), null)
        } else {
            Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        try {
            context.startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            // no viewer for it on this phone: offer to share it instead
            context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(mime)
                .putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), null))
        }
    }
}
