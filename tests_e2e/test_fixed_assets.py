"""The fixed-asset page: add an asset, preview the month-end run, open its
schedule — under the strict CSP without an error."""
from __future__ import annotations

import os
from datetime import date, timedelta

from tests_e2e.conftest import ARTIFACTS


def test_add_an_asset_and_preview_depreciation(flow_page):
    page, watch = flow_page("e2e_assets")
    page.locator('.nav-btn[data-page="fixed-assets"]').first.click()
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector("#asset-category option", state="attached")   # options are never "visible"
        page.select_option("#asset-category", "other")
        page.fill("#asset-name", "E2E laptop")
        page.fill("#asset-cost", "3600000")
        page.fill("#asset-life", "36")
        page.fill("#asset-acquired", (date.today() - timedelta(days=150)).isoformat())
        page.click("#asset-save")
        try:
            page.wait_for_selector("#asset-register table tbody tr", timeout=10_000)
        except Exception as exc:                            # say why the save didn't land
            raise AssertionError(f"no register row; form says: {page.inner_text('#asset-save-msg')!r}; "
                                 f"problems: {watch.problems()}") from exc
        assert "E2E laptop" in page.locator("#asset-register").inner_text()

        page.click("#asset-run-preview")
        page.wait_for_selector("#asset-run-result .fc-note")
        assert page.locator("#asset-run-result").inner_text().strip()

        page.locator("#asset-register .asset-open").first.click()
        page.wait_for_selector("#asset-detail h3")
        assert page.locator("#asset-detail details").count() == 1
        page.click("#asset-detail-close")
        assert not page.locator("#asset-detail").is_visible()
        assert watch.problems() == [], watch.problems()
    except AssertionError:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "fixed-assets.png"), full_page=True)
        raise
