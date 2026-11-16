"""A statement's differences one at a time on the web (scenario AJ13).

The owner, 2026-10-21: «رفع قدم‌به‌قدم» gave the first difference as a card and, once it was confirmed,
nothing came next. Now the button takes the next row without the model, as the phone's Next does, and a
confirmed card brings the next one by itself; «بعدی» with a card still open names it instead of passing it (CA1). The browser suite has no AI provider, so
this runs as the owner's server would without credit."""
from __future__ import annotations

import os
import uuid

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
        # «بعدی» with the second card still open: no third card, the open one is named (CA1)
        page.locator(".ai-stmt-next button").last.click()
        expect(page.locator("#ai-acct-messages")).to_contain_text("کارت ردیف ۲ بالاتر هنوز منتظر است")
        expect(cards).to_have_count(2)
        cards.nth(1).locator(".ai-card-actions button.btn-primary").click()                   # confirm it
        expect(cards).to_have_count(3, timeout=15_000)                                        # then the next comes
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "statement-step-by-step-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


POST = r"""async ([path, body]) => { const r = await fetch(path, { method: 'POST',
  headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return [r.status, await r.json()]; }"""
PUT = POST.replace("'POST'", "'PUT'")

# Days and amounts of its own: the suite's shared books hold other tests' entries in early August, and a row near
# one of them is a row to look at, rightly kept out of a group (CI, 2026-11-16)
GROUP_CSV = ("Date,Description,Debit,Credit,Balance\n"
             "2026-10-12,Card transfer 6037-11 tuition CD1,0,4111117,4111117\n"
             "2026-10-12,Card transfer 6037-22 tuition CD1,0,4222227,8333344\n"
             "2026-10-13,Card transfer 6037-33 tuition CD1,0,4333337,12666681\n"
             "2026-10-14,Card transfer 6037-44 tuition CD1,0,4444447,17111128\n").encode()


def test_cd1_rows_alike_come_as_one_card(flow_page):
    """CD1: once the first card is confirmed, the rows like it come on one card, a voucher per day."""
    page, watch = flow_page("e2e_stepbystep")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    try:
        switch_language(page, "fa")
        with page.expect_response(lambda r: "/transactions/attachments" in r.url and r.request.method == "POST"):
            page.set_input_files("#ai-acct-file", files=[{"name": "statement-cd1.csv", "mimeType": "text/csv", "buffer": GROUP_CSV}])
        page.fill("#ai-acct-input", "صورتحساب بانک")
        with page.expect_response(lambda r: r.url.endswith("/ai-accountant/chat")) as res:
            page.click("#ai-acct-send")
        assert res.value.status == 200, res.value.text()
        # a bank account of its own: in the suite's shared books other tests put accounts under 1110, and a group
        # posts only to an account that takes entries (the row cards of AJ13 still go to 1110)
        sid = res.value.json()["intake"]["statement_id"]
        status, bank = page.evaluate(POST, ["/entities", {"type": "bank", "name": f"CD1 Bank {uuid.uuid4().hex[:6]}"}])
        assert status in (200, 201), bank
        status, out = page.evaluate(PUT, [f"/brain/bank-statements/{sid}/bank-account", {"code": bank["code"]}])
        assert status == 200, out
        cards = page.locator(".ai-card[data-statement-id]")
        before = cards.count()                                  # the conversation may hold an earlier test's cards
        page.get_by_role("button", name="رفع قدم‌به‌قدم").last.click()
        expect(cards).to_have_count(before + 1)
        cards.nth(before).locator(".ai-card-actions button.btn-primary").click()             # confirm the first
        expect(cards).to_have_count(before + 2, timeout=15_000)                               # the group comes by itself
        expect(page.locator("#ai-acct-messages")).to_contain_text("۳ ردیف دیگر مثل ردیف قبل است")
        expect(page.locator("#ai-acct-messages")).to_contain_text("پس از این کارت، ردیف دیگری نمی‌ماند.")
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "statement-group-fa.png"))
        cards.nth(before + 1).locator(".ai-card-actions button.btn-primary").click()          # confirm the group
        # every row in, on its own account the books now reach the statement's closing balance: the last word (BN4)
        expect(page.locator("#ai-acct-messages")).to_contain_text("همهٔ ردیف‌های این صورت‌حساب در دفاتر است", timeout=15_000)
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
