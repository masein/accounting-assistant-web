package app.accountingassistant.android.util

/**
 * Money and digits as each language writes them. Persian: Persian digits and
 * the Arabic thousands separator (٬); Arabic: Arabic-Indic digits; English
 * and Spanish: Western digits and a comma. Amounts are whole units of the
 * books' currency (rial, pound), as the server sends them.
 */
object Numbers {
    private const val PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
    private const val ARABIC = "٠١٢٣٤٥٦٧٨٩"

    fun digits(text: String, lang: String): String = when (lang) {
        "fa" -> text.map { if (it in '0'..'9') PERSIAN[it - '0'] else it }.joinToString("")
        "ar" -> text.map { if (it in '0'..'9') ARABIC[it - '0'] else it }.joinToString("")
        else -> text
    }

    /** 2500000 → "۲٬۵۰۰٬۰۰۰" (fa) or "2,500,000" (en). Negative amounts keep their sign. */
    fun amount(value: Long, lang: String): String {
        val sep = if (lang == "fa" || lang == "ar") '٬' else ','
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

    /**
     * How much of something, as each language writes it: «۴۰٪» (fa), «٤٠٪»
     * (ar), "40 %" (es, with its space), "40%" (en). [fraction] is 0–1.
     */
    fun percent(fraction: Float, lang: String): String {
        val n = (fraction.coerceIn(0f, 1f) * 100).toInt().toString()
        return when (lang) {
            "fa", "ar" -> digits(n, lang) + "٪"
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
