"""Recurring rules, fixed assets and cheques must reference only the caller's
company's entities (#29, same class as #14).

POST/PATCH /recurring, POST/PATCH /fixed-assets and POST /commitments/cheques
stored another company's entity_id (201) — a dangling cross-tenant reference —
and an unknown id failed the foreign key at commit (500), so foreign ids were
distinguishable from random ones. Both must now be a plain 422.
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
from app.models.commitment import Commitment
from app.models.entity import Entity
from app.models.fixed_asset import FixedAsset
from app.models.recurring import RecurringRule
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
                                  username="alpha_xref", password="alphapass123")
    b, _ = provision_company(s, name="BetaCo", locale="uk", base_currency="GBP",
                             username="beta_xref", password="betapass123")
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
                user_id=ids["a_user"], username="alpha_xref", is_admin=True,
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


def _count(Session, model):
    s = Session()
    try:
        with tenant_bypass():
            return len(s.execute(select(model.id)).all())
    finally:
        s.close()


def _stored_entity(Session, model, row_id):
    s = Session()
    try:
        with tenant_bypass():
            return str(s.get(model, uuid.UUID(row_id)).entity_id)
    finally:
        s.close()


def _rule(entity_id):
    return {"name": "Rent", "entity_id": entity_id, "amount": "100.00", "start_date": "2026-12-01",
            "next_run_date": "2026-12-01", "frequency": "monthly",
            "bank_account_code": "1200", "counter_account_code": "0010"}


def _asset(entity_id):
    return {"name": "Laptop", "cost": "1200.00", "acquired_on": "2026-11-01", "life_months": 36,
            "entity_id": entity_id}


def _cheque(entity_id):
    return {"title": "Cheque to supplier", "amount": "250.00", "due_date": "2026-12-15",
            "direction": "pay", "entity_id": entity_id}


CASES = [
    ("/recurring", _rule, RecurringRule),
    ("/fixed-assets", _asset, FixedAsset),
    ("/commitments/cheques", _cheque, Commitment),
]
IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("path,body,model", CASES, ids=IDS)
def test_create_refuses_another_companys_entity(setup, path, body, model):
    c, ids, Session = setup
    before = _count(Session, model)
    r = c.post(path, json=body(ids["b_entity"]))
    assert r.status_code == 422, r.text
    assert _count(Session, model) == before


@pytest.mark.parametrize("path,body,model", CASES, ids=IDS)
def test_create_refuses_an_unknown_entity_without_500(setup, path, body, model):
    c, _ids, Session = setup
    before = _count(Session, model)
    r = c.post(path, json=body(str(uuid.uuid4())))
    assert r.status_code == 422, r.text
    assert _count(Session, model) == before


@pytest.mark.parametrize("path,body,model", CASES, ids=IDS)
def test_create_with_own_entity_still_works(setup, path, body, model):
    c, ids, Session = setup
    r = c.post(path, json=body(ids["a_entity"]))
    assert r.status_code == 201, r.text
    assert _stored_entity(Session, model, r.json()["id"]) == ids["a_entity"]


@pytest.mark.parametrize("path,body,model", CASES[:2], ids=IDS[:2])
def test_update_refuses_foreign_or_unknown_entity(setup, path, body, model):
    c, ids, Session = setup
    r = c.post(path, json=body(ids["a_entity"]))
    assert r.status_code == 201, r.text
    row_id = r.json()["id"]
    for bad in (ids["b_entity"], str(uuid.uuid4())):
        r = c.patch(f"{path}/{row_id}", json={"entity_id": bad})
        assert r.status_code == 422, r.text
        assert _stored_entity(Session, model, row_id) == ids["a_entity"]
