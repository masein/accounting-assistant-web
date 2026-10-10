package app.accountingassistant.android.util

import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * Dates as each language writes them: Jalali months in Persian («۱۸ مهر»),
 * Gregorian elsewhere ("10 Oct"). The server sends ISO dates.
 */
object Dates {
    private val FA_MONTHS = listOf("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                                   "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
    private val EN_MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

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

    /** "۱۸ مهر" / "10 Oct"; the year too when it isn't this year. */
    fun short(iso: String?, lang: String, today: LocalDate = LocalDate.now()): String {
        val d = parse(iso) ?: return ""
        return if (lang == "fa") {
            val (jy, jm, jd) = toJalali(d)
            val thisYear = toJalali(today).first
            Numbers.digits("$jd ${FA_MONTHS[jm - 1]}" + if (jy != thisYear) " $jy" else "", lang)
        } else {
            "${d.dayOfMonth} ${EN_MONTHS[d.monthValue - 1]}" + if (d.year != today.year) " ${d.year}" else ""
        }
    }

    private fun parse(iso: String?): LocalDate? = iso?.let {
        runCatching { OffsetDateTime.parse(it).atZoneSameInstant(ZoneId.systemDefault()).toLocalDate() }.getOrNull()
            ?: runCatching { LocalDate.parse(it.take(10)) }.getOrNull()
    }
}
