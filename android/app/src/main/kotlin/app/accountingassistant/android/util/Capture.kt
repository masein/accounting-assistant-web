package app.accountingassistant.android.util

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import java.io.ByteArrayOutputStream
import java.io.File

/** What the phone sends: the bytes, a name, and their type. */
class Picked(val bytes: ByteArray, val name: String, val mime: String)

object Capture {
    /** The longest side of a photo sent for reading: enough for a receipt, light on mobile data. */
    const val MAX_SIDE = 1600

    /** A photo of a receipt, turned upright, scaled down and saved as JPEG. */
    fun photo(context: Context, uri: Uri): Picked {
        val bitmap: Bitmap = if (Build.VERSION.SDK_INT >= 28) {
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, info, _ ->
                val (w, h) = info.size.width to info.size.height
                val scale = minOf(1f, MAX_SIDE.toFloat() / maxOf(w, h))
                decoder.setTargetSize((w * scale).toInt().coerceAtLeast(1), (h * scale).toInt().coerceAtLeast(1))
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            }
        } else {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            context.contentResolver.openInputStream(uri).use { BitmapFactory.decodeStream(it, null, bounds) }
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= MAX_SIDE) sample *= 2
            context.contentResolver.openInputStream(uri).use {
                BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
            } ?: error("unreadable image")
        }
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 80, out)
        return Picked(out.toByteArray(), (displayName(context, uri) ?: "receipt").substringBeforeLast('.') + ".jpg", "image/jpeg")
    }

    /** A PDF or a spreadsheet, as it is. */
    fun document(context: Context, uri: Uri): Picked {
        val bytes = context.contentResolver.openInputStream(uri).use { it?.readBytes() } ?: error("unreadable file")
        val mime = context.contentResolver.getType(uri) ?: "application/octet-stream"
        return Picked(bytes, displayName(context, uri) ?: "document", mime)
    }

    private fun displayName(context: Context, uri: Uri): String? =
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
}

/** Hold to talk: AAC in an .m4a, mono, small enough to send at once. */
class VoiceRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var file: File? = null

    fun start() {
        val f = File(context.cacheDir, "voice-${System.currentTimeMillis()}.m4a")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        r.setAudioChannels(1)
        r.setAudioSamplingRate(16_000)
        r.setAudioEncodingBitRate(32_000)
        r.setOutputFile(f.absolutePath)
        r.prepare()
        r.start()
        recorder = r
        file = f
    }

    /** The recording, or null when it was too short to hold any words. */
    fun stop(): Picked? {
        val r = recorder ?: return null
        recorder = null
        val ok = runCatching { r.stop() }.isSuccess          // stop() throws when nothing was recorded
        r.release()
        val f = file ?: return null
        file = null
        val bytes = if (ok && f.length() > 0) f.readBytes() else null
        f.delete()
        return bytes?.let { Picked(it, "voice-note.m4a", "audio/mp4") }
    }
}
