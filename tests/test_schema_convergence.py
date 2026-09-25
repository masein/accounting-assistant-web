"""One schema for every install (roadmap 2026-09 §1.13, alembic drift).

Models, migration 045 and the boot-time tenant guard must agree, so that a
database built fresh and one migrated for years end up identical — CI proves
it with ``alembic check`` on both (a fresh bootstrap and the 044 snapshot in
tests/fixtures). These tests pin the pieces that make that true.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.guards import TENANT_NOT_NULL_TABLES, install_tenant_guards, tenant_fk_name
from app.db.tenant import tenant_model_tablenames
from app.models.app_setting import AppSetting
from app.models.company import Company

ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="needs a PostgreSQL test database")


def _migration_045():
    path = ROOT / "alembic" / "versions" / "045_schema_convergence.py"
    spec = importlib.util.spec_from_file_location("m045", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _model_index_names() -> set[str]:
    return {ix.name for t in Base.metadata.tables.values() for ix in t.indexes}


# --- the models ---------------------------------------------------------------------

def _company(db):
    c = Company(id=uuid.uuid4(), name="Conv", slug=f"conv-{uuid.uuid4().hex[:8]}",
                locale="uk", base_currency="GBP", status="active", token_version=0)
    db.add(c)
    db.flush()
    return c


def test_platform_setting_keys_are_unique_even_with_a_null_company(db):
    key = f"conv-{uuid.uuid4().hex[:6]}"
    db.add(AppSetting(key=key, value="a", company_id=None))
    db.flush()
    db.add(AppSetting(key=key, value="b", company_id=None))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_same_key_is_fine_across_companies_but_not_twice_in_one(db):
    key = f"conv-{uuid.uuid4().hex[:6]}"
    a, b = _company(db), _company(db)
    db.add_all([AppSetting(key=key, value="1", company_id=a.id), AppSetting(key=key, value="2", company_id=b.id),
                AppSetting(key=key, value="p", company_id=None)])
    db.flush()
    db.add(AppSetting(key=key, value="3", company_id=a.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_salaries_are_bigint():
    from app.models.employee_pay import EmployeePayProfile
    for col in ("base_salary", "hourly_rate"):
        assert isinstance(EmployeePayProfile.__table__.c[col].type, sa.BigInteger), col


def test_a_rial_salary_above_32_bits_round_trips(db):
    from app.models.employee_pay import EmployeePayProfile
    from app.models.entity import Entity
    emp = Entity(id=uuid.uuid4(), name="Big", type="employee")
    db.add(emp)
    db.flush()
    prof = EmployeePayProfile(entity_id=emp.id, base_salary=3_500_000_000, hourly_rate=2_200_000_000)
    db.add(prof)
    db.flush()
    db.expire(prof)
    assert prof.base_salary == 3_500_000_000 and prof.hourly_rate == 2_200_000_000
    db.rollback()


def test_users_company_fk_cascades_under_its_name():
    from app.models.user import User
    fk = next(iter(User.__table__.c.company_id.foreign_keys))
    assert fk.name == "fk_users_company" and fk.ondelete == "CASCADE"


def test_one_index_per_purpose():
    names = _model_index_names()
    assert {"ix_ai_proposals_user_status", "ix_ai_chat_messages_session", "ix_pending_time_source_external",
            "ix_time_entries_source_external", "uq_app_settings_company_key", "uq_app_settings_global_key"} <= names
    # duplicates and low-cardinality flags are gone from the models
    assert not ({"ix_time_entries_billable", "ix_time_entries_payable", "ix_time_entries_entry_type",
                 "ix_ai_proposals_confirmation_token", "ix_ai_proposals_user_id"} & names)


# --- migration 045 matches the models ---------------------------------------------------

def test_migration_creates_only_indexes_the_models_declare():
    m = _migration_045()
    names = _model_index_names()
    for name, table, _cols in m.INDEXES:
        assert name in names, name
        assert table in Base.metadata.tables, table
    for _old, new in m.RENAME_INDEXES:
        assert new in names, new


def test_migration_drops_only_indexes_the_models_do_not_declare():
    m = _migration_045()
    assert not (set(m.DROP_INDEXES) & _model_index_names())


def test_migration_timestamps_are_not_null_in_the_models():
    m = _migration_045()
    for table, col in m.TIMESTAMPS_NOT_NULL:
        assert Base.metadata.tables[table].c[col].nullable is False, (table, col)


def test_migration_is_a_no_op_on_sqlite():
    m = _migration_045()
    assert m.down_revision == "044" and m.revision == "045"
    m.downgrade()  # nothing to undo, never raises


# --- alembic comparison skips only the tenant company_id constraints ----------------------

def _include_object():
    path = ROOT / "alembic" / "env.py"
    src = path.read_text(encoding="utf-8")
    start = src.index("def include_object(")
    end = src.index("\ndef run_migrations_offline")
    ns: dict = {}
    exec(src[start:end], ns)  # just the hook, not the alembic run
    return ns["include_object"]


def test_tenant_company_id_is_left_to_the_guards():
    inc = _include_object()
    accounts = Base.metadata.tables["accounts"]
    assert inc(accounts.c.company_id, "company_id", "column", False, None) is False
    assert inc(accounts.c.code, "code", "column", False, None) is True
    # a foreign key reflected from the database, as alembic hands it over
    reflected_fk = type("FK", (), {"table": accounts, "columns": [accounts.c.company_id],
                                   "referred_table": Base.metadata.tables["companies"]})()
    assert inc(reflected_fk, "fk_accounts_company", "foreign_key_constraint", True, None) is False
    other_fk = type("FK", (), {"table": accounts, "columns": [accounts.c.code],
                               "referred_table": Base.metadata.tables["companies"]})()
    assert inc(other_fk, "fk_other", "foreign_key_constraint", True, None) is True


def test_users_company_id_is_still_compared():
    inc = _include_object()
    users = Base.metadata.tables["users"]
    assert "users" not in tenant_model_tablenames()
    assert inc(users.c.company_id, "company_id", "column", False, None) is True


# --- the tenant guard -----------------------------------------------------------------------

def test_not_null_list_is_tenant_tables_minus_platform_ones():
    tenant = tenant_model_tablenames()
    assert set(TENANT_NOT_NULL_TABLES) <= tenant
    assert "app_settings" not in TENANT_NOT_NULL_TABLES and "audit_logs" not in TENANT_NOT_NULL_TABLES
    assert tenant_fk_name("quotes") == "fk_quotes_company"


def test_guard_is_a_no_op_on_sqlite():
    assert install_tenant_guards(sa.create_engine("sqlite://")) == []


def test_tax_rates_are_seeded_for_every_company(db):
    from app.db.tenant import tenant_bypass, use_company
    from app.main import _seed_tax_rates_per_company
    from app.models.tax_rate import TaxRate
    from app.services.tax_rate_service import _SEED_RATES
    a, b = _company(db), _company(db)
    db.commit()
    try:
        _seed_tax_rates_per_company(db)
        for c in (a, b):
            with use_company(c.id):
                assert db.execute(sa.select(sa.func.count(TaxRate.id))).scalar_one() == len(_SEED_RATES)
        # a second run adds nothing, and nothing is ever written company-less
        with tenant_bypass():
            before_null = db.execute(sa.select(sa.func.count(TaxRate.id)).where(TaxRate.company_id.is_(None))).scalar_one()
        assert _seed_tax_rates_per_company(db) == 0
        with tenant_bypass():
            assert db.execute(sa.select(sa.func.count(TaxRate.id)).where(TaxRate.company_id.is_(None))).scalar_one() == before_null
    finally:
        from tests.test_admin_audit import _purge_company
        for c in (a, b):
            _purge_company(db, str(c.id))


@needs_pg
def test_guard_on_a_fresh_postgres_database():
    """A throwaway database built like a fresh install (create_all), with one
    clean tenant table, one holding a company-less row and one pointing at a
    deleted company."""
    from sqlalchemy.engine import make_url

    name = f"aa_guard_{uuid.uuid4().hex[:8]}"
    server = sa.create_engine(PG_URL, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    with server.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    eng = sa.create_engine(make_url(PG_URL).set(database=name), poolclass=sa.pool.NullPool)
    try:
        Base.metadata.create_all(eng)
        from datetime import date

        from sqlalchemy.orm import Session

        from app.models.entity import Entity
        from app.models.notification import Reminder
        from app.models.tax_rate import TaxRate

        cid, gone = uuid.uuid4(), uuid.uuid4()
        with Session(eng) as s:  # no tenant context: company_id is taken as given
            s.add(Company(id=cid, name="G", slug="g", locale="uk", base_currency="GBP", status="active", token_version=0))
            s.flush()
            s.add(Entity(id=uuid.uuid4(), name="ok", type="client", company_id=cid))
            s.add(TaxRate(code="X", jurisdiction="UK", description="x", rate=1, effective_from=date(2020, 1, 1)))
            s.add(Reminder(user_id="u", title="t", due_date=date(2026, 1, 1), company_id=gone))
            s.commit()

        done = install_tenant_guards(eng)
        assert "entities → companies foreign key" in done
        assert "entities.company_id NOT NULL" in done
        assert "tax_rates.company_id NOT NULL" not in done       # a company-less row remains
        assert not any(d.startswith("app_settings.company_id") for d in done)  # platform rows allowed

        with eng.connect() as conn:
            def fk(table):
                return conn.execute(sa.text(
                    "SELECT convalidated, confdeltype FROM pg_constraint WHERE conname = :n"),
                    {"n": tenant_fk_name(table)}).one()
            def nullable(table):
                return conn.execute(sa.text(
                    "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t AND column_name = 'company_id'"),
                    {"t": table}).scalar_one()
            assert tuple(fk("entities")) == (True, "c")
            assert tuple(fk("reminders")) == (False, "c")          # the dangling row keeps it NOT VALID
            assert nullable("entities") == "NO" and nullable("tax_rates") == "YES"
            assert nullable("reminders") == "YES"                  # not on the NOT NULL list
            # the FK is enforced for new rows even while NOT VALID
        with pytest.raises(IntegrityError), Session(eng) as s:
            s.add(Reminder(user_id="u", title="t2", due_date=date(2026, 1, 2), company_id=uuid.uuid4()))
            s.commit()
        assert install_tenant_guards(eng) == []  # idempotent
    finally:
        eng.dispose()
        with server.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()
