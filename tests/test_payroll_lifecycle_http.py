"""Payroll over HTTP (roadmap 2026-09 §6, suite 5): profiles → run → post →
pay → payslips and year summary, the state machine (post twice, pay before
post, void a paid run …), the closed period, the employee's own-payslip
scope, and the guard this suite added: a salaried employee can't be put in
two live pay runs for overlapping periods (paid twice for the same month)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.transaction import Transaction, TransactionLine


@pytest.fixture()
def co(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Payroll Co", slug=f"pay-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)

    def session(role="owner", entity_id=None):
        tok = create_session_token(user_id=str(uuid.uuid4()), username=role, is_admin=(role == "owner"),
                                   role=role, company_id=cid, entity_id=entity_id)
        csrf = generate_csrf_token()
        client.cookies.clear()
        client.cookies.set(settings.auth_cookie_name, tok)
        client.cookies.set(CSRF_COOKIE, csrf)
        return _CSRFTestClient(client, csrf)

    db.expunge_all()
    yield session, cid
    client.cookies.clear()
    _purge_company(db, cid)


def _employee(api, name, **profile):
    ent = api.post("/entities", json={"type": "employee", "name": name, "iban": "IR820540102680020817909002",
                                      "bank_name": "Bank Melli"}).json()
    body = {"entity_id": ent["id"], "pay_type": "salaried", "base_salary": 100_000_000,
            "income_tax_rate": 0.1, "social_security_rate": 0.07, **profile}
    r = api.post("/payroll/profiles", json=body)
    assert r.status_code == 201, r.text
    return ent


def _run(api, start="2026-05-22", end="2026-06-21", pay="2026-06-21", **extra):
    return api.post("/payroll/runs", json={"period_start": start, "period_end": end, "pay_date": pay, **extra})


def _balanced(db, cid, tid):
    from app.db.tenant import use_company
    with use_company(cid):
        tid = uuid.UUID(str(tid))
        dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
        cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
        return int(dr), int(cr)


def test_profiles_validation(co):
    session, _ = co
    api = session()
    client_ent = api.post("/entities", json={"type": "client", "name": "Not staff"}).json()
    assert api.post("/payroll/profiles", json={"entity_id": client_ent["id"]}).status_code == 422
    assert api.post("/payroll/profiles", json={"entity_id": str(uuid.uuid4())}).status_code == 404
    emp = _employee(api, "Sara")
    assert api.post("/payroll/profiles", json={"entity_id": emp["id"], "pay_type": "weekly"}).status_code == 422
    assert api.post("/payroll/profiles", json={"entity_id": emp["id"], "income_tax_rate": 1.5}).status_code == 422
    up = api.post("/payroll/profiles", json={"entity_id": emp["id"], "base_salary": 120_000_000})
    assert up.status_code == 201 and up.json()["base_salary"] == 120_000_000
    assert [p["employee_name"] for p in api.get("/payroll/profiles").json()] == ["Sara"]   # upsert, not a duplicate


def test_full_lifecycle_and_state_machine(co, db):
    session, cid = co
    api = session()
    a = _employee(api, "Ali")
    b = _employee(api, "Babak", base_salary=80_000_000)
    r = _run(api)
    assert r.status_code == 201, r.text
    run = r.json()
    rid = run["id"]
    assert run["status"] == "draft" and len(run["lines"]) == 2
    assert run["total_gross"] == 180_000_000
    assert run["total_net"] == run["total_gross"] - run["total_tax"] - run["total_social"] - run["total_deductions"]
    assert api.get(f"/payroll/runs/{rid}").json()["id"] == rid
    assert any(x["id"] == rid for x in api.get("/payroll/runs").json())

    assert api.post(f"/payroll/runs/{rid}/pay").status_code == 409          # pay before post
    posted = api.post(f"/payroll/runs/{rid}/post")
    assert posted.status_code == 200 and posted.json()["status"] == "posted"
    dr, cr = _balanced(db, cid, posted.json()["post_transaction_id"])
    assert dr == cr == 180_000_000
    assert api.post(f"/payroll/runs/{rid}/post").status_code == 409         # post twice

    paid = api.post(f"/payroll/runs/{rid}/pay")
    assert paid.status_code == 200 and paid.json()["status"] == "paid" and paid.json()["warnings"] == []
    dr, cr = _balanced(db, cid, paid.json()["pay_transaction_id"])
    assert dr == cr == run["total_net"]
    assert all(ln["paid_to"] and "Bank Melli" in ln["paid_to"] for ln in paid.json()["lines"])
    assert api.post(f"/payroll/runs/{rid}/pay").status_code == 409          # pay twice
    assert api.post(f"/payroll/runs/{rid}/void").status_code == 409         # a paid run can't be voided

    slip = api.get(f"/payroll/runs/{rid}/payslip/{a['id']}").json()
    assert slip["employee_name"] == "Ali" and slip["status"] == "paid" and slip["gross"] == 100_000_000
    pdf = api.get(f"/payroll/runs/{rid}/payslip/{a['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    assert api.get(f"/payroll/runs/{rid}/payslip/{uuid.uuid4()}").status_code == 404
    assert api.get(f"/payroll/runs/{uuid.uuid4()}").status_code == 404

    ys = {e["employee_name"]: e for e in api.get("/payroll/year-summary", params={"year": 2026}).json()["employees"]}
    assert ys["Ali"]["gross"] == 100_000_000 and ys["Babak"]["gross"] == 80_000_000 and ys["Ali"]["runs"] == 1


def test_void_a_posted_run_reverses_it_and_frees_the_period(co, db):
    session, cid = co
    api = session()
    _employee(api, "Cyrus")
    run = _run(api).json()
    api.post(f"/payroll/runs/{run['id']}/post")
    before = _txn_count(db, cid)
    v = api.post(f"/payroll/runs/{run['id']}/void")
    assert v.status_code == 200 and v.json()["status"] == "voided"
    assert _txn_count(db, cid) == before + 1                             # a reversing entry, nothing deleted
    assert api.post(f"/payroll/runs/{run['id']}/void").status_code == 409
    assert api.post(f"/payroll/runs/{run['id']}/post").status_code == 409
    assert api.get("/payroll/year-summary", params={"year": 2026}).json()["employees"] == []
    # The period is free again once the run is voided.
    assert _run(api).status_code == 201


def test_overlapping_runs_would_pay_a_salaried_employee_twice(co):
    session, _ = co
    api = session()
    _employee(api, "Dara")
    first = _run(api)
    assert first.status_code == 201
    again = _run(api, start="2026-06-01", end="2026-06-30", pay="2026-06-30")
    assert again.status_code == 409 and "Dara" in again.json()["detail"]
    # An explicitly intended off-cycle run (e.g. a correction) can opt in.
    assert _run(api, start="2026-06-01", end="2026-06-30", pay="2026-06-30", allow_overlap=True).status_code == 201
    # A non-overlapping next month is fine.
    assert _run(api, start="2026-07-01", end="2026-07-31", pay="2026-07-31").status_code == 201


def test_run_validation(co):
    session, _ = co
    api = session()
    assert _run(api).status_code == 422                                  # no profiles yet
    emp = _employee(api, "Elham")
    assert _run(api, start="2026-06-30", end="2026-06-01").status_code == 422
    stranger = api.post("/entities", json={"type": "employee", "name": "No profile"}).json()
    r = _run(api, employees=[{"entity_id": stranger["id"]}])
    assert r.status_code == 422 and stranger["id"] in r.json()["detail"]
    assert _run(api, employees=[{"entity_id": emp["id"], "proration": 0.5}]).json()["lines"][0]["gross"] == 50_000_000


def test_closed_period_keeps_the_run_draft(co, db):
    session, cid = co
    api = session()
    _employee(api, "Farid")
    run = _run(api).json()
    assert api.put("/admin/closed-period", json={"closed_period": "2026-06-30"}).status_code == 200
    r = api.post(f"/payroll/runs/{run['id']}/post")
    assert r.status_code in (409, 422)
    assert api.get(f"/payroll/runs/{run['id']}").json()["status"] == "draft"
    assert api.get(f"/payroll/runs/{run['id']}").json()["post_transaction_id"] is None


def test_an_employee_sees_only_their_own_payslips(co):
    session, _ = co
    owner = session()
    g = _employee(owner, "Golnar")
    h = _employee(owner, "Hamid")
    run = _run(owner).json()
    draft_slips = session("employee", entity_id=g["id"]).get("/payroll/my-payslips").json()
    assert draft_slips["payslips"] == []                                 # drafts are never shown
    owner = session()
    owner.post(f"/payroll/runs/{run['id']}/post")
    me = session("employee", entity_id=g["id"])
    mine = me.get("/payroll/my-payslips").json()
    assert [s["entity_id"] for s in mine["payslips"]] == [g["id"]]
    assert me.get(f"/payroll/runs/{run['id']}/payslip/{g['id']}").status_code == 200
    assert me.get(f"/payroll/runs/{run['id']}/payslip/{h['id']}").status_code == 404
    assert me.get(f"/payroll/runs/{run['id']}/payslip/{h['id']}/pdf").status_code == 404
    assert me.get("/payroll/runs").status_code == 403
    assert me.post(f"/payroll/runs/{run['id']}/pay").status_code == 403
    unlinked = session("employee").get("/payroll/my-payslips").json()
    assert unlinked["payslips"] == [] and "not linked" in unlinked["note"]


def test_prorate_raise_helper(co):
    session, _ = co
    api = session()
    r = api.post("/payroll/prorate-raise", json={"period_start": "2026-01-01", "period_end": "2026-01-31",
                                                 "change_date": "2026-01-16", "old_amount": 3000, "new_amount": 3600})
    assert r.status_code == 200 and r.json()["gross"] == round(3000 * 15 / 31 + 3600 * 16 / 31)
    bad = api.post("/payroll/prorate-raise", json={"period_start": "2026-01-31", "period_end": "2026-01-01",
                                                   "change_date": "2026-01-16", "old_amount": 1, "new_amount": 2})
    assert bad.status_code == 422


def _txn_count(db, cid):
    from app.db.tenant import use_company
    with use_company(cid):
        return int(db.execute(select(func.count(Transaction.id))).scalar() or 0)
