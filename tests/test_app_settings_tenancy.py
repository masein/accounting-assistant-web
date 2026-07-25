"""app_settings must be per-company (the server "Reset failed." bug).

The model used to declare ``key`` as the sole primary key, so on any database
bootstrapped from the models the SECOND company writing a setting (e.g.
``reporting_currency`` during reset-db) hit a UniqueViolation. Settings are
now unique per (company_id, key) with a surrogate id.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db.tenant import tenant_bypass, use_company
from app.models.app_setting import AppSetting
from app.services.fx_service import get_reporting_currency, set_reporting_currency
from app.services.locale_service import set_reporting_locale


@pytest.fixture(autouse=True)
def _cleanup_company_scoped_rows(db):
    """The shared test DB runs most tests UNSCOPED (no company), where tenant
    filtering is off — company-scoped rows left behind here (settings, and the
    chart/tax/payment-method seeds a scoped reset-db creates) would leak into
    other tests as duplicate account codes etc. Remove them afterwards."""
    yield
    from app.models.account import Account
    from app.models.tax_rate import TaxRate

    with tenant_bypass():
        for model in (AppSetting, Account, TaxRate):
            if hasattr(model, "company_id"):
                db.execute(delete(model).where(model.company_id.is_not(None)))
        db.commit()


def test_two_companies_can_hold_the_same_setting_key(db):
    company_a, company_b = uuid.uuid4(), uuid.uuid4()

    with use_company(company_a):
        set_reporting_currency(db, "IRR")
        db.commit()
    # The exact server failure: another tenant writes the SAME key while a row
    # for it already exists under a different company.
    with use_company(company_b):
        set_reporting_currency(db, "GBP")
        db.commit()

    with use_company(company_a):
        assert get_reporting_currency(db) == "IRR"
    with use_company(company_b):
        assert get_reporting_currency(db) == "GBP"

    with tenant_bypass():
        rows = db.execute(
            select(AppSetting).where(AppSetting.key == "reporting_currency")
        ).scalars().all()
    by_company = {r.company_id: r.value for r in rows}
    assert by_company[company_a] == "IRR"
    assert by_company[company_b] == "GBP"


def test_setting_update_stays_within_company(db):
    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    with use_company(company_a):
        set_reporting_locale(db, "ir")
        db.commit()
    with use_company(company_b):
        set_reporting_locale(db, "uk")
        db.commit()
    # updating A must not touch B
    with use_company(company_a):
        set_reporting_locale(db, "default")
        db.commit()
    with tenant_bypass():
        rows = db.execute(
            select(AppSetting).where(AppSetting.key == "reporting_locale")
        ).scalars().all()
    by_company = {r.company_id: r.value for r in rows}
    assert by_company[company_a] == "default"
    assert by_company[company_b] == "uk"


def test_reset_db_succeeds_when_another_company_owns_settings(client, db):
    """The exact server failure: reset-db runs under company B while company A
    already owns the reporting_currency row. With the old global-key PK the
    INSERT for B raised UniqueViolation → the bare "Reset failed."."""
    from tests.conftest import _CSRFTestClient

    from app.core.auth import CSRF_COOKIE, create_session_token, generate_csrf_token
    from app.core.config import settings

    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    with use_company(company_a):
        set_reporting_currency(db, "USD")
        set_reporting_locale(db, "uk")
        db.commit()

    token = create_session_token(
        user_id=str(uuid.uuid4()), username="owner-b", is_admin=True,
        company_id=str(company_b),
    )
    csrf = generate_csrf_token()
    client.cookies.set(settings.auth_cookie_name, token)
    client.cookies.set(CSRF_COOKIE, csrf)
    scoped = _CSRFTestClient(client, csrf)

    resp = scoped.post("/admin/reset-db?locale=ir")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # company B got its own rows; company A's settings survive untouched
    with use_company(company_b):
        assert get_reporting_currency(db) == "IRR"
    with use_company(company_a):
        assert get_reporting_currency(db) == "USD"
