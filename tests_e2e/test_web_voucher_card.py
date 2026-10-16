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
        page.evaluate("""() => { const s = document.getElementById('topbar-language'); s.value = 'en';
            s.dispatchEvent(new Event('change', { bubbles: true })); }""")


def test_a_companys_card_keeps_its_summary(flow_page):
    """AD5: a company's books are not a person's: the summary, as before."""
    page, watch = flow_page("e2e_cards")
    _open_chat(page)
    card = _ask(page)
    expect(card.locator(".ai-card-summary")).to_contain_text("DR 112001")
    expect(card.locator(".ai-voucher")).to_have_count(0)
    assert watch.problems() == [], watch.problems()
