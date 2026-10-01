"""Every table on every page is drawn as a table: header cells with padding,
a border and a header background. The .data-table class (Time, Expenses,
Purchase orders, Payroll, Equity, the invoice tax rates) had no CSS rules at
all, so those fifteen tables showed a bare grid of text — "PO # Supplier Order
date Total Status" ran together on one line."""
from __future__ import annotations

import os

from tests_e2e.conftest import ARTIFACTS
from tests_e2e.test_mobile_layout import PAGES

BARE = r"""() => [...document.querySelectorAll('.card[data-page] table')]
  .filter(t => t.offsetParent !== null && t.querySelector('th'))
  .filter(t => { const th = getComputedStyle(t.querySelector('th'));
                 return parseFloat(th.paddingLeft) < 4 || parseFloat(th.borderBottomWidth) < 1; })
  .map(t => (t.id || t.className || 'table') + ': ' + [...t.querySelectorAll('th')].map(h => h.textContent.trim()).join(' | ').slice(0, 60))"""


def test_every_table_is_styled(flow_page):
    page, watch = flow_page("e2e_tables")
    try:
        bare = {}
        for name in PAGES:
            page.evaluate("(p) => { location.hash = p; }", name)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(250)
            page.evaluate("() => document.querySelectorAll('.card[data-page] details').forEach(d => { d.open = true; })")
            found = page.evaluate(BARE)
            if found:
                bare[name] = found
        assert bare == {}, bare
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "table-style.png"), full_page=True)
        raise
