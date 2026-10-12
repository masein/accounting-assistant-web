"""Persian digits on the web, safely (scenarios I12, I13; the owner's call,
2026-10-12): a Persian page writes every figure in Persian digits, as the chat
and the phone do; what a form holds, what is exported and what is sent stay
Latin; account codes read in Persian too; English pages are unchanged."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

TABLE = """() => {
  const box = document.createElement('div');
  box.innerHTML = '<table><thead><tr><th>x</th></tr></thead><tbody><tr><td>' + formatNum(1234567) + '</td></tr></tbody></table>';
  document.body.appendChild(box);
  _exportTableFromEl(box, 'digits', 'csv');
  box.remove();
}"""


def test_a_persian_page_writes_persian_digits_and_keeps_latin_where_it_must(flow_page):
    page, watch = flow_page("e2e_fadigits")
    switch_language(page, "fa")
    page.evaluate("() => { location.hash = 'transactions'; }")
    page.wait_for_load_state("networkidle")
    debit = page.locator("#lines-tbody tr:first-child .line-debit")
    debit.fill("1250000")
    debit.dispatch_event("input")
    wait_until(page, "() => (document.getElementById('bal-debit') || {}).innerText?.includes('۱٬۲۵۰٬۰۰۰')")
    assert debit.input_value() == "1250000"               # what the form holds and sends stays Latin
    hint = page.inner_text("#date-jalali-hint")
    assert hint and not any(c in "0123456789" for c in hint), hint     # redrawn when the language changed
    shown = page.evaluate("""() => [formatNum(1234567), formatMoney(2500, 'GBP'), formatNum(-12.5),
                                    formatKpiValue(40, '%'), window.Chart && Chart.defaults.locale]""")
    assert shown == ["۱٬۲۳۴٬۵۶۷", "£۲٬۵۰۰", "-۱۲٫۵", "۴۰٪", "fa-IR"], shown
    page.evaluate("() => { location.hash = 'accounts'; }")              # account codes read in Persian too
    wait_until(page, "() => [...document.querySelectorAll('#coa-tree td')].some(td => td.textContent.trim() === '۱۱۱۰')")
    assert page.evaluate("() => [...document.querySelectorAll('#coa-tree td')].some(td => td.textContent.trim() === '1110')") is False
    assert page.evaluate("() => localCode('1110')") == "۱۱۱۰"
    with page.expect_download() as dl:                       # a CSV is read by other programs: Latin
        page.evaluate(TABLE)
    assert '"1,234,567"' in open(dl.value.path(), encoding="utf-8").read()
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "web-persian-digits.png"))
    switch_language(page, "en")                               # English pages are unchanged
    assert page.evaluate("() => [formatNum(1234567), Chart.defaults.locale, localCode('1110')]") == ["1,234,567", "en-US", "1110"]
    assert watch.problems() == [], watch.problems()
