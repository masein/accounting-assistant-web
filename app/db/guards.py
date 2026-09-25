"""Database-level guards that must exist on EVERY install.

Migration 036 made ``audit_logs`` append-only with a trigger and a
``ON DELETE RESTRICT`` foreign key. A fresh database never runs migrations
(``create_all`` builds the head schema and Alembic is stamped), so new
installs had neither (found 2026-09-25). ``install_db_guards`` adds whatever
is missing on each boot; it is a no-op once they exist and on SQLite, where
the ORM guard in app/models/audit_log.py does the job.
"""
from __future__ import annotations

import sqlalchemy as sa

_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs is append-only (% refused)', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$ LANGUAGE plpgsql;
"""


def install_db_guards(engine) -> list[str]:
    """Returns what it installed (empty when everything was in place). Each
    guard is its own transaction, so one failing never undoes the other."""
    if engine.dialect.name != "postgresql":
        return []
    installed: list[str] = []
    with engine.begin() as conn:
        has_trigger = conn.execute(sa.text(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_audit_logs_append_only' AND NOT tgisinternal"
        )).first()
        if not has_trigger:
            conn.execute(sa.text(_FUNCTION_SQL))
            conn.execute(sa.text(
                "CREATE TRIGGER trg_audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
                "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
            ))
            installed.append("audit_logs append-only trigger")
    with engine.begin() as conn:
        has_fk = conn.execute(sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'fk_audit_logs_company'"
        )).first()
        if not has_fk:
            # NOT VALID: enforced for every new row without re-checking old
            # ones, so a boot can never fail on historical data.
            conn.execute(sa.text(
                "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_logs_company "
                "FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT NOT VALID"
            ))
            installed.append("audit_logs → companies RESTRICT foreign key")
    return installed
