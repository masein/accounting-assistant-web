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
        assertEquals("۱۸ مهر", Dates.short("2026-10-10T09:30:00+00:00", "fa", today))
        assertEquals("۲۹ اسفند ۱۴۰۴", Dates.short("2026-03-20", "fa", today))
        assertEquals("10 Oct", Dates.short("2026-10-10", "en", today))
        assertEquals("20 Mar 2025", Dates.short("2025-03-20", "en", today))
    }
}
