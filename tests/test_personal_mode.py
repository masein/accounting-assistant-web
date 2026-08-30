"""Personal-finance mode: provisioning a kind='personal' company gives a
personal-role user, the personal chart of accounts (localized names, IR
code scheme so budget/report expense predicates keep working), and none of
the SME seed rows."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.permissions import Role
from app.db.base import Base
from app.db.seed import PERSONAL_SEED_ACCOUNTS
from app.db.tenant import clear_current_company, use_company
from app.models.account import Account, AccountLevel
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


def _provision_personal(db, locale="ir", username="me"):
    return provision_company(
        db, name="My Money", locale=locale, base_currency="IRR",
        username=username, password="personalpass123", kind="personal",
    )


def test_personal_provision_sets_role_and_kind(Session):
    db = Session()
    company, user = _provision_personal(db)
    assert company.kind == "personal"
    assert user.role == Role.PERSONAL
    assert user.is_admin is False
    assert user.is_superadmin is False
    db.close()


def test_personal_chart_seeded_fa_with_hierarchy(Session):
    db = Session()
    company, _ = _provision_personal(db)
    with use_company(company.id):
        accounts = db.execute(select(Account)).scalars().all()
    by_code = {a.code: a for a in accounts}
    expected = {r[0] for r in PERSONAL_SEED_ACCOUNTS}
    assert set(by_code) == expected
    # Persian names for the ir locale
    assert by_code["6110"].name == "خوراک و سوپرمارکت"
    # hierarchy resolves by the 2-digit prefix
    groups = {a.code: a for a in accounts if a.level == AccountLevel.GROUP}
    assert by_code["6110"].parent_id == groups["61"].id
    assert by_code["1130"].parent_id == groups["11"].id
    # every expense general sits under 61/62 so the actual-vs-budget
    # expense predicate (code.startswith 61/62) keeps working
    expense_codes = [a.code for a in accounts
                     if a.level == AccountLevel.GENERAL and a.code.startswith(("61", "62"))]
    assert len(expense_codes) >= 10
    db.close()


def test_personal_chart_english_names_for_uk_locale(Session):
    db = Session()
    company, _ = _provision_personal(db, locale="uk", username="me_uk")
    with use_company(company.id):
        by_code = {a.code: a for a in db.execute(select(Account)).scalars().all()}
    assert by_code["6110"].name == "Food & groceries"
    assert by_code["1130"].name == "Gold & coin savings"
    db.close()


def test_business_provision_unchanged(Session):
    db = Session()
    company, user = provision_company(
        db, name="Biz Ltd", locale="ir", base_currency="IRR",
        username="bizowner", password="bizpass12345",
    )
    assert company.kind == "business"
    assert user.role == Role.OWNER
    assert user.is_admin is True
    with use_company(company.id):
        codes = {a.code for a in db.execute(select(Account)).scalars().all()}
    # SME chart, not the personal one
    assert "2160" in codes            # payroll tax payable — SME only
    assert "1130" in codes
    db.close()


def test_provision_rejects_unknown_kind(Session):
    db = Session()
    with pytest.raises(ValueError):
        provision_company(
            db, name="X", locale="ir", base_currency="IRR",
            username="xx", password="xxpass123456", kind="household",
        )
    db.close()
