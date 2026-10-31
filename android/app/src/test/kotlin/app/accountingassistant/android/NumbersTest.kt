package app.accountingassistant.android

import app.accountingassistant.android.ui.components.priced
import app.accountingassistant.android.ui.components.pricedInWords
import app.accountingassistant.android.util.Numbers
import org.junit.Assert.assertEquals
import org.junit.Test

class NumbersTest {
    @Test fun persianAmountsUsePersianDigitsAndTheArabicSeparator() {
        assertEquals("۲٬۵۰۰٬۰۰۰", Numbers.amount(2_500_000, "fa"))
        assertEquals("۱٬۰۰۵٬۵۴۵٬۵۵۰", Numbers.amount(1_005_545_550, "fa"))
        assertEquals("−۱۲۰", Numbers.amount(-120, "fa"))
    }

    @Test fun englishAmountsUseCommas() {
        assertEquals("2,500,000", Numbers.amount(2_500_000, "en"))
        assertEquals("999", Numbers.amount(999, "en"))
        assertEquals("1,000", Numbers.amount(1000, "es"))
    }

    @Test fun arabicUsesArabicIndicDigits() {
        assertEquals("2,500", Numbers.amount(2500, "ar"))               // Western digits in Arabic, as on the web
        // Android formats %d in the phone's locale: Arabic-Indic in Arabic, brought back to Western
        assertEquals("18 أسطر", Numbers.digits("١٨ أسطر", "ar"))
    }

    @Test fun typedPersianAndArabicDigitsAreNormalised() {
        assertEquals("2,500,000", Numbers.normalise("۲٬۵۰۰٬۰۰۰"))
        assertEquals("2.5", Numbers.normalise("۲٫۵"))
        assertEquals("120", Numbers.normalise("١٢٠"))
    }

    @Test fun aChangeKeepsItsSignInFrontInEveryDirection() {
        assertEquals("\u2066+۱۸۰٬۰۰۰٬۰۰۰\u2069", Numbers.signed(180_000_000, "fa"))
        assertEquals("\u2066−2,500\u2069", Numbers.signed(-2_500, "en"))
        assertEquals("\u20660\u2069", Numbers.signed(0, "en"))
    }

    /** Beside Persian digits a middle dot reads as a zero, «۱۸۰ مهر»: «،» between the pieces (Persian copy review, 2026-10-31). */
    @Test fun piecesOfALineAreJoinedWithACommaInPersianAndArabic() {
        assertEquals("، ", Numbers.separator("fa"))
        assertEquals("، ", Numbers.separator("ar"))
        assertEquals(" · ", Numbers.separator("en"))
        assertEquals(" · ", Numbers.separator("es"))
        // what came as it was sent: by its own letters and digits, whatever the phone's language
        assertEquals("کیف پول سبز، ۲۴ مهر ۱۴۰۵", Numbers.joined(listOf("کیف پول سبز", "۲۴ مهر ۱۴۰۵")))
        assertEquals("Rent، ۷۷٫۵۰۰", Numbers.joined(listOf("Rent", "۷۷٫۵۰۰")))
        assertEquals("Office rent · £950.00", Numbers.joined(listOf("Office rent", "£950.00")))
        assertEquals("Office rent", Numbers.joined(listOf("Office rent")))
    }

    /** The widget's other currencies: named after the figure in Persian, «۵۰۰ دلار», not «۵۰۰٫۰۰$» (Persian copy review, 2026-10-31). */
    @Test fun anAmountAmongPersianWordsNamesItsCurrency() {
        assertEquals("۵۰۰ دلار", pricedInWords(java.math.BigDecimal("500.00"), "USD", "fa"))
        assertEquals("۵۰۰٫۵۰ دلار", pricedInWords(java.math.BigDecimal("500.5"), "USD", "fa"))
        assertEquals("۱۲ یورو", pricedInWords(java.math.BigDecimal(12), "EUR", "fa"))
        assertEquals("۷۷٫۵۰۰ ریال عمان", pricedInWords(java.math.BigDecimal("77.5"), "OMR", "fa"))
        assertEquals("۳٬۰۰۰٬۰۰۰ ریال", pricedInWords(java.math.BigDecimal(3_000_000), "IRR", "fa"))
        // other languages as before
        assertEquals("\$500.00", pricedInWords(java.math.BigDecimal(500), "USD", "en"))
        assertEquals("\$500.00", pricedInWords(java.math.BigDecimal(500), "USD", "ar"))
    }

    @Test fun aShareIsWrittenTheWayEachLanguageWritesIt() {
        assertEquals("۴۰٪", Numbers.percent(0.4f, "fa"))
        assertEquals("40%", Numbers.percent(0.4f, "ar"))
        assertEquals("40\u00A0%", Numbers.percent(0.4f, "es"))
        assertEquals("100%", Numbers.percent(1.3f, "en"))
        assertEquals("0%", Numbers.percent(0f, "en"))
    }

    /** The chart and the statement wrote «25,003 GBP» under a card saying «£23,923» (phone QA, 2026-10-12). */
    @Test fun anAmountIsWrittenAsTheCardsWriteIt() {
        assertEquals("£23,923.00", priced(23_923, "GBP", "en"))          // a currency with places shows them
        assertEquals("−£50.00", priced(-50, "gbp", "en"))
        assertEquals("€1,440.00", priced(1_440, "EUR", "es"))
        assertEquals("£7.50", priced(java.math.BigDecimal("7.5"), "GBP", "en"))
        assertEquals("£25,003", priced(25_003, "GBP", "en", compact = true))   // a chart stays compact
        assertEquals("1.235 KWD", priced(java.math.BigDecimal("1.2345"), "KWD", "en"))
        assertEquals("۴۹٬۶۰۰٬۰۰۰ ریال", priced(49_600_000, "IRR", "fa"))
        assertEquals("1,440 IRR", priced(1_440, "IRR", "en"))
        assertEquals("1,440", priced(1_440, null, "en"))
    }
}
