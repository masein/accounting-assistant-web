package app.accountingassistant.android.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * The palette of docs/design/android-chat.html: Persian tile colours, each
 * with one job. Firouzeh = you can act; saffron = personal books and things
 * waiting for you; pomegranate = money leaving, overdue, reject.
 */
@Immutable
data class AccountantColors(
    val ground: Color,
    val surface: Color,
    val surface2: Color,
    val ink: Color,
    val muted: Color,
    val line: Color,
    val firouzeh: Color,
    val onFirouzeh: Color,
    val firouzehSoft: Color,
    val saffron: Color,
    val saffronSoft: Color,
    val pomegranate: Color,
    val indigo: Color,
    val bubble: Color,
    val onBubble: Color,
    val glass: Color,
    val isDark: Boolean,
)

val LightColors = AccountantColors(
    ground = Color(0xFFF1F5F6), surface = Color(0xFFFFFFFF), surface2 = Color(0xFFE7EEF0),
    ink = Color(0xFF14243B), muted = Color(0xFF5B6B7C), line = Color(0xFFDBE4E8),
    firouzeh = Color(0xFF006D77), onFirouzeh = Color(0xFFFFFFFF), firouzehSoft = Color(0xFFD5EBEB),
    saffron = Color(0xFFA9650A), saffronSoft = Color(0xFFFBECD2), pomegranate = Color(0xFFB23A26),
    indigo = Color(0xFF2F4A9A), bubble = Color(0xFF14243B), onBubble = Color(0xFFF3F7F9),
    glass = Color(0xB8FFFFFF), isDark = false,
)

val DarkColors = AccountantColors(
    ground = Color(0xFF09121C), surface = Color(0xFF121D2A), surface2 = Color(0xFF1A2737),
    ink = Color(0xFFE7EEF3), muted = Color(0xFF8FA1B3), line = Color(0xFF243548),
    firouzeh = Color(0xFF5FC9CD), onFirouzeh = Color(0xFF03272B), firouzehSoft = Color(0xFF11383D),
    saffron = Color(0xFFF0B45A), saffronSoft = Color(0xFF3B2A10), pomegranate = Color(0xFFF17D68),
    indigo = Color(0xFF93A8F2), bubble = Color(0xFF24405A), onBubble = Color(0xFFEEF4F8),
    glass = Color(0xBD1A2737), isDark = true,
)

val LocalAccountantColors = staticCompositionLocalOf { LightColors }

/** A set of books' colour: it follows the books onto the badge, every card and the seal. */
enum class BooksColor { Firouzeh, Saffron, Indigo;

    fun of(c: AccountantColors): Color = when (this) {
        Firouzeh -> c.firouzeh
        Saffron -> c.saffron
        Indigo -> c.indigo
    }
}
