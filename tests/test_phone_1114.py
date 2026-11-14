"""The owner's phone, 2026-11-14 (scenarios CC1–CC4).

Asked «چقدر توی کیفم پول هست؟», the assistant read the wallet as one figure in rials — the dollars at a rate,
the Omani rials (no rate) left out — and read its dollars as Omani rials too. Told it was wrong, it made a
"correction" card of the two receipts just confirmed, credited to «متفرقه»; money a company paid back for a
purchase went to «طلب از دیگران»; replies ended in stray words («તારીખ», «kartaa»). Figures made up.
"""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.db.tenant import use_company
from app.services.ai_accountant.base import ToolContext, ToolError
from app.services.ai_eval.fixture import GREEN_WALLET, WALL_E, seed_personal_books

USER = "u-phone1114"


@pytest.fixture()
def books(db):
    ctx = seed_personal_books(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


def _balance(db, books, code, mode="personal"):
    from app.services.ai_accountant.read_tools import GetAccountBalance, GetAccountBalanceInput
    with use_company(books.company_id):
        return asyncio.run(GetAccountBalance().run(ToolContext(db=db, user_id=USER, mode=mode),
                                                   GetAccountBalanceInput(account_code=code)))


def test_cc1_a_place_in_each_currency(db, books):
    out = _balance(db, books, WALL_E)
    assert out["by_currency"] == [{"currency": "AED", "balance": 1785}, {"currency": "USD", "balance": 2400}]
    assert "balance" not in out and "unconverted" not in out and "say each one" in out["note"]
    green = _balance(db, books, GREEN_WALLET)
    assert green["by_currency"] == [{"currency": "OMR", "balance": Decimal("383.75")}]
    company_view = _balance(db, books, WALL_E, mode="default")
    assert "balance" in company_view and company_view["by_currency"][0]["currency"] == "AED"


def _card(db, books, lines, message="", currency="OMR"):
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    args = ProposeCreateTransactionInput(date=date.today(), description="test", currency=currency, lines=[
        {"account_code": c, "debit": d, "credit": k} for c, d, k in lines])
    with use_company(books.company_id):
        ctx = ToolContext(db=db, user_id=USER, mode="personal", user_message=message)
        return asyncio.run(ProposeCreateTransaction().run(ctx, args))


def test_cc2_money_back_only_from_where_it_went(db, books):
    with pytest.raises(ToolError) as e:                                   # a "correction" credited to a category
        _card(db, books, [(GREEN_WALLET, Decimal("730"), 0), ("6110", 0, Decimal("730"))],
              "اشتباه میکنی یه ۴۱۲ و یه ۳۲۰ همین الان اضافه کردیم")
    assert e.value.code == "more_back_than_spent" and "20.25" in e.value.message
    assert _card(db, books, [(GREEN_WALLET, Decimal("10"), 0), ("6110", 0, Decimal("10"))],
                 "۱۰ ریال از لولو پس گرفتم")["confirmation_token"]           # a refund of what was spent there
    with pytest.raises(ToolError) as e:                                   # nobody owed it
        _card(db, books, [(GREEN_WALLET, Decimal("412"), 0), ("1150", 0, Decimal("412"))],
              "اون ۴۱۲ ریال رو از شرکت گرفتم")
    assert e.value.code == "more_back_than_owed" and "category" in e.value.message
    # a move between two categories is not money back
    assert _card(db, books, [("6180", Decimal("5"), 0), ("6110", 0, Decimal("5"))], "۵ ریال از خوراک ببر به رستوران")


def test_cc3_the_assistant_is_told():
    from app.services.ai_accountant.orchestrator import PERSONAL_MODE_ADDENDUM, _CORRECTABLE_REFUSALS
    assert "A BALANCE THE USER SAYS IS WRONG" in PERSONAL_MODE_ADDENDUM
    assert 'Never make a "correction" card' in PERSONAL_MODE_ADDENDUM
    assert "don't ask for a figure you have" in PERSONAL_MODE_ADDENDUM
    assert "MONEY BACK for something already recorded" in PERSONAL_MODE_ADDENDUM
    assert {"more_back_than_spent", "more_back_than_owed"} <= _CORRECTABLE_REFUSALS


def test_cc4_stray_words_go():
    from app.services.ai_accountant.orchestrator import _no_foreign_words, _no_stray_tail
    assert _no_foreign_words("کارت هزینه آماده است. તારીખ") == "کارت هزینه آماده است."
    assert _no_foreign_words("هزینه‌ها 大小规律 ثبت شد") == "هزینه‌ها ثبت شد"
    assert _no_foreign_words("ok, пока") == "ok,"
    assert _no_foreign_words("این پول را با کدام کیف یکی بگیرم؟") == "این پول را با کدام کیف یکی بگیرم؟"
    assert _no_stray_tail("این پول را با کدام کیف یکی بگیرم؟ kartaa") == "این پول را با کدام کیف یکی بگیرم؟"
    assert _no_foreign_words("SHOP LLC ۴۱۲ ریال") == "SHOP LLC ۴۱۲ ریال"
