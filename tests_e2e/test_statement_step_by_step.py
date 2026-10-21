"""A statement's differences one at a time on the web (scenario AJ13).

The owner, 2026-10-21: «رفع قدم‌به‌قدم» gave the first difference as a card and, once it was confirmed,
nothing came next. Now the button takes the next row without the model, as the phone's Next does, and a
confirmed card brings the next one by itself; «بعدی» skips one. The browser suite has no AI provider, so
this runs as the owner's server would without credit."""
from __future__ import annotations

import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

CSV = ("Date,Description,Debit,Credit,Balance\n"
       "2026-08-02,Tuition AJ13 one,0,35000013,35000013\n"
       "2026-08-03,Tuition AJ13 two,0,27000013,62000026\n"
       "2026-08-04,Supplies AJ13,1300013,0,60700013\n"
       "2026-08-05,Bank fee AJ13,13013,0,60687000\n").encode()


def test_aj13_step_by_step_carries_on_after_each_card(flow_page):
    page, watch = flow_page("e2e_stepbystep")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    try:
        switch_language(page, "fa")
        with page.expect_response(lambda r: "/transactions/attachments" in r.url and r.request.method == "POST"):
            page.set_input_files("#ai-acct-file", files=[{"name": "statement-aj13.csv", "mimeType": "text/csv", "buffer": CSV}])
        page.fill("#ai-acct-input", "صورتحساب بانک")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
            page.click("#ai-acct-send")
        assert res.value.status == 200, res.value.text()
        expect(page.locator(".ai-stmt-account").last).to_contain_text("مقایسه با")              # AJ12: which account
        page.get_by_role("button", name="رفع قدم‌به‌قدم").last.click()
        cards = page.locator(".ai-card[data-statement-id]")
        expect(cards).to_have_count(1)
        expect(page.locator("#ai-acct-messages")).to_contain_text("پس از این، ۳ ردیف دیگر مانده.")
        cards.first.locator(".ai-card-actions button.btn-primary").click()                    # confirm the first
        expect(cards).to_have_count(2, timeout=15_000)                                        # the next one by itself
        expect(page.locator("#ai-acct-messages")).to_contain_text("پس از این، ۲ ردیف دیگر مانده.")
        expect(cards.first).to_contain_text("سند")                                            # the receipt, in Persian
        page.locator(".ai-stmt-next button").click()                                          # «بعدی» skips one
        expect(cards).to_have_count(3)
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "statement-step-by-step-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
