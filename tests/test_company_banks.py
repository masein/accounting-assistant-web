"""A company's own banks, as the user names them (chat review 2026-11-10, scenarios BT1–BT5).

From the production chats, in a company's books: "paid … from <bank>" was credited to the chart's general
«موجودی نقد و بانک»; searching the bank's own name found that general account above the bank's; money moved
between two of the company's banks was booked as an expense from one and a receipt into the other; and the
payment-direction check knew only English. The bank names here are made up.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.ai_accountant import AIProposal
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
from tests.test_statement_export import _company


@pytest.fixture()
def co(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "ir", "IRR")
    day = api.post("/entities", json={"type": "bank", "name": "بانک دی شمال"}).json()["code"]
    ayande = api.post("/entities", json={"type": "bank", "name": "آینده غرب"}).json()["code"]
    yield cid, day, ayande
    client.cookies.clear()
    _purge_company(db, cid)


def _card(db, cid, message, lines):
    args = ProposeCreateTransactionInput(date="2026-08-02", description="test", currency="IRR", lines=[
        {"account_code": c, "debit": d, "credit": k} for c, d, k in lines])
    from app.services.ai_accountant.orchestrator import _numbers_in_text
    with use_company(cid):
        ctx = ToolContext(db=db, user_id="u-banks", user_message=message, source_amounts=_numbers_in_text(message))
        return asyncio.run(ProposeCreateTransaction().run(ctx, args))


def _stored_lines(db, cid, card):
    import uuid
    with use_company(cid):
        row = db.execute(select(AIProposal).where(
            AIProposal.confirmation_token == uuid.UUID(card["confirmation_token"]))).scalar_one()
        db.refresh(row)
        return {ln["account_code"]: (ln["debit"], ln["credit"]) for ln in row.tool_input["lines"]}


def test_bt1_the_card_goes_to_the_bank_named(db, co):
    from app.services.ai_accountant.named_banks import banks_named
    from app.services.ai_accountant.orchestrator import _named_place_on_cards
    cid, day, ayande = co
    with use_company(cid):
        assert [n.code for n in banks_named(db, "we paid 4,000,000 for office supplies from bank dey")] == [day]
        assert [n.code for n in banks_named(db, "۴ میلیون لوازم اداری از حساب اینده غرب دادیم")] == [ayande]
        assert banks_named(db, "این رو برای آینده نگه دار") == []            # «آینده» alone is the future
        assert banks_named(db, "خرید دی ماه") == []                             # «دی» alone is a month
    for message, bank in (("we paid 4,000,000 for office supplies from bank dey", day),
                          ("۴ میلیون لوازم اداری از حساب اینده غرب دادیم", ayande)):
        card = _card(db, cid, message, [("6112", 4_000_000, 0), ("1110", 0, 4_000_000)])
        with use_company(cid):
            _named_place_on_cards(db, [card], personal=False, user_message=message)
        assert _stored_lines(db, cid, card) == {"6112": (4_000_000, 0), bank: (0, 4_000_000)}
        assert card["preview"]["lines"][1]["account_code"] == bank          # the card shown, too
    # no bank named: the card stays where it is
    card = _card(db, cid, "we paid 4,000,000 for office supplies", [("6112", 4_000_000, 0), ("1110", 0, 4_000_000)])
    with use_company(cid):
        _named_place_on_cards(db, [card], personal=False, user_message="we paid 4,000,000 for office supplies")
    assert _stored_lines(db, cid, card) == {"6112": (4_000_000, 0), "1110": (0, 4_000_000)}


def test_bt2_search_finds_the_bank_named(db, co):
    from app.services.ai_accountant.read_tools import SearchAccounts, SearchAccountsInput
    cid, day, ayande = co

    def top(query):
        with use_company(cid):
            out = asyncio.run(SearchAccounts().run(ToolContext(db=db, user_id="u-banks"), SearchAccountsInput(query=query)))
        return out["matches"][0]["code"]
    assert top("بانک دی شمال") == day
    assert top("bank dey") == day
    assert top("حساب آینده غرب") == ayande
    assert top("بانک") == "1110"                                            # a bank, unnamed, is the general one


def test_bt3_a_transfer_between_own_banks_is_one_card(db, co):
    from app.services.ai_accountant.named_banks import own_transfer
    cid, day, ayande = co
    said = "۲۰ میلیون تومان از دی شمال زدیم به حساب آینده غرب و ۳۰۰ هزار تومان کارمزد گرفت"
    with use_company(cid):
        assert own_transfer(db, said) == (day, ayande)
        assert own_transfer(db, "moved 50m from bank dey to ayande") == (day, ayande)
        assert own_transfer(db, "از دی شمال لوازم خریدیم") is None
    for lines in ([("6210", 200_000_000, 0), (day, 0, 200_000_000)],          # an expense from one bank
                  [(ayande, 200_000_000, 0), ("4110", 0, 200_000_000)],       # a receipt into the other
                  [(day, 200_000_000, 0), (ayande, 0, 200_000_000)]):         # the wrong way round
        with pytest.raises(ToolError) as e:
            _card(db, cid, said, lines)
        assert e.value.code == "own_bank_transfer" and day in e.value.message
    assert _card(db, cid, said, [(ayande, 200_000_000, 0), (day, 0, 200_000_000)])["confirmation_token"]
    # the fee, from the bank the money left
    assert _card(db, cid, said, [("6210", 3_000_000, 0), (day, 0, 3_000_000)])["confirmation_token"]


def test_bt4_a_payment_said_in_persian_credits_the_bank(db, co):
    cid, day, _ayande = co
    card = _card(db, cid, "۴ میلیون تومان لوازم اداری خریدیم از بانک دی شمال",
                 [(day, 40_000_000, 0), ("6112", 0, 40_000_000)])        # reversed: the bank debited
    assert _stored_lines(db, cid, card) == {day: (0, 40_000_000), "6112": (40_000_000, 0)}
    # money that came in is left as it is
    card = _card(db, cid, "۴ میلیون تومان از مشتری به حساب بانک دی شمال واریز شد",
                 [(day, 40_000_000, 0), ("4110", 0, 40_000_000)])
    assert _stored_lines(db, cid, card) == {day: (40_000_000, 0), "4110": (0, 40_000_000)}


def test_bt5_the_assistant_is_told():
    from app.services.ai_accountant.orchestrator import SYSTEM_PROMPT
    assert "the bank the user names is that bank's own account" in SYSTEM_PROMPT
    assert "never an expense from one and a receipt into the other" in SYSTEM_PROMPT
