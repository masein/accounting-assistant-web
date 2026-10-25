"""A card can't take back money someone doesn't owe (scenario BC1, owner's phone 2026-10-25).

«امیر به من ۶۰۰ ریال دیگه داد» with 300 OMR already owed to Amir came as Cr «طلب از دیگران», and the reply said
Amir would owe the user 300. It is 900 the user owes Amir."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import GREEN_WALLET, seed_personal_books

USER = "u-people"


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _card(db, person, lines, *, currency="OMR", new=None, said="امیر به من ۶۰۰ ریال عمان دیگه داد"):
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    links = [] if new else [{"entity_id": person.entities["امیر"], "role": "supplier"}]
    return asyncio.run(ProposeCreateTransaction().run(
        ToolContext(db=db, user_id=USER, mode="personal", user_message=said),
        ProposeCreateTransactionInput(date=date.today(), description="x", currency=currency, entity_links=links,
                                      new_entities=[{"name": new, "type": "supplier"}] if new else [],
                                      lines=[{"account_code": c, "debit": d, "credit": cr} for c, d, cr in lines])))


def test_bc1_money_from_someone_you_owe_is_more_you_owe(db, person):
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _card(db, person, [(GREEN_WALLET, 600, 0), ("1150", 0, 600)])          # Amir "paying back"
        assert e.value.code == "people_direction"
        assert "you owe امیر 300" in str(e.value) and "2130" in str(e.value) and "900" in str(e.value)
        assert _card(db, person, [(GREEN_WALLET, 600, 0), ("2130", 0, 600)])["status"] == "pending"


def test_bc1_paying_back_someone_you_owe_nothing_is_refused(db, person):
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _card(db, person, [("2130", 50, 0), (GREEN_WALLET, 0, 50)], currency="AED",
                  said="۵۰ درهم به امیر پس دادم")                                    # owes Amir no dirhams
        assert e.value.code == "people_direction" and "1150" in str(e.value)
        assert _card(db, person, [("2130", 100, 0), (GREEN_WALLET, 0, 100)])["status"] == "pending"  # paying 100 of 300


def test_bc1_someone_new_cant_be_paying_back(db, person):
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _card(db, person, [(GREEN_WALLET, 20, 0), ("1150", 0, 20)], new="سارا")
        assert e.value.code == "people_direction"


def test_bc1_a_companys_cards_are_not_held_to_it(db):
    from app.services.ai_accountant.proposal_tools import _guard_people_direction
    _guard_people_direction(ToolContext(db=db, user_id=USER, mode="default"), None)   # returns before reading args


def test_bc1_money_from_a_person_is_not_a_starting_balance(db, person):
    from app.services.ai_accountant.money_places import ProposeMoneyPlace, ProposeMoneyPlaceInput

    def place(message):
        return asyncio.run(ProposeMoneyPlace().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message=message),
            ProposeMoneyPlaceInput(place="کیف پول سبز", kind="cash", currency="OMR", amount="600")))
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            place("امیر به من ۶۰۰ ریال عمان دیگه داد، گذاشتم توی کیف پول سبز")
        assert e.value.code == "money_from_person" and "2130" in str(e.value) and "امیر" in str(e.value)
        with pytest.raises(ToolError) as e:
            place("۶۰۰ ریال عمان توی کیف پول سبز دارم")                         # no one gave it: as before
        assert e.value.code == "already_started"


def test_bc1_a_card_with_nobody_on_it_is_held_to_the_person_named(db, person):
    """The real model left Amir off the card; the end of the turn would have put him on it, too late."""
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    with use_company(person.company_id), pytest.raises(ToolError) as e:
        asyncio.run(ProposeCreateTransaction().run(
            ToolContext(db=db, user_id=USER, mode="personal", user_message="امیر به من ۶۰۰ ریال عمان دیگه داد"),
            ProposeCreateTransactionInput(date=date.today(), description="x", currency="OMR",
                                          lines=[{"account_code": GREEN_WALLET, "debit": 600, "credit": 0},
                                                 {"account_code": "1150", "debit": 0, "credit": 600}])))
    assert e.value.code == "people_direction" and "900" in str(e.value)


def test_bc1_the_balance_says_what_is_owed_once_the_card_is_posted(db, person):
    """«حساب من با امیر الان چقدر هست؟» right after the card: 300 now, 900 once posted."""
    from decimal import Decimal

    from app.services.ai_accountant.orchestrator import _get_or_create_session
    from app.services.ai_accountant.personal_tools import GetPeopleBalances, GetPeopleBalancesInput
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    msg = "امیر به من ۶۰۰ ریال عمان دیگه داد"
    with use_company(person.company_id):
        session = str(_get_or_create_session(db, user_id=USER, session_id=None).id)
        tc = ToolContext(db=db, user_id=USER, mode="personal", user_message=msg, chat_session_id=session)
        asyncio.run(ProposeCreateTransaction().run(tc, ProposeCreateTransactionInput(
            date=date.today(), description="x", currency="OMR",
            lines=[{"account_code": GREEN_WALLET, "debit": 600, "credit": 0}, {"account_code": "2130", "debit": 0, "credit": 600}])))
        out = asyncio.run(GetPeopleBalances().run(tc, GetPeopleBalancesInput(person="امیر")))
    amir = out["people"][0]
    assert Decimal(str(amir["you_owe"]["OMR"])) == 300
    assert Decimal(str(amir["once_posted"]["you_owe"]["OMR"])) == 900 and "once" in out["note"]
    assert Decimal(str(amir["once_posted"]["you_owe"]["USD"])) == Decimal("333.77")       # the other currency as it is
