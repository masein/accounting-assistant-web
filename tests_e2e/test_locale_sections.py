"""An Iranian company sees Iran's tax and payroll, a UK company the UK's: a UK
company was offered the Moadian panel, both were offered each other's VAT
codes, and a UK rule set listed Iran's allowances at "0 GBP" (deep browser
test, 2026-10-02, finding #20)."""
from __future__ import annotations

from tests_e2e.conftest import wait_until
from tests_e2e.test_jalali_dates import _owner

PUT = r"""async ([path, body]) => { const r = await fetch(path, { method: 'PUT',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.text()]; }"""
SEEN = r"""() => {
  const shown = (id) => { const el = document.getElementById(id); return !!el && getComputedStyle(el).display !== 'none'; };
  const codes = [...new Set([...document.querySelectorAll('#tr-list-body tr td:first-child')].map(td => td.textContent.trim()))];
  return { moadian: shown('moadian-panel'), ttms: shown('ttms-panel'), mtd: shown('mtd-panel'), codes,
           placeholder: document.getElementById('tr-code').placeholder }; }"""


def _visit(page, name):
    page.evaluate("(p) => { location.hash = p; }", name)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def test_each_country_sees_its_own_tax_and_payroll(browser, flow_page):
    octx, owner = _owner(browser)
    before = owner.evaluate("async () => (await (await fetch('/admin/reporting-locale')).json()).locale")
    try:
        for locale, own, other in (("uk", "UK_", "IR_"), ("ir", "IR_", "UK_")):
            assert owner.evaluate(PUT, ["/admin/reporting-locale", {"locale": locale}])[0] == 200
            page, watch = flow_page("e2e_locale")
            _visit(page, "invoices")
            wait_until(page, "() => document.querySelectorAll('#tr-list-body tr td').length > 1")
            seen = page.evaluate(SEEN)
            assert seen["moadian"] == (locale == "ir") and seen["ttms"] == (locale == "ir"), seen
            assert seen["mtd"] == (locale == "uk"), seen
            assert seen["codes"] and all(not c.startswith(other) for c in seen["codes"]), seen
            assert any(c.startswith(own) for c in seen["codes"]) and seen["placeholder"].startswith(own), seen
            _visit(page, "payroll")
            rules = page.inner_text("#pr-rules-summary")
            if locale == "uk":
                assert rules and " 0 GBP" not in rules and "Housing" not in rules, rules
            else:
                assert "Housing" in rules, rules
            assert watch.problems() == [], watch.problems()
    finally:
        owner.evaluate(PUT, ["/admin/reporting-locale", {"locale": before}])
        octx.close()
