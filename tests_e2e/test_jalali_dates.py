"""With the Jalali calendar chosen, no page shows a Gregorian date: the cash
forecast's weeks, invoice and Moadian dates, pay runs and rule sets, time
entries, claims, purchase orders, tax rates, statement rows, recurring and
reminder dates, the MTD deadlines all printed 2026-09-28 as it came."""
from __future__ import annotations

import os
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS, BASE_URL, _flow_states, _log_in
from tests_e2e.test_mobile_layout import PAGES

GREGORIAN = r"""() => [...document.querySelectorAll('.card[data-page] *')]
  .filter(e => e.offsetParent !== null && !e.closest('input, textarea, select, pre, code, script, bdi[dir="ltr"]'))
  .map(e => [e, [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ')])
  .filter(([, t]) => /\b20\d\d-\d\d-\d\d\b/.test(t))
  .map(([e, t]) => e.tagName.toLowerCase() + ' «' + t.trim().slice(0, 50) + '»')"""
POST = r"""async ([path, body]) => { const r = await fetch(path, { method: path.includes('calendar') ? 'PUT' : 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.text()]; }"""


def _owner(browser):
    if "e2e_owner" not in _flow_states:
        _flow_states["e2e_owner"] = _log_in(browser, "e2e_owner")
    ctx = browser.new_context(storage_state=_flow_states["e2e_owner"])
    page = ctx.new_page()
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    return ctx, page


def test_a_jalali_company_sees_no_gregorian_date(browser, flow_page):
    octx, owner = _owner(browser)
    try:
        assert owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "jalali"}])[0] == 200
        page, watch = flow_page("e2e_jalali")
        today = date.today()
        status, body = page.evaluate(POST, ["/invoices", {"number": "JAL-1", "kind": "sales", "status": "issued", "amount": 1000,
                                                          "issue_date": (today - timedelta(days=40)).isoformat(),
                                                          "due_date": (today - timedelta(days=10)).isoformat()}])
        assert status in (201, 409), body
        page.reload()
        page.wait_for_load_state("networkidle")
        found = {}
        for name in PAGES:
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(300)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            hits = page.evaluate(GREGORIAN)
            if hits:
                found[name] = hits
        try:
            assert found == {}, found
            assert watch.problems() == [], watch.problems()
        except Exception:
            os.makedirs(ARTIFACTS, exist_ok=True)
            page.screenshot(path=os.path.join(ARTIFACTS, "jalali-dates.png"), full_page=True)
            raise
    finally:
        owner.evaluate(POST, ["/admin/display-calendar", {"calendar": "gregorian"}])
        octx.close()
