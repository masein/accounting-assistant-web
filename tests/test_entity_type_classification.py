"""Freelancer/contractor paid for services → SUPPLIER, not EMPLOYEE.

Deterministic classification (overrides the model's non-deterministic type pick)
at every chat entity-creation point: the standalone propose_create_entity, the
propose_create_transaction.new_entities fold-in, and the orchestrator merge.
Includes the expense-account contradiction guard, on UK + Iran seeds.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.services.ai_accountant.base import ToolContext
from app.services.ai_accountant.entity_create import classify_entity_type
from app.services.ai_accountant.proposal_tools import (
    ProposeCreateEntity,
    ProposeCreateEntityInput,
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


# ─── pure classifier ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Nina is a new freelancer", "Dan is a contractor", "hired a subcontractor",
    "she's a sub-contractor", "a consultant we use", "he is self-employed",
    "a sole trader", "working on a contract", "brought in for a project",
])
def test_service_party_classified_supplier(text):
    # Even when the model wrongly says "employee".
    assert classify_entity_type("employee", text=text) == "supplier"


@pytest.mark.parametrize("text", [
    "add Priya as a new employee", "on £30k salary", "paid June wages to staff",
    "put her on payroll", "PAYE deductions",
])
def test_genuine_employment_classified_employee(text):
    assert classify_entity_type("employee", text=text) == "employee"


def test_employee_pick_on_non_staff_posting_coerced_supplier():
    # No employment words + the entry doesn't debit a staff-cost account.
    assert classify_entity_type("employee", text="paid her 650", staff_cost=False) == "supplier"
    # With a staff-cost posting it stays employee.
    assert classify_entity_type("employee", text="paid her 650", staff_cost=True) == "employee"


def test_other_types_unchanged():
    assert classify_entity_type("client", text="add Acme as a client") == "client"
    assert classify_entity_type("bank", text="opened account at Barclays") == "bank"
    assert classify_entity_type("contractor", text="") == "supplier"   # alias still maps


# ─── standalone propose_create_entity ──────────────────────────────────

def test_standalone_freelancer_is_supplier(uk):
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="Nina Hart is a new freelancer; add her")
    out = asyncio.run(ProposeCreateEntity().run(
        ctx, ProposeCreateEntityInput(name="Nina Hart", type="employee")))  # model said employee
    assert out["new_entities"][0]["type"] == "supplier"
    assert "supplier" in out["summary"]


def test_standalone_real_employee_stays_employee(uk):
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="add Priya as a new employee on a 30k salary")
    out = asyncio.run(ProposeCreateEntity().run(
        ctx, ProposeCreateEntityInput(name="Priya", type="employee")))
    assert out["new_entities"][0]["type"] == "employee"


# ─── combined propose_create_transaction.new_entities ──────────────────

@pytest.mark.parametrize("fixture,expense,bank", [("uk", "7800", "1200"), ("ir", "6112", "1110")])
def test_combined_freelancer_folded_as_supplier(fixture, expense, bank, request):
    db = request.getfixturevalue(fixture)
    ctx = ToolContext(db=db, user_id=USER, username="t",
                      user_message="Nina Hart is a new freelancer. I paid her 650 from the bank for photography")
    payload = ProposeCreateTransactionInput(
        date=date(2025, 6, 18).isoformat(),
        description="Paid Nina Hart for photography",
        currency="GBP" if fixture == "uk" else "IRR",
        lines=[
            {"account_code": expense, "debit": 650, "credit": 0},
            {"account_code": bank, "debit": 0, "credit": 650},
        ],
        new_entities=[{"name": "Nina Hart", "type": "employee", "role": "employee"}],  # model said employee
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    assert out["new_entities"][0]["type"] == "supplier"
    assert "Will create supplier: Nina Hart" in out["summary"]
    # Persisted type is corrected too, so Confirm creates a supplier.
    assert out["preview"]["new_entities"][0]["type"] == "supplier"


def test_combined_contradiction_guard_no_freelancer_word(uk):
    # No freelancer language, but the entry debits Professional fees (7800) — an
    # employee wage payment would debit staff costs (70xx), so coerce supplier.
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="I paid Max 400 from the bank")
    payload = ProposeCreateTransactionInput(
        date=date(2025, 6, 18).isoformat(), description="Paid Max", currency="GBP",
        lines=[
            {"account_code": "7800", "debit": 400, "credit": 0},
            {"account_code": "1200", "debit": 0, "credit": 400},
        ],
        new_entities=[{"name": "Max Doe", "type": "employee", "role": "employee"}],
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    assert out["new_entities"][0]["type"] == "supplier"


def test_combined_real_employee_wage_stays_employee(uk):
    # Staff-cost posting (7000) + employment language → stays employee.
    ctx = ToolContext(db=uk, user_id=USER, username="t",
                      user_message="paid June wages to our employee Priya from the bank")
    payload = ProposeCreateTransactionInput(
        date=date(2025, 6, 18).isoformat(), description="June wages — Priya", currency="GBP",
        lines=[
            {"account_code": "7000", "debit": 2000, "credit": 0},
            {"account_code": "1200", "debit": 0, "credit": 2000},
        ],
        new_entities=[{"name": "Priya Singh", "type": "employee", "role": "employee"}],
    )
    out = asyncio.run(ProposeCreateTransaction().run(ctx, payload))
    assert out["new_entities"][0]["type"] == "employee"
