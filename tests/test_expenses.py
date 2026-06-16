"""Mileage claims & expense approval workflow (test plan §5.4, §5.5).

Driven directly against an isolated in-memory chart (UK + Iran) so locale-aware
accounts resolve. Covers mileage math, threshold routing, approve/reject
posting, and confirm-gated reimbursement — all double-entry balanced.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.expenses import (
    ExpenseSettingsPayload,
    MileageCreate,
    approve_expense,
    create_mileage,
    reimburse_expense,
    reject_expense,
    write_settings,
)
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.services.account_resolver import resolve_account_code
from app.services.locale_service import set_reporting_locale


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


def _employee(db: Session, name="Alice Patel") -> Entity:
    e = Entity(type="employee", name=name)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _balanced(db: Session, txn_id) -> tuple[int, int]:
    txn_id = UUID(str(txn_id))
    lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txn_id)
    ).scalars().all()
    return sum(li.debit for li in lines), sum(li.credit for li in lines)


def _leg(db: Session, txn_id, code: str) -> tuple[int, int]:
    txn_id = UUID(str(txn_id))
    acc = db.execute(select(Account).where(Account.code == code)).scalar_one()
    lines = db.execute(
        select(TransactionLine).where(
            TransactionLine.transaction_id == txn_id, TransactionLine.account_id == acc.id
        )
    ).scalars().all()
    return sum(li.debit for li in lines), sum(li.credit for li in lines)


def _txn_count(db: Session) -> int:
    return db.execute(select(func.count(Transaction.id))).scalar()


# ─── 1. Mileage math + immediate posting below threshold (§5.4) ────────

@pytest.mark.parametrize("fixture,mileage,payable,bank", [
    ("uk", "7400", "2270", "1200"),
    ("ir", "6130", "2195", "1110"),
])
def test_mileage_posts_balanced_below_threshold(fixture, mileage, payable, bank, request):
    db = request.getfixturevalue(fixture)
    write_settings(ExpenseSettingsPayload(mileage_rate=0.45, mileage_unit="mile",
                                          approval_threshold=1000), db)
    emp = _employee(db)
    res = create_mileage(MileageCreate(
        entity_id=emp.id, claim_date=date(2025, 6, 10), distance=100,
    ), db)
    # 100 × 0.45 = 45, rounded.
    assert res["amount"] == 45
    assert res["needs_approval"] is False
    assert res["status"] == "approved"
    txn = res["transaction_id"]
    assert txn is not None
    assert _balanced(db, txn) == (45, 45)
    assert _leg(db, txn, mileage) == (45, 0)      # DR mileage expense
    assert _leg(db, txn, payable) == (0, 45)      # CR employee expenses payable

    # Reimbursement is a separate, confirm-gated step — not auto-paid.
    assert res["reimbursement_transaction_id"] is None
    paid = reimburse_expense(UUID(res["id"]), None, db)
    assert paid["status"] == "reimbursed"
    pay_txn = paid["reimbursement_transaction_id"]
    assert _balanced(db, pay_txn) == (45, 45)
    assert _leg(db, pay_txn, payable) == (45, 0)  # DR clear payable
    assert _leg(db, pay_txn, bank) == (0, 45)    # CR bank


# ─── 2. Threshold routing (§5.5) ───────────────────────────────────────

def test_above_threshold_routes_not_posted_then_approve(uk):
    write_settings(ExpenseSettingsPayload(mileage_rate=1, approval_threshold=100), uk)
    emp = _employee(uk)
    res = create_mileage(MileageCreate(
        entity_id=emp.id, claim_date=date(2025, 6, 10), distance=250, rate=1,
    ), uk)
    assert res["amount"] == 250
    assert res["needs_approval"] is True
    assert res["status"] == "pending_approval"
    assert res["transaction_id"] is None
    assert _txn_count(uk) == 0                     # routed, nothing posted

    approved = approve_expense(UUID(res["id"]), "manager@co", uk)
    assert approved["status"] == "approved"
    assert approved["decided_by"] == "manager@co"  # audit trail records approver
    assert approved["decided_at"] is not None
    assert approved["transaction_id"] is not None
    assert _balanced(uk, approved["transaction_id"]) == (250, 250)


def test_below_threshold_posts_normally(uk):
    write_settings(ExpenseSettingsPayload(mileage_rate=1, approval_threshold=1000), uk)
    emp = _employee(uk)
    res = create_mileage(MileageCreate(
        entity_id=emp.id, claim_date=date(2025, 6, 10), distance=50, rate=1,
    ), uk)
    assert res["needs_approval"] is False
    assert res["status"] == "approved" and res["transaction_id"] is not None


def test_reject_posts_nothing(uk):
    write_settings(ExpenseSettingsPayload(mileage_rate=1, approval_threshold=100), uk)
    emp = _employee(uk)
    res = create_mileage(MileageCreate(
        entity_id=emp.id, claim_date=date(2025, 6, 10), distance=300, rate=1,
    ), uk)
    rejected = reject_expense(UUID(res["id"]), "manager@co", uk)
    assert rejected["status"] == "rejected"
    assert rejected["decided_by"] == "manager@co"
    assert rejected["transaction_id"] is None
    assert _txn_count(uk) == 0

    # A rejected claim can't be reimbursed.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        reimburse_expense(UUID(res["id"]), None, uk)
    assert ei.value.status_code == 409


def test_threshold_zero_means_always_post(ir):
    write_settings(ExpenseSettingsPayload(mileage_rate=5000, approval_threshold=0), ir)
    emp = _employee(ir)
    res = create_mileage(MileageCreate(
        entity_id=emp.id, claim_date=date(2025, 6, 10), distance=10, unit="km",
    ), ir)
    assert res["amount"] == 50000
    assert res["needs_approval"] is False
    assert res["status"] == "approved" and res["transaction_id"] is not None


def test_mileage_accounts_resolve_both_locales(uk, ir):
    for db in (uk, ir):
        for key in ("mileage_expense", "expenses_payable"):
            code = resolve_account_code(db, key)
            assert db.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
