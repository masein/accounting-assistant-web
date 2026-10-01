"""The browser's form messages are in the user's language: "Please fill out
this field." and "Value must be greater than or equal to 0." came in the
browser's language on the voucher form, whatever the page's was. (The bubble
itself is the browser's; validationMessage is what it shows.)"""
from __future__ import annotations

from tests_e2e.conftest import switch_language

MESSAGE = r"""(sel) => { const el = document.querySelector(sel); el.form ? el.form.checkValidity() : el.checkValidity();
  return el.validationMessage; }"""


def test_the_voucher_form_says_what_is_wrong_in_persian(flow_page):
    page, watch = flow_page("e2e_valid")
    try:
        switch_language(page, "fa")
        page.click('.nav-btn[data-page="transactions"]')
        page.wait_for_selector("#transaction-form .line-debit")
        page.fill("#date", "")
        assert page.evaluate(MESSAGE, "#date") == "این خانه را پر کنید."
        page.fill("#date", "2026-10-01")
        assert page.evaluate(MESSAGE, "#date") == ""            # a value: valid again, no message left behind

        debit = "#transaction-form .line-debit >> nth=0"
        page.fill(debit, "-5")
        assert page.evaluate(MESSAGE, "#transaction-form .line-debit") == "نمی‌تواند کمتر از 0 باشد."
        page.fill(debit, "10.5")                                 # step 1
        assert page.evaluate(MESSAGE, "#transaction-form .line-debit") == "با گام‌های 1 وارد کنید."
        page.fill(debit, "10")
        assert page.evaluate(MESSAGE, "#transaction-form .line-debit") == ""

        # a script's own message is left as it is
        assert page.evaluate("""() => { const el = document.getElementById('date'); el.setCustomValidity('Custom');
            el.form.checkValidity(); const m = el.validationMessage; el.setCustomValidity(''); return m; }""") == "Custom"

        switch_language(page, "en")
        page.fill("#date", "")
        assert page.evaluate(MESSAGE, "#date") == "Fill in this field."
        page.fill("#date", "2026-10-01")
        assert watch.problems() == [], watch.problems()
    finally:
        try:
            switch_language(page, "en")
        except Exception:
            pass
