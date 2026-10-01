"""With the Jalali calendar chosen, a date is typed and picked in Jalali: the
voucher's date was the browser's Gregorian field ("mm/dd/yyyy") with the
Jalali day printed under it. The field (dressDateInputs, js/03-ui.js) keeps
the native input as the value — in ISO, as scripts and tests set it."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS, switch_language
from tests_e2e.test_jalali_dates import POST, _owner

ROUND_TRIP = r"""() => { let bad = 0;
  for (let t = Date.UTC(1990, 0, 1); t <= Date.UTC(2050, 11, 31); t += 86400000) {
    const iso = new Date(t).toISOString().slice(0, 10);
    if (jalaliTextToIso(isoToJalaliText(iso)) !== iso) bad++; }
  return bad; }"""
FIELD = r"""() => { const i = document.getElementById('date'), w = i.closest('.jdate');
  return w ? { text: w.querySelector('.jdate-text').value, value: i.value,
               invalid: w.querySelector('.jdate-text').getAttribute('aria-invalid') } : null; }"""


def _text(page):
    return page.locator("#date").locator("xpath=..").locator(".jdate-text")


def test_a_jalali_company_types_and_picks_dates_in_jalali(browser, flow_page):
    octx, owner = _owner(browser)
    page = None
    try:
        assert owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "jalali"}])[0] == 200
        page, watch = flow_page("e2e_jdate")
        switch_language(page, "fa")
        page.reload()
        page.wait_for_load_state("networkidle")
        page.click('.nav-btn[data-page="transactions"]')
        page.wait_for_function("() => document.getElementById('date').closest('.jdate')", timeout=5_000)
        assert page.evaluate(ROUND_TRIP) == 0   # every day 1990–2050 there and back
        # the Jalali day once printed under the field is not said twice
        assert not page.locator("#date-jalali-hint").is_visible()

        # typed in Persian digits → the value is the Gregorian day, in ISO
        _text(page).fill("۱۴۰۵/۰۱/۰۱")
        assert page.evaluate(FIELD)["value"] == "2026-03-21"
        # not a day (no 13th month): said so, and the value stays
        _text(page).fill("1405/13/01")
        _text(page).press("Tab")
        assert page.evaluate(FIELD) == {"text": "1405/13/01", "value": "2026-03-21", "invalid": "true"}

        # the month grid opens on the field's month, starts on Saturday, and picks
        page.click("#date >> xpath=.. >> .jdate-btn")
        pop = page.locator("#jdate-pop")
        assert pop.is_visible()
        assert pop.locator(".jdate-title").inner_text() == "فروردین 1405"
        assert pop.locator(".jdate-wd").first.inner_text() == "ش"
        # 1 Farvardin 1405 is a Saturday: no blank cells before it
        assert page.evaluate("() => document.querySelector('#jdate-pop .jdate-grid').children[7].className") == "jdate-day"
        pop.locator('.jdate-day[data-iso="2026-04-04"]').click()   # 15 Farvardin
        assert not pop.is_visible()
        assert page.evaluate(FIELD) == {"text": "1405/01/15", "value": "2026-04-04", "invalid": None}

        # the next month, then Escape: closed, the value as it was
        page.click("#date >> xpath=.. >> .jdate-btn")
        pop.locator(".jdate-next").click()
        assert pop.locator(".jdate-title").inner_text() == "اردیبهشت 1405"
        page.keyboard.press("Escape")
        assert not pop.is_visible() and page.evaluate(FIELD)["value"] == "2026-04-04"

        # from the keyboard: Alt+Down opens it on the chosen day; arrows walk the days (left is
        # the next day right to left) and over into the next month; Enter picks
        _text(page).focus()
        page.keyboard.press("Alt+ArrowDown")
        assert page.evaluate("() => document.activeElement.dataset.iso") == "2026-04-04"
        page.keyboard.press("ArrowLeft")
        assert page.evaluate("() => document.activeElement.dataset.iso") == "2026-04-05"
        for _ in range(4):
            page.keyboard.press("ArrowDown")      # 4 weeks on: 13 Ordibehesht
        assert pop.locator(".jdate-title").inner_text() == "اردیبهشت 1405"
        page.keyboard.press("Enter")
        assert page.evaluate(FIELD) == {"text": "1405/02/13", "value": "2026-05-03", "invalid": None}
        assert page.evaluate("() => document.activeElement.classList.contains('jdate-text')")

        # a script setting the value, and a test filling the native field, repaint the box
        page.evaluate("() => { document.getElementById('date').value = '2026-10-01'; }")
        assert page.evaluate(FIELD)["text"] == "1405/07/09"
        page.fill("#date", "2026-09-22")
        assert page.evaluate(FIELD)["text"] == "1405/06/31"

        # back to Gregorian: the browser's own field again
        assert owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "gregorian"}])[0] == 200
        page.reload()
        page.wait_for_load_state("networkidle")
        page.click('.nav-btn[data-page="transactions"]')
        assert page.evaluate("() => document.querySelectorAll('.jdate').length") == 0
        assert page.locator("#date").is_visible()
        page.fill("#date", "2026-10-01")
        assert page.locator("#date-jalali-hint").inner_text() == "1405/07/09"   # under a Gregorian field, it helps
        assert watch.problems() == [], watch.problems()
    except Exception:
        if page is not None:
            os.makedirs(ARTIFACTS, exist_ok=True)
            page.screenshot(path=os.path.join(ARTIFACTS, "jalali-date-field.png"), full_page=True)
        raise
    finally:
        owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "gregorian"}])
        octx.close()
