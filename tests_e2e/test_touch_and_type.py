"""On a phone every control is big enough for a thumb, the sidebar's section
labels are readable in Persian, and a Jalali date field is one field to a
screen reader (deep browser test, 2026-10-02: #7, #40, #41)."""
from __future__ import annotations

from tests_e2e.conftest import switch_language
from tests_e2e.test_jalali_dates import POST, _owner

SMALL = r"""() => [...document.querySelectorAll('.card[data-page] .chip, .card[data-page] .btn-sm, .card[data-page] .il-del,'
  + ' .card[data-page] input[type=checkbox]')]
  .filter(e => e.offsetParent !== null)
  .map(e => [e, e.getBoundingClientRect()])
  .filter(([e, r]) => e.type === 'checkbox' ? (r.width < 20 || r.height < 20) : (r.height < 32 || r.width < 32))
  .map(([e, r]) => (e.className || e.type) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height) + ' «' + (e.textContent || '').trim().slice(0, 20) + '»')"""


def test_thumb_sized_controls_readable_labels_and_one_date_field(browser, flow_page):
    octx, owner = _owner(browser)
    try:
        # an invoice, so the list has its row buttons and PDF link (alone, the list was empty)
        owner.evaluate(POST, ["/invoices", {"number": "TOUCH-1", "kind": "sales", "status": "issued", "amount": 1000,
                                            "issue_date": "2026-09-01", "due_date": "2026-10-01"}])
        assert owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "jalali"}])[0] == 200
        page, watch = flow_page("e2e_touch")
        page.set_viewport_size({"width": 390, "height": 844})
        switch_language(page, "fa")
        page.reload()
        page.wait_for_load_state("networkidle")
        small = {}
        for name in ("ai-accountant", "invoices", "transactions", "commitments", "entities", "recurring"):
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            if name == "invoices" and page.locator("#inv-mode-itemized").is_visible():
                page.click("#inv-mode-itemized")
            found = page.evaluate(SMALL)
            if found:
                small[name] = found
        assert small == {}, small
        exposed = page.evaluate("() => [...document.querySelectorAll('.jdate-native')].filter(e => e.getAttribute('aria-hidden') !== 'true').map(e => e.id)")
        assert page.evaluate("() => document.querySelectorAll('.jdate-native').length") > 0
        assert exposed == [], exposed
        label = page.evaluate("""() => { const h = document.querySelector('.nav-section-h'); const s = getComputedStyle(h);
            return [parseFloat(s.fontSize), s.letterSpacing]; }""")
        assert label[0] >= 12 and label[1] in ("0px", "normal"), label
        assert watch.problems() == [], watch.problems()
    finally:
        owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "gregorian"}])
        octx.close()
