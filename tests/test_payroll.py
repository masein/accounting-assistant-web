"""Payroll (test plan §6): gross→net, overtime, pre-tax base reduction,
proration, balanced postings, payslip + year-end tie-out, and edge cases.

Calculation maths is unit-tested pure; the API is driven directly against an
isolated in-memory chart (UK + Iran) so locale-aware accounts resolve and a
seeded locale can't leak into the shared session fixture.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.payroll import (
    PayProfileUpsert,
    PayRunCreate,
    PayRunEmployeeInput,
    create_run,
    get_payslip,
    pay_run,
    post_run,
    upsert_profile,
    year_summary,
)
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.models.transaction import Transaction, TransactionLine
from app.services import payroll_service
from app.services.account_resolver import resolve_account_code
from app.services.locale_service import set_reporting_locale
from app.services.payroll_service import PayrollInputError


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


def _employee(db: Session, name: str = "Alice Patel") -> Entity:
    e = Entity(type="employee", name=name)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _txn_balanced(db: Session, txn_id) -> tuple[int, int]:
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


# ─── 1. Pure calculation (§6.1–6.4, §6.6) ──────────────────────────────

def test_salaried_gross_to_net():
    c = payroll_service.calculate(
        pay_type="salaried", base_salary=5000,
        income_tax_rate=0.2, social_security_rate=0.1, pension_rate=0.05,
    )
    assert c.gross == 5000
    assert c.pre_tax_deductions == 250          # 5% of 5000
    assert c.taxable_base == 4750               # gross − pre-tax
    assert c.income_tax == 950                  # 20% of 4750
    assert c.social_security == 500             # 10% of gross
    assert c.net_pay == 3300                    # 5000 − 250 − 950 − 500
    # Posting splits balance: gross == tax + social + deductions + net.
    assert c.gross == c.income_tax + c.social_security + c.pre_tax_deductions + c.net_pay


def test_hourly_overtime_multiplier():
    c = payroll_service.calculate(
        pay_type="hourly", hourly_rate=20, standard_hours=40, overtime_multiplier=1.5,
        hours=50,
    )
    assert c.hours == 40 and c.overtime_hours == 10
    assert c.gross == 1100                       # 40·20 + 10·20·1.5 = 800 + 300


def test_pre_tax_deduction_reduces_taxable_base():
    no_pension = payroll_service.calculate(pay_type="salaried", base_salary=4000, income_tax_rate=0.25)
    with_pension = payroll_service.calculate(
        pay_type="salaried", base_salary=4000, income_tax_rate=0.25, pension_rate=0.10,
    )
    assert with_pension.taxable_base == 3600     # 4000 − 400
    assert with_pension.income_tax < no_pension.income_tax
    assert with_pension.income_tax == 900        # 25% of 3600


def test_proration_half_period():
    c = payroll_service.calculate(pay_type="salaried", base_salary=5000, proration=0.5)
    assert c.gross == 2500


def test_prorate_raise_day_weighted():
    # 31-day period, raise on day 16: 15 days at 3000, 16 days at 3600.
    gross = payroll_service.prorate_raise(
        date(2025, 1, 1), date(2025, 1, 31), date(2025, 1, 16), 3000, 3600,
    )
    assert gross == round(3000 * 15 / 31 + 3600 * 16 / 31)  # 3310


def test_zero_and_negative_hours_rejected():
    with pytest.raises(PayrollInputError):
        payroll_service.calculate(pay_type="hourly", hourly_rate=20, hours=0)
    with pytest.raises(PayrollInputError):
        payroll_service.calculate(pay_type="hourly", hourly_rate=20, hours=-5)


# ─── 2. End-to-end run + balanced posting (UK + Iran) ──────────────────

@pytest.mark.parametrize("fixture,wages,paye,social,ded,net,bank", [
    ("uk", "7100", "2211", "2212", "2260", "2250", "1200"),
    ("ir", "6110", "2160", "2170", "2190", "2180", "1110"),
])
def test_run_posts_balanced_and_payslip_ties(fixture, wages, paye, social, ded, net, bank, request):
    db = request.getfixturevalue(fixture)
    emp = _employee(db)
    upsert_profile(PayProfileUpsert(
        entity_id=emp.id, pay_type="salaried", base_salary=5000,
        income_tax_rate=0.2, social_security_rate=0.1, pension_rate=0.05,
    ), db)

    run = create_run(PayRunCreate(
        period_start=date(2025, 6, 1), period_end=date(2025, 6, 30), pay_date=date(2025, 6, 30),
    ), db)
    assert run["total_gross"] == 5000 and run["total_net"] == 3300
    assert run["status"] == "draft"

    run_id = UUID(run["id"])
    posted = post_run(run_id, db)
    assert posted["status"] == "posted"
    txn_id = posted["post_transaction_id"]
    dr, cr = _txn_balanced(db, txn_id)
    assert dr == cr == 5000                                  # balanced
    assert _leg(db, txn_id, wages) == (5000, 0)              # DR gross
    assert _leg(db, txn_id, paye) == (0, 950)               # CR income tax
    assert _leg(db, txn_id, social) == (0, 500)             # CR social
    assert _leg(db, txn_id, ded) == (0, 250)                # CR pre-tax deductions
    assert _leg(db, txn_id, net) == (0, 3300)               # CR net pay payable

    paid = pay_run(run_id, None, db)
    assert paid["status"] == "paid"
    pay_txn = paid["pay_transaction_id"]
    pdr, pcr = _txn_balanced(db, pay_txn)
    assert pdr == pcr == 3300
    assert _leg(db, pay_txn, net) == (3300, 0)              # DR clear payable
    assert _leg(db, pay_txn, bank) == (0, 3300)            # CR bank

    # Payslip ties to the line and the posting.
    slip = get_payslip(run_id, emp.id, db)
    assert slip["gross"] == 5000 and slip["income_tax"] == 950
    assert slip["net_pay"] == 3300

    # Year summary ties to the run line.
    ys = year_summary(2025, emp.id, db)
    assert len(ys["employees"]) == 1
    agg = ys["employees"][0]
    assert agg["gross"] == 5000 and agg["net_pay"] == 3300 and agg["runs"] == 1


def test_hourly_run_overtime_end_to_end(uk):
    emp = _employee(db=uk, name="Bob Chen")
    upsert_profile(PayProfileUpsert(
        entity_id=emp.id, pay_type="hourly", hourly_rate=20, standard_hours=40,
        overtime_multiplier=1.5, income_tax_rate=0.2,
    ), uk)
    run = create_run(PayRunCreate(
        period_start=date(2025, 6, 1), period_end=date(2025, 6, 7), pay_date=date(2025, 6, 7),
        employees=[PayRunEmployeeInput(entity_id=emp.id, hours=50)],
    ), uk)
    line = run["lines"][0]
    assert line["overtime_hours"] == 10
    assert line["gross"] == 1100
    assert line["income_tax"] == 220                        # 20% of 1100 (no pre-tax)
    posted = post_run(UUID(run["id"]), uk)
    dr, cr = _txn_balanced(uk, posted["post_transaction_id"])
    assert dr == cr == 1100


def test_mid_period_raise_prorates_in_run(uk):
    emp = _employee(db=uk, name="Carol Raise")
    upsert_profile(PayProfileUpsert(entity_id=emp.id, pay_type="salaried", base_salary=3600), uk)
    gross = payroll_service.prorate_raise(
        date(2025, 6, 1), date(2025, 6, 30), date(2025, 6, 16), 3000, 3600,
    )
    run = create_run(PayRunCreate(
        period_start=date(2025, 6, 1), period_end=date(2025, 6, 30), pay_date=date(2025, 6, 30),
        employees=[PayRunEmployeeInput(entity_id=emp.id, gross_override=gross)],
    ), uk)
    assert run["lines"][0]["gross"] == gross
    assert run["total_gross"] == gross


def test_zero_hours_run_rejected(uk):
    emp = _employee(db=uk, name="Dan Zero")
    upsert_profile(PayProfileUpsert(
        entity_id=emp.id, pay_type="hourly", hourly_rate=20, standard_hours=40,
    ), uk)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        create_run(PayRunCreate(
            period_start=date(2025, 6, 1), period_end=date(2025, 6, 7), pay_date=date(2025, 6, 7),
            employees=[PayRunEmployeeInput(entity_id=emp.id, hours=0)],
        ), uk)
    assert ei.value.status_code == 422


def test_payroll_accounts_resolve_both_locales(uk, ir):
    for db in (uk, ir):
        for key in ("wages_expense", "paye_payable", "social_security_payable",
                    "net_pay_payable", "payroll_deductions_payable"):
            code = resolve_account_code(db, key)
            assert db.execute(select(Account).where(Account.code == code)).scalar_one_or_none()
