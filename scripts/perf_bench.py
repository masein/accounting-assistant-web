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
        txns.append({"id": tid, "date": d, "reference": f"B-{i}", "description": f"bench {i}",
                     "currency": "IRR", "company_id": cid})
        k = rng.randint(2, 4)
        amount = rng.randint(1, 5000) * 10_000
        accs = rng.sample(leaves, k)
        # one debit side, the rest credit, balanced
        parts = [amount // (k - 1)] * (k - 1)
        parts[-1] += amount - sum(parts)
        lines.append({"id": uuid.uuid4(), "transaction_id": tid, "account_id": accs[0].id, "debit": amount,
                      "credit": 0, "company_id": cid})
        for a, p in zip(accs[1:], parts):
            lines.append({"id": uuid.uuid4(), "transaction_id": tid, "account_id": a.id, "debit": 0, "credit": p,
                          "company_id": cid})
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
]


def bench(company_id: str, runs: int) -> None:
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
        url = path.format(cash=cash, client=client)
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
    args = ap.parse_args()
    _guard()
    cid = seed(args.transactions, args.reseed)
    for i in range(1, args.companies):
        seed(args.transactions, args.reseed, slug=f"perf-bench-{i}")
    bench(cid, args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
