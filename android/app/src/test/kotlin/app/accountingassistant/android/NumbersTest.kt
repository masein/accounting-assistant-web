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
}
