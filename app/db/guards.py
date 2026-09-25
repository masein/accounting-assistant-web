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


# Tenant tables whose company_id migration 015 made NOT NULL (plus
# company_profiles, created NOT NULL later). The model column stays nullable
# on purpose — the test harness creates rows with no company — so a fresh
# install, built by create_all and stamped, never got these constraints.
# app_settings and audit_logs hold platform rows (company_id NULL) and the
# tables added after 015 have never been NOT NULL in production; they get the
# foreign key only, until their data has been checked.
TENANT_NOT_NULL_TABLES = (
    "accounts", "adjustments", "ai_chat_messages", "ai_chat_sessions", "ai_proposals",
    "bank_statement_rows", "bank_statements", "billing_rate_overrides", "budget_limits",
    "company_profiles", "credit_notes", "employee_pay_profiles", "entities", "goods_receipt_lines",
    "goods_receipts", "integrity_checks", "inventory_items", "inventory_movements", "invoice_items",
    "invoices", "mileage_claims", "pay_run_lines", "pay_runs", "payments", "projects",
    "purchase_order_lines", "purchase_orders", "recurring_rules", "tax_rates", "time_entries",
    "transaction_attachments", "transaction_entities", "transaction_fee_applications",
    "transaction_lines", "transaction_versions", "transactions", "trial_balance_lines",
    "trial_balances",
)


def tenant_fk_name(table: str) -> str:
    return f"fk_{table}_company"


def install_tenant_guards(engine) -> list[str]:
    """Give every tenant table the ``company_id → companies`` foreign key
    (ON DELETE CASCADE; audit_logs keeps its RESTRICT one) and the tables in
    TENANT_NOT_NULL_TABLES their NOT NULL — what a migrated database has had
    since 015. Runs after the Default-company fold, when no row is company-less.
    Idempotent; each table is its own transaction; nothing ever deletes data:
    a foreign key old rows would break stays NOT VALID, and NOT NULL is skipped
    (with a warning) while NULLs remain."""
    import logging

    from app.db.tenant import tenant_model_tablenames

    if engine.dialect.name != "postgresql":
        return []
    log = logging.getLogger("app.migrations")
    installed: list[str] = []
    for table in sorted(tenant_model_tablenames()):
        added_fk = False
        with engine.begin() as conn:
            if not sa.inspect(conn).has_table(table):
                continue
            has_fk = conn.execute(sa.text("""
                SELECT 1 FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                WHERE c.contype = 'f' AND c.conrelid = CAST(:t AS regclass)
                  AND c.confrelid = CAST('companies' AS regclass) AND a.attname = 'company_id'
            """), {"t": table}).first()
            if not has_fk:
                conn.execute(sa.text(
                    f'ALTER TABLE "{table}" ADD CONSTRAINT "{tenant_fk_name(table)}" '
                    'FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE NOT VALID'
                ))
                installed.append(f"{table} → companies foreign key")
                added_fk = True
        if added_fk:
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text(f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{tenant_fk_name(table)}"'))
            except Exception:  # noqa: BLE001 — old rows point at a missing company; keep NOT VALID
                log.warning("%s: company foreign key left NOT VALID (rows reference a missing company)", table)
        if table not in TENANT_NOT_NULL_TABLES:
            continue
        with engine.begin() as conn:
            col = next((c for c in sa.inspect(conn).get_columns(table) if c["name"] == "company_id"), None)
            if col is None or not col["nullable"]:
                continue
            nulls = conn.execute(sa.text(f'SELECT count(*) FROM "{table}" WHERE company_id IS NULL')).scalar_one()
            if nulls:
                log.warning("%s: %d row(s) without a company — company_id left nullable", table, nulls)
                continue
            conn.execute(sa.text(f'ALTER TABLE "{table}" ALTER COLUMN company_id SET NOT NULL'))
            installed.append(f"{table}.company_id NOT NULL")
    return installed
