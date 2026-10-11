package app.accountingassistant.android.util

import androidx.compose.runtime.staticCompositionLocalOf
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId

/** The books' calendar ("jalali" or "gregorian"), from the session; null: decide by language. */
val LocalCalendar = staticCompositionLocalOf<String?> { null }

/**
 * Dates in the books' calendar, as each language writes it: «۱۸ مهر» or
 * «۵ اکتبر» in Persian, "5 oct" in Spanish, «٥ أكتوبر» in Arabic, "5 Oct".
 * The server sends ISO dates and writes its replies in the same calendar.
 */
object Dates {
    private val FA_MONTHS = listOf("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                                   "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
    private val EN_MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    private val JALALI_LATIN = listOf("Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                                      "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand")
    /** The server's names (calendar_periods, blocks), so a date reads the same in the card and the reply. */
    private val GREGORIAN = mapOf(
        "fa" to listOf("ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"),
        "es" to listOf("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"),
        "ar" to listOf("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
    )

    /** Gregorian → Jalali (the arithmetic algorithm, exact for 1300–1500 AP). */
    fun toJalali(date: LocalDate): Triple<Int, Int, Int> {
        val gy = date.year; val gm = date.monthValue; val gd = date.dayOfMonth
        val gdm = intArrayOf(0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
        val gy2 = if (gm > 2) gy + 1 else gy
        var days = 355666 + 365 * gy + (gy2 + 3) / 4 - (gy2 + 99) / 100 + (gy2 + 399) / 400 + gd + gdm[gm - 1]
        var jy = -1595 + 33 * (days / 12053)
        days %= 12053
        jy += 4 * (days / 1461)
        days %= 1461
        if (days > 365) {
            jy += (days - 1) / 365
            days = (days - 1) % 365
        }
        val jm = if (days < 186) 1 + days / 31 else 7 + (days - 186) / 30
        val jd = 1 + if (days < 186) days % 31 else (days - 186) % 30
        return Triple(jy, jm, jd)
    }

    /**
     * «۱۸ مهر» / "10 Oct"; the year too when it isn't this year. [calendar]
     * is the books' (LocalCalendar); without one, Persian is Jalali.
     */
    fun short(iso: String?, lang: String, calendar: String? = null, today: LocalDate = LocalDate.now()): String {
        val d = parse(iso) ?: return ""
        val jalali = calendar?.let { it == "jalali" } ?: (lang == "fa")
        val text = if (jalali) {
            val (jy, jm, jd) = toJalali(d)
            val months = if (lang == "fa" || lang == "ar") FA_MONTHS else JALALI_LATIN
            "$jd ${months[jm - 1]}" + if (jy != toJalali(today).first) " $jy" else ""
        } else {
            val months = GREGORIAN[lang] ?: EN_MONTHS
            "${d.dayOfMonth} ${months[d.monthValue - 1]}" + if (d.year != today.year) " ${d.year}" else ""
        }
        return Numbers.digits(text, lang)
    }

    private fun parse(iso: String?): LocalDate? = iso?.let {
        runCatching { OffsetDateTime.parse(it).atZoneSameInstant(ZoneId.systemDefault()).toLocalDate() }.getOrNull()
            ?: runCatching { LocalDate.parse(it.take(10)) }.getOrNull()
    }
}
