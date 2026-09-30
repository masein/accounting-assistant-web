"""Quotes and recurring invoices are in the company's currency when none is
given — they defaulted to a literal "IRR", so a UK company's quote (and the
invoice it became) and its recurring invoices were in rials. Invoices,
entries, adjustments, POs and fixed assets already used the base currency."""
from __future__ import annotations

import pytest

from tests.test_statement_export import _company


@pytest.fixture(params=[("uk", "GBP"), ("ir", "IRR")])
def co(request, client, db):
    from tests.test_admin_audit import _purge_company
    locale, ccy = request.param
    api, cid = _company(client, db, locale, ccy)
    client_ent = api.post("/entities", json={"type": "client", "name": "Acme"}).json()
    yield api, ccy, client_ent["id"]
    client.cookies.clear()
    _purge_company(db, cid)


def test_a_quote_and_the_invoice_it_becomes(co):
    api, ccy, entity = co
    q = api.post("/quotes", json={"entity_id": entity, "issue_date": "2026-09-01", "valid_until": "2026-09-30",
                                  "amount": 1_000})
    assert q.status_code == 201, q.text
    assert q.json()["currency"] == ccy
    inv = api.post(f"/quotes/{q.json()['id']}/convert", json={})
    assert inv.status_code == 201, inv.text
    body = inv.json()
    assert (body.get("invoice") or body).get("currency") == ccy, body
    # an explicit one is kept
    usd = api.post("/quotes", json={"entity_id": entity, "issue_date": "2026-09-01", "valid_until": "2026-09-30",
                                    "amount": 10, "currency": "usd"})
    assert usd.json()["currency"] == "USD"


def test_a_recurring_invoice(co):
    api, ccy, entity = co
    r = api.post("/recurring-invoices", json={"entity_id": entity, "amount": 500, "start_date": "2026-12-01",
                                             "generate_now": False})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body.get("template") or body).get("currency") == ccy, body
