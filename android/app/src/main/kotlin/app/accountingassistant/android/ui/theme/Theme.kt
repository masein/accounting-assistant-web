package app.accountingassistant.android.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.MaterialExpressiveTheme
import androidx.compose.material3.MotionScheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.unit.dp

val AccountantShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(20.dp),   // cards and the voucher
    large = RoundedCornerShape(28.dp),    // sheets
    extraLarge = RoundedCornerShape(36.dp),
)

/**
 * Material 3 Expressive with our own colours: dynamic colour stays off,
 * because a set of books' colour carries meaning and must not shift with the
 * wallpaper.
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun AccountantTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val c = if (dark) DarkColors else LightColors
    val scheme = if (dark) {
        darkColorScheme(
            primary = c.firouzeh, onPrimary = c.onFirouzeh, primaryContainer = c.firouzehSoft, onPrimaryContainer = c.ink,
            secondary = c.saffron, secondaryContainer = c.saffronSoft, onSecondaryContainer = c.ink,
            tertiary = c.indigo, error = c.pomegranate,
            background = c.ground, onBackground = c.ink, surface = c.surface, onSurface = c.ink,
            surfaceVariant = c.surface2, onSurfaceVariant = c.muted, outline = c.line, outlineVariant = c.line,
            surfaceContainer = c.surface, surfaceContainerHigh = c.surface2, surfaceContainerLow = c.surface,
        )
    } else {
        lightColorScheme(
            primary = c.firouzeh, onPrimary = c.onFirouzeh, primaryContainer = c.firouzehSoft, onPrimaryContainer = c.ink,
            secondary = c.saffron, secondaryContainer = c.saffronSoft, onSecondaryContainer = c.ink,
            tertiary = c.indigo, error = c.pomegranate,
            background = c.ground, onBackground = c.ink, surface = c.surface, onSurface = c.ink,
            surfaceVariant = c.surface2, onSurfaceVariant = c.muted, outline = c.line, outlineVariant = c.line,
            surfaceContainer = c.surface, surfaceContainerHigh = c.surface2, surfaceContainerLow = c.surface,
        )
    }
    CompositionLocalProvider(LocalAccountantColors provides c) {
        MaterialExpressiveTheme(
            colorScheme = scheme,
            motionScheme = MotionScheme.expressive(),
            typography = AccountantTypography,
            shapes = AccountantShapes,
            content = content,
        )
    }
}
