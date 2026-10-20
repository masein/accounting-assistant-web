"""Amir's cards on the web, in a person's books (scenarios AG1, AG2), under the strict CSP.

«Amir paid for these» and a list: every card names Amir, and each says he is
made once, with whichever card is confirmed first — not nine Amirs, and not a
«supplier» in a person's books. The reply is served by the test, as the server
now sends it (tests/test_personal_people.py)."""
from __future__ import annotations

import json
import os

from playwright.sync_api import expect

from tests_e2e.conftest import ARTIFACTS, switch_language

AMIR = {"name": "Amir", "type": "supplier", "role": "payee", "shared": True}
ROWS = [("333.77", "Muscat Airbnb"), ("851.06", "Muscat Airbnb"), ("525.63", "Muscat tickets")]


def _reply(travel, owe, tag):
    proposals = []
    for i, (amount, what) in enumerate(ROWS):
        value = float(amount)
        proposals.append({
            "confirmation_token": f"7f1c2a9e-0000-4000-8000-0000000{tag}{i:02d}", "tool_name": "propose_create_transaction",
            "summary": f"{what} — paid by Amir", "preview": {"description": f"{what} — paid by Amir"}, "expires_at": None,
            "new_entities": [AMIR],
            "card": {"title": f"{what} — paid by Amir", "amount": {"value": value, "currency": "USD"},
                     "date": {"iso": "2026-10-18", "display": "18 Oct 2026"},
                     "lines": [{"account": "6185", "name": travel, "debit": value, "credit": 0},
                               {"account": "2130", "name": owe, "debit": 0, "credit": value}]}})
    return {"session_id": "s-amir", "turns": 2, "stop_reason": "end_turn", "tool_calls": [], "proposals": proposals,
            "text": "Three cards are ready, each under Travel in USD, with Amir as the person you owe."}


def _ask(page, reply, words, tag):
    page.route("**/ai-accountant/chat", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(reply, ensure_ascii=False)))
    page.fill("#ai-acct-input", words)
    page.click("#ai-acct-send")
    cards = page.locator(f".ai-card[data-token^='7f1c2a9e-0000-4000-8000-0000000{tag}']")
    expect(cards).to_have_count(3)
    return cards


def test_every_card_names_amir_made_once(flow_page):
    page, watch = flow_page("e2e_people")      # its own user: sharing e2e_cards ran its third test past 120 requests a minute
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    page.evaluate("() => { currentRole = 'personal'; }")
    cards = _ask(page, _reply("Travel", "Money I owe", "0a6"), "Amir payed for these: $333.77 - Muscat Airbnb …", "0a6")
    for i in range(3):
        note = cards.nth(i).locator(".ai-card-note")
        expect(note).to_have_text("➕ Will create person: Amir — once, with whichever of these cards you confirm first")
    assert "supplier" not in page.locator("#ai-acct-messages").inner_text().lower()
    os.makedirs(ARTIFACTS, exist_ok=True)
    page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-personal-amir-en.png"))
    try:
        switch_language(page, "fa")
        page.evaluate("() => { currentRole = 'personal'; }")
        cards = _ask(page, _reply("سفر", "بدهی به دیگران", "0a7"), "امیر این‌ها را حساب کرد", "0a7")
        expect(cards.nth(2).locator(".ai-card-note")).to_have_text(
            "➕ ایجاد شخص: Amir — یک بار، با اولین کارتی از این‌ها که تأیید کنید")
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-personal-amir-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")


def test_a_card_names_the_amir_already_in_the_books(flow_page):
    """AK3, AK4: «۲۰ ریال عمان از پول امیر رو دادم» — Amir is in the books already, so the card links him and says
    so above its lines; a lunch only shared with him names no one."""
    page, watch = flow_page("e2e_people")
    page.evaluate("() => { location.hash = 'ai-accountant'; }")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ai-acct-input")
    token = "7f1c2a9e-0000-4000-8000-0000000ak301"
    lunch = "7f1c2a9e-0000-4000-8000-0000000ak302"
    reply = {"session_id": "s-ak", "turns": 2, "stop_reason": "end_turn", "tool_calls": [],
             "text": "کارت‌ها آماده است.",
             "proposals": [
                 {"confirmation_token": token, "tool_name": "propose_create_transaction", "summary": "پرداخت به امیر",
                  "preview": {"description": "پرداخت به امیر"}, "expires_at": None, "new_entities": [],
                  "card": {"title": "پرداخت به امیر", "amount": {"value": 20, "currency": "OMR"},
                           "date": {"iso": "2026-10-20", "display": "۲۸ مهر ۱۴۰۵"},
                           "people": [{"name": "امیر", "new": False}],
                           "lines": [{"account": "2130", "name": "بدهی به دیگران", "debit": 20, "credit": 0},
                                     {"account": "112001", "name": "کیف پول قشنگم", "debit": 0, "credit": 20}]}},
                 {"confirmation_token": lunch, "tool_name": "propose_create_transaction", "summary": "ناهار با امیر",
                  "preview": {"description": "ناهار با امیر"}, "expires_at": None, "new_entities": [],
                  "card": {"title": "ناهار با امیر", "amount": {"value": 7, "currency": "OMR"},
                           "date": {"iso": "2026-10-20", "display": "۲۸ مهر ۱۴۰۵"}, "people": [],
                           "lines": [{"account": "6180", "name": "تفریح و رستوران", "debit": 7, "credit": 0},
                                     {"account": "112001", "name": "کیف پول قشنگم", "debit": 0, "credit": 7}]}}]}
    try:
        switch_language(page, "fa")
        page.evaluate("() => { currentRole = 'personal'; }")
        page.route("**/ai-accountant/chat", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(reply, ensure_ascii=False)))
        page.fill("#ai-acct-input", "۲۰ ریال عمان از پول امیر رو از کیف پول دادم، ناهار با امیر ۷ ریال")
        page.click("#ai-acct-send")
        card = page.locator(f".ai-card[data-token='{token}']")
        expect(card.locator(".ai-voucher-people")).to_have_text("شخص: امیر")
        expect(card.locator(".ai-card-note")).to_have_count(0)                         # nobody made
        expect(page.locator(f".ai-card[data-token='{lunch}'] .ai-voucher-people")).to_have_count(0)
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.locator("#ai-acct-messages").screenshot(path=os.path.join(ARTIFACTS, "chat-personal-known-amir-fa.png"))
        assert watch.problems() == [], watch.problems()
    finally:
        switch_language(page, "en")
