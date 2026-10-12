package app.accountingassistant.android.util

/**
 * Money and digits as each language writes them. Persian: Persian digits and
 * the Arabic thousands separator (٬); Arabic, English and Spanish: Western
 * digits and a comma, as the web and the server write them (Arabic was
 * Arabic-Indic until the owner chose Western, 2026-10-11). Amounts are in the
 * currency's own unit with its places (docs/PLAN_PENCE.md): £7.50 is 7.50 and
 * shows as «7.50», whole rials show none.
 */
object Numbers {
    private const val PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
    private const val ARABIC = "٠١٢٣٤٥٦٧٨٩"            // read, never written: typed (normalise) or from Android's formatting

    fun digits(text: String, lang: String): String = when (lang) {
        "fa" -> text.map { if (it in '0'..'9') PERSIAN[it - '0'] else it }.joinToString("")
        // everyone else writes Western digits, Arabic too; Android formats %d in the phone's locale
        // (Arabic-Indic in Arabic: «١٨ أسطر»), so those come back to Western here
        else -> text.map { val a = ARABIC.indexOf(it); if (a >= 0) '0' + a else it }.joinToString("")
    }

    /** How many decimal places [currency] has (ISO 4217; rials none, dinars 3), as the server's app/core/money.py. */
    fun places(currency: String?): Int = when (currency?.trim()?.uppercase()) {
        "IRR", "IRT", "JPY", "KRW", "VND", "CLP", "ISK", "PYG", "UGX", "XAF", "XOF", "XPF", "BIF", "DJF", "GNF",
        "KMF", "RWF", "VUV" -> 0
        "BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND" -> 3
        null, "" -> 0
        else -> 2
    }

    /**
     * An amount with [places] decimals, rounded half-up: 7.5 with 2 → "7.50",
     * 2500000 with 0 → "۲٬۵۰۰٬۰۰۰" (fa, with the Persian decimal mark ٫ when it has places).
     */
    fun amount(value: java.math.BigDecimal, lang: String, places: Int): String {
        val v = value.setScale(places, java.math.RoundingMode.HALF_UP)
        val abs = v.abs()
        val whole = abs.toBigInteger().toString()
        val sep = if (lang == "fa") '٬' else ','
        val grouped = whole.reversed().chunked(3).joinToString(sep.toString()).reversed()
        val frac = if (places > 0) (if (lang == "fa") "٫" else ".") + abs.toPlainString().substringAfter('.', "").padEnd(places, '0') else ""
        return digits((if (v.signum() < 0) "−" else "") + grouped + frac, lang)
    }

    /** What a person typed as an amount, read exactly: «۷٫۵۰», «1,234.56», «7.5». Null when it isn't one. */
    fun parseAmount(typed: String): java.math.BigDecimal? {
        val t = normalise(typed).replace(",", "").replace(" ", "").trim()
        if (t.isEmpty() || t.count { it == '.' } > 1 || !t.all { it.isDigit() || it == '.' }) return null
        return t.toBigDecimalOrNull()
    }

    /** 2500000 → "۲٬۵۰۰٬۰۰۰" (fa) or "2,500,000" (en). Negative amounts keep their sign. */
    fun amount(value: Long, lang: String): String {
        val sep = if (lang == "fa") '٬' else ','
        val abs = kotlin.math.abs(value).toString()
        val grouped = abs.reversed().chunked(3).joinToString(sep.toString()).reversed()
        return digits((if (value < 0) "−" else "") + grouped, lang)
    }

    /**
     * A change with its sign, kept left to right as one unit: «+۱۸۰٬۰۰۰٬۰۰۰»
     * inside Persian words would otherwise put the plus after the number.
     */
    fun signed(value: Long, lang: String): String =
        "\u2066" + (if (value > 0) "+" else if (value < 0) "−" else "") + amount(kotlin.math.abs(value), lang) + "\u2069"

    /** The same for an exact amount, with its currency's [places]. */
    fun signed(value: java.math.BigDecimal, lang: String, places: Int): String =
        "\u2066" + (if (value.signum() > 0) "+" else if (value.signum() < 0) "−" else "") + amount(value.abs(), lang, places) + "\u2069"

    /**
     * How much of something, as each language writes it: «۴۰٪» (fa), "40 %"
     * (es, with its space), "40%" (en and ar). [fraction] is 0–1.
     */
    fun percent(fraction: Float, lang: String): String {
        val n = (fraction.coerceIn(0f, 1f) * 100).toInt().toString()
        return when (lang) {
            "fa" -> digits(n, lang) + "٪"
            "es" -> "$n\u00A0%"
            else -> "$n%"
        }
    }

    /** Persian and Arabic digits typed by the user, back to Western ones. */
    fun normalise(text: String): String = text.map {
        val p = PERSIAN.indexOf(it)
        val a = ARABIC.indexOf(it)
        when {
            p >= 0 -> '0' + p
            a >= 0 -> '0' + a
            it == '٬' || it == '،' -> ','
            it == '٫' -> '.'
            else -> it
        }
    }.joinToString("")
}
