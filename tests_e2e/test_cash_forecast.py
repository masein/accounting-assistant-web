"""The dashboard's cash forecast explorer: opens, lists the weeks, and runs a
what-if (a one-off payment) under the strict CSP without an error."""
from __future__ import annotations

import os
import re
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS


def test_forecast_explorer_runs_a_scenario(flow_page):
    page, watch = flow_page("e2e_forecast")
    page.locator('.nav-btn[data-page="dashboard"]').first.click()
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector("#forecast-summary:not(:empty)")
        page.click("#forecast-explorer > summary")
        page.wait_for_selector("#fc-whatif")
        assert page.locator("#forecast-explorer-body .fc-week").count() == 13

        page.click("#fc-whatif button[type=submit]")             # nothing chosen: says so
        assert page.locator("#fc-result .fc-note").is_visible()

        page.fill("#fc-whatif input[name=oneoff_amount]", "1000000")
        page.fill("#fc-whatif input[name=oneoff_date]", (date.today() + timedelta(days=10)).isoformat())
        page.fill("#fc-whatif input[name=oneoff_label]", "New machine")
        page.click("#fc-whatif button[type=submit]")
        page.wait_for_selector("#fc-result table.mini-table")
        rows = page.locator("#fc-result table.mini-table").nth(1).locator("tbody tr")
        assert rows.count() == 13
        # after 13 weeks the difference is the one-off, paid out
        diff = page.locator("#fc-result table.mini-table").first.locator("tbody tr").nth(1).locator("td").nth(3)
        assert re.sub(r"\D", "", diff.inner_text()) == "1000000"

        page.click("#fc-whatif button[type=reset]")
        assert page.locator("#fc-result").inner_text().strip() == ""
        assert watch.problems() == [], watch.problems()
    except AssertionError:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "cash-forecast.png"), full_page=True)
        raise


def test_the_forecast_weeks_stay_on_one_line(flow_page):
    """The week column broke its dates in two ("2026-09-" / "28"; in Persian
    the end was cut: "1405/07/0") — deep browser test, 2026-10-02, #37."""
    page, watch = flow_page("e2e_forecast")
    page.evaluate("() => { location.hash = 'dashboard'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#forecast-wrap td.date-cell")
    lines = page.evaluate("""() => [...document.querySelectorAll('#forecast-wrap td.date-cell')].map(td => {
        const s = getComputedStyle(td);
        return Math.round((td.clientHeight - parseFloat(s.paddingTop) - parseFloat(s.paddingBottom)) / parseFloat(s.lineHeight || 16)); })""")
    assert len(lines) == 13 and set(lines) == {1}, lines
