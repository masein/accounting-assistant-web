"""Payments may not exceed an invoice's open balance (QA 2026-09-24)."""
from __future__ import annotations

import uuid


def _invoice(auth_client, amount=3_000_000):
    r = auth_client.post("/invoices", json={
        "number": f"QA-OVP-{uuid.uuid4().hex[:6]}", "kind": "sales", "issue_date": "2026-09-01",
        "due_date": "2026-10-01", "amount": amount, "currency": "IRR", "status": "issued",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _pay(auth_client, iid, amount):
    return auth_client.post(f"/invoices/{iid}/payments", json={"amount": amount, "date": "2026-09-05", "method": "bank"})


def test_overpayment_is_refused_and_exact_settlement_works(auth_client):
    iid = _invoice(auth_client)
    r = _pay(auth_client, iid, 5_000_000)
    assert r.status_code == 400 and "exceeds the open balance of 3,000,000" in r.json()["detail"]

    assert _pay(auth_client, iid, 1_000_000).status_code == 201       # partial
    r = _pay(auth_client, iid, 2_500_000)                               # more than what is left
    assert r.status_code == 400 and "2,000,000" in r.json()["detail"]
    assert _pay(auth_client, iid, 2_000_000).status_code == 201       # exactly the remainder

    inv = next(i for i in auth_client.get("/invoices").json() if i["id"] == iid)
    assert inv["status"] == "paid" and inv["amount_paid"] == 3_000_000 and inv["balance_due"] == 0
    # Nothing more can be taken on a settled invoice.
    r = _pay(auth_client, iid, 1)
    assert r.status_code == 400 and "no open balance" in r.json()["detail"]
