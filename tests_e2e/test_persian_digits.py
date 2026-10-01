"""An amount typed on a Persian keyboard lands in the field: Chrome's number
inputs drop ۰–۹ and ٠–٩, so typing «۱۲۳۴» into a journal line left it empty."""
from __future__ import annotations

PASTE = r"""(t) => { const el = document.activeElement; const dt = new DataTransfer(); dt.setData('text', t);
  el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }"""


def test_persian_digits_go_into_a_number_field(flow_page):
    page, watch = flow_page("e2e_persian")
    page.evaluate("() => { location.hash = 'transactions'; }")
    page.wait_for_load_state("networkidle")
    debit = page.locator("#lines-tbody tr:first-child .line-debit")
    debit.fill("")
    debit.click()
    page.keyboard.insert_text("۱۲۳۴")                    # a Persian keyboard / input method
    assert debit.input_value() == "1234"
    debit.fill("")
    debit.click()
    page.keyboard.type("٩٨٧")                             # Arabic-Indic, key by key
    assert debit.input_value() == "987"
    debit.fill("")
    debit.click()
    page.evaluate(PASTE, "۱٬۲۵۰٬۰۰۰")                     # pasted with its thousands separators
    assert debit.input_value() == "1250000"
    assert "1,250,000" in page.inner_text("#bal-debit")    # the field's own listeners saw it
    assert watch.problems() == [], watch.problems()
