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
