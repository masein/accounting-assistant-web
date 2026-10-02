"""The books are written in the company's language (app/services/book_text.py).

An Iranian company's general journal read "Invoice ARM-1805 — receivable",
"Payment for invoice …", "Opening balance", "Dividend declared — …" — every
description the app writes itself was an English f-string (deep browser
test, 2026-10-02). An ``ir`` company's postings are now Persian, with Jalali
dates; every other company's read exactly as before."""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.transaction import Transaction, TransactionLine
from app.services import book_text

FA = re.compile(r"[؀-ۿ]")


@pytest.fixture()
def books(client, db):
    """A company in ``locale`` with its chart, and an owner's API client for it."""
    from tests.test_admin_audit import _purge_company
    from tests.test_statement_export import _company
    made = []

    def make(locale):
        api, cid = _company(client, db, locale, "IRR")
        made.append(cid)
        return api, cid
    yield make
    for cid in made:
        _purge_company(db, cid)


def _texts(db, txn_id) -> list[str]:
    txn = db.get(Transaction, uuid.UUID(str(txn_id)))
    lines = db.execute(select(TransactionLine).where(TransactionLine.transaction_id == txn.id)).scalars().all()
    return [txn.description or ""] + [ln.line_description or "" for ln in lines]


def _invoice_cycle(auth_client, db):
    ent = auth_client.post("/entities", json={"type": "client", "name": f"مشتری {uuid.uuid4().hex[:6]}"}).json()
    sup = auth_client.post("/entities", json={"type": "supplier", "name": f"تأمین {uuid.uuid4().hex[:6]}"}).json()
    inv = auth_client.post("/invoices", json={"number": f"BL-{uuid.uuid4().hex[:6]}", "kind": "sales", "status": "issued",
                                              "issue_date": "2026-09-23", "due_date": "2026-10-23", "amount": 1_090_000,
                                              "tax_total": 90_000, "subtotal": 1_000_000, "currency": "IRR",
                                              "entity_id": ent["id"]})
    assert inv.status_code == 201, inv.text
    inv = inv.json()
    pay = auth_client.post(f"/invoices/{inv['id']}/payments", json={"amount": 1_090_000, "date": "2026-09-25", "method": "bank"})
    assert pay.status_code == 201, pay.text
    bill = auth_client.post("/invoices", json={"number": f"BB-{uuid.uuid4().hex[:6]}", "kind": "purchase", "status": "issued",
                                               "issue_date": "2026-09-23", "due_date": "2026-10-23", "amount": 400_000,
                                               "currency": "IRR", "entity_id": sup["id"]})
    assert bill.status_code == 201, bill.text
    bill = bill.json()
    texts = _texts(db, inv["transaction_id"]) + _texts(db, pay.json()["transaction_id"]) + _texts(db, bill["transaction_id"])
    return inv, bill, texts


def test_an_iranian_companys_invoices_bills_and_payments_are_written_in_persian(books, db):
    api, cid = books("ir")
    with use_company(cid):
        inv, bill, texts = _invoice_cycle(api, db)
    english = [t for t in texts if t and not FA.search(t)]
    assert english == [], english
    assert f"صدور فاکتور فروش {inv['number']}" in texts
    assert f"ثبت فاکتور خرید {bill['number']}" in texts
    assert f"دریافت وجه فاکتور {inv['number']}" in texts


def test_every_other_companys_books_read_as_before(books, db):
    api, cid = books("default")
    with use_company(cid):
        inv, bill, texts = _invoice_cycle(api, db)
    assert f"Invoice {inv['number']} issued" in texts
    assert f"Invoice {inv['number']} — receivable" in texts
    assert f"Payment for invoice {inv['number']}" in texts
    assert not any(FA.search(t) for t in texts if t and not t.startswith(("مشتری", "تأمین")))


def test_dates_inside_a_description_are_jalali_in_persian_books(books, db):
    _, ir = books("ir")
    _, uk = books("uk")
    with use_company(ir):
        assert book_text.book_date(db, date(2026, 10, 1)) == "1405/07/09"
        assert book_text.bt(db, "pr_run", start=book_text.book_date(db, date(2026, 9, 23)),
                            end=book_text.book_date(db, date(2026, 10, 22))) == "حقوق و دستمزد 1405/07/01 تا 1405/07/30"
    with use_company(uk):
        assert book_text.book_date(db, date(2026, 10, 1)) == "2026-10-01"
        assert book_text.bt(db, "inv_issued", number="X-1") == "Invoice X-1 issued"


