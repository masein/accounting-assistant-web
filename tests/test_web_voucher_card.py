"""A person's draft card on the web, as the phone draws it (scenarios AD1–AD5).

The web chat drew a voucher from the server's summary, written for the model
in English with DR and CR («DR 112001 کیف پول سبز: 77.5»). Its reply now
carries each voucher's title, date, amount and lines with their accounts'
names, as the phone's card; the page draws them To and From in a person's books."""
from __future__ import annotations

from tests.test_ai_guardrails import _calls, _text
from tests.test_chat_replies import model  # noqa: F401  (fixture)
from tests.test_personal_money import person  # noqa: F401  (fixture)


def _chat(web, message):
    r = web.post("/ai-accountant/chat", json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def test_ad1_the_card_comes_with_its_lines_and_names(person, model):
    web = person["web"]()
    model(_calls(("propose_money_place", {"place": "کیف پول سبز", "kind": "cash", "currency": "OMR", "amount": "77.500"})),
          _text("کارت آماده است."))
    card = _chat(web, "۷۷٫۵ ریال عمان توی کیف پول سبز داریم")["proposals"][0]["card"]
    assert card["title"] == "کیف پول سبز — موجودی اولیه"
    assert card["amount"] == {"value": 77.5, "currency": "OMR"}
    assert [(ln["account"], ln["name"], ln["debit"], ln["credit"]) for ln in card["lines"]] == [
        ("112001", "کیف پول سبز", 77.5, 0), ("3110", "خالص دارایی اولیه", 0, 77.5)]
    # the date in the question's language: Persian words, Persian digits
    assert card["date"]["display"] and not any(ch.isascii() and ch.isdigit() for ch in card["date"]["display"])


def test_ad2_asked_in_english_the_date_is_english(person, model):
    web = person["web"]()
    model(_calls(("propose_money_place", {"place": "Blue wallet", "kind": "cash", "currency": "OMR", "amount": "10"})),
          _text("The card is ready."))
    card = _chat(web, "I have 10 Omani rials in my blue wallet")["proposals"][0]["card"]
    assert card["title"].startswith("Blue wallet") and card["date"]["display"][0].isdigit()


def test_ad4_a_card_that_posts_nothing_has_no_lines(person, model):
    web = person["web"]()
    model(_calls(("propose_books_currency", {"currency": "OMR"})), _text("Ready."))
    proposal = _chat(web, "count my books in Omani rials")["proposals"][0]
    assert proposal["card"] is None and "Omani rial" in proposal["summary"]
