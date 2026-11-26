"""An invoice's party must belong to the caller's company (#14).

POST /invoices with another company's entity_id was accepted (201) and posted
a ledger entry carrying a cross-tenant reference; PATCH and the OCR import took
it too. Quotes, purchase orders and recurring templates already refuse it.

Self-contained engine (its own in-memory SQLite), like the isolation suite.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all mappers
from app.db.base import Base
from app.db.tenant import clear_current_company, tenant_bypass, use_company
from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.transaction import Transaction
from app.services.company_service import provision_company


@pytest.fixture()
def Session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _pragma(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(bind=eng)
    clear_current_company()
    yield sessionmaker(bind=eng, autoflush=False, autocommit=False)
    clear_current_company()
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def setup(Session):
    """Company A (the caller, with its own client) and company B (with one)."""
    from app.main import app
    from app.db.session import get_db
    from app.core.auth import create_session_token, CSRF_COOKIE, generate_csrf_token
    from app.core.config import settings

    s = Session()
    a, a_user = provision_company(s, name="AlphaCo", locale="uk", base_currency="GBP",
                                  username="alpha_inv", password="alphapass123")
    b, _ = provision_company(s, name="BetaCo", locale="uk", base_currency="GBP",
                             username="beta_inv", password="betapass123")
    ids = {"a_company": str(a.id), "a_user": str(a_user.id)}
    with use_company(a.id):
        ent = Entity(name="Alpha Client", type="client")
        s.add(ent)
        s.flush()
        ids["a_entity"] = str(ent.id)
    with use_company(b.id):
        ent = Entity(name="Beta Client", type="client")
        s.add(ent)
        s.flush()
        ids["b_entity"] = str(ent.id)
    s.commit()
    s.close()

    def _fresh_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    app.dependency_overrides[get_db] = _fresh_db
    try:
        with TestClient(app) as c:
            token = create_session_token(
                user_id=ids["a_user"], username="alpha_inv", is_admin=True,
                company_id=ids["a_company"], is_superadmin=False, token_version=0,
            )
            c.cookies.set(settings.auth_cookie_name, token)
            csrf = generate_csrf_token()
            c.cookies.set(CSRF_COOKIE, csrf)
            c.headers["X-CSRF-Token"] = csrf
            yield c, ids, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.router.lifespan_context = original


def _body(entity_id, number, status="issued"):
    return {"number": number, "kind": "sales", "status": status, "issue_date": "2026-11-20",
            "due_date": "2026-12-20", "amount": "500.00", "currency": "GBP", "entity_id": entity_id}


def _counts(Session):
    s = Session()
    try:
        with tenant_bypass():
            return (len(s.execute(select(Invoice.id)).all()), len(s.execute(select(Transaction.id)).all()))
    finally:
        s.close()


def test_create_refuses_another_companys_entity(setup):
    c, ids, Session = setup
    before = _counts(Session)
    r = c.post("/invoices", json=_body(ids["b_entity"], "XT-1"))
    assert r.status_code == 422, r.text
    assert _counts(Session) == before          # no invoice, no ledger entry


def test_create_refuses_an_unknown_entity(setup):
    c, _ids, _ = setup
    r = c.post("/invoices", json=_body(str(uuid.uuid4()), "XT-2"))
    assert r.status_code == 422, r.text


def test_create_with_own_entity_still_works(setup):
    c, ids, _ = setup
    r = c.post("/invoices", json=_body(ids["a_entity"], "XT-3"))
    assert r.status_code == 201, r.text
    assert r.json()["entity_id"] == ids["a_entity"]


def test_update_refuses_another_companys_entity(setup):
    c, ids, Session = setup
    r = c.post("/invoices", json=_body(ids["a_entity"], "XT-4", status="draft"))
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]
    r = c.patch(f"/invoices/{inv_id}", json={"entity_id": ids["b_entity"]})
    assert r.status_code == 422, r.text
    s = Session()
    try:
        with tenant_bypass():
            assert str(s.get(Invoice, uuid.UUID(inv_id)).entity_id) == ids["a_entity"]
    finally:
        s.close()


def test_ocr_import_refuses_another_companys_entity(setup):
    c, ids, _ = setup
    r = c.post("/invoices/ocr-import",
               data={"kind": "sales", "create": "true", "entity_id": ids["b_entity"]},
               files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "image/png")})
    assert r.status_code == 422, r.text
