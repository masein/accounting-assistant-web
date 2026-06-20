"""Direction guard: paying a supplier must CREDIT the bank, never debit it.

Live bug (PR #32 follow-up): a payment linked to a supplier entity posted
reversed (DR bank / CR expense). The server-side guard auto-corrects a
wholesale-reversed payment before it reaches Confirm; a genuine inflow and the
non-supplier control stay correct. Also covers spelled-out relative dates.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.date_resolver import relative_offset_date
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateTransaction,
    ProposeCreateTransactionInput,
)
from app.services.locale_service import set_reporting_locale

USER = "u1"


def _make_session(chart, parent_fn, locale: str) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in chart:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in chart:
        p = parent_fn(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, locale)
    db.commit()
    return db


@pytest.fixture
def uk():
    db = _make_session(UK_SEED_ACCOUNTS, _parent_code_uk, "uk")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def ir():
    db = _make_session(SEED_ACCOUNTS, _parent_code_ir, "ir")
    try:
        yield db
    finally:
        db.close()


def _propose(db, message, lines, *, new_entities=None, currency="GBP"):
    ctx = ToolContext(db=db, user_id=USER, username="t", user_message=message)
    payload = ProposeCreateTransactionInput(
        date=date(2025, 6, 18).isoformat(), description=message,
        currency=currency, lines=lines, new_entities=(new_entities or []),
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    return out["preview"]["lines"]


def _legs(lines):
    """{code: (debit, credit)}"""
    return {ln["account_code"]: (ln["debit"], ln["credit"]) for ln in lines}


# ─── reversed supplier payment gets corrected ──────────────────────────

def test_reversed_payment_to_existing_supplier_corrected(uk):
    db = uk
    db.add(Entity(type="supplier", name="Dan Campbell"))
    db.commit()
    # Model proposes it REVERSED: DR bank / CR purchases.
    lines = _propose(
        db, "I paid Dan Campbell 500 GBP from the bank today",
        [{"account_code": "1200", "debit": 500, "credit": 0},
         {"account_code": "5000", "debit": 0, "credit": 500}],
    )
    legs = _legs(lines)
    assert legs["1200"] == (0, 500)   # bank CREDITED (cash out)
    assert legs["5000"] == (500, 0)   # expense DEBITED


def test_reversed_payment_to_new_supplier_corrected(uk):
    lines = _propose(
        uk, "Dan Campbell is a contractor; I paid him 10000 GBP from the bank",
        [{"account_code": "1200", "debit": 10_000, "credit": 0},
         {"account_code": "5000", "debit": 0, "credit": 10_000}],
        new_entities=[{"name": "Dan Campbell", "type": "supplier", "role": "supplier"}],
    )
    legs = _legs(lines)
    assert legs["1200"] == (0, 10_000)   # bank credited
    assert legs["5000"] == (10_000, 0)


def test_reversed_payment_iran(ir):
    lines = _propose(
        ir, "پرداخت به تأمین‌کننده — I paid supplier 200 from the bank",
        [{"account_code": "1110", "debit": 200, "credit": 0},
         {"account_code": "6112", "debit": 0, "credit": 200}],
        currency="IRR",
    )
    legs = _legs(lines)
    assert legs["1110"] == (0, 200)      # Iran cash credited
    assert legs["6112"] == (200, 0)


# ─── correct entries are left untouched ────────────────────────────────

def test_correct_payment_unchanged(uk):
    lines = _propose(
        uk, "Record a 40 GBP stationery expense paid from the bank today",
        [{"account_code": "7600", "debit": 40, "credit": 0},
         {"account_code": "1200", "debit": 0, "credit": 40}],
    )
    legs = _legs(lines)
    assert legs["7600"] == (40, 0) and legs["1200"] == (0, 40)  # unchanged


def test_genuine_inflow_still_debits_bank(uk):
    # "received from a client" → bank DEBITED. The guard must NOT touch this,
    # even though the model (correctly) debits the bank.
    lines = _propose(
        uk, "received 500 GBP from client Acme into the bank",
        [{"account_code": "1200", "debit": 500, "credit": 0},
         {"account_code": "4000", "debit": 0, "credit": 500}],
    )
    legs = _legs(lines)
    assert legs["1200"] == (500, 0)   # bank stays debited (money in)
    assert legs["4000"] == (0, 500)


def test_refund_inflow_not_flipped(uk):
    # A supplier refund into the bank is an inflow; "refund" excludes outflow.
    lines = _propose(
        uk, "supplier refunded us 75 GBP into the bank",
        [{"account_code": "1200", "debit": 75, "credit": 0},
         {"account_code": "5000", "debit": 0, "credit": 75}],
    )
    assert _legs(lines)["1200"] == (75, 0)   # untouched


# ─── spelled-out relative dates ────────────────────────────────────────

def test_number_word_relative_dates():
    t = date(2026, 6, 20)
    assert relative_offset_date("I paid Dan two days ago", t) == t - timedelta(days=2)
    assert relative_offset_date("spent three days ago", t) == t - timedelta(days=3)
    assert relative_offset_date("paid a day ago", t) == t - timedelta(days=1)
    assert relative_offset_date("two weeks ago", t) == t - timedelta(weeks=2)
    # digit form still works; unrelated text returns None.
    assert relative_offset_date("3 days ago", t) == t - timedelta(days=3)
    assert relative_offset_date("paid Dan Campbell", t) is None
