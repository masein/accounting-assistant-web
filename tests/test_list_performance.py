"""Lists and reports on a busy year don't run a query per row (scenario K2).

On a year of books (20,000 journals, 3,300 invoices) the Invoices page took
12.7 s and 25,171 queries, the مودیان list 5.9 s, receivables aging 1,495
queries and every bell poll 514 — each read its rows one invoice or one
notification at a time. These tests pin the batched reads: the number of SQL
statements must not grow with the number of invoices, and the batched figures
are the same as the one-at-a-time ones they replaced.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.tenant import use_company
from app.models.invoice import Invoice
from app.models.notification import Notification
from tests.test_report_performance import count_queries


@pytest.fixture()
def books(client, db):
    """A private Iranian company with a chart, a profile (for مودیان) and an owner."""
    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings
    from app.db.seed import seed_chart_if_empty
    from app.models.company import Company
    from app.models.company_profile import CompanyProfile
    from tests.conftest import _CSRFTestClient
    from tests.test_admin_audit import _purge_company

    c = Company(id=uuid.uuid4(), name="Busy Co", slug=f"busy-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    cid = str(c.id)
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.add(CompanyProfile(legal_name="شرکت پرکار", economic_code="14001234567", national_id="14001234567"))
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True, role="owner",
                               company_id=cid)
    csrf = generate_csrf_token()
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    db.expunge_all()
    api = _CSRFTestClient(client, csrf)
    parties = {}
    for kind, name in (("client", "Client A"), ("client", "Client B"), ("supplier", "Supplier S")):
        r = api.post("/entities", json={"type": kind, "name": f"{name} {uuid.uuid4().hex[:4]}"})
        assert r.status_code == 201, r.text
        parties.setdefault(kind, []).append(r.json()["id"])
    yield {"api": api, "cid": cid, "parties": parties, "n": 0}
    client.cookies.clear()
    _purge_company(db, cid)


def _invoice(books, *, kind="sales", days_ago=40, due_in=30, price=10_000_000):
    books["n"] += 1
    issue = date.today() - timedelta(days=days_ago)
    who = books["parties"]["client" if kind == "sales" else "supplier"]
    r = books["api"].post("/invoices", json={
        "number": f"{'S' if kind == 'sales' else 'B'}-{books['n']}", "kind": kind, "status": "issued",
        "issue_date": issue.isoformat(), "due_date": (issue + timedelta(days=due_in)).isoformat(),
        "amount": 0, "currency": "IRR", "entity_id": who[books["n"] % len(who)],
        "items": [{"product_name": "Service", "quantity": 1, "unit_price": price, "tax_rate": 10}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _pay(books, inv, amount):
    r = books["api"].post(f"/invoices/{inv['id']}/payments",
                          json={"amount": amount, "date": date.today().isoformat()})
    assert r.status_code == 201, r.text


def _credit(books, inv, amount):
    r = books["api"].post(f"/invoices/{inv['id']}/credit-notes",
                          json={"amount": amount, "date": date.today().isoformat()})
    assert r.status_code == 201, r.text


def _round(books, times=1):
    """Every shape a list reads per invoice: overdue and unpaid, paid in full,
    part paid with a reduction, overpaid (a customer credit), an open bill."""
    for _ in range(times):
        _invoice(books)                                    # overdue, unpaid
        paid = _invoice(books, days_ago=5)
        _pay(books, paid, paid["amount"])
        part = _invoice(books, days_ago=20)
        _pay(books, part, part["amount"] // 3)
        _credit(books, part, part["amount"] // 10)
        over = _invoice(books, days_ago=10)
        _pay(books, over, over["amount"] + 5_000_000)      # the extra is the customer's credit
        bill = _invoice(books, kind="purchase", days_ago=45)
        _pay(books, bill, bill["amount"] // 2)


def _queries(books, url, *, warm=False):
    from app.api.reports import invalidate_dashboard_cache
    if warm:                                               # the feed writes its rows on the first poll
        assert books["api"].get(url).status_code == 200
    invalidate_dashboard_cache()
    with count_queries() as q:
        r = books["api"].get(url)
    assert r.status_code == 200, r.text
    return q["n"]


@pytest.mark.parametrize("url, cap, warm", [
    ("/invoices", 12, False),
    ("/invoices?kind=purchase", 12, False),
    ("/moadian/invoices?state=all", 16, False),
    ("/manager-reports/operational/accounts-receivable", 10, False),
    ("/manager-reports/operational/accounts-payable", 10, False),
    ("/notifications/feed", 45, True),
])
def test_query_count_does_not_grow_with_the_invoices(books, url, cap, warm):
    if url.startswith("/moadian"):
        r = books["api"].put("/moadian/settings", json={
            "memory_id": "a11xy9", "default_sstid": "2720000114542", "default_mu": "1627"})
        assert r.status_code == 200, r.text
    _round(books)
    small = _queries(books, url, warm=warm)
    _round(books, 4)
    large = _queries(books, url, warm=warm)
    assert large <= small, f"{url}: {small} → {large} queries — an N+1 crept in"
    assert large <= cap, f"{url}: {large} queries"


def test_the_close_pack_reads_its_aging_in_a_few_queries(books):
    _round(books)
    small = _queries(books, "/manager-reports/close-pack?format=xlsx")
    _round(books, 4)
    large = _queries(books, "/manager-reports/close-pack?format=xlsx")
    assert large <= small, f"close pack: {small} → {large} queries"


def test_the_batched_list_reads_like_one_invoice_at_a_time(books, db):
    from app.api.invoices import _to_read
    _round(books, 2)
    listed = books["api"].get("/invoices").json()
    assert len(listed) == 10
    with use_company(books["cid"]):
        for item in listed:
            one = _to_read(db.get(Invoice, uuid.UUID(item["id"]))).model_dump(mode="json")
            assert item == one, item["number"]
    # every shape really is there: a payment, a reduction, a credit, a party credit
    assert any(i["amount_paid"] and i["balance_due"] == 0 for i in listed)
    assert any(i["credited"] for i in listed)
    assert any(i["credit_available"] for i in listed)
    assert any(i["party_credit"] for i in listed)


@pytest.mark.parametrize("kind, url", [
    ("sales", "/manager-reports/operational/accounts-receivable"),
    ("purchase", "/manager-reports/operational/accounts-payable"),
])
def test_aging_matches_the_invoice_totals(books, db, kind, url):
    from app.api.invoices import _invoice_totals
    _round(books, 2)
    items = books["api"].get(url).json()["items"]
    with use_company(books["cid"]):
        rows = db.execute(select(Invoice).where(Invoice.kind == kind)).scalars().all()
        expect = {str(r.id): _invoice_totals(db, r) for r in rows}
    open_ids = {i for i, (_p, _c, due) in expect.items() if due > 0}
    assert {i["invoice_id"] for i in items} == open_ids and open_ids
    for i in items:
        paid, credited, due = expect[i["invoice_id"]]
        assert (i["amount_paid"], i["credited"], i["balance_due"]) == (paid, credited, due)


def test_the_sums_read_in_chunks_add_up_the_same(books, db, monkeypatch):
    import app.api.invoices as invoices
    _round(books, 2)
    with use_company(books["cid"]):
        rows = list(db.execute(select(Invoice)).scalars())
        whole = invoices.totals_for(db, rows)
        monkeypatch.setattr(invoices, "_CHUNK", 3)
        assert invoices.totals_for(db, rows) == whole


def test_the_feed_still_updates_resolves_and_never_doubles(books, db, monkeypatch):
    from app.services import notification_service as svc
    inv = _invoice(books)                                  # overdue
    key = f"inv-{inv['id']}-overdue"

    def rows():
        db.expire_all()
        with use_company(books["cid"]):
            return db.execute(select(Notification).where(Notification.dedupe_key == key)).scalars().all()

    assert books["api"].get("/notifications/feed").status_code == 200
    (row,) = rows()
    assert row.dismissed_at is None and inv["number"] in row.title

    r = books["api"].patch(f"/invoices/{inv['id']}", json={"number": "RENAMED-1"})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(svc, "_CHUNK", 1)                  # one key per IN list: the chunks still meet
    assert books["api"].get("/notifications/feed").status_code == 200
    (row,) = rows()
    assert "RENAMED-1" in row.title and row.dismissed_at is None   # updated in place, not added again

    _pay(books, inv, inv["amount"])
    assert books["api"].get("/notifications/feed").status_code == 200
    (row,) = rows()
    assert row.dismissed_at is not None                    # paid: the condition cleared


def _journal(db, cid, code_dr, code_cr, amount, days_ago=10):
    from app.models.account import Account
    from app.models.transaction import Transaction, TransactionLine
    with use_company(cid):
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        t = Transaction(id=uuid.uuid4(), date=date.today() - timedelta(days=days_ago), reference="T",
                        description="isolation", currency="IRR")
        db.add(t)
        db.flush()
        db.add_all([
            TransactionLine(transaction_id=t.id, account_id=acc[code_dr].id, debit=amount, credit=0,
                            base_debit=amount, base_credit=0),
            TransactionLine(transaction_id=t.id, account_id=acc[code_cr].id, debit=0, credit=amount,
                            base_debit=0, base_credit=amount),
        ])
        db.commit()


@pytest.fixture()
def neighbour(db):
    """Another company with a big, odd-numbered sale of its own."""
    from app.db.seed import seed_chart_if_empty
    from app.models.company import Company
    from tests.test_admin_audit import _purge_company
    c = Company(id=uuid.uuid4(), name="Neighbour", slug=f"nb-{uuid.uuid4().hex[:6]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
    yield str(c.id)
    _purge_company(db, str(c.id))


def _sales_code(db, cid):
    from app.models.account import Account
    with use_company(cid):
        codes = sorted(a.code for a in db.execute(select(Account)).scalars())
    return next(c for c in codes if c.startswith("41") and len(c) >= 4)


def test_the_plain_reads_stay_inside_the_company(books, neighbour, db):
    """The insights window and the CFO report read plain rows rather than ORM
    objects; the company filter must still apply to them."""
    from app.services import insight_service
    from app.services.cfo_intelligence import _load_monthly_data
    odd = 987_654_321
    _journal(db, neighbour, "1110", _sales_code(db, neighbour), odd)
    _journal(db, books["cid"], "1110", _sales_code(db, books["cid"]), 1_000)
    with use_company(books["cid"]):
        token = insight_service._ROWS.set({"today": date.today()})
        try:
            window = insight_service._window_rows(db)
        finally:
            insight_service._ROWS.reset(token)
        amounts = {ln.debit for t in window for ln in t.lines} | {ln.credit for t in window for ln in t.lines}
        assert 1_000 in amounts and odd not in amounts
        data = _load_monthly_data(db)
        assert sum(data["monthly_revenue"].values()) == 1_000
        assert data["total_cash"] == 1_000
