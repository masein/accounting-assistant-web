"""Quotes / پیش‌فاکتور → invoice (roadmap §4.2): a quote carries invoice
lines but never touches the ledger; accept/decline/expire are tracked; the
conversion creates the sales invoice and locks the quote in one commit."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.transaction import Transaction, TransactionLine


def _txn_count(db) -> int:
    return int(db.execute(select(func.count(Transaction.id))).scalar() or 0)


@pytest.fixture()
def customer(auth_client, db):
    ent = auth_client.post("/entities", json={
        "type": "client", "name": f"Quote Co {uuid.uuid4().hex[:6]}", "payment_terms": "Net 15",
    }).json()
    yield ent
    from app.models.audit_log import AuditLog  # noqa: F401  (append-only; left alone)
    from app.models.invoice import Invoice
    from app.models.quote import Quote
    db.rollback()
    ent_id = uuid.UUID(ent["id"])
    for q in db.execute(select(Quote).where(Quote.entity_id == ent_id)).scalars().all():
        db.delete(q)
    db.flush()
    for inv in db.execute(select(Invoice).where(Invoice.entity_id == ent_id)).scalars().all():
        db.delete(inv)
    db.commit()


def _lines():
    return [
        {"product_name": "Design", "quantity": 2, "unit_price": 1_000_000, "tax_rate": 10},
        {"product_name": "Hosting (exempt)", "quantity": 1, "unit_price": 500_000, "taxable": False},
    ]


def _quote(auth_client, customer, **over):
    body = {
        "issue_date": "2026-09-01", "valid_until": "2099-12-31", "currency": "IRR",
        "entity_id": customer["id"], "items": _lines(), **over,
    }
    r = auth_client.post("/quotes", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_quote_totals_numbering_and_no_ledger_effect(auth_client, db, customer):
    before = _txn_count(db)
    q = _quote(auth_client, customer)
    assert q["number"].startswith("Q-") and q["number"][2:].isdigit()
    assert q["status"] == "draft" and q["effective_status"] == "draft"
    assert q["subtotal"] == 2_500_000
    assert q["tax_total"] == 200_000                      # 10 % on the taxable line only
    assert q["amount"] == 2_700_000
    assert [i["product_name"] for i in q["items"]] == ["Design", "Hosting (exempt)"]
    assert q["entity_name"] == customer["name"]
    assert _txn_count(db) == before                       # a quote never posts

    q2 = _quote(auth_client, customer)
    assert int(q2["number"][2:]) == int(q["number"][2:]) + 1
    nxt = auth_client.get("/quotes/next-number").json()
    assert int(nxt["quote_number"][2:]) == int(q2["number"][2:]) + 1
    assert nxt["invoice_number"]


def test_create_quote_validation(auth_client, customer):
    q = _quote(auth_client, customer, number=f"QV-{uuid.uuid4().hex[:5]}")
    dup = auth_client.post("/quotes", json={"number": q["number"], "issue_date": "2026-09-01",
                                            "valid_until": "2026-10-01", "amount": 1000})
    assert dup.status_code == 409
    assert auth_client.post("/quotes", json={"issue_date": "2026-09-10", "valid_until": "2026-09-01",
                                             "amount": 1000}).status_code == 422
    assert auth_client.post("/quotes", json={"issue_date": "2026-09-01", "valid_until": "2026-09-30",
                                             "amount": 0}).status_code == 422
    assert auth_client.post("/quotes", json={"issue_date": "2026-09-01", "valid_until": "2026-09-30",
                                             "amount": 1000, "entity_id": str(uuid.uuid4())}).status_code == 422
    assert auth_client.post("/quotes", json={"issue_date": "2026-09-01", "valid_until": "2026-09-30",
                                             "amount": 1000, "status": "accepted"}).status_code == 422


def test_lifecycle_sent_accepted_locked_and_reopened(auth_client, customer):
    q = _quote(auth_client, customer)
    qid = q["id"]
    r = auth_client.patch(f"/quotes/{qid}", json={"items": [
        {"product_name": "Design", "quantity": 3, "unit_price": 1_000_000, "tax_rate": 10}]})
    assert r.status_code == 200 and r.json()["amount"] == 3_300_000 and len(r.json()["items"]) == 1

    sent = auth_client.patch(f"/quotes/{qid}", json={"status": "sent"}).json()
    assert sent["status"] == "sent" and sent["sent_at"]
    acc = auth_client.patch(f"/quotes/{qid}", json={"status": "accepted"}).json()
    assert acc["status"] == "accepted" and acc["decided_at"]
    # Accepted is locked for content…
    assert auth_client.patch(f"/quotes/{qid}", json={"description": "late change"}).status_code == 409
    # …"converted" can only come from the convert action…
    assert auth_client.patch(f"/quotes/{qid}", json={"status": "converted"}).status_code == 422
    # …and it can be reopened for edits.
    back = auth_client.patch(f"/quotes/{qid}", json={"status": "draft"}).json()
    assert back["status"] == "draft" and back["decided_at"] is None and back["sent_at"]
    assert auth_client.patch(f"/quotes/{qid}", json={"description": "revised"}).json()["description"] == "revised"
    assert auth_client.patch(f"/quotes/{qid}", json={"valid_until": "2026-08-01"}).status_code == 422


def test_expired_is_derived_and_filterable(auth_client, customer):
    old = _quote(auth_client, customer, issue_date="2026-01-01", valid_until="2026-01-31", status="sent")
    fresh = _quote(auth_client, customer)
    assert old["status"] == "sent" and old["effective_status"] == "expired"
    expired = auth_client.get("/quotes", params={"status": "expired", "entity_id": customer["id"]}).json()
    assert [x["id"] for x in expired] == [old["id"]]
    mine = auth_client.get("/quotes", params={"entity_id": customer["id"]}).json()
    assert {x["id"] for x in mine} == {old["id"], fresh["id"]}
    # Extending the validity clears it.
    ext = auth_client.patch(f"/quotes/{old['id']}", json={"valid_until": "2099-01-01"}).json()
    assert ext["effective_status"] == "sent"


def test_convert_creates_the_invoice_and_locks_the_quote(auth_client, db, customer):
    q = _quote(auth_client, customer)
    auth_client.patch(f"/quotes/{q['id']}", json={"status": "accepted"})
    before = _txn_count(db)
    r = auth_client.post(f"/quotes/{q['id']}/convert", json={"issue_date": "2026-09-20"})
    assert r.status_code == 201, r.text
    inv, locked = r.json()["invoice"], r.json()["quote"]
    assert inv["kind"] == "sales" and inv["status"] == "issued"
    assert inv["amount"] == q["amount"] and inv["tax_total"] == q["tax_total"]
    assert [i["product_name"] for i in inv["items"]] == ["Design", "Hosting (exempt)"]
    assert inv["entity_id"] == customer["id"]
    assert inv["due_date"] == (date(2026, 9, 20) + timedelta(days=15)).isoformat()  # customer's "Net 15"
    assert locked["status"] == "converted" and locked["converted_invoice_id"] == inv["id"]
    assert locked["converted_invoice_number"] == inv["number"]

    # AR recognised once, balanced.
    assert _txn_count(db) == before + 1
    tid = uuid.UUID(inv["transaction_id"])
    dr = db.execute(select(func.sum(TransactionLine.debit)).where(TransactionLine.transaction_id == tid)).scalar()
    cr = db.execute(select(func.sum(TransactionLine.credit)).where(TransactionLine.transaction_id == tid)).scalar()
    assert int(dr) == int(cr) == q["amount"]

    # The quote is now history: no edits, no deletion, no second invoice.
    assert auth_client.patch(f"/quotes/{q['id']}", json={"status": "draft"}).status_code == 409
    assert auth_client.delete(f"/quotes/{q['id']}").status_code == 409
    assert auth_client.post(f"/quotes/{q['id']}/convert", json={}).status_code == 409
    # The invoice remembers where it came from.
    events = auth_client.get(f"/invoices/{inv['id']}/timeline").json()
    assert any(e["event"] == "quote" and q["number"] in e["detail"] for e in events)


def test_convert_is_atomic_on_a_number_clash(auth_client, db, customer):
    taken = auth_client.post("/invoices", json={
        "number": f"CLASH-{uuid.uuid4().hex[:5]}", "kind": "sales", "status": "draft",
        "issue_date": "2026-09-01", "due_date": "2026-09-30", "amount": 1000, "entity_id": customer["id"],
    }).json()
    q = _quote(auth_client, customer)
    before = _txn_count(db)
    r = auth_client.post(f"/quotes/{q['id']}/convert", json={"number": taken["number"]})
    assert r.status_code == 409
    again = auth_client.get(f"/quotes/{q['id']}").json()
    assert again["status"] == "draft" and again["converted_invoice_id"] is None
    assert _txn_count(db) == before


def test_convert_as_draft_posts_nothing_and_declined_cannot_convert(auth_client, db, customer):
    q = _quote(auth_client, customer, amount=900_000, items=[])
    assert q["amount"] == 900_000 and q["items"] == []
    before = _txn_count(db)
    r = auth_client.post(f"/quotes/{q['id']}/convert", json={"status": "draft", "due_date": "2026-10-30",
                                                              "issue_date": "2026-10-01"})
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    assert inv["status"] == "draft" and inv["transaction_id"] is None and inv["amount"] == 900_000
    assert inv["due_date"] == "2026-10-30"
    assert _txn_count(db) == before

    d = _quote(auth_client, customer)
    auth_client.patch(f"/quotes/{d['id']}", json={"status": "declined"})
    assert auth_client.post(f"/quotes/{d['id']}/convert", json={}).status_code == 409
    assert auth_client.post(f"/quotes/{d['id']}/convert", json={"status": "paid"}).status_code in (409, 422)
    bad = _quote(auth_client, customer)
    assert auth_client.post(f"/quotes/{bad['id']}/convert", json={"status": "paid"}).status_code == 422
    assert auth_client.post(f"/quotes/{bad['id']}/convert",
                            json={"issue_date": "2026-10-10", "due_date": "2026-10-01"}).status_code == 422


def test_delete_draft_and_audit_trail(auth_client, db, customer):
    from app.models.audit_log import AuditLog
    q = _quote(auth_client, customer)
    auth_client.patch(f"/quotes/{q['id']}", json={"status": "sent"})
    assert auth_client.delete(f"/quotes/{q['id']}").status_code == 204
    assert auth_client.get(f"/quotes/{q['id']}").status_code == 404
    actions = {a.action for a in db.execute(select(AuditLog).where(
        AuditLog.entity_type == "quote", AuditLog.entity_id == q["id"])).scalars().all()}
    assert {"create", "update", "delete"} <= actions


def test_quote_pdf_uses_quote_title_and_validity(auth_client, customer, monkeypatch):
    from app.services.documents import render as render_mod
    captured = {}
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: captured.setdefault("ctx", ctx) and b"%PDF-1.4 fake")
    q = _quote(auth_client, customer)
    r = auth_client.get(f"/quotes/{q['id']}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert f'quote-{q["number"]}.pdf' in r.headers["content-disposition"]
    ctx = captured["ctx"]
    assert ctx["title"] in ("QUOTE", "پیش‌فاکتور")
    labels = [m["label"] for m in ctx["meta"]]
    assert ctx["labels"]["valid_until"] in labels and ctx["labels"]["due_date"] not in labels


def test_invoice_pdf_title_unchanged(auth_client, customer, monkeypatch):
    from app.services.documents import render as render_mod
    captured = {}
    monkeypatch.setattr(render_mod, "render_pdf", lambda ctx, *a, **k: captured.setdefault("ctx", ctx) and b"%PDF-1.4 fake")
    inv = auth_client.post("/invoices", json={
        "number": f"PDF-{uuid.uuid4().hex[:5]}", "kind": "sales", "status": "draft",
        "issue_date": "2026-09-01", "due_date": "2026-09-30", "amount": 1000, "entity_id": customer["id"],
    }).json()
    assert auth_client.get(f"/invoices/{inv['id']}/pdf").status_code == 200
    assert captured["ctx"]["title"] in ("INVOICE", "صورتحساب")
    assert captured["ctx"]["labels"]["due_date"] in [m["label"] for m in captured["ctx"]["meta"]]


def test_suggest_number_ignores_like_wildcards_and_other_series(db):
    from app.api.invoices import suggest_number
    from app.models.quote import Quote
    p = f"Z{uuid.uuid4().hex[:4]}_"
    for n in (f"{p}7", f"{p}12", f"{p}x3", f"{p[:-1]}A99"):  # A99 would match "_" as a wildcard
        db.add(Quote(number=n, status="draft", issue_date=date(2026, 1, 1), valid_until=date(2026, 1, 2), amount=1))
    db.flush()
    assert suggest_number(db, Quote, p, start=1) == f"{p}13"
    assert suggest_number(db, Quote, f"none-{uuid.uuid4().hex[:4]}-", start=500).endswith("-500")
    db.rollback()
