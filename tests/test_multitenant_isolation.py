"""Cross-tenant isolation suite — the primary acceptance bar for multi-tenancy.

Company B can NEVER see or touch Company A's data:
  * list reads are scoped (B never sees A's rows)
  * fetch-by-exact-id returns nothing cross-tenant (→ 404 at the API)
  * writes are stamped with the CALLER's company even if a body says otherwise
  * a company-scoped delete/reset only ever hits the caller's company
  * account codes are unique PER company (both can have 1100)
  * provisioning a new company seeds its own empty, isolated chart

Self-contained engine (its own in-memory SQLite) so committing provisioning
data never pollutes the shared suite database.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.models  # noqa: F401 — register all mappers
from app.db.tenant import (
    set_current_company,
    clear_current_company,
    tenant_bypass,
    use_company,
    tenant_model_tablenames,
)
from app.models.account import Account
from app.models.entity import Entity
from app.models.company import Company
from app.models.user import User
from app.services.company_service import provision_company


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _pragma(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def Session(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _clear_ctx():
    clear_current_company()
    yield
    clear_current_company()


def _make_company(db, name, slug, locale="uk", currency="GBP") -> Company:
    with tenant_bypass():
        c = Company(name=name, slug=slug, locale=locale, base_currency=currency, status="active")
        db.add(c)
        db.flush()
    return c


# ---------------------------------------------------------------------------
# Write-stamping & read scoping
# ---------------------------------------------------------------------------
def test_writes_are_stamped_with_current_company(Session):
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    with use_company(a.id):
        ent = Entity(name="Acme", type="customer")
        db.add(ent)
        db.flush()
        assert ent.company_id == a.id
    db.close()


def test_body_supplied_company_id_cannot_win(Session):
    """A create that tries to set a foreign company_id is overridden to the
    caller's company by the before_flush stamp."""
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    b = _make_company(db, "Beta", "beta")
    with use_company(b.id):
        ent = Entity(name="Sneaky", type="customer", company_id=a.id)  # tries to plant in A
        db.add(ent)
        db.flush()
        assert ent.company_id == b.id  # injected value loses
    db.close()


def test_list_reads_are_company_scoped(Session):
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    b = _make_company(db, "Beta", "beta")
    with use_company(a.id):
        db.add(Entity(name="A-Customer", type="customer"))
        db.flush()
    with use_company(b.id):
        db.add(Entity(name="B-Customer", type="customer"))
        db.flush()

    with use_company(a.id):
        names = {e.name for e in db.execute(select(Entity)).scalars().all()}
        assert names == {"A-Customer"}
    with use_company(b.id):
        names = {e.name for e in db.execute(select(Entity)).scalars().all()}
        assert names == {"B-Customer"}
    db.close()


def test_fetch_by_exact_id_is_404_cross_tenant(Session):
    """B fetching A's row by its exact id gets nothing — the basis of the
    API returning 404 instead of leaking the row. Each 'request' is a fresh
    session, exactly as get_db yields per HTTP request in production."""
    setup = Session()
    a = _make_company(setup, "Alpha", "alpha")
    b = _make_company(setup, "Beta", "beta")
    a_id, b_id = a.id, b.id
    with use_company(a_id):
        ent = Entity(name="A-Only", type="customer")
        setup.add(ent)
        setup.flush()
        a_entity_id = ent.id
    setup.commit()
    setup.close()

    # A separate request as B cannot get A's entity by id.
    bdb = Session()
    with use_company(b_id):
        assert bdb.get(Entity, a_entity_id) is None
        assert bdb.execute(select(Entity).where(Entity.id == a_entity_id)).scalars().first() is None
    bdb.close()

    # A separate request as A still can.
    adb = Session()
    with use_company(a_id):
        assert adb.get(Entity, a_entity_id) is not None
    adb.close()


def test_update_cannot_touch_other_company(Session):
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    b = _make_company(db, "Beta", "beta")
    with use_company(a.id):
        ent = Entity(name="Original", type="customer")
        db.add(ent)
        db.flush()
        a_id = ent.id
    # B issues a bulk update that names A's row id — scoping blocks it.
    with use_company(b.id):
        from sqlalchemy import update
        db.execute(update(Entity).where(Entity.id == a_id).values(name="Hacked"))
        db.flush()
    with use_company(a.id):
        assert db.get(Entity, a_id).name == "Original"
    db.close()


