"""A new company's first look (scenario D13).

Its CEO Mode and dashboard drew blank boxes that looked broken: a plugin
registered once now writes «No data yet» in the page's language on any chart
whose datasets are empty or all zero, and leaves a chart with any figure as
it was. And with nothing booked yet, the reports' currency fell back to the
server's "most common" default, IRR: a new UK company's dashboard said 0 IRR."""
from __future__ import annotations

from tests_e2e.conftest import BASE_URL, PageWatch, _flow_states, _log_in, switch_language, wait_until

DRAW = r"""(rows) => rows.map(([type, data]) => {
  const c = document.createElement('canvas'); c.width = 300; c.height = 150; document.body.appendChild(c);
  const chart = new Chart(c, { type, data: { labels: data.map((_, i) => 'x' + i), datasets: [{ data }] },
                                options: { animation: false, responsive: false } });
  const said = chart.$empty; chart.destroy(); c.remove(); return said; })"""
CEO = ["ceo-trend-chart", "ceo-profit-chart", "ceo-expense-chart", "ceo-balance-chart"]


def _page(browser, username="e2e_owner"):
    if username not in _flow_states:
        _flow_states[username] = _log_in(browser, username)
    ctx = browser.new_context(storage_state=_flow_states[username], viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    watch = PageWatch(page)
    page.goto(f"{BASE_URL}/")
    page.wait_for_load_state("networkidle")
    return ctx, page, watch


def test_a_chart_with_nothing_to_show_says_so(browser):
    ctx, page, watch = _page(browser)
    try:
        # empty, all zero and an empty doughnut say so; any figure draws as before
        got = page.evaluate(DRAW, [["bar", []], ["bar", [0, 0]], ["doughnut", []], ["line", [0, 0, 0]],
                                   ["bar", [0, 5]], ["line", [3]], ["doughnut", [2, 1]]])
        assert got == [True, True, True, True, False, False, False]

        # in the page's language
        switch_language(page, "fa")
        assert page.evaluate("() => t('noDataYet')") == "هنوز داده‌ای وجود ندارد."

        # CEO Mode's charts all go through it
        page.click('.nav-btn[data-page="ceo"]')
        wait_until(page, "(ids) => ids.every(id => typeof Chart.getChart(document.getElementById(id))?.$empty === 'boolean')",
                   CEO, timeout_ms=10_000)
        assert watch.problems() == []
    finally:
        switch_language(page, "en")
        ctx.close()


PICK = r"""(meta) => { const sel = document.getElementById('mgr-currency'); const was = [sel.value, sel.dataset.defaulted];
  delete sel.dataset.defaulted; applyReportCurrencyDefault(meta); const got = sel.value;
  sel.value = was[0]; if (was[1]) sel.dataset.defaulted = was[1]; return got; }"""


def test_with_nothing_booked_the_reports_use_the_company_currency(browser):
    ctx, page, watch = _page(browser)
    try:
        empty_uk = {"reporting_currency": "GBP", "most_common_currency": "IRR", "used_currencies": []}
        assert page.evaluate(PICK, empty_uk) == "GBP"                       # it was IRR
        assert page.evaluate(PICK, {**empty_uk, "used_currencies": ["GBP"], "most_common_currency": "GBP"}) == "GBP"
        assert page.evaluate(PICK, {**empty_uk, "used_currencies": ["GBP", "USD"]}) == "ALL"
        assert page.evaluate(PICK, {"reporting_currency": "IRR", "most_common_currency": "IRR", "used_currencies": []}) == "IRR"
        assert watch.problems() == []
    finally:
        ctx.close()
