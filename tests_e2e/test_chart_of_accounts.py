"""Chart of accounts page: add an account under a parent (the code is
suggested), find it in the tree and deactivate it — under the strict CSP."""
from __future__ import annotations

import os
import re
import uuid

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS


def test_add_and_deactivate_an_account(flow_page):
    page, watch = flow_page("e2e_chart")
    page.locator('.nav-btn[data-page="accounts"]').first.click()
    page.wait_for_load_state("networkidle")
    name = f"E2E bank {uuid.uuid4().hex[:6]}"
    try:
        page.wait_for_selector("#coa-tree table")
        page.fill("#coa-new-parent", "1110")
        page.dispatch_event("#coa-new-parent", "change")
        # (wait_for_function evaluates a string: the strict CSP refuses that)
        expect(page.locator("#coa-new-code")).to_have_value(re.compile(r"^1110\d+$"))
        page.fill("#coa-new-name", name)
        page.click("#coa-new-save")
        page.wait_for_selector(f"#coa-tree td:has-text('{name}')")
        row = page.locator("#coa-tree tr", has_text=name)
        assert row.locator("button[data-coa='off']").is_hidden()        # under ⋯, not on the row (#12)
        row.locator("details.row-menu summary").click()
        row.locator("button[data-coa='off']").click()
        page.wait_for_selector(f"#coa-tree td:has-text('{name}')", state="detached")   # hidden once inactive
        page.check("#coa-show-inactive")
        page.wait_for_selector(f"#coa-tree tr:has-text('{name}') .alert-chip")
        assert page.locator("#coa-opening-grid table").count() == 1
        assert watch.problems() == [], watch.problems()
    except Exception:
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, "chart-of-accounts.png"), full_page=True)
        raise
