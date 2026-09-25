"""Boot must not touch platform-wide settings (found 2026-09-25).

``ensure_default_company`` attaches orphan tenant rows (company_id NULL) to
the Default company on every boot. Since #97 the AI provider wiring is ONE
platform row with company_id NULL, so the backfill either moved it into
Default or — once Default held its own copy — hit uq_app_settings_company_key
and the pre-start failed on every restart. And because alembic's fileConfig
disabled the app's loggers, that failure exited 1 with no traceback.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.db.seed import DEFAULT_COMPANY_ID, orphan_backfill_statements
from app.models.app_setting import PLATFORM_SETTING_KEYS, AppSetting

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def scratch():
    """A private database with just app_settings, so the real backfill SQL
    runs without re-homing the shared test database's rows."""
    eng = create_engine("sqlite://")
    AppSetting.__table__.create(eng)
    yield eng
    eng.dispose()


def _rows(eng):
    with eng.connect() as c:
        return {(r.key, r.company_id) for r in c.execute(text("SELECT key, company_id FROM app_settings"))}


def _insert(eng, key, company_id):
    with eng.begin() as c:
        c.execute(text("INSERT INTO app_settings (id, key, value, company_id) VALUES (:id, :k, 'v', :c)"),
                  {"id": uuid.uuid4().hex, "k": key, "c": company_id})


def _default():
    return uuid.UUID(DEFAULT_COMPANY_ID).hex  # CHAR(32) on SQLite


def test_ai_config_is_a_platform_key():
    assert "ai_config" in PLATFORM_SETTING_KEYS


def test_backfill_leaves_platform_rows_and_moves_the_rest(scratch):
    _insert(scratch, "ai_config", None)                 # the platform row
    _insert(scratch, "ai_config", _default())           # Default's legacy copy
    _insert(scratch, "reporting_currency", None)        # a genuine orphan
    with scratch.begin() as c:
        for sql, params in orphan_backfill_statements(["app_settings"], company_id=_default()):
            c.execute(text(sql), params)               # must not raise
    rows = _rows(scratch)
    assert ("ai_config", None) in rows                  # still platform-wide
    assert ("ai_config", _default()) in rows
    assert ("reporting_currency", _default()) in rows   # orphan attached
    assert ("reporting_currency", None) not in rows


def test_without_the_exclusion_the_same_data_breaks_the_boot(scratch):
    """Pins the failure mode the exclusion prevents."""
    _insert(scratch, "ai_config", None)
    _insert(scratch, "ai_config", _default())
    with pytest.raises(IntegrityError):
        with scratch.begin() as c:
            c.execute(text("UPDATE app_settings SET company_id = :cid WHERE company_id IS NULL"),
                      {"cid": _default()})


def test_other_tables_get_the_plain_backfill():
    stmts = dict(orphan_backfill_statements(["invoices", "app_settings"]))
    assert "UPDATE invoices SET company_id = :cid WHERE company_id IS NULL" in stmts
    app_sql = next(sql for sql in stmts if sql.startswith("UPDATE app_settings"))
    assert "key NOT IN" in app_sql
    assert set(stmts[app_sql].values()) >= {DEFAULT_COMPANY_ID, *PLATFORM_SETTING_KEYS}


def test_ensure_default_company_uses_the_filtered_statements():
    src = (ROOT / "app" / "db" / "seed.py").read_text(encoding="utf-8")
    body = src[src.index("def ensure_default_company"):]
    assert "orphan_backfill_statements(" in body
    assert 'text(f"UPDATE {table}' not in body


def test_alembic_logging_keeps_the_app_loggers():
    """alembic/env.py must not disable existing loggers, or app.prestart's
    'PRE-START FAILED' traceback never reaches the container log."""
    env = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "disable_existing_loggers=False" in env
    assert "fileConfig(config.config_file_name)\n" not in env


def test_backfill_never_touches_append_only_audit_logs():
    """A failed login for an unknown username writes an audit row with no
    company. The backfill used to UPDATE it into Default, which the
    append-only trigger refuses: the next boot failed (found 2026-09-25)."""
    from app.db.seed import APPEND_ONLY_TABLES
    from app.db.tenant import tenant_model_tablenames
    tables = sorted(tenant_model_tablenames())
    assert "audit_logs" in tables and "audit_logs" in APPEND_ONLY_TABLES
    sqls = [sql for sql, _ in orphan_backfill_statements(tables)]
    assert not any("audit_logs" in sql for sql in sqls)
    assert any(sql.startswith("UPDATE invoices ") for sql in sqls)   # the rest still backfill


def test_failed_login_for_unknown_user_leaves_a_company_less_audit_row(client, db):
    """Pins the data shape that made the boot fail, so the exclusion above
    stays necessary-and-sufficient."""
    from app.db.tenant import tenant_bypass
    from app.models.audit_log import AuditLog
    name = f"ghost-{uuid.uuid4().hex[:6]}"
    assert client.post("/auth/login", json={"username": name, "password": "nope"}).status_code == 401
    with tenant_bypass():
        row = db.execute(select(AuditLog).where(AuditLog.action == "login_failed",
                                                AuditLog.detail.contains(name))).scalars().first()
    assert row is not None and row.company_id is None


def test_postgres_trigger_refuses_the_old_backfill():
    from tests.conftest import IS_SQLITE, _engine
    if IS_SQLITE:
        pytest.skip("the append-only trigger exists on PostgreSQL only")
    from sqlalchemy.exc import DBAPIError
    with _engine.begin() as conn:
        has_trigger = conn.execute(text(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_audit_logs_append_only'")).first()
    if not has_trigger:
        pytest.skip("trigger not installed in this database")
    with pytest.raises(DBAPIError):
        with _engine.begin() as conn:
            conn.execute(text("UPDATE audit_logs SET company_id = company_id WHERE company_id IS NULL "
                              "AND id IN (SELECT id FROM audit_logs LIMIT 1)"))
