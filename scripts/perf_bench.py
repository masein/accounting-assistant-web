"""Report performance bench (roadmap 2026-09 §2.6) — for a SCRATCH database only.

Seeds one company with N journals over the last 12 months (2–4 lines each,
client/supplier links, some attachments), then times the heavy read endpoints
in-process and counts their SQL statements:

    DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/aa_scratch_bench \\
    APP_ENV=test python -m scripts.perf_bench --transactions 20000

Refuses a database whose name does not contain "scratch" or "bench", and
APP_ENV=prod. Re-running reuses the seeded company (``--reseed`` to rebuild).
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
import uuid
from datetime import date, timedelta


def _guard() -> None:
    from sqlalchemy.engine import make_url

    from app.core.config import settings
    name = make_url(settings.database_url).database or ""
    if settings.app_env == "prod" or not any(w in name for w in ("scratch", "bench")):
        sys.exit(f"refusing to run against database {name!r} (APP_ENV={settings.app_env})")


def seed(n: int, reseed: bool, slug: str = "perf-bench") -> str:
    import sqlalchemy as sa

    from app.db.seed import seed_chart_if_empty
    from app.db.session import SessionLocal, engine
    from app.db.tenant import tenant_bypass, use_company
    from app.models.account import Account
    from app.models.company import Company
    from app.models.entity import Entity, TransactionEntity
    from app.models.transaction import Transaction, TransactionAttachment, TransactionLine

    db = SessionLocal()
    with tenant_bypass():
        existing = db.execute(sa.select(Company).where(Company.slug == slug)).scalars().first()
    if existing is not None and not reseed:
        print(f"reusing company {existing.id}")
        return str(existing.id)
    if existing is not None:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM companies WHERE id = :i"), {"i": existing.id})
    cid = uuid.uuid4()
    db.add(Company(id=cid, name=f"Perf bench {slug}", slug=slug, locale="ir", base_currency="IRR",
                   status="active", token_version=0))
    db.commit()
    with use_company(cid):
        seed_chart_if_empty(db, locale="ir")
        db.commit()
        leaves = [a for a in db.execute(sa.select(Account)).scalars() if len(a.code) >= 4]
    rng = random.Random(42)
    entities = [{"id": uuid.uuid4(), "name": f"{kind} {i}", "type": kind, "company_id": cid}
                for kind, count in (("client", 60), ("supplier", 40)) for i in range(count)]
    today = date.today()
    txns, lines, links, atts = [], [], [], []
    for i in range(n):
        tid = uuid.uuid4()
        d = today - timedelta(days=rng.randint(0, 365))
        # base amounts as the app posts them (home currency: rate 1); without
        # them every boot's catch-up converted all 20,000 one by one
        txns.append({"id": tid, "date": d, "reference": f"B-{i}", "description": f"bench {i}",
                     "currency": "IRR", "fx_rate": 1.0, "company_id": cid})
        k = rng.randint(2, 4)
        amount = rng.randint(1, 5000) * 10_000
        accs = rng.sample(leaves, k)
        # one debit side, the rest credit, balanced
        parts = [amount // (k - 1)] * (k - 1)
        parts[-1] += amount - sum(parts)
        lines.append({"id": uuid.uuid4(), "transaction_id": tid, "account_id": accs[0].id, "debit": amount,
                      "credit": 0, "base_debit": amount, "base_credit": 0, "company_id": cid})
        for a, p in zip(accs[1:], parts):
            lines.append({"id": uuid.uuid4(), "transaction_id": tid, "account_id": a.id, "debit": 0, "credit": p,
                          "base_debit": 0, "base_credit": p, "company_id": cid})
        e = rng.choice(entities)
        links.append({"id": uuid.uuid4(), "transaction_id": tid, "entity_id": e["id"],
                      "role": "client" if e["type"] == "client" else "supplier", "company_id": cid})
        if rng.random() < 0.1:
            atts.append({"id": uuid.uuid4(), "transaction_id": tid, "file_name": "r.pdf",
                         "file_path": f"/bench/{uuid.uuid4().hex}.pdf", "content_type": "application/pdf", "size_bytes": 1,
                         "company_id": cid})
    with engine.begin() as conn:
        conn.execute(sa.insert(Entity), entities)
        for chunk in range(0, len(txns), 5000):
            conn.execute(sa.insert(Transaction), txns[chunk:chunk + 5000])
        for chunk in range(0, len(lines), 10000):
            conn.execute(sa.insert(TransactionLine), lines[chunk:chunk + 10000])
        conn.execute(sa.insert(TransactionEntity), links)
        if atts:
            conn.execute(sa.insert(TransactionAttachment), atts)
        conn.execute(sa.text("ANALYZE"))
    print(f"seeded company {cid}: {len(txns)} journals, {len(lines)} lines")
    db.close()
    return str(cid)


def seed_invoices(company_id: str, sales: int, bills: int) -> None:
    """A year of invoices made the app's own way (VAT lines, recognition,
    payments, part payments, credit notes), so every per-invoice path runs."""
    import sqlalchemy as sa

    from app.api.invoices import add_credit_note, add_payment, create_invoice
    from app.db.session import SessionLocal
    from app.db.tenant import use_company
    from app.models.entity import Entity
    from app.models.invoice import Invoice
    from app.schemas.invoice import CreditNoteCreate, InvoiceCreate, InvoiceItemCreate, PaymentCreate

    db = SessionLocal()
    with use_company(company_id):
        have = db.execute(sa.select(sa.func.count(Invoice.id))).scalar() or 0
        if have >= sales + bills:
            print(f"reusing {have} invoices")
            db.close()
            return
        clients = list(db.execute(sa.select(Entity.id).where(Entity.type == "client")).scalars())
        suppliers = list(db.execute(sa.select(Entity.id).where(Entity.type == "supplier")).scalars())
        rng = random.Random(7)
        today = date.today()
        t0 = time.perf_counter()
        for i in range(have, sales + bills):
            kind = "sales" if i < sales else "purchase"
            issue = today - timedelta(days=rng.randint(5, 360))
            items = [InvoiceItemCreate(product_name=f"Item {rng.randint(1, 40)}", quantity=rng.randint(1, 5),
                                       unit_price=rng.randint(1, 200) * 100_000, tax_rate=10.0)
                     for _ in range(rng.randint(1, 3))]
            inv = create_invoice(InvoiceCreate(
                number=f"{'S' if kind == 'sales' else 'B'}-{i}", kind=kind, issue_date=issue,
                due_date=issue + timedelta(days=30), amount=0, currency="IRR", status="issued",
                entity_id=rng.choice(clients if kind == "sales" else suppliers), items=items), db)
            paid_on = min(issue + timedelta(days=rng.randint(1, 40)), today)
            r = rng.random()
            if r < 0.70:
                add_payment(inv.id, PaymentCreate(amount=inv.amount, date=paid_on), db)
            elif r < 0.85:
                add_payment(inv.id, PaymentCreate(amount=max(1, inv.amount // 3), date=paid_on), db)
            if rng.random() < 0.05:
                add_credit_note(inv.id, CreditNoteCreate(amount=max(1, inv.amount // 10), date=paid_on), db)
            if (i + 1) % 500 == 0:
                print(f"  {i + 1} invoices, {time.perf_counter() - t0:.0f} s")
    db.close()
    print(f"seeded {sales} sales invoices and {bills} bills")


ENDPOINTS = [
    "/reports/ledger-summary",
    "/reports/owner-dashboard",
    "/reports/accounts/{cash}/detail",
    "/reports/entities/{client}/transactions",
    "/manager-reports/books/trial-balance",
    "/manager-reports/books/general-ledger",
    "/manager-reports/financial/iran/balance-sheet",
    "/manager-reports/financial/iran/income-statement",
    "/manager-reports/operational/debtor-creditor",
    "/manager-reports/books/general-journal?page=1&page_size=50",
    "/transactions?limit=50",
    # the pages since §2.6 (scenario K1)
    "/invoices",
    "/invoices?kind=purchase",
    "/quotes",
    "/moadian/invoices?state=all",
    "/insights",
    "/reports/cash-forecast",
    "/reports/missing-references",
    "/reports/tax-summary",
    "/tax/ir/seasons",
    "/tax/ir/quarterly?year={jy}&season={season}",
    "/tax/ir/vat-return?year={jy}&season={season}",
    "/budgets/actual-vs-budget?month={month}",
    "/entities",
    "/notifications/feed",
    "/brain/audit/logs",
    "/brain/cfo/report",
    "/brain/ceo/report",
    "/manager-reports/operational/accounts-receivable",
    "/manager-reports/operational/accounts-payable",
    "/manager-reports/sales/by-invoice",
    "/manager-reports/sales/by-product",
    "/manager-reports/sales/trend",
    "/manager-reports/purchases/by-invoice",
    "/manager-reports/close-pack",
    "/commitments",
]


def bench(company_id: str, runs: int, only: list[str] | None = None) -> None:
    import sqlalchemy as sa
    from fastapi.testclient import TestClient

    from app.core.auth import create_session_token
    from app.core.config import settings
    from app.db.session import SessionLocal, engine
    from app.db.tenant import use_company
    from app.main import app
    from app.models.account import Account
    from app.models.entity import Entity

    db = SessionLocal()
    with use_company(company_id):
        cash = db.execute(sa.select(Account.code).where(Account.code.like("1111%")).order_by(Account.code)).scalars().first() \
            or db.execute(sa.select(Account.code).order_by(Account.code)).scalars().first()
        client = db.execute(sa.select(Entity.id).where(Entity.type == "client")).scalars().first()
        from app.services.calendar_periods import company_calendar, month_key
        month = month_key(date.today(), company_calendar(db))
        from app.utils.jalali import gregorian_to_jalali
        jy, jm, _jd = gregorian_to_jalali(date.today())
        season = (jm - 1) // 3 + 1
    db.close()

    counter = {"n": 0}
    sa.event.listen(engine, "before_cursor_execute", lambda *a, **k: counter.__setitem__("n", counter["n"] + 1))
    tok = create_session_token(user_id=str(uuid.uuid4()), username="bench", is_admin=True,
                               company_id=company_id, role="owner")
    c = TestClient(app)  # NOT a context manager: no lifespan against the real app DB
    c.cookies.set(settings.auth_cookie_name, tok)
    from app.api.reports import invalidate_dashboard_cache
    print(f"{'endpoint':62} {'median ms':>10} {'max ms':>8} {'queries':>8} {'status':>6}")
    for path in ENDPOINTS:
        if only and not any(o in path for o in only):
            continue
        url = path.format(cash=cash, client=client, month=month, jy=jy, season=season)
        times, queries, status = [], 0, 0
        for _ in range(runs):
            invalidate_dashboard_cache()
            counter["n"] = 0
            t0 = time.perf_counter()
            r = c.get(url)
            times.append((time.perf_counter() - t0) * 1000)
            queries, status = counter["n"], r.status_code
        print(f"{url[:62]:62} {statistics.median(times):10.0f} {max(times):8.0f} {queries:8d} {status:6d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transactions", type=int, default=20_000)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--reseed", action="store_true")
    ap.add_argument("--companies", type=int, default=1, help="extra same-size tenants, to bench a shared database")
    ap.add_argument("--sales", type=int, default=2_500, help="sales invoices over the year (0: none)")
    ap.add_argument("--bills", type=int, default=800, help="purchase bills over the year")
    ap.add_argument("--only", action="append", default=[],
                    help="bench only the endpoints containing this text (repeatable)")
    args = ap.parse_args()
    _guard()
    cid = seed(args.transactions, args.reseed)
    if args.sales or args.bills:
        seed_invoices(cid, args.sales, args.bills)
    for i in range(1, args.companies):
        seed(args.transactions, args.reseed, slug=f"perf-bench-{i}")
    bench(cid, args.runs, only=args.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
