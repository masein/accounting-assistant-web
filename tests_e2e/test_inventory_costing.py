"""Inventory page: add an item with a barcode and a reorder point, record a
receipt, and see it valued in the stock panel — under the strict CSP."""
from __future__ import annotations

import os
import uuid

from tests_e2e.conftest import ARTIFACTS


def test_stock_panel_values_a_receipt(flow_page):
    page, watch = flow_page("e2e_stock")
    page.locator('.nav-btn[data-page="inventory"]').first.click()
    page.wait_for_load_state("networkidle")
    name = f"E2E tea {uuid.uuid4().hex[:6]}"
    try:
        page.fill("#mgr-inv-item-name", name)
        page.fill("#mgr-inv-item-barcode", uuid.uuid4().hex[:12])
        page.fill("#mgr-inv-item-reorder", "5")
        page.click("#mgr-add-item-btn")
        page.wait_for_selector(f"#mgr-mv-item option:has-text('{name}')", state="attached")
        page.select_option("#mgr-mv-item", label=name)
        page.fill("#mgr-mv-qty", "3")
        page.fill("#mgr-mv-cost", "1000")
        page.click("#mgr-add-mv-btn")
        page.wait_for_load_state("networkidle")
        page.click("#stock-refresh")
        page.wait_for_selector(f"#stock-table td:has-text('{name}')")
        row = page.locator("#stock-table tr", has_text=name)
        assert "3" in row.inner_text() and row.locator(".alert-chip").count() == 1    # 3 ≤ 5: reorder
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "inventory-costing.png"), full_page=True)
        raise
