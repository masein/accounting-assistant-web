"""A person's draft card on the web, as the phone draws it (scenarios AD1–AD5), under the strict CSP.

The reply carries each voucher's title, date, amount and lines (the server's
side is tests/test_web_voucher_card.py). In a person's books the page draws
the card itself, its lines To and From; a company's card keeps the summary.
The chat's replies are served by the test, so the page is told it holds a
person's books (``currentRole``) as signing in to personal books would."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

TOKEN = "7f1c2a9e-0000-4000-8000-0000000ca4d5"
SUMMARY = ("Proposed journal entry on 2026-10-16 (OMR):\n  کیف پول سبز — موجودی اولیه\n"
           "  DR 112001 کیف پول سبز: 77.5\n  CR 3110 خالص دارایی اولیه: 77.5\n  Total: 77.5 OMR")
CARD = {"title": "کیف پول سبز — موجودی اولیه", "amount": {"value": 77.5, "currency": "OMR"},
        "date": {"iso": "2026-10-16", "display": "۲۴ مهر ۱۴۰۵"},
        "lines": [{"account": "112001", "name": "کیف پول سبز", "debit": 77.5, "credit": 0},
                  {"account": "3110", "name": "خالص دارایی اولیه", "debit": 0, "credit": 77.5}]}
REPLY = {"session_id": "s-cards", "text": "کارت آماده است.", "turns": 2, "stop_reason": "end_turn", "tool_calls": [],
         "proposals": [{"confirmation_token": TOKEN, "tool_name": "propose_money_place", "summary": SUMMARY,
                        "preview": {"description": "کیف پول سبز — موجودی اولیه"}, "expires_at": None, "card": CARD}]}


def _ask(page):
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(REPLY, ensure_ascii=False)))
    page.fill("#ai-acct-input", "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")
    page.click("#ai-acct-send")
    card = page.locator(f'.ai-card[data-token="{TOKEN}"]')
    expect(card).to_be_visible()
    return card


def _open_chat(page):
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")


def test_a_persons_card_is_the_card_itself_to_and_from(flow_page):
    page, watch = flow_page("e2e_cards")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _open_chat(page)
    try:
        switch_language(page, "fa")
        page.evaluate("() => { currentRole = 'personal'; }")
        card = _ask(page)
        expect(card.locator(".ai-card-summary")).to_have_count(0)                  # no DR, no CR
        expect(card.locator(".ai-voucher-amount")).to_contain_text("OMR")
        expect(card.locator(".ai-voucher-what")).to_have_text("کیف پول سبز — موجودی اولیه، ۲۴ مهر ۱۴۰۵")
        lines = card.locator(".ai-voucher-line")
        expect(lines).to_have_count(2)
        expect(lines.nth(0)).to_contain_text("۱۱۲۰۰۱")
        expect(lines.nth(0).locator(".ai-voucher-side")).to_have_text("به ۷۷٫۵۰۰")
        expect(lines.nth(1).locator(".ai-voucher-side")).to_have_text("از ۷۷٫۵۰۰")
        assert "DR" not in card.inner_text() and "CR" not in card.inner_text()
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator(".ai-chat").screenshot(path=os.path.join(ARTIFACTS, "chat-personal-card-fa.png"))
        # AD3: copied as it reads
        card.hover()
        card.get_by_role("button", name="کپی").click()
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.splitlines()[0] == "کیف پول سبز — موجودی اولیه، ۲۴ مهر ۱۴۰۵", copied
        assert "۱۱۲۰۰۱ کیف پول سبز  به ۷۷٫۵۰۰" in copied and "DR" not in copied, copied
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")                    # and wait until it is saved: the next test is in English


def test_a_companys_card_is_the_card_too_dr_and_cr(flow_page):
    """AE3 (it was AD5, the summary): a company's card is the card itself, its lines Dr and Cr."""
    page, watch = flow_page("e2e_cards")
    _open_chat(page)
    card = _ask(page)
    expect(card.locator(".ai-card-summary")).to_have_count(0)
    lines = card.locator(".ai-voucher-line")
    expect(lines.nth(0).locator(".ai-voucher-side")).to_have_text("Dr 77.500")
    expect(lines.nth(1).locator(".ai-voucher-side")).to_have_text("Cr 77.500")
    assert "Proposed journal entry" not in card.inner_text()
    assert watch.problems() == [], watch.problems()


def _stored(token, state, **extra):
    return {"confirmation_token": token, "tool_name": "propose_money_place", "summary": SUMMARY,
            "preview": {"description": CARD["title"]}, "card": CARD, "state": state, "approval_status": None,
            "posted": None, "new_entities": [], **extra}


HISTORY = [
    {"id": "m1", "role": "user", "content": {"role": "user", "text": "کیف پول‌ها"}, "created_at": "2026-10-16T09:00:00+00:00", "cards": []},
    {"id": "m2", "role": "assistant", "content": {"role": "assistant", "text": "چهار کارت."}, "created_at": "2026-10-16T09:00:05+00:00",
     "cards": [_stored("7f1c2a9e-0000-4000-8000-0000000000a1", "executed",
                       posted={"voucher": "JE-0042", "date": {"iso": "2026-10-16", "display": "۲۴ مهر ۱۴۰۵"}, "undone": False}),
               _stored("7f1c2a9e-0000-4000-8000-0000000000a2", "cancelled"),
               _stored("7f1c2a9e-0000-4000-8000-0000000000a3", "expired"),
               _stored("7f1c2a9e-0000-4000-8000-0000000000a4", "pending")]},
]


def test_a_conversation_opened_again_keeps_its_cards(flow_page):
    """AE1, AE2: the cards come back under their answer, each as it stands."""
    page, watch = flow_page("e2e_cards")
    sessions = [{"id": "s-hist", "title": "کیف پول‌ها", "created_at": "2026-10-16T09:00:00+00:00",
                 "updated_at": "2026-10-16T09:00:05+00:00", "message_count": 2}]
    page.route("**/ai-accountant/sessions", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(sessions, ensure_ascii=False)))
    page.route("**/ai-accountant/sessions/s-hist/messages", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(HISTORY, ensure_ascii=False)))
    try:
        switch_language(page, "fa")
        page.evaluate("() => { currentRole = 'personal'; }")
        _open_chat(page)
        cards = page.locator("#ai-acct-messages .ai-card")
        expect(cards).to_have_count(4)
        posted, cancelled, expired, pending = (cards.nth(i) for i in range(4))
        expect(posted.locator(".ai-card-posted")).to_have_text("ثبت شد، JE-۰۰۴۲، ۲۴ مهر ۱۴۰۵")   # in the page's digits
        expect(posted.locator(".ai-card-actions")).to_have_count(0)
        expect(cancelled.locator(".ai-card-gone")).to_contain_text("لغو")
        expect(expired.locator(".ai-card-gone")).to_contain_text("منقضی")
        expect(pending.locator(".ai-card-actions button.btn-primary")).to_be_enabled()   # still works
        expect(pending.locator(".ai-voucher-side").first).to_have_text("به ۷۷٫۵۰۰")
        expect(posted.locator(".ai-card-head")).to_contain_text("سند")                    # no longer "please confirm"
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-history-cards-fa.png"))
        page.evaluate("() => { document.getElementById('ai-acct-messages').scrollTop = 0; }")
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-history-cards-fa-top.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")                    # and wait until it is saved: the next test is in English
