"""Converge every install onto one schema (roadmap 2026-09 §1.13, alembic drift).

Two kinds of database exist: MIGRATED ones (production) built up through the
chain, and FRESH ones built by create_all from the models of the day and then
stamped. They had drifted apart, and both had drifted from the models
(``alembic check`` listed 139 differences). The models now describe the right
schema; this migration brings either kind there. Every step checks first, so
it is safe on both, and it never deletes a row.

* ``employee_pay_profiles.base_salary`` / ``hourly_rate`` → BIGINT (fresh
  installs had INTEGER; a Rial salary overflows it).
* ``users.preferred_language`` NOT NULL DEFAULT 'en'; ``created_at`` /
  ``updated_at`` NOT NULL where the models always meant it.
* ``app_settings``: partial unique indexes per company and for the platform
  (fresh installs had a plain UNIQUE that lets two platform rows share a key).
* ``users.company_id`` → companies ON DELETE CASCADE as ``fk_users_company``
  (production had SET NULL: a company-less login is a platform-wide one).
* Unique constraints / indexes: one per purpose, with the names the models
  use; duplicates dropped, missing ones created.

Tenant ``company_id`` NOT NULL + foreign keys are handled at boot by
``app/db/guards.install_tenant_guards`` (they must wait for the Default-company
fold), and kept out of ``alembic check`` by ``alembic/env.py``.

Revision ID: 045
Revises: 044
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIMESTAMPS_NOT_NULL = (
    ("adjustments", "created_at"), ("api_keys", "created_at"), ("billing_rate_overrides", "created_at"),
    ("companies", "created_at"), ("company_profiles", "created_at"), ("company_profiles", "updated_at"),
    ("credit_notes", "created_at"), ("employee_pay_profiles", "created_at"),
    ("employee_pay_profiles", "updated_at"), ("goods_receipts", "created_at"),
    ("mileage_claims", "created_at"), ("mileage_claims", "updated_at"), ("pay_run_lines", "created_at"),
    ("pay_runs", "created_at"), ("pay_runs", "updated_at"), ("payments", "created_at"),
    ("pending_time_entries", "created_at"), ("projects", "created_at"),
    ("purchase_order_lines", "created_at"), ("purchase_orders", "created_at"),
    ("purchase_orders", "updated_at"), ("tax_rates", "created_at"), ("time_entries", "created_at"),
)

# (index name, table, columns) the models declare
INDEXES = (
    ("ix_ai_proposals_user_status", "ai_proposals", "user_id, status"),
    ("ix_ai_chat_messages_session", "ai_chat_messages", "session_id, created_at"),
    ("ix_ai_chat_sessions_user_id", "ai_chat_sessions", "user_id"),
    ("ix_pending_time_source_external", "pending_time_entries", "company_id, source, external_id"),
    ("ix_time_entries_source_external", "time_entries", "source, external_id"),
    ("ix_time_entries_payroll_status", "time_entries", "payroll_status"),
    ("ix_goods_receipts_receipt_date", "goods_receipts", "receipt_date"),
    ("ix_pay_runs_pay_date", "pay_runs", "pay_date"),
    ("ix_pay_runs_period_end", "pay_runs", "period_end"),
    ("ix_projects_code", "projects", "code"),
    ("ix_purchase_order_lines_inventory_item_id", "purchase_order_lines", "inventory_item_id"),
    ("ix_purchase_orders_matched_invoice_id", "purchase_orders", "matched_invoice_id"),
    ("ix_purchase_orders_order_date", "purchase_orders", "order_date"),
)

# Indexes that duplicate another one, or index a low-cardinality flag.
DROP_INDEXES = (
    "ix_ai_proposals_token", "ix_ai_proposals_confirmation_token", "ix_ai_proposals_user_id",
    "ix_ai_chat_sessions_user",
    "ix_pending_time_entries_external_id", "ix_pending_time_entries_source",
    "ix_time_entries_billable", "ix_time_entries_entry_type", "ix_time_entries_payable",
    "ix_time_entries_external_id", "ix_time_entries_source",
)

# Same index, the migrated name → the model's name.
RENAME_INDEXES = (
    ("ix_pending_time_company", "ix_pending_time_entries_company_id"),
    ("ix_pending_time_status", "ix_pending_time_entries_status"),
)


def _has_table(conn, table):
    return sa.inspect(conn).has_table(table)


def _index_exists(conn, name):
    return bool(conn.execute(sa.text("SELECT 1 FROM pg_class WHERE relkind = 'i' AND relname = :n"),
                             {"n": name}).first())


def _constraint(conn, table, name):
    return conn.execute(sa.text(
        "SELECT contype FROM pg_constraint WHERE conname = :n AND conrelid = CAST(:t AS regclass)"
    ), {"n": name, "t": table}).scalar()


def _column(conn, table, column):
    return conn.execute(sa.text(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).first()


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # --- money columns ----------------------------------------------------------
    for col in ("base_salary", "hourly_rate"):
        info = _column(conn, "employee_pay_profiles", col)
        if info and info[0] == "integer":
            op.execute(f"ALTER TABLE employee_pay_profiles ALTER COLUMN {col} TYPE BIGINT")

    # --- NOT NULL the models always meant ---------------------------------------
    info = _column(conn, "users", "preferred_language")
    if info:
        op.execute("UPDATE users SET preferred_language = 'en' WHERE preferred_language IS NULL")
        op.execute("ALTER TABLE users ALTER COLUMN preferred_language SET DEFAULT 'en'")
        if info[1] == "YES":
            op.execute("ALTER TABLE users ALTER COLUMN preferred_language SET NOT NULL")
    for table, col in TIMESTAMPS_NOT_NULL:
        info = _column(conn, table, col)
        if info and info[1] == "YES":
            op.execute(f'UPDATE "{table}" SET {col} = now() WHERE {col} IS NULL')
            op.execute(f'ALTER TABLE "{table}" ALTER COLUMN {col} SET NOT NULL')

    # --- app_settings: one key per company, one per platform ---------------------
    if _has_table(conn, "app_settings"):
        if _constraint(conn, "app_settings", "uq_app_settings_company_key") == "u":
            op.execute("ALTER TABLE app_settings DROP CONSTRAINT uq_app_settings_company_key")
        dupes = [r[0] for r in conn.execute(sa.text(
            "SELECT key FROM app_settings WHERE company_id IS NULL GROUP BY key HAVING count(*) > 1"))]
        if dupes:
            raise RuntimeError(
                "app_settings has more than one platform row for: " + ", ".join(dupes)
                + " — keep one of each (DELETE the stale ones) and restart.")
        if not _index_exists(conn, "uq_app_settings_company_key"):
            op.execute("CREATE UNIQUE INDEX uq_app_settings_company_key ON app_settings (company_id, key) "
                       "WHERE company_id IS NOT NULL")
        if not _index_exists(conn, "uq_app_settings_global_key"):
            op.execute("CREATE UNIQUE INDEX uq_app_settings_global_key ON app_settings (key) "
                       "WHERE company_id IS NULL")

    # --- unique constraints under the models' names -------------------------------
    if _has_table(conn, "ai_proposals") and not _constraint(conn, "ai_proposals", "ai_proposals_confirmation_token_key"):
        op.execute("ALTER TABLE ai_proposals ADD CONSTRAINT ai_proposals_confirmation_token_key "
                   "UNIQUE (confirmation_token)")
    if _has_table(conn, "companies"):
        if not _index_exists(conn, "ix_companies_slug"):
            op.execute("CREATE UNIQUE INDEX ix_companies_slug ON companies (slug)")
        if _constraint(conn, "companies", "companies_slug_key"):
            op.execute("ALTER TABLE companies DROP CONSTRAINT companies_slug_key")
    if _has_table(conn, "company_profiles"):
        if not _constraint(conn, "company_profiles", "uq_company_profile_company"):
            op.execute("ALTER TABLE company_profiles ADD CONSTRAINT uq_company_profile_company UNIQUE (company_id)")
        if _constraint(conn, "company_profiles", "company_profiles_company_id_fkey") \
                and not _constraint(conn, "company_profiles", "fk_company_profiles_company"):
            op.execute("ALTER TABLE company_profiles RENAME CONSTRAINT company_profiles_company_id_fkey "
                       "TO fk_company_profiles_company")

    # --- users → companies: CASCADE under one name --------------------------------
    if _has_table(conn, "users"):
        rows = conn.execute(sa.text("""
            SELECT c.conname, c.confdeltype FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f' AND c.conrelid = CAST('users' AS regclass)
              AND c.confrelid = CAST('companies' AS regclass) AND a.attname = 'company_id'
        """)).all()
        good = any(name == "fk_users_company" and deltype == "c" for name, deltype in rows)
        for name, deltype in rows:
            if not (name == "fk_users_company" and deltype == "c"):
                op.execute(f'ALTER TABLE users DROP CONSTRAINT "{name}"')
        if not good:
            op.execute("ALTER TABLE users ADD CONSTRAINT fk_users_company FOREIGN KEY (company_id) "
                       "REFERENCES companies(id) ON DELETE CASCADE")

    # --- indexes: one per purpose, the models' names --------------------------------
    for old, new in RENAME_INDEXES:
        if _index_exists(conn, old):
            if _index_exists(conn, new):
                op.execute(f'DROP INDEX "{old}"')
            else:
                op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')
    for name in DROP_INDEXES:
        if _index_exists(conn, name):
            op.execute(f'DROP INDEX "{name}"')
    for name, table, cols in INDEXES:
        if _has_table(conn, table) and not _index_exists(conn, name):
            op.execute(f'CREATE INDEX "{name}" ON "{table}" ({cols})')


def downgrade() -> None:
    # Convergence has no meaningful inverse: every step keeps data and only
    # tightens or renames. Leaving the schema as it is is the safe downgrade.
    pass
