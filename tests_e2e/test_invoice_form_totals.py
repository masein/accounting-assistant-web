"""The invoice form's totals read like the list's: 0–9 and the currency's
symbol — on a Persian browser they came in Persian digits ("IRR ۰"), and a
UK company's read "3,600 GBP" next to a list of "£3,600"; the Iran-only goods
id column is a UK company's no more (deep browser test, 2026-10-02, #18, #34)."""
from __future__ import annotations

from tests_e2e.conftest import BASE_URL, _flow_states, _log_in
from tests_e2e.test_jalali_dates import _owner

PUT = r"""async ([path, body]) => { const r = await fetch(path, { method: 'PUT',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return r.status; }"""


def test_the_form_totals_read_like_the_list(browser):
    octx, owner = _owner(browser)
    before = owner.evaluate("async () => (await (await fetch('/admin/reporting-locale')).json()).locale")
    if "e2e_invform" not in _flow_states:
        _flow_states["e2e_invform"] = _log_in(browser, "e2e_invform")
    try:
        for locale in ("uk", "ir"):
            assert owner.evaluate(PUT, ["/admin/reporting-locale", {"locale": locale}]) == 200
            ctx = browser.new_context(storage_state=_flow_states["e2e_invform"], locale="fa-IR")
            page = ctx.new_page()
            page.goto(f"{BASE_URL}/#invoices")
            page.wait_for_load_state("networkidle")
            if page.locator("#inv-mode-itemized").is_visible():
                page.click("#inv-mode-itemized")
            row = page.locator("#inv-items-body .inv-line").first
            row.locator(".il-qty").fill("2")
            row.locator(".il-price").fill("1500")
            grand = page.inner_text("#inv-grand")
            assert not any("\u06f0" <= ch <= "\u06f9" for ch in grand), grand      # 0–9, as the list
            assert "3,000" in grand, grand
            goods = page.evaluate("() => getComputedStyle(document.querySelector('#inv-items-body .il-sstid').closest('td')).display")
            if locale == "uk":
                assert grand.startswith("£"), grand
                assert goods == "none"
            else:
                assert goods != "none"
            ctx.close()
    finally:
        owner.evaluate(PUT, ["/admin/reporting-locale", {"locale": before}])
        octx.close()
