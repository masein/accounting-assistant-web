"""My finances, each currency on its own (scenarios AQ1, AQ2, docs/PLAN_CURRENCIES.md phase 2), under the strict CSP.

Books in rials with Omani rials in a wallet: a row for each currency, and the details in the one chosen.
The figures are the server's own for those books (tests/test_finances_by_currency.py), recorded in
fixtures/finances_currencies.json."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

DATA = json.loads((Path(__file__).parent / "fixtures" / "finances_currencies.json").read_text(encoding="utf-8"))


def _by_currency(prefix):
    def serve(route):
        ccy = (parse_qs(urlparse(route.request.url).query).get("currency") or ["IRR"])[0]
        if f"{prefix}_{ccy}" not in DATA:            # the company's own dashboard, opened before the page
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(DATA[f"{prefix}_{ccy}"], ensure_ascii=False))
    return serve


def test_a_row_for_each_currency_and_the_details_in_one(flow_page):
    page, watch = flow_page("e2e_finances")
    page.route("**/personal/currencies", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(DATA["currencies"], ensure_ascii=False)))
    page.route("**/reports/owner-dashboard*", _by_currency("dashboard"))
    page.route("**/personal/report-card*", _by_currency("card"))
    try:
        switch_language(page, "fa")
        page.evaluate("() => { currentRole = 'personal'; location.hash = 'personal-dashboard'; }")
        page.wait_for_load_state("networkidle")
        rows = page.locator("#pd-ccy-rows .pd-ccy-row")
        expect(rows).to_have_count(2)
        expect(rows.nth(0).locator(".pd-ccy-name")).to_have_text("ریال ایران")
        expect(rows.nth(1).locator(".pd-ccy-name")).to_have_text("ریال عمان")
        expect(rows.nth(1).locator(".kpi-card .value").first).to_have_text("۵۸٫۵۰۰ OMR")
        expect(page.locator("#pd-kpi-grid")).to_be_hidden()
        pick = page.locator("#pd-ccy-pick .pd-ccy-btn")
        expect(pick).to_have_count(2)
        expect(pick.nth(0)).to_have_attribute("aria-pressed", "true")
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("[data-page='personal-dashboard'] h2").scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(ARTIFACTS, "finances-currencies-fa.png"))
        pick.nth(1).click()                                         # the details in Omani rials
        expect(pick.nth(1)).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#pd-cat-wrap")).to_contain_text("۲۰٫۲۵۰")
        expect(page.locator("#rc-figures")).to_contain_text("۴۱٫۵۰۰")
        page.locator("#pd-report-card").screenshot(path=os.path.join(ARTIFACTS, "finances-report-card-omr-fa.png"))
        page.locator("#pd-cat-wrap").scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        page.locator("#pd-cat-wrap").locator("xpath=..").screenshot(path=os.path.join(ARTIFACTS, "finances-categories-omr-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_on_a_phone_sized_screen(flow_page):
    page, watch = flow_page("e2e_finances")
    page.set_viewport_size({"width": 400, "height": 900})
    page.route("**/personal/currencies", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(DATA["currencies"], ensure_ascii=False)))
    page.route("**/reports/owner-dashboard*", _by_currency("dashboard"))
    page.route("**/personal/report-card*", _by_currency("card"))
    page.evaluate("() => { currentRole = 'personal'; location.hash = 'personal-dashboard'; }")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#pd-ccy-rows .pd-ccy-row")).to_have_count(2)
    wide = page.evaluate("""() => [...document.querySelectorAll('[data-page="personal-dashboard"] *')]
        .filter(e => e.offsetParent && e.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 6).map(e => (e.id || e.className || e.tagName) + ':' + Math.round(e.getBoundingClientRect().right))""")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), wide   # no sideways scroll
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#pd-ccy-rows").screenshot(path=os.path.join(ARTIFACTS, "finances-currencies-en-narrow.png"))
    assert watch.problems() == [], watch.problems()
