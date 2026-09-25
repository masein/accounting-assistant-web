"""Tenant isolation over HTTP (roadmap 2026-09 §6, suite 2). Company A owns
one of every object type; company B's owner calls GET/PATCH/DELETE/PDF on
those ids and must get 404 (never the data, never a change, never a 500).
Then A's rows are checked untouched. The ORM-level filter is tested in
test_multitenant_isolation.py; this proves it holds through every route."""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import tenant_bypass, use_company
from app.models.adjustment import Adjustment
from app.models.company import Company
from app.models.entity import Entity
from app.models.equity import Shareholding
from app.models.invoice import Invoice
from app.models.pay_run import PayRun, PayRunLine
from app.models.purchase_order import PurchaseOrder
from app.models.quote import Quote, QuoteItem
from app.models.recurring import RecurringRule
from app.models.time_billing import TimeEntry
from app.models.transaction import Transaction, TransactionAttachment
from tests.conftest import _CSRFTestClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _company(db, name):
    c = Company(id=uuid.uuid4(), name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c); db.flush()
    return c


def _owner(client, company_id):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="o", is_admin=True,
                               role="owner", company_id=str(company_id))
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


@pytest.fixture()
def world(db, tmp_path):
    """Company A with one of everything; company B empty."""
    a, b = _company(db, "A"), _company(db, "B")
    db.commit()
    a_id, b_id = a.id, b.id
    d = date(2026, 6, 15)
    with use_company(str(a_id)):
        client_ent = Entity(id=uuid.uuid4(), name="A Client", type="client")
        emp = Entity(id=uuid.uuid4(), name="A Employee", type="employee")
        holder = Entity(id=uuid.uuid4(), name="A Holder", type="shareholder")
        db.add_all([client_ent, emp, holder]); db.flush()
        txn = Transaction(id=uuid.uuid4(), date=d, description="A voucher", currency="IRR")
        db.add(txn); db.flush()
        from app.api.transactions import UPLOADS_DIR
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        stored = UPLOADS_DIR / f"{uuid.uuid4().hex}.png"
        stored.write_bytes(PNG)
        att = TransactionAttachment(id=uuid.uuid4(), transaction_id=txn.id, file_name="a.png",
                                    file_path=str(stored), content_type="image/png", size_bytes=len(PNG))
        inv = Invoice(id=uuid.uuid4(), number=f"A-{uuid.uuid4().hex[:5]}", kind="sales", status="issued",
                      issue_date=d, due_date=d, amount=5_000, currency="IRR", entity_id=client_ent.id)
        po = PurchaseOrder(id=uuid.uuid4(), number=f"PO-{uuid.uuid4().hex[:5]}", order_date=d, status="draft",
                           currency="IRR", entity_id=client_ent.id, description="A order")
        run = PayRun(id=uuid.uuid4(), period_start=d, period_end=d, pay_date=d, currency="IRR", status="posted")
        quote = Quote(id=uuid.uuid4(), number=f"QA-{uuid.uuid4().hex[:5]}", status="draft", issue_date=d,
                      valid_until=d, amount=7_000, currency="IRR", entity_id=client_ent.id)
        db.add_all([att, inv, po, run, quote]); db.flush()
        db.add(QuoteItem(id=uuid.uuid4(), quote_id=quote.id, product_name="A line", quantity=1,
                         unit_price=7_000, line_total=7_000))
        db.add(PayRunLine(id=uuid.uuid4(), run_id=run.id, entity_id=emp.id, employee_name=emp.name,
                          gross=100_000, net_pay=80_000))
        sh = Shareholding(id=uuid.uuid4(), entity_id=holder.id, shares=100, percent=100)
        te = TimeEntry(id=uuid.uuid4(), employee_id=emp.id, client_id=client_ent.id, work_date=d, hours=3,
                       status="unbilled", description="A time")
        rule = RecurringRule(id=uuid.uuid4(), name="A rent", direction="payment", frequency="monthly",
                             amount=1_000, start_date=d, next_run_date=d, auto_post=False, status="active")
        adj = Adjustment(id=uuid.uuid4(), kind="accrual", amount=1_000, start_date=d, direction="expense",
                         description="A accrual")
        db.add_all([sh, te, rule, adj])
        db.commit()
    ids = {"txn": txn.id, "att": att.id, "inv": inv.id, "po": po.id, "run": run.id, "emp": emp.id,
           "quote": quote.id,
           "sh": sh.id, "te": te.id, "rule": rule.id, "adj": adj.id, "att_path": str(stored)}
    # The app shares this session in tests. Rows still sitting in its identity
    # map would be handed back by session.get() WITHOUT a query — and the tenant
    # filter lives in the query. A real request starts with an empty session,
    # so make this one look the same.
    db.expunge_all()
    return {"a": a_id, "b": b_id, **ids}