def test_scoped_delete_only_hits_caller_company(Session):
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    b = _make_company(db, "Beta", "beta")
    with use_company(a.id):
        db.add(Entity(name="A1", type="customer"))
        db.flush()
    with use_company(b.id):
        db.add(Entity(name="B1", type="customer"))
        db.flush()
        from sqlalchemy import delete
        db.execute(delete(Entity))  # B wipes its own
        db.flush()
    # A's data survives
    with use_company(a.id):
        assert {e.name for e in db.execute(select(Entity)).scalars().all()} == {"A1"}
    with use_company(b.id):
        assert db.execute(select(Entity)).scalars().all() == []
    db.close()


# ---------------------------------------------------------------------------
# Per-company uniqueness
# ---------------------------------------------------------------------------
def test_two_companies_can_share_account_code(Session):
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    b = _make_company(db, "Beta", "beta")
    from app.models.account import AccountLevel
    with use_company(a.id):
        db.add(Account(code="1100", name="Cash A", level=AccountLevel.DETAIL))
        db.flush()
    with use_company(b.id):
        db.add(Account(code="1100", name="Cash B", level=AccountLevel.DETAIL))
        db.flush()  # must NOT raise a unique violation
    with use_company(a.id):
        assert db.execute(select(Account).where(Account.code == "1100")).scalars().one().name == "Cash A"
    with use_company(b.id):
        assert db.execute(select(Account).where(Account.code == "1100")).scalars().one().name == "Cash B"
    db.close()


# ---------------------------------------------------------------------------
# Provisioning + per-company seeding
# ---------------------------------------------------------------------------
def test_provision_company_seeds_isolated_chart(Session):
    db = Session()
    company, user = provision_company(
        db, name="Beta Ltd", locale="uk", base_currency="GBP",
        username="beta_admin", password="betapass123",
    )
    assert user.company_id == company.id
    assert user.is_superadmin is False
    # The new company has its own non-empty chart...
    with use_company(company.id):
        codes = {a.code for a in db.execute(select(Account)).scalars().all()}
        assert len(codes) > 0
    # ...and a different, freshly provisioned company starts from its own chart.
    company2, _ = provision_company(
        db, name="Gamma Ltd", locale="ir", base_currency="IRR",
        username="gamma_admin", password="gammapass123",
    )
    with use_company(company2.id):
        # Gamma sees only its own accounts, none of Beta's rows.
        accs = db.execute(select(Account)).scalars().all()
        assert all(a.company_id == company2.id for a in accs)
    db.close()


def test_provision_rejects_duplicate_username(Session):
    db = Session()
    provision_company(db, name="One", locale="uk", base_currency="GBP",
                      username="dup", password="password123")
    with pytest.raises(ValueError):
        provision_company(db, name="Two", locale="uk", base_currency="GBP",
                          username="dup", password="password123")
    db.close()


# ---------------------------------------------------------------------------
# Safety net: the tenant registry covers the whole business schema
# ---------------------------------------------------------------------------
def test_all_business_tables_are_tenant_scoped():
    names = tenant_model_tablenames()
    # A representative set of business tables that MUST carry company_id.
    must_have = {
        "accounts", "entities", "transactions", "transaction_lines",
        "transaction_entities", "invoices", "invoice_items", "payments",
        "credit_notes", "projects", "time_entries", "billing_rate_overrides",
        "bank_statements", "bank_statement_rows", "purchase_orders", "pay_runs",
        "mileage_claims", "tax_rates", "adjustments", "ai_chat_sessions",
        "ai_proposals", "audit_logs", "app_settings",
    }
    missing = must_have - names
    assert not missing, f"tenant scoping missing on: {sorted(missing)}"


