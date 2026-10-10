package app.accountingassistant.android

import android.content.Intent
import android.net.Uri
import app.accountingassistant.android.util.Shared
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class SharedTest {
    @Test fun aSharedSmsBecomesWords() {
        val i = Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_TEXT, "بانک ملت\nبرداشت: ۲٬۵۰۰٬۰۰۰\nمانده: ۴۱٬۲۰۰٬۰۰۰")
        val s = Shared.from(i)!!
        assertEquals("بانک ملت\nبرداشت: ۲٬۵۰۰٬۰۰۰\nمانده: ۴۱٬۲۰۰٬۰۰۰", s.text)
        assertEquals(0, s.files.size)
    }

    @Test fun aSharedStatementBecomesAFile() {
        val uri = Uri.parse("content://bank/statement-mehr.pdf")
        val s = Shared.from(Intent(Intent.ACTION_SEND).setType("application/pdf").putExtra(Intent.EXTRA_STREAM, uri))!!
        assertEquals(listOf(uri), s.files)
    }

    @Test fun anOrdinaryLaunchIsNotAShare() {
        assertNull(Shared.from(Intent(Intent.ACTION_MAIN)))
        assertNull(Shared.from(Intent(Intent.ACTION_SEND).setType("text/plain")))       // nothing in it
    }
}
