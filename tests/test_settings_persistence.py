"""Feedback fixes: reporting currency/locale must survive a refresh in a
tenant context (the read prefers Company fields, so the write must update
them), and the shareholder entity type exists end-to-end."""
from __future__ import annotations

import uuid

import pytest

from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
from app.core.config import settings
from app.db.tenant import use_company
from app.models.company import Company
from app.services.fx_service import get_reporting_currency, set_reporting_currency
from app.services.locale_service import get_reporting_locale, set_reporting_locale
from tests.conftest import _CSRFTestClient


def _purge_setting(db, key):
    """SQLite test schema has a key-only PK on app_settings (prod is composite
    (company_id, key)), so rows committed by earlier tests collide with a
    tenant-stamped upsert. Clear the key first."""
    from sqlalchemy import delete
    from app.db.tenant import tenant_bypass
    from app.models.app_setting import AppSetting
    with tenant_bypass():
        db.execute(delete(AppSetting).where(AppSetting.key == key))
    db.flush()


def _company(db, base_currency="GBP", locale="uk"):
    c = Company(id=uuid.uuid4(), name="Co", slug=f"co-{uuid.uuid4().hex[:8]}",
                locale=locale, base_currency=base_currency, status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def _owner_api(client, company):
    tok = create_session_token(user_id=str(uuid.uuid4()), username="owner", is_admin=True,
                               company_id=str(company.id), role="owner")
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, tok)
    client.cookies.set(CSRF_COOKIE, csrf)
    return _CSRFTestClient(client, csrf)


# --- currency ---------------------------------------------------------------

def test_set_currency_updates_tenant_company_so_read_agrees(db):
    _purge_setting(db, "reporting_currency")
    co = _company(db, base_currency="GBP")
    with use_company(str(co.id)):
        assert get_reporting_currency(db) == "GBP"
        set_reporting_currency(db, "USD")
        # THE bug: without updating Company.base_currency the read stays GBP.
        assert get_reporting_currency(db) == "USD"
    db.refresh(co)
    assert co.base_currency == "USD"


def test_currency_round_trip_through_api(db, client):
    _purge_setting(db, "reporting_currency")
    co = _company(db, base_currency="GBP")
    api = _owner_api(client, co)
    r = api.put("/fx/reporting-currency", json={"currency": "EUR"})
    assert r.status_code == 200, r.text
    # simulate the refresh: a fresh GET must show the saved value
    r2 = api.get("/fx/reporting-currency")
    assert r2.json()["currency"] == "EUR"


def test_set_locale_updates_tenant_company(db):
    _purge_setting(db, "reporting_locale")
    co = _company(db, locale="uk")
    with use_company(str(co.id)):
        set_reporting_locale(db, "ir")
        assert get_reporting_locale(db) == "ir"
    db.refresh(co)
    assert co.locale == "ir"


def test_setters_without_tenant_context_still_work(db):
    # CLI/tests with no company context: AppSetting-only behaviour unchanged.
    _purge_setting(db, "reporting_currency")
    set_reporting_currency(db, "IRR")
    assert get_reporting_currency(db) == "IRR"


# --- shareholder entity type -------------------------------------------------

def test_shareholder_entity_create_and_update(db, client):
    co = _company(db)
    api = _owner_api(client, co)
    r = api.post("/entities", json={"type": "shareholder", "name": "Mehdi"})
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "shareholder"
    eid = r.json()["id"]
    # retype an accidental employee → shareholder (the friend's exact case)
    r2 = api.post("/entities", json={"type": "employee", "name": "Farhad"})
    fid = r2.json()["id"]
    r3 = api.patch(f"/entities/{fid}", json={"type": "shareholder"})
    assert r3.status_code == 200 and r3.json()["type"] == "shareholder"
    assert api.get(f"/entities/{eid}").json()["type"] == "shareholder"


def test_invalid_entity_type_rejected_on_create(db, client):
    co = _company(db)
    api = _owner_api(client, co)
    assert api.post("/entities", json={"type": "alien", "name": "X"}).status_code == 400


def test_ai_classifier_maps_shareholder_language(db):
    from app.services.ai_accountant.entity_create import classify_entity_type, normalize_entity_type
    assert normalize_entity_type("shareholder") == "shareholder"
    assert normalize_entity_type("partner") == "shareholder"
    assert classify_entity_type("employee", text="Mehdi is a shareholder of the company") == "shareholder"
    assert classify_entity_type("employee", text="ثبت مهدی به عنوان سهامدار") == "shareholder"
    # plain staff language still classifies as employee
    assert classify_entity_type("employee", text="new hire on payroll") == "employee"