def _cross_tenant_calls(w):
    """(method, url, json) that company B fires at company A's ids."""
    return [
        ("get", f"/transactions/{w['txn']}", None),
        ("patch", f"/transactions/{w['txn']}", {"description": "tampered"}),
        ("delete", f"/transactions/{w['txn']}", None),
        ("get", f"/transactions/attachments/{w['att']}/file", None),
        ("delete", f"/transactions/attachments/{w['att']}", None),
        ("get", f"/invoices/{w['inv']}/timeline", None),
        ("get", f"/invoices/{w['inv']}/payments", None),
        ("get", f"/invoices/{w['inv']}/pdf", None),
        ("patch", f"/invoices/{w['inv']}", {"description": "tampered"}),
        ("delete", f"/invoices/{w['inv']}", None),
        ("get", f"/purchase-orders/{w['po']}", None),
        ("get", f"/purchase-orders/{w['po']}/pdf", None),
        ("patch", f"/purchase-orders/{w['po']}", {"description": "tampered"}),
        ("get", f"/payroll/runs/{w['run']}", None),
        ("get", f"/payroll/runs/{w['run']}/payslip/{w['emp']}", None),
        ("get", f"/payroll/runs/{w['run']}/payslip/{w['emp']}/pdf", None),
        ("patch", f"/equity/shareholdings/{w['sh']}", {"percent": 1}),
        ("delete", f"/equity/shareholdings/{w['sh']}", None),
        ("patch", f"/time/entries/{w['te']}", {"hours": 1}),
        ("delete", f"/time/entries/{w['te']}", None),
        ("patch", f"/recurring/{w['rule']}", {"name": "tampered"}),
        ("delete", f"/recurring/{w['rule']}", None),
        ("get", f"/adjustments/{w['adj']}", None),
        ("get", f"/quotes/{w['quote']}", None),
        ("get", f"/quotes/{w['quote']}/pdf", None),
        ("patch", f"/quotes/{w['quote']}", {"description": "tampered"}),
        ("post", f"/quotes/{w['quote']}/convert", {}),
        ("delete", f"/quotes/{w['quote']}", None),
    ]


def test_company_b_cannot_see_or_change_company_a_objects(client, db, world):
    w = world
    b = _owner(client, w["b"])
    leaks = []
    for method, url, body in _cross_tenant_calls(w):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(b, method)(url, **kwargs)
        if r.status_code not in (403, 404):
            leaks.append((method.upper(), url, r.status_code))
    assert not leaks, f"company B reached company A's objects: {leaks}"

    # A's rows are untouched (and the file still exists)
    db.expire_all()
    with tenant_bypass():
        assert db.get(Transaction, w["txn"]).description == "A voucher"
        assert db.get(Transaction, w["txn"]).deleted_at is None
        assert db.get(TransactionAttachment, w["att"]) is not None
        assert db.get(Invoice, w["inv"]).description is None
        assert db.get(PurchaseOrder, w["po"]).description == "A order"
        assert float(db.get(Shareholding, w["sh"]).percent) == 100
        assert float(db.get(TimeEntry, w["te"]).hours) == 3
        assert db.get(RecurringRule, w["rule"]).name == "A rent"
        q = db.get(Quote, w["quote"])
        assert q is not None and q.status == "draft" and q.description is None and q.converted_invoice_id is None
    assert Path(w["att_path"]).exists()


def test_company_a_still_reaches_its_own_objects(client, world):
    w = world
    a = _owner(client, w["a"])
    ok = {
        f"/transactions/{w['txn']}", f"/transactions/attachments/{w['att']}/file",
        f"/invoices/{w['inv']}/timeline", f"/purchase-orders/{w['po']}",
        f"/payroll/runs/{w['run']}", f"/adjustments/{w['adj']}", f"/quotes/{w['quote']}",
    }
    for url in ok:
        r = a.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])


def test_list_endpoints_never_include_the_other_company(client, world):
    w = world
    b = _owner(client, w["b"])
    for url, key in (("/transactions", "id"), ("/invoices", "id"), ("/purchase-orders", "id"),
                     ("/payroll/runs", "id"), ("/recurring", "id"), ("/entities", "id"), ("/equity/cap-table", None),
                     ("/quotes", "id")):
        r = b.get(url)
        assert r.status_code == 200, (url, r.status_code)
        text = r.text
        for obj in ("txn", "inv", "po", "run", "rule", "emp", "sh", "quote"):
            assert str(w[obj]) not in text, (url, obj)
