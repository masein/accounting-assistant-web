"""Server-ops fixes: reset-db must wipe equity ghosts (events / shareholdings /
registered capital) and /health must report schema versions for stale-image
diagnosis."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import tenant_bypass, use_company
from app.models.company import Company
from app.models.entity import Entity
from app.models.equity import EquityEvent, Shareholding
from tests.conftest import _CSRFTestClient


@pytest.fixture()
def company(db):
    c = Company(id=uuid.uuid4(), name="ResetCo", slug=f"rst-{uuid.uuid4().hex[:8]}",
                locale="ir", base_currency="IRR", status="active", token_version=0)
    db.add(c)
    db.flush()
    with use_company(c.id):
        yield c
    # teardown: drop everything stamped with this company
    from app.models.account import Account
    from app.models.app_setting import AppSetting
    from app.models.entity import TransactionEntity
    from app.models.transaction import Transaction, TransactionLine
    db.rollback()
    with tenant_bypass():
        for Model in (TransactionLine, TransactionEntity, EquityEvent, Shareholding,
                      Transaction, Entity, Account, AppSetting):
            db.query(Model).filter(Model.company_id == c.id).delete(synchronize_session=False)
        db.commit()


def _api(client, company):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(company.id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


def _purge_settings(db, *keys):
    """SQLite test PK is key-only (prod is (company_id, key)) — delete rows other
    tests committed so reset-db's upsert doesn't collide."""
    from app.models.app_setting import AppSetting
    with tenant_bypass():
        for k in keys:
            db.query(AppSetting).filter(AppSetting.key == k).delete(synchronize_session=False)
        db.commit()


def test_reset_db_wipes_equity_ghosts(db, client, company):
    _purge_settings(db, "reporting_locale", "reporting_currency")
    from app.services import equity_service as eq
    sh = Entity(name=f"Cyrus {uuid.uuid4().hex[:6]}", type="shareholder")
    db.add(sh)
    db.flush()
    db.add(Shareholding(entity_id=sh.id, percent=100))
    db.flush()
    eq.contribution(db, entity_id=sh.id, amount=500_000_000, txn_date=date(2026, 3, 1))
    db.commit()
    db.refresh(company)
    assert company.registered_capital == 500_000_000
    assert db.execute(select(EquityEvent)).scalars().first() is not None

    api = _api(client, company)
    r = api.post("/admin/reset-db?locale=ir")
    assert r.status_code == 200, r.text

    # equity events + shareholdings gone; registered capital back to zero —
    # the changes-in-equity statement starts clean after a reset.
    assert db.execute(select(EquityEvent)).scalars().first() is None
    assert db.execute(select(Shareholding)).scalars().first() is None
    db.refresh(company)
    assert company.registered_capital == 0


def test_health_reports_schema_versions(client):
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert "image_schema" in d and "db_schema" in d
    # The build must know its own migration head (stale-image detection).
    assert isinstance(d["image_schema"], str) and d["image_schema"] not in ("", "unknown")
