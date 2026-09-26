"""Report performance guards (roadmap 2026-09 §2.6).

The ledger summary is summed in the database, the dashboard reads flat rows
and the journal list eager-loads everything it serialises. These tests pin
that: the number of SQL statements must not grow with the number of
journals (an N+1 fails here long before it is slow in production), and the
figures on a small, known ledger are exact.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import event, select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.seed import seed_chart_if_empty
from app.db.tenant import use_company
from app.models.account import Account
from app.models.company import Company
from app.models.entity import Entity, TransactionEntity
from app.models.transaction import Transaction, TransactionAttachment, TransactionLine


@contextmanager
def count_queries():
    from tests.conftest import _engine
    box = {"n": 0}

    def _count(*_a, **_k):
        box["n"] += 1
    event.listen(_engine, "before_cursor_execute", _count)
    try:
        yield box
    finally:
        event.remove(_engine, "before_cursor_execute", _count)


@pytest.fixture()
def books(db, client):
    """A private Iranian company with a chart, two clients and a supplier."""
    c = Company(id=uuid.uuid4(), name="Perf", slug=f"perf-{uuid.uuid4().hex[:8]}", locale="ir",
                base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.commit()
    with use_company(c.id):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        acc = {a.code: a for a in db.execute(select(Account)).scalars()}
        ents = [Entity(id=uuid.uuid4(), name=n, type=t, company_id=c.id)
                for n, t in (("Client A", "client"), ("Client B", "client"), ("Supplier S", "supplier"))]
        db.add_all(ents)
        db.commit()
    tok = create_session_token(user_id=str(uuid.uuid4()), username="perf-owner", is_admin=True,
                               company_id=str(c.id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    yield {"company": c, "acc": acc, "ents": ents, "client": client}
    from tests.test_admin_audit import _purge_company
    _purge_company(db, str(c.id))


def _journal(db, books, *, debit_code, credit_code, amount, days_ago=5, entity=None, role="client",
             reference="R", attachment=False, currency="IRR", deleted=False, desc="line"):
    c, acc = books["company"], books["acc"]
    with use_company(c.id):
        t = Transaction(id=uuid.uuid4(), date=date.today() - timedelta(days=days_ago), reference=reference,
                        description="j", currency=currency)
        if deleted:
            from datetime import datetime, timezone
            t.deleted_at = datetime.now(timezone.utc)
        db.add(t)
        db.flush()
        db.add_all([
            TransactionLine(transaction_id=t.id, account_id=acc[debit_code].id, debit=amount, credit=0,
                            line_description=desc),
            TransactionLine(transaction_id=t.id, account_id=acc[credit_code].id, debit=0, credit=amount,
                            line_description=desc),
        ])
        if entity is not None:
            db.add(TransactionEntity(transaction_id=t.id, entity_id=entity.id, role=role))
        if attachment:
            db.add(TransactionAttachment(transaction_id=t.id, file_name="r.pdf",
                                         file_path=f"/tmp/perf-{uuid.uuid4().hex}.pdf",
                                         content_type="application/pdf", size_bytes=1))
        db.commit()
    return t


def _pick(acc, prefix):
    return sorted(code for code in acc if code.startswith(prefix) and len(code) >= 4)[0]


def _many(db, books, n):
    acc = books["acc"]
    cash, rev, exp = "1110", _pick(acc, "41"), _pick(acc, "6")
    for i in range(n):
        if i % 2:
            _journal(db, books, debit_code=cash, credit_code=rev, amount=1000 + i, entity=books["ents"][i % 2],
                     attachment=(i % 3 == 0), reference=f"S{i}")
        else:
            _journal(db, books, debit_code=exp, credit_code=cash, amount=500 + i, entity=books["ents"][2],
                     role="supplier", attachment=(i % 4 == 0), reference=f"P{i}")


def _queries(books, url):
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    with count_queries() as q:
        r = books["client"].get(url)
    assert r.status_code == 200, r.text
    return q["n"]


@pytest.mark.parametrize("url", [
    "/transactions?limit=50",
    "/reports/ledger-summary",
    "/reports/owner-dashboard",
])
def test_query_count_does_not_grow_with_the_books(db, books, url):
    _many(db, books, 12)
    small = _queries(books, url)
    _many(db, books, 36)
    large = _queries(books, url)
    assert large <= small, f"{url}: {small} → {large} queries — an N+1 crept in"
    assert large <= 20, f"{url}: {large} queries"


def test_ledger_summary_figures(db, books):
    acc = books["acc"]
    cash, rev, exp = "1110", _pick(acc, "41"), _pick(acc, "6")
    _journal(db, books, debit_code=cash, credit_code=rev, amount=10_000)
    _journal(db, books, debit_code=cash, credit_code=rev, amount=5_000)
    _journal(db, books, debit_code=exp, credit_code=cash, amount=3_000)
    _journal(db, books, debit_code=exp, credit_code=cash, amount=99_999, deleted=True)   # undone: never counts
    _journal(db, books, debit_code=cash, credit_code=rev, amount=77_777, currency="USD")  # other currency view
    d = books["client"].get("/reports/ledger-summary?currency=IRR").json()
    rows = {r["account_code"]: r for r in d["rows"]}
    assert rows[cash]["debit_turnover"] == 15_000 and rows[cash]["credit_turnover"] == 3_000
    assert rows[cash]["debit_balance"] == 12_000 and rows[cash]["credit_balance"] == 0
    assert rows[rev]["credit_balance"] == 15_000
    assert rows[exp]["debit_balance"] == 3_000
    assert rows[cash]["parent_code"]                      # the کل group is still reported
    assert d["total_debit_turnover"] == d["total_credit_turnover"] == 18_000
    assert "USD" in d["other_currencies"]
    usd = books["client"].get("/reports/ledger-summary?currency=USD").json()
    assert {r["account_code"]: r["debit_turnover"] for r in usd["rows"]}[cash] == 77_777


def test_ledger_summary_never_counts_another_company(db, books, client):
    acc = books["acc"]
    _journal(db, books, debit_code="1110", credit_code=_pick(acc, "41"), amount=1_234)
    other = Company(id=uuid.uuid4(), name="Else", slug=f"else-{uuid.uuid4().hex[:8]}", locale="ir",
                    base_currency="IRR", status="active", token_version=0)
    db.add(other)
    db.commit()
    try:
        with use_company(other.id):
            seed_chart_if_empty(db, locale="ir")
            db.commit()
            oacc = {a.code: a for a in db.execute(select(Account)).scalars()}
            t = Transaction(id=uuid.uuid4(), date=date.today(), reference="X", currency="IRR")
            db.add(t)
            db.flush()
            db.add_all([TransactionLine(transaction_id=t.id, account_id=oacc["1110"].id, debit=9_999_999, credit=0),
                        TransactionLine(transaction_id=t.id, account_id=oacc[_pick(oacc, "41")].id, debit=0,
                                        credit=9_999_999)])
            db.commit()
        d = books["client"].get("/reports/ledger-summary").json()
        assert d["total_debit_turnover"] == 1_234
    finally:
        from tests.test_admin_audit import _purge_company
        _purge_company(db, str(other.id))


def test_dashboard_figures_on_a_known_ledger(db, books):
    acc = books["acc"]
    cash, rev, exp = "1110", _pick(acc, "41"), _pick(acc, "6")
    a, b, s = books["ents"]
    _journal(db, books, debit_code=cash, credit_code=rev, amount=40_000, entity=a)
    _journal(db, books, debit_code=cash, credit_code=rev, amount=10_000, entity=b, reference="")
    _journal(db, books, debit_code=exp, credit_code=cash, amount=7_000, entity=s, role="supplier", attachment=True)
    _journal(db, books, debit_code=exp, credit_code=cash, amount=3_000, desc="")          # no entity, no attachment
    _journal(db, books, debit_code=exp, credit_code=cash, amount=50_000, deleted=True)   # undone
    from app.api.reports import invalidate_dashboard_cache
    invalidate_dashboard_cache()
    d = books["client"].get("/reports/owner-dashboard").json()
    kpis = {k["key"]: k["value"] for k in d["kpis"]}
    assert kpis["cash_on_hand"] == 40_000 + 10_000 - 7_000 - 3_000
    assert kpis["monthly_net_profit"] in (50_000 - 10_000, 0)  # 0 only if the journals fell in last month
    categories = {r["category"]: r["amount"] for r in d["expense_by_category"]}
    assert sum(categories.values()) == 10_000
    vendors = {r["vendor"]: r["amount"] for r in d["spend_by_vendor"]}
    assert vendors == {"Supplier S": 7_000, "Unassigned vendor": 3_000}
    clients = {r["client"]: r["revenue"] for r in d["profitability_by_client"]}
    assert clients["Client A"] == 40_000 and clients["Client B"] == 10_000
    issues = {h["key"]: h["count"] for h in d["health_issues"]}
    assert issues["missing_reference"] == 1
    assert issues["unlinked_entity"] == 1
    assert issues["expense_without_attachment"] == 1          # of the two expense journals, one has a file
    assert issues["missing_line_description"] == 2            # both lines of the 3,000 journal
