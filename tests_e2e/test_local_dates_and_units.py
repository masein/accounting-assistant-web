"""Defaults are today where the user is, and amounts name their unit.

* "Today" came from new Date().toISOString(): the date in UTC — from midnight
  to 03:30 in Tehran a new voucher was dated yesterday — and a due date from a
  client's payment terms was a day early east of Greenwich.
* A purchase order's required order date started empty.
* The equity page said amounts were "in the smallest currency unit (e.g.
  Rials)", which to a UK company read as pence."""
from __future__ import annotations

from datetime import datetime, timezone

from tests_e2e.conftest import BASE_URL, PageWatch, _flow_states, _log_in, switch_language, wait_until

API = r"""async ([method, path, body]) => { const r = await fetch(path, { method,
  headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return [r.status, await r.json().catch(() => null)]; }"""
# 00:45 on 2 October in Tehran (UTC+03:30) — still 1 October in UTC
TEHRAN_NIGHT = datetime(2026, 10, 1, 21, 15, tzinfo=timezone.utc)


def test_in_tehran_after_midnight_today_is_the_new_day(browser):
    if "e2e_units" not in _flow_states:
        _flow_states["e2e_units"] = _log_in(browser, "e2e_units")
    ctx = browser.new_context(storage_state=_flow_states["e2e_units"], timezone_id="Asia/Tehran",
                              viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.clock.set_fixed_time(TEHRAN_NIGHT)
    watch = PageWatch(page)
    try:
        page.goto(f"{BASE_URL}/")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => new Date().toISOString().slice(0, 10)") == "2026-10-01"   # what it used
        assert page.evaluate("() => localIsoDate(new Date())") == "2026-10-02"
        page.click('.nav-btn[data-page="transactions"]')
        assert page.input_value("#date") == "2026-10-02"

        # a client on "Net 30": issued 1 October, due 31 October — not the 30th
        status, ent = page.evaluate(API, ["POST", "/entities", {"type": "client", "name": "Net Thirty Co", "payment_terms": "Net 30"}])
        assert status in (200, 201), ent
        page.click('.nav-btn[data-page="invoices"]')
        page.fill("#inv-issue", "2026-10-01")
        wait_until(page, "(id) => [...document.getElementById('inv-entity').options].some(o => o.value === id)", ent["id"])
        with page.expect_response(lambda r: r.url.endswith(f"/entities/{ent['id']}")):
            page.select_option("#inv-entity", ent["id"])
        wait_until(page, "() => document.getElementById('inv-due').value === '2026-10-31'")

        # a purchase order's order date starts at today, here
        page.click('.nav-btn[data-page="purchase-orders"]')
        wait_until(page, "() => document.getElementById('po-order-date').value === '2026-10-02'")
        assert watch.problems() == [], watch.problems()
    finally:
        ctx.close()


def test_the_equity_page_names_the_companys_currency(flow_page):
    page, watch = flow_page("e2e_units_2")
    try:
        switch_language(page, "fa")
        page.click('.nav-btn[data-page="equity"]')
        wait_until(page, "() => !document.getElementById('equity-amount-hint').textContent.includes('{')")
        said = page.inner_text("#equity-amount-hint")
        ccy = page.evaluate("() => baseCurrencyCode()")
        assert ccy in said and "کوچک‌ترین" not in said, said        # not "the smallest unit"
        if ccy != "IRR":
            assert "ریال" not in said, said
        assert watch.problems() == [], watch.problems()
    finally:
        try:
            switch_language(page, "en")
        except Exception:
            pass
