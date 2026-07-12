"""Part A — pay hourly employees from tracked hours (monthly).

Covers the acceptance list: hours-derived gross (regular + monthly overtime +
paid leave) feeding the existing tax→net→GL flow with a balanced journal;
single-entry dual settlement (invoiced to the client AND paid to the employee,
counted once on each side); posting locks entries / voiding unlocks + reverses;
unpaid entries pay nothing; salaried runs unchanged.
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
    hours_summary,
    post_run,
    upsert_profile,
    void_run,
)
from app.db.base import Base
from app.db.seed import UK_SEED_ACCOUNTS, _parent_code_uk
from app.models.account import Account
from app.models.entity import Entity
from app.models.time_billing import TimeEntry
from app.models.transaction import Transaction, TransactionLine
from app.services import payroll_service
from app.services.locale_service import set_reporting_locale
from app.services.payroll_service import PayrollInputError
from fastapi import HTTPException

JAN_START, JAN_END, PAY_DATE = date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1)


def _make_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    by_code: dict[str, Account] = {}
    for code, name, level in UK_SEED_ACCOUNTS:
        acc = Account(code=code, name=name, level=level)
        db.add(acc)
        by_code[code] = acc
    db.flush()
    for code, _n, _l in UK_SEED_ACCOUNTS:
        p = _parent_code_uk(code)
        if p and p in by_code:
            by_code[code].parent_id = by_code[p].id
    set_reporting_locale(db, "uk")
    db.commit()
    return db


@pytest.fixture
def db():
    s = _make_session()
    try:
        yield s
    finally:
        s.close()


def _employee(db, name="Hana Hourly") -> Entity:
    e = Entity(name=name, type="employee")
    db.add(e)
    db.flush()
    return e


def _client(db, name="Client Co") -> Entity:
    e = Entity(name=name, type="client")
    db.add(e)
    db.flush()
    return e


def _hourly_profile(db, ent, rate=20, monthly=160, mult=1.5, tax=0.10):
    return upsert_profile(PayProfileUpsert(
        entity_id=ent.id, pay_type="hourly", hourly_rate=rate,
        standard_hours=40, monthly_standard_hours=monthly,
        overtime_multiplier=mult, income_tax_rate=tax,
    ), db)


def _log(db, ent, day, hours, *, entry_type="work", payable=None, client=None, billable=False):
    from app.services.time_billing_service import default_payable
    e = TimeEntry(
        employee_id=ent.id, client_id=(client.id if client else None),
        work_date=date(2026, 1, day), hours=hours, entry_type=entry_type,
        payable=(payable if payable is not None else default_payable(entry_type)),
        billable=billable, status="unbilled", payroll_status="unpaid",
    )
    db.add(e)
    db.flush()
    return e


def _run_jan(db, employees=None):
    return create_run(PayRunCreate(
        period_start=JAN_START, period_end=JAN_END, pay_date=PAY_DATE,
        employees=employees,
    ), db)


# ---------------------------------------------------------------------------
# Pure maths: leave in calculate()
# ---------------------------------------------------------------------------

def test_calculate_hourly_with_leave_and_overtime():
    c = payroll_service.calculate(
        pay_type="hourly", hourly_rate=20, standard_hours=160,
        overtime_multiplier=1.5, hours=170, leave_hours=8,
    )
    # regular 160·20 + OT 10·20·1.5 + leave 8·20 = 3200 + 300 + 160
    assert (c.hours, c.overtime_hours, c.leave_hours) == (160, 10, 8)
    assert c.gross == 3200 + 300 + 160


def test_calculate_full_leave_month_is_paid():
    c = payroll_service.calculate(
        pay_type="hourly", hourly_rate=20, standard_hours=160, hours=0, leave_hours=40,
    )
    assert c.gross == 800 and c.leave_hours == 40


def test_calculate_zero_everything_rejected():
    with pytest.raises(PayrollInputError):
        payroll_service.calculate(pay_type="hourly", hourly_rate=20, standard_hours=160, hours=0)


# ---------------------------------------------------------------------------
# Hours-derived run: gross, overtime, leave, undertime, balanced GL
# ---------------------------------------------------------------------------

def test_run_derives_gross_from_tracked_hours_with_monthly_overtime(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, mult=1.5, tax=0.10)
    # 168 worked in 4 chunks + 8h paid leave → reg 160, OT 8, leave 8
    for day, h in ((5, 40), (12, 40), (19, 40), (26, 48)):
        _log(db, ent, day, h)
    _log(db, ent, 28, 8, entry_type="leave")
    run = _run_jan(db)

    line = run["lines"][0]
    assert line["hours"] == 160 and line["overtime_hours"] == 8 and line["leave_hours"] == 8
    expected_gross = 160 * 20 + int(8 * 20 * 1.5) + 8 * 20  # 3200+240+160
    assert line["gross"] == expected_gross
    assert line["income_tax"] == int(expected_gross * 0.10 + 0.5)
    assert run["total_gross"] == expected_gross

    # Entries are linked to the run (excluded from other runs), not yet locked.
    entries = db.execute(select(TimeEntry)).scalars().all()
    assert all(str(e.payroll_run_id) == run["id"] for e in entries if e.payable)
    assert all(e.payroll_status == "unpaid" for e in entries)

    # Post → balanced journal + entries locked 'paid'.
    posted = post_run(UUID(run["id"]), db)
    assert posted["status"] == "posted"
    txn = db.execute(select(Transaction)).scalars().all()[-1]
    lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn.id)).scalars().all()
    assert sum(l.debit for l in lines) == sum(l.credit for l in lines) == expected_gross
    assert all(e.payroll_status == "paid" for e in db.execute(select(TimeEntry)).scalars().all() if e.payable)


def test_undertime_reported_and_paid_as_worked(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    _log(db, ent, 5, 100)  # 60h under required
    rows = hours_summary(JAN_START, JAN_END, None, db)
    assert rows[0]["undertime_hours"] == 60 and rows[0]["overtime_hours"] == 0
    run = _run_jan(db)
    assert run["lines"][0]["gross"] == 100 * 20  # pay what was worked


def test_unpaid_entry_type_pays_nothing_and_auto_skip(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160)
    _log(db, ent, 5, 10, entry_type="unpaid")
    # auto-included run: nothing payable anywhere → 422 (nobody to pay)
    with pytest.raises(HTTPException) as ei:
        _run_jan(db)
    assert ei.value.status_code == 422


def test_explicitly_listed_employee_with_no_hours_is_422(db):
    ent = _employee(db)
    _hourly_profile(db, ent)
    with pytest.raises(HTTPException) as ei:
        _run_jan(db, employees=[PayRunEmployeeInput(entity_id=ent.id)])
    assert ei.value.status_code == 422
    assert "no payable tracked hours" in ei.value.detail


def test_explicit_manual_hours_still_win(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    _log(db, ent, 5, 999)  # tracked time exists but the manual input wins
    run = _run_jan(db, employees=[PayRunEmployeeInput(entity_id=ent.id, hours=10)])
    assert run["lines"][0]["gross"] == 200
    # manual path leaves the tracked entry untouched
    e = db.execute(select(TimeEntry)).scalars().one()
    assert e.payroll_run_id is None


# ---------------------------------------------------------------------------
# Dual settlement + double-count protection
# ---------------------------------------------------------------------------

def test_same_entry_pays_employee_and_bills_client_once_each(db):
    from app.services.time_billing_service import create_invoice_from_time, payroll_hours_summary
    ent = _employee(db)
    cli = _client(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    # billable client work — also payable to the employee
    e = _log(db, ent, 5, 10, client=cli, billable=True)
    # give the worker a billable rate so invoicing works
    from app.services.time_billing_service import set_billable_rate
    set_billable_rate(db, employee_id=ent.id, rate=50)
    inv = create_invoice_from_time(db, client_id=cli.id, invoice_date=date(2026, 1, 31))
    db.refresh(e)
    assert e.status == "invoiced" and e.invoice_id is not None      # billed once
    assert e.payroll_status == "unpaid" and e.payroll_run_id is None  # not yet paid

    run = _run_jan(db)  # payroll still picks it up — independent settlement
    assert run["lines"][0]["gross"] == 200
    db.refresh(e)
    assert str(e.payroll_run_id) == run["id"] and e.status == "invoiced"

    # a second run for the same period finds nothing (no double pay)
    s = payroll_hours_summary(db, ent.id, JAN_START, JAN_END)
    assert s["worked_hours"] == 0 and s["entry_count"] == 0


def test_internal_nonbillable_work_still_pays(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    _log(db, ent, 5, 10)  # no client, not billable
    run = _run_jan(db)
    assert run["lines"][0]["gross"] == 200


# ---------------------------------------------------------------------------
# Lock / void
# ---------------------------------------------------------------------------

def test_posted_run_locks_entries_and_void_unlocks_with_reversal(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    e = _log(db, ent, 5, 100)
    run = _run_jan(db)
    post_run(UUID(run["id"]), db)
    db.refresh(e)
    assert e.payroll_status == "paid"

    n_txn_before = len(db.execute(select(Transaction)).scalars().all())
    voided = void_run(UUID(run["id"]), db)
    assert voided["status"] == "voided"
    db.refresh(e)
    assert e.payroll_status == "unpaid" and e.payroll_run_id is None  # unlocked

    txns = db.execute(select(Transaction)).scalars().all()
    assert len(txns) == n_txn_before + 1  # a reversing entry was posted
    rev_lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == txns[-1].id)
    ).scalars().all()
    assert sum(l.debit for l in rev_lines) == sum(l.credit for l in rev_lines) == 2000

    # unlocked hours can be re-run
    run2 = _run_jan(db)
    assert run2["lines"][0]["gross"] == 2000


def test_void_draft_unlinks_without_reversal(db):
    ent = _employee(db)
    _hourly_profile(db, ent, rate=20, monthly=160, tax=0)
    e = _log(db, ent, 5, 10)
    run = _run_jan(db)
    n_txn = len(db.execute(select(Transaction)).scalars().all())
    void_run(UUID(run["id"]), db)
    db.refresh(e)
    assert e.payroll_run_id is None
    assert len(db.execute(select(Transaction)).scalars().all()) == n_txn  # no GL activity


# ---------------------------------------------------------------------------
# Salaried unchanged
# ---------------------------------------------------------------------------

def test_salaried_run_is_unaffected_by_time_entries(db):
    ent = _employee(db, "Sam Salaried")
    upsert_profile(PayProfileUpsert(
        entity_id=ent.id, pay_type="salaried", base_salary=5000, income_tax_rate=0.2,
    ), db)
    _log(db, ent, 5, 999)  # stray tracked time must not affect salaried pay
    run = _run_jan(db)
    assert run["lines"][0]["gross"] == 5000
    e = db.execute(select(TimeEntry)).scalars().one()
    assert e.payroll_run_id is None
