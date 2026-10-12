"""A document's status in words (scenario S1): an invoice, quote or purchase
order PDF printed its status as stored, «partially_paid»."""
from __future__ import annotations

import io

import pytest

from app.services.documents.labels import STATUS, status_label
from tests.test_statement_export import _company


@pytest.fixture()
def uk(client, db):
    from tests.test_admin_audit import _purge_company
    api, cid = _company(client, db, "uk", "GBP")
    yield api, cid
    client.cookies.clear()
    _purge_company(db, cid)


@pytest.mark.parametrize("status, en, fa", [
    ("partially_paid", "Partially paid", "بخشی پرداخت‌شده"),
    ("sent", "Sent", "ارسال‌شده"),
    ("partially_received", "Partially received", "دریافت جزئی"),
    ("converted", "Invoiced", "فاکتور شده"),
    ("on_hold", "On hold", "On hold"),                      # one not listed still reads as words
])
def test_a_status_in_words(status, en, fa):
    assert status_label(status, "uk") == en and status_label(status, "default") == en
    assert status_label(status, "ir") == fa


def test_no_status_keeps_an_underscore():
    assert all("_" not in en and "_" not in fa for en, fa in STATUS.values())


def test_s1_the_invoice_pdf_says_partially_paid(uk):
    from pypdf import PdfReader
    api, _ = uk
    ent = api.post("/entities", json={"type": "client", "name": "Aria Design Ltd"}).json()
    inv = api.post("/invoices", json={"number": "INV-2001", "kind": "sales", "status": "issued",
                                      "issue_date": "2026-10-01", "due_date": "2026-10-31", "amount": 1200,
                                      "currency": "GBP", "entity_id": ent["id"]})
    assert inv.status_code == 201, inv.text
    pay = api.post(f"/invoices/{inv.json()['id']}/payments", json={"amount": "400.50", "date": "2026-10-05", "method": "bank"})
    assert pay.status_code == 201, pay.text
    pdf = api.get(f"/invoices/{inv.json()['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Partially paid" in text and "partially_paid" not in text
