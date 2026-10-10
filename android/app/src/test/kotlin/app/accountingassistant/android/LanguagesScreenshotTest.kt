package app.accountingassistant.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.data.ThreadDto
import app.accountingassistant.android.ui.chat.ChatItem
import app.accountingassistant.android.ui.chat.ChatScreen
import app.accountingassistant.android.ui.chat.ChatUiState
import app.accountingassistant.android.ui.chat.ThreadsContent
import app.accountingassistant.android.ui.theme.AccountantTheme
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The first run in Arabic (right to left) and Spanish, and the conversations sheet in Persian. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class LanguagesScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private fun firstRun(name: String, lang: String) {
        compose.setContent {
            AccountantTheme(dark = false) {
                val sugg = LocalContext.current.resources.getStringArray(R.array.suggestions).toList()
                ChatScreen(state = ChatUiState(items = listOf(ChatItem.Suggestions(options = sugg))), lang = lang,
                           booksName = "Arman", personalBooks = false, onDraft = {}, onSend = {}, onConfirm = {},
                           onCancel = {}, onUndo = {}, onBooks = {}, onThreads = {}, userInitial = "M")
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+ar") fun arabicFirstRun() = firstRun("first-run-ar", "ar")
    @Test @Config(qualifiers = "+es") fun spanishFirstRun() = firstRun("first-run-es", "es")

    @Test @Config(qualifiers = "+fa")
    fun persianConversations() {
        compose.setContent {
            AccountantTheme(dark = false) {
                Box(Modifier.fillMaxSize().background(LocalAccountantColors.current.surface)) {
                    ThreadsContent(threads = listOf(
                        ThreadDto("t1", "حقوق مهر", "2026-10-10T09:30:00+00:00", 6),
                        ThreadDto("t2", "فاکتور آریا", "2026-10-08T12:00:00+00:00", 4),
                        ThreadDto("t3", null, "2026-03-19T08:00:00+00:00", 2),
                    ), current = "t1", lang = "fa", onOpen = {}, onNew = {})
                }
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/threads-fa.png")
    }
}
