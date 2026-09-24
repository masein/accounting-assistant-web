"""app_settings.company_id nullable — platform-wide settings.

The AI provider wiring (key ``ai_config``) is configured once per
installation by the super-admin, not per company, so it is stored as a
row with ``company_id IS NULL``. Migration 027 already created the
partial unique index ``uq_app_settings_global_key`` for such rows;
migration 015 had made the column NOT NULL, which this relaxes.

Revision ID: 035
Revises: 034
Create Date: 2026-09-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_nullable(conn) -> bool:
    row = conn.execute(
        sa.text("SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'app_settings' AND column_name = 'company_id'")
    ).first()
    return row is None or row[0] == "YES"


def upgrade() -> None:
    conn = op.get_bind()
    if not _is_nullable(conn):
        conn.execute(sa.text("ALTER TABLE app_settings ALTER COLUMN company_id DROP NOT NULL"))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_settings_global_key "
        "ON app_settings (key) WHERE company_id IS NULL"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM app_settings WHERE company_id IS NULL"))
    conn.execute(sa.text("ALTER TABLE app_settings ALTER COLUMN company_id SET NOT NULL"))
