package app.accountingassistant.android.ui.components

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/** The few line icons the chat needs, drawn on a 24 grid like the design board's. */
object AppIcons {
    private fun icon(name: String, block: ImageVector.Builder.() -> Unit) = ImageVector.Builder(
        name = name, defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f,
    ).apply(block).build()

    private fun ImageVector.Builder.stroke(block: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit) =
        path(stroke = SolidColor(Color.Black), strokeLineWidth = 2f, strokeLineCap = StrokeCap.Round,
             strokeLineJoin = StrokeJoin.Round, pathBuilder = block)

    val Plus: ImageVector = icon("Plus") {
        stroke { moveTo(12f, 5f); lineTo(12f, 19f); moveTo(5f, 12f); lineTo(19f, 12f) }
    }

    val Mic: ImageVector = icon("Mic") {
        stroke {
            moveTo(12f, 3f); curveTo(10.3f, 3f, 9f, 4.3f, 9f, 6f); lineTo(9f, 11f)
            curveTo(9f, 12.7f, 10.3f, 14f, 12f, 14f); curveTo(13.7f, 14f, 15f, 12.7f, 15f, 11f)
            lineTo(15f, 6f); curveTo(15f, 4.3f, 13.7f, 3f, 12f, 3f); close()
            moveTo(5f, 11f); curveTo(5f, 14.9f, 8.1f, 18f, 12f, 18f); curveTo(15.9f, 18f, 19f, 14.9f, 19f, 11f)
            moveTo(12f, 18f); lineTo(12f, 21f)
        }
    }

    val Send: ImageVector = icon("Send") {
        stroke { moveTo(12f, 19f); lineTo(12f, 5f); moveTo(6f, 11f); lineTo(12f, 5f); lineTo(18f, 11f) }
    }

    val Share: ImageVector = icon("Share") {
        stroke {
            moveTo(4f, 12f); lineTo(4f, 19f); curveTo(4f, 20.1f, 4.9f, 21f, 6f, 21f); lineTo(18f, 21f)
            curveTo(19.1f, 21f, 20f, 20.1f, 20f, 19f); lineTo(20f, 12f)
            moveTo(16f, 6f); lineTo(12f, 2f); lineTo(8f, 6f); moveTo(12f, 2f); lineTo(12f, 15f)
        }
    }

    val ChevronDown: ImageVector = icon("ChevronDown") {
        stroke { moveTo(7f, 10f); lineTo(12f, 15f); lineTo(17f, 10f) }
    }
}
