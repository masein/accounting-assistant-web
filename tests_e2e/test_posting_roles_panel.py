"""Where new entries post (scenario O7, 2026-10-12): the migration page lists
each kind of entry with the account it posts to, in the page's language, and
saves only what the owner changed. The shared e2e company's roles are never
changed here: the save is caught before it reaches the server."""
from __future__ import annotations

import json
import os

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until


def test_the_migration_page_shows_and_saves_where_entries_post(flow_page):
    page, watch = flow_page("e2e_roles")
    switch_language(page, "fa")
    page.evaluate("() => { location.hash = 'migration'; }")
    page.wait_for_load_state("networkidle")
    # 21 roles, plus stock, cost of goods sold and stock adjustments (docs/PLAN_INVENTORY_GL.md)
    wait_until(page, "() => document.querySelectorAll('#migration-roles select[data-role]').length === 24", timeout_ms=8000)
    rows = page.evaluate("""() => [...document.querySelectorAll('#migration-roles tr')].map(tr =>
        [tr.querySelector('td').innerText.trim(), tr.querySelector('select').value])""")
    labels = dict(rows)
    assert "حساب‌های دریافتنی" in labels and "حقوق پرداختنی" in labels and "تنخواه گردان" in labels, rows
    assert "موجودی کالا" in labels and "بهای تمام‌شده کالای فروش‌رفته" in labels, rows
    assert labels["حساب‌های دریافتنی"] == ""                       # the seeded chart: its default, nothing chosen
    first = page.inner_text("#migration-roles select[data-role=ar] option:first-child")
    default = page.evaluate("async () => localCode((await (await fetch('/accounts/posting-roles')).json()).find(r => r.category === 'ar').default_code)")
    assert "پیش‌فرض کدینگ" in first and default in first and not any(c in "0123456789" for c in first), first   # which default, in the page's digits

    sent = []
    def fulfil(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps([]))
    page.route("**/accounts/posting-roles", lambda route: fulfil(route) if route.request.method == "PUT" else route.continue_())
    page.select_option("#migration-roles select[data-role=petty_cash]", "1110")
    page.click("[data-action=migration-roles-save]")
    wait_until(page, "() => true")
    page.wait_for_timeout(500)
    assert sent == [{"roles": {"petty_cash": "1110"}}], sent        # only what changed
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#migration-roles").screenshot(path=os.path.join(ARTIFACTS, "posting-roles-panel.png"))
    assert watch.problems() == [], watch.problems()
