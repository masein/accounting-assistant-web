"""A person's money in several currencies, each kept as it is (scenarios AB1–AB7).

From the owner's phone: «۷۷.۵ ریال عمان توی کیف پول سبز داریم» in books that
count in Iranian rials was refused, "change your books' currency first". A
person keeps rials at a bank, Omani rials in a wallet and dollars in a vault,
and wants each kept in its own currency, not exchanged."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from tests.test_ai_guardrails import _calls, _text
from tests.test_personal_money import API, person, scripted  # noqa: F401  (fixtures)


def _place(place, kind, currency, amount):
    return ("propose_money_place", {"place": place, "kind": kind, "currency": currency, "amount": amount})


def _say(person, phone, message):
    r = person["client"].post(f"{API}/chat", headers=phone, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(person, phone, card):
    r = person["client"].post(f"{API}/proposals/{card['token']}/confirm", headers=phone, json={})
    assert r.status_code == 200, r.text
    return r.json()


def _card(reply):
    return next(b for b in reply["blocks"] if b["type"] == "proposal")


def test_ab1_asked_once_with_answers_to_tap(person, scripted):
    """While the books can still change, the question comes with its two answers to tap."""
    scripted(_calls(("ask_with_choices", {"options": ["دفاتر به ریال عمان", "ریال ایران بماند، این ریال عمان"]})),
             _text("دفاتر شما به ریال ایران است. ارز دفاتر ریال عمان شود، یا این پول به ریال عمان کنار بقیه بماند؟"))
    reply = _say(person, person["phone"](), "۷۷.۵ ریال عمان توی کیف پول سبز داریم")
    words = [b for b in reply["blocks"] if b["type"] == "text"][-1]
    assert words["choices"] == ["دفاتر به ریال عمان", "ریال ایران بماند، این ریال عمان"]
    assert not [b for b in reply["blocks"] if b["type"] == "proposal"]          # nothing to post yet


def test_ab2_the_wallet_is_a_place_of_its_own_in_omani_rials(person, db, scripted):
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    phone = person["phone"]()
    scripted(_calls(_place("کیف پول سبز", "cash", "OMR", "77.500")), _text("۷۷٫۵۰۰ ریال عمان در کیف پول سبز."))
    card = _card(_say(person, phone, "ریال ایران بماند، این ریال عمان"))
    assert card["amount"] == {"value": 77.5, "currency": "OMR"}
    assert [(ln["account"], ln["name"], ln["debit"]) for ln in card["lines"]][0] == ("112001", "کیف پول سبز", 77.5)
    _confirm(person, phone, card)
    with use_company(person["cid"]):
        wallet = db.execute(select(Account).where(Account.code == "112001")).scalars().one()
        parent = db.get(Account, wallet.parent_id)
        txn = db.execute(select(Transaction).join(TransactionLine).where(TransactionLine.account_id == wallet.id)).scalars().one()
    assert (wallet.name, parent.code) == ("کیف پول سبز", "1120")
    assert txn.currency == "OMR"                                                 # kept as it is: no rate, no exchange


def test_ab3_or_the_books_count_in_omani_rials_instead(person, db, scripted):
    from app.models.company import Company
    from app.db.tenant import tenant_bypass
    phone = person["phone"]()
    scripted(_calls(("propose_books_currency", {"currency": "OMR"})), _text("دفاتر به ریال عمان."))
    card = _card(_say(person, phone, "دفاتر به ریال عمان"))
    assert "Omani rial" in card["summary"]
    _confirm(person, phone, card)
    with tenant_bypass():
        db.expire_all()
        assert db.get(Company, uuid.UUID(person["cid"])).base_currency == "OMR"


def test_ab3_once_there_are_entries_the_books_keep_their_currency(person, db):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.money_places import ProposeBooksCurrency, ProposeBooksCurrencyInput
    api = person["web"]()
    api.post("/transactions", json={"date": "2026-10-01", "description": "cash", "currency": "IRR", "lines": [
        {"account_code": "1120", "debit": 1000, "credit": 0}, {"account_code": "3110", "debit": 0, "credit": 1000}]})
    with use_company(person["cid"]):
        with pytest.raises(ToolError) as e:
            asyncio.run(ProposeBooksCurrency().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                                   ProposeBooksCurrencyInput(currency="OMR")))
    assert e.value.code == "books_have_entries"


def test_ab4_ab5_many_places_many_currencies_each_as_it_is(person, scripted):
    phone = person["phone"]()
    for place, kind, ccy, amount in (("کیف پول سبز", "cash", "OMR", "77.500"), ("گاوصندوق", "cash", "USD", "500"),
                                     ("حساب ملت", "bank", "IRR", 3_000_000)):
        scripted(_calls(_place(place, kind, ccy, amount)), _text("ثبت شد."))
        _confirm(person, phone, _card(_say(person, phone, f"{amount} {ccy} {place}")))
    reply = _say(person, phone, "چقدر پول دارم؟")                              # a quick answer: no model
    words = [b for b in reply["blocks"] if b["type"] == "text"][-1]["text"]
    assert words == "موجودی نقد و بانک امروز، هر ارز جداگانه: ۳,۰۰۰,۰۰۰ ریال، ۷۷٫۵۰۰ ریال عمان، ۵۰۰٫۰۰ دلار آمریکا."
    table = next(b for b in reply["blocks"] if b["type"] == "table")
    assert table["kind"] == "holdings"
    assert sorted((r["label"], r["currency"], r["value"]) for r in table["rows"]) == [
        ("حساب ملت", "IRR", 3_000_000), ("کیف پول سبز", "OMR", 77.5), ("گاوصندوق", "USD", 500)]


def test_ab6_the_same_place_is_found_and_not_started_twice(person, db, scripted):
    from app.services.ai_accountant.base import ToolContext, ToolError
    from app.services.ai_accountant.money_places import ProposeMoneyPlace, ProposeMoneyPlaceInput, find_place
    phone = person["phone"]()
    scripted(_calls(_place("کیف پول سبز", "cash", "OMR", "77.500")), _text("ثبت شد."))
    _confirm(person, phone, _card(_say(person, phone, "۷۷.۵ ریال عمان توی کیف پول سبز")))
    with use_company(person["cid"]):
        _parent, acc = find_place(db, "كيف پول سبز", "1120")                    # Arabic letters, as a keyboard may type
        assert acc is not None and acc.code == "112001"
        with pytest.raises(ToolError) as e:
            asyncio.run(ProposeMoneyPlace().run(ToolContext(db=db, user_id="u", username="me", mode="personal"),
                                                ProposeMoneyPlaceInput(place="کیف پول سبز", kind="cash", currency="OMR",
                                                                       amount="10")))
    assert e.value.code == "already_started"


def test_ab7_the_instructions_ask_instead_of_refusing(person, scripted):
    scripted(_text("…"))
    _say(person, person["phone"](), "I have 77.5 Omani rials in my green wallet")
    prompt = scripted.prompts[0]
    assert "never refuse it, never convert it" in prompt and "ask_with_choices" in prompt
    assert "«امور مالی من» (My finances)" in prompt and "no Markdown" in prompt
    assert "propose NOTHING" not in prompt
