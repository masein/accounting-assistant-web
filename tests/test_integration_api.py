"""Part B — the inbound /api/v1 time-tracking API (key auth, idempotency,
parking, closed-period rejection, tenant scoping)."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.api_key_auth import generate_api_key
from app.models.api_key import ApiKey
from app.models.company import Company
from app.models.employee_pay import EmployeePayProfile
from app.models.entity import Entity
from app.models.pay_run import PayRun, PayRunLine
from app.models.pending_time_entry import PendingTimeEntry
from app.models.time_billing import TimeEntry


def _company(db, name="Acme"):
    c = Company(id=uuid.uuid4(), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def _key(db, company, label="jira"):
    raw, digest, prefix = generate_api_key()
    k = ApiKey(company_id=company.id, label=label, key_hash=digest, prefix=prefix)
    db.add(k)
    db.flush()
    return raw, k


def _employee(db, company, name="Hana", email=None, code=None):
    e = Entity(id=uuid.uuid4(), name=name, type="employee", email=email, code=code,
               company_id=company.id)
    db.add(e)
    db.flush()
    return e


def _push(client, raw_key, body):
    return client.post("/api/v1/time-entries", json=body,
                       headers={"Authorization": f"Bearer {raw_key}"})


def _entry(world, **over):
    base = {
        "external_id": "JIRA-42/worklog/1", "source": world["src"],
        "worker": "hana@acme.test", "date": "2026-01-05", "hours": 6,
        "description": "Implement login flow",
    }
    base.update(over)
    return base


@pytest.fixture()
def world(db, client):
    co = _company(db)
    raw, key = _key(db, co)
    emp = _employee(db, co, email="hana@acme.test", code="EMP-7")
    db.flush()
    # unique per-test source so unscoped test-side queries can't see other
    # tests' committed rows (the API itself is tenant-scoped by the key)
    return {"co": co, "raw": raw, "key": key, "emp": emp, "client": client,
            "src": f"jira-{uuid.uuid4().hex[:8]}"}


def test_no_key_is_401(client):
    r = client.post("/api/v1/time-entries", json={"external_id": "x", "source": "jira", "worker": "a@b.c", "date": "2026-01-05", "hours": 1})
    assert r.status_code == 401
    assert client.get("/api/v1/time-entries").status_code == 401


def test_bad_and_revoked_keys_are_401(world, db):
    c = world["client"]
    assert _push(c, "ak_totally-wrong", _entry(world)).status_code == 401
    world["key"].revoked = True
    db.flush()
    assert _push(c, world["raw"], _entry(world)).status_code == 401


def test_push_maps_by_email_and_is_idempotent(world, db):
    c = world["client"]
    r = _push(c, world["raw"], _entry(world))
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["status"] == "mapped" and res["id"]

    # Re-push with changed hours → UPDATE, not duplicate.
    r2 = _push(c, world["raw"], _entry(world, hours=7.5))
    assert r2.json()["results"][0]["status"] == "mapped"
    entries = db.query(TimeEntry).filter(TimeEntry.source == world["src"]).all()
    assert len(entries) == 1
    assert float(entries[0].hours) == 7.5
    assert entries[0].employee_id == world["emp"].id
    assert entries[0].created_by == f"api:{world['src']}"


def test_push_maps_by_code_and_batch(world, db):
    c = world["client"]
    body = {"entries": [
        _entry(world, external_id="T-1", worker="EMP-7"),
        _entry(world, external_id="T-2", worker="emp-7", hours=2),  # case-insensitive
    ]}
    r = _push(c, world["raw"], body)
    assert r.status_code == 200
    assert r.json()["summary"]["mapped"] == 2


def test_unknown_worker_is_parked_not_dropped(world, db):
    c = world["client"]
    r = _push(c, world["raw"], _entry(world, external_id="T-9", worker="ghost@nowhere.test"))
    res = r.json()["results"][0]
    assert res["status"] == "pending_unmatched"
    parked = db.query(PendingTimeEntry).filter(PendingTimeEntry.external_id == "T-9", PendingTimeEntry.source == world["src"]).one()
    assert parked.status == "pending" and parked.worker_ref == "ghost@nowhere.test"
    # re-push updates the SAME parked row (idempotent parking)
    _push(c, world["raw"], _entry(world, external_id="T-9", worker="ghost@nowhere.test", hours=3))
    rows = db.query(PendingTimeEntry).filter(PendingTimeEntry.external_id == "T-9", PendingTimeEntry.source == world["src"]).all()
    assert len(rows) == 1 and float(rows[0].hours) == 3


def test_push_into_finalized_period_is_rejected(world, db):
    c = world["client"]
    run = PayRun(id=uuid.uuid4(), period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                 pay_date=date(2026, 2, 1), currency="GBP", status="posted",
                 company_id=world["co"].id)
    db.add(run)
    db.flush()
    db.add(PayRunLine(id=uuid.uuid4(), run_id=run.id, entity_id=world["emp"].id,
                      employee_name="Hana", gross=1, net_pay=1, company_id=world["co"].id))
    db.flush()
    r = _push(c, world["raw"], _entry(world, external_id="T-late"))
    res = r.json()["results"][0]
    assert res["status"] == "rejected"
    assert "finalized" in res["reason"]


def test_update_of_settled_entry_rejected_and_delete_blocked(world, db):
    c = world["client"]
    _push(c, world["raw"], _entry(world))
    e = db.query(TimeEntry).filter(TimeEntry.source == world["src"]).one()
    run = PayRun(id=uuid.uuid4(), period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
                 pay_date=date(2026, 2, 1), currency="GBP", status="draft",
                 company_id=world["co"].id)
    db.add(run)
    db.flush()
    e.payroll_run_id = run.id
    e.payroll_status = "paid"
    db.flush()
    r = _push(c, world["raw"], _entry(world, hours=1))
    assert r.json()["results"][0]["status"] == "rejected"
    d = c.request("DELETE", f"/api/v1/time-entries/by-external/{world['src']}/JIRA-42/worklog/1",
                  headers={"Authorization": f"Bearer {world['raw']}"})
    assert d.status_code == 409


def test_delete_upstream_removes_unsettled_entry(world, db):
    c = world["client"]
    _push(c, world["raw"], _entry(world))
    d = c.request("DELETE", f"/api/v1/time-entries/by-external/{world['src']}/JIRA-42/worklog/1",
                  headers={"Authorization": f"Bearer {world['raw']}"})
    assert d.status_code == 200
    assert db.query(TimeEntry).filter(TimeEntry.source == world["src"]).count() == 0


def test_day_over_24h_rejected(world, db):
    c = world["client"]
    _push(c, world["raw"], _entry(world, external_id="T-a", hours=20))
    r = _push(c, world["raw"], _entry(world, external_id="T-b", hours=6))
    res = r.json()["results"][0]
    assert res["status"] == "rejected" and "exceed" in res["reason"]


def test_key_cannot_cross_companies(world, db):
    """Company B's key must not see or touch company A's data."""
    c = world["client"]
    _push(c, world["raw"], _entry(world))  # company A entry (mapped)
    co_b = _company(db, "OtherCo")
    raw_b, _ = _key(db, co_b, label="trello")
    _employee(db, co_b, name="Bob", email="bob@other.test")
    db.flush()
    # B lists: sees nothing of A
    r = c.get("/api/v1/time-entries", headers={"Authorization": f"Bearer {raw_b}"})
    assert r.status_code == 200
    assert r.json()["mapped"] == [] and r.json()["pending"] == []
    # B pushing A's worker email → parked (no cross-company match), not mapped
    rb = _push(c, raw_b, _entry(world, external_id="TB-1"))
    assert rb.json()["results"][0]["status"] == "pending_unmatched"
    # B cannot delete A's worklog
    d = c.request("DELETE", f"/api/v1/time-entries/by-external/{world['src']}/JIRA-42/worklog/1",
                  headers={"Authorization": f"Bearer {raw_b}"})
    assert d.status_code == 404


def test_parked_entry_resolves_to_normal_entry(world, db, client):
    from tests.conftest import _CSRFTestClient
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings as app_settings
    c = world["client"]
    _push(c, world["raw"], _entry(world, external_id="T-park", worker="ghost@x.test"))
    parked = db.query(PendingTimeEntry).filter(PendingTimeEntry.external_id == "T-park", PendingTimeEntry.source == world["src"]).one()

    # employer (owner session in company A) resolves it to Hana
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(world["co"].id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(app_settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    api = _CSRFTestClient(client, csrf)
    r = api.post(f"/time/pending/{parked.id}/resolve", json={"entity_id": str(world["emp"].id)})
    assert r.status_code == 200, r.text
    assert r.json()["employee_id"] == str(world["emp"].id)
    db.refresh(parked)
    assert parked.status == "resolved" and parked.resolved_entry_id is not None
    e = db.get(TimeEntry, parked.resolved_entry_id)
    assert e.payable and e.payroll_status == "unpaid" and e.source == world["src"]


def test_owner_key_management_roundtrip(world, db, client):
    from tests.conftest import _CSRFTestClient
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings as app_settings
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(world["co"].id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(app_settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    api = _CSRFTestClient(client, csrf)

    made = api.post("/admin/api-keys", json={"label": "toggl"})
    assert made.status_code == 201
    raw = made.json()["api_key"]
    assert raw.startswith("ak_")
    listed = api.get("/admin/api-keys").json()
    assert any(k["label"] == "toggl" and not k["revoked"] for k in listed)
    assert all("api_key" not in k for k in listed)  # never shown again

    kid = made.json()["id"]
    assert api.delete(f"/admin/api-keys/{kid}").status_code == 204
    # the revoked key stops working
    r = _push(world["client"], raw, _entry(world))
    assert r.status_code == 401


def test_non_owner_cannot_manage_keys(world, client):
    from tests.conftest import _CSRFTestClient
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings as app_settings
    tok = create_session_token(user_id=str(uuid.uuid4()), username="cfo", is_admin=False,
                               company_id=str(world["co"].id), role="cfo")
    csrf = generate_csrf_token()
    client.cookies.set(app_settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    api = _CSRFTestClient(client, csrf)
    assert api.get("/admin/api-keys").status_code == 403
    assert api.post("/admin/api-keys", json={"label": "x"}).status_code == 403
