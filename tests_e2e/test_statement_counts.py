"""The bank statements list shows rows posted from a statement apart from rows found in the books (CF1)."""
from __future__ import annotations

import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

CSV = ("Date,Description,Debit,Credit,Balance\n"
       "2026-09-02,Counts CF1 one,0,5000017,5000017\n"
       "2026-09-03,Counts CF1 two,0,6000017,11000034\n").encode()


def test_cf1_posted_beside_matched(flow_page):
    page, watch = flow_page("e2e_bscounts")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    try:
        switch_language(page, "fa")
        with page.expect_response(lambda r: "/transactions/attachments" in r.url and r.request.method == "POST"):
            page.set_input_files("#ai-acct-file", files=[{"name": "statement-cf1.csv", "mimeType": "text/csv", "buffer": CSV}])
        page.fill("#ai-acct-input", "صورتحساب بانک")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
            page.click("#ai-acct-send")
        assert res.value.status == 200, res.value.text()
        cards = page.locator(".ai-card[data-statement-id]")
        before = cards.count()
        page.get_by_role("button", name="رفع قدم‌به‌قدم").last.click()
        expect(cards).to_have_count(before + 1)
        with page.expect_response(lambda r: "/ai-accountant/execute" in r.url):
            cards.nth(before).locator(".ai-card-actions button.btn-primary").click()      # one row posted
        page.evaluate("() => { location.hash = 'bank-statements'; }")
        row = page.locator("#bs-list-body tr", has_text="statement-cf1.csv").first
        row.wait_for(timeout=15_000)
        cells = row.locator("td")
        expect(cells.nth(3)).to_have_text("۲")                         # rows
        expect(cells.nth(4)).to_have_text("۱")                         # posted from it
        expect(page.locator("th", has_text="ثبت‌شده")).to_have_count(1)
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#bs-list-body").locator("xpath=ancestor::table").screenshot(path=os.path.join(ARTIFACTS, "statement-counts-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
