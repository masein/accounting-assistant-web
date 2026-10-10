package app.accountingassistant.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onRoot
import app.accountingassistant.android.data.DeviceDto
import app.accountingassistant.android.ui.account.AccountSheetContent
import app.accountingassistant.android.ui.account.LockScreen
import app.accountingassistant.android.ui.theme.AccountantTheme
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The account sheet (phones signed in, the app lock) and the lock screen. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w393dp-h852dp-xxhdpi")
class AccountScreenshotTest {
    @get:Rule val compose = createComposeRule()

    @Test @Config(qualifiers = "+fa")
    fun persianAccountSheet() {
        compose.setContent {
            AccountantTheme(dark = false) {
                Box(Modifier.fillMaxSize().background(LocalAccountantColors.current.surface)) {
                    AccountSheetContent(
                        username = "maryam", booksName = "شرکت بازرگانی آرمان", role = "owner",
                        devices = listOf(DeviceDto("d1", "Google Pixel 8", thisDevice = true), DeviceDto("d2", "Samsung Galaxy A54")),
                        lockAvailable = true, lockOn = true, onLock = {}, onRevoke = {}, onSignOut = {},
                    )
                }
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/account-fa.png")
    }

    @Test @Config(qualifiers = "+fa-night")
    fun persianLockScreenDark() {
        compose.setContent { AccountantTheme(dark = true) { LockScreen(onUnlock = {}, askAtOnce = false) } }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/lock-fa-dark.png")
    }
}
