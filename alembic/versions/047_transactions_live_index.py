"""Composite partial index for live journals (roadmap 2026-09 §2.6).

Reports filter transactions by company, currency and date and skip replaced
or undone journals; ``ix_transactions_live (company_id, currency, date) WHERE
deleted_at IS NULL`` serves all of them in a database shared by many
companies.

Revision ID: 047
Revises: 046
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_transactions_live ON transactions (company_id, currency, date) "
               "WHERE deleted_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_transactions_live")
