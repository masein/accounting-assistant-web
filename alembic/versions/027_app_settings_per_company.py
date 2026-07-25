"""app_settings: per-company settings with a surrogate PK.

Fixes the "Reset failed." UniqueViolation on multi-company servers: the model
declared ``key`` as the sole PK while migration 015 changed the table to
PK (company_id, key) — so ``create_all``-bootstrapped databases kept the
global-key PK and the second company writing ``reporting_currency`` (or any
setting) violated it.

This migration normalizes BOTH historical variants:
- PK(key)              — model-bootstrapped databases (the broken ones)
- PK(company_id, key)  — databases that ran migration 015's ALTER

to: surrogate ``id`` PK + unique (company_id, key) (partial indexes so legacy
NULL-company rows stay unique per key).

Revision ID: 027
Revises: 026
Create Date: 2026-07-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first())


def upgrade() -> None:
    conn = op.get_bind()
    UUID = sa.dialects.postgresql.UUID

    if not _column_exists(conn, "app_settings", "id"):
        op.add_column("app_settings", sa.Column("id", UUID(as_uuid=True), nullable=True))
        conn.execute(sa.text("UPDATE app_settings SET id = gen_random_uuid() WHERE id IS NULL"))
        conn.execute(sa.text("ALTER TABLE app_settings ALTER COLUMN id SET NOT NULL"))

    # Replace whichever historical PK exists with the surrogate id.
    conn.execute(sa.text("ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_pkey"))
    conn.execute(sa.text("ALTER TABLE app_settings ADD PRIMARY KEY (id)"))

    # Per-company uniqueness. Existing data can't violate these: both legacy
    # shapes were at least as strict. Partial indexes keep legacy global
    # (NULL-company) rows unique per key.
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_settings_company_key "
        "ON app_settings (company_id, key) WHERE company_id IS NOT NULL"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_settings_global_key "
        "ON app_settings (key) WHERE company_id IS NULL"
    ))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_app_settings_key ON app_settings (key)"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_app_settings_company_key"))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_app_settings_global_key"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_app_settings_key"))
    conn.execute(sa.text("ALTER TABLE app_settings DROP CONSTRAINT IF EXISTS app_settings_pkey"))
    conn.execute(sa.text("ALTER TABLE app_settings ADD PRIMARY KEY (company_id, key)"))
    if _column_exists(conn, "app_settings", "id"):
        op.drop_column("app_settings", "id")
