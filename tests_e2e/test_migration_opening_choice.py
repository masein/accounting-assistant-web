"""Opening balances already in the books (scenario O3, 2026-10-12): the
migration preview asks to keep them or replace them, keeping them when the
file's differ, and the page says what was done. The books are never touched
here (every e2e user shares one company): the preview is drawn from a made-up
answer and the confirm is caught before it reaches the server."""
from __future__ import annotations

import json
import os

from tests_e2e.conftest import ARTIFACTS, switch_language, wait_until

PREVIEW = {"token": "t-e2e", "batch_id": "00000000-0000-0000-0000-000000000000", "default_opening_date": "2026-03-21",
           "summary": {"tiers": {"group": 7, "kol": 18, "moein": 44, "tafsili": 15},
                       "tafsili_split": {"bank_accounts": 1, "counterparties": 14}, "counterparty_types": {"client": 6, "employee": 8},
                       "opening": {"basis": "moein", "total_debit": 43014416338, "total_credit": 43014416338, "balanced": True,
                                   "difference": 0, "existing": {"date": "2026-10-04", "total": 80818673909}, "suggested": "keep"},
                       "validation": {"errors": [], "warnings": []}}}
RESULT = {"batch_id": PREVIEW["batch_id"], "status": "applied", "result": {
    "chart": {"group": {"created": 0, "updated": 0}, "kol": {"created": 2, "updated": 0}, "moein": {"created": 12, "updated": 0},
              "conflicts": [{"code": "1113", "title": "سایر حساب‌ها و اسناد دریافتنی", "tier": "kol", "account": "x", "bank": "y"}]},
    "entities": {"banks_created": 0, "counterparties_created": 2},
    "opening_journal": {"kept": True, "transaction_id": "x", "opening_date": "2026-10-04", "total_debit": 80818673909}}}


def test_the_preview_asks_and_keeps_the_books_opening_by_default(flow_page):
    page, watch = flow_page("e2e_migchoice")
    switch_language(page, "fa")
    page.evaluate("() => { location.hash = 'migration'; }")
    page.wait_for_load_state("networkidle")
    page.evaluate("(b) => { _migrationToken = b.token; _migrationRenderPreview(b); }", PREVIEW)
    wait_until(page, "() => document.getElementById('migration-opening-choice')")
    choice = page.evaluate("() => [...document.querySelectorAll('input[name=migration-opening]')].map(i => [i.value, i.checked])")
    assert choice == [["keep", True], ["replace", False]], choice
    legend = page.inner_text("#migration-opening-choice legend")
    when = page.evaluate("() => formatDisplayDate('2026-10-04')")             # the company's calendar
    assert "۸۰٬۸۱۸٬۶۷۳٬۹۰۹" in legend and when in legend and "۴" in when, legend   # the books' total and date, in the page's digits
    assert "۴۳٬۰۱۴٬۴۱۶٬۳۳۸" in page.inner_text("#migration-opening-choice")
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.screenshot(path=os.path.join(ARTIFACTS, "migration-opening-choice.png"))

    sent = []
    def fulfil(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(RESULT))
    page.route("**/migration/import/confirm", fulfil)
    page.click("#migration-confirm-btn")
    wait_until(page, "() => getComputedStyle(document.getElementById('migration-result')).display !== 'none'")
    assert sent and sent[0]["opening"] == "keep" and sent[0]["token"] == "t-e2e", sent
    shown = page.inner_text("#migration-result")
    assert "مانده‌های افتتاحیه بدون تغییر ماند" in shown and when in shown, shown
    assert "۱۱۱۳" in shown and "سایر حساب‌ها و اسناد دریافتنی" in shown, shown     # left out of the chart, said
    assert watch.problems() == [], watch.problems()
