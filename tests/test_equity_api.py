"""Shareholder equity over HTTP (roadmap 2026-09 §6, suite 4): all nine
/equity routes inside a private company, the ledger balanced after every
posting, the cap table summing to 100 %, and the guards this suite added:
no dividend paid beyond what was declared, no mixing percent and share
weights, no deleting a holder who is still owed a dividend, nothing left
behind when a posting fails half-way, and the revaluation-surplus capital
increase working on the Iranian chart too."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.transaction import Transaction, TransactionLine

D = "2026-06-15"


@pytest.fixture(params=["ir", "uk"])
def co(request, client, db):
    """A private company (Iranian or UK chart) with an owner session."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    locale = request.param
    c = Company(id=uuid.uuid4(), name=f"Equity {locale}", slug=f"eq-{uuid.uuid4().hex[:6]}", locale=locale,
                base_currency="IRR" if locale == "ir" else "GBP", status="active", token_version=0,
                registered_capital=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid, locale
    client.cookies.clear()
    _purge_company(db, cid)


def _holder(api, name=None, **holding):
    ent = api.post("/entities", json={"type": "shareholder", "name": name or f"Holder {uuid.uuid4().hex[:4]}"})
    assert ent.status_code == 201, ent.text
    ent = ent.json()
    if holding:
        r = api.post("/equity/shareholdings", json={"entity_id": ent["id"], **holding})
        assert r.status_code == 201, r.text
        ent["holding"] = r.json()
    return ent


def _ledger_balanced(db, cid, txn_ids):
    from app.db.tenant import use_company
    with use_company(cid):
        for tid in txn_ids:
            tid = uuid.UUID(tid)
            dr = db.execute(select(func.coalesce(func.sum(TransactionLine.debit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
            cr = db.execute(select(func.coalesce(func.sum(TransactionLine.credit), 0)).where(TransactionLine.transaction_id == tid)).scalar()
            assert int(dr) == int(cr) > 0, tid


def _txn_count(db, cid):
    from app.db.tenant import use_company
    with use_company(cid):
        return int(db.execute(select(func.count(Transaction.id))).scalar() or 0)


# ─── cap table ─────────────────────────────────────────────────────────

def test_shareholdings_crud_and_the_100_percent_rule(co):
    api, _cid, _ = co
    a = _holder(api, percent=60, shares=600)
    b = _holder(api, percent=40, shares=400)
    ct = api.get("/equity/cap-table").json()
    assert ct["total_percent"] == 100 and [r["percent"] for r in ct["rows"]] == [60, 40]

    c = _holder(api)
    r = api.post("/equity/shareholdings", json={"entity_id": c["id"], "percent": 1})
    assert r.status_code == 422 and "exceed 100%" in r.json()["detail"]
    assert api.post("/equity/shareholdings", json={"entity_id": a["id"], "percent": 1}).status_code == 409
    client_ent = api.post("/entities", json={"type": "client", "name": "Not a holder"}).json()
    assert api.post("/equity/shareholdings", json={"entity_id": client_ent["id"], "percent": 1}).status_code == 422
    assert api.post("/equity/shareholdings", json={"entity_id": str(uuid.uuid4()), "percent": 1}).status_code == 404

    hid = a["holding"]["id"]
    assert api.patch(f"/equity/shareholdings/{hid}", json={"percent": 61}).status_code == 422
    upd = api.patch(f"/equity/shareholdings/{hid}", json={"percent": 55, "shares": 550}).json()
    assert upd["percent"] == 55 and upd["shares"] == 550
    assert api.patch(f"/equity/shareholdings/{uuid.uuid4()}", json={"shares": 1}).status_code == 404
    assert api.delete(f"/equity/shareholdings/{b['holding']['id']}").status_code == 204
    assert api.get("/equity/cap-table").json()["total_percent"] == 55
    assert api.delete(f"/equity/shareholdings/{uuid.uuid4()}").status_code == 404


# ─── contributions and capital ─────────────────────────────────────────

def test_contribution_to_capital_and_uncapitalised(co, db):
    api, cid, _ = co
    a = _holder(api, percent=100)
    r = api.post("/equity/contribution", json={"entity_id": a["id"], "amount": 5_000_000, "date": D})
    assert r.status_code == 201, r.text
    assert r.json()["registered_capital"] == 5_000_000
    _ledger_balanced(db, cid, r.json()["transaction_ids"])
    loan = api.post("/equity/contribution", json={"entity_id": a["id"], "amount": 1_000, "date": D,
                                                  "to_capital": False}).json()
    assert loan["registered_capital"] is None
    ct = api.get("/equity/cap-table").json()
    assert ct["registered_capital"] == 5_000_000 and ct["rows"][0]["paid_in"] == 5_001_000
    assert api.post("/equity/contribution", json={"entity_id": str(uuid.uuid4()), "amount": 1, "date": D}).status_code == 422
    assert api.post("/equity/contribution", json={"entity_id": a["id"], "amount": 0, "date": D}).status_code == 422


@pytest.mark.parametrize("source", ["retained_earnings", "cash", "revaluation_surplus"])
def test_capital_increase_from_every_source(co, db, source):
    api, cid, _ = co
    r = api.post("/equity/capital-increase", json={"amount": 2_000, "date": D, "source": source})
    assert r.status_code == 201, (source, r.text)
    assert r.json()["registered_capital"] == 2_000
    _ledger_balanced(db, cid, r.json()["transaction_ids"])


def test_capital_increase_rejects_an_unknown_source(co):
    api, _, _ = co
    assert api.post("/equity/capital-increase", json={"amount": 1, "date": D, "source": "magic"}).status_code == 422


# ─── dividends ─────────────────────────────────────────────────────────

def test_dividend_declared_by_cap_table_sums_exactly(co, db):
    api, cid, _ = co
    a = _holder(api, percent=33.3333)
    b = _holder(api, percent=33.3333)
    c = _holder(api, percent=33.3334)
    r = api.post("/equity/dividend/declare", json={"total_amount": 1_000_001, "date": D})
    assert r.status_code == 201, r.text
    allocs = {x["entity_id"]: x["amount"] for x in r.json()["allocations"]}
    assert sum(allocs.values()) == 1_000_001 and set(allocs) == {a["id"], b["id"], c["id"]}
    _ledger_balanced(db, cid, r.json()["transaction_ids"])
    rows = {x["entity_id"]: x for x in api.get("/equity/cap-table").json()["rows"]}
    assert all(rows[k]["dividends_outstanding"] == v for k, v in allocs.items())


def test_explicit_allocation_must_sum_and_a_bad_holder_leaves_nothing_behind(co, db):
    api, cid, _ = co
    a = _holder(api, percent=50)
    stranger = api.post("/entities", json={"type": "supplier", "name": "Not a shareholder"}).json()
    before = _txn_count(db, cid)
    bad_sum = api.post("/equity/dividend/declare", json={"total_amount": 100, "date": D, "allocations": [
        {"entity_id": a["id"], "amount": 60}]})
    assert bad_sum.status_code == 422
    # The first allocation posts before the second one fails: all or nothing.
    half = api.post("/equity/dividend/declare", json={"total_amount": 100, "date": D, "allocations": [
        {"entity_id": a["id"], "amount": 60}, {"entity_id": stranger["id"], "amount": 40}]})
    assert half.status_code == 422
    assert _txn_count(db, cid) == before
    assert api.get("/equity/cap-table").json()["rows"][0]["dividends_declared"] == 0


def test_dividend_pay_is_capped_at_what_is_outstanding(co, db):
    api, cid, _ = co
    a = _holder(api, percent=100)
    b = _holder(api)
    api.post("/equity/dividend/declare", json={"total_amount": 1_000, "date": D})
    paid = api.post("/equity/dividend/pay", json={"entity_id": a["id"], "amount": 600, "date": D})
    assert paid.status_code == 201
    _ledger_balanced(db, cid, paid.json()["transaction_ids"])
    over = api.post("/equity/dividend/pay", json={"entity_id": a["id"], "amount": 401, "date": D})
    assert over.status_code == 422 and "400" in over.json()["detail"].replace(",", "")
    none = api.post("/equity/dividend/pay", json={"entity_id": b["id"], "amount": 1, "date": D})
    assert none.status_code == 422
    assert api.post("/equity/dividend/pay", json={"entity_id": a["id"], "amount": 400, "date": D}).status_code == 201
    row = api.get("/equity/cap-table").json()["rows"][0]
    assert row["dividends_paid"] == 1_000 and row["dividends_outstanding"] == 0


def test_mixed_percent_and_share_weights_are_refused(co):
    api, _, _ = co
    _holder(api, percent=60)
    _holder(api, shares=1_000)             # a share count, not a percent
    r = api.post("/equity/dividend/declare", json={"total_amount": 1_000, "date": D})
    assert r.status_code == 422 and "percent" in r.json()["detail"]


def test_share_counts_alone_allocate(co):
    api, _, _ = co
    a = _holder(api, shares=300)
    b = _holder(api, shares=100)
    r = api.post("/equity/dividend/declare", json={"total_amount": 1_000, "date": D}).json()
    allocs = {x["entity_id"]: x["amount"] for x in r["allocations"]}
    assert allocs == {a["id"]: 750, b["id"]: 250}


def test_a_holder_owed_a_dividend_cannot_be_deleted(co):
    api, _, _ = co
    a = _holder(api, percent=100)
    api.post("/equity/dividend/declare", json={"total_amount": 500, "date": D})
    r = api.delete(f"/equity/shareholdings/{a['holding']['id']}")
    assert r.status_code == 409 and "500" in r.json()["detail"]
    api.post("/equity/dividend/pay", json={"entity_id": a["id"], "amount": 500, "date": D})
    assert api.delete(f"/equity/shareholdings/{a['holding']['id']}").status_code == 204


# ─── current account, closed period, roles ─────────────────────────────

def test_current_account_in_and_out(co, db):
    api, cid, _ = co
    a = _holder(api, percent=100)
    for direction in ("in", "out"):
        r = api.post("/equity/current-account", json={"entity_id": a["id"], "amount": 700, "date": D,
                                                      "direction": direction})
        assert r.status_code == 201, r.text
        _ledger_balanced(db, cid, r.json()["transaction_ids"])
    assert api.post("/equity/current-account", json={"entity_id": a["id"], "amount": 1, "date": D,
                                                     "direction": "sideways"}).status_code == 422


def test_closed_period_blocks_every_equity_posting(co, db):
    api, cid, _ = co
    a = _holder(api, percent=100)
    # A wrong key must not silently clear (or set) the lock.
    assert api.put("/admin/closed-period", json={"closed_through": "2026-06-30"}).status_code == 422
    assert api.put("/admin/closed-period", json={"closed_period": "2026-06-30"}).status_code == 200
    before = _txn_count(db, cid)
    calls = [
        ("/equity/contribution", {"entity_id": a["id"], "amount": 1, "date": D}),
        ("/equity/capital-increase", {"amount": 1, "date": D}),
        ("/equity/dividend/declare", {"total_amount": 1, "date": D}),
        ("/equity/current-account", {"entity_id": a["id"], "amount": 1, "date": D, "direction": "in"}),
    ]
    for url, body in calls:
        r = api.post(url, json=body)
        assert r.status_code in (409, 422) and ("closed" in r.text.lower() or "locked" in r.text.lower()), (url, r.text)
    assert _txn_count(db, cid) == before
    assert api.get("/equity/cap-table").json()["registered_capital"] == 0


def test_viewer_reads_nothing_it_should_not_and_writes_nothing(co, client):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from tests.conftest import _CSRFTestClient
    api, cid, _ = co
    a = _holder(api, percent=100)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="v", is_admin=False, role="viewer", company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    viewer = _CSRFTestClient(client, csrf)
    assert viewer.post("/equity/contribution", json={"entity_id": a["id"], "amount": 1, "date": D}).status_code == 403
    assert viewer.delete(f"/equity/shareholdings/{a['holding']['id']}").status_code == 403
