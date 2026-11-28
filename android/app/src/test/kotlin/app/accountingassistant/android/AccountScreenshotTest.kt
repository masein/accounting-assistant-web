package app.accountingassistant.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import app.accountingassistant.android.data.CurrencyOption
import app.accountingassistant.android.data.DeviceDto
import app.accountingassistant.android.sms.SmsSender
import app.accountingassistant.android.ui.account.AccountSheetContent
import app.accountingassistant.android.ui.account.SmsSection
import app.accountingassistant.android.ui.account.CurrencySection
import app.accountingassistant.android.ui.account.LockScreen
import app.accountingassistant.android.ui.account.UpdateScreen
import app.accountingassistant.android.ui.theme.AccountantTheme
import app.accountingassistant.android.ui.theme.LocalAccountantColors
import app.accountingassistant.android.util.Numbers
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
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

    /** «Read bank SMS» outside Play (scenarios Y5–Y8): a sender to decide on, one read, one ignored. */
    @Test @Config(qualifiers = "+fa")
    fun persianBankSms() {
        compose.setContent {
            AccountantTheme(dark = false) {
                Box(Modifier.fillMaxSize().background(LocalAccountantColors.current.surface)) {
                    AccountSheetContent(
                        username = "maryam", booksName = "شرکت بازرگانی آرمان", role = "owner", devices = emptyList(),
                        lockAvailable = true, lockOn = true, onLock = {}, onRevoke = {}, onSignOut = {},
                        sms = SmsSection(on = true, denied = false, onSwitch = {}, onDecide = { _, _ -> }, senders = listOf(
                            SmsSender("98700717", "Mellat", null) to 2,
                            SmsSender("9830007", "Saman", true) to 0,
                            SmsSender("100088", null, false) to 0,
                        )),
                    )
                }
            }
        }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/account-sms-fa.png")
    }

    /** A person's books choose their currency (scenario Z3): Change opens the choices in place. */
    @Test @Config(qualifiers = "+fa")
    fun persianBooksCurrency() {
        compose.setContent {
            AccountantTheme(dark = false) {
                Box(Modifier.fillMaxSize().background(LocalAccountantColors.current.surface)) {
                    AccountSheetContent(
                        username = "masein", booksName = "masein", role = "personal", devices = emptyList(),
                        lockAvailable = true, lockOn = true, onLock = {}, onRevoke = {}, onSignOut = {},
                        currency = CurrencySection("IRR", "ریال ایران", listOf(
                            CurrencyOption("IRR", "ریال ایران"), CurrencyOption("OMR", "ریال عمان"),
                            CurrencyOption("AED", "درهم امارات"), CurrencyOption("USD", "دلار آمریکا"),
                        ), onChoose = {}),
                    )
                }
            }
        }
        compose.onNodeWithText("تغییر").performClick()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/account-currency-fa.png")
    }

    @Test @Config(qualifiers = "+fa-night")
    fun persianLockScreenDark() {
        compose.setContent { AccountantTheme(dark = true) { LockScreen(onUnlock = {}, askAtOnce = false) } }
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/lock-fa-dark.png")
    }

    /** Scenario CX2: an app too old for the server says so in the phone's language, with the version it needs. */
    private fun update(name: String, lang: String, dark: Boolean = false) {
        compose.setContent { AccountantTheme(dark = dark) { UpdateScreen(minVersion = "0.2.0", lang = lang) } }
        val app = RuntimeEnvironment.getApplication()
        compose.onNodeWithText(app.getString(R.string.update_title)).assertExists()
        compose.onNodeWithText(app.getString(R.string.update_body)).assertExists()
        compose.onNodeWithText(app.getString(R.string.update_version, "\u2066" + Numbers.digits("0.2.0", lang) + "\u2069")).assertExists()
        compose.onRoot().captureRoboImage("build/outputs/roborazzi/$name.png")
    }

    @Test @Config(qualifiers = "+fa")
    fun persianUpdate() {
        update("update-fa", "fa")
        assertEquals("برنامه را به\u200Cروز کنید", RuntimeEnvironment.getApplication().getString(R.string.update_title))
    }

    @Test @Config(qualifiers = "+en-night")
    fun englishUpdateDark() {
        update("update-en-dark", "en", dark = true)
        assertEquals("Update the app", RuntimeEnvironment.getApplication().getString(R.string.update_title))
    }

    @Test @Config(qualifiers = "+ar")
    fun arabicUpdate() {
        update("update-ar", "ar")
        assertEquals("حدّث التطبيق", RuntimeEnvironment.getApplication().getString(R.string.update_title))
    }

    @Test @Config(qualifiers = "+es")
    fun spanishUpdate() {
        update("update-es", "es")
        assertEquals("Actualiza la app", RuntimeEnvironment.getApplication().getString(R.string.update_title))
    }
}
