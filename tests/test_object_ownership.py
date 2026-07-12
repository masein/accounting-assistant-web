"""Object-level ownership — a self-service Employee sees only their OWN records.

An Employee (role with only the *_OWN permissions) may read/act on their own
payslip / expenses / time; anyone else's returns 404. Roles with the full read
permission (owner/cfo/accountant) see everything in the company.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.core.permissions import Role
from app.models.company import Company
from app.models.entity import Entity
from app.models.mileage_claim import MileageClaim
from app.models.pay_run import PayRun, PayRunLine
from app.models.time_billing import TimeEntry
from tests.conftest import _CSRFTestClient


def _company(db):
    c = Company(id=uuid.uuid4(), name="Co", slug=f"co-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status="active", token_version=0)
    db.add(c); db.flush()
    return c


def _emp(db, company, name):
    e = Entity(id=uuid.uuid4(), name=name, type="employee", company_id=company.id)
    db.add(e); db.flush()
    return e


def _token(client, role, company, entity_id=None):
    tok = create_session_token(
        user_id=str(uuid.uuid4()), username=role, is_admin=(role == Role.OWNER),
        company_id=str(company.id), role=role,
        entity_id=str(entity_id) if entity_id else None,
    )
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def world(db):
    """A company with two employees (mine, other) and one record each."""
    co = _company(db)
    mine = _emp(db, co, "Mine")
    other = _emp(db, co, "Other")
    client_ent = Entity(id=uuid.uuid4(), name="ClientCo", type="customer", company_id=co.id)
    db.add(client_ent)
    # Pay run with a line for each employee
    run = PayRun(id=uuid.uuid4(), period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                 pay_date=date(2026, 2, 1), currency="GBP", status="posted", company_id=co.id)
    db.add(run); db.flush()
    for ent in (mine, other):
        db.add(PayRunLine(id=uuid.uuid4(), run_id=run.id, entity_id=ent.id,
                          employee_name=ent.name, gross=100000, net_pay=80000, company_id=co.id))
    # A mileage claim + a time entry for each
    for ent in (mine, other):
        db.add(MileageClaim(id=uuid.uuid4(), entity_id=ent.id, employee_name=ent.name,
                            claim_date=date(2026, 1, 15), amount=5000, currency="GBP",
                            status="approved", company_id=co.id))
        db.add(TimeEntry(id=uuid.uuid4(), employee_id=ent.id, client_id=client_ent.id,
                         work_date=date(2026, 1, 10), hours=8, status="unbilled", company_id=co.id))
    db.flush()
    return {"co": co, "mine": mine, "other": other, "client": client_ent, "run": run}


def _claim_id(db, entity_id):
    return db.query(MileageClaim).filter(MileageClaim.entity_id == entity_id).first().id


def _entry_id(db, entity_id):
    return db.query(TimeEntry).filter(TimeEntry.employee_id == entity_id).first().id


# --- Payslip -----------------------------------------------------------------
def test_employee_sees_only_own_payslip(world, db, client):
    api = _token(client, Role.EMPLOYEE, world["co"], entity_id=world["mine"].id)
    run = world["run"].id
    assert api.get(f"/payroll/runs/{run}/payslip/{world['mine'].id}").status_code == 200
    assert api.get(f"/payroll/runs/{run}/payslip/{world['other'].id}").status_code == 404


def test_accountant_sees_any_payslip(world, db, client):
    api = _token(client, Role.ACCOUNTANT, world["co"])
    run = world["run"].id
    assert api.get(f"/payroll/runs/{run}/payslip/{world['mine'].id}").status_code == 200
    assert api.get(f"/payroll/runs/{run}/payslip/{world['other'].id}").status_code == 200


# --- Expenses ----------------------------------------------------------------
def test_employee_expense_list_and_get_scoped(world, db, client):
    api = _token(client, Role.EMPLOYEE, world["co"], entity_id=world["mine"].id)
    rows = api.get("/expenses").json()
    assert {r["entity_id"] for r in rows} == {str(world["mine"].id)}
    assert api.get(f"/expenses/{_claim_id(db, world['mine'].id)}").status_code == 200
    assert api.get(f"/expenses/{_claim_id(db, world['other'].id)}").status_code == 404


def test_accountant_expense_list_sees_all(world, db, client):
    api = _token(client, Role.ACCOUNTANT, world["co"])
    ids = {r["entity_id"] for r in api.get("/expenses").json()}
    assert {str(world["mine"].id), str(world["other"].id)} <= ids


# --- Time --------------------------------------------------------------------
def test_employee_time_scoped(world, db, client):
    api = _token(client, Role.EMPLOYEE, world["co"], entity_id=world["mine"].id)
    rows = api.get("/time/entries").json()
    assert {r["employee_id"] for r in rows} == {str(world["mine"].id)}
    # can't edit someone else's entry
    assert api.patch(f"/time/entries/{_entry_id(db, world['other'].id)}",
                     json={"hours": 1}).status_code == 404
    # can't log time for another employee
    assert api.post("/time/entries", json={
        "employee_id": str(world["other"].id), "client_id": str(world["client"].id),
        "work_date": "2026-01-20", "hours": 3,
    }).status_code == 403
    # can log their own
    assert api.post("/time/entries", json={
        "employee_id": str(world["mine"].id), "client_id": str(world["client"].id),
        "work_date": "2026-01-20", "hours": 3,
    }).status_code == 201


def test_unlinked_employee_sees_nothing(world, db, client):
    # Employee token with no entity link → no own records at all.
    api = _token(client, Role.EMPLOYEE, world["co"], entity_id=None)
    assert api.get("/expenses").json() == []
    assert api.get("/time/entries").json() == []
    assert api.get(f"/payroll/runs/{world['run'].id}/payslip/{world['mine'].id}").status_code == 404
