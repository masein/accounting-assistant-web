package app.accountingassistant.android.ui.chat

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import app.accountingassistant.android.util.Capture
import app.accountingassistant.android.util.Picked
import java.io.File

/** The phone's ways in: the camera, the photo picker, a file, the microphone. */
class CaptureLaunchers(
    val camera: () -> Unit,
    val photo: () -> Unit,
    val file: () -> Unit,
    /** Starts listening, asking for the microphone the first time. */
    val speak: (start: () -> Unit) -> Unit,
)

@Composable
fun rememberCapture(onPicked: (Picked) -> Unit): CaptureLaunchers {
    val context = LocalContext.current
    val shot = remember { arrayOfNulls<Uri>(1) }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = shot[0]
        if (ok && uri != null) runCatching { Capture.photo(context, uri) }.onSuccess(onPicked)
    }
    val photo = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) runCatching { Capture.photo(context, uri) }.onSuccess(onPicked)
    }
    val file = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) runCatching { Capture.document(context, uri) }.onSuccess(onPicked)
    }
    val mic = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { /* the next press records */ }
    return remember {
        CaptureLaunchers(
            camera = { shot[0] = newPhotoUri(context); camera.launch(shot[0]!!) },
            photo = { photo.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
            file = { file.launch(arrayOf("application/pdf", "image/*", "text/csv", "application/vnd.ms-excel",
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) },
            speak = { start ->
                if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) start()
                else mic.launch(Manifest.permission.RECORD_AUDIO)
            },
        )
    }
}

private fun newPhotoUri(context: Context): Uri {
    val dir = File(context.cacheDir, "captures").apply { mkdirs() }
    val file = File(dir, "receipt-${System.currentTimeMillis()}.jpg")
    return FileProvider.getUriForFile(context, context.packageName + ".files", file)
}
