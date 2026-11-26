"""#13: a chat entry's "today" is the book's local day, not the server's (UTC) date.

An Iranian book's entry said at 00:45 Tehran time on 1 Azar 1405 (2026-11-21T21:15Z) was dated 30 Aban —
the UTC day — so it landed in the previous Jalali month. The day is taken in the company's timezone
(from its locale); a book with no country keeps the server's date.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import uuid

import pytest

INSTANT = _dt.datetime(2026, 11, 21, 21, 15, tzinfo=_dt.timezone.utc)   # server clock (UTC)
UTC_DAY = _dt.date(2026, 11, 21)                                        # 30 Aban 1405
TEHRAN_DAY = _dt.date(2026, 11, 22)                                     # 1 Azar 1405




@pytest.fixture()
def frozen_clock(monkeypatch):
    """The instant book_today reads: 00:45 Tehran / 01:15 Muscat on the 22nd, still the 21st in UTC."""
    from app.services import locale_service
    monkeypatch.setattr(locale_service, "_now", lambda: INSTANT)
    yield


@pytest.fixture()
def books(db):
    from app.services.ai_eval.fixture import seed_personal_books
    from tests.test_admin_audit import _purge_company
    ctx = seed_personal_books(db, today=_dt.date(2026, 11, 15))
    yield ctx
    _purge_company(db, ctx.company_id)


def _set_locale(db, books, locale, base_currency=None):
    from app.models import Company
    company = db.get(Company, uuid.UUID(books.company_id))
    company.locale = locale
    if base_currency:
        company.base_currency = base_currency
    db.commit()


def _propose(db, books, said, model_date, **extra):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.proposal_tools import ProposeCreateTransaction, ProposeCreateTransactionInput
    from app.services.ai_eval.fixture import PRETTY_WALLET
    args = ProposeCreateTransactionInput(date=model_date.isoformat(), description="خرید نان", currency="OMR", lines=[
        {"account_code": "6110", "debit": 2, "credit": 0}, {"account_code": PRETTY_WALLET, "debit": 0, "credit": 2}],
        **extra)
    with use_company(books.company_id):
        return asyncio.run(ProposeCreateTransaction().run(
            ToolContext(db=db, user_id=books.user_id, user_message=said, mode="personal"), args))


def test_book_today_follows_the_company_timezone(db, books, frozen_clock):
    from app.db.tenant import use_company
    from app.services.locale_service import book_today
    with use_company(books.company_id):
        _set_locale(db, books, "ir")
        assert book_today(db) == TEHRAN_DAY
        _set_locale(db, books, "uk")                # 21:15 GMT: still the 21st in London
        assert book_today(db) == UTC_DAY
        _set_locale(db, books, "default", "OMR")    # an Omani book: 01:15 in Muscat
        assert book_today(db) == TEHRAN_DAY
        _set_locale(db, books, "default", "AED")    # an Emirati book: 01:15 in Dubai
        assert book_today(db) == TEHRAN_DAY
        _set_locale(db, books, "default", "USD")    # no country → the server's own date, as before
        assert book_today(db) == INSTANT.astimezone().date()


@pytest.mark.parametrize("locale,currency", [("ir", None), ("default", "OMR")])
def test_no_day_said_after_midnight_takes_the_local_day(db, books, frozen_clock, locale, currency):
    _set_locale(db, books, locale, currency)   # 00:45 in Tehran, 01:15 in Muscat
    card = _propose(db, books, "۲ ریال نان خریدم", UTC_DAY)
    assert card["preview"]["date"] == TEHRAN_DAY.isoformat()


def test_today_and_yesterday_are_local_in_tehran(db, books, frozen_clock):
    _set_locale(db, books, "ir")
    assert _propose(db, books, "امروز ۲ ریال نان خریدم", UTC_DAY)["preview"]["date"] == TEHRAN_DAY.isoformat()
    assert _propose(db, books, "دیروز ۲ ریال نان خریدم", UTC_DAY)["preview"]["date"] == UTC_DAY.isoformat()


def test_future_guard_uses_the_local_day(db, books, frozen_clock):
    # 2026-11-23 is one day ahead of the Tehran today, so it records; against the UTC day it is two ahead
    # and was refused as a future date.
    _set_locale(db, books, "ir")
    tomorrow = TEHRAN_DAY + _dt.timedelta(days=1)
    card = _propose(db, books, f"{tomorrow.isoformat()} ۲ ریال نان خریدم", tomorrow)
    assert card["preview"]["date"] == tomorrow.isoformat()


def test_the_model_is_told_the_local_today(db, books, frozen_clock):
    from app.db.tenant import use_company
    from app.services.ai_accountant.orchestrator import run_chat_turn
    from tests.test_ai_guardrails import _FakeClient, _text

    class _Seeing(_FakeClient):
        prompts: list[str] = []

        async def chat(self, *, system_prompt, **kw):
            self.prompts.append(system_prompt)
            return await super().chat(system_prompt=system_prompt, **kw)

    _set_locale(db, books, "ir")
    model = _Seeing([_text("بفرمایید.")])
    with use_company(books.company_id):
        asyncio.run(run_chat_turn(db, user_id=books.user_id, user_message="یه سوال دارم", lang="fa",
                                  client=model, mode="personal"))
    assert "Today: 2026-11-22 (Jalali 1405/09/01" in model.prompts[-1], model.prompts[-1][-400:]


def _invoice(db, books):
    from app.db.tenant import use_company
    from app.models.entity import Entity
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.invoice_tools import ProposeCreateInvoice, ProposeCreateInvoiceInput
    with use_company(books.company_id):
        db.add(Entity(name="Acme Trading", type="client"))
        db.commit()
        args = ProposeCreateInvoiceInput(party="Acme Trading", currency="OMR",
                                         lines=[{"description": "Consulting", "unit_price": "10.000"}])
        return asyncio.run(ProposeCreateInvoice().run(
            ToolContext(db=db, user_id=books.user_id, user_message="invoice Acme 10 rials", mode="default"), args))


@pytest.mark.parametrize("locale,currency", [("ir", None), ("default", "OMR")])
def test_invoice_issue_date_and_number_take_the_local_day(db, books, frozen_clock, locale, currency):
    _set_locale(db, books, locale, currency)
    card = _invoice(db, books)
    assert card["preview"]["issue_date"] == TEHRAN_DAY.isoformat(), card["preview"]
    assert card["preview"]["number"].startswith("INV-20261122-"), card["preview"]


@pytest.mark.parametrize("locale,currency", [("ir", None), ("default", "OMR")])
def test_currency_exchange_takes_the_local_day(db, books, frozen_clock, locale, currency):
    from app.db.tenant import use_company
    from app.services.ai_accountant.base import ToolContext
    from app.services.ai_accountant.money_places import ProposeCurrencyExchange, ProposeCurrencyExchangeInput
    from app.services.ai_eval.fixture import PRETTY_WALLET, WALL_E
    _set_locale(db, books, locale, currency)
    args = ProposeCurrencyExchangeInput(from_place=WALL_E, from_currency="USD", from_amount="100",
                                        to_currency="OMR", to_amount="38.500", to_place=PRETTY_WALLET)
    with use_company(books.company_id):
        card = asyncio.run(ProposeCurrencyExchange().run(
            ToolContext(db=db, user_id=books.user_id, user_message="۱۰۰ دلار چنج کردم شد ۳۸.۵ ریال عمان",
                        mode="personal"), args))
    assert card["preview"]["date"] == TEHRAN_DAY.isoformat(), card["preview"]
