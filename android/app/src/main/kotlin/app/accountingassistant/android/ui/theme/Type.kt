package app.accountingassistant.android.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import app.accountingassistant.android.R

private fun vazirmatn(weight: Int) = Font(
    R.font.vazirmatn, FontWeight(weight),
    variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
)

/** Vazirmatn (OFL, variable) for every word in the app; its Latin is Roboto-based. */
val Vazirmatn = FontFamily(vazirmatn(400), vazirmatn(500), vazirmatn(600), vazirmatn(700), vazirmatn(800))

/** Reem Kufi, a Kufic face after bannai tilework: display moments only, never with a ZWNJ. */
val ReemKufi = FontFamily(
    Font(R.font.reem_kufi, FontWeight(600), variationSettings = FontVariation.Settings(FontVariation.weight(600))),
    Font(R.font.reem_kufi, FontWeight(700), variationSettings = FontVariation.Settings(FontVariation.weight(700))),
)

val AccountantTypography = Typography(
    displaySmall = TextStyle(fontFamily = ReemKufi, fontWeight = FontWeight(700), fontSize = 30.sp, lineHeight = 38.sp),
    headlineSmall = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(800), fontSize = 22.sp, lineHeight = 30.sp),
    titleMedium = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(800), fontSize = 17.sp, lineHeight = 24.sp),
    titleSmall = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(700), fontSize = 14.sp, lineHeight = 20.sp),
    bodyLarge = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(400), fontSize = 15.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(400), fontSize = 14.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(400), fontSize = 12.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(700), fontSize = 14.sp, lineHeight = 20.sp),
    labelMedium = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(600), fontSize = 12.sp, lineHeight = 16.sp),
    labelSmall = TextStyle(fontFamily = Vazirmatn, fontWeight = FontWeight(600), fontSize = 11.sp, lineHeight = 14.sp),
)

/** Big money: tabular figures, heavy. */
val FigureStyle = TextStyle(
    fontFamily = Vazirmatn, fontWeight = FontWeight(800), fontSize = 24.sp, lineHeight = 30.sp,
    fontFeatureSettings = "tnum",
)
