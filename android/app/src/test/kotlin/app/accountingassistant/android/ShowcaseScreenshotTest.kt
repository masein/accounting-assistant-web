package app.accountingassistant.android

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.ui.showcase.ChatShowcase
import app.accountingassistant.android.ui.theme.AccountantTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The opening scene in both languages and both themes, draft and stamped.
 * `./gradlew :app:recordRoborazziDebug` writes the images to
 * app/build/outputs/roborazzi; `verifyRoborazziDebug` compares against them.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class ShowcaseScreenshotTest {
    @get:Rule val compose = createComposeRule()

    private fun shot(name: String, lang: String, dark: Boolean, posted: Boolean) {
        compose.setContent {
            AccountantTheme(dark = dark) { ChatShowcase(lang = lang, startPosted = posted, fixedUndoSeconds = 119) }
        }
        compose.mainClock.advanceTimeBy(1500)
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianLightDraft() = shot("showcase-fa-light-draft", "fa", dark = false, posted = false)

    @Test @Config(qualifiers = "+fa")
    fun persianLightStamped() = shot("showcase-fa-light-stamped", "fa", dark = false, posted = true)

    @Test @Config(qualifiers = "+fa-night")
    fun persianDarkStamped() = shot("showcase-fa-dark-stamped", "fa", dark = true, posted = true)

    @Test @Config(qualifiers = "+en")
    fun englishLightDraft() = shot("showcase-en-light-draft", "en", dark = false, posted = false)

    @Test @Config(qualifiers = "+en-night")
    fun englishDarkStamped() = shot("showcase-en-dark-stamped", "en", dark = true, posted = true)
}
