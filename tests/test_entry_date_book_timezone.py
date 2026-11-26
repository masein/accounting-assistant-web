"""#13: a chat entry's "today" is the book's local day, not the server's (UTC) date.

An Iranian book's entry said at 00:45 Tehran time on 1 Azar 1405 (2026-11-21T21:15Z) was dated 30 Aban —
the UTC day — so it landed in the previous Jalali month. The day is taken in the company's timezone
(from its locale); a book with no country keeps the server's date.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import sys
import uuid

import pytest

INSTANT = _dt.datetime(2026, 11, 21, 21, 15, tzinfo=_dt.timezone.utc)   # server clock (UTC)
UTC_DAY = _dt.date(2026, 11, 21)                                        # 30 Aban 1405
TEHRAN_DAY = _dt.date(2026, 11, 22)                                     # 1 Azar 1405

_RealDate, _RealDatetime = _dt.date, _dt.datetime


class _FrozenDate(_RealDate):
    @classmethod
    def today(cls):                     # a server running in UTC
        return _RealDate(INSTANT.year, INSTANT.month, INSTANT.day)


class _FrozenDatetime(_RealDatetime):
    @classmethod
    def now(cls, tz=None):
        return INSTANT.astimezone(tz) if tz is not None else INSTANT.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return INSTANT.replace(tzinfo=None)

    @classmethod
    def today(cls):
        return INSTANT.replace(tzinfo=None)


@pytest.fixture()
def frozen_clock(monkeypatch):
    for name, mod in list(sys.modules.items()):
        if not (name == "app" or name.startswith("app.")) or mod is None:
            continue
        for attr, val in list(vars(mod).items()):
            if val is _RealDate:
                monkeypatch.setattr(mod, attr, _FrozenDate)
            elif val is _RealDatetime:
                monkeypatch.setattr(mod, attr, _FrozenDatetime)
    yield


@pytest.fixture()
def books(db):
    from app.services.ai_eval.fixture import seed_personal_books
    from tests.test_admin_audit import _purge_company
    ctx = seed_personal_books(db, today=_dt.date(2026, 11, 15))
    yield ctx
    _purge_company(db, ctx.company_id)


def _set_locale(db, books, locale):
    from app.models import Company
    db.get(Company, uuid.UUID(books.company_id)).locale = locale
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
        _set_locale(db, books, "default")           # no country → the server's own date, as before
        assert book_today(db) == UTC_DAY


def test_no_day_said_after_midnight_in_tehran_takes_the_local_day(db, books, frozen_clock):
    _set_locale(db, books, "ir")
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
