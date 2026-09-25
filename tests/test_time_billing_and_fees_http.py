"""Time billing and transaction fees over HTTP (roadmap 2026-09 §6, suite
10): projects and rate scopes, write-off, the unbilled summary, invoice
preview that saves nothing, invoicing that stamps entries in ONE commit,
void un-billing, pending worklogs, and fee rules in basis points with exact
half-up rounding, caps, hybrid rules and gross-mode solving."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import event, func, select

from app.models.invoice import Invoice
from app.models.transaction import Transaction


@pytest.fixture()
def co(client, db):
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty, seed_payment_methods_if_empty
    from app.db.tenant import use_company
    from app.models.company import Company
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Billing Co", slug=f"bill-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        seed_payment_methods_if_empty(db)
        db.commit()
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, create_session_token(
        user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner", company_id=cid))
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    yield _CSRFTestClient(client, csrf), cid
    client.cookies.clear()
    _purge_company(db, cid)


def _ent(api, kind, name=None):
    r = api.post("/entities", json={"type": kind, "name": name or f"{kind} {uuid.uuid4().hex[:4]}"})
    assert r.status_code == 201, r.text
    return r.json()


def _count(db, cid, model):
    from app.db.tenant import use_company
    with use_company(cid):
        return int(db.execute(select(func.count(model.id))).scalar() or 0)


@pytest.fixture()
def setup(co):
    api, cid = co
    client_ent = _ent(api, "client", "Acme")
    dev = _ent(api, "employee", "Dev One")
    designer = _ent(api, "supplier", "Freelance Designer")
    proj = api.post("/time/projects", json={"client_id": client_ent["id"], "name": "Website"}).json()
    return {"api": api, "cid": cid, "client": client_ent, "dev": dev, "designer": designer, "project": proj}


def _log(api, worker, *, hours, project=None, client=None, when="2026-06-10", **extra):
    body = {"employee_id": worker["id"], "work_date": when, "hours": hours, **extra}
    if project:
        body["project_id"] = project["id"]
    if client:
        body["client_id"] = client["id"]
    r = api.post("/time/entries", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ─── projects and rates ────────────────────────────────────────────────

def test_projects_and_rate_scopes(setup):
    api, c, dev, proj = setup["api"], setup["client"], setup["dev"], setup["project"]
    assert api.post("/time/projects", json={"client_id": dev["id"], "name": "x"}).status_code == 422
    assert api.post("/time/projects", json={"client_id": str(uuid.uuid4()), "name": "x"}).status_code == 404
    assert [p["name"] for p in api.get("/time/projects", params={"client_id": c["id"]}).json()] == ["Website"]

    assert api.post("/time/rates", json={"employee_id": c["id"], "rate": 1}).status_code == 422
    assert api.post("/time/rates", json={"employee_id": dev["id"], "rate": -1}).status_code == 422
    for body in ({"rate": 1_000_000}, {"rate": 1_200_000, "client_id": c["id"]},
                 {"rate": 1_500_000, "project_id": proj["id"]}):
        r = api.post("/time/rates", json={"employee_id": dev["id"], **body})
        assert r.status_code == 201, r.text
    rates = api.get("/time/rates", params={"employee_id": dev["id"]}).json()
    assert [x["rate"] for x in rates if x["project_id"] == proj["id"]] == [1_500_000]
    assert [x["rate"] for x in rates if x["client_id"] == c["id"] and x["project_id"] is None] == [1_200_000]
    # The most specific rate wins on an entry.
    e = _log(api, dev, hours=2, project=proj)
    assert e["client_id"] == c["id"]                           # a project implies its client
    other = _log(api, dev, hours=1, client=c, when="2026-06-11")
    unb = {x["client_id"]: x for x in api.get("/time/unbilled").json()["clients"]}[c["id"]]
    assert unb["hours"] == 3 and unb["value"] == 2 * 1_500_000 + 1 * 1_200_000
    assert other["billable"] is True


def test_write_off_and_its_locks(setup):
    api, c, dev = setup["api"], setup["client"], setup["dev"]
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 100_000})
    e = _log(api, dev, hours=1, client=c)
    w = api.post(f"/time/entries/{e['id']}/write-off")
    assert w.status_code == 200 and w.json()["status"] == "written_off"
    assert api.get("/time/unbilled").json()["clients"] == []
    assert api.post(f"/time/entries/{uuid.uuid4()}/write-off").status_code == 404


# ─── preview, invoice, void ────────────────────────────────────────────

def test_preview_saves_nothing_and_invoicing_is_one_commit(setup, db):
    api, cid, c, dev, proj = setup["api"], setup["cid"], setup["client"], setup["dev"], setup["project"]
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 400_000})
    e1 = _log(api, dev, hours=2.5, project=proj)
    e2 = _log(api, dev, hours=1, project=proj, when="2026-06-12")
    inv_before, txn_before = _count(db, cid, Invoice), _count(db, cid, Transaction)

    pv = api.post("/time/invoice-preview", json={"client_id": c["id"], "invoice_date": "2026-06-30"})
    assert pv.status_code == 200, pv.text
    assert pv.json()["subtotal"] == 1_400_000                  # 3.5 h × 400,000
    assert (_count(db, cid, Invoice), _count(db, cid, Transaction)) == (inv_before, txn_before)
    assert all(x["status"] == "unbilled" for x in api.get("/time/entries").json())

    commits = []
    listener = lambda session: commits.append(1)             # noqa: E731
    from sqlalchemy.orm import Session as _S
    event.listen(_S, "after_commit", listener)
    try:
        r = api.post("/time/invoice", json={"client_id": c["id"], "invoice_date": "2026-06-30"})
    finally:
        event.remove(_S, "after_commit", listener)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "issued" and out["amount"] == pv.json()["total"]
    # The invoice and the "invoiced" stamps on its entries land together.
    assert len(commits) == 1, f"{len(commits)} commits"
    entries = {x["id"]: x for x in api.get("/time/entries").json()}
    assert entries[e1["id"]]["status"] == entries[e2["id"]]["status"] == "invoiced"
    assert _count(db, cid, Transaction) == txn_before + 1       # AR recognised
    pdf = api.get(out["pdf_url"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert api.post("/time/invoice", json={"client_id": c["id"]}).status_code == 422   # nothing left

    # Invoiced time is locked…
    assert api.post(f"/time/entries/{e1['id']}/write-off").status_code == 409
    assert api.delete(f"/time/entries/{e1['id']}").status_code == 409
    # …until the invoice is voided, which puts it back as unbilled.
    assert api.post(f"/invoices/{out['invoice_id']}/void").status_code == 200
    assert all(x["status"] == "unbilled" for x in api.get("/time/entries").json())


def test_missing_rates_and_mixed_currencies_are_refused(setup):
    api, c, dev, designer = setup["api"], setup["client"], setup["dev"], setup["designer"]
    _log(api, dev, hours=1, client=c)
    r = api.post("/time/invoice-preview", json={"client_id": c["id"]})
    assert r.status_code == 422 and "No billable rate" in r.json()["detail"]
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 100, "currency": "IRR"})
    api.post("/time/rates", json={"employee_id": designer["id"], "rate": 50, "currency": "USD"})
    _log(api, designer, hours=1, client=c)
    r = api.post("/time/invoice", json={"client_id": c["id"]})
    assert r.status_code == 422 and "multiple currencies" in r.json()["detail"]


def test_closed_period_blocks_invoicing_and_stamps_nothing(setup):
    api, c, dev = setup["api"], setup["client"], setup["dev"]
    api.post("/time/rates", json={"employee_id": dev["id"], "rate": 100_000})
    _log(api, dev, hours=1, client=c)
    assert api.put("/admin/closed-period", json={"closed_period": "2026-06-30"}).status_code == 200
    r = api.post("/time/invoice", json={"client_id": c["id"], "invoice_date": "2026-06-30"})
    assert r.status_code in (409, 422)
    assert all(x["status"] == "unbilled" for x in api.get("/time/entries").json())


# ─── pending worklogs ──────────────────────────────────────────────────

def test_pending_worklogs_resolve_and_reject(setup, db):
    from app.db.tenant import use_company
    from app.models.pending_time_entry import PendingTimeEntry
    api, cid, dev = setup["api"], setup["cid"], setup["dev"]
    with use_company(cid):
        a = PendingTimeEntry(source="jira", external_id="J-1", worker_ref="unknown@x", work_date=date(2026, 6, 3),
                             hours=3, entry_type="work", status="pending")
        b = PendingTimeEntry(source="jira", external_id="J-2", worker_ref="unknown@x", work_date=date(2026, 6, 4),
                             hours=1, entry_type="leave", status="pending")
        db.add_all([a, b])
        db.commit()
        aid, bid = str(a.id), str(b.id)
    assert {p["id"] for p in api.get("/time/pending").json()} >= {aid, bid}
    client_ent = setup["client"]
    assert api.post(f"/time/pending/{aid}/resolve", json={"entity_id": client_ent["id"]}).status_code == 422
    r = api.post(f"/time/pending/{aid}/resolve", json={"entity_id": dev["id"]})
    assert r.status_code == 200 and r.json()["hours"] == 3 and r.json()["employee_id"] == dev["id"]
    assert api.post(f"/time/pending/{aid}/resolve", json={"entity_id": dev["id"]}).status_code == 404
    assert api.post(f"/time/pending/{bid}/reject").json() == {"ok": True}
    assert api.post(f"/time/pending/{bid}/reject").status_code == 404
    assert api.get("/time/pending").json() == []


# ─── fees in basis points ──────────────────────────────────────────────

def _rule(api, **body):
    r = api.put("/transactions/fees", json={"method_name": "Paya", "bank_name": "Bank Melli", **body})
    assert r.status_code == 200, r.text
    return r.json()["rule"]


def _calc(api, amount, mode="net"):
    r = api.post("/transactions/fees/calculate", json={"amount": amount, "method_name": "Paya",
                                                       "bank_name": "Bank Melli", "amount_mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("base,bps,fee", [
    (10_000, 125, 125),       # 1.25 %
    (50, 100, 1),             # 0.5 → 1 (round() gave 0)
    (250, 100, 3),            # 2.5 → 3 (round() gave 2)
    (150, 100, 2),
    (123_456_789_012_345, 1, 12_345_678_901),   # exact far past float precision
])
def test_percent_fee_is_exact_and_half_up(co, base, bps, fee):
    api, _ = co
    _rule(api, fee_type="percent", percent_bps=bps)
    out = _calc(api, base)
    assert out["fee_amount"] == fee and out["gross_amount"] == base + fee and out["net_amount"] == base


def test_flat_hybrid_cap_free_and_gross_mode(co):
    api, _ = co
    _rule(api, fee_type="flat", flat_fee=2_000)
    assert _calc(api, 1_000_000)["fee_amount"] == 2_000
    _rule(api, fee_type="hybrid", flat_fee=1_000, percent_bps=10, max_fee=5_000)
    small, big = _calc(api, 1_000_000), _calc(api, 100_000_000)
    assert small["fee_amount"] == 2_000 and small["applied_cap"] is False     # 1,000 + 0.1 %
    assert big["fee_amount"] == 5_000 and big["applied_cap"] is True
    g = _calc(api, 1_002_000, mode="gross")
    assert g["base_amount"] + g["fee_amount"] == 1_002_000 and g["base_amount"] == 1_000_000
    _rule(api, fee_type="free")
    assert _calc(api, 1_000_000)["fee_amount"] == 0
    listed = api.get("/transactions/fees", params={"method_name": "Paya", "bank_name": "Bank Melli"}).json()
    assert listed and listed[0]["fee_type"] == "free"
    assert [m["name"] for m in api.get("/transactions/fees/methods").json()]


def test_fee_calculation_errors(co):
    api, _ = co
    r = api.post("/transactions/fees/calculate", json={"amount": 1, "method_name": "Pigeon", "bank_name": "Bank Melli"})
    assert r.status_code == 404
    r = api.post("/transactions/fees/calculate", json={"amount": 1, "method_name": "Paya", "bank_name": "No Such Bank"})
    assert r.status_code == 404
    assert api.post("/transactions/fees/calculate", json={"amount": 1, "method_name": "Paya"}).status_code == 422
    assert api.put("/transactions/fees", json={"method_name": "Paya", "fee_type": "flat"}).status_code == 422