def test_http_get_by_id_is_404_cross_tenant(engine, Session):
    """End-to-end through the real app + auth middleware: B logging in and
    GETting A's entity by its exact id receives 404, and B's entity list never
    contains A's rows."""
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db
    from app.core.auth import create_session_token, CSRF_COOKIE, generate_csrf_token
    from app.core.config import settings

    setup = Session()
    a, a_user = provision_company(setup, name="AlphaCo", locale="uk", base_currency="GBP",
                                  username="alpha_login", password="alphapass123")
    b, b_user = provision_company(setup, name="BetaCo", locale="uk", base_currency="GBP",
                                  username="beta_login", password="betapass123")
    a_id = a.id
    b_user_id, b_company_id = str(b_user.id), str(b.id)
    with use_company(a_id):
        ent = Entity(name="Alpha Secret Customer", type="customer")
        setup.add(ent)
        setup.flush()
        a_entity_id = str(ent.id)
    setup.commit()
    setup.close()

    def _fresh_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _fresh_db
    try:
        with TestClient(app) as c:
            token = create_session_token(
                user_id=b_user_id, username="beta_login", is_admin=True,
                company_id=b_company_id, is_superadmin=False, token_version=0,
            )
            c.cookies.set(settings.auth_cookie_name, token)
            c.cookies.set(CSRF_COOKIE, generate_csrf_token())
            # B fetching A's entity by exact id → 404
            r = c.get(f"/entities/{a_entity_id}")
            assert r.status_code == 404, r.text
            # B's entity list excludes A's row
            r2 = c.get("/entities")
            assert r2.status_code == 200, r2.text
            assert all(e["name"] != "Alpha Secret Customer" for e in r2.json())
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.router.lifespan_context = original


def _client_for(Session):
    """Build a TestClient whose get_db yields fresh sessions from the test engine."""
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import get_db

    def _fresh_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _fresh_db

    def _teardown():
        from app.db.session import get_db as gd
        app.dependency_overrides.pop(gd, None)
        app.router.lifespan_context = original

    return TestClient(app), _teardown


def test_suspended_company_login_is_refused(Session):
    from app.core.config import settings  # noqa: F401
    setup = Session()
    provision_company(setup, name="SuspCo", locale="uk", base_currency="GBP",
                      username="susp_login", password="susppass123")
    setup.commit()
    setup.close()

    client, teardown = _client_for(Session)
    try:
        # Active → login ok
        r = client.post("/auth/login", json={"username": "susp_login", "password": "susppass123"})
        assert r.status_code == 200, r.text
        # Suspend the company directly, then login must be refused.
        db = Session()
        with tenant_bypass():
            c = db.execute(select(Company).where(Company.slug == "suspco")).scalars().one()
            c.status = "suspended"
            db.commit()
        db.close()
        r2 = client.post("/auth/login", json={"username": "susp_login", "password": "susppass123"})
        assert r2.status_code == 403, r2.text
    finally:
        teardown()


def test_password_reset_invalidates_existing_token(Session):
    from app.core.auth import create_session_token, CSRF_COOKIE, generate_csrf_token
    from app.core.config import settings

    setup = Session()
    company, user = provision_company(setup, name="ResetCo", locale="uk", base_currency="GBP",
                                      username="reset_login", password="resetpass123")
    uid, cid = str(user.id), str(company.id)
    setup.commit()
    setup.close()

    client, teardown = _client_for(Session)
    try:
        token = create_session_token(user_id=uid, username="reset_login", is_admin=True,
                                     company_id=cid, is_superadmin=False, token_version=0)
        client.cookies.set(settings.auth_cookie_name, token)
        client.cookies.set(CSRF_COOKIE, generate_csrf_token())
        # Valid token → an authenticated, company-scoped endpoint works.
        assert client.get("/entities").status_code == 200
        # Bump the login's token_version (as reset-password does) → stale token.
        db = Session()
        with tenant_bypass():
            u = db.execute(select(User).where(User.username == "reset_login")).scalars().one()
            u.token_version = (u.token_version or 0) + 1
            db.commit()
        db.close()
        # Same (now stale) token is rejected by the auth middleware.
        r = client.get("/entities")
        assert r.status_code == 401, r.text
    finally:
        teardown()


def test_unscoped_context_sees_everything(Session):
    """With no company set (CLI / migrations) filtering is off — preserves
    single-tenant tooling."""
    db = Session()
    a = _make_company(db, "Alpha", "alpha")
    with use_company(a.id):
        db.add(Entity(name="X", type="customer"))
        db.flush()
    clear_current_company()
    # unscoped sees the row
    assert len(db.execute(select(Entity)).scalars().all()) == 1
    db.close()
