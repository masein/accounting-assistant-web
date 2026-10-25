"""A person's money in several currencies has no total across them (scenario BA1, owner's phone 2026-10-22)."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.cash_tools import GetCashPosition, GetCashPositionInput
from app.services.ai_eval.fixture import seed_personal_books


@pytest.fixture()
def person(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _cash(db, mode):
    return asyncio.run(GetCashPosition().run(ToolContext(db=db, user_id="u", mode=mode), GetCashPositionInput()))


def test_ba1_each_currency_on_its_own_and_no_total(db, person):
    with use_company(person.company_id):
        out = _cash(db, "personal")
    assert out["total"] is None and "unconverted" not in out
    assert all("balance" not in a for a in out["accounts"])               # a place's name, not a rial figure
    held = {x["currency"]: Decimal(str(x["total"])) for x in out["by_currency"]}
    assert held == {"IRR": Decimal("47000000"), "OMR": Decimal("123.5"), "USD": Decimal("2400"), "AED": Decimal("1785")}
    assert "never add them up" in out["note"]


def test_ba1_the_card_is_a_table_of_places(db, person):
    from app.services.ai_accountant.blocks import _cash_figure
    with use_company(person.company_id):
        block = _cash_figure(_cash(db, "personal"), lang="fa")
    assert block["type"] == "table" and {r["currency"] for r in block["rows"]} == {"IRR", "OMR", "USD", "AED"}


def test_ba1_a_companys_cash_keeps_its_total(db):
    out = _cash(db, "default")
    assert out["total"] is not None and all("balance" in a for a in out["accounts"])


def _card(db, message, currency, lines):
    from datetime import date

    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    return asyncio.run(ProposeCreateTransaction().run(
        ToolContext(db=db, user_id="u", mode="personal", user_message=message),
        ProposeCreateTransactionInput(date=date.today(), description="x", currency=currency,
                                      lines=[{"account_code": c, "debit": d, "credit": cr} for c, d, cr in lines])))


RECLASS = "این سه تا خرید رو ببرشون زیر «لوازم و تعمیرات خانه»: ۲ ریال عمان، ۳ ریال عمان، ۱ ریال عمان"


def test_ba2_a_card_in_another_currency_than_the_one_named_is_refused(db, person):
    from app.services.ai_accountant.base import ToolError
    with use_company(person.company_id):
        with pytest.raises(ToolError) as e:
            _card(db, RECLASS, None, [("6125", 6, 0), ("6195", 0, 6)])          # no currency: the books' rials
        assert e.value.code == "stated_currency" and "OMR" in str(e.value)
        assert _card(db, RECLASS, "OMR", [("6125", 6, 0), ("6195", 0, 6)])["status"] == "pending"


def test_ba2_two_currencies_named_hold_nothing(db, person):
    with use_company(person.company_id):
        both = "۱۰ دلار دادم معادل ۳.۸۵ ریال عمان"
        assert _card(db, both, "USD", [("6180", 10, 0), ("112003", 0, 10)])["status"] == "pending"
