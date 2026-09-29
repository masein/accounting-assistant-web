"""What a self-service employee can see and change on the Time page: their own
rates, their own unbilled time, no client invoices, and not whether their time
counts towards pay (an audit of every *_own route after the mileage fix)."""
from __future__ import annotations

import uuid

import pytest

from tests.test_payroll_lifecycle_http import co  # noqa: F401 — fixture


@pytest.fixture()
def team(co):
    session, cid = co
    owner = session()
    sara = owner.post("/entities", json={"type": "employee", "name": "Sara Ahmadi"}).json()
    reza = owner.post("/entities", json={"type": "employee", "name": "Reza Karimi"}).json()
    aria = owner.post("/entities", json={"type": "client", "name": "Aria Trading"}).json()
    for who, rate in ((sara, 2_000_000), (reza, 3_500_000)):
        r = owner.post("/time/rates", json={"employee_id": who["id"], "rate": rate})
        assert r.status_code in (200, 201), r.text
    # the sessions share one test client: take a fresh one for each call
    return {"session": session, "cid": cid, "sara": sara, "reza": reza, "aria": aria,
            "owner": lambda: session(), "me": lambda: session("employee", entity_id=sara["id"])}


def _log(api, who, client, hours, **extra):
    r = api.post("/time/entries", json={"employee_id": who["id"], "client_id": client["id"] if client else None,
                                        "work_date": "2026-09-01", "hours": hours, "billable": bool(client), **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_an_employee_sees_only_their_own_rates(team):
    everyone = team["owner"]().get("/time/rates").json()
    assert {r["employee_name"] for r in everyone} == {"Sara Ahmadi", "Reza Karimi"}
    mine = team["me"]().get("/time/rates").json()
    assert [(r["employee_name"], r["rate"]) for r in mine] == [("Sara Ahmadi", 2_000_000)]
    asked = team["me"]().get("/time/rates", params={"employee_id": team["reza"]["id"]}).json()
    assert [r["employee_name"] for r in asked] == ["Sara Ahmadi"]                 # asking for Reza changes nothing
    assert team["session"]("employee").get("/time/rates").json() == []            # unlinked: nothing


def test_an_employee_sees_only_their_own_unbilled_time(team):
    _log(team["owner"](), team["reza"], team["aria"], 5)
    _log(team["me"](), team["sara"], team["aria"], 2)
    books = team["owner"]().get("/time/unbilled").json()
    assert books["clients"][0]["hours"] == 7
    mine = team["me"]().get("/time/unbilled").json()
    (c,) = mine["clients"]
    assert c["hours"] == 2 and c["value"] == 4_000_000                             # her 2 h at her rate
    assert team["session"]("employee").get("/time/unbilled").json()["clients"] == []


def test_client_invoices_are_for_the_books_people(team):
    inv = team["owner"]().post("/invoices", json={"number": f"T-{uuid.uuid4().hex[:5]}", "kind": "sales",
                                                "status": "issued", "issue_date": "2026-09-01",
                                                "due_date": "2026-09-30", "amount": 9_000_000, "currency": "IRR",
                                                "entity_id": team["aria"]["id"]})
    assert inv.status_code == 201, inv.text
    url = f"/time/invoice/{inv.json()['id']}/pdf"
    assert team["owner"]().get(url).status_code == 200
    assert team["me"]().get(url).status_code == 404                                  # like an unknown id


def test_an_employee_cant_make_their_time_payable(team):
    e = _log(team["me"](), team["sara"], None, 4, entry_type="unpaid", payable=True)
    assert e["payable"] is False                                                   # the type's default wins
    r = team["me"]().patch(f"/time/entries/{e['id']}", json={"payable": True})
    assert r.status_code == 200 and r.json()["payable"] is False
    r = team["me"]().patch(f"/time/entries/{e['id']}", json={"entry_type": "work"})
    assert r.json()["payable"] is True                                             # work is paid by default
    r = team["me"]().patch(f"/time/entries/{e['id']}", json={"entry_type": "unpaid", "payable": True})
    assert r.json()["payable"] is False
    # the books people decide
    r = team["owner"]().patch(f"/time/entries/{e['id']}", json={"payable": True})
    assert r.json()["payable"] is True
    other = _log(team["owner"](), team["reza"], None, 1, entry_type="unpaid", payable=True)
    assert other["payable"] is True


def test_an_approver_cant_sign_a_claim_in_someone_elses_name(team, db):
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.audit_log import AuditLog
    s = team["session"]
    assert s().post("/expenses/settings", json={"mileage_rate": 5_000, "mileage_unit": "km",
                                                "approval_threshold": 10_000}).status_code == 200
    claim = s("employee", entity_id=team["sara"]["id"]).post(
        "/expenses/mileage", json={"claim_date": "2026-09-01", "distance": 10}).json()   # 50,000 > 10,000
    assert claim["status"] == "pending_approval"
    mgr = s("manager")
    r = mgr.post(f"/expenses/{claim['id']}/approve", params={"approver": "The Owner"})
    assert r.status_code == 200 and r.json()["decided_by"] == "manager"            # the session's user, not the name
    cid = team["cid"]
    with use_company(cid):
        ev = db.execute(select(AuditLog).where(AuditLog.entity_id == claim["id"],
                                               AuditLog.action == "approve")).scalar_one()
    assert ev.username == "manager"


def test_the_time_page_leaves_invoicing_to_the_books_people():
    js = open("app/static/js/11-time-expenses-payroll.js", encoding="utf-8").read()
    ready = js[js.index("async function tmLoadReady()"):js.index("const res = await fetch(API + '/time/unbilled')")]
    assert "if (!tmBooks) return;" in ready and "tm-ready-section" in ready
    assert "tmBooks = !!pk.books;" in js
    assert 'id="tm-ready-section"' in open("app/static/index.html", encoding="utf-8").read()


def test_a_petty_cash_holder_is_a_user_of_this_company(team, db):
    from app.core.auth import hash_password
    from app.models.company import Company
    from app.models.user import User
    from tests.test_admin_audit import _purge_company
    ph, salt = hash_password("x")
    other = Company(id=uuid.uuid4(), name="Elsewhere", slug=f"else-{uuid.uuid4().hex[:6]}", locale="ir",
                    base_currency="IRR", status="active", token_version=0)
    db.add(other)
    db.commit()
    stranger = User(username=f"stranger-{uuid.uuid4().hex[:6]}", password_hash=ph, password_salt=salt,
                    role="employee", is_active=True, company_id=other.id)
    colleague = User(username=f"colleague-{uuid.uuid4().hex[:6]}", password_hash=ph, password_salt=salt,
                     role="employee", is_active=True, company_id=uuid.UUID(team["cid"]))
    db.add_all([stranger, colleague])
    db.commit()
    stranger_name, stranger_id, colleague_name = stranger.username, str(stranger.id), colleague.username
    try:
        owner = team["owner"]()
        assert owner.post("/petty-cash/accounts", json={"username": stranger_name}).status_code == 404
        assert owner.post("/petty-cash/accounts", json={"user_id": stranger_id}).status_code == 404
        assert owner.post("/petty-cash/accounts", json={"user_id": "not-a-uuid"}).status_code == 404
        r = owner.post("/petty-cash/accounts", json={"username": colleague_name})
        assert r.status_code == 201 and r.json()["holder_name"] == colleague_name
    finally:
        _purge_company(db, str(other.id))
