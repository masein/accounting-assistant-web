"""A bank turnover posted by the user's rules, on the web (scenarios AF1–AF3), under the strict CSP.

The reply the real model gave on the school's statement (2026-10-18), served by the test: the statement
imported (its card), the assistant's sentence, and one card for the whole statement, its lines what each
account receives in all."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

TOKEN = "7f1c2a9e-0000-4000-8000-0000000af001"
CARD = {"title": "صورتحساب پارسیان: ۹۵ ردیف در ۴۳ سند", "amount": {"value": 23045354368, "currency": "IRR"},
        "date": {"iso": "2026-06-22", "display": "۱ تیر ۱۴۰۵"},
        "lines": [{"account": "111001", "name": "بانک دی حساب 0105222028000", "debit": 5144811663, "credit": 0},
                  {"account": "611201", "name": "هزینه های تنخواه طی ماه", "debit": 17898395405, "credit": 0},
                  {"account": "611202", "name": "کارمزد های بانکی طی ماه", "debit": 2147300, "credit": 0},
                  {"account": "111001", "name": "بانک دی حساب 0105222028000", "debit": 0, "credit": 17900542705},
                  {"account": "411001", "name": "درآمد شهریه", "debit": 0, "credit": 5144811663}]}
INTAKE = {"kind": "bank_statement", "status": "imported", "statement_id": "88131976-ce3b-4022-b1bb-266e0eb6ded1",
          "bank_name": "Parsian", "bank_label": "پارسیان", "file_name": "تنخواه پارسیان 3 ماهه دوم 1405.xlsx",
          "total_rows": 95, "from_date": "2026-06-22", "to_date": "2026-09-22", "currency": "IRR",
          "counts": {"matched": 0, "unrecorded": 95, "needs_confirmation": 0, "amount_mismatch": 0, "missing_in_bank": 0},
          "balance": {"statement_closing": 3910384886, "book_balance": 0, "gap": 3910384886}}
REPLY = {"session_id": "s-af", "turns": 2, "stop_reason": "end_turn", "tool_calls": [], "intake": INTAKE,
         "text": "کارت آماده است: ۹۵ ردیف صورتحساب پارسیان، به‌تفکیک تاریخ در ۴۳ سند از ۱ تیر ۱۴۰۵ تا ۳۱ شهریور ۱۴۰۵.",
         "proposals": [{"confirmation_token": TOKEN, "tool_name": "propose_post_statement",
                        "summary": "Post 95 rows of the Parsian statement as 43 voucher(s)", "card": CARD,
                        "preview": {"description": CARD["title"], "vouchers": 43}, "expires_at": None}]}


def test_the_statement_and_one_card_for_it(flow_page):
    page, watch = flow_page("e2e_stmtrules")
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(REPLY, ensure_ascii=False)))
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    try:
        switch_language(page, "fa")
        page.fill("#ai-acct-input", "از فایل گردش ۳ ماهه دوم ۱۴۰۵ مبالغی که علامت + دارند در بدهکار بانک دی …")
        page.click("#ai-acct-send")
        card = page.locator(f'.ai-card[data-token="{TOKEN}"]')
        expect(card).to_be_visible()
        expect(card.locator(".ai-voucher-what")).to_contain_text("صورتحساب پارسیان: ۹۵ ردیف در ۴۳ سند")
        expect(card.locator(".ai-voucher-line")).to_have_count(5)
        expect(page.locator("#ai-acct-messages")).to_contain_text("۹۵")              # the statement's own card
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-statement-rules-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_a_new_bank_on_the_card(flow_page):
    """AF9: the file really is Parsian's — the card's bank line is the bank it makes on confirm (the real
    model's card, 2026-10-18)."""
    page, watch = flow_page("e2e_stmtrules")
    new = "بانک پارسیان (تازه)"
    lines = [{**ln, "account": "111002", "name": new} if ln["account"] == "111001" else ln for ln in CARD["lines"]]
    reply = {**REPLY, "text": "کارت آماده است: سمت بانکی روی حساب جدید «بانک پارسیان» (۱۱۱۰۰۲) که با تأیید همین کارت ساخته می‌شود.",
             "proposals": [{**REPLY["proposals"][0], "card": {**CARD, "lines": lines},
                            "preview": {**REPLY["proposals"][0]["preview"], "new_bank_name": "بانک پارسیان",
                                        "account_code": "111002", "place": new}}]}
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(reply, ensure_ascii=False)))
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    try:
        switch_language(page, "fa")
        page.fill("#ai-acct-input", "ساخت حساب جدید «بانک پارسیان»")
        page.click("#ai-acct-send")
        card = page.locator(f'.ai-card[data-token="{TOKEN}"]')
        expect(card).to_be_visible()
        expect(card.locator(".ai-voucher-line").filter(has_text=new)).to_have_count(2)   # Dr in, Cr out
        expect(card).not_to_contain_text("بانک دی")
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-statement-new-bank-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
