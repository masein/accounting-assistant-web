package app.accountingassistant.android

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
        assertEquals("٢٬٥٠٠", Numbers.amount(2500, "ar"))
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

    @Test fun aShareIsWrittenTheWayEachLanguageWritesIt() {
        assertEquals("۴۰٪", Numbers.percent(0.4f, "fa"))
        assertEquals("٤٠٪", Numbers.percent(0.4f, "ar"))
        assertEquals("40\u00A0%", Numbers.percent(0.4f, "es"))
        assertEquals("100%", Numbers.percent(1.3f, "en"))
        assertEquals("0%", Numbers.percent(0f, "en"))
    }
}
