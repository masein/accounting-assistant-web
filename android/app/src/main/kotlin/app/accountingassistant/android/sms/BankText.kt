package app.accountingassistant.android.sms

/**
 * Does an SMS look like a movement on a bank account? Read on the phone only,
 * to decide whether to ask about a sender the user hasn't named yet, and to
 * keep anything else (a one-time code, an advert) from leaving the phone
 * (roadmap ROADMAP_ANDROID_CHAT §7.1). The banks and their spellings are the
 * server's (app/services/bank_sms.py BANKS), normalised the same way, so a
 * bank named here is the one the server files the message under.
 */
object BankText {
    /** Canonical name → the spellings seen in SMS (after [normalize]). */
    val BANKS: Map<String, List<String>> = linkedMapOf(
        "Mellat" to listOf("ملت"), "Melli" to listOf("ملی ایران", "بانک ملی", "ملی"), "Saderat" to listOf("صادرات"),
        "Tejarat" to listOf("تجارت"), "Saman" to listOf("سامان"), "Blu" to listOf("بلو", "blu"),
        "Pasargad" to listOf("پاسارگاد"), "Ayandeh" to listOf("آینده", "اینده"), "Parsian" to listOf("پارسیان"),
        "Keshavarzi" to listOf("کشاورزی"), "Sepah" to listOf("سپه"), "Refah" to listOf("رفاه"),
        "Resalat" to listOf("رسالت"), "Maskan" to listOf("مسکن"), "Shahr" to listOf("بانک شهر", "شهر"),
        "EghtesadNovin" to listOf("اقتصاد نوین", "اقتصادنوین"), "Sina" to listOf("سینا"), "Dey" to listOf("بانک دی"),
        "ToseeTaavon" to listOf("توسعه تعاون"), "PostBank" to listOf("پست بانک", "پست‌بانک"),
        "Karafarin" to listOf("کارآفرین", "کارافرین"), "Sarmayeh" to listOf("سرمایه"),
        "Iranzamin" to listOf("ایران زمین", "ایرانزمین"), "Gardeshgari" to listOf("گردشگری"),
        "MehrIran" to listOf("مهر ایران", "قرض الحسنه مهر"), "Tosee" to listOf("توسعه صادرات"),
        "Middle East" to listOf("خاورمیانه"),
    )

    /** Money moving, or the balance after it: the server's direction and balance words. */
    private val WORDS = listOf("واریز", "انتقال", "دریافت", "سود", "بستانکار", "افزایش", "برگشت", "برداشت", "خرید",
                               "پرداخت", "کارمزد", "قسط", "بدهکار", "کسر", "مانده", "موجودی")
    /** A sum: grouped by thousands, or four figures and more. */
    private val AMOUNT = Regex("""\d{1,3}(?:,\d{3})+|\d{4,}""")
    // the direction marks, written as escapes for the regex: none sits in the source as itself
    private val BIDI = Regex("""[\u200E\u200F\u202A-\u202E\u2066-\u2069]""")

    /** As app/services/bank_sms.py normalize: one form of each letter and digit. */
    fun normalize(text: String): String = buildString {
        for (ch in text.replace(BIDI, "")) {
            append(when (ch) {
                in '۰'..'۹' -> '0' + (ch - '۰')
                in '٠'..'٩' -> '0' + (ch - '٠')
                'ي' -> 'ی'
                'ك' -> 'ک'
                'ة' -> 'ه'
                '‌' -> ' '
                '：' -> ':'
                '٬', '،' -> ','
                '٫' -> '.'
                else -> ch
            })
        }
    }.lines().joinToString("\n") { it.trim() }.trim()

    /** The bank a message names (its longest spelling found), or null. */
    fun bankOf(text: String): String? {
        val low = normalize(text).lowercase()
        var best: Pair<String, Int>? = null
        for ((name, spellings) in BANKS) for (sp in spellings) {
            if (low.contains(sp.lowercase()) && (best == null || sp.length > best.second)) best = name to sp.length
        }
        return best?.first
    }

    /** A movement on an account: a sum with a movement or balance word, from a named bank or in rials or tomans. */
    fun looksLikeMovement(text: String): Boolean {
        val t = normalize(text)
        val fromABank = bankOf(t) != null || "ریال" in t || "تومان" in t
        return fromABank && WORDS.any { it in t } && AMOUNT.containsMatchIn(t)
    }

    /** One form of a sender: «+98 700-717» and «98700717» are the same. */
    fun senderKey(raw: String): String =
        normalize(raw).filter { !it.isWhitespace() && it != '-' }.removePrefix("+").lowercase()

    /** The bank as the reader's language names it: «بانک ملت», «Mellat». */
    fun displayName(bank: String?, lang: String): String? {
        if (bank == null || bank !in BANKS) return null
        if (lang != "fa") return bank
        val fa = BANKS.getValue(bank).first()
        return if (fa.startsWith("بانک") || fa.startsWith("پست")) fa else "بانک $fa"
    }
}
