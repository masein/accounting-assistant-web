"""Time-based billing: rate precedence, aggregation/grouping, invoice journal
balance incl VAT, no-double-billing, void→unbill, multi-currency guard, and
locking of invoiced time — on UK + Iran seeds.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.invoices import void_invoice
from app.api.time_tracking import (
    TimeEntryCreate,
    TimeEntryUpdate,
    create_entry,
    update_entry,
)
from app.db.base import Base
from app.db.seed import SEED_ACCOUNTS, UK_SEED_ACCOUNTS, _parent_code_ir, _parent_code_uk
from app.models.account import Account
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.time_billing import Project, TimeEntry
from app.models.transaction import TransactionLine
from app.services import time_billing_service as tbs
from app.services.locale_service import set_reporting_locale
from app.services.tax_rate_service import seed_tax_rates


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
    seed_tax_rates(db)
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


def _client(db, name="Acme Group"):
    e = Entity(type="client", name=name)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _employee(db, name="Sarah Lee"):
    e = Entity(type="employee", name=name)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _project(db, client, name="OTL"):
    p = Project(client_id=client.id, name=name, status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _log(db, emp, client, hours, *, project=None, when=date(2026, 6, 10), billable=True, desc="work"):
    return create_entry(TimeEntryCreate(
        employee_id=emp.id, client_id=client.id,
        project_id=(project.id if project else None), work_date=when,
        hours=hours, description=desc, billable=billable,
    ), db)


# ─── 1. Rate precedence: project > client > default > profile ──────────

def test_rate_precedence(uk):
    emp = _employee(uk)
    c = _client(uk)
    c2 = _client(uk, "Beta Ltd")
    p = _project(uk, c)
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=50)                       # default
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=60, client_id=c.id)       # client
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=90, project_id=p.id)      # project
    uk.commit()
    assert tbs.resolve_billable_rate(uk, emp.id, c.id, p.id)["rate"] == 90       # project wins
    assert tbs.resolve_billable_rate(uk, emp.id, c.id, None)["rate"] == 60       # client
    assert tbs.resolve_billable_rate(uk, emp.id, c2.id, None)["rate"] == 50      # default override


def test_profile_billable_rate_fallback(uk):
    emp = _employee(uk, "Bob")
    uk.add(EmployeePayProfile(entity_id=emp.id, pay_type="salaried", billable_rate=70, currency="GBP"))
    uk.commit()
    c = _client(uk)
    r = tbs.resolve_billable_rate(uk, emp.id, c.id, None)
    assert r["rate"] == 70 and r["source"] == "profile"


def test_set_default_rate_writes_profile_when_present(uk):
    emp = _employee(uk, "Cara")
    uk.add(EmployeePayProfile(entity_id=emp.id, pay_type="salaried", currency="GBP"))
    uk.commit()
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=80)
    uk.commit()
    prof = uk.execute(select(EmployeePayProfile).where(EmployeePayProfile.entity_id == emp.id)).scalar_one()
    assert float(prof.billable_rate) == 80


# ─── 2. Aggregation + grouping ─────────────────────────────────────────

def test_preview_groups_by_project_then_employee(uk):
    emp = _employee(uk)
    c = _client(uk)
    p = _project(uk, c, "OTL")
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=90, project_id=p.id)
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=50)
    uk.commit()
    _log(uk, emp, c, 2.5, project=p, desc="dashboard")
    _log(uk, emp, c, 1.5, project=p, desc="api")
    _log(uk, emp, c, 2.0)  # general (no project) → default rate 50

    pv = tbs.build_preview(uk, client_id=c.id)
    by_name = {g["project_name"]: g for g in pv["groups"]}
    assert by_name["OTL"]["lines"][0]["hours"] == 4.0
    assert by_name["OTL"]["lines"][0]["amount"] == 360          # 4 × 90
    assert by_name["General"]["lines"][0]["amount"] == 100      # 2 × 50
    assert pv["subtotal"] == 460
    assert pv["entry_count"] == 3 and pv["total_hours"] == 6.0


def test_non_billable_excluded(uk):
    emp = _employee(uk)
    c = _client(uk)
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=50)
    uk.commit()
    _log(uk, emp, c, 3, billable=False)
    pv = tbs.build_preview(uk, client_id=c.id)
    assert pv["empty"] is True


# ─── 3. Invoice journal balance incl VAT + no double-billing ───────────

@pytest.mark.parametrize("fixture,ar,rev,vat,cur", [
    ("uk", "1100", "4000", "2200", "GBP"),
    ("ir", "1112", "4110", "2130", "IRR"),
])
def test_invoice_from_time_balances_with_vat(fixture, ar, rev, vat, cur, request):
    db = request.getfixturevalue(fixture)
    emp = _employee(db)
    c = _client(db)
    p = _project(db, c)
    tbs.set_billable_rate(db, employee_id=emp.id, rate=100, project_id=p.id, currency=cur)
    db.commit()
    _log(db, emp, c, 4, project=p)

    inv, preview = tbs.create_invoice_from_time(db, client_id=c.id)
    # 4 × 100 = 400 subtotal; VAT 20% (uk) / 9% (ir).
    assert preview["subtotal"] == 400
    assert inv.amount == preview["total"]
    assert inv.currency == cur

    lines = db.execute(
        select(TransactionLine).where(TransactionLine.transaction_id == inv.transaction_id)
    ).scalars().all()
    assert sum(li.debit for li in lines) == sum(li.credit for li in lines)   # balanced incl VAT

    def leg(code):
        acc = db.execute(select(Account).where(Account.code == code)).scalar_one()
        ls = [li for li in lines if li.account_id == acc.id]
        return sum(li.debit for li in ls), sum(li.credit for li in ls)

    assert leg(ar) == (preview["total"], 0)     # DR trade debtors gross
    assert leg(rev) == (0, 400)                  # CR sales net
    assert leg(vat)[1] == preview["tax"]         # CR output VAT
    assert preview["tax"] > 0

    # Entries are now invoiced and excluded from re-invoicing (no double-bill).
    entries = db.execute(select(TimeEntry).where(TimeEntry.client_id == c.id)).scalars().all()
    assert all(e.status == "invoiced" and e.invoice_id == inv.id for e in entries)
    again = tbs.build_preview(db, client_id=c.id)
    assert again["empty"] is True


def test_void_returns_time_to_unbilled(uk):
    emp = _employee(uk)
    c = _client(uk)
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=75)
    uk.commit()
    _log(uk, emp, c, 2)
    inv, _ = tbs.create_invoice_from_time(uk, client_id=c.id)
    assert uk.execute(select(func.count()).select_from(TimeEntry)
                      .where(TimeEntry.status == "invoiced")).scalar() == 1

    void_invoice(inv.id, uk)
    e = uk.execute(select(TimeEntry)).scalars().one()
    assert e.status == "unbilled" and e.invoice_id is None and e.rate_snapshot is None
    # The invoice journal was reversed too (net zero on the AR account).
    assert uk.get(Invoice, inv.id).status == "voided"


# ─── 4. Multi-currency guard + locking ─────────────────────────────────

def test_multi_currency_blocked(uk):
    e1 = _employee(uk, "GBP worker")
    e2 = _employee(uk, "EUR worker")
    c = _client(uk)
    tbs.set_billable_rate(uk, employee_id=e1.id, rate=50, currency="GBP")
    tbs.set_billable_rate(uk, employee_id=e2.id, rate=60, currency="EUR")
    uk.commit()
    _log(uk, e1, c, 2)
    _log(uk, e2, c, 2)
    with pytest.raises(tbs.TimeBillingError):
        tbs.build_preview(uk, client_id=c.id)


def test_invoiced_entry_is_locked(uk):
    emp = _employee(uk)
    c = _client(uk)
    tbs.set_billable_rate(uk, employee_id=emp.id, rate=75)
    uk.commit()
    _log(uk, emp, c, 2)
    inv, _ = tbs.create_invoice_from_time(uk, client_id=c.id)
    entry = uk.execute(select(TimeEntry)).scalars().one()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        update_entry(entry.id, TimeEntryUpdate(hours=99), uk)
    assert ei.value.status_code == 409
