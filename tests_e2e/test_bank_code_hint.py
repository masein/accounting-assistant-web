"""A bank party's code field says what it is, and a code that isn't a bank
account is refused in the user's language instead of being replaced
(deep browser test, 2026-10-02, finding #22)."""
from __future__ import annotations

import uuid

from tests_e2e.conftest import switch_language


def test_the_bank_code_is_explained_and_checked(flow_page):
    page, watch = flow_page("e2e_bankcode")
    try:
        switch_language(page, "fa")
        page.evaluate("() => { location.hash = 'entities'; }")
        page.wait_for_load_state("networkidle")
        assert not page.locator("#entity-code-hint").is_visible()
        page.select_option("#entity-type", "bank")
        assert page.locator("#entity-code-hint").is_visible()
        assert "حساب" in page.inner_text("#entity-code-hint")
        page.fill("#entity-name", f"بانک آزمون {uuid.uuid4().hex[:4]}")
        page.fill("#entity-code", "301")
        with page.expect_response(lambda r: r.url.endswith("/entities") and r.request.method == "POST") as res:
            page.click("#entity-add")
        assert res.value.status == 422
        page.locator("#alert").wait_for(state="visible")
        assert "کد را خالی بگذارید" in page.inner_text("#alert")
        page.select_option("#entity-type", "client")
        assert not page.locator("#entity-code-hint").is_visible()
    finally:
        switch_language(page, "en")
