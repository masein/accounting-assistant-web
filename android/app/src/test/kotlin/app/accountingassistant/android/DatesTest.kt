package app.accountingassistant.android

import app.accountingassistant.android.util.Dates
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate

class DatesTest {
    @Test fun gregorianToJalali() {
        assertEquals(Triple(1405, 7, 18), Dates.toJalali(LocalDate.of(2026, 10, 10)))
        assertEquals(Triple(1405, 1, 1), Dates.toJalali(LocalDate.of(2026, 3, 21)))       // Nowruz
        assertEquals(Triple(1404, 12, 29), Dates.toJalali(LocalDate.of(2026, 3, 20)))
        assertEquals(Triple(1403, 12, 30), Dates.toJalali(LocalDate.of(2025, 3, 20)))     // a leap Esfand
    }

    @Test fun shortDatesInEachLanguage() {
        val today = LocalDate.of(2026, 10, 10)
        assertEquals("۱۸ مهر", Dates.short("2026-10-10T09:30:00+00:00", "fa", today = today))
        assertEquals("۲۹ اسفند ۱۴۰۴", Dates.short("2026-03-20", "fa", today = today))
        assertEquals("10 Oct", Dates.short("2026-10-10", "en", today = today))
        assertEquals("20 Mar 2025", Dates.short("2025-03-20", "en", today = today))
        assertEquals("10 oct", Dates.short("2026-10-10", "es", today = today))
        assertEquals("10 أكتوبر", Dates.short("2026-10-10", "ar", today = today))
    }

    /** A period as Persian says it: «۱ تا ۳۰ شهریور», not «۱ شهریور – ۳۰ شهریور» (Persian copy review, 2026-10-31). */
    @Test fun aPeriodInPersianNamesItsMonthOnce() {
        val today = LocalDate.of(2026, 10, 10)
        assertEquals("۱ تا ۳۰ شهریور", Dates.range("2026-08-23", "2026-09-21", "fa", today = today))
        assertEquals("۱ مرداد تا ۳۰ شهریور", Dates.range("2026-07-23", "2026-09-21", "fa", today = today))
        // a past year once, at the end; across years each end its own
        assertEquals("۱ تا ۳۰ شهریور ۱۴۰۴", Dates.range("2025-08-23", "2025-09-21", "fa", today = today))
        assertEquals("۲۰ اسفند ۱۴۰۴ تا ۱۰ فروردین", Dates.range("2026-03-11", "2026-03-30", "fa", today = today))
        // a Gregorian company read in Persian: its own months
        assertEquals("۱ تا ۳۰ سپتامبر", Dates.range("2026-09-01", "2026-09-30", "fa", "gregorian", today))
        // other languages as before
        assertEquals("\u20681 Sep – 30 Sep\u2069", Dates.range("2026-09-01", "2026-09-30", "en", today = today))
        assertEquals("\u20681 Shahrivar – 30 Shahrivar\u2069", Dates.range("2026-08-23", "2026-09-21", "en", "jalali", today))
    }

    /** Scenario N38: the books' calendar, written as each language writes it, the same words as the replies. */
    @Test fun theBooksCalendarDecidesNotTheLanguage() {
        val today = LocalDate.of(2026, 10, 10)
        assertEquals("۱۰ اکتبر", Dates.short("2026-10-10", "fa", "gregorian", today))        // a Gregorian company, read in Persian
        assertEquals("۲۳ سپتامبر", Dates.short("2026-09-23", "fa", "gregorian", today))
        assertEquals("18 Mehr", Dates.short("2026-10-10", "en", "jalali", today))            // a Jalali company, read in English
        assertEquals("۱۸ مهر", Dates.short("2026-10-10", "fa", "jalali", today))
        assertEquals("18 مهر", Dates.short("2026-10-10", "ar", "jalali", today))
        assertEquals("1 dic 2025", Dates.short("2025-12-01", "es", "gregorian", today))
    }
}
