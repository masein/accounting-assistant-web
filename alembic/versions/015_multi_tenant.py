"""Multi-tenant isolation: companies, company_id on every tenant table,
backfill into a Default company, per-company uniqueness, super-admin.

Ordered & idempotent:
  1. create `companies`; add company_id/is_superadmin/token_version to users
  2. add nullable company_id to all tenant tables
  3. create the Default company (locale=uk, GBP) with a fixed id
  4. backfill every existing row + every user -> Default
  5. set company_id NOT NULL + FK + index on tenant tables
  6. swap accounts' global-unique code for per-company (company_id, code)
  7. attach the `admin` user to Default and make it super-admin

Revision ID: 015
Revises: 014
Create Date: 2026-06-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

# Every tenant-scoped table (matches TenantMixin subclasses).
TENANT_TABLES = [
    "accounts", "adjustments", "ai_chat_messages", "ai_chat_sessions", "ai_proposals",
    "app_settings", "audit_logs", "bank_statement_rows", "bank_statements",
    "billing_rate_overrides", "budget_limits", "credit_notes", "employee_pay_profiles",
    "entities", "goods_receipt_lines", "goods_receipts", "integrity_checks",
    "inventory_items", "inventory_movements", "invoice_items", "invoices", "mileage_claims",
    "pay_run_lines", "pay_runs", "payments", "projects", "purchase_order_lines",
    "purchase_orders", "recurring_rules", "tax_rates", "time_entries",
    "transaction_attachments", "transaction_entities", "transaction_fee_applications",
    "transaction_lines", "transaction_versions", "transactions", "trial_balance_lines",
    "trial_balances",
]


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"), {"t": table}
    ).first())


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    # 1. companies + user columns -------------------------------------------------
    if not _table_exists(conn, "companies"):
        op.create_table(
            "companies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("slug", sa.String(128), nullable=False, unique=True),
            sa.Column("locale", sa.String(16), nullable=False, server_default="default"),
            sa.Column("base_currency", sa.String(8), nullable=False, server_default="IRR"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_companies_slug", "companies", ["slug"], unique=True)
        op.create_index("ix_companies_status", "companies", ["status"])

    for col, ddl in [
        ("company_id", sa.Column("company_id", UUID(as_uuid=True), nullable=True)),
        ("is_superadmin", sa.Column("is_superadmin", sa.Boolean, nullable=False, server_default="false")),
        ("token_version", sa.Column("token_version", sa.Integer, nullable=False, server_default="0")),
    ]:
        if not _column_exists(conn, "users", col):
            op.add_column("users", ddl)

    # 2. add nullable company_id to every tenant table ----------------------------
    for t in TENANT_TABLES:
        if _table_exists(conn, t) and not _column_exists(conn, t, "company_id"):
            op.add_column(t, sa.Column("company_id", UUID(as_uuid=True), nullable=True))

    # 3. create the Default company ----------------------------------------------
    existing = conn.execute(
        sa.text("SELECT 1 FROM companies WHERE id = :id"), {"id": DEFAULT_COMPANY_ID}
    ).first()
    if not existing:
        conn.execute(sa.text(
            "INSERT INTO companies (id, name, slug, locale, base_currency, status, token_version) "
            "VALUES (:id, 'Default', 'default', 'uk', 'GBP', 'active', 0)"
        ), {"id": DEFAULT_COMPANY_ID})

    # 4. backfill every existing row + every user --------------------------------
    for t in TENANT_TABLES:
        if _table_exists(conn, t):
            conn.execute(sa.text(
                f"UPDATE {t} SET company_id = :cid WHERE company_id IS NULL"
            ), {"cid": DEFAULT_COMPANY_ID})
    conn.execute(sa.text("UPDATE users SET company_id = :cid WHERE company_id IS NULL"),
                 {"cid": DEFAULT_COMPANY_ID})

    # 5. NOT NULL + FK + index on tenant tables ----------------------------------
    # audit_logs records system events too (logins happen BEFORE a company
    # context exists), so its company_id stays nullable; everything else is
    # strictly NOT NULL.
    NULLABLE_TENANT = {"audit_logs"}
    for t in TENANT_TABLES:
        if not _table_exists(conn, t):
            continue
        if t not in NULLABLE_TENANT:
            op.alter_column(t, "company_id", existing_type=UUID(as_uuid=True), nullable=False)
        op.create_foreign_key(
            f"fk_{t}_company", t, "companies", ["company_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_{t}_company_id", t, ["company_id"])

    # users FK -> companies (kept nullable: super-admin has no company)
    op.create_foreign_key("fk_users_company", "users", "companies",
                          ["company_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_users_company_id", "users", ["company_id"])

    # 6. accounts: global-unique code -> per-company (company_id, code) ----------
    # The auto-named single-column unique is dropped; replaced by a composite.
    conn.execute(sa.text("""
        DO $$
        DECLARE c text;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'accounts'::regclass AND contype = 'u'
          LOOP EXECUTE 'ALTER TABLE accounts DROP CONSTRAINT ' || quote_ident(c); END LOOP;
        END $$;
    """))
    # drop the implicit unique index on code if present, keep a plain index
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_accounts_code"))
    op.create_index("ix_accounts_code", "accounts", ["code"])
    op.create_unique_constraint("uq_account_company_code", "accounts", ["company_id", "code"])

    # app_settings: PK was (key); make it per-company (company_id, key)
    conn.execute(sa.text("ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_pkey"))
    op.create_primary_key("app_settings_pkey", "app_settings", ["company_id", "key"])

    # 7. promote admin to super-admin under Default ------------------------------
    conn.execute(sa.text(
        "UPDATE users SET is_superadmin = true, company_id = :cid WHERE username = 'admin'"
    ), {"cid": DEFAULT_COMPANY_ID})


def downgrade() -> None:
    conn = op.get_bind()
    op.drop_constraint("uq_account_company_code", "accounts", type_="unique")
    for t in TENANT_TABLES:
        if _table_exists(conn, t):
            op.drop_constraint(f"fk_{t}_company", t, type_="foreignkey")
            op.drop_index(f"ix_{t}_company_id", t)
            op.drop_column(t, "company_id")
    op.drop_constraint("fk_users_company", "users", type_="foreignkey")
    for col in ("token_version", "is_superadmin", "company_id"):
        if _column_exists(conn, "users", col):
            op.drop_column("users", col)
    op.drop_table("companies")