def test_a_converted_quote_and_a_bank_fee_read_in_persian_too(books, db):
    from app.services.transaction_fee import build_fee_line_items
    api, cid = books("ir")
    with use_company(cid):
        ent = api.post("/entities", json={"type": "client", "name": f"مشتری {uuid.uuid4().hex[:6]}"}).json()
        q = api.post("/quotes", json={"issue_date": "2026-09-01", "valid_until": "2099-12-31", "currency": "IRR",
                                      "entity_id": ent["id"], "items": [{"product_name": "طراحی", "quantity": 1,
                                                                         "unit_price": 1_000_000}]})
        assert q.status_code == 201, q.text
        r = api.post(f"/quotes/{q.json()['id']}/convert", json={"issue_date": "2026-09-20"})
        assert r.status_code in (200, 201), r.text
        inv = r.json()["invoice"]
        assert inv["description"] == f"پیش‌فاکتور {q.json()['number']}"
        assert all(FA.search(t) for t in _texts(db, inv["transaction_id"]) if t), _texts(db, inv["transaction_id"])
    fee = build_fee_line_items(5_000, "card", "ملت", lang="fa")
    assert [ln["line_description"] for ln in fee] == ["کارمزد تراکنش - کارت به کارت از طریق ملت", "کسر کارمزد بانک - ملت"]
    assert [ln["line_description"] for ln in build_fee_line_items(5_000, "card", "HSBC")] == \
        ["Transaction fee - Card-to-Card via HSBC", "Bank fee deduction - HSBC"]


def test_a_bank_partys_own_account_is_named_in_persian(books, db):
    """Adding «بانک ملت» opened the account "بانک ملت — bank account" (finding #22)."""
    from app.models.account import Account
    api, cid = books("ir")
    with use_company(cid):
        r = api.post("/entities", json={"type": "bank", "name": "بانک ملت"})
        assert r.status_code in (200, 201), r.text
        names = [a.name for a in db.execute(select(Account)).scalars() if "ملت" in (a.name or "")]
    assert names == ["حساب بانکی بانک ملت"], names


def test_every_text_has_both_languages_and_the_same_placeholders():
    for key, said in book_text.TEXT.items():
        assert set(said) == {"en", "fa"}, key
        fields = lambda s: sorted(re.findall(r"\{(\w+)\}", s))
        assert fields(said["en"]) == fields(said["fa"]), key
        assert FA.search(said["fa"]), key


POSTING_MODULES = [
    "app/api/invoices.py", "app/api/payroll.py", "app/api/expenses.py", "app/api/adjustments.py", "app/api/fx.py",
    "app/api/petty_cash.py", "app/services/equity_service.py", "app/services/chart_service.py",
    "app/services/fixed_assets.py", "app/services/time_billing_service.py", "app/services/purchase_billing.py",
    "app/services/inventory_costing.py", "app/services/recurring_service.py", "app/services/reporting/ledger_service.py",
    "app/api/brain.py", "app/api/quotes.py", "app/services/journal_import.py", "app/services/transaction_fee.py",
    "app/services/ai_accountant/statement_intake.py",
]
ROOT = Path(__file__).resolve().parents[1]
# An English literal where the books get their words: a description (also a
# dict's "line_description" and a description's `or "…"` fallback), a line's
# text in a (code, debit, credit, "text") tuple.
_LITERAL = re.compile(r"""(?:\b(?:line_)?description=\(?|["'](?:line_)?description["']:\s*|, (?:0|amount|amt|[a-z_.]+), |"""
                      r"""\bdescription\b[^"'#]*?\bor\s+\(?)f?["']([A-Z][a-z][^"']*)["']""")


_API_DOC = re.compile(r"^\s*(?:None|False|True|\.\.\.|-?[\d.]+),\s*description=")


def test_the_posting_code_writes_no_english_of_its_own():
    found = []
    for rel in POSTING_MODULES:
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            # API docs (a Field/Query/Form's description, also on its own line after
            # the default), error details, and the legacy English PDF's item name
            if any(x in line for x in ("Field(", "Query(", "Form(", "detail=", "ge=", "le=", "product_name=")) \
                    or _API_DOC.match(line) or line.lstrip().startswith("#"):
                continue
            for m in _LITERAL.finditer(line):
                found.append(f"{rel}:{n} {m.group(1)!r}")
    assert found == [], found
