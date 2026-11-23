"""A place the person counted (chat review 2026-11-23, scenario CT1).

From the owner: he counted his wallet (59.9 Omani rials where the books said 274.815), his envelope and another
wallet, and asked how to say so in the app. The difference had to be worked out by hand and given back as a figure,
and the assistant's rule was never to make a "correction" card. Now the count is said as it is, and the card works
the difference out. Figures made up: the pretty wallet holds 60 − 20.25 = 39.75 Omani rials.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import PRETTY_WALLET, seed_personal_books

USER = "u-counted"


@pytest.fixture()
def books(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _count(db, books, said="شمردم، توی کیف پول قشنگم ۲۰ ریال عمان هست", **kw):
    from app.services.ai_accountant.money_places import ProposeCountedPlace, ProposeCountedPlaceInput
    with use_company(books.company_id):
        return asyncio.run(ProposeCountedPlace().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message=said), ProposeCountedPlaceInput(**kw)))


def _confirm(db, books, card):
    from app.services.ai_accountant.execute_service import execute_proposal
    with use_company(books.company_id):
        return execute_proposal(db, confirmation_token=card["confirmation_token"], actor_user_id=USER)


def _held(db, books, code=PRETTY_WALLET, ccy="OMR"):
    from app.services.ai_accountant.money_places import _held as held
    with use_company(books.company_id):
        return Decimal(str(held(db, code).get(ccy, 0)))


def _spend(db, books, amount, code=PRETTY_WALLET):
    from app.models.account import Account
    from app.services.ai_eval.fixture import _journal
    from sqlalchemy import select
    from datetime import date
    with use_company(books.company_id):
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        _journal(db, acc, date.today(), "خرید", [("6110", Decimal(amount), 0), (code, 0, Decimal(amount))],
                 currency="OMR")
        db.commit()


def test_ct1_the_count_matches(db, books):
    out = _count(db, books, place="کیف پول قشنگم", counted="39.75")
    assert out["status"] == "matches" and "confirmation_token" not in out and "Nothing to record" in out["summary"]


def test_ct1_less_than_the_books_asks_what_it_was_then_cards_it(db, books):
    with pytest.raises(ToolError) as asked:
        _count(db, books, place="کیف پول قشنگم", counted="20")
    assert asked.value.code == "gap_account"
    assert "19.75 OMR left it unrecorded" in str(asked.value) and "ask_with_choices" in str(asked.value)
    card = _count(db, books, place="کیف پول قشنگم", counted="20", gap_account="6220")
    p = card["preview"]
    assert (Decimal(str(p["balance"])), Decimal(str(p["counted"])), Decimal(str(p["difference"]))) == \
        (Decimal("39.75"), Decimal("20"), Decimal("-19.75"))
    assert [(ln["account_code"], Decimal(str(ln["debit"])), Decimal(str(ln["credit"]))) for ln in p["lines"]] == \
        [("6220", Decimal("19.75"), 0), (PRETTY_WALLET, 0, Decimal("19.75"))]
    assert p["description"].startswith("شمارش کیف پول قشنگم: ۲۰٫۰۰۰ ریال عمان") and "کمتر از دفاتر" in p["description"]
    done = _confirm(db, books, card)
    assert _held(db, books) == Decimal("20")
    from app.services.ai_accountant.execute_service import undo_action
    with use_company(books.company_id):
        undo_action(db, audit_log_id=done.audit_log_id, actor_user_id=USER)       # taken back like any entry
    assert _held(db, books) == Decimal("39.75")


def test_ct1_more_than_the_books_comes_from_somewhere(db, books):
    card = _count(db, books, said="I counted 50 OMR in the pretty wallet", place=PRETTY_WALLET, counted="50",
                  gap_account="3110")
    assert [(ln["account_code"], Decimal(str(ln["debit"]))) for ln in card["preview"]["lines"]] == \
        [(PRETTY_WALLET, Decimal("10.25")), ("3110", 0)]
    assert "بیشتر از دفاتر" in card["preview"]["description"]      # in the place's own language, as its other cards
    _confirm(db, books, card)
    assert _held(db, books) == Decimal("50")


def test_ct1_the_count_stands_when_the_place_changed_before_confirming(db, books):
    from app.services.ai_accountant.execute_service import ProposalCancelled
    card = _count(db, books, place="کیف پول قشنگم", counted="20", gap_account="6220")
    _spend(db, books, "5")                                       # a card confirmed in between: 34.75 now
    _confirm(db, books, card)
    assert _held(db, books) == Decimal("20")                     # 14.75 posted, not 19.75: what they counted
    again = _count(db, books, place="کیف پول قشنگم", counted="10", gap_account="6220")
    _spend(db, books, "15")                                      # 5 now: less than they counted
    with pytest.raises(ProposalCancelled) as stale:
        _confirm(db, books, again)
    assert "holds 5 Omani rial now, less than was counted" in str(stale.value)
    assert _held(db, books) == Decimal("5")
    from app.core.messages import localize_detail
    fa = localize_detail(str(stale.value), "fa").replace("⁨", "").replace("⁩", "")
    assert fa.startswith("کیف پول قشنگم اکنون ۵ ریال عمان دارد، کمتر از آنچه شمرده شد")


def test_ct1_a_place_of_several_currencies_is_counted_one_at_a_time(db, books):
    with pytest.raises(ToolError) as e:
        _count(db, books, place="کیف پول والای", counted="100", gap_account="6220")
    assert e.value.code == "which_currency" and "AED" in str(e.value) and "USD" in str(e.value)
    card = _count(db, books, place="کیف پول والای", counted="2300", currency="USD", gap_account="6220")
    assert Decimal(str(card["preview"]["amount"])) == Decimal("100") and card["preview"]["currency"] == "USD"


def test_ct1_the_rule_and_the_tool_are_offered():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM
    from app.services.ai_accountant.tool_routing import GROUPS
    assert "A PLACE THEY COUNTED" in PERSONAL_MODE_ADDENDUM and "never subtract it yourself" in PERSONAL_MODE_ADDENDUM
    assert GROUPS["counting"][1] == ("propose_counted_place",)
    from app.services.ai_accountant.tool_routing import route
    assert "counting" in route("شمردم، توی کیف پول قشنگم ۵۹.۹ ریال عمان هست", personal=True)
    assert "counting" in route("I counted 700 dollars in the envelope", personal=True)
    assert "counting" not in route("امروز ۷ ریال عمان ناهار خوردم از کیف پول قشنگم", personal=True)


def test_ct1_the_phone_and_the_web_draw_its_card(db, books):
    from app.services.ai_accountant.blocks import proposal_block
    card = _count(db, books, place="کیف پول قشنگم", counted="20", gap_account="6220")
    with use_company(books.company_id):
        block = proposal_block(db, {**card, "tool_name": "propose_counted_place"}, calendar="jalali", lang="fa")
    assert block["type"] == "proposal" and block["title"].startswith("شمارش کیف پول قشنگم")
    assert block["amount"] == {"value": 19.75, "currency": "OMR"} or Decimal(str(block["amount"]["value"])) == Decimal("19.75")
    assert [ln["account"] for ln in block["lines"]] == ["6220", PRETTY_WALLET] and block["actions"] == ["confirm", "cancel"]
    from tests.test_mobile_contract import contract_errors
    assert contract_errors(block) == []
